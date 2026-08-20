"""Read-only exact-scope inspection for lifecycle erasure planning."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence, Tuple

from erii._lifecycle.filesystem import (
    assert_no_link_or_reparse_ancestors,
    read_stable_bytes,
    require_regular_directory,
    require_regular_file,
    sqlite_uri,
)
from erii.core.temporal_history import TemporalHistoryValidator
from erii.lifecycle_erasure_contracts import (
    ErasureInventory,
    ErasureScope,
    ErasureScopeInspection,
    ErasureSelectionError,
    ErasureSelector,
    ErasureStorageKind,
    _required_text,
)
from erii.models.consequence import NarrativeTensionLink, RelationshipConsequence
from erii.models.relationship import RelationshipEvent


def _empty_inventory(deleted: Counter[str]) -> ErasureInventory:
    return ErasureInventory(
        counts={
            "deleted": dict(deleted),
            "rebuilt": {},
            "delegated": {},
            "unverified_external": {},
        }
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(read_stable_bytes(path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ErasureSelectionError(
            f"staging artifact {path.name!r} is unreadable"
        ) from exc


def _file_profiles(root: Path) -> dict[str, Tuple[Path, Mapping[str, Any]]]:
    profiles: dict[str, Tuple[Path, Mapping[str, Any]]] = {}
    for path in root.glob("*/*/relationship.json"):
        raw = _read_json(path)
        if not isinstance(raw, Mapping):
            raise ErasureSelectionError("relationship profile is malformed")
        relationship_id = _required_text(raw.get("relationship_id"), "relationship_id")
        if relationship_id in profiles:
            raise ErasureSelectionError(
                "relationship identity is ambiguous in staging"
            )
        profiles[relationship_id] = (path, raw)
    return profiles


def _require_relation_match(
    selector: ErasureSelector,
    profile: Mapping[str, Any],
) -> None:
    if (
        profile["relationship_id"] != selector.relationship_id
        or profile["agent_id"] != selector.agent_id
        or profile["user_id"] != selector.user_id
    ):
        raise ErasureSelectionError(
            "agent_id, user_id, and relationship_id do not identify one relationship"
        )


def _digest_path(directory: str, stable_id: str) -> Path:
    digest = hashlib.sha256(stable_id.encode("utf-8")).hexdigest()
    return Path(directory, f"{digest}.json")


def _file_consequence_journal_paths(
    root: Path,
    relationship_id: str,
) -> Tuple[Path, Path]:
    return (
        _digest_path(str(root / "_relationship_consequences"), relationship_id),
        _digest_path(str(root / "_narrative_tension_links"), relationship_id),
    )


def _load_file_consequence_journals(
    root: Path,
    relationship_id: str,
) -> Tuple[
    Path,
    Path,
    list[RelationshipConsequence],
    list[NarrativeTensionLink],
]:
    consequence_path, link_path = _file_consequence_journal_paths(
        root,
        relationship_id,
    )
    raw_consequences = _read_json(consequence_path) if consequence_path.exists() else []
    raw_links = _read_json(link_path) if link_path.exists() else []
    if not isinstance(raw_consequences, list):
        raise ErasureSelectionError(
            "relationship consequence journal is malformed"
        )
    if not isinstance(raw_links, list):
        raise ErasureSelectionError(
            "Narrative Tension link journal is malformed"
        )
    try:
        consequences = [
            RelationshipConsequence.from_dict(item) for item in raw_consequences
        ]
        links = [NarrativeTensionLink.from_dict(item) for item in raw_links]
    except (KeyError, TypeError, ValueError) as exc:
        raise ErasureSelectionError(
            "relationship consequence history is malformed"
        ) from exc
    if any(item.relationship_id != relationship_id for item in consequences):
        raise ErasureSelectionError(
            "relationship consequence journal crosses relationship scope"
        )
    if any(item.relationship_id != relationship_id for item in links):
        raise ErasureSelectionError(
            "Narrative Tension link journal crosses relationship scope"
        )
    return consequence_path, link_path, consequences, links


def _consequence_dependency_ids(
    consequences: Sequence[RelationshipConsequence],
    links: Sequence[NarrativeTensionLink],
    *,
    event_ids: set[str],
    decision_ids: set[str],
    source_turn_ids: set[str],
    delete_all: bool = False,
) -> Tuple[set[str], set[str]]:
    removed_consequences = {
        item.consequence_id
        for item in consequences
        if delete_all
        or item.source_event_id in event_ids
        or item.source_decision_id in decision_ids
        or item.source_turn_id in source_turn_ids
    }
    removed_links = {
        item.link_id
        for item in links
        if delete_all
        or item.consequence_id in removed_consequences
        or item.source_event_id in event_ids
        or item.source_decision_id in decision_ids
        or item.source_turn_id in source_turn_ids
    }
    return removed_consequences, removed_links


def _add_consequence_deletion_estimate(
    deleted: Counter[str],
    consequences: Sequence[RelationshipConsequence],
    links: Sequence[NarrativeTensionLink],
    *,
    event_ids: set[str],
    decision_ids: set[str],
    source_turn_ids: set[str],
    delete_all: bool = False,
) -> None:
    removed_consequences, removed_links = _consequence_dependency_ids(
        consequences,
        links,
        event_ids=event_ids,
        decision_ids=decision_ids,
        source_turn_ids=source_turn_ids,
        delete_all=delete_all,
    )
    deleted["relationship_consequence"] += len(removed_consequences)
    deleted["narrative_tension_link"] += len(removed_links)


def _sqlite_profile(connection: sqlite3.Connection, relationship_id: str):
    return connection.execute(
        "SELECT * FROM relationships WHERE relationship_id = ?",
        (relationship_id,),
    ).fetchone()


def _sqlite_json_rows(
    connection: sqlite3.Connection,
    table: str,
    id_column: str,
    relationship_id: str,
    *,
    order_by_sequence: bool = False,
) -> list[Tuple[str, Mapping[str, Any]]]:
    order_clause = " ORDER BY sequence ASC" if order_by_sequence else ""
    rows = connection.execute(
        (
            f"SELECT {id_column}, data FROM {table} "
            f"WHERE relationship_id = ?{order_clause}"  # noqa: S608
        ),
        (relationship_id,),
    ).fetchall()
    result = []
    for row in rows:
        try:
            raw = json.loads(row["data"])
        except (TypeError, ValueError) as exc:
            raise ErasureSelectionError(
                f"{table} contains malformed JSON"
            ) from exc
        if not isinstance(raw, Mapping):
            raise ErasureSelectionError(f"{table} contains malformed records")
        result.append((str(row[id_column]), raw))
    return result


def _load_sqlite_consequence_journals(
    connection: sqlite3.Connection,
    relationship_id: str,
) -> Tuple[list[RelationshipConsequence], list[NarrativeTensionLink]]:
    consequence_rows = _sqlite_json_rows(
        connection,
        "relationship_consequences",
        "consequence_id",
        relationship_id,
        order_by_sequence=True,
    )
    link_rows = _sqlite_json_rows(
        connection,
        "narrative_tension_links",
        "link_id",
        relationship_id,
        order_by_sequence=True,
    )
    try:
        consequences = [
            RelationshipConsequence.from_dict(raw) for _, raw in consequence_rows
        ]
        links = [NarrativeTensionLink.from_dict(raw) for _, raw in link_rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise ErasureSelectionError(
            "relationship consequence history is malformed"
        ) from exc
    if any(
        row_id != item.consequence_id or item.relationship_id != relationship_id
        for (row_id, _), item in zip(consequence_rows, consequences)
    ):
        raise ErasureSelectionError(
            "relationship consequence row identity is inconsistent"
        )
    if any(
        row_id != item.link_id or item.relationship_id != relationship_id
        for (row_id, _), item in zip(link_rows, links)
    ):
        raise ErasureSelectionError(
            "Narrative Tension link row identity is inconsistent"
        )
    return consequences, links


def _file_relation(
    root: Path,
    selector: ErasureSelector,
) -> Tuple[Path, Mapping[str, Any], str]:
    matched = _file_profiles(root).get(selector.relationship_id or "")
    if matched is None:
        raise ErasureSelectionError("relationship was not found in staging")
    profile_path, profile = matched
    _require_relation_match(selector, profile)
    return profile_path, profile, str(profile["relationship_id"])


def _raw_record_events(record: Mapping[str, Any]) -> Tuple[str, ...]:
    events = record.get("events", ())
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise ErasureSelectionError(
            "relationship adjudication events are malformed"
        )
    return tuple(
        _required_text(item.get("event_id"), "event_id")
        for item in events
        if isinstance(item, Mapping)
    )


def _cascade_event_deletions(
    direct_events: Sequence[Mapping[str, Any]],
    adjudications: Sequence[Mapping[str, Any]],
    initial_event_ids: set[str],
    *,
    initial_decision_ids: set[str] | None = None,
) -> Tuple[set[str], set[str], set[str]]:
    """Close event deletion over journal and causal dependencies."""
    all_raw_events = [
        *direct_events,
        *(
            event
            for record in adjudications
            for event in record.get("events", ())
            if isinstance(event, Mapping)
        ),
    ]
    event_occurrences = Counter(
        str(item.get("event_id"))
        for item in all_raw_events
        if isinstance(item.get("event_id"), str)
    )
    decision_occurrences = Counter(
        str(receipt.get("decision_id"))
        for record in adjudications
        if isinstance((receipt := record.get("receipt")), Mapping)
        and isinstance(receipt.get("decision_id"), str)
    )
    removed_events = set(initial_event_ids)
    removed_decisions = set(initial_decision_ids or ())
    removed_source_turns: set[str] = set()
    if any(
        decision_occurrences[decision_id] != 1
        for decision_id in removed_decisions
    ):
        raise ErasureSelectionError(
            "relationship adjudication was not found exactly once in staging"
        )
    for record in adjudications:
        receipt = record.get("receipt", {})
        if receipt.get("decision_id") not in removed_decisions:
            continue
        removed_events.update(_raw_record_events(record))
        source_turn_id = receipt.get("source_turn_id")
        if isinstance(source_turn_id, str):
            removed_source_turns.add(source_turn_id)
    if any(event_occurrences[event_id] != 1 for event_id in removed_events):
        raise ErasureSelectionError(
            "relationship event was not found exactly once in staging"
        )
    try:
        models = [RelationshipEvent.from_dict(item) for item in all_raw_events]
        prerequisites = TemporalHistoryValidator.causal_prerequisites(models)
    except (KeyError, TypeError, ValueError) as exc:
        raise ErasureSelectionError(
            "relationship event history is malformed"
        ) from exc

    changed = True
    while changed:
        changed = False
        for record in adjudications:
            receipt = record.get("receipt", {})
            record_event_ids = set(_raw_record_events(record))
            references = set(receipt.get("event_ids", ()))
            related = receipt.get("related_event_id")
            if isinstance(related, str):
                references.add(related)
            if not (record_event_ids | references).intersection(removed_events):
                continue
            decision_id = receipt.get("decision_id")
            source_turn_id = receipt.get("source_turn_id")
            if isinstance(decision_id, str):
                removed_decisions.add(decision_id)
            if isinstance(source_turn_id, str):
                removed_source_turns.add(source_turn_id)
            additions = record_event_ids.difference(removed_events)
            if additions:
                removed_events.update(additions)
                changed = True
        for raw_event in all_raw_events:
            event_id = raw_event.get("event_id")
            if not isinstance(event_id, str) or event_id in removed_events:
                continue
            metadata = raw_event.get("metadata", {})
            adjudication = (
                metadata.get("adjudication", {})
                if isinstance(metadata, Mapping)
                else {}
            )
            references = set(
                adjudication.get("references", ())
                if isinstance(adjudication, Mapping)
                else ()
            )
            references.update(prerequisites.get(event_id, set()))
            if references.intersection(removed_events):
                removed_events.add(event_id)
                changed = True
    for record in adjudications:
        if set(_raw_record_events(record)).intersection(removed_events):
            receipt = record.get("receipt", {})
            decision_id = receipt.get("decision_id")
            source_turn_id = receipt.get("source_turn_id")
            if isinstance(decision_id, str):
                removed_decisions.add(decision_id)
            if isinstance(source_turn_id, str):
                removed_source_turns.add(source_turn_id)
    return removed_events, removed_decisions, removed_source_turns


def _estimate_file_scope(
    root: Path,
    selector: ErasureSelector,
) -> Tuple[Tuple[str, ...], ErasureInventory]:
    deleted: Counter[str] = Counter()
    if selector.scope is ErasureScope.COMPLETE_USER:
        registry = _read_json(root / "_relationship_identities.json")
        user_id = _required_text(selector.user_id, "user_id")
        identity_id = _required_text(
            selector.user_identity_id,
            "user_identity_id",
        )
        if registry.get("user", {}).get(user_id) != identity_id:
            raise ErasureSelectionError(
                "user identity does not match the staging registry"
            )
        profiles = [
            raw
            for _, raw in _file_profiles(root).values()
            if raw.get("user_id") == user_id
        ]
        if not profiles or any(
            raw.get("user_identity_id") != identity_id for raw in profiles
        ):
            raise ErasureSelectionError(
                "complete user scope is missing or has ambiguous identity bindings"
            )
        affected = tuple(sorted(str(raw["relationship_id"]) for raw in profiles))
        deleted["relationship"] = len(affected)
        return affected, _empty_inventory(deleted)

    _, _, relationship_id = _file_relation(root, selector)
    _, _, consequences, tension_links = _load_file_consequence_journals(
        root,
        relationship_id,
    )
    if selector.scope is ErasureScope.RELATIONSHIP:
        deleted["relationship"] = 1
        event_path = _digest_path(
            str(root / "_relationship_events"),
            relationship_id,
        )
        adjudication_path = _digest_path(
            str(root / "_relationship_adjudications"),
            relationship_id,
        )
        direct = list(_read_json(event_path)) if event_path.exists() else []
        adjudications = (
            list(_read_json(adjudication_path))
            if adjudication_path.exists()
            else []
        )
        deleted["relationship_event"] = len(direct) + sum(
            len(_raw_record_events(item)) for item in adjudications
        )
        deleted["relationship_adjudication"] = len(adjudications)
        _add_consequence_deletion_estimate(
            deleted,
            consequences,
            tension_links,
            event_ids=set(),
            decision_ids=set(),
            source_turn_ids=set(),
            delete_all=True,
        )
    elif selector.scope is ErasureScope.SOURCE_TURN:
        turn_id = _required_text(selector.source_turn_id, "source_turn_id")
        turn_path = _digest_path(str(root / "_turn_records"), relationship_id)
        turns = list(_read_json(turn_path)) if turn_path.exists() else []
        if sum(item.get("turn_id") == turn_id for item in turns) != 1:
            raise ErasureSelectionError(
                "source turn was not found exactly once in staging"
            )
        adjudication_path = _digest_path(
            str(root / "_relationship_adjudications"),
            relationship_id,
        )
        adjudications = (
            list(_read_json(adjudication_path))
            if adjudication_path.exists()
            else []
        )
        removed = [
            item
            for item in adjudications
            if item.get("receipt", {}).get("source_turn_id") == turn_id
        ]
        deleted["source_turn"] = 1
        deleted["relationship_adjudication"] = len(removed)
        deleted["relationship_event"] = sum(
            len(_raw_record_events(item)) for item in removed
        )
        _add_consequence_deletion_estimate(
            deleted,
            consequences,
            tension_links,
            event_ids={
                event_id
                for item in removed
                for event_id in _raw_record_events(item)
            },
            decision_ids={
                _required_text(
                    item.get("receipt", {}).get("decision_id"),
                    "decision_id",
                )
                for item in removed
            },
            source_turn_ids={turn_id},
        )
    else:
        target = _required_text(
            selector.relationship_event_id,
            "relationship_event_id",
        )
        direct_path = _digest_path(
            str(root / "_relationship_events"),
            relationship_id,
        )
        adjudication_path = _digest_path(
            str(root / "_relationship_adjudications"),
            relationship_id,
        )
        direct = list(_read_json(direct_path)) if direct_path.exists() else []
        adjudications = (
            list(_read_json(adjudication_path))
            if adjudication_path.exists()
            else []
        )
        event_ids, decision_ids, source_turn_ids = _cascade_event_deletions(
            direct,
            adjudications,
            {target},
        )
        deleted["relationship_event"] = len(event_ids)
        deleted["relationship_adjudication"] = len(decision_ids)
        _add_consequence_deletion_estimate(
            deleted,
            consequences,
            tension_links,
            event_ids=event_ids,
            decision_ids=decision_ids,
            source_turn_ids=source_turn_ids,
        )
    return (relationship_id,), _empty_inventory(deleted)


def _open_sqlite_read_only(path: Path) -> sqlite3.Connection:
    try:
        assert_no_link_or_reparse_ancestors(
            path,
            label="SQLite erasure inspection target",
        )
        require_regular_file(path, label="SQLite erasure inspection target")
        connection = sqlite3.connect(sqlite_uri(path, immutable=True), uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise ErasureSelectionError(
            "SQLite staging copy is unreadable"
        ) from exc


def _estimate_sqlite_scope(
    path: Path,
    selector: ErasureSelector,
) -> Tuple[Tuple[str, ...], ErasureInventory]:
    deleted: Counter[str] = Counter()
    with closing(_open_sqlite_read_only(path)) as connection:
        if selector.scope is ErasureScope.COMPLETE_USER:
            user_id = _required_text(selector.user_id, "user_id")
            identity_id = _required_text(
                selector.user_identity_id,
                "user_identity_id",
            )
            identities = connection.execute(
                "SELECT identity_id FROM stable_identities "
                "WHERE kind = 'user' AND external_id = ?",
                (user_id,),
            ).fetchall()
            rows = connection.execute(
                "SELECT * FROM relationships "
                "WHERE user_id = ? ORDER BY relationship_id",
                (user_id,),
            ).fetchall()
            if (
                len(identities) != 1
                or identities[0]["identity_id"] != identity_id
                or not rows
                or any(row["user_identity_id"] != identity_id for row in rows)
            ):
                raise ErasureSelectionError(
                    "complete user scope is missing or has ambiguous identity bindings"
                )
            affected = tuple(str(row["relationship_id"]) for row in rows)
            deleted["relationship"] = len(affected)
            return affected, _empty_inventory(deleted)

        profile = _sqlite_profile(connection, selector.relationship_id or "")
        if profile is None:
            raise ErasureSelectionError("relationship was not found in staging")
        _require_relation_match(selector, profile)
        relationship_id = str(profile["relationship_id"])
        consequences, tension_links = _load_sqlite_consequence_journals(
            connection,
            relationship_id,
        )
        if selector.scope is ErasureScope.RELATIONSHIP:
            deleted["relationship"] = 1
            deleted["relationship_event"] = connection.execute(
                "SELECT COUNT(*) FROM relationship_events "
                "WHERE relationship_id = ?",
                (relationship_id,),
            ).fetchone()[0]
            adjudications = _sqlite_json_rows(
                connection,
                "relationship_adjudications",
                "decision_id",
                relationship_id,
            )
            deleted["relationship_adjudication"] = len(adjudications)
            deleted["relationship_event"] += sum(
                len(_raw_record_events(raw)) for _, raw in adjudications
            )
            _add_consequence_deletion_estimate(
                deleted,
                consequences,
                tension_links,
                event_ids=set(),
                decision_ids=set(),
                source_turn_ids=set(),
                delete_all=True,
            )
        elif selector.scope is ErasureScope.SOURCE_TURN:
            turn_id = _required_text(selector.source_turn_id, "source_turn_id")
            count = connection.execute(
                "SELECT COUNT(*) FROM source_turns "
                "WHERE relationship_id = ? AND turn_id = ?",
                (relationship_id, turn_id),
            ).fetchone()[0]
            if count != 1:
                raise ErasureSelectionError(
                    "source turn was not found exactly once in staging"
                )
            adjudications = _sqlite_json_rows(
                connection,
                "relationship_adjudications",
                "decision_id",
                relationship_id,
            )
            removed = [
                raw
                for _, raw in adjudications
                if raw.get("receipt", {}).get("source_turn_id") == turn_id
            ]
            deleted["source_turn"] = 1
            deleted["relationship_adjudication"] = len(removed)
            deleted["relationship_event"] = sum(
                len(_raw_record_events(item)) for item in removed
            )
            _add_consequence_deletion_estimate(
                deleted,
                consequences,
                tension_links,
                event_ids={
                    event_id
                    for item in removed
                    for event_id in _raw_record_events(item)
                },
                decision_ids={
                    _required_text(
                        item.get("receipt", {}).get("decision_id"),
                        "decision_id",
                    )
                    for item in removed
                },
                source_turn_ids={turn_id},
            )
        else:
            target = _required_text(
                selector.relationship_event_id,
                "relationship_event_id",
            )
            direct = [
                raw
                for _, raw in _sqlite_json_rows(
                    connection,
                    "relationship_events",
                    "event_id",
                    relationship_id,
                )
            ]
            adjudications = [
                raw
                for _, raw in _sqlite_json_rows(
                    connection,
                    "relationship_adjudications",
                    "decision_id",
                    relationship_id,
                )
            ]
            event_ids, decision_ids, source_turn_ids = _cascade_event_deletions(
                direct,
                adjudications,
                {target},
            )
            deleted["relationship_event"] = len(event_ids)
            deleted["relationship_adjudication"] = len(decision_ids)
            _add_consequence_deletion_estimate(
                deleted,
                consequences,
                tension_links,
                event_ids=event_ids,
                decision_ids=decision_ids,
                source_turn_ids=source_turn_ids,
            )
    return (relationship_id,), _empty_inventory(deleted)


def inspect_erasure_scope(
    staging_path: str,
    storage_kind: ErasureStorageKind,
    selector: ErasureSelector,
) -> ErasureScopeInspection:
    """Validate an exact selector using read-only raw storage access."""
    if isinstance(storage_kind, str):
        storage_kind = ErasureStorageKind(storage_kind)
    if not isinstance(selector, ErasureSelector):
        raise TypeError("inspect_erasure_scope() requires an ErasureSelector")
    path = Path(staging_path)
    try:
        assert_no_link_or_reparse_ancestors(
            path,
            label="erasure inspection target",
        )
        if storage_kind is ErasureStorageKind.FILE_STORAGE:
            require_regular_directory(path, label="FileStorage staging path")
            affected, estimate = _estimate_file_scope(path, selector)
        else:
            require_regular_file(path, label="SQLite staging path")
            affected, estimate = _estimate_sqlite_scope(path, selector)
    except ErasureSelectionError:
        raise
    except Exception as exc:
        raise ErasureSelectionError(
            "erasure inspection target is unsafe or unreadable"
        ) from exc
    return ErasureScopeInspection(
        storage_kind=storage_kind,
        selector=selector,
        affected_relationship_ids=affected,
        inventory_estimate=estimate,
    )


__all__ = ["inspect_erasure_scope"]
