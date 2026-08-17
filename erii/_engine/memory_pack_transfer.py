"""Snapshot-bound, zero-write planning for MemoryPack transfers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
from typing import Any, List, Mapping, Optional, Protocol, Sequence, Tuple
import uuid

from erii._engine.memory_pack_analysis import (
    MemoryPackAnalysis,
    analyze_memory_pack,
    validate_memory_pack_persisted_turn_adjudications,
    validate_memory_pack_relationship_consequences,
)
from erii.core.adjudication import (
    list_complete_relationship_events,
    relationship_occurrence_fingerprint,
)
from erii.core.memory_pack_evidence import validate_memory_pack_archival_evidence
from erii.core.memory_pack_import_compatibility import (
    has_legacy_persona_decision_reason_loss,
)
from erii.core.persona_compilation import PersonaCompiler
from erii.core.persona_context import validate_persona_premise_binding
from erii.core.temporal_history import TemporalHistoryValidator
from erii.models.adjudication import AdjudicationRecord, PersonaGrowthProposal
from erii.models.archival import (
    ArchivalTombstone,
    TimelineEntry,
    merge_archival_tombstone_batch,
)
from erii.models.consequence import NarrativeTensionLink, RelationshipConsequence
from erii.models.consolidation import (
    PersonaReflectionDecisionRecord,
    RelationshipProcessingRun,
)
from erii.models.node import MemoryNode
from erii.models.pack import MemoryPack
from erii.models.persona import (
    PersonaCompilationProposal,
    PersonaCompilationStatus,
    PersonaManifest,
)
from erii.models.relationship import RelationshipEvent, RelationshipProfile
from erii.models.temporal import (
    OpenLoopResolution,
    OpenLoopSpec,
    PromiseConditionConfirmation,
    PromiseResolution,
    PromiseSpec,
)
from erii.models.turn import TurnRecord


class StaleMemoryPackTransferPlanError(RuntimeError):
    """Raised when a transfer input no longer matches its frozen plan."""


@dataclass(frozen=True)
class MemoryPackSourceSnapshot:
    """Validated source identity and facts used by a transfer plan."""

    fingerprint: str
    analysis_fingerprint: str
    analysis: MemoryPackAnalysis


@dataclass(frozen=True)
class MemoryPackTargetSnapshot:
    """Content-addressed target relationship state observed during preflight."""

    agent_id: str
    user_id: str
    relationship_id: Optional[str]
    revision: str


@dataclass(frozen=True)
class MemoryPackTargetReadObservation:
    """One ordered target read and its content-addressed outcome."""

    method: str
    arguments: str
    outcome: str
    revision: str


@dataclass(frozen=True)
class MemoryPackTargetReadSet:
    """Frozen ordered conflict reads consumed by MemoryPack preflight."""

    observations: Tuple[MemoryPackTargetReadObservation, ...]
    fingerprint: str


@dataclass(frozen=True)
class MemoryPackTransferPlan:
    """Deterministic transfer intent bound to source and target snapshots."""

    source: MemoryPackSourceSnapshot
    target: MemoryPackTargetSnapshot
    target_reads: MemoryPackTargetReadSet
    overwrite: bool
    fingerprint: str


@dataclass(frozen=True)
class MemoryPackExportSnapshot:
    """Storage-independent values captured for one MemoryPack export."""

    agent_id: str
    user_id: str
    core_memory: str
    nodes: Tuple[MemoryNode, ...]
    legacy_timeline: Tuple[str, ...]
    timeline_entries: Tuple[TimelineEntry, ...]
    archival_tombstones: Tuple[ArchivalTombstone, ...]
    relationship: Optional[RelationshipProfile]
    relationship_events: Tuple[RelationshipEvent, ...]
    relationship_direct_event_ids: Tuple[str, ...]
    relationship_adjudications: Tuple[AdjudicationRecord, ...]
    relationship_consequences: Tuple[RelationshipConsequence, ...]
    narrative_tension_links: Tuple[NarrativeTensionLink, ...]
    persona_growth_proposals: Tuple[PersonaGrowthProposal, ...]
    persona_compilation_proposals: Tuple[PersonaCompilationProposal, ...]
    persona_manifests: Tuple[PersonaManifest, ...]
    turn_records: Tuple[TurnRecord, ...]
    relationship_processing_runs: Tuple[RelationshipProcessingRun, ...]
    persona_reflection_decisions: Tuple[PersonaReflectionDecisionRecord, ...]
    exported_at: Optional[str] = None


class MemoryPackNodeWriteMode(str, Enum):
    """How the execution layer combines imported and target memory nodes."""

    MERGE = "merge"
    REPLACE = "replace"


class MemoryPackCoreWriteMode(str, Enum):
    """When the execution layer writes a non-empty imported Core memory."""

    IF_EMPTY = "if_empty"
    ALWAYS = "always"


@dataclass(frozen=True)
class MemoryPackLegacyTimelineWrite:
    """One legacy Timeline append payload."""

    content: str
    timestamp: Optional[str]


@dataclass(frozen=True)
class MemoryPackPersonaCompilationWritePlan:
    """Validated and remapped Persona Compilation payloads."""

    target_blueprint_id: str
    proposals: Tuple[PersonaCompilationProposal, ...]
    manifests: Tuple[PersonaManifest, ...]
    selected_manifest: Optional[PersonaManifest]
    selected_proposal_key: Optional[Tuple[str, int]]
    fingerprint: str


@dataclass(frozen=True)
class MemoryPackRelationshipWritePlan:
    """Relationship-scoped payloads after deterministic ID remapping."""

    source_relationship_id: str
    relationship_id: str
    turn_records: Tuple[TurnRecord, ...]
    archival_tombstones: Tuple[ArchivalTombstone, ...]
    direct_events: Tuple[RelationshipEvent, ...]
    adjudications: Tuple[AdjudicationRecord, ...]
    consequences: Tuple[RelationshipConsequence, ...]
    narrative_tension_links: Tuple[NarrativeTensionLink, ...]
    persona_growth_proposals: Tuple[PersonaGrowthProposal, ...]
    persona_reflection_decisions: Tuple[PersonaReflectionDecisionRecord, ...]
    processing_runs: Tuple[RelationshipProcessingRun, ...]


@dataclass(frozen=True)
class MemoryPackWritePlan:
    """Zero-write payload plan consumed by the existing Engine execution order."""

    source_fingerprint: str
    target_agent: str
    target_user: str
    target_relationship_id: Optional[str]
    overwrite: bool
    node_write_mode: MemoryPackNodeWriteMode
    node_documents: Tuple[str, ...]
    core_memory: Optional[str]
    core_write_mode: MemoryPackCoreWriteMode
    legacy_timeline: Tuple[MemoryPackLegacyTimelineWrite, ...]
    timeline_entries: Tuple[TimelineEntry, ...]
    persona_compilation: Optional[MemoryPackPersonaCompilationWritePlan]
    relationship: Optional[MemoryPackRelationshipWritePlan]
    batch_order: Tuple[str, ...]
    fingerprint: str

    def memory_nodes(self) -> Tuple[MemoryNode, ...]:
        """Returns fresh node objects so callers cannot mutate the frozen plan."""
        return tuple(
            MemoryNode.from_dict(json.loads(document))
            for document in self.node_documents
        )


@dataclass(frozen=True)
class MemoryPackHistoryExecutionResult:
    """Observable result of the relationship-history execution seam."""

    relationship_id: str
    unit_order: Tuple[str, ...]
    direct_event_count: int
    adjudication_count: int


@dataclass(frozen=True)
class MemoryPackWriteExecutionResult:
    """Observable result of executing every frozen non-compilation batch."""

    target_agent: str
    target_user: str
    target_relationship_id: Optional[str]
    executed_batches: Tuple[str, ...]
    saved_node_count: int
    core_memory_written: bool
    history: Optional[MemoryPackHistoryExecutionResult]


def _memory_pack_write_result_json(
    result: MemoryPackWriteExecutionResult,
) -> str:
    """Serializes the content-free v1 SQLite operation receipt result."""
    return _canonical_json(
        {
            "receipt_version": 1,
            "target_agent": result.target_agent,
            "target_user": result.target_user,
            "target_relationship_id": result.target_relationship_id,
            "executed_batches": list(result.executed_batches),
            "saved_node_count": result.saved_node_count,
            "core_memory_written": result.core_memory_written,
            "history": (
                None
                if result.history is None
                else {
                    "relationship_id": result.history.relationship_id,
                    "unit_order": list(result.history.unit_order),
                    "direct_event_count": result.history.direct_event_count,
                    "adjudication_count": result.history.adjudication_count,
                }
            ),
        }
    )


def _memory_pack_write_result_from_json(
    result_json: str,
    plan: MemoryPackWritePlan,
) -> MemoryPackWriteExecutionResult:
    """Loads and scope-checks one content-free v1 SQLite receipt result."""
    try:
        value = json.loads(result_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("MemoryPack write receipt result is invalid") from exc
    fields = {
        "receipt_version",
        "target_agent",
        "target_user",
        "target_relationship_id",
        "executed_batches",
        "saved_node_count",
        "core_memory_written",
        "history",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("MemoryPack write receipt result fields are invalid")
    if value["receipt_version"] != 1:
        raise ValueError("MemoryPack write receipt result version is invalid")
    if (
        value["target_agent"] != plan.target_agent
        or value["target_user"] != plan.target_user
        or value["target_relationship_id"] != plan.target_relationship_id
    ):
        raise ValueError("MemoryPack write receipt result scope is invalid")
    executed_batches = value["executed_batches"]
    if (
        not isinstance(executed_batches, list)
        or any(not isinstance(item, str) for item in executed_batches)
        or tuple(executed_batches) != plan.batch_order
    ):
        raise ValueError("MemoryPack write receipt batch order is invalid")
    saved_node_count = value["saved_node_count"]
    if (
        isinstance(saved_node_count, bool)
        or not isinstance(saved_node_count, int)
        or saved_node_count < 0
    ):
        raise ValueError("MemoryPack write receipt node count is invalid")
    core_memory_written = value["core_memory_written"]
    if not isinstance(core_memory_written, bool):
        raise ValueError("MemoryPack write receipt Core result is invalid")

    history_value = value["history"]
    history = None
    if history_value is not None:
        history_fields = {
            "relationship_id",
            "unit_order",
            "direct_event_count",
            "adjudication_count",
        }
        if not isinstance(history_value, dict) or set(history_value) != history_fields:
            raise ValueError("MemoryPack write receipt history fields are invalid")
        unit_order = history_value["unit_order"]
        direct_event_count = history_value["direct_event_count"]
        adjudication_count = history_value["adjudication_count"]
        if (
            not isinstance(history_value["relationship_id"], str)
            or not isinstance(unit_order, list)
            or any(not isinstance(item, str) for item in unit_order)
            or isinstance(direct_event_count, bool)
            or not isinstance(direct_event_count, int)
            or direct_event_count < 0
            or isinstance(adjudication_count, bool)
            or not isinstance(adjudication_count, int)
            or adjudication_count < 0
        ):
            raise ValueError("MemoryPack write receipt history is invalid")
        history = MemoryPackHistoryExecutionResult(
            relationship_id=history_value["relationship_id"],
            unit_order=tuple(unit_order),
            direct_event_count=direct_event_count,
            adjudication_count=adjudication_count,
        )

    return MemoryPackWriteExecutionResult(
        target_agent=plan.target_agent,
        target_user=plan.target_user,
        target_relationship_id=plan.target_relationship_id,
        executed_batches=tuple(executed_batches),
        saved_node_count=saved_node_count,
        core_memory_written=core_memory_written,
        history=history,
    )


class MemoryPackRelationshipHistoryStorage(Protocol):
    """Narrow write seam shared by FileStorage and SQLiteStorage history."""

    def list_relationship_events(
        self,
        relationship_id: str,
    ) -> Sequence[RelationshipEvent]: ...

    def list_relationship_adjudications(
        self,
        relationship_id: str,
    ) -> Sequence[AdjudicationRecord]: ...

    def append_relationship_event(
        self,
        event: RelationshipEvent,
    ) -> RelationshipEvent: ...

    def commit_relationship_adjudication(
        self,
        record: AdjudicationRecord,
    ) -> AdjudicationRecord: ...


class MemoryPackWriteStorage(MemoryPackRelationshipHistoryStorage, Protocol):
    """Narrow payload-write seam implemented by both durable adapters."""

    def load_nodes(self, agent_id: str, user_id: str) -> List[MemoryNode]: ...

    def save_nodes(
        self,
        agent_id: str,
        user_id: str,
        nodes: List[MemoryNode],
    ) -> None: ...

    def get_core_memory(self, agent_id: str, user_id: str) -> str: ...

    def save_core_memory(
        self,
        agent_id: str,
        user_id: str,
        content: str,
    ) -> None: ...

    def add_timeline_entry(
        self,
        agent_id: str,
        user_id: str,
        entry: str,
        timestamp: Optional[str] = None,
    ) -> None: ...

    def create_turn_record(self, record: TurnRecord) -> TurnRecord: ...

    def import_timeline_entries(
        self,
        agent_id: str,
        user_id: str,
        entries: List[TimelineEntry],
    ) -> None: ...

    def import_archival_tombstones(
        self,
        relationship_id: str,
        tombstones: List[ArchivalTombstone],
    ) -> None: ...

    def append_relationship_consequence(
        self,
        consequence: RelationshipConsequence,
    ) -> RelationshipConsequence: ...

    def append_narrative_tension_link(
        self,
        link: NarrativeTensionLink,
    ) -> NarrativeTensionLink: ...

    def save_persona_growth_proposal(
        self,
        proposal: PersonaGrowthProposal,
    ) -> PersonaGrowthProposal: ...

    def commit_persona_reflection_decision(
        self,
        decision: PersonaReflectionDecisionRecord,
    ) -> PersonaReflectionDecisionRecord: ...

    def create_relationship_processing_run(
        self,
        run: RelationshipProcessingRun,
    ) -> RelationshipProcessingRun: ...


def _canonical_fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def assemble_memory_pack_export(snapshot: MemoryPackExportSnapshot) -> MemoryPack:
    """Assemble and validate a portable MemoryPack without reading or writing."""
    legacy_timeline = []
    for line in snapshot.legacy_timeline:
        if line.startswith("[") and "]" in line:
            index = line.index("]")
            legacy_timeline.append(
                {
                    "timestamp": line[1:index],
                    "content": line[index + 2 :],
                }
            )
        else:
            legacy_timeline.append({"timestamp": "", "content": line})

    pack = MemoryPack(
        agent_id=snapshot.agent_id,
        user_id=snapshot.user_id,
        core_memory=snapshot.core_memory,
        nodes=list(snapshot.nodes),
        timeline=legacy_timeline,
        timeline_entries=list(snapshot.timeline_entries),
        archival_ledger=list(snapshot.archival_tombstones),
        relationship=snapshot.relationship,
        relationship_events=list(snapshot.relationship_events),
        relationship_direct_event_ids=list(
            snapshot.relationship_direct_event_ids
        ),
        relationship_adjudications=list(snapshot.relationship_adjudications),
        relationship_consequences=list(snapshot.relationship_consequences),
        narrative_tension_links=list(snapshot.narrative_tension_links),
        persona_growth_proposals=list(snapshot.persona_growth_proposals),
        persona_compilation_proposals=list(
            snapshot.persona_compilation_proposals
        ),
        persona_manifests=list(snapshot.persona_manifests),
        turn_records=list(snapshot.turn_records),
        relationship_processing_runs=list(
            snapshot.relationship_processing_runs
        ),
        persona_reflection_decisions=list(
            snapshot.persona_reflection_decisions
        ),
        exported_at=snapshot.exported_at,
    )
    validate_memory_pack_archival_evidence(pack)
    validate_memory_pack_persisted_turn_adjudications(
        pack,
        snapshot.agent_id,
        snapshot.user_id,
        (
            snapshot.relationship.relationship_id
            if snapshot.relationship is not None
            else None
        ),
    )
    validate_memory_pack_relationship_consequences(pack)
    return pack


def _portable_value(value: object) -> object:
    if isinstance(value, Enum):
        return _portable_value(value.value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _portable_value(to_dict())
    if isinstance(value, Mapping):
        return {
            str(key): _portable_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_portable_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(
        f"unsupported MemoryPack target read value: {type(value).__name__}"
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        _portable_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _observe_target_read(
    storage: object,
    method: str,
    arguments: Sequence[object],
) -> tuple[MemoryPackTargetReadObservation, object, Optional[NotImplementedError]]:
    encoded_arguments = _canonical_json(list(arguments))
    try:
        result = getattr(storage, method)(*arguments)
    except NotImplementedError as exc:
        outcome = "not_implemented"
        observation = MemoryPackTargetReadObservation(
            method=method,
            arguments=encoded_arguments,
            outcome=outcome,
            revision=_canonical_fingerprint({"outcome": outcome}),
        )
        return observation, None, exc
    outcome = "returned"
    return (
        MemoryPackTargetReadObservation(
            method=method,
            arguments=encoded_arguments,
            outcome=outcome,
            revision=_canonical_fingerprint(
                {"outcome": outcome, "value": _portable_value(result)}
            ),
        ),
        result,
        None,
    )


class MemoryPackTargetReadRecorder:
    """Read-only Adapter that records exactly what target preflight consumes."""

    READ_METHODS = frozenset(
        {
            "capture_archival_tombstone_validation_source",
            "get_persona_growth_proposal",
            "get_relationship",
            "list_narrative_tension_links",
            "list_persona_compilation_proposals",
            "list_persona_growth_proposals",
            "list_persona_manifests",
            "list_persona_reflection_decisions",
            "list_persona_reflection_records",
            "list_relationship_adjudications",
            "list_relationship_consequences",
            "list_relationship_events",
            "list_relationship_processing_runs",
            "list_timeline_entries",
            "list_turn_records",
        }
    )

    def __init__(self, storage: object) -> None:
        self._storage = storage
        self._observations: list[MemoryPackTargetReadObservation] = []

    def _read(self, method: str, *arguments: object) -> Any:
        if method not in self.READ_METHODS:
            raise AttributeError(f"{method} is not a MemoryPack target read")
        observation, result, unavailable = _observe_target_read(
            self._storage,
            method,
            arguments,
        )
        self._observations.append(observation)
        if unavailable is not None:
            raise unavailable
        return result

    def get_relationship(self, agent_id: str, user_id: str):
        return self._read("get_relationship", agent_id, user_id)

    def list_timeline_entries(self, agent_id: str, user_id: str):
        return self._read("list_timeline_entries", agent_id, user_id)

    def list_relationship_adjudications(self, relationship_id: str):
        return self._read("list_relationship_adjudications", relationship_id)

    def list_relationship_consequences(self, relationship_id: str):
        return self._read("list_relationship_consequences", relationship_id)

    def list_narrative_tension_links(self, relationship_id: str):
        return self._read("list_narrative_tension_links", relationship_id)

    def list_relationship_events(self, relationship_id: str):
        return self._read("list_relationship_events", relationship_id)

    def list_relationship_processing_runs(self, relationship_id: str):
        return self._read("list_relationship_processing_runs", relationship_id)

    def list_persona_reflection_decisions(self, relationship_id: str):
        return self._read("list_persona_reflection_decisions", relationship_id)

    def list_persona_reflection_records(self, relationship_id: str):
        return self._read("list_persona_reflection_records", relationship_id)

    def list_persona_growth_proposals(self, relationship_id: str):
        return self._read("list_persona_growth_proposals", relationship_id)

    def get_persona_growth_proposal(self, proposal_id: str):
        return self._read("get_persona_growth_proposal", proposal_id)

    def list_persona_compilation_proposals(self, blueprint_id: str):
        return self._read("list_persona_compilation_proposals", blueprint_id)

    def list_persona_manifests(self, blueprint_id: str):
        return self._read("list_persona_manifests", blueprint_id)

    def list_turn_records(self, relationship_id: str):
        return self._read("list_turn_records", relationship_id)

    def capture_archival_tombstone_validation_source(
        self,
        relationship_id: str,
        archival_ids: Sequence[str],
    ):
        return self._read(
            "capture_archival_tombstone_validation_source",
            relationship_id,
            sorted(set(archival_ids)),
        )

    def validate_archival_tombstones(
        self,
        relationship_id: str,
        tombstones: Sequence[ArchivalTombstone],
    ) -> None:
        try:
            source = self.capture_archival_tombstone_validation_source(
                relationship_id,
                [item.archival_id for item in tombstones],
            )
        except NotImplementedError as exc:
            self._storage.validate_archival_tombstones(
                relationship_id,
                list(tombstones),
            )
            raise ValueError(
                "target storage cannot bind archival validation source reads"
            ) from exc
        merge_archival_tombstone_batch(
            relationship_id,
            tombstones,
            existing=source.tombstones,
            live_records=source.live_records,
        )

    def freeze(self) -> MemoryPackTargetReadSet:
        observations = tuple(self._observations)
        return MemoryPackTargetReadSet(
            observations=observations,
            fingerprint=_canonical_fingerprint(
                [
                    {
                        "method": item.method,
                        "arguments": item.arguments,
                        "outcome": item.outcome,
                        "revision": item.revision,
                    }
                    for item in observations
                ]
            ),
        )


def _empty_target_read_set() -> MemoryPackTargetReadSet:
    return MemoryPackTargetReadRecorder(object()).freeze()


def _replay_arguments(observation: MemoryPackTargetReadObservation) -> list[object]:
    return json.loads(observation.arguments)


def replay_memory_pack_target_read_set(
    storage: object,
    read_set: MemoryPackTargetReadSet,
) -> None:
    """Rejects a target whose conflict reads no longer match preflight."""
    current = []
    for expected in read_set.observations:
        observed, _, _ = _observe_target_read(
            storage,
            expected.method,
            _replay_arguments(expected),
        )
        current.append(observed)
    current_read_set = MemoryPackTargetReadSet(
        observations=tuple(current),
        fingerprint=_canonical_fingerprint(
            [
                {
                    "method": item.method,
                    "arguments": item.arguments,
                    "outcome": item.outcome,
                    "revision": item.revision,
                }
                for item in current
            ]
        ),
    )
    if current_read_set != read_set:
        raise StaleMemoryPackTransferPlanError(
            "MemoryPack target conflict reads changed after preflight"
        )


def _source_fingerprint(pack: MemoryPack) -> str:
    return _canonical_fingerprint(pack.to_dict())


def memory_pack_import_operation_id(
    source: MemoryPackSourceSnapshot,
    target_agent: str,
    target_user: str,
    *,
    overwrite: bool,
) -> str:
    """Returns the stable v1 identity of one public MemoryPack import request."""
    return _canonical_fingerprint(
        {
            "contract": "erii.memory-pack-import-receipt.v1",
            "source": source.fingerprint,
            "target_agent": target_agent,
            "target_user": target_user,
            "overwrite": bool(overwrite),
        }
    )


def memory_pack_import_result_json(_result: MemoryPack) -> str:
    """Returns the content-free success token stored by the v1 import receipt."""
    return _canonical_json(
        {
            "operation": "memory-pack-import",
            "receipt_version": 1,
        }
    )


def memory_pack_import_result_from_json(
    result_json: str,
    pack: MemoryPack,
) -> MemoryPack:
    """Validates a v1 import success token and returns the caller's source pack."""
    try:
        value = json.loads(result_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("MemoryPack import receipt result is invalid") from exc
    if value != {
        "operation": "memory-pack-import",
        "receipt_version": 1,
    }:
        raise ValueError("MemoryPack import receipt result is invalid")
    return pack


def _target_snapshot(
    agent_id: str,
    user_id: str,
    relationship: Optional[RelationshipProfile],
) -> MemoryPackTargetSnapshot:
    relationship_document = (
        relationship.to_dict() if relationship is not None else None
    )
    return MemoryPackTargetSnapshot(
        agent_id=agent_id,
        user_id=user_id,
        relationship_id=(
            relationship.relationship_id if relationship is not None else None
        ),
        revision=_canonical_fingerprint(
            {
                "agent_id": agent_id,
                "user_id": user_id,
                "relationship": relationship_document,
            }
        ),
    )


def analyze_memory_pack_source(pack: MemoryPack) -> MemoryPackSourceSnapshot:
    """Validate and freeze the exact portable source used for planning."""
    analysis = analyze_memory_pack(pack)
    return MemoryPackSourceSnapshot(
        fingerprint=_source_fingerprint(pack),
        analysis_fingerprint=_canonical_fingerprint(
            {
                "has_bound_archival_history": analysis.has_bound_archival_history,
                "requires_exact_relationship_restore": (
                    analysis.requires_exact_relationship_restore
                ),
            }
        ),
        analysis=analysis,
    )


def bind_memory_pack_transfer_plan(
    source: MemoryPackSourceSnapshot,
    pack: MemoryPack,
    target_agent: str,
    target_user: str,
    target_relationship: Optional[RelationshipProfile],
    *,
    overwrite: bool,
    target_reads: Optional[MemoryPackTargetReadSet] = None,
) -> MemoryPackTransferPlan:
    """Bind a validated source to the target relationship snapshot and intent."""
    if _source_fingerprint(pack) != source.fingerprint:
        raise StaleMemoryPackTransferPlanError(
            "MemoryPack transfer source changed after analysis"
        )
    target = _target_snapshot(
        target_agent,
        target_user,
        target_relationship,
    )
    frozen_target_reads = target_reads or _empty_target_read_set()
    fingerprint = _canonical_fingerprint(
        {
            "source": source.fingerprint,
            "analysis": source.analysis_fingerprint,
            "target": target.revision,
            "target_reads": frozen_target_reads.fingerprint,
            "overwrite": bool(overwrite),
        }
    )
    return MemoryPackTransferPlan(
        source=source,
        target=target,
        target_reads=frozen_target_reads,
        overwrite=bool(overwrite),
        fingerprint=fingerprint,
    )


def require_memory_pack_transfer_plan_current(
    plan: MemoryPackTransferPlan,
    pack: MemoryPack,
    target_relationship: Optional[RelationshipProfile],
) -> None:
    """Reject execution when the source or target relationship became stale."""
    if _source_fingerprint(pack) != plan.source.fingerprint:
        raise StaleMemoryPackTransferPlanError(
            "MemoryPack transfer source changed after planning"
        )
    current_target = _target_snapshot(
        plan.target.agent_id,
        plan.target.user_id,
        target_relationship,
    )
    if current_target != plan.target:
        raise StaleMemoryPackTransferPlanError(
            "MemoryPack transfer target changed after preflight"
        )


def _plan_persona_compilation_writes(
    pack: MemoryPack,
    target_profile: RelationshipProfile,
) -> Optional[MemoryPackPersonaCompilationWritePlan]:
    if pack.relationship is None or not (
        pack.persona_compilation_proposals
        or pack.persona_manifests
        or pack.relationship.manifest_id
    ):
        return None

    source_blueprint = pack.relationship.blueprint
    target_blueprint = target_profile.blueprint
    source_blueprint_id = source_blueprint.blueprint_id
    target_blueprint_id = target_blueprint.blueprint_id
    remapped = source_blueprint_id != target_blueprint_id
    if source_blueprint.source_text != target_blueprint.source_text:
        raise ValueError(
            "MemoryPack Persona Compilation cannot be remapped to different source text"
        )

    source_proposals: dict[
        tuple[str, int], PersonaCompilationProposal
    ] = {}
    validated_source_candidates = {}
    for source_proposal in pack.persona_compilation_proposals:
        source_key = (source_proposal.proposal_id, source_proposal.revision)
        if source_key in source_proposals:
            raise ValueError(
                "MemoryPack contains a duplicate Persona proposal revision"
            )
        if (
            source_proposal.blueprint_id != source_blueprint_id
            or source_proposal.blueprint_revision != source_blueprint.revision
            or source_proposal.source_sha256 != source_blueprint.source_sha256
        ):
            raise ValueError(
                "MemoryPack Persona proposal belongs to a different Blueprint revision"
            )
        validated_source = PersonaCompiler._validate_against_source(
            source_proposal.candidate,
            source_blueprint.source_text,
        )
        if validated_source.model_dump(
            mode="json"
        ) != source_proposal.candidate.model_dump(mode="json"):
            raise ValueError(
                "MemoryPack Persona proposal lacks canonical source-span hashes"
            )
        expected_fingerprint = PersonaCompiler.content_fingerprint(
            source_blueprint_id,
            source_blueprint.revision,
            source_blueprint.source_sha256,
            validated_source,
        )
        if source_proposal.content_fingerprint != expected_fingerprint:
            raise ValueError("MemoryPack Persona proposal fingerprint is invalid")
        if source_proposal.status == PersonaCompilationStatus.PENDING:
            if any(
                value is not None
                for value in (
                    source_proposal.decided_by,
                    source_proposal.decided_at,
                    source_proposal.decision_reason,
                )
            ):
                raise ValueError(
                    "pending MemoryPack Persona proposal has decision state"
                )
        elif (
            source_proposal.decided_by is None
            or source_proposal.decided_at is None
        ):
            raise ValueError(
                "decided MemoryPack Persona proposal lacks provenance"
            )
        source_proposals[source_key] = source_proposal
        validated_source_candidates[source_key] = validated_source

    for source_proposal in source_proposals.values():
        if source_proposal.revision > 1 and (
            source_proposal.proposal_id,
            source_proposal.parent_revision,
        ) not in source_proposals:
            raise ValueError(
                "MemoryPack Persona proposal parent revision is missing"
            )

    source_manifests_by_id: dict[str, PersonaManifest] = {}
    source_manifests_by_revision: dict[tuple[str, int], PersonaManifest] = {}
    for source_manifest in pack.persona_manifests:
        manifest_key = (
            source_manifest.approved_proposal_id,
            source_manifest.approved_revision,
        )
        if (
            source_manifest.manifest_id in source_manifests_by_id
            or manifest_key in source_manifests_by_revision
        ):
            raise ValueError("MemoryPack contains a duplicate Persona Manifest")
        if (
            source_manifest.blueprint_id != source_blueprint_id
            or source_manifest.blueprint_revision != source_blueprint.revision
            or source_manifest.source_sha256 != source_blueprint.source_sha256
        ):
            raise ValueError(
                "MemoryPack Persona Manifest belongs to a different Blueprint revision"
            )
        source_proposal = source_proposals.get(manifest_key)
        if source_proposal is None:
            raise ValueError(
                "MemoryPack Manifest references a missing proposal revision"
            )
        if source_proposal.status not in (
            PersonaCompilationStatus.APPROVED,
            PersonaCompilationStatus.REVOKED,
        ):
            raise ValueError(
                "MemoryPack Manifest references an unapproved proposal"
            )
        validated_manifest_candidate = PersonaCompiler._validate_against_source(
            source_manifest.candidate,
            source_blueprint.source_text,
        )
        if validated_manifest_candidate.model_dump(
            mode="json"
        ) != source_manifest.candidate.model_dump(mode="json"):
            raise ValueError(
                "MemoryPack Persona Manifest lacks canonical source-span hashes"
            )
        source_approval = replace(
            source_proposal,
            status=PersonaCompilationStatus.APPROVED,
            decided_by=source_manifest.approved_by,
            decided_at=source_manifest.approved_at,
            decision_reason=source_proposal.decision_reason,
        )
        expected_source_manifest = PersonaCompiler.manifest_from_approved(
            source_approval
        )
        if expected_source_manifest.to_dict() != source_manifest.to_dict():
            raise ValueError(
                "MemoryPack Persona Manifest does not match its approved proposal"
            )
        if source_proposal.status == PersonaCompilationStatus.APPROVED and (
            source_proposal.decided_by != source_manifest.approved_by
            or source_proposal.decided_at != source_manifest.approved_at
        ):
            raise ValueError(
                "approved MemoryPack Persona proposal has different Manifest provenance"
            )
        source_manifests_by_id[source_manifest.manifest_id] = source_manifest
        source_manifests_by_revision[manifest_key] = source_manifest

    for source_proposal in source_proposals.values():
        if source_proposal.status in (
            PersonaCompilationStatus.APPROVED,
            PersonaCompilationStatus.REVOKED,
        ) and (
            source_proposal.proposal_id,
            source_proposal.revision,
        ) not in source_manifests_by_revision:
            raise ValueError(
                "approved MemoryPack proposal is missing its Manifest"
            )

    proposal_id_map: dict[str, str] = {}
    mapped_proposals = []
    for source_proposal in sorted(
        source_proposals.values(),
        key=lambda item: (item.proposal_id, item.revision),
    ):
        proposal_id_map.setdefault(
            source_proposal.proposal_id,
            (
                str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        (
                            f"erii:{target_blueprint_id}:persona-compilation:"
                            f"{source_proposal.proposal_id}"
                        ),
                    )
                )
                if remapped
                else source_proposal.proposal_id
            ),
        )
        source_key = (source_proposal.proposal_id, source_proposal.revision)
        validated_target = PersonaCompiler._validate_against_source(
            validated_source_candidates[source_key],
            target_blueprint.source_text,
        )
        fingerprint = PersonaCompiler.content_fingerprint(
            target_blueprint_id,
            target_blueprint.revision,
            target_blueprint.source_sha256,
            validated_target,
        )
        mapped_proposals.append(
            replace(
                source_proposal,
                proposal_id=proposal_id_map[source_proposal.proposal_id],
                blueprint_id=target_blueprint_id,
                blueprint_revision=target_blueprint.revision,
                source_sha256=target_blueprint.source_sha256,
                candidate=validated_target,
                content_fingerprint=fingerprint,
            )
        )

    proposal_by_key = {
        (item.proposal_id, item.revision): item for item in mapped_proposals
    }
    mapped_manifest_by_source_id = {}
    for source_manifest in source_manifests_by_id.values():
        mapped_proposal_id = proposal_id_map.get(
            source_manifest.approved_proposal_id
        )
        if mapped_proposal_id is None:
            raise ValueError(
                "MemoryPack manifest references a missing proposal revision"
            )
        mapped_proposal = proposal_by_key.get(
            (mapped_proposal_id, source_manifest.approved_revision)
        )
        if mapped_proposal is None:
            raise ValueError(
                "MemoryPack manifest references a missing proposal revision"
            )
        mapped_approval = replace(
            mapped_proposal,
            status=PersonaCompilationStatus.APPROVED,
            decided_by=source_manifest.approved_by,
            decided_at=source_manifest.approved_at,
            decision_reason=mapped_proposal.decision_reason,
        )
        mapped_manifest_by_source_id[
            source_manifest.manifest_id
        ] = PersonaCompiler.manifest_from_approved(mapped_approval)

    selected_manifest = None
    selected_proposal_key = None
    if pack.relationship.manifest_id is not None:
        selected_manifest = mapped_manifest_by_source_id.get(
            pack.relationship.manifest_id
        )
        if selected_manifest is None:
            raise ValueError(
                "relationship references a Manifest missing from MemoryPack"
            )
        selected_proposal_key = (
            selected_manifest.approved_proposal_id,
            selected_manifest.approved_revision,
        )
        if target_profile.manifest_id not in (
            None,
            selected_manifest.manifest_id,
        ):
            raise ValueError(
                "target relationship is pinned to a different Manifest"
            )
        validate_persona_premise_binding(
            target_profile.premise,
            selected_manifest.candidate,
        )

    plan_payload = {
        "target_blueprint_id": target_blueprint_id,
        "proposals": [item.to_dict() for item in mapped_proposals],
        "manifests": [
            item.to_dict() for item in mapped_manifest_by_source_id.values()
        ],
        "selected_manifest": (
            selected_manifest.to_dict()
            if selected_manifest is not None
            else None
        ),
        "selected_proposal_key": selected_proposal_key,
    }
    return MemoryPackPersonaCompilationWritePlan(
        target_blueprint_id=target_blueprint_id,
        proposals=tuple(mapped_proposals),
        manifests=tuple(mapped_manifest_by_source_id.values()),
        selected_manifest=selected_manifest,
        selected_proposal_key=selected_proposal_key,
        fingerprint=_canonical_fingerprint(plan_payload),
    )


