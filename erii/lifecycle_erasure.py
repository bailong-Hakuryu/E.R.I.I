"""Deterministic erasure transforms for offline E.R.I.I. storage copies.

This module deliberately operates on a caller-provided staging copy.  It has
no API that locates, copies, backs up, or publishes a live store; those safety
steps belong to the lifecycle coordinator.
"""

from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from erii._lifecycle.erasure_inspection import (
    _cascade_event_deletions,
    _consequence_dependency_ids,
    _digest_path,
    _empty_inventory,
    _file_profiles,
    _file_relation,
    _load_file_consequence_journals,
    _load_sqlite_consequence_journals,
    _raw_record_events,
    _read_json,
    _require_relation_match,
    _sqlite_json_rows,
    _sqlite_profile,
    inspect_erasure_scope,
)
from erii.core.adjudication import list_complete_relationship_events
from erii.core.consolidation import RelationshipConsolidator
from erii.core.consequence import NarrativeTensionProjector
from erii.core.relationship import RelationshipProjector
from erii.core.temporal_history import TemporalHistoryValidator
from erii.lifecycle_erasure_contracts import (
    ErasureInventory,
    ErasureScope,
    ErasureScopeInspection,
    ErasureSelectionError,
    ErasureSelector,
    ErasureStorageKind,
    ErasureTransformResult,
    RelationshipRebuildProof,
    _INVENTORY_DISPOSITIONS,
    _required_text,
)
from erii.models.consequence import (
    NarrativeTensionLink,
    NarrativeTensionProjection,
    RelationshipConsequence,
)
from erii.models.turn import TurnRecord
from erii.storage.file_storage import FileStorage
from erii.storage.memory_pack import memory_pack_remap_scope_id
from erii.storage.sqlite_storage import SQLiteStorage


def _json_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _inventory_with_rebuilds(
    inventory: ErasureInventory,
    rebuilt: Counter[str],
    *,
    delegated: Optional[Counter[str]] = None,
    unverified_external: Optional[Counter[str]] = None,
) -> ErasureInventory:
    return ErasureInventory(
        counts={
            "deleted": dict(inventory.counts["deleted"]),
            "rebuilt": dict(rebuilt),
            "delegated": dict(delegated or {}),
            "unverified_external": dict(unverified_external or {}),
        }
    )


