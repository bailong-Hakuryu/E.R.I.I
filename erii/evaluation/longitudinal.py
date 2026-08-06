"""Deterministic longitudinal evaluation behind one narrow runner interface.

The public seam is ``LongitudinalEvalRunner.run(scenario, adapter, faults)``.
Scenario text remains inside the input and production storage; reports expose
only counts, stable identities and cryptographic digests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import tracemalloc
from typing import Any, Mapping, Protocol, runtime_checkable

from erii.core.relationship import RelationshipProjector
from erii.data_lifecycle import (
    FILE_STORAGE_MANIFEST,
    DataLifecycleCoordinator,
    EraseRequest,
    ErasureScope,
    ErasureSelector,
    ErasureTransformResult,
    LifecycleOperation,
    LifecycleOutcome,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    RebuildRequest,
)
from erii.engine import ERIIEngine
from erii.models.adjudication import (
    DecisionReceipt,
    DecisionOutcome,
    GrowthTriggerKind,
    RelationshipSignalType,
    SignalStrength,
)
from erii.models.pack import MemoryPack
from erii.models.turn import TurnRecord
from erii.models.relationship import (
    BeliefUpdate,
    CharacterBlueprint,
    RelationshipEvent,
    RelationshipEventType,
    RelationshipProfile,
)
from erii.models.recall import (
    PersonaDelivery,
    RecallAudience,
    RecallOptions,
    RecallRequest,
)
from erii.storage.file_storage import FileStorage
from erii.storage.sqlite_storage import SQLiteStorage


REPORT_VERSION = "longitudinal-eval-report/v1"
EXTRACTOR_VERSION = "erii.synthetic-longitudinal/v1"
_ACCEPTED_OUTCOMES = {DecisionOutcome.ACCEPTED, DecisionOutcome.CORROBORATED}
_REPORT_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_PERFORMANCE_LIMITS = {
    "smoke": {
        "turn_projection_ms": 30_000.0,
        "export_ms": 15_000.0,
        "import_ms": 15_000.0,
        "duplicate_import_ms": 15_000.0,
        "erase_ms": 60_000.0,
        "rebuild_ms": 60_000.0,
        "peak_python_memory_bytes": 512 * 1024 * 1024,
        "storage_size_bytes": 256 * 1024 * 1024,
    },
    "full": {
        "turn_projection_ms": 180_000.0,
        "export_ms": 60_000.0,
        "import_ms": 60_000.0,
        "duplicate_import_ms": 60_000.0,
        "erase_ms": 240_000.0,
        "rebuild_ms": 240_000.0,
        "peak_python_memory_bytes": 1024 * 1024 * 1024,
        "storage_size_bytes": 2 * 1024 * 1024 * 1024,
    },
}
_MEASUREMENT_METRICS = {
    "performance_ceiling_failures",
    "peak_python_memory_ceiling_failures",
    "storage_size_ceiling_failures",
    "lifecycle_performance_ceiling_failures",
    "lifecycle_peak_memory_ceiling_failures",
    "lifecycle_storage_size_ceiling_failures",
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_report_identifier(value: str, field_name: str) -> str:
    normalized = _require_identifier(value, field_name)
    if _REPORT_IDENTIFIER.fullmatch(normalized) is None:
        raise ValueError(
            f"{field_name} must be a non-sensitive machine identifier"
        )
    return normalized


def _performance_tier(turn_count: int) -> str:
    return "smoke" if turn_count <= 32 else "full"


def _lifecycle_storage_size_bytes(target: LifecycleTarget) -> int:
    path = Path(target.path)
    if target.kind is LifecycleTargetKind.FILE_STORAGE:
        if not path.exists():
            return 0
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return sum(
        item.stat().st_size
        for item in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        if item.is_file()
    )


def _selected_relationship_is_verified(
    operation: LifecycleOperation,
    details: ErasureTransformResult,
    selector: ErasureSelector,
) -> bool:
    relationship_id = selector.relationship_id
    if (
        relationship_id is None
        or relationship_id not in details.affected_relationship_ids
    ):
        return False
    if operation is LifecycleOperation.ERASE:
        return details.inventory.counts["deleted"].get("relationship", 0) == 1
    return details.inventory.counts["deleted"] == {} and any(
        proof.relationship_id == relationship_id for proof in details.rebuild_proofs
    )


@dataclass(frozen=True)
class RelationshipSpec:
    """One original synthetic Agent x User relationship used by a Scenario."""

    key: str
    agent_id: str
    user_id: str
    persona_source: str
    pivotal_signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("key", "agent_id", "user_id", "persona_source"):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(getattr(self, field_name), field_name),
            )
        pivotal = tuple(_require_identifier(item, "pivotal signal") for item in self.pivotal_signals)
        if len(pivotal) != len(set(pivotal)):
            raise ValueError("pivotal_signals must be unique")
        object.__setattr__(self, "pivotal_signals", pivotal)


@dataclass(frozen=True)
class GrowthSpec:
    """A source-bound pending growth proposal requested after one Turn."""

    intent_key: str
    review_id: str
    statement: str
    rationale: str
    proposed_changes: Mapping[str, Any]
    supporting_candidate_keys: tuple[str, ...]
    trigger_kind: str = "accumulation"

    def __post_init__(self) -> None:
        for field_name in (
            "intent_key",
            "review_id",
            "statement",
            "rationale",
            "trigger_kind",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(getattr(self, field_name), field_name),
            )
        GrowthTriggerKind(self.trigger_kind)
        keys = tuple(
            _require_identifier(item, "supporting candidate key")
            for item in self.supporting_candidate_keys
        )
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("supporting_candidate_keys must be non-empty and unique")
        object.__setattr__(self, "supporting_candidate_keys", keys)
        try:
            normalized = json.loads(_canonical_bytes(self.proposed_changes))
        except (TypeError, ValueError) as exc:
            raise ValueError("proposed_changes must be JSON-compatible") from exc
        if not isinstance(normalized, dict):
            raise ValueError("proposed_changes must be a JSON object")
        object.__setattr__(self, "proposed_changes", normalized)


@dataclass(frozen=True)
class AuthoritySpec:
    """One deterministic candidate presented to the production adjudicator."""

    candidate_key: str
    event_type: str
    summary: str
    signal_type: str
    strength: str = "moderate"
    expected_accepted: bool = True
    grounded: bool = True
    persona_reflection: str | None = None
    growth_trigger: str = "none"
    growth: GrowthSpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.expected_accepted, bool) or not isinstance(
            self.grounded,
            bool,
        ):
            raise TypeError("authority acceptance and grounding flags must be bool")
        for field_name in (
            "candidate_key",
            "event_type",
            "summary",
            "signal_type",
            "strength",
            "growth_trigger",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(getattr(self, field_name), field_name),
            )
        if self.persona_reflection is not None:
            object.__setattr__(
                self,
                "persona_reflection",
                _require_identifier(self.persona_reflection, "persona_reflection"),
            )
        if self.growth is not None and not self.expected_accepted:
            raise ValueError("a rejected candidate cannot create a growth proposal")
        RelationshipEventType(self.event_type)
        RelationshipSignalType(self.signal_type)
        SignalStrength(self.strength)
        GrowthTriggerKind(self.growth_trigger)


@dataclass(frozen=True)
class TurnSpec:
    """One visible exchange; most Turns intentionally request no artifact."""

    ordinal: int
    relationship_key: str
    turn_id: str
    user_message: str
    agent_message: str
    authority: AuthoritySpec | None = None

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int):
            raise TypeError("Turn ordinal must be an integer")
        if self.ordinal < 1:
            raise ValueError("Turn ordinal must be positive")
        for field_name in (
            "relationship_key",
            "turn_id",
            "user_message",
            "agent_message",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True)
class ProjectionProbe:
    """A deterministic correction case for the production projector."""

    relationship_key: str
    belief_key: str
    initial_value: Any
    corrected_value: Any

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relationship_key",
            _require_identifier(self.relationship_key, "relationship_key"),
        )
        object.__setattr__(
            self,
            "belief_key",
            _require_identifier(self.belief_key, "belief_key"),
        )
        try:
            _canonical_bytes(self.initial_value)
            _canonical_bytes(self.corrected_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("projection values must be JSON-compatible") from exc
        if _canonical_bytes(self.initial_value) == _canonical_bytes(self.corrected_value):
            raise ValueError("a ProjectionProbe must actually change the belief value")


@dataclass(frozen=True)
class RecallProbe:
    """A query whose required and forbidden event candidates stay content-private."""

    probe_id: str
    relationship_key: str
    query: str
    expected_candidate_keys: tuple[str, ...]
    forbidden_candidate_keys: tuple[str, ...]
    top_k: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "probe_id",
            _require_report_identifier(self.probe_id, "probe_id"),
        )
        for field_name in ("relationship_key", "query"):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(getattr(self, field_name), field_name),
            )
        expected = tuple(
            _require_identifier(item, "expected candidate key")
            for item in self.expected_candidate_keys
        )
        forbidden = tuple(
            _require_identifier(item, "forbidden candidate key")
            for item in self.forbidden_candidate_keys
        )
        if not expected or not forbidden:
            raise ValueError("a RecallProbe requires positive and negative candidates")
        if len(expected) != len(set(expected)) or len(forbidden) != len(set(forbidden)):
            raise ValueError("RecallProbe candidate keys must be unique within each set")
        if set(expected).intersection(forbidden):
            raise ValueError("expected and forbidden recall candidates must be disjoint")
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int) or self.top_k < 1:
            raise ValueError("RecallProbe top_k must be a positive integer")
        object.__setattr__(self, "expected_candidate_keys", expected)
        object.__setattr__(self, "forbidden_candidate_keys", forbidden)


@dataclass(frozen=True)
class Scenario:
    """A complete, immutable longitudinal input trajectory."""

    scenario_id: str
    relationships: tuple[RelationshipSpec, ...]
    turns: tuple[TurnSpec, ...]
    projection_probes: tuple[ProjectionProbe, ...] = ()
    recall_probes: tuple[RecallProbe, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scenario_id",
            _require_report_identifier(self.scenario_id, "scenario_id"),
        )
        relationships = tuple(self.relationships)
        turns = tuple(self.turns)
        probes = tuple(self.projection_probes)
        recall_probes = tuple(self.recall_probes)
        if not relationships or not turns:
            raise ValueError("a Scenario requires relationships and Turns")
        relationship_keys = [item.key for item in relationships]
        if len(relationship_keys) != len(set(relationship_keys)):
            raise ValueError("relationship keys must be unique")
        if len({(item.agent_id, item.user_id) for item in relationships}) != len(
            relationships
        ):
            raise ValueError("Agent x User pairs must be unique")
        if [turn.ordinal for turn in turns] != list(range(1, len(turns) + 1)):
            raise ValueError("Turn ordinals must form a contiguous one-based sequence")
        turn_ids = [turn.turn_id for turn in turns]
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("turn IDs must be globally unique within a Scenario")
        known = set(relationship_keys)
        if any(turn.relationship_key not in known for turn in turns):
            raise ValueError("every Turn must reference a declared relationship")
        if any(probe.relationship_key not in known for probe in probes):
            raise ValueError("every ProjectionProbe must reference a declared relationship")
        if any(probe.relationship_key not in known for probe in recall_probes):
            raise ValueError("every RecallProbe must reference a declared relationship")
        recall_probe_ids = [probe.probe_id for probe in recall_probes]
        if len(recall_probe_ids) != len(set(recall_probe_ids)):
            raise ValueError("recall probe IDs must be unique")
        candidate_keys = [
            turn.authority.candidate_key
            for turn in turns
            if turn.authority is not None
        ]
        if len(candidate_keys) != len(set(candidate_keys)):
            raise ValueError("candidate keys must be globally unique within a Scenario")
        seen: dict[str, str] = {}
        candidate_acceptance: dict[str, bool] = {}
        for turn in turns:
            authority = turn.authority
            if authority is None:
                continue
            if authority.grounded and authority.summary not in (
                turn.user_message + "\n" + turn.agent_message
            ):
                raise ValueError(
                    "a grounded synthetic authority summary must occur in its visible Turn"
                )
            if authority.growth is not None and not set(
                authority.growth.supporting_candidate_keys
            ).issubset(set(seen) | {authority.candidate_key}):
                raise ValueError("growth support must reference current or earlier candidates")
            if authority.growth is not None and any(
                seen.get(key, turn.relationship_key) != turn.relationship_key
                for key in authority.growth.supporting_candidate_keys
            ):
                raise ValueError("growth support cannot cross relationship scope")
            seen[authority.candidate_key] = turn.relationship_key
            candidate_acceptance[authority.candidate_key] = authority.expected_accepted
        for probe in recall_probes:
            referenced = probe.expected_candidate_keys + probe.forbidden_candidate_keys
            if any(key not in seen for key in referenced):
                raise ValueError("RecallProbe references an unknown candidate")
            if any(not candidate_acceptance[key] for key in referenced):
                raise ValueError("RecallProbe candidates must be accepted events")
            if any(seen[key] != probe.relationship_key for key in probe.expected_candidate_keys):
                raise ValueError("positive recall candidates must belong to the probed relationship")
        object.__setattr__(self, "relationships", relationships)
        object.__setattr__(self, "turns", turns)
        object.__setattr__(self, "projection_probes", probes)
        object.__setattr__(self, "recall_probes", recall_probes)

    @property
    def ordinary_turn_count(self) -> int:
        return sum(turn.authority is None for turn in self.turns)

    @property
    def expected_event_count(self) -> int:
        return sum(
            turn.authority is not None and turn.authority.expected_accepted
            for turn in self.turns
        )

    @property
    def expected_growth_count(self) -> int:
        return sum(
            turn.authority is not None and turn.authority.growth is not None
            for turn in self.turns
        )

    @property
    def fingerprint(self) -> str:
        return _digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "relationships": [
                {
                    "key": item.key,
                    "agent_id": item.agent_id,
                    "user_id": item.user_id,
                    "persona_source": item.persona_source,
                    "pivotal_signals": list(item.pivotal_signals),
                }
                for item in self.relationships
            ],
            "turns": [
                {
                    "ordinal": turn.ordinal,
                    "relationship_key": turn.relationship_key,
                    "turn_id": turn.turn_id,
                    "user_message": turn.user_message,
                    "agent_message": turn.agent_message,
                    "authority": _authority_content(turn.authority),
                }
                for turn in self.turns
            ],
            "projection_probes": [
                {
                    "relationship_key": probe.relationship_key,
                    "belief_key": probe.belief_key,
                    "initial_value": probe.initial_value,
                    "corrected_value": probe.corrected_value,
                }
                for probe in self.projection_probes
            ],
            "recall_probes": [
                {
                    "probe_id": probe.probe_id,
                    "relationship_key": probe.relationship_key,
                    "query": probe.query,
                    "expected_candidate_keys": list(probe.expected_candidate_keys),
                    "forbidden_candidate_keys": list(probe.forbidden_candidate_keys),
                    "top_k": probe.top_k,
                }
                for probe in self.recall_probes
            ],
        }


def _authority_content(authority: AuthoritySpec | None) -> object:
    if authority is None:
        return None
    growth = authority.growth
    return {
        "candidate_key": authority.candidate_key,
        "event_type": authority.event_type,
        "summary": authority.summary,
        "signal_type": authority.signal_type,
        "strength": authority.strength,
        "expected_accepted": authority.expected_accepted,
        "grounded": authority.grounded,
        "persona_reflection": authority.persona_reflection,
        "growth_trigger": authority.growth_trigger,
        "growth": (
            None
            if growth is None
            else {
                "intent_key": growth.intent_key,
                "review_id": growth.review_id,
                "statement": growth.statement,
                "rationale": growth.rationale,
                "proposed_changes": growth.proposed_changes,
                "supporting_candidate_keys": list(growth.supporting_candidate_keys),
                "trigger_kind": growth.trigger_kind,
            }
        ),
    }


@dataclass(frozen=True)
class FaultSchedule:
    """Deterministic process faults applied at stable Turn ordinals."""

    retry_at: frozenset[int] = frozenset()
    restart_after: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        raw_faults = tuple(self.retry_at) + tuple(self.restart_after)
        if any(isinstance(item, bool) or not isinstance(item, int) for item in raw_faults):
            raise TypeError("fault ordinals must be integers")
        retries = frozenset(self.retry_at)
        restarts = frozenset(self.restart_after)
        if any(item < 1 for item in retries | restarts):
            raise ValueError("fault ordinals must be positive")
        object.__setattr__(self, "retry_at", retries)
        object.__setattr__(self, "restart_after", restarts)

    def validate(self, scenario: Scenario) -> None:
        maximum = len(scenario.turns)
        if any(item > maximum for item in self.retry_at | self.restart_after):
            raise ValueError("fault schedule references a Turn outside the Scenario")


@dataclass(frozen=True)
class ApplyResult:
    """Content-free identities produced by applying one Turn."""

    relationship_key: str
    turn_id: str
    decision_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    growth_proposal_ids: tuple[str, ...] = ()
    accepted: bool | None = None


@dataclass(frozen=True)
class EventProvenance:
    event_id: str
    source_turn_id: str
    evidence_count: int
    source_verified: bool


@dataclass(frozen=True)
class GrowthObservation:
    proposal_id: str
    supporting_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class RelationshipObservation:
    relationship_key: str
    turn_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    state_digest: str
    belief_digest: str
    provenance: tuple[EventProvenance, ...]
    growth: tuple[GrowthObservation, ...]
    reflection_event_ids: tuple[str, ...]

    @property
    def authority_digest(self) -> str:
        return _digest(
            {
                "event_ids": list(self.event_ids),
                "decision_ids": list(self.decision_ids),
                "state_digest": self.state_digest,
                "belief_digest": self.belief_digest,
                "growth": [
                    {
                        "proposal_id": item.proposal_id,
                        "supporting_event_ids": list(item.supporting_event_ids),
                    }
                    for item in self.growth
                ],
            }
        )


@dataclass(frozen=True)
class SystemObservation:
    relationships: tuple[RelationshipObservation, ...]

    @property
    def digest(self) -> str:
        return _digest(
            [
                {
                    "relationship_key": item.relationship_key,
                    "turn_ids": list(item.turn_ids),
                    "event_ids": list(item.event_ids),
                    "decision_ids": list(item.decision_ids),
                    "state_digest": item.state_digest,
                    "belief_digest": item.belief_digest,
                    "provenance": [
                        {
                            "event_id": source.event_id,
                            "source_turn_id": source.source_turn_id,
                            "evidence_count": source.evidence_count,
                            "source_verified": source.source_verified,
                        }
                        for source in item.provenance
                    ],
                    "growth": [
                        {
                            "proposal_id": growth.proposal_id,
                            "supporting_event_ids": list(growth.supporting_event_ids),
                        }
                        for growth in item.growth
                    ],
                    "reflection_event_ids": list(item.reflection_event_ids),
                }
                for item in self.relationships
            ]
        )

    @property
    def normalized_digest(self) -> str:
        """Hashes the same graph without run-local UUID identities."""
        normalized = []
        for item in self.relationships:
            event_names = {
                event_id: f"event-{index}"
                for index, event_id in enumerate(item.event_ids, 1)
            }
            provenance = {source.event_id: source for source in item.provenance}
            normalized.append(
                {
                    "relationship_key": item.relationship_key,
                    "turn_ids": list(item.turn_ids),
                    "event_sources": [
                        {
                            "event": event_names[event_id],
                            "source_turn_id": provenance[event_id].source_turn_id,
                            "evidence_count": provenance[event_id].evidence_count,
                            "source_verified": provenance[event_id].source_verified,
                        }
                        for event_id in item.event_ids
                        if event_id in provenance
                    ],
                    "untraced_event_count": sum(
                        event_id not in provenance for event_id in item.event_ids
                    ),
                    "decision_count": len(item.decision_ids),
                    "state_digest": item.state_digest,
                    "belief_digest": item.belief_digest,
                    "growth": [
                        {
                            "proposal": f"growth-{index}",
                            "supporting_events": [
                                event_names.get(event_id, "unresolved")
                                for event_id in growth.supporting_event_ids
                            ],
                        }
                        for index, growth in enumerate(item.growth, 1)
                    ],
                    "reflection_events": [
                        event_names.get(event_id, "unresolved")
                        for event_id in item.reflection_event_ids
                    ],
                }
            )
        return _digest(normalized)

    def by_key(self) -> dict[str, RelationshipObservation]:
        return {item.relationship_key: item for item in self.relationships}


@dataclass(frozen=True)
class PortabilityObservation:
    """Content-free evidence from export, fresh import, and duplicate import."""

    target_adapter_id: str
    export_count: int
    import_count: int
    duplicate_import_count: int
    imported: SystemObservation
    duplicate: SystemObservation
    export_elapsed_ns: int
    import_elapsed_ns: int
    duplicate_import_elapsed_ns: int


@dataclass(frozen=True)
class RecallProbeObservation:
    """Body-free outcome of one real structured recall operation."""

    probe_id: str
    expected_count: int
    positive_match_count: int
    forbidden_match_count: int
    projection_provenance_failures: int


@dataclass(frozen=True)
class PerformanceObservation:
    """One elapsed-time observation with a stable scale and wide CI ceiling."""

    operation: str
    scale_unit: str
    scale_count: int
    elapsed_ns: int
    maximum_ms: float

    def __post_init__(self) -> None:
        _require_report_identifier(self.operation, "performance operation")
        _require_report_identifier(self.scale_unit, "performance scale unit")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.scale_count, self.elapsed_ns)
        ):
            raise ValueError("performance scale and elapsed time must be non-negative integers")
        if self.maximum_ms <= 0:
            raise ValueError("performance maximum_ms must be positive")

    @property
    def elapsed_ms(self) -> float:
        return round(self.elapsed_ns / 1_000_000, 3)

    @property
    def passed(self) -> bool:
        return self.elapsed_ms <= self.maximum_ms

    def to_dict(self, *, include_measurement: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "operation": self.operation,
            "scale_unit": self.scale_unit,
            "scale_count": self.scale_count,
            "maximum_ms": self.maximum_ms,
        }
        if include_measurement:
            payload["elapsed_ms"] = self.elapsed_ms
            payload["passed"] = self.passed
        return payload


@dataclass(frozen=True)
class LifecyclePerformanceObservation:
    """One public lifecycle operation measured on a disposable production store."""

    operation: str
    scale_unit: str
    scale_count: int
    elapsed_ns: int
    maximum_ms: float
    peak_python_memory_bytes: int
    peak_python_memory_maximum_bytes: int
    final_storage_size_bytes: int
    final_storage_size_maximum_bytes: int
    verified: bool

    def __post_init__(self) -> None:
        _require_report_identifier(self.operation, "lifecycle operation")
        _require_report_identifier(self.scale_unit, "lifecycle scale unit")
        integer_fields = (
            "scale_count",
            "elapsed_ns",
            "peak_python_memory_bytes",
            "peak_python_memory_maximum_bytes",
            "final_storage_size_bytes",
            "final_storage_size_maximum_bytes",
        )
        for field_name in integer_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.maximum_ms <= 0:
            raise ValueError("lifecycle maximum_ms must be positive")
        if not isinstance(self.verified, bool):
            raise TypeError("lifecycle verified flag must be bool")

    @property
    def elapsed_ms(self) -> float:
        return round(self.elapsed_ns / 1_000_000, 3)

    @property
    def passed(self) -> bool:
        return (
            self.verified
            and self.elapsed_ms <= self.maximum_ms
            and self.peak_python_memory_bytes
            <= self.peak_python_memory_maximum_bytes
            and self.final_storage_size_bytes
            <= self.final_storage_size_maximum_bytes
        )

    def to_dict(self, *, include_measurement: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "operation": self.operation,
            "scale_unit": self.scale_unit,
            "scale_count": self.scale_count,
            "maximum_ms": self.maximum_ms,
            "peak_python_memory_maximum_bytes": (
                self.peak_python_memory_maximum_bytes
            ),
            "final_storage_size_maximum_bytes": (
                self.final_storage_size_maximum_bytes
            ),
            "verified": self.verified,
        }
        if include_measurement:
            payload.update(
                {
                    "elapsed_ms": self.elapsed_ms,
                    "peak_python_memory_bytes": self.peak_python_memory_bytes,
                    "final_storage_size_bytes": self.final_storage_size_bytes,
                    "passed": self.passed,
                }
            )
        return payload


@runtime_checkable
class LongitudinalSystemAdapter(Protocol):
    """The replaceable system seam exercised by LongitudinalEvalRunner."""

    @property
    def adapter_id(self) -> str: ...

    def prepare(self, scenario: Scenario) -> None: ...

    def apply(self, turn: TurnSpec) -> ApplyResult: ...

    def observe(self, scenario: Scenario) -> SystemObservation: ...

    def portability_round_trip(self, scenario: Scenario) -> PortabilityObservation: ...

    def probe_recall(self, scenario: Scenario) -> tuple[RecallProbeObservation, ...]: ...

    def storage_size_bytes(self) -> int: ...

    def lifecycle_performance(
        self,
        scenario: Scenario,
        tier: str,
    ) -> tuple[LifecyclePerformanceObservation, ...]: ...

    def restart(self) -> None: ...

    def close(self) -> None: ...


class _ERIIEvalAdapter:
    """Shared implementation that drives the production Engine and projector."""

    def __init__(self) -> None:
        self._engine: ERIIEngine | None = None
        self._scenario: Scenario | None = None

    @property
    def adapter_id(self) -> str:
        raise NotImplementedError

    def _open_engine(self) -> ERIIEngine:
        raise NotImplementedError

    @property
    def engine(self) -> ERIIEngine:
        if self._engine is None:
            raise RuntimeError("prepare() must be called before using the Adapter")
        return self._engine

    def prepare(self, scenario: Scenario) -> None:
        if self._scenario is not None and self._scenario.fingerprint != scenario.fingerprint:
            raise ValueError("an Adapter instance cannot change Scenario identity")
        self._scenario = scenario
        if self._engine is None:
            self._engine = self._open_engine()
        for relationship in scenario.relationships:
            compiled = {
                "relationship_policy": {
                    "version": "synthetic-longitudinal-policy/v1",
                    "pivotal_signals": list(relationship.pivotal_signals),
                }
            }
            self.engine.initialize_relationship(
                relationship.agent_id,
                relationship.user_id,
                relationship.persona_source,
                compiled_persona=compiled,
            )

    def apply(self, turn: TurnSpec) -> ApplyResult:
        relationship = self._relationship(turn.relationship_key)
        self.engine.record_turn(
            relationship.agent_id,
            relationship.user_id,
            turn.user_message,
            turn.agent_message,
            turn_id=turn.turn_id,
            delivery_exception={
                "exception_record_version": "delivery-exception-record/v1",
                "disposition": "shown_unreviewed",
                "actor_kind": "host_policy",
                "actor_id": "erii.synthetic-longitudinal/v1",
                "reason_code": "preexisting_visible_exchange",
                "decided_at": "2026-08-02T00:00:00+00:00",
                "reply_attempt_number": None,
            },
            processing_channels=(),
        )
        authority = turn.authority
        if authority is None:
            return ApplyResult(turn.relationship_key, turn.turn_id)

        persisted_turn = self.engine.get_turn(
            relationship.agent_id,
            relationship.user_id,
            turn.turn_id,
        )
        source_message = persisted_turn.transcript.user_message
        if source_message is None:
            raise RuntimeError("a completed synthetic Turn lost its user source")
        quote = authority.summary if authority.grounded else "unsupported synthetic claim"
        candidate: dict[str, object] = {
            "candidate_key": authority.candidate_key,
            "event_type": authority.event_type,
            "summary": authority.summary,
            "signal": {
                "signal_type": authority.signal_type,
                "strength": authority.strength,
                "extraction_confidence": 0.95,
                "interpretation_confidence": 0.95,
            },
            "evidence": [
                {
                    "source_id": source_message.message_id,
                    "source_revision": persisted_turn.source_revision,
                    "quote": quote,
                }
            ],
            "growth_trigger": authority.growth_trigger,
        }
        if authority.persona_reflection is not None:
            candidate["persona_reflection"] = authority.persona_reflection
        result = self.engine.adjudicate_turn_candidates(
            relationship.agent_id,
            relationship.user_id,
            turn.turn_id,
            [candidate],
            extractor_version=EXTRACTOR_VERSION,
        )
        accepted = bool(result.events) and all(
            receipt.outcome in _ACCEPTED_OUTCOMES for receipt in result.receipts
        )
        growth_ids: tuple[str, ...] = ()
        if authority.growth is not None and accepted:
            event_ids_by_candidate = {
                record.receipt.candidate_key: event_id
                for record in self.engine.list_relationship_adjudications(
                    relationship.agent_id,
                    relationship.user_id,
                )
                for event_id in record.receipt.event_ids
            }
            growth = authority.growth
            proposal = self.engine.propose_persona_growth(
                relationship.agent_id,
                relationship.user_id,
                {
                    "intent_key": growth.intent_key,
                    "review_id": growth.review_id,
                    "statement": growth.statement,
                    "rationale": growth.rationale,
                    "proposed_changes": growth.proposed_changes,
                    "supporting_event_ids": [
                        event_ids_by_candidate[key]
                        for key in growth.supporting_candidate_keys
                    ],
                    "trigger_kind": growth.trigger_kind,
                },
            )
            growth_ids = (proposal.proposal_id,)
        return ApplyResult(
            relationship_key=turn.relationship_key,
            turn_id=turn.turn_id,
            decision_ids=tuple(receipt.decision_id for receipt in result.receipts),
            event_ids=tuple(event.event_id for event in result.events),
            growth_proposal_ids=growth_ids,
            accepted=accepted,
        )

    def observe(self, scenario: Scenario) -> SystemObservation:
        relationships: list[RelationshipObservation] = []
        for spec in scenario.relationships:
            turns = self.engine.list_turns(spec.agent_id, spec.user_id)
            turns_by_id = {turn.turn_id: turn for turn in turns}
            events = self.engine.list_relationship_events(spec.agent_id, spec.user_id)
            decisions = self.engine.list_relationship_adjudications(
                spec.agent_id,
                spec.user_id,
            )
            snapshot = self.engine.get_relationship_snapshot(spec.agent_id, spec.user_id)
            growth = self.engine.list_persona_growth_proposals(spec.agent_id, spec.user_id)
            provenance = tuple(
                EventProvenance(
                    event_id=event_id,
                    source_turn_id=record.receipt.source_turn_id,
                    evidence_count=len(record.receipt.evidence),
                    source_verified=self._source_is_verified(record.receipt, turns_by_id),
                )
                for record in decisions
                for event_id in record.receipt.event_ids
            )
            reflection_ids = tuple(
                event.event_id
                for event in events
                if isinstance(event.metadata.get("adjudication"), Mapping)
                and event.metadata["adjudication"].get("persona_reflection") is not None
            )
            relationships.append(
                RelationshipObservation(
                    relationship_key=spec.key,
                    turn_ids=tuple(turn.turn_id for turn in turns),
                    event_ids=tuple(event.event_id for event in events),
                    decision_ids=tuple(record.receipt.decision_id for record in decisions),
                    state_digest=_digest(snapshot.state.to_dict()),
                    belief_digest=_digest(
                        {
                            key: {
                                "value": value.value,
                                "confidence": value.confidence,
                            }
                            for key, value in sorted(snapshot.beliefs.items())
                        }
                    ),
                    provenance=provenance,
                    growth=tuple(
                        GrowthObservation(
                            proposal_id=item.proposal_id,
                            supporting_event_ids=tuple(item.supporting_event_ids),
                        )
                        for item in growth
                    ),
                    reflection_event_ids=reflection_ids,
                )
            )
        return SystemObservation(tuple(relationships))

    def portability_round_trip(self, scenario: Scenario) -> PortabilityObservation:
        """Exports every relationship into a fresh opposite production backend."""

        export_started = time.perf_counter_ns()
        packs = [
            self.engine.export_memory(spec.agent_id, spec.user_id)
            for spec in scenario.relationships
        ]
        export_elapsed = time.perf_counter_ns() - export_started
        with tempfile.TemporaryDirectory(prefix="erii-longitudinal-portability-") as root:
            target = self._portability_target(Path(root))
            target._scenario = scenario
            target._engine = target._open_engine()
            try:
                import_started = time.perf_counter_ns()
                for pack in packs:
                    target.engine.import_memory(pack)
                import_elapsed = time.perf_counter_ns() - import_started
                imported = target.observe(scenario)
                duplicate_started = time.perf_counter_ns()
                for pack in packs:
                    target.engine.import_memory(pack)
                duplicate_elapsed = time.perf_counter_ns() - duplicate_started
                duplicate = target.observe(scenario)
                return PortabilityObservation(
                    target_adapter_id=target.adapter_id,
                    export_count=len(packs),
                    import_count=len(packs),
                    duplicate_import_count=len(packs),
                    imported=imported,
                    duplicate=duplicate,
                    export_elapsed_ns=export_elapsed,
                    import_elapsed_ns=import_elapsed,
                    duplicate_import_elapsed_ns=duplicate_elapsed,
                )
            finally:
                target.close()

    def probe_recall(self, scenario: Scenario) -> tuple[RecallProbeObservation, ...]:
        """Executes positive and negative probes through structured production recall."""

        relationships = {item.key: item for item in scenario.relationships}
        candidate_event_ids: dict[str, set[str]] = {}
        for relationship in scenario.relationships:
            for record in self.engine.list_relationship_adjudications(
                relationship.agent_id,
                relationship.user_id,
            ):
                candidate_event_ids.setdefault(record.receipt.candidate_key, set()).update(
                    record.receipt.event_ids
                )

        observations: list[RecallProbeObservation] = []
        for probe in scenario.recall_probes:
            relationship = relationships[probe.relationship_key]
            result = self.engine.recall_structured(
                RecallRequest(
                    agent_id=relationship.agent_id,
                    user_id=relationship.user_id,
                    query=probe.query,
                    audience=RecallAudience.AGENT_PRIVATE,
                    options=RecallOptions(
                        top_k=probe.top_k,
                        max_per_type=probe.top_k,
                        reinforce=False,
                        persona_delivery=PersonaDelivery.FULL,
                    ),
                )
            )
            selected_ids = {event.source_id for event in result.events}
            positive_matches = sum(
                bool(candidate_event_ids.get(key, set()).intersection(selected_ids))
                for key in probe.expected_candidate_keys
            )
            forbidden_matches = sum(
                len(candidate_event_ids.get(key, set()).intersection(selected_ids))
                for key in probe.forbidden_candidate_keys
            )
            provenance_failures = sum(
                not any(
                    reference.source_id == event.source_id
                    and reference.source_kind == "relationship_event"
                    for reference in event.source_references
                )
                for event in result.events
            )
            observations.append(
                RecallProbeObservation(
                    probe_id=probe.probe_id,
                    expected_count=len(probe.expected_candidate_keys),
                    positive_match_count=positive_matches,
                    forbidden_match_count=forbidden_matches,
                    projection_provenance_failures=provenance_failures,
                )
            )
        return tuple(observations)

    def lifecycle_performance(
        self,
        scenario: Scenario,
        tier: str,
    ) -> tuple[LifecyclePerformanceObservation, ...]:
        """Measures public erase and rebuild on independent disposable clones."""

        packs = tuple(
            self.engine.export_memory(spec.agent_id, spec.user_id)
            for spec in scenario.relationships
        )
        selected = packs[0].relationship
        if selected is None:
            raise RuntimeError("longitudinal lifecycle measurement requires a relationship")
        selector = ErasureSelector(
            scope=ErasureScope.RELATIONSHIP,
            agent_id=selected.agent_id,
            user_id=selected.user_id,
            relationship_id=selected.relationship_id,
        )
        with tempfile.TemporaryDirectory(prefix="erii-longitudinal-lifecycle-") as root:
            workspace = Path(root)
            return tuple(
                self._measure_lifecycle_operation(
                    operation=operation,
                    live_target=self._seed_lifecycle_store(
                        workspace,
                        operation.value,
                        packs,
                    ),
                    selector=selector,
                    backup_target=LifecycleTarget(
                        kind=LifecycleTargetKind.BACKUP,
                        path=str(workspace / f"{operation.value}.eriibak"),
                    ),
                    relationship_count=len(scenario.relationships),
                    tier=tier,
                )
                for operation in (LifecycleOperation.ERASE, LifecycleOperation.REBUILD)
            )

    def _seed_lifecycle_store(
        self,
        root: Path,
        label: str,
        packs: tuple[MemoryPack, ...],
    ) -> LifecycleTarget:
        path = self._lifecycle_store_path(root, label)
        with self._open_lifecycle_engine(path) as engine:
            for pack in packs:
                engine.import_memory(pack)
        target = LifecycleTarget(kind=self._lifecycle_target_kind, path=str(path))
        if target.kind is LifecycleTargetKind.FILE_STORAGE:
            (path / FILE_STORAGE_MANIFEST).write_bytes(
                b'{"format":"erii.file-storage","version":2}'
            )
        return target

    @staticmethod
    def _measure_lifecycle_operation(
        *,
        operation: LifecycleOperation,
        live_target: LifecycleTarget,
        selector: ErasureSelector,
        backup_target: LifecycleTarget,
        relationship_count: int,
        tier: str,
    ) -> LifecyclePerformanceObservation:
        tracing_started_here = not tracemalloc.is_tracing()
        if tracing_started_here:
            tracemalloc.start()
        tracemalloc.reset_peak()
        started = time.perf_counter_ns()
        try:
            coordinator = DataLifecycleCoordinator()
            source = coordinator.inspect(live_target)
            request_type = (
                EraseRequest
                if operation is LifecycleOperation.ERASE
                else RebuildRequest
            )
            request = request_type(
                source=source,
                selector=selector,
                backup_destination=backup_target,
            )
            report = coordinator.execute(coordinator.plan(request))
            final = coordinator.inspect(live_target)
            elapsed_ns = time.perf_counter_ns() - started
            peak_python_memory_bytes = tracemalloc.get_traced_memory()[1]
        finally:
            if tracing_started_here:
                tracemalloc.stop()

        details = report.details
        verified = (
            report.operation is operation
            and report.outcome is LifecycleOutcome.APPLIED
            and final.status is LifecycleStatus.CURRENT
            and report.artifact_fingerprint == final.fingerprint
            and isinstance(details, ErasureTransformResult)
            and _selected_relationship_is_verified(operation, details, selector)
        )
        limits = _PERFORMANCE_LIMITS[tier]
        return LifecyclePerformanceObservation(
            operation=operation.value,
            scale_unit="relationships",
            scale_count=relationship_count,
            elapsed_ns=elapsed_ns,
            maximum_ms=limits[f"{operation.value}_ms"],
            peak_python_memory_bytes=peak_python_memory_bytes,
            peak_python_memory_maximum_bytes=int(
                limits["peak_python_memory_bytes"]
            ),
            final_storage_size_bytes=_lifecycle_storage_size_bytes(live_target),
            final_storage_size_maximum_bytes=int(limits["storage_size_bytes"]),
            verified=verified,
        )

    @property
    def _lifecycle_target_kind(self) -> LifecycleTargetKind:
        raise NotImplementedError

    def _lifecycle_store_path(self, root: Path, label: str) -> Path:
        raise NotImplementedError

    def _open_lifecycle_engine(self, path: Path) -> ERIIEngine:
        raise NotImplementedError

    def _portability_target(self, root: Path) -> "_ERIIEvalAdapter":
        raise NotImplementedError

    @staticmethod
    def _source_is_verified(
        receipt: DecisionReceipt,
        turns_by_id: Mapping[str, TurnRecord],
    ) -> bool:
        turn = turns_by_id.get(receipt.source_turn_id)
        if turn is None or turn.source_revision != receipt.source_revision:
            return False
        messages = {
            message.message_id: message
            for message in (
                turn.transcript.user_message,
                turn.transcript.agent_message,
            )
            if message is not None
        }
        if not receipt.evidence:
            return False
        for evidence in receipt.evidence:
            message = messages.get(evidence.source_id)
            if message is None:
                return False
            if evidence.source_revision != receipt.source_revision:
                return False
            if hashlib.sha256(message.content.encode("utf-8")).hexdigest() != (
                evidence.message_sha256
            ):
                return False
            if message.content[evidence.start : evidence.end] != evidence.quote:
                return False
        return True

    def restart(self) -> None:
        if self._scenario is None:
            raise RuntimeError("prepare() must be called before restart()")
        if self._engine is not None:
            self._engine.close()
        self._engine = self._open_engine()
        self.prepare(self._scenario)

    def close(self) -> None:
        if self._engine is not None:
            self._engine.close()
            self._engine = None

    def _relationship(self, key: str) -> RelationshipSpec:
        if self._scenario is None:
            raise RuntimeError("prepare() must be called before apply()")
        for relationship in self._scenario.relationships:
            if relationship.key == key:
                return relationship
        raise KeyError(key)


class FileStorageEvalAdapter(_ERIIEvalAdapter):
    """Production FileStorage adapter for the longitudinal seam."""

    def __init__(self, root_dir: str | os.PathLike[str]) -> None:
        super().__init__()
        self.root_dir = os.fspath(root_dir)

    @property
    def adapter_id(self) -> str:
        return "file-storage/v2"

    def _open_engine(self) -> ERIIEngine:
        return ERIIEngine(storage_driver=FileStorage(self.root_dir))

    def _portability_target(self, root: Path) -> _ERIIEvalAdapter:
        return SQLiteEvalAdapter(root / "memory.db")

    @property
    def _lifecycle_target_kind(self) -> LifecycleTargetKind:
        return LifecycleTargetKind.FILE_STORAGE

    def _lifecycle_store_path(self, root: Path, label: str) -> Path:
        return root / label

    def _open_lifecycle_engine(self, path: Path) -> ERIIEngine:
        return ERIIEngine(storage_driver=FileStorage(path))

    def storage_size_bytes(self) -> int:
        root = Path(self.root_dir)
        if not root.exists():
            return 0
        return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


class SQLiteEvalAdapter(_ERIIEvalAdapter):
    """Production SQLiteStorage adapter for the longitudinal seam."""

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        super().__init__()
        self.db_path = os.fspath(db_path)

    @property
    def adapter_id(self) -> str:
        return "sqlite/v10"

    def _open_engine(self) -> ERIIEngine:
        return ERIIEngine(storage_driver=SQLiteStorage(self.db_path))

    def _portability_target(self, root: Path) -> _ERIIEvalAdapter:
        return FileStorageEvalAdapter(root / "files")

    @property
    def _lifecycle_target_kind(self) -> LifecycleTargetKind:
        return LifecycleTargetKind.SQLITE

    def _lifecycle_store_path(self, root: Path, label: str) -> Path:
        return root / f"{label}.sqlite3"

    def _open_lifecycle_engine(self, path: Path) -> ERIIEngine:
        return ERIIEngine(storage_driver=SQLiteStorage(path))

    def storage_size_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for path in (
                Path(self.db_path),
                Path(f"{self.db_path}-wal"),
                Path(f"{self.db_path}-shm"),
            )
            if path.is_file()
        )


@dataclass(frozen=True)
class MetricResult:
    """One explicit hard gate; no blended quality score is emitted."""

    name: str
    failures: int
    maximum: int = 0

    @property
    def passed(self) -> bool:
        return self.failures <= self.maximum

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "failures": self.failures,
            "maximum": self.maximum,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class EvalReport:
    """A content-free report with a deterministic correctness digest."""

    scenario_id: str
    scenario_fingerprint: str
    adapter_id: str
    turn_count: int
    ordinary_turn_count: int
    expected_event_count: int
    observed_event_count: int
    expected_growth_count: int
    observed_growth_count: int
    restart_count: int
    retry_count: int
    portability_target_adapter_id: str
    portability_export_count: int
    portability_import_count: int
    portability_duplicate_import_count: int
    portability_observation_digest: str
    recall_probe_count: int
    recall_expected_match_count: int
    recall_positive_match_count: int
    recall_forbidden_match_count: int
    performance_tier: str
    performance_observations: tuple[PerformanceObservation, ...]
    lifecycle_performance_observations: tuple[LifecyclePerformanceObservation, ...]
    peak_python_memory_bytes: int
    peak_python_memory_maximum_bytes: int
    final_storage_size_bytes: int
    final_storage_size_maximum_bytes: int
    final_observation_digest: str
    metrics: tuple[MetricResult, ...]
    report_version: str = REPORT_VERSION

    def __post_init__(self) -> None:
        _require_report_identifier(self.scenario_id, "scenario_id")
        _require_report_identifier(self.adapter_id, "adapter_id")
        _require_report_identifier(
            self.portability_target_adapter_id,
            "portability_target_adapter_id",
        )
        _require_report_identifier(self.performance_tier, "performance_tier")
        _require_report_identifier(self.report_version, "report_version")
        object.__setattr__(
            self,
            "performance_observations",
            tuple(self.performance_observations),
        )
        object.__setattr__(
            self,
            "lifecycle_performance_observations",
            tuple(self.lifecycle_performance_observations),
        )
        for field_name in (
            "peak_python_memory_bytes",
            "peak_python_memory_maximum_bytes",
            "final_storage_size_bytes",
            "final_storage_size_maximum_bytes",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        for field_name in (
            "scenario_fingerprint",
            "portability_observation_digest",
            "final_observation_digest",
        ):
            value = getattr(self, field_name)
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{field_name} must be a SHA-256 digest")

    @property
    def passed(self) -> bool:
        return all(metric.passed for metric in self.metrics)

    @property
    def report_digest(self) -> str:
        return _digest(
            self._payload(include_digest=False, include_measurements=False)
        )

    def _payload(
        self,
        *,
        include_digest: bool,
        include_measurements: bool,
    ) -> dict[str, object]:
        selected_metrics = tuple(
            metric
            for metric in self.metrics
            if include_measurements or metric.name not in _MEASUREMENT_METRICS
        )
        payload: dict[str, object] = {
            "report_version": self.report_version,
            "scenario_id": self.scenario_id,
            "scenario_fingerprint": self.scenario_fingerprint,
            "adapter_id": self.adapter_id,
            "passed": all(metric.passed for metric in selected_metrics),
            "counts": {
                "turns": self.turn_count,
                "ordinary_zero_artifact_turns": self.ordinary_turn_count,
                "expected_events": self.expected_event_count,
                "observed_events": self.observed_event_count,
                "expected_growth_proposals": self.expected_growth_count,
                "observed_growth_proposals": self.observed_growth_count,
                "restarts": self.restart_count,
                "retries": self.retry_count,
            },
            "portability": {
                "target_adapter_id": self.portability_target_adapter_id,
                "exports": self.portability_export_count,
                "fresh_imports": self.portability_import_count,
                "duplicate_imports": self.portability_duplicate_import_count,
                "observation_digest": self.portability_observation_digest,
            },
            "structured_recall": {
                "probes": self.recall_probe_count,
                "expected_matches": self.recall_expected_match_count,
                "positive_matches": self.recall_positive_match_count,
                "forbidden_matches": self.recall_forbidden_match_count,
            },
            "performance": {
                "tier": self.performance_tier,
                "scale": {
                    "relationships": self.portability_export_count,
                    "turns": self.turn_count,
                    "events": self.observed_event_count,
                    "growth_proposals": self.observed_growth_count,
                },
                "observations": [
                    item.to_dict(include_measurement=include_measurements)
                    for item in self.performance_observations
                ],
                "lifecycle_operations": [
                    item.to_dict(include_measurement=include_measurements)
                    for item in self.lifecycle_performance_observations
                ],
                "resources": {
                    "peak_python_memory_maximum_bytes": (
                        self.peak_python_memory_maximum_bytes
                    ),
                    "final_storage_size_maximum_bytes": (
                        self.final_storage_size_maximum_bytes
                    ),
                },
            },
            "final_observation_digest": self.final_observation_digest,
            "metrics": [metric.to_dict() for metric in selected_metrics],
        }
        if include_measurements:
            resources = payload["performance"]["resources"]
            resources["peak_python_memory_bytes"] = self.peak_python_memory_bytes
            resources["final_storage_size_bytes"] = self.final_storage_size_bytes
        if include_digest:
            payload["report_digest"] = self.report_digest
        return payload

    def to_dict(self) -> dict[str, object]:
        return self._payload(include_digest=True, include_measurements=True)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
        )

    def write_json(self, path: str | os.PathLike[str]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json() + "\n", encoding="utf-8")


@dataclass
class _RunState:
    expected_events: dict[str, set[str]] = field(default_factory=dict)
    expected_turns: dict[str, set[str]] = field(default_factory=dict)
    expected_decisions: dict[str, set[str]] = field(default_factory=dict)
    expected_growth: dict[str, set[str]] = field(default_factory=dict)
    expected_reflections: dict[str, set[str]] = field(default_factory=dict)
    acceptance_mismatches: int = 0
    state_cross_talk: int = 0
    retry_failures: int = 0
    restart_failures: int = 0


class LongitudinalEvalRunner:
    """Runs Scenario, faults, production Adapter and deterministic hard gates."""

    def run(
        self,
        scenario: Scenario,
        adapter: LongitudinalSystemAdapter,
        faults: FaultSchedule = FaultSchedule(),
    ) -> EvalReport:
        faults.validate(scenario)
        if not isinstance(adapter, LongitudinalSystemAdapter):
            raise TypeError("adapter does not satisfy LongitudinalSystemAdapter")
        state = _RunState(
            expected_events={item.key: set() for item in scenario.relationships},
            expected_turns={item.key: set() for item in scenario.relationships},
            expected_decisions={item.key: set() for item in scenario.relationships},
            expected_growth={item.key: set() for item in scenario.relationships},
            expected_reflections={item.key: set() for item in scenario.relationships},
        )
        tracing_started_here = not tracemalloc.is_tracing()
        if tracing_started_here:
            tracemalloc.start()
        try:
            adapter.prepare(scenario)
            turn_projection_started = time.perf_counter_ns()
            for turn in scenario.turns:
                before = adapter.observe(scenario) if turn.authority is not None else None
                result = adapter.apply(turn)
                state.expected_turns[turn.relationship_key].add(turn.turn_id)
                self._record_result(turn, result, state)
                if before is not None:
                    after = adapter.observe(scenario)
                    state.state_cross_talk += self._unrelated_authority_changes(
                        before,
                        after,
                        turn.relationship_key,
                    )
                if turn.ordinal in faults.retry_at:
                    before_retry = adapter.observe(scenario)
                    repeated = adapter.apply(turn)
                    self._record_result(turn, repeated, state, repeated=True)
                    after_retry = adapter.observe(scenario)
                    state.retry_failures += before_retry.digest != after_retry.digest
                if turn.ordinal in faults.restart_after:
                    before_restart = adapter.observe(scenario)
                    adapter.restart()
                    after_restart = adapter.observe(scenario)
                    state.restart_failures += before_restart.digest != after_restart.digest
            final = adapter.observe(scenario)
            turn_projection_elapsed = time.perf_counter_ns() - turn_projection_started
            recall = adapter.probe_recall(scenario)
            portability = adapter.portability_round_trip(scenario)
            peak_python_memory = tracemalloc.get_traced_memory()[1]
            final_storage_size = adapter.storage_size_bytes()
            performance_tier = _performance_tier(len(scenario.turns))
            lifecycle_performance = adapter.lifecycle_performance(
                scenario,
                performance_tier,
            )
            performance = self._performance_observations(
                scenario,
                performance_tier,
                turn_projection_elapsed,
                portability,
            )
            return self._score(
                scenario,
                adapter.adapter_id,
                faults,
                final,
                portability,
                recall,
                performance_tier,
                performance,
                lifecycle_performance,
                peak_python_memory,
                final_storage_size,
                state,
            )
        finally:
            if tracing_started_here:
                tracemalloc.stop()
            adapter.close()

    @staticmethod
    def _record_result(
        turn: TurnSpec,
        result: ApplyResult,
        state: _RunState,
        *,
        repeated: bool = False,
    ) -> None:
        authority = turn.authority
        if authority is None:
            if result.event_ids or result.decision_ids or result.growth_proposal_ids:
                state.acceptance_mismatches += 1
            return
        if result.accepted != authority.expected_accepted:
            state.acceptance_mismatches += 1
        expected_decisions = state.expected_decisions[turn.relationship_key]
        if repeated and not set(result.decision_ids).issubset(expected_decisions):
            state.acceptance_mismatches += 1
        expected_decisions.update(result.decision_ids)
        if authority.expected_accepted:
            expected = state.expected_events[turn.relationship_key]
            if repeated and not set(result.event_ids).issubset(expected):
                state.acceptance_mismatches += 1
            expected.update(result.event_ids)
            if authority.persona_reflection is not None:
                state.expected_reflections[turn.relationship_key].update(result.event_ids)
        elif result.event_ids:
            state.acceptance_mismatches += len(result.event_ids)
        expected_growth = state.expected_growth[turn.relationship_key]
        if repeated and not set(result.growth_proposal_ids).issubset(expected_growth):
            state.acceptance_mismatches += 1
        expected_growth.update(result.growth_proposal_ids)

    @staticmethod
    def _unrelated_authority_changes(
        before: SystemObservation,
        after: SystemObservation,
        changed_key: str,
    ) -> int:
        before_by_key = before.by_key()
        return sum(
            item.authority_digest != before_by_key[item.relationship_key].authority_digest
            for item in after.relationships
            if item.relationship_key != changed_key
        )

    @staticmethod
    def _performance_observations(
        scenario: Scenario,
        tier: str,
        turn_projection_elapsed_ns: int,
        portability: PortabilityObservation,
    ) -> tuple[PerformanceObservation, ...]:
        limits = _PERFORMANCE_LIMITS[tier]
        relationship_count = len(scenario.relationships)
        return (
            PerformanceObservation(
                operation="turn_projection",
                scale_unit="turns",
                scale_count=len(scenario.turns),
                elapsed_ns=turn_projection_elapsed_ns,
                maximum_ms=limits["turn_projection_ms"],
            ),
            PerformanceObservation(
                operation="export",
                scale_unit="relationships",
                scale_count=relationship_count,
                elapsed_ns=portability.export_elapsed_ns,
                maximum_ms=limits["export_ms"],
            ),
            PerformanceObservation(
                operation="import",
                scale_unit="relationships",
                scale_count=relationship_count,
                elapsed_ns=portability.import_elapsed_ns,
                maximum_ms=limits["import_ms"],
            ),
            PerformanceObservation(
                operation="duplicate_import",
                scale_unit="relationships",
                scale_count=relationship_count,
                elapsed_ns=portability.duplicate_import_elapsed_ns,
                maximum_ms=limits["duplicate_import_ms"],
            ),
        )

    def _score(
        self,
        scenario: Scenario,
        adapter_id: str,
        faults: FaultSchedule,
        final: SystemObservation,
        portability: PortabilityObservation,
        recall: tuple[RecallProbeObservation, ...],
        performance_tier: str,
        performance: tuple[PerformanceObservation, ...],
        lifecycle_performance: tuple[LifecyclePerformanceObservation, ...],
        peak_python_memory_bytes: int,
        final_storage_size_bytes: int,
        state: _RunState,
    ) -> EvalReport:
        observed_by_key = final.by_key()
        memory_leaks = 0
        turn_mismatches = 0
        event_mismatches = 0
        decision_mismatches = 0
        growth_identity_mismatches = 0
        ungrounded_writes = 0
        provenance_failures = 0
        reflection_failures = 0
        all_event_ids: set[str] = set()
        for relationship in scenario.relationships:
            observed = observed_by_key[relationship.key]
            actual_turns = set(observed.turn_ids)
            expected_turns = state.expected_turns[relationship.key]
            turn_mismatches += len(actual_turns.symmetric_difference(expected_turns))
            actual_events = set(observed.event_ids)
            expected_events = state.expected_events[relationship.key]
            event_mismatches += len(actual_events.symmetric_difference(expected_events))
            decision_mismatches += len(
                set(observed.decision_ids).symmetric_difference(
                    state.expected_decisions[relationship.key]
                )
            )
            growth_identity_mismatches += len(
                {item.proposal_id for item in observed.growth}.symmetric_difference(
                    state.expected_growth[relationship.key]
                )
            )
            ungrounded_writes += len(actual_events - expected_events)
            ungrounded_writes += len(
                set(observed.decision_ids) - state.expected_decisions[relationship.key]
            )
            ungrounded_writes += len(
                {item.proposal_id for item in observed.growth}
                - state.expected_growth[relationship.key]
            )
            memory_leaks += len(all_event_ids.intersection(actual_events))
            all_event_ids.update(actual_events)
            provenance_by_event = {item.event_id: item for item in observed.provenance}
            for event_id in actual_events:
                provenance = provenance_by_event.get(event_id)
                if (
                    provenance is None
                    or provenance.source_turn_id not in actual_turns
                    or provenance.evidence_count < 1
                    or not provenance.source_verified
                ):
                    provenance_failures += 1
            for proposal in observed.growth:
                for event_id in proposal.supporting_event_ids:
                    if event_id not in actual_events or event_id not in provenance_by_event:
                        provenance_failures += 1
            reflection_failures += len(
                set(observed.reflection_event_ids).symmetric_difference(
                    state.expected_reflections[relationship.key]
                )
            )
        correction_failures = self._projection_failures(scenario)
        observed_events = sum(len(item.event_ids) for item in final.relationships)
        observed_growth = sum(len(item.growth) for item in final.relationships)
        growth_count_failures = abs(observed_growth - scenario.expected_growth_count)
        recall_expected = sum(item.expected_count for item in recall)
        recall_positive = sum(item.positive_match_count for item in recall)
        recall_forbidden = sum(item.forbidden_match_count for item in recall)
        recall_provenance_failures = sum(
            item.projection_provenance_failures for item in recall
        )
        limits = _PERFORMANCE_LIMITS[performance_tier]
        peak_memory_maximum = int(limits["peak_python_memory_bytes"])
        storage_size_maximum = int(limits["storage_size_bytes"])
        metrics = (
            MetricResult("relationship_memory_leaks", memory_leaks),
            MetricResult("relationship_state_cross_talk", state.state_cross_talk),
            MetricResult("ungrounded_authority_writes", ungrounded_writes),
            MetricResult("authority_acceptance_mismatches", state.acceptance_mismatches),
            MetricResult("turn_identity_mismatches", turn_mismatches),
            MetricResult("event_identity_mismatches", event_mismatches),
            MetricResult("decision_identity_mismatches", decision_mismatches),
            MetricResult("growth_identity_mismatches", growth_identity_mismatches),
            MetricResult("provenance_failures", provenance_failures),
            MetricResult("reflection_provenance_failures", reflection_failures),
            MetricResult("growth_count_failures", growth_count_failures),
            MetricResult("correction_projection_failures", correction_failures),
            MetricResult("restart_idempotency_failures", state.restart_failures),
            MetricResult("retry_idempotency_failures", state.retry_failures),
            MetricResult(
                "portability_import_failures",
                int(final.digest != portability.imported.digest),
            ),
            MetricResult(
                "portability_duplicate_import_failures",
                int(portability.imported.digest != portability.duplicate.digest),
            ),
            MetricResult(
                "structured_recall_positive_failures",
                recall_expected - recall_positive,
            ),
            MetricResult("structured_recall_negative_failures", recall_forbidden),
            MetricResult(
                "structured_recall_provenance_failures",
                recall_provenance_failures,
            ),
            MetricResult(
                "performance_ceiling_failures",
                sum(not item.passed for item in performance),
            ),
            MetricResult(
                "peak_python_memory_ceiling_failures",
                int(peak_python_memory_bytes > peak_memory_maximum),
            ),
            MetricResult(
                "storage_size_ceiling_failures",
                int(final_storage_size_bytes > storage_size_maximum),
            ),
            MetricResult(
                "lifecycle_performance_ceiling_failures",
                sum(item.elapsed_ms > item.maximum_ms for item in lifecycle_performance),
            ),
            MetricResult(
                "lifecycle_peak_memory_ceiling_failures",
                sum(
                    item.peak_python_memory_bytes
                    > item.peak_python_memory_maximum_bytes
                    for item in lifecycle_performance
                ),
            ),
            MetricResult(
                "lifecycle_storage_size_ceiling_failures",
                sum(
                    item.final_storage_size_bytes
                    > item.final_storage_size_maximum_bytes
                    for item in lifecycle_performance
                ),
            ),
            MetricResult(
                "lifecycle_verification_failures",
                sum(not item.verified for item in lifecycle_performance),
            ),
        )
        return EvalReport(
            scenario_id=scenario.scenario_id,
            scenario_fingerprint=scenario.fingerprint,
            adapter_id=adapter_id,
            turn_count=len(scenario.turns),
            ordinary_turn_count=scenario.ordinary_turn_count,
            expected_event_count=scenario.expected_event_count,
            observed_event_count=observed_events,
            expected_growth_count=scenario.expected_growth_count,
            observed_growth_count=observed_growth,
            restart_count=len(faults.restart_after),
            retry_count=len(faults.retry_at),
            portability_target_adapter_id=portability.target_adapter_id,
            portability_export_count=portability.export_count,
            portability_import_count=portability.import_count,
            portability_duplicate_import_count=portability.duplicate_import_count,
            portability_observation_digest=portability.imported.normalized_digest,
            recall_probe_count=len(recall),
            recall_expected_match_count=recall_expected,
            recall_positive_match_count=recall_positive,
            recall_forbidden_match_count=recall_forbidden,
            performance_tier=performance_tier,
            performance_observations=performance,
            lifecycle_performance_observations=lifecycle_performance,
            peak_python_memory_bytes=peak_python_memory_bytes,
            peak_python_memory_maximum_bytes=peak_memory_maximum,
            final_storage_size_bytes=final_storage_size_bytes,
            final_storage_size_maximum_bytes=storage_size_maximum,
            final_observation_digest=final.normalized_digest,
            metrics=metrics,
        )

    @staticmethod
    def _projection_failures(scenario: Scenario) -> int:
        relationships = {item.key: item for item in scenario.relationships}
        failures = 0
        for number, probe in enumerate(scenario.projection_probes, 1):
            spec = relationships[probe.relationship_key]
            profile = RelationshipProfile(
                relationship_id=f"eval-relationship-{number}",
                persona_id=f"eval-persona-{number}",
                agent_identity_id=f"eval-agent-{number}",
                user_identity_id=f"eval-user-{number}",
                agent_id=spec.agent_id,
                user_id=spec.user_id,
                blueprint=CharacterBlueprint(
                    blueprint_id=f"eval-blueprint-{number}",
                    source_text=spec.persona_source,
                    created_at="2026-08-02T00:00:00+00:00",
                ),
                created_at="2026-08-02T00:00:00+00:00",
            )
            initial = RelationshipEvent(
                event_id=f"eval-belief-initial-{number}",
                relationship_id=profile.relationship_id,
                event_type=RelationshipEventType.OBSERVATION,
                content="Synthetic initial belief evidence.",
                belief_updates=(
                    BeliefUpdate(key=probe.belief_key, value=probe.initial_value),
                ),
                recorded_at="2026-08-02T00:00:01+00:00",
            )
            correction = RelationshipEvent(
                event_id=f"eval-belief-correction-{number}",
                relationship_id=profile.relationship_id,
                event_type=RelationshipEventType.CORRECTION,
                content="Synthetic correction evidence.",
                belief_updates=(
                    BeliefUpdate(key=probe.belief_key, value=probe.corrected_value),
                ),
                recorded_at="2026-08-02T00:00:02+00:00",
            )
            snapshot = RelationshipProjector.project(profile, (initial, correction))
            current = snapshot.beliefs.get(probe.belief_key)
            if (
                current is None
                or current.value != probe.corrected_value
                or current.evidence_event_id != correction.event_id
                or snapshot.event_count != 2
            ):
                failures += 1
        return failures


__all__ = [
    "AuthoritySpec",
    "EvalReport",
    "FaultSchedule",
    "FileStorageEvalAdapter",
    "GrowthSpec",
    "LongitudinalEvalRunner",
    "LongitudinalSystemAdapter",
    "ProjectionProbe",
    "RelationshipSpec",
    "SQLiteEvalAdapter",
    "Scenario",
    "TurnSpec",
]