def plan_memory_pack_persona_compilation_writes(
    pack: MemoryPack,
    target_profile: RelationshipProfile,
) -> Optional[MemoryPackPersonaCompilationWritePlan]:
    """Returns validated Persona Compilation writes without reading Storage."""
    return _plan_persona_compilation_writes(pack, target_profile)


def _relationship_remap_maps(
    pack: MemoryPack,
    target_relationship_id: str,
) -> tuple[dict[str, str], dict[str, str]]:
    if pack.relationship is None:
        return {}, {}
    source_relationship_id = pack.relationship.relationship_id
    decision_id_map = {}
    for record in pack.relationship_adjudications:
        receipt = record.receipt
        if source_relationship_id == target_relationship_id:
            mapped_decision_id = receipt.decision_id
        else:
            processing_identity = (
                f"{receipt.processing_mode.value}:"
                f"{receipt.reprocessing_id or ''}"
            )
            mapped_decision_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"erii:{target_relationship_id}:decision:"
                        f"{receipt.source_turn_id}:{receipt.source_revision}:"
                        f"{processing_identity}:{receipt.candidate_key}"
                    ),
                )
            )
        decision_id_map[receipt.decision_id] = mapped_decision_id

    source_event_ids = {
        event.event_id for event in pack.relationship_events
    } | {
        event.event_id
        for record in pack.relationship_adjudications
        for event in record.events
    }
    event_id_map = {
        event_id: (
            event_id
            if source_relationship_id == target_relationship_id
            else str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"erii:{target_relationship_id}:{event_id}",
                )
            )
        )
        for event_id in source_event_ids
    }
    if source_relationship_id != target_relationship_id:
        for record in pack.relationship_adjudications:
            mapped_decision_id = decision_id_map[
                record.receipt.decision_id
            ]
            for index, event in enumerate(record.events):
                event_suffix = "event" if index == 0 else f"event:{index}"
                event_id_map[event.event_id] = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{mapped_decision_id}:{event_suffix}",
                    )
                )
    return decision_id_map, event_id_map