def _merge_inventories(inventories: Sequence[ErasureInventory]) -> ErasureInventory:
    merged = {
        disposition: Counter()
        for disposition in _INVENTORY_DISPOSITIONS
    }
    for inventory in inventories:
        for disposition in _INVENTORY_DISPOSITIONS:
            merged[disposition].update(inventory.counts[disposition])
    return ErasureInventory(
        counts={key: dict(value) for key, value in merged.items()}
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _unlink_counted(path: Path, deleted: Counter[str], kind: str) -> None:
    if path.exists():
        path.unlink()
        deleted[kind] += 1


def _filter_file_consequence_dependencies(
    root: Path,
    relationship_id: str,
    *,
    event_ids: set[str],
    decision_ids: set[str],
    source_turn_ids: set[str],
    deleted: Counter[str],
) -> None:
    consequence_path, link_path, consequences, links = (
        _load_file_consequence_journals(root, relationship_id)
    )
    removed_consequences, removed_links = _consequence_dependency_ids(
        consequences,
        links,
        event_ids=event_ids,
        decision_ids=decision_ids,
        source_turn_ids=source_turn_ids,
    )
    if removed_links:
        _write_json(
            link_path,
            [item.to_dict() for item in links if item.link_id not in removed_links],
        )
        deleted["narrative_tension_link"] += len(removed_links)
    if removed_consequences:
        _write_json(
            consequence_path,
            [
                item.to_dict()
                for item in consequences
                if item.consequence_id not in removed_consequences
            ],
        )
        deleted["relationship_consequence"] += len(removed_consequences)


def _delete_file_consequence_journals(
    root: Path,
    relationship_id: str,
    deleted: Counter[str],
) -> None:
    consequence_path, link_path, consequences, links = (
        _load_file_consequence_journals(root, relationship_id)
    )
    # Links derive from consequences, so erase them before their roots.
    if link_path.exists():
        link_path.unlink()
        deleted["narrative_tension_link"] += len(links)
    if consequence_path.exists():
        consequence_path.unlink()
        deleted["relationship_consequence"] += len(consequences)


def _erase_file_relationship(
    root: Path,
    selector: ErasureSelector,
) -> Tuple[Tuple[str, ...], ErasureInventory]:
    profiles = _file_profiles(root)
    matched = profiles.get(selector.relationship_id or "")
    if matched is None:
        raise ErasureSelectionError("relationship was not found in staging")
    profile_path, profile = matched
    _require_relation_match(selector, profile)
    relationship_id = str(profile["relationship_id"])
    blueprint = profile.get("blueprint")
    blueprint_id = blueprint.get("blueprint_id") if isinstance(blueprint, Mapping) else None
    persona_id = profile.get("persona_id")
    deleted: Counter[str] = Counter()

    _delete_file_consequence_journals(root, relationship_id, deleted)

    pair_dir = profile_path.parent
    vector_node_ids: set[str] = set()
    nodes_path = pair_dir / "nodes.json"
    if nodes_path.exists():
        raw_nodes = _read_json(nodes_path)
        if not isinstance(raw_nodes, list):
            raise ErasureSelectionError("memory node aggregate is malformed")
        vector_node_ids.update(
            str(item["node_id"])
            for item in raw_nodes
            if isinstance(item, Mapping) and isinstance(item.get("node_id"), str)
        )
        deleted["memory_node"] += len(vector_node_ids)
    pair_files = sum(1 for item in pair_dir.rglob("*") if item.is_file())
    shutil.rmtree(pair_dir)
    deleted["relationship"] += 1
    deleted["pair_artifact"] += max(0, pair_files - 1)

    aggregate_paths = {
        "relationship_event": _digest_path(
            str(root / "_relationship_events"), relationship_id
        ),
        "relationship_adjudication": _digest_path(
            str(root / "_relationship_adjudications"), relationship_id
        ),
        "persona_growth": _digest_path(
            str(root / "_persona_growth"), relationship_id
        ),
        "source_turn": _digest_path(str(root / "_turn_records"), relationship_id),
        "reply_attempt": _digest_path(str(root / "_reply_attempts"), relationship_id),
        "relationship_processing": _digest_path(
            str(root / "_relationship_processing"), relationship_id
        ),
    }
    for kind, path in aggregate_paths.items():
        if path.exists():
            raw = _read_json(path)
            if isinstance(raw, list):
                deleted[kind] += len(raw)
                if kind == "relationship_adjudication":
                    deleted["relationship_event"] += sum(
                        len(_raw_record_events(item))
                        for item in raw
                        if isinstance(item, Mapping)
                    )
            elif isinstance(raw, Mapping):
                deleted[kind] += sum(
                    len(raw.get(key, ()))
                    for key in ("runs", "reflection_decisions", "reflections")
                )
            path.unlink()

    if isinstance(blueprint_id, str):
        compilation_path = _digest_path(
            str(root / "_persona_compilations"), blueprint_id
        )
        if compilation_path.exists():
            raw = _read_json(compilation_path)
            if isinstance(raw, Mapping):
                deleted["persona_compilation"] += len(raw.get("proposals", ()))
                deleted["persona_manifest"] += len(raw.get("manifests", ()))
            compilation_path.unlink()

    archival_path = root / "_archival_state.json"
    if archival_path.exists():
        state = _read_json(archival_path)
        if not isinstance(state, Mapping):
            raise ErasureSelectionError("archival state is malformed")
        state = dict(state)
        records = list(state.get("records", ()))
        tombstones = list(state.get("tombstones", ()))
        imported = list(state.get("imported_timeline", ()))
        artifacts = dict(state.get("artifacts", {}))
        removed_archival_ids = {
            str(item.get("receipt", {}).get("archival_id"))
            for item in records
            if item.get("receipt", {}).get("relationship_id") == relationship_id
        }
        removed_archival_ids.update(
            str(item.get("archival_id"))
            for item in tombstones
            if item.get("relationship_id") == relationship_id
        )
        deleted["archival_record"] += sum(
            item.get("receipt", {}).get("relationship_id") == relationship_id
            for item in records
        )
        deleted["archival_tombstone"] += sum(
            item.get("relationship_id") == relationship_id for item in tombstones
        )
        deleted["timeline_entry"] += sum(
            item.get("relationship_id") == relationship_id for item in imported
        )
        deleted["archival_batch"] += sum(
            key in removed_archival_ids
            or (
                isinstance(value, Mapping)
                and value.get("relationship_id") == relationship_id
            )
            for key, value in artifacts.items()
        )
        for key, value in artifacts.items():
            if not isinstance(value, Mapping):
                continue
            if key not in removed_archival_ids and value.get(
                "relationship_id"
            ) != relationship_id:
                continue
            for node in value.get("memories", ()):
                if isinstance(node, Mapping) and isinstance(node.get("node_id"), str):
                    vector_node_ids.add(str(node["node_id"]))
            deleted["timeline_entry"] += len(value.get("timeline", ()))
        state["records"] = [
            item
            for item in records
            if item.get("receipt", {}).get("relationship_id") != relationship_id
        ]
        state["tombstones"] = [
            item for item in tombstones if item.get("relationship_id") != relationship_id
        ]
        state["imported_timeline"] = [
            item for item in imported if item.get("relationship_id") != relationship_id
        ]
        state["artifacts"] = {
            key: value
            for key, value in artifacts.items()
            if key not in removed_archival_ids
            and not (
                isinstance(value, Mapping)
                and value.get("relationship_id") == relationship_id
            )
        }
        _write_json(archival_path, state)

    registry_path = root / "_relationship_identities.json"
    registry = _read_json(registry_path)
    if not isinstance(registry, Mapping):
        raise ErasureSelectionError("identity registry is malformed")
    mutable_registry = {
        key: dict(registry.get(key, {}))
        for key in ("agent", "user", "relationships", "personas", "blueprints")
    }
    mutable_registry["relationships"].pop(relationship_id, None)
    if isinstance(persona_id, str):
        mutable_registry["personas"].pop(persona_id, None)
    if isinstance(blueprint_id, str):
        mutable_registry["blueprints"].pop(blueprint_id, None)
    remaining_pairs = set(mutable_registry["relationships"].values())
    if not any(pair.startswith(f"{selector.agent_id}\0") for pair in remaining_pairs):
        mutable_registry["agent"].pop(selector.agent_id, None)
    if not any(pair.endswith(f"\0{selector.user_id}") for pair in remaining_pairs):
        mutable_registry["user"].pop(selector.user_id, None)
    _write_json(registry_path, mutable_registry)
    deleted["memory_node"] = len(vector_node_ids)
    inventory = _empty_inventory(deleted)
    if vector_node_ids:
        count = len(vector_node_ids)
        inventory = _inventory_with_rebuilds(
            inventory,
            Counter(),
            delegated=Counter({"memory_vector_delete": count}),
            unverified_external=Counter({"memory_vector": count}),
        )
    return (relationship_id,), inventory


def _delete_sqlite_memory_pack_receipts(
    connection: sqlite3.Connection,
    selector: ErasureSelector,
    deleted: Counter[str],
) -> None:
    """Revokes cached import success after any target-scope erasure."""
    if selector.scope is ErasureScope.COMPLETE_USER:
        cursor = connection.execute(
            "DELETE FROM memory_pack_write_receipts WHERE target_user = ?",
            (selector.user_id,),
        )
    else:
        agent_id = _required_text(selector.agent_id, "agent_id")
        user_id = _required_text(selector.user_id, "user_id")
        relationship_id = _required_text(
            selector.relationship_id,
            "relationship_id",
        )
        remap_scope_id = memory_pack_remap_scope_id(agent_id, user_id)
        cursor = connection.execute(
            """
            DELETE FROM memory_pack_write_receipts
            WHERE target_agent = ? AND target_user = ?
              AND relationship_id IN (?, ?)
            """,
            (agent_id, user_id, relationship_id, remap_scope_id),
        )
    deleted["memory_pack_write_receipt"] += cursor.rowcount


def _delete_sqlite_consequence_dependencies(
    connection: sqlite3.Connection,
    relationship_id: str,
    *,
    event_ids: set[str],
    decision_ids: set[str],
    source_turn_ids: set[str],
    deleted: Counter[str],
    delete_all: bool = False,
) -> None:
    consequences, links = _load_sqlite_consequence_journals(
        connection,
        relationship_id,
    )
    removed_consequences, removed_links = _consequence_dependency_ids(
        consequences,
        links,
        event_ids=event_ids,
        decision_ids=decision_ids,
        source_turn_ids=source_turn_ids,
        delete_all=delete_all,
    )
    # Preserve the explicit dependency order even when SQLite foreign keys are
    # disabled by an offline staging connection.
    for link_id in sorted(removed_links):
        deleted["narrative_tension_link"] += connection.execute(
            "DELETE FROM narrative_tension_links WHERE link_id = ?",
            (link_id,),
        ).rowcount
    for consequence_id in sorted(removed_consequences):
        deleted["relationship_consequence"] += connection.execute(
            "DELETE FROM relationship_consequences WHERE consequence_id = ?",
            (consequence_id,),
        ).rowcount


def _erase_sqlite_relationship(
    path: Path,
    selector: ErasureSelector,
) -> Tuple[Tuple[str, ...], ErasureInventory]:
    deleted: Counter[str] = Counter()
    vector_count = 0
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        _delete_sqlite_memory_pack_receipts(connection, selector, deleted)
        row = _sqlite_profile(connection, selector.relationship_id or "")
        if row is None:
            raise ErasureSelectionError("relationship was not found in staging")
        _require_relation_match(selector, row)
        relationship_id = str(row["relationship_id"])
        blueprint_id = str(row["blueprint_id"])
        agent_identity_id = str(row["agent_identity_id"])
        user_identity_id = str(row["user_identity_id"])
        adjudicated_event_count = sum(
            len(_raw_record_events(raw))
            for _, raw in _sqlite_json_rows(
                connection,
                "relationship_adjudications",
                "decision_id",
                relationship_id,
            )
        )
        _delete_sqlite_consequence_dependencies(
            connection,
            relationship_id,
            event_ids=set(),
            decision_ids=set(),
            source_turn_ids=set(),
            deleted=deleted,
            delete_all=True,
        )
        tables = (
            ("persona_reflection_records", "relationship_id", "persona_reflection"),
            ("persona_reflection_decisions", "relationship_id", "reflection_decision"),
            ("relationship_processing_runs", "relationship_id", "processing_run"),
            ("reply_attempts", "relationship_id", "reply_attempt"),
            ("relationship_adjudications", "relationship_id", "relationship_adjudication"),
            ("persona_growth_proposals", "relationship_id", "persona_growth"),
            ("relationship_events", "relationship_id", "relationship_event"),
            ("archival_tombstones", "relationship_id", "archival_tombstone"),
            ("archival_records", "relationship_id", "archival_record"),
            ("source_turns", "relationship_id", "source_turn"),
            ("relationship_initial_context", "relationship_id", "initial_context"),
        )
        for table, column, kind in tables:
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE {column} = ?",  # noqa: S608 - closed constants
                (relationship_id,),
            )
            deleted[kind] += cursor.rowcount
        for table, kind in (
            ("memory_nodes", "memory_node"),
            ("core_memories", "core_memory"),
            ("timeline_entries", "timeline_entry"),
        ):
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE agent_id = ? AND user_id = ?",  # noqa: S608
                (selector.agent_id, selector.user_id),
            )
            deleted[kind] += cursor.rowcount
            if table == "memory_nodes":
                vector_count = cursor.rowcount
        deleted["relationship_event"] += adjudicated_event_count
        deleted["persona_manifest"] += connection.execute(
            "DELETE FROM persona_manifests WHERE blueprint_id = ?",
            (blueprint_id,),
        ).rowcount
        deleted["persona_compilation"] += connection.execute(
            "DELETE FROM persona_compilation_revisions WHERE blueprint_id = ?",
            (blueprint_id,),
        ).rowcount
        deleted["relationship"] += connection.execute(
            "DELETE FROM relationships WHERE relationship_id = ?",
            (relationship_id,),
        ).rowcount
        for identity_id in (agent_identity_id, user_identity_id):
            referenced = connection.execute(
                """
                SELECT 1 FROM relationships
                WHERE agent_identity_id = ? OR user_identity_id = ?
                LIMIT 1
                """,
                (identity_id, identity_id),
            ).fetchone()
            if referenced is None:
                deleted["stable_identity"] += connection.execute(
                    "DELETE FROM stable_identities WHERE identity_id = ?",
                    (identity_id,),
                ).rowcount
        connection.commit()
    inventory = _empty_inventory(deleted)
    if vector_count:
        inventory = _inventory_with_rebuilds(
            inventory,
            Counter(),
            delegated=Counter({"memory_vector_delete": vector_count}),
            unverified_external=Counter({"memory_vector": vector_count}),
        )
    return (relationship_id,), inventory


def _event_ids_for_turn(
    adjudications: Sequence[Mapping[str, Any]],
    source_turn_id: str,
) -> set[str]:
    return {
        event_id
        for record in adjudications
        if isinstance(record.get("receipt"), Mapping)
        and record["receipt"].get("source_turn_id") == source_turn_id
        for event_id in _raw_record_events(record)
    }


def _decision_ids_for_turn(
    adjudications: Sequence[Mapping[str, Any]],
    source_turn_id: str,
) -> set[str]:
    """Returns every adjudication owned by a turn, including eventless outcomes."""

    decision_ids: set[str] = set()
    for record in adjudications:
        receipt = record.get("receipt")
        if not isinstance(receipt, Mapping):
            continue
        if receipt.get("source_turn_id") != source_turn_id:
            continue
        decision_ids.add(
            _required_text(receipt.get("decision_id"), "decision_id")
        )
    return decision_ids


def _processing_dependency_closure(
    direct_events: Sequence[Mapping[str, Any]],
    adjudications: Sequence[Mapping[str, Any]],
    processing_runs: Sequence[Mapping[str, Any]],
    initial_event_ids: set[str],
    *,
    initial_decision_ids: set[str] | None = None,
    initial_source_turn_ids: set[str] | None = None,
) -> Tuple[set[str], set[str], set[str], set[str]]:
    """Conservatively revokes every run frozen over removed journal authority.

    A processing run is an immutable claim about exact direct-event and
    adjudication prefixes.  Once an item in either prefix is erased, changing
    the run in place would manufacture a historical review that never
    happened.  The safe offline transform therefore removes the run and all
    of its derived outputs, then repeats until no later frozen prefix depends
    on the removed authority.
    """

    direct_order = tuple(
        _required_text(item.get("event_id"), "event_id")
        for item in direct_events
    )
    decision_order = tuple(
        _required_text(
            item.get("receipt", {}).get("decision_id"),
            "decision_id",
        )
        for item in adjudications
    )
    if len(direct_order) != len(set(direct_order)):
        raise ErasureSelectionError("relationship event journal repeats an event")
    if len(decision_order) != len(set(decision_order)):
        raise ErasureSelectionError(
            "relationship adjudication journal repeats a decision"
        )

    removed_events, removed_decisions, derived_source_turns = (
        _cascade_event_deletions(
            direct_events,
            adjudications,
            set(initial_event_ids),
            initial_decision_ids=set(initial_decision_ids or ()),
        )
    )
    removed_source_turns = {
        *(initial_source_turn_ids or ()),
        *derived_source_turns,
    }
    removed_runs: set[str] = set()

    while True:
        event_seeds = set(removed_events)
        decision_seeds = set(removed_decisions)
        source_seeds = set(removed_source_turns)
        for raw_run in processing_runs:
            processing_id = _required_text(
                raw_run.get("processing_id"),
                "processing_id",
            )
            if processing_id in removed_runs:
                continue
            source_turn_id = _required_text(
                raw_run.get("source_turn_id"),
                "source_turn_id",
            )
            run_event_ids = {
                _required_text(item, "event_id")
                for item in raw_run.get("event_ids", ())
            }
            run_decision_ids = {
                _required_text(item, "decision_id")
                for item in raw_run.get("decision_ids", ())
            }
            direct_count = raw_run.get(
                "adjudication_base_direct_event_count",
                0,
            )
            decision_count = raw_run.get(
                "adjudication_base_decision_count",
                0,
            )
            if (
                isinstance(direct_count, bool)
                or not isinstance(direct_count, int)
                or direct_count < 0
                or direct_count > len(direct_order)
                or isinstance(decision_count, bool)
                or not isinstance(decision_count, int)
                or decision_count < 0
                or decision_count > len(decision_order)
            ):
                raise ErasureSelectionError(
                    "relationship processing baseline exceeds its journals"
                )
            depends_on_removed_prefix = bool(
                removed_events.intersection(direct_order[:direct_count])
                or removed_decisions.intersection(
                    decision_order[:decision_count]
                )
            )
            if not (
                source_turn_id in removed_source_turns
                or run_event_ids.intersection(removed_events)
                or run_decision_ids.intersection(removed_decisions)
                or depends_on_removed_prefix
            ):
                continue
            removed_runs.add(processing_id)
            event_seeds.update(run_event_ids)
            decision_seeds.update(run_decision_ids)
            source_seeds.add(source_turn_id)

        if (
            event_seeds == removed_events
            and decision_seeds == removed_decisions
            and source_seeds == removed_source_turns
        ):
            break
        removed_events, removed_decisions, derived_source_turns = (
            _cascade_event_deletions(
                direct_events,
                adjudications,
                event_seeds,
                initial_decision_ids=decision_seeds,
            )
        )
        removed_source_turns = {
            *source_seeds,
            *derived_source_turns,
        }

    return (
        removed_events,
        removed_decisions,
        removed_source_turns,
        removed_runs,
    )


def _legacy_turn_without_revoked_authority(
    raw_turn: Mapping[str, Any],
) -> Dict[str, Any]:
    """Preserves the exchange while dropping a review bound to erased history."""

    turn = TurnRecord.from_dict(raw_turn)
    disposition = (
        turn.delivery_disposition.value
        if turn.delivery_disposition is not None
        else None
    )
    if disposition == "shown_unreviewed":
        # Legacy has no shown-unreviewed branch.  ``overridden`` preserves the
        # important authority fact: the visible Agent reply remains
        # quarantined from automatic derived writes.
        disposition = "overridden"
    legacy = {
        "turn_id": turn.turn_id,
        "relationship_id": turn.relationship_id,
        "status": turn.status.value,
        "transcript": turn.transcript.to_dict(),
        "interaction_context": [
            item.to_dict() for item in turn.interaction_context
        ],
        "source_revision": turn.source_revision,
        "record_version": turn.record_version,
        "opened_at": turn.opened_at,
        "continuity_assessment": None,
        "delivery_disposition": disposition,
        "processing_plan": (
            turn.processing_plan.to_dict()
            if turn.processing_plan is not None
            else None
        ),
        "processing_outcomes": [
            item.to_dict() for item in turn.processing_outcomes
        ],
        "completed_at": turn.completed_at,
        "abandoned_at": turn.abandoned_at,
        "abandonment_reason": turn.abandonment_reason,
    }
    return TurnRecord.from_dict(legacy).to_dict()


def _revoke_invalidated_turn_authority(
    turns: Sequence[Mapping[str, Any]],
    direct_events: Sequence[Mapping[str, Any]],
    adjudications: Sequence[Mapping[str, Any]],
    *,
    removed_event_ids: set[str],
    removed_decision_ids: set[str],
    rebuilt: Counter[str],
) -> list[Mapping[str, Any]]:
    """Downgrades only Turns whose frozen opening prefix was actually erased."""

    direct_order = tuple(
        _required_text(item.get("event_id"), "event_id")
        for item in direct_events
    )
    decision_order = tuple(
        _required_text(
            item.get("receipt", {}).get("decision_id"),
            "decision_id",
        )
        for item in adjudications
    )
    result: list[Mapping[str, Any]] = []
    for raw_turn in turns:
        baseline = raw_turn.get("context_baseline")
        if not isinstance(baseline, Mapping):
            result.append(raw_turn)
            continue
        direct_count = baseline.get("direct_event_count")
        decision_count = baseline.get("adjudication_count")
        if (
            isinstance(direct_count, bool)
            or not isinstance(direct_count, int)
            or direct_count < 0
            or direct_count > len(direct_order)
            or isinstance(decision_count, bool)
            or not isinstance(decision_count, int)
            or decision_count < 0
            or decision_count > len(decision_order)
        ):
            raise ErasureSelectionError(
                "Turn Context Baseline exceeds its relationship journals"
            )
        invalidated = bool(
            removed_event_ids.intersection(direct_order[:direct_count])
            or removed_decision_ids.intersection(
                decision_order[:decision_count]
            )
        )
        if not invalidated:
            result.append(raw_turn)
            continue
        result.append(_legacy_turn_without_revoked_authority(raw_turn))
        rebuilt["source_turn_authority"] += 1
    return result