def _remap_temporal_payload(payload, event_id_map: Mapping[str, str]):
    if payload is None or isinstance(payload, (PromiseSpec, OpenLoopSpec)):
        return payload

    def mapped(source_id: str) -> str:
        try:
            return event_id_map[source_id]
        except KeyError as exc:
            raise ValueError(
                "MemoryPack temporal payload references an event outside the pack"
            ) from exc

    if isinstance(payload, PromiseConditionConfirmation):
        return replace(
            payload,
            promise_event_id=mapped(payload.promise_event_id),
        )
    if isinstance(payload, PromiseResolution):
        return replace(
            payload,
            promise_event_id=mapped(payload.promise_event_id),
            superseding_promise_event_id=(
                mapped(payload.superseding_promise_event_id)
                if payload.superseding_promise_event_id is not None
                else None
            ),
        )
    if isinstance(payload, OpenLoopResolution):
        return replace(
            payload,
            open_loop_event_id=mapped(payload.open_loop_event_id),
            superseding_open_loop_event_id=(
                mapped(payload.superseding_open_loop_event_id)
                if payload.superseding_open_loop_event_id is not None
                else None
            ),
        )
    raise ValueError("unsupported temporal payload in MemoryPack")


def _plan_persona_growth_writes(
    pack: MemoryPack,
    target_relationship_id: str,
    event_id_map: Mapping[str, str],
) -> Tuple[PersonaGrowthProposal, ...]:
    assert pack.relationship is not None
    if pack.relationship.relationship_id == target_relationship_id:
        return tuple(pack.persona_growth_proposals)
    return tuple(
        replace(
            source_proposal,
            proposal_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"erii:{target_relationship_id}:growth:"
                        f"{source_proposal.review_id}:"
                        f"{source_proposal.intent_key}"
                    ),
                )
            ),
            relationship_id=target_relationship_id,
            supporting_event_ids=tuple(
                event_id_map[event_id]
                for event_id in source_proposal.supporting_event_ids
            ),
        )
        for source_proposal in pack.persona_growth_proposals
    )