def _filter_processing_state(
    state: Mapping[str, Any],
    *,
    source_turn_ids: set[str],
    event_ids: set[str],
    processing_ids: set[str],
    deleted: Counter[str],
) -> Dict[str, Any]:
    result = dict(state)
    raw_runs = list(state.get("runs", ()))
    runs = [
        item
        for item in raw_runs
        if item.get("processing_id") not in processing_ids
        and item.get("source_turn_id") not in source_turn_ids
        and not event_ids.intersection(item.get("event_ids", ()))
    ]
    deleted["processing_run"] += len(raw_runs) - len(runs)

    raw_decisions = list(state.get("reflection_decisions", ()))
    decisions = [
        item
        for item in raw_decisions
        if item.get("source_turn_id") not in source_turn_ids
        and item.get("event_id") not in event_ids
    ]
    deleted["reflection_decision"] += len(raw_decisions) - len(decisions)

    raw_reflections = list(state.get("reflections", ()))
    removed_reflection_ids = {
        str(item.get("reflection_id"))
        for item in raw_reflections
        if item.get("event_id") in event_ids
    }
    changed = True
    while changed:
        changed = False
        for item in raw_reflections:
            reflection_id = str(item.get("reflection_id"))
            if (
                reflection_id not in removed_reflection_ids
                and item.get("target_reflection_id") in removed_reflection_ids
            ):
                removed_reflection_ids.add(reflection_id)
                changed = True
    reflections = [
        item
        for item in raw_reflections
        if str(item.get("reflection_id")) not in removed_reflection_ids
    ]
    decisions = [
        item
        for item in decisions
        if item.get("target_reflection_id") not in removed_reflection_ids
        and (
            not isinstance(item.get("reflection_record"), Mapping)
            or item["reflection_record"].get("reflection_id")
            not in removed_reflection_ids
        )
    ]
    deleted["persona_reflection"] += len(raw_reflections) - len(reflections)
    # Decisions removed through a now-missing target are also derived artifacts.
    deleted["reflection_decision"] += (
        len(raw_decisions)
        - deleted["reflection_decision"]
        - len(decisions)
    )
    result["runs"] = runs
    result["reflection_decisions"] = decisions
    result["reflections"] = reflections
    return result


def _filter_growth(
    proposals: Sequence[Mapping[str, Any]],
    event_ids: set[str],
    deleted: Counter[str],
) -> list[Mapping[str, Any]]:
    remaining = [
        item
        for item in proposals
        if not event_ids.intersection(item.get("supporting_event_ids", ()))
    ]
    deleted["persona_growth"] += len(proposals) - len(remaining)
    return remaining


def _filter_file_archival_for_turns(
    root: Path,
    *,
    relationship_id: str,
    source_turn_ids: set[str],
    deleted: Counter[str],
) -> Tuple[set[str], int]:
    archival_path = root / "_archival_state.json"
    if not archival_path.exists():
        return set(), 0
    state = _read_json(archival_path)
    if not isinstance(state, Mapping):
        raise ErasureSelectionError("archival state is malformed")
    result = dict(state)
    records = list(state.get("records", ()))
    tombstones = list(state.get("tombstones", ()))
    artifacts = dict(state.get("artifacts", {}))
    imported = list(state.get("imported_timeline", ()))
    archival_ids = {
        str(item.get("receipt", {}).get("archival_id"))
        for item in records
        if item.get("receipt", {}).get("relationship_id") == relationship_id
        and item.get("receipt", {}).get("source_turn_id") in source_turn_ids
    }
    archival_ids.update(
        str(item.get("archival_id"))
        for item in tombstones
        if item.get("relationship_id") == relationship_id
        and item.get("source_turn_id") in source_turn_ids
    )
    archival_ids.update(
        str(key)
        for key, value in artifacts.items()
        if isinstance(value, Mapping)
        and value.get("relationship_id") == relationship_id
        and value.get("source_turn_id") in source_turn_ids
    )
    kept_records = [
        item
        for item in records
        if str(item.get("receipt", {}).get("archival_id")) not in archival_ids
    ]
    kept_tombstones = [
        item
        for item in tombstones
        if str(item.get("archival_id")) not in archival_ids
    ]
    kept_imported = [
        item
        for item in imported
        if item.get("source_turn_id") not in source_turn_ids
        and item.get("source_archival_id") not in archival_ids
    ]
    removed_batches = {
        key: value for key, value in artifacts.items() if str(key) in archival_ids
    }
    removed_memory_count = sum(
        len(value.get("memories", ()))
        for value in removed_batches.values()
        if isinstance(value, Mapping)
    )
    deleted["archival_record"] += len(records) - len(kept_records)
    deleted["archival_tombstone"] += len(tombstones) - len(kept_tombstones)
    deleted["archival_batch"] += len(removed_batches)
    deleted["timeline_entry"] += len(imported) - len(kept_imported)
    deleted["memory_node"] += removed_memory_count
    result["records"] = kept_records
    result["tombstones"] = kept_tombstones
    result["artifacts"] = {
        key: value for key, value in artifacts.items() if str(key) not in archival_ids
    }
    result["imported_timeline"] = kept_imported
    _write_json(archival_path, result)
    return archival_ids, removed_memory_count


def _erase_file_turn(
    root: Path,
    selector: ErasureSelector,
) -> Tuple[Tuple[str, ...], ErasureInventory]:
    profile_path, _, relationship_id = _file_relation(root, selector)
    turn_id = _required_text(selector.source_turn_id, "source_turn_id")
    deleted: Counter[str] = Counter()
    turn_path = _digest_path(str(root / "_turn_records"), relationship_id)
    turns = list(_read_json(turn_path)) if turn_path.exists() else []
    matches = [item for item in turns if item.get("turn_id") == turn_id]
    if len(matches) != 1:
        raise ErasureSelectionError("source turn was not found exactly once in staging")
    remaining_turns = [item for item in turns if item.get("turn_id") != turn_id]
    deleted["source_turn"] += 1
    rebuilt: Counter[str] = Counter()

    adjudication_path = _digest_path(
        str(root / "_relationship_adjudications"), relationship_id
    )
    adjudications = (
        list(_read_json(adjudication_path)) if adjudication_path.exists() else []
    )
    direct_path = _digest_path(str(root / "_relationship_events"), relationship_id)
    direct_events = list(_read_json(direct_path)) if direct_path.exists() else []
    processing_path = _digest_path(
        str(root / "_relationship_processing"), relationship_id
    )
    processing = _read_json(processing_path) if processing_path.exists() else {}
    processing_runs = list(processing.get("runs", ()))
    initial_event_ids = _event_ids_for_turn(adjudications, turn_id)
    (
        event_ids,
        decision_ids,
        derived_source_turn_ids,
        processing_ids,
    ) = _processing_dependency_closure(
        direct_events,
        adjudications,
        processing_runs,
        initial_event_ids,
        initial_decision_ids=_decision_ids_for_turn(adjudications, turn_id),
        initial_source_turn_ids={turn_id},
    )
    affected_source_turn_ids = set(derived_source_turn_ids)
    _filter_file_consequence_dependencies(
        root,
        relationship_id,
        event_ids=event_ids,
        decision_ids=decision_ids,
        source_turn_ids=affected_source_turn_ids,
        deleted=deleted,
    )
    remaining_turns = _revoke_invalidated_turn_authority(
        remaining_turns,
        direct_events,
        adjudications,
        removed_event_ids=event_ids,
        removed_decision_ids=decision_ids,
        rebuilt=rebuilt,
    )
    _write_json(turn_path, remaining_turns)
    kept_direct = [
        item for item in direct_events if item.get("event_id") not in event_ids
    ]
    kept_adjudications = [
        item
        for item in adjudications
        if item.get("receipt", {}).get("decision_id") not in decision_ids
    ]
    deleted["relationship_event"] += (
        len(direct_events)
        - len(kept_direct)
        + sum(
            len(_raw_record_events(item))
            for item in adjudications
            if item.get("receipt", {}).get("decision_id") in decision_ids
        )
    )
    deleted["relationship_adjudication"] += (
        len(adjudications) - len(kept_adjudications)
    )
    _write_json(direct_path, kept_direct)
    _write_json(adjudication_path, kept_adjudications)

    attempts_path = _digest_path(str(root / "_reply_attempts"), relationship_id)
    attempts = list(_read_json(attempts_path)) if attempts_path.exists() else []
    kept_attempts = [item for item in attempts if item.get("turn_id") != turn_id]
    deleted["reply_attempt"] += len(attempts) - len(kept_attempts)
    _write_json(attempts_path, kept_attempts)

    growth_path = _digest_path(str(root / "_persona_growth"), relationship_id)
    growth = list(_read_json(growth_path)) if growth_path.exists() else []
    _write_json(growth_path, _filter_growth(growth, event_ids, deleted))

    _write_json(
        processing_path,
        _filter_processing_state(
            processing,
            source_turn_ids=affected_source_turn_ids,
            event_ids=event_ids,
            processing_ids=processing_ids,
            deleted=deleted,
        ),
    )

    archival_ids, vector_count = _filter_file_archival_for_turns(
        root,
        relationship_id=relationship_id,
        source_turn_ids=affected_source_turn_ids,
        deleted=deleted,
    )
    # Imported packs can store provenance-bearing nodes outside archival batches.
    nodes_path = profile_path.parent / "nodes.json"
    if nodes_path.exists():
        nodes = list(_read_json(nodes_path))
        kept_nodes = [
            item
            for item in nodes
            if item.get("source_turn_id") not in affected_source_turn_ids
            and item.get("source_archival_id") not in archival_ids
        ]
        removed = len(nodes) - len(kept_nodes)
        deleted["memory_node"] += removed
        vector_count += removed
        _write_json(nodes_path, kept_nodes)
    inventory = _inventory_with_rebuilds(
        _empty_inventory(deleted),
        rebuilt,
    )
    if vector_count:
        inventory = _inventory_with_rebuilds(
            inventory,
            rebuilt,
            delegated=Counter({"memory_vector_delete": vector_count}),
            unverified_external=Counter({"memory_vector": vector_count}),
        )
    return (relationship_id,), inventory


def _erase_file_event(
    root: Path,
    selector: ErasureSelector,
) -> Tuple[Tuple[str, ...], ErasureInventory]:
    profile_path, _, relationship_id = _file_relation(root, selector)
    target_event_id = _required_text(
        selector.relationship_event_id,
        "relationship_event_id",
    )
    deleted: Counter[str] = Counter()
    direct_path = _digest_path(str(root / "_relationship_events"), relationship_id)
    direct_events = list(_read_json(direct_path)) if direct_path.exists() else []
    adjudication_path = _digest_path(
        str(root / "_relationship_adjudications"), relationship_id
    )
    adjudications = (
        list(_read_json(adjudication_path)) if adjudication_path.exists() else []
    )
    processing_path = _digest_path(
        str(root / "_relationship_processing"), relationship_id
    )
    processing = _read_json(processing_path) if processing_path.exists() else {}
    processing_runs = list(processing.get("runs", ()))
    (
        event_ids,
        decision_ids,
        source_turn_ids,
        processing_ids,
    ) = _processing_dependency_closure(
        direct_events,
        adjudications,
        processing_runs,
        {target_event_id},
    )
    _filter_file_consequence_dependencies(
        root,
        relationship_id,
        event_ids=event_ids,
        decision_ids=decision_ids,
        source_turn_ids=source_turn_ids,
        deleted=deleted,
    )
    rebuilt: Counter[str] = Counter()
    turn_path = _digest_path(str(root / "_turn_records"), relationship_id)
    turns = list(_read_json(turn_path)) if turn_path.exists() else []
    turns = _revoke_invalidated_turn_authority(
        turns,
        direct_events,
        adjudications,
        removed_event_ids=event_ids,
        removed_decision_ids=decision_ids,
        rebuilt=rebuilt,
    )
    _write_json(turn_path, turns)
    kept_direct = [
        item for item in direct_events if item.get("event_id") not in event_ids
    ]
    kept_adjudications = [
        item
        for item in adjudications
        if item.get("receipt", {}).get("decision_id") not in decision_ids
    ]
    deleted["relationship_event"] += (
        len(direct_events)
        - len(kept_direct)
        + sum(
            len(_raw_record_events(item))
            for item in adjudications
            if item.get("receipt", {}).get("decision_id") in decision_ids
        )
    )
    deleted["relationship_adjudication"] += (
        len(adjudications) - len(kept_adjudications)
    )
    _write_json(direct_path, kept_direct)
    _write_json(adjudication_path, kept_adjudications)

    growth_path = _digest_path(str(root / "_persona_growth"), relationship_id)
    growth = list(_read_json(growth_path)) if growth_path.exists() else []
    _write_json(growth_path, _filter_growth(growth, event_ids, deleted))

    _write_json(
        processing_path,
        _filter_processing_state(
            processing,
            source_turn_ids=source_turn_ids,
            event_ids=event_ids,
            processing_ids=processing_ids,
            deleted=deleted,
        ),
    )

    archival_ids, vector_count = _filter_file_archival_for_turns(
        root,
        relationship_id=relationship_id,
        source_turn_ids=source_turn_ids,
        deleted=deleted,
    )
    nodes_path = profile_path.parent / "nodes.json"
    if nodes_path.exists():
        nodes = list(_read_json(nodes_path))
        kept_nodes = [
            item
            for item in nodes
            if item.get("source_turn_id") not in source_turn_ids
            and item.get("source_archival_id") not in archival_ids
        ]
        removed = len(nodes) - len(kept_nodes)
        deleted["memory_node"] += removed
        vector_count += removed
        _write_json(nodes_path, kept_nodes)
    inventory = _inventory_with_rebuilds(
        _empty_inventory(deleted),
        rebuilt,
    )
    if vector_count:
        inventory = _inventory_with_rebuilds(
            inventory,
            rebuilt,
            delegated=Counter({"memory_vector_delete": vector_count}),
            unverified_external=Counter({"memory_vector": vector_count}),
        )
    return (relationship_id,), inventory


def _erase_file_complete_user(
    root: Path,
    selector: ErasureSelector,
) -> Tuple[Tuple[str, ...], ErasureInventory]:
    registry_path = root / "_relationship_identities.json"
    registry = _read_json(registry_path)
    if not isinstance(registry, Mapping):
        raise ErasureSelectionError("identity registry is malformed")
    user_id = _required_text(selector.user_id, "user_id")
    user_identity_id = _required_text(
        selector.user_identity_id,
        "user_identity_id",
    )
    if registry.get("user", {}).get(user_id) != user_identity_id:
        raise ErasureSelectionError("user identity does not match the staging registry")
    profiles = [
        raw
        for _, raw in _file_profiles(root).values()
        if raw.get("user_id") == user_id
    ]
    if not profiles:
        raise ErasureSelectionError("complete user scope matched no relationships")
    if any(raw.get("user_identity_id") != user_identity_id for raw in profiles):
        raise ErasureSelectionError("complete user scope has ambiguous identity bindings")
    results = []
    affected = []
    for raw in sorted(profiles, key=lambda item: str(item["relationship_id"])):
        relationship_id = str(raw["relationship_id"])
        current_affected, inventory = _erase_file_relationship(
            root,
            ErasureSelector(
                scope=ErasureScope.RELATIONSHIP,
                agent_id=str(raw["agent_id"]),
                user_id=user_id,
                relationship_id=relationship_id,
            ),
        )
        affected.extend(current_affected)
        results.append(inventory)
    return tuple(sorted(affected)), _merge_inventories(results)