def plan_memory_pack_persona_growth_writes(
    pack: MemoryPack,
    target_relationship_id: str,
) -> Tuple[PersonaGrowthProposal, ...]:
    """Returns deterministic Growth remaps without other planning or Storage."""
    if pack.relationship is None:
        if pack.persona_growth_proposals:
            raise ValueError(
                "MemoryPack Persona Growth requires a relationship profile"
            )
        return ()
    _decision_id_map, event_id_map = _relationship_remap_maps(
        pack,
        target_relationship_id,
    )
    return _plan_persona_growth_writes(
        pack,
        target_relationship_id,
        event_id_map,
    )


def _plan_relationship_writes(
    pack: MemoryPack,
    target_relationship_id: str,
) -> MemoryPackRelationshipWritePlan:
    assert pack.relationship is not None
    source_relationship_id = pack.relationship.relationship_id
    decision_id_map, event_id_map = _relationship_remap_maps(
        pack,
        target_relationship_id,
    )

    def remap_event(source_event: RelationshipEvent) -> RelationshipEvent:
        if source_relationship_id == target_relationship_id:
            return source_event
        metadata = source_event.to_dict().get("metadata", {})
        temporal_payload = _remap_temporal_payload(
            source_event.temporal_payload,
            event_id_map,
        )
        adjudication = metadata.get("adjudication")
        if isinstance(adjudication, dict):
            if adjudication.get("decision_id"):
                adjudication["decision_id"] = decision_id_map.get(
                    adjudication["decision_id"],
                    adjudication["decision_id"],
                )
            adjudication["references"] = [
                event_id_map.get(item, item)
                for item in adjudication.get("references", [])
            ]
            adjudication["occurrence_fingerprint"] = (
                relationship_occurrence_fingerprint(
                    relationship_id=target_relationship_id,
                    event_type=source_event.event_type.value,
                    summary=source_event.content,
                    occurred_at=source_event.occurred_at,
                    occurrence_key=adjudication.get("occurrence_key"),
                    temporal_payload=(
                        temporal_payload.to_dict()
                        if temporal_payload is not None
                        else None
                    ),
                )
            )
        return replace(
            source_event,
            event_id=event_id_map[source_event.event_id],
            relationship_id=target_relationship_id,
            metadata=metadata,
            temporal_payload=temporal_payload,
        )

    if source_relationship_id != target_relationship_id:
        remapped_history = []
        seen_source_ids = set()
        for source_event in [
            *pack.relationship_events,
            *(
                event
                for record in pack.relationship_adjudications
                for event in record.events
            ),
        ]:
            if source_event.event_id in seen_source_ids:
                continue
            seen_source_ids.add(source_event.event_id)
            remapped_history.append(remap_event(source_event))
        TemporalHistoryValidator.validate_complete_history(remapped_history)

    top_level_source_by_id = {
        event.event_id: event for event in pack.relationship_events
    }
    if pack.relationship_direct_event_ids:
        ordered_ids = tuple(pack.relationship_direct_event_ids)
        if (
            len(ordered_ids) != len(set(ordered_ids))
            or any(
                event_id not in top_level_source_by_id
                for event_id in ordered_ids
            )
        ):
            raise ValueError(
                "MemoryPack direct-event journal order does not match its direct events"
            )
        direct_source_events = [
            top_level_source_by_id[event_id] for event_id in ordered_ids
        ]
    else:
        adjudicated_event_ids = {
            event.event_id
            for record in pack.relationship_adjudications
            for event in record.events
        }
        direct_source_events = [
            source_event
            for source_event in pack.relationship_events
            if source_event.event_id not in adjudicated_event_ids
        ]
    imported_direct_events = tuple(
        remap_event(source_event) for source_event in direct_source_events
    )

    imported_records = []
    for source_record in pack.relationship_adjudications:
        if source_relationship_id == target_relationship_id:
            imported_records.append(source_record)
            continue
        imported_events = tuple(
            remap_event(event) for event in source_record.events
        )
        old_receipt = source_record.receipt
        mapped_occurrence = (
            imported_events[0].metadata["adjudication"][
                "occurrence_fingerprint"
            ]
            if imported_events
            else hashlib.sha256(
                (
                    f"{target_relationship_id}:"
                    f"{old_receipt.occurrence_fingerprint}"
                ).encode("utf-8")
            ).hexdigest()
        )
        imported_receipt = replace(
            old_receipt,
            decision_id=decision_id_map[old_receipt.decision_id],
            relationship_id=target_relationship_id,
            occurrence_fingerprint=mapped_occurrence,
            event_ids=tuple(event.event_id for event in imported_events),
            related_event_id=(
                event_id_map.get(old_receipt.related_event_id)
                if old_receipt.related_event_id
                else None
            ),
        )
        imported_records.append(
            replace(
                source_record,
                receipt=imported_receipt,
                events=imported_events,
            )
        )

    growth_proposals = _plan_persona_growth_writes(
        pack,
        target_relationship_id,
        event_id_map,
    )

    return MemoryPackRelationshipWritePlan(
        source_relationship_id=source_relationship_id,
        relationship_id=target_relationship_id,
        turn_records=tuple(pack.turn_records),
        archival_tombstones=tuple(pack.archival_ledger),
        direct_events=imported_direct_events,
        adjudications=tuple(imported_records),
        consequences=tuple(pack.relationship_consequences),
        narrative_tension_links=tuple(pack.narrative_tension_links),
        persona_growth_proposals=tuple(growth_proposals),
        persona_reflection_decisions=tuple(
            pack.persona_reflection_decisions
        ),
        processing_runs=tuple(pack.relationship_processing_runs),
    )


def _memory_pack_write_batch_order(
    *,
    core_memory: Optional[str],
    legacy_timeline: Sequence[MemoryPackLegacyTimelineWrite],
    timeline_entries: Sequence[TimelineEntry],
    persona_compilation: Optional[MemoryPackPersonaCompilationWritePlan],
    relationship: Optional[MemoryPackRelationshipWritePlan],
) -> Tuple[str, ...]:
    batch_order = []
    if persona_compilation is not None:
        batch_order.append("persona_compilation")
    batch_order.append("nodes")
    if core_memory:
        batch_order.append("core_memory")
    if legacy_timeline:
        batch_order.append("legacy_timeline")
    if relationship is not None:
        if relationship.turn_records:
            batch_order.append("turn_records")
        if timeline_entries:
            batch_order.append("timeline_entries")
        if relationship.archival_tombstones:
            batch_order.append("archival_tombstones")
        if relationship.direct_events or relationship.adjudications:
            batch_order.append("relationship_history")
        if relationship.consequences:
            batch_order.append("relationship_consequences")
        if relationship.narrative_tension_links:
            batch_order.append("narrative_tension_links")
        if relationship.persona_growth_proposals:
            batch_order.append("persona_growth_proposals")
        if relationship.persona_reflection_decisions:
            batch_order.append("persona_reflection_decisions")
        if relationship.processing_runs:
            batch_order.append("relationship_processing_runs")
    elif timeline_entries:
        batch_order.append("timeline_entries")
    return tuple(batch_order)