def _erase_sqlite_turn(
    path: Path,
    selector: ErasureSelector,
) -> Tuple[Tuple[str, ...], ErasureInventory]:
    deleted: Counter[str] = Counter()
    vector_count = 0
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        _delete_sqlite_memory_pack_receipts(connection, selector, deleted)
        profile = _sqlite_profile(connection, selector.relationship_id or "")
        if profile is None:
            raise ErasureSelectionError("relationship was not found in staging")
        _require_relation_match(selector, profile)
        relationship_id = str(profile["relationship_id"])
        turn_id = _required_text(selector.source_turn_id, "source_turn_id")
        turn_matches = connection.execute(
            """
            SELECT COUNT(*) FROM source_turns
            WHERE relationship_id = ? AND turn_id = ?
            """,
            (relationship_id, turn_id),
        ).fetchone()[0]
        if turn_matches != 1:
            raise ErasureSelectionError("source turn was not found exactly once in staging")

        adjudications = _sqlite_json_rows(
            connection,
            "relationship_adjudications",
            "decision_id",
            relationship_id,
            order_by_sequence=True,
        )
        initial_event_ids = {
            event_id
            for decision_id, raw in adjudications
            if raw.get("receipt", {}).get("source_turn_id") == turn_id
            for event_id in _raw_record_events(raw)
        }
        initial_decision_ids = {
            decision_id
            for decision_id, raw in adjudications
            if raw.get("receipt", {}).get("source_turn_id") == turn_id
        }
        direct_rows = _sqlite_json_rows(
            connection,
            "relationship_events",
            "event_id",
            relationship_id,
            order_by_sequence=True,
        )
        direct_events = [raw for _, raw in direct_rows]
        processing_rows = _sqlite_json_rows(
            connection,
            "relationship_processing_runs",
            "processing_id",
            relationship_id,
        )
        (
            event_ids,
            removed_decisions,
            affected_source_turn_ids,
            removed_runs,
        ) = _processing_dependency_closure(
            direct_events,
            [raw for _, raw in adjudications],
            [raw for _, raw in processing_rows],
            initial_event_ids,
            initial_decision_ids=initial_decision_ids,
            initial_source_turn_ids={turn_id},
        )
        _delete_sqlite_consequence_dependencies(
            connection,
            relationship_id,
            event_ids=event_ids,
            decision_ids=removed_decisions,
            source_turn_ids=affected_source_turn_ids,
            deleted=deleted,
        )
        rebuilt: Counter[str] = Counter()
        turn_rows = _sqlite_json_rows(
            connection,
            "source_turns",
            "turn_id",
            relationship_id,
        )
        remaining_turn_rows = [
            (item_id, raw)
            for item_id, raw in turn_rows
            if item_id != turn_id
        ]
        rebuilt_turns = _revoke_invalidated_turn_authority(
            [raw for _, raw in remaining_turn_rows],
            direct_events,
            [raw for _, raw in adjudications],
            removed_event_ids=event_ids,
            removed_decision_ids=removed_decisions,
            rebuilt=rebuilt,
        )
        for (item_id, _), raw in zip(remaining_turn_rows, rebuilt_turns):
            connection.execute(
                """
                UPDATE source_turns SET data = ?
                WHERE relationship_id = ? AND turn_id = ?
                """,
                (
                    json.dumps(raw, ensure_ascii=False),
                    relationship_id,
                    item_id,
                ),
            )
        reflection_decisions = _sqlite_json_rows(
            connection,
            "persona_reflection_decisions",
            "decision_id",
            relationship_id,
        )
        removed_reflection_decisions = {
            item_id
            for item_id, raw in reflection_decisions
            if raw.get("source_turn_id") in affected_source_turn_ids
            or raw.get("event_id") in event_ids
        }
        reflection_records = _sqlite_json_rows(
            connection,
            "persona_reflection_records",
            "reflection_id",
            relationship_id,
        )
        removed_reflections = {
            item_id
            for item_id, raw in reflection_records
            if raw.get("event_id") in event_ids
        }
        changed = True
        while changed:
            changed = False
            for item_id, raw in reflection_records:
                if item_id not in removed_reflections and raw.get(
                    "target_reflection_id"
                ) in removed_reflections:
                    removed_reflections.add(item_id)
                    changed = True
        removed_reflection_decisions.update(
            item_id
            for item_id, raw in reflection_decisions
            if raw.get("target_reflection_id") in removed_reflections
            or (
                isinstance(raw.get("reflection_record"), Mapping)
                and raw["reflection_record"].get("reflection_id") in removed_reflections
            )
        )
        growth_rows = _sqlite_json_rows(
            connection,
            "persona_growth_proposals",
            "proposal_id",
            relationship_id,
        )
        removed_growth = {
            item_id
            for item_id, raw in growth_rows
            if event_ids.intersection(raw.get("supporting_event_ids", ()))
        }

        archival_rows = _sqlite_json_rows(
            connection,
            "archival_records",
            "archival_id",
            relationship_id,
        )
        archival_ids = {
            item_id
            for item_id, raw in archival_rows
            if raw.get("receipt", {}).get("source_turn_id")
            in affected_source_turn_ids
        }
        tombstone_rows = _sqlite_json_rows(
            connection,
            "archival_tombstones",
            "archival_id",
            relationship_id,
        )
        archival_ids.update(
            item_id
            for item_id, raw in tombstone_rows
            if raw.get("source_turn_id") in affected_source_turn_ids
        )

        def delete_ids(table: str, column: str, ids: set[str], kind: str) -> None:
            for item_id in sorted(ids):
                deleted[kind] += connection.execute(
                    f"DELETE FROM {table} WHERE {column} = ?",  # noqa: S608
                    (item_id,),
                ).rowcount

        delete_ids(
            "persona_reflection_decisions",
            "decision_id",
            removed_reflection_decisions,
            "reflection_decision",
        )
        delete_ids(
            "persona_reflection_records",
            "reflection_id",
            removed_reflections,
            "persona_reflection",
        )
        delete_ids(
            "relationship_processing_runs",
            "processing_id",
            removed_runs,
            "processing_run",
        )
        delete_ids(
            "relationship_adjudications",
            "decision_id",
            removed_decisions,
            "relationship_adjudication",
        )
        direct_event_ids = {
            item_id for item_id, raw in direct_rows if raw.get("event_id") in event_ids
        }
        delete_ids(
            "relationship_events",
            "event_id",
            direct_event_ids,
            "relationship_event",
        )
        deleted["relationship_event"] += sum(
            len(_raw_record_events(raw))
            for item_id, raw in adjudications
            if item_id in removed_decisions
        )
        delete_ids(
            "persona_growth_proposals",
            "proposal_id",
            removed_growth,
            "persona_growth",
        )
        delete_ids(
            "archival_tombstones",
            "archival_id",
            archival_ids,
            "archival_tombstone",
        )
        delete_ids(
            "archival_records",
            "archival_id",
            archival_ids,
            "archival_record",
        )

        memory_rows = connection.execute(
            "SELECT node_id, data FROM memory_nodes WHERE agent_id = ? AND user_id = ?",
            (selector.agent_id, selector.user_id),
        ).fetchall()
        memory_ids = set()
        for row in memory_rows:
            raw = json.loads(row["data"])
            if raw.get("source_turn_id") in affected_source_turn_ids or raw.get(
                "source_archival_id"
            ) in archival_ids:
                memory_ids.add(str(row["node_id"]))
        delete_ids("memory_nodes", "node_id", memory_ids, "memory_node")
        vector_count += len(memory_ids)

        timeline_rows = connection.execute(
            "SELECT id, source_archival_id, data FROM timeline_entries "
            "WHERE agent_id = ? AND user_id = ?",
            (selector.agent_id, selector.user_id),
        ).fetchall()
        timeline_ids = set()
        for row in timeline_rows:
            raw = json.loads(row["data"]) if row["data"] else {}
            if raw.get("source_turn_id") in affected_source_turn_ids or row[
                "source_archival_id"
            ] in archival_ids:
                timeline_ids.add(str(row["id"]))
        delete_ids("timeline_entries", "id", timeline_ids, "timeline_entry")
        deleted["reply_attempt"] += connection.execute(
            "DELETE FROM reply_attempts WHERE relationship_id = ? AND turn_id = ?",
            (relationship_id, turn_id),
        ).rowcount
        deleted["source_turn"] += connection.execute(
            "DELETE FROM source_turns WHERE relationship_id = ? AND turn_id = ?",
            (relationship_id, turn_id),
        ).rowcount
        connection.commit()
    inventory = _inventory_with_rebuilds(
        _empty_inventory(deleted),
        rebuilt,
    )
    if vector_count:
        inventory = _inventory_with_rebuilds(
            inventory,
            rebuilt,
            delegated=Counter({"memory_vector_delete": vector_count}),
            unverified_external=Counter({"memory_vector": vector_count}),
        )
    return (relationship_id,), inventory