def _memory_pack_write_plan_payload(
    plan: MemoryPackWritePlan,
) -> Mapping[str, object]:
    """Returns the canonical payload protected by a write-plan fingerprint."""
    return {
        "source": plan.source_fingerprint,
        "target_agent": plan.target_agent,
        "target_user": plan.target_user,
        "target_relationship_id": plan.target_relationship_id,
        "overwrite": plan.overwrite,
        "node_write_mode": plan.node_write_mode.value,
        "nodes": plan.node_documents,
        "core_memory": plan.core_memory,
        "core_write_mode": plan.core_write_mode.value,
        "legacy_timeline": [
            {"content": item.content, "timestamp": item.timestamp}
            for item in plan.legacy_timeline
        ],
        "timeline_entries": [
            item.to_dict() for item in plan.timeline_entries
        ],
        "persona_compilation": (
            plan.persona_compilation.fingerprint
            if plan.persona_compilation is not None
            else None
        ),
        "relationship": (
            _portable_value(plan.relationship.__dict__)
            if plan.relationship is not None
            else None
        ),
        "batch_order": list(plan.batch_order),
    }


def plan_memory_pack_writes(
    pack: MemoryPack,
    target_agent: str,
    target_user: str,
    target_profile: Optional[RelationshipProfile],
    *,
    overwrite: bool,
) -> MemoryPackWritePlan:
    """Freezes deterministic remaps and payload batches without Storage I/O."""
    if pack.relationship is not None and target_profile is None:
        raise ValueError(
            "MemoryPack relationship writes require a target relationship"
        )

    compilation = (
        _plan_persona_compilation_writes(pack, target_profile)
        if target_profile is not None
        else None
    )
    relationship = (
        _plan_relationship_writes(pack, target_profile.relationship_id)
        if pack.relationship is not None and target_profile is not None
        else None
    )
    legacy_timeline = (
        tuple(
            MemoryPackLegacyTimelineWrite(
                content=entry.get("content", ""),
                timestamp=entry.get("timestamp"),
            )
            for entry in pack.timeline
        )
        if not pack.timeline_entries
        else ()
    )
    batch_order = _memory_pack_write_batch_order(
        core_memory=pack.core_memory or None,
        legacy_timeline=legacy_timeline,
        timeline_entries=pack.timeline_entries,
        persona_compilation=compilation,
        relationship=relationship,
    )

    node_documents = tuple(
        _canonical_json(node.to_dict()) for node in pack.nodes
    )
    plan = MemoryPackWritePlan(
        source_fingerprint=_source_fingerprint(pack),
        target_agent=target_agent,
        target_user=target_user,
        target_relationship_id=(
            target_profile.relationship_id
            if target_profile is not None
            else None
        ),
        overwrite=bool(overwrite),
        node_write_mode=(
            MemoryPackNodeWriteMode.REPLACE
            if overwrite
            else MemoryPackNodeWriteMode.MERGE
        ),
        node_documents=node_documents,
        core_memory=pack.core_memory or None,
        core_write_mode=(
            MemoryPackCoreWriteMode.ALWAYS
            if overwrite
            else MemoryPackCoreWriteMode.IF_EMPTY
        ),
        legacy_timeline=legacy_timeline,
        timeline_entries=tuple(pack.timeline_entries),
        persona_compilation=compilation,
        relationship=relationship,
        batch_order=tuple(batch_order),
        fingerprint="",
    )
    return replace(
        plan,
        fingerprint=_canonical_fingerprint(
            _memory_pack_write_plan_payload(plan)
        ),
    )


def _schedule_memory_pack_relationship_history(
    storage: MemoryPackRelationshipHistoryStorage,
    plan: MemoryPackRelationshipWritePlan,
) -> Tuple[Tuple[str, Any], ...]:
    """Computes a complete causal interleaving without writing."""
    try:
        existing = list_complete_relationship_events(
            storage,
            plan.relationship_id,
        )
    except NotImplementedError:
        existing = []
    available_ids = {event.event_id for event in existing}
    direct_queue = list(plan.direct_events)
    adjudication_queue = list(plan.adjudications)
    imported_events = [
        event
        for unit in [*direct_queue, *adjudication_queue]
        for event in (
            (unit,) if isinstance(unit, RelationshipEvent) else unit.events
        )
    ]
    prerequisites = TemporalHistoryValidator.causal_prerequisites(
        imported_events
    )
    direct_index = 0
    adjudication_index = 0
    schedule = []

    while (
        direct_index < len(direct_queue)
        or adjudication_index < len(adjudication_queue)
    ):
        journal_heads = []
        if direct_index < len(direct_queue):
            journal_heads.append(("event", direct_queue[direct_index]))
        if adjudication_index < len(adjudication_queue):
            journal_heads.append(
                ("adjudication", adjudication_queue[adjudication_index])
            )
        for unit_kind, unit in journal_heads:
            unit_events = (
                (unit,)
                if isinstance(unit, RelationshipEvent)
                else unit.events
            )
            causal_ids = set(available_ids)
            ready = True
            for event in unit_events:
                references = prerequisites[event.event_id]
                if not references.issubset(causal_ids):
                    ready = False
                    break
                causal_ids.add(event.event_id)
            if not ready:
                continue

            schedule.append((unit_kind, unit))
            if unit_kind == "event":
                direct_index += 1
            else:
                adjudication_index += 1
            available_ids.update(event.event_id for event in unit_events)
            break
        else:
            remaining_units = [
                *direct_queue[direct_index:],
                *adjudication_queue[adjudication_index:],
            ]
            unresolved = sorted(
                {
                    reference
                    for unit in remaining_units
                    for event in (
                        (unit,)
                        if isinstance(unit, RelationshipEvent)
                        else unit.events
                    )
                    for reference in prerequisites[event.event_id]
                    if reference not in available_ids
                }
            )
            raise ValueError(
                "MemoryPack relationship history has unresolved causal ordering"
                + (f": {', '.join(unresolved)}" if unresolved else "")
            )
    return tuple(schedule)


def _commit_memory_pack_relationship_history_schedule(
    storage: MemoryPackRelationshipHistoryStorage,
    plan: MemoryPackRelationshipWritePlan,
    schedule: Sequence[Tuple[str, Any]],
) -> MemoryPackHistoryExecutionResult:
    """Commits one already validated relationship-history schedule."""

    for unit_kind, unit in schedule:
        if unit_kind == "event":
            stored_event = storage.append_relationship_event(unit)
            if not stored_event.same_payload_as(unit):
                raise ValueError(
                    "persisted relationship event differs from "
                    "the imported journal entry"
                )
        else:
            stored_record = storage.commit_relationship_adjudication(unit)
            if stored_record.to_dict() != unit.to_dict():
                raise ValueError(
                    "persisted relationship adjudication differs from "
                    "the imported journal entry"
                )

    return MemoryPackHistoryExecutionResult(
        relationship_id=plan.relationship_id,
        unit_order=tuple(unit_kind for unit_kind, _ in schedule),
        direct_event_count=len(plan.direct_events),
        adjudication_count=len(plan.adjudications),
    )


def execute_memory_pack_relationship_history(
    storage: MemoryPackRelationshipHistoryStorage,
    plan: MemoryPackRelationshipWritePlan,
) -> MemoryPackHistoryExecutionResult:
    """Commits both relationship journals in one preflighted causal order.

    The two append-only journals must preserve their own order, but temporal
    references may cross from either journal to the other.  This seam first
    computes the complete interleaving without writing, so an unresolved
    dependency cannot leave a partially imported relationship history.
    """
    schedule = _schedule_memory_pack_relationship_history(storage, plan)
    return _commit_memory_pack_relationship_history_schedule(
        storage,
        plan,
        schedule,
    )


def _validate_memory_pack_write_execution(
    plan: MemoryPackWritePlan,
) -> Tuple[str, ...]:
    """Validates frozen execution facts before the first payload write."""
    expected_batches = _memory_pack_write_batch_order(
        core_memory=plan.core_memory,
        legacy_timeline=plan.legacy_timeline,
        timeline_entries=plan.timeline_entries,
        persona_compilation=plan.persona_compilation,
        relationship=plan.relationship,
    )
    if plan.batch_order != expected_batches:
        raise ValueError(
            "MemoryPack write plan batch order changed after planning"
        )
    if plan.fingerprint != _canonical_fingerprint(
        _memory_pack_write_plan_payload(plan)
    ):
        raise ValueError("MemoryPack write plan changed after planning")

    relationship = plan.relationship
    if relationship is None:
        if plan.target_relationship_id is not None:
            raise ValueError(
                "MemoryPack write plan target relationship has no payload"
            )
        return tuple(
            batch
            for batch in expected_batches
            if batch != "persona_compilation"
        )
    if plan.target_relationship_id != relationship.relationship_id:
        raise ValueError(
            "MemoryPack write plan relationship identity changed after planning"
        )

    source_relationship_id = relationship.source_relationship_id
    target_relationship_id = relationship.relationship_id
    if (
        relationship.turn_records
        and source_relationship_id != target_relationship_id
    ):
        raise ValueError(
            "MemoryPack source transcripts require exact relationship restore"
        )
    if (
        relationship.archival_tombstones
        and source_relationship_id != target_relationship_id
    ):
        raise ValueError(
            "MemoryPack archival provenance requires exact relationship restore"
        )
    if (
        relationship.consequences
        or relationship.narrative_tension_links
    ) and source_relationship_id != target_relationship_id:
        raise ValueError(
            "MemoryPack relationship consequences require exact "
            "relationship restore"
        )
    if (
        relationship.processing_runs
        or relationship.persona_reflection_decisions
    ) and source_relationship_id != target_relationship_id:
        raise ValueError(
            "MemoryPack relationship processing requires exact "
            "relationship restore"
        )
    return tuple(
        batch
        for batch in expected_batches
        if batch != "persona_compilation"
    )


def _execute_memory_pack_writes_direct(
    storage: MemoryPackWriteStorage,
    plan: MemoryPackWritePlan,
    planned_batches: Tuple[str, ...],
) -> MemoryPackWriteExecutionResult:
    """Executes one plan through the supplied direct or transactional view."""
    relationship = plan.relationship
    history_schedule = (
        _schedule_memory_pack_relationship_history(storage, relationship)
        if relationship is not None
        and (relationship.direct_events or relationship.adjudications)
        else ()
    )
    executed_batches = []

    if plan.node_write_mode == MemoryPackNodeWriteMode.REPLACE:
        existing_nodes = []
    else:
        existing_nodes = storage.load_nodes(
            plan.target_agent,
            plan.target_user,
        )
    node_map = {node.node_id: node for node in existing_nodes}
    for node in plan.memory_nodes():
        node_map[node.node_id] = node
    storage.save_nodes(
        plan.target_agent,
        plan.target_user,
        list(node_map.values()),
    )
    executed_batches.append("nodes")

    core_memory_written = False
    if plan.core_memory:
        if (
            plan.core_write_mode == MemoryPackCoreWriteMode.ALWAYS
            or not storage.get_core_memory(
                plan.target_agent,
                plan.target_user,
            )
        ):
            storage.save_core_memory(
                plan.target_agent,
                plan.target_user,
                plan.core_memory,
            )
            core_memory_written = True
        executed_batches.append("core_memory")

    if plan.legacy_timeline:
        for entry in plan.legacy_timeline:
            storage.add_timeline_entry(
                plan.target_agent,
                plan.target_user,
                entry.content,
                entry.timestamp,
            )
        executed_batches.append("legacy_timeline")

    history_result = None
    if relationship is not None:
        target_relationship_id = relationship.relationship_id
        if relationship.turn_records:
            for turn_record in relationship.turn_records:
                stored_turn = storage.create_turn_record(turn_record)
                if stored_turn.to_dict() != turn_record.to_dict():
                    raise ValueError(
                        "persisted Turn differs from the imported record"
                    )
            executed_batches.append("turn_records")

        if plan.timeline_entries:
            storage.import_timeline_entries(
                plan.target_agent,
                plan.target_user,
                list(plan.timeline_entries),
            )
            executed_batches.append("timeline_entries")
        if relationship.archival_tombstones:
            storage.import_archival_tombstones(
                target_relationship_id,
                list(relationship.archival_tombstones),
            )
            executed_batches.append("archival_tombstones")

        if relationship.direct_events or relationship.adjudications:
            history_result = _commit_memory_pack_relationship_history_schedule(
                storage,
                relationship,
                history_schedule,
            )
            executed_batches.append("relationship_history")

        for consequence in relationship.consequences:
            stored_consequence = storage.append_relationship_consequence(
                consequence
            )
            if not stored_consequence.same_payload_as(consequence):
                raise ValueError(
                    "persisted relationship consequence differs from "
                    "the imported journal entry"
                )
        if relationship.consequences:
            executed_batches.append("relationship_consequences")

        for link in relationship.narrative_tension_links:
            stored_link = storage.append_narrative_tension_link(link)
            if not stored_link.same_payload_as(link):
                raise ValueError(
                    "persisted Narrative Tension link differs from "
                    "the imported journal entry"
                )
        if relationship.narrative_tension_links:
            executed_batches.append("narrative_tension_links")

        for proposal in relationship.persona_growth_proposals:
            stored_proposal = storage.save_persona_growth_proposal(proposal)
            if stored_proposal.to_dict() != proposal.to_dict():
                raise ValueError(
                    "persisted Persona Growth proposal differs from "
                    "the imported record"
                )
        if relationship.persona_growth_proposals:
            executed_batches.append("persona_growth_proposals")

        for decision in relationship.persona_reflection_decisions:
            stored_decision = storage.commit_persona_reflection_decision(
                decision
            )
            if stored_decision.to_dict() != decision.to_dict():
                raise ValueError(
                    "persisted Persona Reflection decision differs from "
                    "the imported record"
                )
        if relationship.persona_reflection_decisions:
            executed_batches.append("persona_reflection_decisions")

        for processing_run in relationship.processing_runs:
            stored_run = storage.create_relationship_processing_run(
                processing_run
            )
            if stored_run.to_dict() != processing_run.to_dict():
                raise ValueError(
                    "persisted relationship processing run differs from "
                    "the imported record"
                )
        if relationship.processing_runs:
            executed_batches.append("relationship_processing_runs")
    elif plan.timeline_entries:
        storage.import_timeline_entries(
            plan.target_agent,
            plan.target_user,
            list(plan.timeline_entries),
        )
        executed_batches.append("timeline_entries")

    assert tuple(executed_batches) == planned_batches
    return MemoryPackWriteExecutionResult(
        target_agent=plan.target_agent,
        target_user=plan.target_user,
        target_relationship_id=plan.target_relationship_id,
        executed_batches=tuple(executed_batches),
        saved_node_count=len(node_map),
        core_memory_written=core_memory_written,
        history=history_result,
    )


def execute_memory_pack_writes(
    storage: MemoryPackWriteStorage,
    plan: MemoryPackWritePlan,
) -> MemoryPackWriteExecutionResult:
    """Executes every frozen non-compilation batch behind one atomic seam.

    Built-in durable adapters expose ``atomic_memory_pack_write_store_v1``
    and publish the complete operation together.  Existing custom adapters
    retain the direct per-method compatibility path until they add the
    versioned capability.
    """
    # Frozen-plan validation is pure and deliberately precedes capability
    # discovery or transaction acquisition.  A corrupted plan therefore keeps
    # the same deterministic error contract even when a durable adapter is
    # busy or its backing path is temporarily unavailable.
    planned_batches = _validate_memory_pack_write_execution(plan)
    receipt_capability_provider = getattr(
        storage,
        "atomic_memory_pack_write_store_v2",
        None,
    )
    receipt_capability = (
        receipt_capability_provider()
        if callable(receipt_capability_provider)
        else None
    )
    if receipt_capability is not None:
        return receipt_capability.execute_memory_pack_write_v2(
            plan.fingerprint,
            plan.target_agent,
            plan.target_user,
            plan.target_relationship_id,
            lambda transactional_storage: _execute_memory_pack_writes_direct(
                transactional_storage,
                plan,
                planned_batches,
            ),
            _memory_pack_write_result_json,
            lambda result_json: _memory_pack_write_result_from_json(
                result_json,
                plan,
            ),
            lock_relationship_id=plan.target_relationship_id,
        )

    capability_provider = getattr(
        storage,
        "atomic_memory_pack_write_store_v1",
        None,
    )
    capability = (
        capability_provider()
        if callable(capability_provider)
        else None
    )
    if capability is None:
        return _execute_memory_pack_writes_direct(
            storage,
            plan,
            planned_batches,
        )
    return capability.execute_memory_pack_write(
        plan.target_agent,
        plan.target_user,
        plan.target_relationship_id,
        lambda transactional_storage: _execute_memory_pack_writes_direct(
            transactional_storage,
            plan,
            planned_batches,
        )
    )