def _erase_sqlite_event(
    path: Path,
    selector: ErasureSelector,
) -> Tuple[Tuple[str, ...], ErasureInventory]:
    deleted: Counter[str] = Counter()
    vector_count = 0
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        _delete_sqlite_memory_pack_receipts(connection, selector, deleted)
        profile = _sqlite_profile(connection, selector.relationship_id or "")
        if profile is None:
            raise ErasureSelectionError("relationship was not found in staging")
        _require_relation_match(selector, profile)
        relationship_id = str(profile["relationship_id"])
        target_event_id = _required_text(
            selector.relationship_event_id,
            "relationship_event_id",
        )
        direct_rows = _sqlite_json_rows(
            connection,
            "relationship_events",
            "event_id",
            relationship_id,
        )
        direct_events = [raw for _, raw in direct_rows]
        adjudication_rows = _sqlite_json_rows(
            connection,
            "relationship_adjudications",
            "decision_id",
            relationship_id,
            order_by_sequence=True,
        )
        adjudications = [raw for _, raw in adjudication_rows]
        processing_rows = _sqlite_json_rows(
            connection,
            "relationship_processing_runs",
            "processing_id",
            relationship_id,
        )
        (
            event_ids,
            decision_ids,
            source_turn_ids,
            removed_runs,
        ) = _processing_dependency_closure(
            direct_events,
            adjudications,
            [raw for _, raw in processing_rows],
            {target_event_id},
        )
        _delete_sqlite_consequence_dependencies(
            connection,
            relationship_id,
            event_ids=event_ids,
            decision_ids=decision_ids,
            source_turn_ids=source_turn_ids,
            deleted=deleted,
        )
        rebuilt: Counter[str] = Counter()
        turn_rows = _sqlite_json_rows(
            connection,
            "source_turns",
            "turn_id",
            relationship_id,
        )
        rebuilt_turns = _revoke_invalidated_turn_authority(
            [raw for _, raw in turn_rows],
            direct_events,
            adjudications,
            removed_event_ids=event_ids,
            removed_decision_ids=decision_ids,
            rebuilt=rebuilt,
        )
        for (item_id, _), raw in zip(turn_rows, rebuilt_turns):
            connection.execute(
                """
                UPDATE source_turns SET data = ?
                WHERE relationship_id = ? AND turn_id = ?
                """,
                (
                    json.dumps(raw, ensure_ascii=False),
                    relationship_id,
                    item_id,
                ),
            )

        def delete_ids(table: str, column: str, ids: set[str], kind: str) -> None:
            for item_id in sorted(ids):
                deleted[kind] += connection.execute(
                    f"DELETE FROM {table} WHERE {column} = ?",  # noqa: S608
                    (item_id,),
                ).rowcount

        direct_ids = {
            item_id for item_id, raw in direct_rows if raw.get("event_id") in event_ids
        }
        delete_ids(
            "relationship_events",
            "event_id",
            direct_ids,
            "relationship_event",
        )
        removed_adjudicated_event_count = sum(
            len(_raw_record_events(raw))
            for item_id, raw in adjudication_rows
            if item_id in decision_ids
        )
        delete_ids(
            "relationship_adjudications",
            "decision_id",
            decision_ids,
            "relationship_adjudication",
        )
        deleted["relationship_event"] += removed_adjudicated_event_count

        reflection_decisions = _sqlite_json_rows(
            connection,
            "persona_reflection_decisions",
            "decision_id",
            relationship_id,
        )
        removed_reflection_decisions = {
            item_id
            for item_id, raw in reflection_decisions
            if raw.get("source_turn_id") in source_turn_ids
            or raw.get("event_id") in event_ids
        }
        reflection_records = _sqlite_json_rows(
            connection,
            "persona_reflection_records",
            "reflection_id",
            relationship_id,
        )
        removed_reflections = {
            item_id
            for item_id, raw in reflection_records
            if raw.get("event_id") in event_ids
        }
        changed = True
        while changed:
            changed = False
            for item_id, raw in reflection_records:
                if item_id not in removed_reflections and raw.get(
                    "target_reflection_id"
                ) in removed_reflections:
                    removed_reflections.add(item_id)
                    changed = True
        removed_reflection_decisions.update(
            item_id
            for item_id, raw in reflection_decisions
            if raw.get("target_reflection_id") in removed_reflections
            or (
                isinstance(raw.get("reflection_record"), Mapping)
                and raw["reflection_record"].get("reflection_id") in removed_reflections
            )
        )
        growth_rows = _sqlite_json_rows(
            connection,
            "persona_growth_proposals",
            "proposal_id",
            relationship_id,
        )
        removed_growth = {
            item_id
            for item_id, raw in growth_rows
            if event_ids.intersection(raw.get("supporting_event_ids", ()))
        }
        delete_ids(
            "persona_reflection_decisions",
            "decision_id",
            removed_reflection_decisions,
            "reflection_decision",
        )
        delete_ids(
            "persona_reflection_records",
            "reflection_id",
            removed_reflections,
            "persona_reflection",
        )
        delete_ids(
            "relationship_processing_runs",
            "processing_id",
            removed_runs,
            "processing_run",
        )
        delete_ids(
            "persona_growth_proposals",
            "proposal_id",
            removed_growth,
            "persona_growth",
        )

        archival_rows = _sqlite_json_rows(
            connection,
            "archival_records",
            "archival_id",
            relationship_id,
        )
        archival_ids = {
            item_id
            for item_id, raw in archival_rows
            if raw.get("receipt", {}).get("source_turn_id") in source_turn_ids
        }
        tombstone_rows = _sqlite_json_rows(
            connection,
            "archival_tombstones",
            "archival_id",
            relationship_id,
        )
        archival_ids.update(
            item_id
            for item_id, raw in tombstone_rows
            if raw.get("source_turn_id") in source_turn_ids
        )
        delete_ids(
            "archival_tombstones",
            "archival_id",
            archival_ids,
            "archival_tombstone",
        )
        delete_ids(
            "archival_records",
            "archival_id",
            archival_ids,
            "archival_record",
        )
        memory_rows = connection.execute(
            "SELECT node_id, data FROM memory_nodes WHERE agent_id = ? AND user_id = ?",
            (selector.agent_id, selector.user_id),
        ).fetchall()
        memory_ids = set()
        for row in memory_rows:
            raw = json.loads(row["data"])
            if raw.get("source_turn_id") in source_turn_ids or raw.get(
                "source_archival_id"
            ) in archival_ids:
                memory_ids.add(str(row["node_id"]))
        delete_ids("memory_nodes", "node_id", memory_ids, "memory_node")
        vector_count += len(memory_ids)
        timeline_rows = connection.execute(
            "SELECT id, source_archival_id, data FROM timeline_entries "
            "WHERE agent_id = ? AND user_id = ?",
            (selector.agent_id, selector.user_id),
        ).fetchall()
        timeline_ids = set()
        for row in timeline_rows:
            raw = json.loads(row["data"]) if row["data"] else {}
            if raw.get("source_turn_id") in source_turn_ids or row[
                "source_archival_id"
            ] in archival_ids:
                timeline_ids.add(str(row["id"]))
        delete_ids("timeline_entries", "id", timeline_ids, "timeline_entry")
        connection.commit()
    inventory = _inventory_with_rebuilds(
        _empty_inventory(deleted),
        rebuilt,
    )
    if vector_count:
        inventory = _inventory_with_rebuilds(
            inventory,
            rebuilt,
            delegated=Counter({"memory_vector_delete": vector_count}),
            unverified_external=Counter({"memory_vector": vector_count}),
        )
    return (relationship_id,), inventory