def execute_memory_pack_persona_compilation(
    storage: MemoryPackWriteStorage,
    plan: MemoryPackPersonaCompilationWritePlan,
    target_profile: RelationshipProfile,
    *,
    validate_only: bool = False,
) -> RelationshipProfile:
    """Checks target conflicts and executes one planned compilation history.

    This is the execution seam for Persona Compilation imports. It validates
    that the planned compilation proposals and manifests do not conflict with
    the target storage state, then commits them in dependency order.

    When validate_only is True, only conflict checks are performed and no
    writes occur. This is used during preflight validation in the import flow.

    The selected manifest binding (if any) updates the target relationship
    profile's manifest_id and returns the refreshed profile.
    """
    target_blueprint_id = plan.target_blueprint_id
    mapped_proposals = plan.proposals
    mapped_manifest_by_source_id = {
        item.manifest_id: item for item in plan.manifests
    }
    selected_manifest = plan.selected_manifest
    selected_proposal_key = plan.selected_proposal_key

    def immutable_proposal_content(
        proposal: PersonaCompilationProposal,
    ) -> dict[str, Any]:
        data = proposal.to_dict()
        for key in (
            "status",
            "created_at",
            "created_by",
            "decided_by",
            "decided_at",
            "decision_reason",
        ):
            data.pop(key, None)
        return data

    def proposal_lifecycle(proposal: PersonaCompilationProposal):
        return (
            proposal.status,
            proposal.decided_by,
            proposal.decided_at,
            proposal.decision_reason,
        )

    existing_compilations = {
        (item.proposal_id, item.revision): item
        for item in storage.list_persona_compilation_proposals(
            target_blueprint_id
        )
    }
    existing_manifests = storage.list_persona_manifests(
        target_blueprint_id
    )
    existing_manifest_by_id = {item.manifest_id: item for item in existing_manifests}
    existing_manifest_by_revision = {
        (item.approved_proposal_id, item.approved_revision): item
        for item in existing_manifests
    }
    legacy_reason_loss_keys = set()
    for mapped in mapped_proposals:
        key = (mapped.proposal_id, mapped.revision)
        existing = existing_compilations.get(key)
        if existing is None:
            continue
        if immutable_proposal_content(existing) != immutable_proposal_content(mapped):
            raise ValueError("MemoryPack proposal identity conflicts with stored content")
        if existing.status == mapped.status:
            if proposal_lifecycle(existing) != proposal_lifecycle(mapped):
                if not has_legacy_persona_decision_reason_loss(
                    existing,
                    mapped,
                ):
                    raise ValueError(
                        "MemoryPack proposal lifecycle conflicts with storage"
                    )
                legacy_reason_loss_keys.add(key)
        elif not (
            existing.status == PersonaCompilationStatus.PENDING
            or (
                existing.status == PersonaCompilationStatus.APPROVED
                and mapped.status == PersonaCompilationStatus.REVOKED
            )
        ):
            raise ValueError("MemoryPack proposal status conflicts with storage")

    for mapped_manifest in mapped_manifest_by_source_id.values():
        existing = existing_manifest_by_id.get(mapped_manifest.manifest_id)
        by_revision = existing_manifest_by_revision.get(
            (
                mapped_manifest.approved_proposal_id,
                mapped_manifest.approved_revision,
            )
        )
        for candidate in (existing, by_revision):
            if candidate is not None and candidate.to_dict() != mapped_manifest.to_dict():
                raise ValueError("MemoryPack Manifest identity conflicts with storage")

    # No writes occur until the complete source graph and every target
    # conflict have been validated.
    if validate_only:
        return target_profile

    for mapped in mapped_proposals:
        key = (mapped.proposal_id, mapped.revision)
        if key in existing_compilations:
            continue
        pending = replace(
            mapped,
            status=PersonaCompilationStatus.PENDING,
            decided_by=None,
            decided_at=None,
            decision_reason=None,
        )
        storage.save_persona_compilation_proposal(pending)
        existing_compilations[key] = pending

    for mapped in mapped_proposals:
        key = (mapped.proposal_id, mapped.revision)
        current = existing_compilations[key]
        applied_mapped = (
            current
            if key in legacy_reason_loss_keys
            else mapped
        )
        if mapped.status == PersonaCompilationStatus.PENDING:
            continue
        matching_manifest = next(
            (
                item
                for item in mapped_manifest_by_source_id.values()
                if (
                    item.approved_proposal_id,
                    item.approved_revision,
                )
                == key
            ),
            None,
        )
        if mapped.status in (
            PersonaCompilationStatus.APPROVED,
            PersonaCompilationStatus.REVOKED,
        ):
            if matching_manifest is None:
                raise ValueError("approved MemoryPack proposal is missing its Manifest")
            approved = replace(
                applied_mapped,
                status=PersonaCompilationStatus.APPROVED,
                decided_by=matching_manifest.approved_by,
                decided_at=matching_manifest.approved_at,
                decision_reason=applied_mapped.decision_reason,
            )
            manifest_already_exists = (
                matching_manifest.manifest_id in existing_manifest_by_id
            )
            if key == selected_proposal_key and mapped.status == PersonaCompilationStatus.APPROVED:
                expected = current.status
                storage.approve_and_bind_persona_manifest(
                    target_profile,
                    approved,
                    matching_manifest,
                    expected,
                )
                target_profile = storage.get_relationship(
                    target_profile.agent_id,
                    target_profile.user_id,
                ) or target_profile
            elif current.status == PersonaCompilationStatus.PENDING:
                storage.approve_persona_manifest(
                    approved,
                    matching_manifest,
                    PersonaCompilationStatus.PENDING,
                )
            elif (
                current.status == PersonaCompilationStatus.APPROVED
                and not manifest_already_exists
            ):
                storage.approve_persona_manifest(
                    approved,
                    matching_manifest,
                    PersonaCompilationStatus.APPROVED,
                )
            elif current.status == PersonaCompilationStatus.REVOKED and not manifest_already_exists:
                raise ValueError("revoked stored proposal is missing its Persona Manifest")

            if (
                mapped.status == PersonaCompilationStatus.REVOKED
                and current.status != PersonaCompilationStatus.REVOKED
            ):
                storage.save_persona_compilation_proposal(
                    mapped,
                    PersonaCompilationStatus.APPROVED,
                )
            existing_compilations[key] = applied_mapped
            existing_manifest_by_id[matching_manifest.manifest_id] = matching_manifest
        elif (
            mapped.status == PersonaCompilationStatus.REJECTED
            and current.status == PersonaCompilationStatus.PENDING
        ):
            storage.save_persona_compilation_proposal(
                mapped,
                PersonaCompilationStatus.PENDING,
            )
            existing_compilations[key] = mapped

    if selected_manifest is not None and target_profile.manifest_id is None:
        target_profile = storage.bind_relationship_manifest(
            target_profile,
            selected_manifest.manifest_id,
        )
    return target_profile


__all__ = [
    "MemoryPackCoreWriteMode",
    "MemoryPackExportSnapshot",
    "MemoryPackHistoryExecutionResult",
    "MemoryPackLegacyTimelineWrite",
    "MemoryPackNodeWriteMode",
    "MemoryPackPersonaCompilationWritePlan",
    "MemoryPackRelationshipHistoryStorage",
    "MemoryPackRelationshipWritePlan",
    "MemoryPackSourceSnapshot",
    "MemoryPackTargetSnapshot",
    "MemoryPackTargetReadObservation",
    "MemoryPackTargetReadRecorder",
    "MemoryPackTargetReadSet",
    "MemoryPackTransferPlan",
    "MemoryPackWriteExecutionResult",
    "MemoryPackWritePlan",
    "MemoryPackWriteStorage",
    "StaleMemoryPackTransferPlanError",
    "analyze_memory_pack_source",
    "assemble_memory_pack_export",
    "bind_memory_pack_transfer_plan",
    "execute_memory_pack_persona_compilation",
    "execute_memory_pack_relationship_history",
    "execute_memory_pack_writes",
    "memory_pack_import_operation_id",
    "memory_pack_import_result_from_json",
    "memory_pack_import_result_json",
    "plan_memory_pack_persona_compilation_writes",
    "plan_memory_pack_persona_growth_writes",
    "plan_memory_pack_writes",
    "replay_memory_pack_target_read_set",
    "require_memory_pack_transfer_plan_current",
]