def _erase_sqlite_complete_user(
    path: Path,
    selector: ErasureSelector,
) -> Tuple[Tuple[str, ...], ErasureInventory]:
    user_id = _required_text(selector.user_id, "user_id")
    user_identity_id = _required_text(
        selector.user_identity_id,
        "user_identity_id",
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        identities = connection.execute(
            """
            SELECT identity_id FROM stable_identities
            WHERE kind = 'user' AND external_id = ?
            """,
            (user_id,),
        ).fetchall()
        if len(identities) != 1 or identities[0]["identity_id"] != user_identity_id:
            raise ErasureSelectionError(
                "user identity does not match the staging registry"
            )
        rows = connection.execute(
            "SELECT * FROM relationships WHERE user_id = ? ORDER BY relationship_id",
            (user_id,),
        ).fetchall()
    if not rows:
        raise ErasureSelectionError("complete user scope matched no relationships")
    if any(row["user_identity_id"] != user_identity_id for row in rows):
        raise ErasureSelectionError("complete user scope has ambiguous identity bindings")
    affected = []
    inventories = []
    for row in rows:
        current_affected, inventory = _erase_sqlite_relationship(
            path,
            ErasureSelector(
                scope=ErasureScope.RELATIONSHIP,
                agent_id=str(row["agent_id"]),
                user_id=user_id,
                relationship_id=str(row["relationship_id"]),
            ),
        )
        affected.extend(current_affected)
        inventories.append(inventory)
    receipt_deletions: Counter[str] = Counter()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _delete_sqlite_memory_pack_receipts(
            connection,
            selector,
            receipt_deletions,
        )
        connection.commit()
    if receipt_deletions:
        inventories.append(_empty_inventory(receipt_deletions))
    return tuple(sorted(affected)), _merge_inventories(inventories)


def _verified_tension_projection(
    storage: FileStorage | SQLiteStorage,
    relationship_id: str,
) -> Tuple[
    list[RelationshipConsequence],
    list[NarrativeTensionLink],
    Tuple[NarrativeTensionProjection, ...],
]:
    try:
        consequences = storage.list_relationship_consequences(relationship_id)
        links = storage.list_narrative_tension_links(relationship_id)
        adjudications = storage.list_relationship_adjudications(relationship_id)
        turns = storage.list_turn_records(relationship_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise ErasureSelectionError(
            "relationship consequence history is malformed"
        ) from exc

    decisions = {item.receipt.decision_id: item for item in adjudications}
    if len(decisions) != len(adjudications):
        raise ErasureSelectionError(
            "relationship consequence source decisions are ambiguous"
        )
    turns_by_id = {item.turn_id: item for item in turns}
    if len(turns_by_id) != len(turns):
        raise ErasureSelectionError(
            "relationship consequence source turns are ambiguous"
        )

    for item in (*consequences, *links):
        if item.relationship_id != relationship_id:
            raise ErasureSelectionError(
                "relationship consequence history crosses relationship scope"
            )
        record = decisions.get(item.source_decision_id)
        if record is None:
            raise ErasureSelectionError(
                "relationship consequence history has a missing source decision"
            )
        receipt = record.receipt
        if (
            receipt.relationship_id != relationship_id
            or receipt.source_turn_id != item.source_turn_id
            or receipt.source_revision != item.source_revision
            or item.source_event_id not in receipt.event_ids
            or not any(
                event.event_id == item.source_event_id for event in record.events
            )
        ):
            raise ErasureSelectionError(
                "relationship consequence history has inconsistent source authority"
            )
        turn = turns_by_id.get(item.source_turn_id)
        if turn is None or turn.source_revision != item.source_revision:
            raise ErasureSelectionError(
                "relationship consequence history has a missing source turn"
            )

    try:
        projections = NarrativeTensionProjector.project(consequences, links)
    except (KeyError, TypeError, ValueError) as exc:
        raise ErasureSelectionError(
            "Narrative Tension projection cannot be rebuilt"
        ) from exc
    return consequences, links, projections


def _rebuild_relationship(
    staging_path: str,
    storage_kind: ErasureStorageKind,
    selector: ErasureSelector,
) -> Tuple[RelationshipRebuildProof, Counter[str]]:
    storage = (
        FileStorage(staging_path)
        if storage_kind is ErasureStorageKind.FILE_STORAGE
        else SQLiteStorage(staging_path)
    )
    profile = storage.get_relationship(
        _required_text(selector.agent_id, "agent_id"),
        _required_text(selector.user_id, "user_id"),
    )
    if profile is None or profile.relationship_id != selector.relationship_id:
        raise ErasureSelectionError("relationship changed during staging rebuild")
    events = list_complete_relationship_events(storage, profile.relationship_id)
    TemporalHistoryValidator.validate_complete_history(events)
    snapshot = RelationshipProjector.project(profile, events)
    consolidation = RelationshipConsolidator.project(profile.relationship_id, events)
    consequences, tension_links, tensions = _verified_tension_projection(
        storage,
        profile.relationship_id,
    )
    proof = RelationshipRebuildProof(
        relationship_id=profile.relationship_id,
        event_count=len(events),
        state_digest=_json_digest(snapshot.state.to_dict()),
        belief_digest=_json_digest(
            {key: value.to_dict() for key, value in snapshot.beliefs.items()}
        ),
        consolidation_digest=_json_digest(consolidation.to_dict()),
        episode_count=len(consolidation.episodes),
        chapter_count=len(consolidation.chapters),
        consequence_count=len(consequences),
        tension_link_count=len(tension_links),
        tension_count=len(tensions),
        tension_digest=_json_digest([item.to_dict() for item in tensions]),
    )
    return proof, Counter(
        {
            "relationship_state": 1,
            "current_belief": len(snapshot.beliefs),
            "episode": len(consolidation.episodes),
            "relationship_chapter": len(consolidation.chapters),
            "narrative_tension": len(tensions),
        }
    )


def validate_staged_storage_semantics(
    staging_path: str,
    storage_kind: ErasureStorageKind,
    selector: ErasureSelector,
) -> None:
    """Requires an affected relationship to survive a full MemoryPack round trip.

    Physical format inspection cannot prove that frozen journal prefixes,
    Source Turns, processing runs, and derived records still agree.  A
    staging-only export followed by import into a disposable fresh store runs
    the production MemoryPack semantic validators before the coordinator is
    allowed to publish the mutation.
    """

    if selector.scope is ErasureScope.COMPLETE_USER:
        return
    agent_id = _required_text(selector.agent_id, "agent_id")
    user_id = _required_text(selector.user_id, "user_id")
    storage = (
        FileStorage(staging_path)
        if storage_kind is ErasureStorageKind.FILE_STORAGE
        else SQLiteStorage(staging_path)
    )
    profile = storage.get_relationship(agent_id, user_id)
    if profile is None and selector.scope is ErasureScope.RELATIONSHIP:
        return
    if profile is None or profile.relationship_id != selector.relationship_id:
        raise ErasureSelectionError(
            "affected relationship is missing after staging mutation"
        )

    from erii.engine import ERIIEngine

    try:
        with tempfile.TemporaryDirectory(
            prefix="erii-lifecycle-semantic-validation-"
        ) as temporary:
            temporary_root = Path(temporary)
            pack_path = temporary_root / "relationship.erii"
            with ERIIEngine(storage_driver=storage) as source_engine:
                source_engine.export_memory(
                    agent_id,
                    user_id,
                    export_path=str(pack_path),
                )
            fresh_path = (
                temporary_root / "fresh-file-storage"
                if storage_kind is ErasureStorageKind.FILE_STORAGE
                else temporary_root / "fresh.sqlite3"
            )
            fresh_storage = (
                FileStorage(str(fresh_path))
                if storage_kind is ErasureStorageKind.FILE_STORAGE
                else SQLiteStorage(str(fresh_path))
            )
            with ERIIEngine(storage_driver=fresh_storage) as target_engine:
                target_engine.import_memory(str(pack_path))
                imported = target_engine.storage.get_relationship(
                    agent_id,
                    user_id,
                )
                if (
                    imported is None
                    or imported.relationship_id != profile.relationship_id
                ):
                    raise ValueError(
                        "MemoryPack round trip changed relationship identity"
                    )
    except Exception as exc:
        raise ErasureSelectionError(
            "staged lifecycle mutation is not semantically portable"
        ) from exc


def rebuild_staged_storage(
    staging_path: str,
    storage_kind: ErasureStorageKind,
    selector: ErasureSelector,
) -> ErasureTransformResult:
    """Recomputes projections without deleting any authoritative history."""

    if selector.scope is not ErasureScope.RELATIONSHIP:
        raise ErasureSelectionError(
            "deterministic rebuild currently requires an exact relationship selector"
        )
    inspection = inspect_erasure_scope(staging_path, storage_kind, selector)
    proof, rebuilt = _rebuild_relationship(staging_path, storage_kind, selector)
    validate_staged_storage_semantics(staging_path, storage_kind, selector)
    return ErasureTransformResult(
        storage_kind=inspection.storage_kind,
        selector=selector,
        affected_relationship_ids=inspection.affected_relationship_ids,
        rebuild_proofs=(proof,),
        inventory=ErasureInventory(
            counts={
                "deleted": {},
                "rebuilt": dict(rebuilt),
                "delegated": {},
                "unverified_external": {},
            }
        ),
    )


def erase_staged_storage(
    staging_path: str,
    storage_kind: ErasureStorageKind,
    selector: ErasureSelector,
) -> ErasureTransformResult:
    """Mutates one explicit staging copy and never discovers a live source.

    The caller is responsible for proving that ``staging_path`` is disposable,
    private, and detached from the live source before invoking this function.
    """

    if isinstance(storage_kind, str):
        storage_kind = ErasureStorageKind(storage_kind)
    if not isinstance(selector, ErasureSelector):
        raise TypeError("erase_staged_storage() requires an ErasureSelector")
    path = Path(staging_path)
    inspection = inspect_erasure_scope(staging_path, storage_kind, selector)
    if storage_kind is ErasureStorageKind.FILE_STORAGE:
        if not path.is_dir():
            raise ErasureSelectionError("FileStorage staging path must be a directory")
        if selector.scope is ErasureScope.RELATIONSHIP:
            affected, inventory = _erase_file_relationship(path, selector)
        elif selector.scope is ErasureScope.SOURCE_TURN:
            affected, inventory = _erase_file_turn(path, selector)
        elif selector.scope is ErasureScope.RELATIONSHIP_EVENT:
            affected, inventory = _erase_file_event(path, selector)
        elif selector.scope is ErasureScope.COMPLETE_USER:
            affected, inventory = _erase_file_complete_user(path, selector)
        else:
            raise NotImplementedError("this erasure scope is not implemented yet")
    else:
        if not path.is_file():
            raise ErasureSelectionError("SQLite staging path must be a file")
        if selector.scope is ErasureScope.RELATIONSHIP:
            affected, inventory = _erase_sqlite_relationship(path, selector)
        elif selector.scope is ErasureScope.SOURCE_TURN:
            affected, inventory = _erase_sqlite_turn(path, selector)
        elif selector.scope is ErasureScope.RELATIONSHIP_EVENT:
            affected, inventory = _erase_sqlite_event(path, selector)
        elif selector.scope is ErasureScope.COMPLETE_USER:
            affected, inventory = _erase_sqlite_complete_user(path, selector)
        else:
            raise NotImplementedError("this erasure scope is not implemented yet")
    if affected != inspection.affected_relationship_ids:
        raise ErasureSelectionError("erasure scope changed after read-only inspection")
    rebuild_proofs: Tuple[RelationshipRebuildProof, ...] = ()
    if selector.scope in {
        ErasureScope.SOURCE_TURN,
        ErasureScope.RELATIONSHIP_EVENT,
    }:
        proof, rebuilt = _rebuild_relationship(staging_path, storage_kind, selector)
        rebuild_proofs = (proof,)
        rebuilt.update(inventory.counts["rebuilt"])
        inventory = _inventory_with_rebuilds(
            inventory,
            rebuilt,
            delegated=Counter(inventory.counts["delegated"]),
            unverified_external=Counter(
                inventory.counts["unverified_external"]
            ),
        )
    validate_staged_storage_semantics(staging_path, storage_kind, selector)
    return ErasureTransformResult(
        storage_kind=storage_kind,
        selector=selector,
        affected_relationship_ids=affected,
        rebuild_proofs=rebuild_proofs,
        inventory=inventory,
    )


__all__ = [
    "ErasureInventory",
    "ErasureScope",
    "ErasureScopeInspection",
    "ErasureSelectionError",
    "ErasureSelector",
    "ErasureStorageKind",
    "ErasureTransformResult",
    "RelationshipRebuildProof",
    "erase_staged_storage",
    "inspect_erasure_scope",
    "rebuild_staged_storage",
    "validate_staged_storage_semantics",
]
