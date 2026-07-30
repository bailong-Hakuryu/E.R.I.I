"""Contracts for relationship processing, persona reflection, and consolidation.

The types in this module keep three authority levels separate:

* extraction decisions are untrusted, frozen proposals;
* accepted :class:`RelationshipEvent` objects remain authoritative history;
* reflections, episodes, and chapters are source-linked interpretations.
"""

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from typing import (
    Any,
    Dict,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
)

from erii.models.adjudication import (
    EvidenceReference,
    GrowthTriggerKind,
    PersonaGrowthProposal,
    PersonaGrowthStatus,
    RelationshipEventCandidate,
    SourceProcessingMode,
)
from erii.models.persona import PersonaManifest
from erii.models.provenance import ExtractorDescriptor
from erii.models.relationship import (
    CharacterBlueprint,
    RelationshipBaseline,
    RelationshipEvent,
    utc_now,
)
from erii.models.turn import InteractionContextSignal, SourceTranscript


def _require_text(value: object, field_name: str, *, maximum: int = 8192) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise ValueError(f"{field_name} exceeds its maximum length")
    return cleaned


def _optional_text(
    value: Optional[object],
    field_name: str,
    *,
    maximum: int = 8192,
) -> Optional[str]:
    if value is None:
        return None
    return _require_text(value, field_name, maximum=maximum)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_fingerprint(value: object, field_name: str) -> str:
    text = _require_text(value, field_name, maximum=64).lower()
    if len(text) != 64:
        raise ValueError(f"{field_name} must be a SHA-256 hexadecimal digest")
    try:
        int(text, 16)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be a SHA-256 hexadecimal digest"
        ) from exc
    return text


def _unique_texts(
    values: Sequence[object],
    field_name: str,
    *,
    maximum_items: int = 256,
) -> Tuple[str, ...]:
    if isinstance(values, (str, bytes)) or len(values) > maximum_items:
        raise ValueError(f"{field_name} must be a bounded sequence")
    result = tuple(
        _require_text(item, field_name, maximum=256) for item in values
    )
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


_AUTOMATIC_CANDIDATE_FIELDS = frozenset(
    {
        "candidate_key",
        "event_type",
        "summary",
        "signal",
        "temporal_payload",
        "evidence",
        "occurred_at",
        "occurrence_key",
        "references",
        "depends_on",
    }
)


def _automatic_candidate(
    value: Union[RelationshipEventCandidate, Mapping[str, Any]],
) -> RelationshipEventCandidate:
    if isinstance(value, RelationshipEventCandidate):
        if (
            value.persona_reflection is not None
            or value.growth_trigger != GrowthTriggerKind.NONE
        ):
            raise ValueError(
                "automatic relationship extraction cannot contain persona "
                "reflection or persona-growth intent"
            )
        return value
    if not isinstance(value, Mapping):
        raise ValueError("relationship candidates must be strict mappings")
    unknown = set(value).difference(_AUTOMATIC_CANDIDATE_FIELDS)
    if unknown:
        raise ValueError(
            "automatic relationship candidate contains forbidden or unknown fields"
        )
    candidate = RelationshipEventCandidate.model_validate(value)
    if (
        candidate.persona_reflection is not None
        or candidate.growth_trigger != GrowthTriggerKind.NONE
    ):
        raise ValueError(
            "automatic relationship extraction cannot contain persona "
            "reflection or persona-growth intent"
        )
    return candidate


def _candidate_to_dict(candidate: RelationshipEventCandidate) -> Dict[str, Any]:
    data = candidate.model_dump(mode="json")
    data.pop("persona_reflection", None)
    data.pop("growth_trigger", None)
    return data


@dataclass(frozen=True)
class RelationshipEventExtractionRequest:
    """Bounded canonical facts passed to a host relationship extractor."""

    source_turn_id: str
    source_revision: str
    relationship_id: str
    agent_id: str
    user_id: str
    transcript: SourceTranscript
    interaction_context: Tuple[InteractionContextSignal, ...] = ()
    prior_events: Tuple[RelationshipEvent, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "source_turn_id",
            "source_revision",
            "relationship_id",
            "agent_id",
            "user_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name, maximum=256),
            )
        transcript = self.transcript
        if not isinstance(transcript, SourceTranscript):
            transcript = SourceTranscript.from_dict(transcript)
            object.__setattr__(self, "transcript", transcript)
        context = tuple(
            item
            if isinstance(item, InteractionContextSignal)
            else InteractionContextSignal.from_dict(item)
            for item in self.interaction_context
        )
        if len(context) > 32:
            raise ValueError("interaction_context cannot exceed 32 signals")
        object.__setattr__(self, "interaction_context", context)
        events = tuple(
            item
            if isinstance(item, RelationshipEvent)
            else RelationshipEvent.from_dict(item)
            for item in self.prior_events
        )
        if len(events) > 32:
            raise ValueError("prior_events cannot exceed 32 events")
        if any(item.relationship_id != self.relationship_id for item in events):
            raise ValueError("prior event belongs to another relationship")
        object.__setattr__(self, "prior_events", events)


@dataclass(frozen=True)
class RelationshipEventCandidatesDecision:
    """A strict non-empty, persona-free extraction decision."""

    candidates: Tuple[RelationshipEventCandidate, ...]

    kind = "candidates"

    def __post_init__(self) -> None:
        candidates = tuple(_automatic_candidate(item) for item in self.candidates)
        if not 1 <= len(candidates) <= 32:
            raise ValueError("candidates decision requires between 1 and 32 candidates")
        keys = tuple(item.candidate_key for item in candidates)
        if len(keys) != len(set(keys)):
            raise ValueError("candidate_key must be unique within an extraction decision")
        object.__setattr__(self, "candidates", candidates)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "candidates": [_candidate_to_dict(item) for item in self.candidates],
        }


_NO_RELATIONSHIP_EVENT_REASONS = frozenset(
    {
        "duplicate_only",
        "ephemeral_coordination",
        "no_relationship_signal",
        "ordinary_acknowledgement",
        "ordinary_exchange",
    }
)


@dataclass(frozen=True)
class RelationshipNoEventDecision:
    """A successful extraction that explicitly proposes no relationship event."""

    reason_code: str

    kind = "no_relationship_event"

    def __post_init__(self) -> None:
        if self.reason_code not in _NO_RELATIONSHIP_EVENT_REASONS:
            raise ValueError("reason_code is not in the no-relationship-event allowlist")

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "reason_code": self.reason_code}


RelationshipEventExtractionDecision = Union[
    RelationshipEventCandidatesDecision,
    RelationshipNoEventDecision,
]


def relationship_extraction_decision_from_value(
    value: object,
) -> RelationshipEventExtractionDecision:
    """Strictly validates a host relationship extractor result."""
    if isinstance(
        value,
        (RelationshipEventCandidatesDecision, RelationshipNoEventDecision),
    ):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("relationship extractor result must be a discriminated mapping")
    kind = value.get("kind")
    if kind == RelationshipEventCandidatesDecision.kind:
        if set(value) != {"kind", "candidates"}:
            raise ValueError("candidates decision contains unknown or missing fields")
        return RelationshipEventCandidatesDecision(
            candidates=tuple(value["candidates"]),
        )
    if kind == RelationshipNoEventDecision.kind:
        if set(value) != {"kind", "reason_code"}:
            raise ValueError(
                "no_relationship_event decision contains unknown or missing fields"
            )
        return RelationshipNoEventDecision(reason_code=value["reason_code"])
    raise ValueError(
        "relationship extractor result requires "
        "kind=candidates|no_relationship_event"
    )


class RelationshipEventExtractorV1(Protocol):
    """Host capability that proposes facts and never writes kernel state."""

    descriptor: ExtractorDescriptor

    def extract(
        self,
        request: RelationshipEventExtractionRequest,
    ) -> RelationshipEventExtractionDecision:
        """Returns one strict extraction decision."""


class RelationshipProcessingStatus(str, Enum):
    """Durable phase of one frozen Source Turn relationship run."""

    EXTRACTED = "extracted"
    ADJUDICATED = "adjudicated"
    REFLECTION_PENDING = "reflection_pending"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"


class RelationshipProcessingOutcome(str, Enum):
    """Stable user-facing meaning of a relationship processing run."""

    PENDING = "pending"
    EVENTS_ACCEPTED = "events_accepted"
    NO_RELATIONSHIP_EVENT = "no_relationship_event"
    NO_ACCEPTED_EVENTS = "no_accepted_events"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"


class RelationshipProcessingConflictError(ValueError):
    """A frozen relationship processing identity was reused inconsistently."""


@dataclass(frozen=True)
class RelationshipProcessingRun:
    """Durable source-level run that freezes extraction before adjudication."""

    processing_id: str
    relationship_id: str
    source_turn_id: str
    source_revision: str
    processing_mode: SourceProcessingMode
    status: RelationshipProcessingStatus
    outcome: RelationshipProcessingOutcome
    extractor_descriptor: ExtractorDescriptor
    frozen_decision: RelationshipEventExtractionDecision
    adjudication_base_direct_event_count: int = 0
    adjudication_base_decision_count: int = 0
    adjudication_base_fingerprint: Optional[str] = None
    reflection_planned: bool = False
    decision_ids: Tuple[str, ...] = ()
    event_ids: Tuple[str, ...] = ()
    reflection_outcome_ids: Tuple[str, ...] = ()
    reflection_failure_event_ids: Tuple[str, ...] = ()
    reprocessing_id: Optional[str] = None
    batch_fingerprint: Optional[str] = None
    rule_version: str = "relationship-adjudication-v1"
    contract_version: str = "relationship-processing-v1"
    record_version: int = 1
    safe_failure_code: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    completed_at: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in (
            "processing_id",
            "relationship_id",
            "source_turn_id",
            "source_revision",
            "rule_version",
            "contract_version",
            "created_at",
            "updated_at",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name, maximum=256),
            )
        mode = self.processing_mode
        if not isinstance(mode, SourceProcessingMode):
            mode = SourceProcessingMode(mode)
            object.__setattr__(self, "processing_mode", mode)
        status = self.status
        if not isinstance(status, RelationshipProcessingStatus):
            status = RelationshipProcessingStatus(status)
            object.__setattr__(self, "status", status)
        outcome = self.outcome
        if not isinstance(outcome, RelationshipProcessingOutcome):
            outcome = RelationshipProcessingOutcome(outcome)
            object.__setattr__(self, "outcome", outcome)
        descriptor = self.extractor_descriptor
        if not isinstance(descriptor, ExtractorDescriptor):
            descriptor = ExtractorDescriptor.from_dict(descriptor)
            object.__setattr__(self, "extractor_descriptor", descriptor)
        decision = relationship_extraction_decision_from_value(self.frozen_decision)
        object.__setattr__(self, "frozen_decision", decision)
        for field_name in (
            "adjudication_base_direct_event_count",
            "adjudication_base_decision_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.adjudication_base_fingerprint is not None:
            object.__setattr__(
                self,
                "adjudication_base_fingerprint",
                _require_fingerprint(
                    self.adjudication_base_fingerprint,
                    "adjudication_base_fingerprint",
                ),
            )
        if not isinstance(self.reflection_planned, bool):
            raise ValueError("reflection_planned must be boolean")
        expected_fingerprint = _fingerprint(decision.to_dict())
        if self.batch_fingerprint is None:
            object.__setattr__(self, "batch_fingerprint", expected_fingerprint)
        elif _require_fingerprint(
            self.batch_fingerprint,
            "batch_fingerprint",
        ) != expected_fingerprint:
            raise ValueError("batch_fingerprint does not match frozen_decision")
        else:
            object.__setattr__(
                self,
                "batch_fingerprint",
                expected_fingerprint,
            )
        for field_name in (
            "decision_ids",
            "event_ids",
            "reflection_outcome_ids",
            "reflection_failure_event_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _unique_texts(getattr(self, field_name), field_name),
            )
        if not set(self.reflection_failure_event_ids).issubset(self.event_ids):
            raise ValueError("reflection failures must reference accepted run events")
        if (
            len(self.reflection_outcome_ids)
            + len(self.reflection_failure_event_ids)
            > len(self.event_ids)
        ):
            raise ValueError(
                "reflection results cannot outnumber accepted run events"
            )
        if not self.reflection_planned and (
            self.reflection_outcome_ids
            or self.reflection_failure_event_ids
            or status
            in {
                RelationshipProcessingStatus.REFLECTION_PENDING,
                RelationshipProcessingStatus.PARTIAL_FAILED,
            }
        ):
            raise ValueError(
                "reflection outcomes and states require reflection_planned"
            )
        if isinstance(self.record_version, bool) or self.record_version < 1:
            raise ValueError("record_version must be a positive integer")
        object.__setattr__(
            self,
            "reprocessing_id",
            _optional_text(
                self.reprocessing_id,
                "reprocessing_id",
                maximum=256,
            ),
        )
        if mode == SourceProcessingMode.HISTORICAL_REPROCESSING:
            if self.reprocessing_id is None:
                raise ValueError("historical processing requires reprocessing_id")
        elif self.reprocessing_id is not None:
            raise ValueError("normal processing cannot contain reprocessing_id")
        object.__setattr__(
            self,
            "safe_failure_code",
            _optional_text(
                self.safe_failure_code,
                "safe_failure_code",
                maximum=128,
            ),
        )
        if status in (
            RelationshipProcessingStatus.COMPLETED,
            RelationshipProcessingStatus.PARTIAL_FAILED,
            RelationshipProcessingStatus.FAILED,
        ):
            if self.completed_at is None:
                raise ValueError("terminal relationship processing requires completed_at")
            object.__setattr__(
                self,
                "completed_at",
                _require_text(self.completed_at, "completed_at", maximum=256),
            )
            if outcome == RelationshipProcessingOutcome.PENDING:
                raise ValueError("terminal relationship processing cannot remain pending")
        elif self.completed_at is not None:
            raise ValueError("non-terminal relationship processing cannot be completed")
        if isinstance(decision, RelationshipNoEventDecision):
            if self.decision_ids or self.event_ids:
                raise ValueError("no-event processing cannot contain decisions or events")
            if self.reflection_planned:
                raise ValueError("no-event processing cannot plan persona reflection")
            if (
                status != RelationshipProcessingStatus.COMPLETED
                or outcome
                != RelationshipProcessingOutcome.NO_RELATIONSHIP_EVENT
            ):
                raise ValueError(
                    "no-event processing must be completed with the "
                    "no_relationship_event outcome"
                )
            return

        if status == RelationshipProcessingStatus.EXTRACTED:
            if (
                outcome != RelationshipProcessingOutcome.PENDING
                or self.decision_ids
                or self.event_ids
                or self.reflection_outcome_ids
                or self.reflection_failure_event_ids
                or self.safe_failure_code is not None
            ):
                raise ValueError(
                    "extracted processing must remain pending without derived results"
                )
        elif status == RelationshipProcessingStatus.ADJUDICATED:
            if (
                outcome != RelationshipProcessingOutcome.PENDING
                or not self.decision_ids
                or self.reflection_outcome_ids
                or self.reflection_failure_event_ids
                or self.safe_failure_code is not None
            ):
                raise ValueError(
                    "adjudicated processing requires decisions and a pending outcome"
                )
        elif status == RelationshipProcessingStatus.REFLECTION_PENDING:
            if (
                outcome != RelationshipProcessingOutcome.PENDING
                or not self.decision_ids
                or not self.event_ids
                or not self.reflection_planned
                or self.reflection_failure_event_ids
                or self.safe_failure_code is not None
            ):
                raise ValueError(
                    "reflection-pending processing requires accepted events "
                    "without a terminal failure"
                )
        elif status == RelationshipProcessingStatus.COMPLETED:
            if self.safe_failure_code is not None or self.reflection_failure_event_ids:
                raise ValueError(
                    "completed processing cannot retain a failure outcome"
                )
            if self.event_ids:
                if (
                    outcome != RelationshipProcessingOutcome.EVENTS_ACCEPTED
                    or not self.decision_ids
                    or (
                        self.reflection_planned
                        and len(self.reflection_outcome_ids)
                        != len(self.event_ids)
                    )
                    or (
                        not self.reflection_planned
                        and self.reflection_outcome_ids
                    )
                ):
                    raise ValueError(
                        "completed accepted-event processing has inconsistent results"
                    )
            elif (
                outcome != RelationshipProcessingOutcome.NO_ACCEPTED_EVENTS
                or not self.decision_ids
                or self.reflection_outcome_ids
            ):
                raise ValueError(
                    "completed zero-event processing has inconsistent results"
                )
        elif status == RelationshipProcessingStatus.PARTIAL_FAILED:
            if (
                outcome != RelationshipProcessingOutcome.PARTIAL_FAILED
                or not self.decision_ids
                or not self.event_ids
                or not self.reflection_planned
                or not self.reflection_failure_event_ids
                or self.safe_failure_code != "persona_reflection_failed"
            ):
                raise ValueError(
                    "partially failed processing has inconsistent reflection results"
                )
        elif (
            outcome != RelationshipProcessingOutcome.FAILED
            or self.decision_ids
            or self.event_ids
            or self.reflection_outcome_ids
            or self.reflection_failure_event_ids
            or self.safe_failure_code != "relationship_adjudication_failed"
        ):
            raise ValueError(
                "failed processing must contain only an adjudication failure"
            )

    @property
    def processing_identity(self) -> str:
        return f"{self.processing_mode.value}:{self.reprocessing_id or ''}"

    def same_frozen_input_as(self, other: "RelationshipProcessingRun") -> bool:
        """Compares immutable run identity and the frozen extractor decision."""
        return (
            self.processing_id == other.processing_id
            and self.relationship_id == other.relationship_id
            and self.source_turn_id == other.source_turn_id
            and self.source_revision == other.source_revision
            and self.processing_identity == other.processing_identity
            and self.extractor_descriptor == other.extractor_descriptor
            and self.batch_fingerprint == other.batch_fingerprint
            and self.adjudication_base_direct_event_count
            == other.adjudication_base_direct_event_count
            and self.adjudication_base_decision_count
            == other.adjudication_base_decision_count
            and self.adjudication_base_fingerprint
            == other.adjudication_base_fingerprint
            and self.reflection_planned == other.reflection_planned
            and self.rule_version == other.rule_version
            and self.contract_version == other.contract_version
            and self.created_at == other.created_at
        )

    def advance(self, **changes: Any) -> "RelationshipProcessingRun":
        """Returns the next CAS revision while preserving frozen input."""
        changes.setdefault("record_version", self.record_version + 1)
        changes.setdefault("updated_at", utc_now())
        advanced = replace(self, **changes)
        if not self.same_frozen_input_as(advanced):
            raise RelationshipProcessingConflictError(
                "relationship processing frozen input is immutable"
            )
        return advanced

    def to_dict(self) -> Dict[str, Any]:
        return {
            "processing_id": self.processing_id,
            "relationship_id": self.relationship_id,
            "source_turn_id": self.source_turn_id,
            "source_revision": self.source_revision,
            "processing_mode": self.processing_mode.value,
            "reprocessing_id": self.reprocessing_id,
            "status": self.status.value,
            "outcome": self.outcome.value,
            "extractor_descriptor": self.extractor_descriptor.to_dict(),
            "frozen_decision": self.frozen_decision.to_dict(),
            "adjudication_base_direct_event_count": (
                self.adjudication_base_direct_event_count
            ),
            "adjudication_base_decision_count": (
                self.adjudication_base_decision_count
            ),
            "adjudication_base_fingerprint": (
                self.adjudication_base_fingerprint
            ),
            "reflection_planned": self.reflection_planned,
            "batch_fingerprint": self.batch_fingerprint,
            "decision_ids": list(self.decision_ids),
            "event_ids": list(self.event_ids),
            "reflection_outcome_ids": list(self.reflection_outcome_ids),
            "reflection_failure_event_ids": list(
                self.reflection_failure_event_ids
            ),
            "rule_version": self.rule_version,
            "contract_version": self.contract_version,
            "record_version": self.record_version,
            "safe_failure_code": self.safe_failure_code,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationshipProcessingRun":
        return cls(
            processing_id=str(data["processing_id"]),
            relationship_id=str(data["relationship_id"]),
            source_turn_id=str(data["source_turn_id"]),
            source_revision=str(data["source_revision"]),
            processing_mode=SourceProcessingMode(str(data["processing_mode"])),
            reprocessing_id=data.get("reprocessing_id"),
            status=RelationshipProcessingStatus(str(data["status"])),
            outcome=RelationshipProcessingOutcome(str(data["outcome"])),
            extractor_descriptor=ExtractorDescriptor.from_dict(
                data["extractor_descriptor"]
            ),
            frozen_decision=relationship_extraction_decision_from_value(
                data["frozen_decision"]
            ),
            adjudication_base_direct_event_count=int(
                data.get("adjudication_base_direct_event_count", 0)
            ),
            adjudication_base_decision_count=int(
                data.get("adjudication_base_decision_count", 0)
            ),
            adjudication_base_fingerprint=data.get(
                "adjudication_base_fingerprint"
            ),
            reflection_planned=data.get("reflection_planned", False),
            batch_fingerprint=data.get("batch_fingerprint"),
            decision_ids=tuple(data.get("decision_ids", ())),
            event_ids=tuple(data.get("event_ids", ())),
            reflection_outcome_ids=tuple(
                data.get("reflection_outcome_ids", ())
            ),
            reflection_failure_event_ids=tuple(
                data.get("reflection_failure_event_ids", ())
            ),
            rule_version=str(
                data.get("rule_version", "relationship-adjudication-v1")
            ),
            contract_version=str(
                data.get("contract_version", "relationship-processing-v1")
            ),
            record_version=int(data.get("record_version", 1)),
            safe_failure_code=data.get("safe_failure_code"),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            completed_at=data.get("completed_at"),
        )


@dataclass(frozen=True)
class ReflectionInterpreterDescriptor:
    """Non-sensitive identity of a host persona-reflection interpreter."""

    interpreter_id: str
    interpreter_version: str
    interpretation_schema_version: str = "1"

    def __post_init__(self) -> None:
        for field_name in (
            "interpreter_id",
            "interpreter_version",
            "interpretation_schema_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name, maximum=128),
            )

    def to_dict(self) -> Dict[str, str]:
        return {
            "interpreter_id": self.interpreter_id,
            "interpreter_version": self.interpreter_version,
            "interpretation_schema_version": self.interpretation_schema_version,
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "ReflectionInterpreterDescriptor":
        if set(data) != {
            "interpreter_id",
            "interpreter_version",
            "interpretation_schema_version",
        }:
            raise ValueError(
                "ReflectionInterpreterDescriptor contains unknown or missing fields"
            )
        return cls(
            interpreter_id=data["interpreter_id"],
            interpreter_version=data["interpreter_version"],
            interpretation_schema_version=data["interpretation_schema_version"],
        )


class ReflectionProvenanceState(str, Enum):
    """Whether a reflection has complete modern context provenance."""

    COMPLETE = "complete"
    LEGACY_UNAVAILABLE = "legacy_unavailable"


@dataclass(frozen=True)
class ApprovedGrowthReference:
    """Immutable pointer to growth that was approved when reflection occurred."""

    proposal_id: str
    revision: int
    content_fingerprint: str
    approved_at: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proposal_id",
            _require_text(self.proposal_id, "proposal_id", maximum=256),
        )
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("growth revision must be a positive integer")
        object.__setattr__(
            self,
            "content_fingerprint",
            _require_fingerprint(
                self.content_fingerprint,
                "content_fingerprint",
            ),
        )
        object.__setattr__(
            self,
            "approved_at",
            _optional_text(self.approved_at, "approved_at", maximum=256),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "revision": self.revision,
            "content_fingerprint": self.content_fingerprint,
            "approved_at": self.approved_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ApprovedGrowthReference":
        return cls(
            proposal_id=str(data["proposal_id"]),
            revision=int(data["revision"]),
            content_fingerprint=str(data["content_fingerprint"]),
            approved_at=data.get("approved_at"),
        )


@dataclass(frozen=True)
class ReflectionContextProvenance:
    """Minimal immutable IDs and hashes describing reflection-time context."""

    relationship_event_id: str
    source_turn_id: Optional[str] = None
    source_revision: Optional[str] = None
    decision_id: Optional[str] = None
    evidence_ids: Tuple[str, ...] = ()
    blueprint_id: Optional[str] = None
    blueprint_sha256: Optional[str] = None
    blueprint_revision: Optional[int] = None
    manifest_id: Optional[str] = None
    manifest_revision: Optional[int] = None
    manifest_fingerprint: Optional[str] = None
    baseline_fingerprint: Optional[str] = None
    approved_growth: Tuple[ApprovedGrowthReference, ...] = ()
    prior_event_ids: Tuple[str, ...] = ()
    prior_reflection_ids: Tuple[str, ...] = ()
    provenance_state: ReflectionProvenanceState = (
        ReflectionProvenanceState.COMPLETE
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relationship_event_id",
            _require_text(
                self.relationship_event_id,
                "relationship_event_id",
                maximum=256,
            ),
        )
        state = self.provenance_state
        if not isinstance(state, ReflectionProvenanceState):
            state = ReflectionProvenanceState(state)
            object.__setattr__(self, "provenance_state", state)
        for field_name in (
            "source_turn_id",
            "source_revision",
            "decision_id",
            "blueprint_id",
            "manifest_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_text(
                    getattr(self, field_name),
                    field_name,
                    maximum=256,
                ),
            )
        for field_name in (
            "evidence_ids",
            "prior_event_ids",
            "prior_reflection_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _unique_texts(getattr(self, field_name), field_name),
            )
        growth = tuple(
            item
            if isinstance(item, ApprovedGrowthReference)
            else ApprovedGrowthReference.from_dict(item)
            for item in self.approved_growth
        )
        growth_ids = tuple((item.proposal_id, item.revision) for item in growth)
        if len(growth_ids) != len(set(growth_ids)):
            raise ValueError("approved_growth references must be unique")
        object.__setattr__(self, "approved_growth", growth)
        for field_name in (
            "blueprint_sha256",
            "manifest_fingerprint",
            "baseline_fingerprint",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _require_fingerprint(value, field_name),
                )
        for field_name in ("blueprint_revision", "manifest_revision"):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise ValueError(f"{field_name} must be a positive integer")
        manifest_values = (
            self.manifest_id,
            self.manifest_revision,
            self.manifest_fingerprint,
        )
        if any(value is not None for value in manifest_values) and not all(
            value is not None for value in manifest_values
        ):
            raise ValueError("manifest provenance must be all present or all absent")
        if state == ReflectionProvenanceState.COMPLETE:
            required = (
                self.source_turn_id,
                self.source_revision,
                self.decision_id,
                self.blueprint_id,
                self.blueprint_sha256,
                self.blueprint_revision,
                self.baseline_fingerprint,
            )
            if any(value is None for value in required) or not self.evidence_ids:
                raise ValueError(
                    "complete reflection provenance lacks required source evidence"
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provenance_state": self.provenance_state.value,
            "source_turn_id": self.source_turn_id,
            "source_revision": self.source_revision,
            "decision_id": self.decision_id,
            "evidence_ids": list(self.evidence_ids),
            "relationship_event_id": self.relationship_event_id,
            "blueprint_id": self.blueprint_id,
            "blueprint_sha256": self.blueprint_sha256,
            "blueprint_revision": self.blueprint_revision,
            "manifest_id": self.manifest_id,
            "manifest_revision": self.manifest_revision,
            "manifest_fingerprint": self.manifest_fingerprint,
            "baseline_fingerprint": self.baseline_fingerprint,
            "approved_growth": [
                item.to_dict() for item in self.approved_growth
            ],
            "prior_event_ids": list(self.prior_event_ids),
            "prior_reflection_ids": list(self.prior_reflection_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReflectionContextProvenance":
        return cls(
            provenance_state=ReflectionProvenanceState(
                str(data.get("provenance_state", "complete"))
            ),
            source_turn_id=data.get("source_turn_id"),
            source_revision=data.get("source_revision"),
            decision_id=data.get("decision_id"),
            evidence_ids=tuple(data.get("evidence_ids", ())),
            relationship_event_id=str(data["relationship_event_id"]),
            blueprint_id=data.get("blueprint_id"),
            blueprint_sha256=data.get("blueprint_sha256"),
            blueprint_revision=(
                int(data["blueprint_revision"])
                if data.get("blueprint_revision") is not None
                else None
            ),
            manifest_id=data.get("manifest_id"),
            manifest_revision=(
                int(data["manifest_revision"])
                if data.get("manifest_revision") is not None
                else None
            ),
            manifest_fingerprint=data.get("manifest_fingerprint"),
            baseline_fingerprint=data.get("baseline_fingerprint"),
            approved_growth=tuple(
                ApprovedGrowthReference.from_dict(item)
                for item in data.get("approved_growth", ())
            ),
            prior_event_ids=tuple(data.get("prior_event_ids", ())),
            prior_reflection_ids=tuple(
                data.get("prior_reflection_ids", ())
            ),
        )


class PersonaReflectionRecordKind(str, Enum):
    """Append-only meanings supported by persona reflection history."""

    REFLECTION = "reflection"
    CORRECTION = "correction"
    REINTERPRETATION = "reinterpretation"
    LEGACY = "legacy"


_EMOTIONAL_INTENSITIES = frozenset({"weak", "moderate", "strong"})


@dataclass(frozen=True)
class PersonaReflectionContentDecision:
    """A bounded first-person reflection proposed by an interpreter."""

    content: str
    emotional_direction: str
    emotional_intensity: str
    core_meaning: str

    kind = "reflection"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "content",
            _require_text(self.content, "reflection content", maximum=4000),
        )
        object.__setattr__(
            self,
            "emotional_direction",
            _require_text(
                self.emotional_direction,
                "emotional_direction",
                maximum=128,
            ),
        )
        if self.emotional_intensity not in _EMOTIONAL_INTENSITIES:
            raise ValueError("emotional_intensity must be weak, moderate, or strong")
        object.__setattr__(
            self,
            "core_meaning",
            _require_text(self.core_meaning, "core_meaning", maximum=4000),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "kind": self.kind,
            "content": self.content,
            "emotional_direction": self.emotional_direction,
            "emotional_intensity": self.emotional_intensity,
            "core_meaning": self.core_meaning,
        }


_NO_REFLECTION_REASONS = frozenset(
    {
        "insufficient_persona_basis",
        "no_distinct_inner_response",
        "ordinary_event",
        "reflection_not_needed",
    }
)


@dataclass(frozen=True)
class PersonaNoReflectionDecision:
    """A successful interpreter decision that creates no reflection record."""

    reason_code: str

    kind = "no_reflection"

    def __post_init__(self) -> None:
        if self.reason_code not in _NO_REFLECTION_REASONS:
            raise ValueError("reason_code is not in the no-reflection allowlist")

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "reason_code": self.reason_code}


PersonaReflectionDecision = Union[
    PersonaReflectionContentDecision,
    PersonaNoReflectionDecision,
]


def persona_reflection_decision_from_value(
    value: object,
) -> PersonaReflectionDecision:
    """Strictly validates an interpreter decision."""
    if isinstance(
        value,
        (PersonaReflectionContentDecision, PersonaNoReflectionDecision),
    ):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("reflection interpreter result must be a mapping")
    kind = value.get("kind")
    if kind == PersonaReflectionContentDecision.kind:
        expected = {
            "kind",
            "content",
            "emotional_direction",
            "emotional_intensity",
            "core_meaning",
        }
        if set(value) != expected:
            raise ValueError("reflection decision contains unknown or missing fields")
        return PersonaReflectionContentDecision(
            content=value["content"],
            emotional_direction=value["emotional_direction"],
            emotional_intensity=value["emotional_intensity"],
            core_meaning=value["core_meaning"],
        )
    if kind == PersonaNoReflectionDecision.kind:
        if set(value) != {"kind", "reason_code"}:
            raise ValueError(
                "no_reflection decision contains unknown or missing fields"
            )
        return PersonaNoReflectionDecision(reason_code=value["reason_code"])
    raise ValueError(
        "reflection interpreter result requires kind=reflection|no_reflection"
    )


@dataclass(frozen=True)
class PersonaReflectionRecord:
    """One immutable first-person interpretation in a relationship history."""

    reflection_id: str
    relationship_id: str
    event_id: str
    record_kind: PersonaReflectionRecordKind
    content: str
    emotional_direction: str
    emotional_intensity: str
    core_meaning: str
    interpreter_descriptor: ReflectionInterpreterDescriptor
    context_provenance: ReflectionContextProvenance
    target_reflection_id: Optional[str] = None
    recorded_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for field_name in (
            "reflection_id",
            "relationship_id",
            "event_id",
            "recorded_at",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name, maximum=256),
            )
        kind = self.record_kind
        if not isinstance(kind, PersonaReflectionRecordKind):
            kind = PersonaReflectionRecordKind(kind)
            object.__setattr__(self, "record_kind", kind)
        content_decision = PersonaReflectionContentDecision(
            content=self.content,
            emotional_direction=self.emotional_direction,
            emotional_intensity=self.emotional_intensity,
            core_meaning=self.core_meaning,
        )
        object.__setattr__(self, "content", content_decision.content)
        object.__setattr__(
            self,
            "emotional_direction",
            content_decision.emotional_direction,
        )
        object.__setattr__(
            self,
            "emotional_intensity",
            content_decision.emotional_intensity,
        )
        object.__setattr__(
            self,
            "core_meaning",
            content_decision.core_meaning,
        )
        descriptor = self.interpreter_descriptor
        if not isinstance(descriptor, ReflectionInterpreterDescriptor):
            descriptor = ReflectionInterpreterDescriptor.from_dict(descriptor)
            object.__setattr__(self, "interpreter_descriptor", descriptor)
        provenance = self.context_provenance
        if not isinstance(provenance, ReflectionContextProvenance):
            provenance = ReflectionContextProvenance.from_dict(provenance)
            object.__setattr__(self, "context_provenance", provenance)
        if provenance.relationship_event_id != self.event_id:
            raise ValueError("reflection provenance references a different event")
        target = _optional_text(
            self.target_reflection_id,
            "target_reflection_id",
            maximum=256,
        )
        object.__setattr__(self, "target_reflection_id", target)
        if kind in (
            PersonaReflectionRecordKind.CORRECTION,
            PersonaReflectionRecordKind.REINTERPRETATION,
        ):
            if target is None:
                raise ValueError(
                    "correction and reinterpretation require target_reflection_id"
                )
        elif target is not None:
            raise ValueError(
                "original and legacy reflections cannot target another reflection"
            )
        if kind == PersonaReflectionRecordKind.LEGACY and (
            provenance.provenance_state
            != ReflectionProvenanceState.LEGACY_UNAVAILABLE
        ):
            raise ValueError("legacy reflection requires legacy_unavailable provenance")
        if (
            kind != PersonaReflectionRecordKind.LEGACY
            and provenance.provenance_state
            != ReflectionProvenanceState.COMPLETE
        ):
            raise ValueError(
                "formal reflection requires complete source provenance"
            )

    @property
    def content_fingerprint(self) -> str:
        return _fingerprint(
            {
                "relationship_id": self.relationship_id,
                "event_id": self.event_id,
                "record_kind": self.record_kind.value,
                "target_reflection_id": self.target_reflection_id,
                "content": self.content,
                "emotional_direction": self.emotional_direction,
                "emotional_intensity": self.emotional_intensity,
                "core_meaning": self.core_meaning,
                "interpreter_descriptor": self.interpreter_descriptor.to_dict(),
                "context_provenance": self.context_provenance.to_dict(),
            }
        )

    def same_payload_as(self, other: "PersonaReflectionRecord") -> bool:
        return (
            self.reflection_id == other.reflection_id
            and self.content_fingerprint == other.content_fingerprint
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reflection_id": self.reflection_id,
            "relationship_id": self.relationship_id,
            "event_id": self.event_id,
            "record_kind": self.record_kind.value,
            "target_reflection_id": self.target_reflection_id,
            "content": self.content,
            "emotional_direction": self.emotional_direction,
            "emotional_intensity": self.emotional_intensity,
            "core_meaning": self.core_meaning,
            "content_fingerprint": self.content_fingerprint,
            "interpreter_descriptor": self.interpreter_descriptor.to_dict(),
            "context_provenance": self.context_provenance.to_dict(),
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersonaReflectionRecord":
        record = cls(
            reflection_id=str(data["reflection_id"]),
            relationship_id=str(data["relationship_id"]),
            event_id=str(data["event_id"]),
            record_kind=PersonaReflectionRecordKind(str(data["record_kind"])),
            target_reflection_id=data.get("target_reflection_id"),
            content=str(data["content"]),
            emotional_direction=str(data["emotional_direction"]),
            emotional_intensity=str(data["emotional_intensity"]),
            core_meaning=str(data["core_meaning"]),
            interpreter_descriptor=ReflectionInterpreterDescriptor.from_dict(
                data["interpreter_descriptor"]
            ),
            context_provenance=ReflectionContextProvenance.from_dict(
                data["context_provenance"]
            ),
            recorded_at=str(data["recorded_at"]),
        )
        supplied = data.get("content_fingerprint")
        if supplied is not None and _require_fingerprint(
            supplied,
            "content_fingerprint",
        ) != record.content_fingerprint:
            raise ValueError("reflection content_fingerprint is invalid")
        return record


@dataclass(frozen=True)
class PersonaReflectionDecisionRecord:
    """Durable terminal reflection/no-reflection result for one identity."""

    decision_id: str
    relationship_id: str
    event_id: str
    source_turn_id: str
    source_revision: str
    interpreter_descriptor: ReflectionInterpreterDescriptor
    decision: PersonaReflectionDecision
    context_provenance: ReflectionContextProvenance
    record_kind: PersonaReflectionRecordKind = (
        PersonaReflectionRecordKind.REFLECTION
    )
    target_reflection_id: Optional[str] = None
    interpretation_id: Optional[str] = None
    reflection_record: Optional[PersonaReflectionRecord] = None
    recorded_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for field_name in (
            "decision_id",
            "relationship_id",
            "event_id",
            "source_turn_id",
            "source_revision",
            "recorded_at",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name, maximum=256),
            )
        descriptor = self.interpreter_descriptor
        if not isinstance(descriptor, ReflectionInterpreterDescriptor):
            descriptor = ReflectionInterpreterDescriptor.from_dict(descriptor)
            object.__setattr__(self, "interpreter_descriptor", descriptor)
        decision = persona_reflection_decision_from_value(self.decision)
        object.__setattr__(self, "decision", decision)
        provenance = self.context_provenance
        if not isinstance(provenance, ReflectionContextProvenance):
            provenance = ReflectionContextProvenance.from_dict(provenance)
            object.__setattr__(self, "context_provenance", provenance)
        if provenance.relationship_event_id != self.event_id:
            raise ValueError("reflection decision provenance references another event")
        kind = self.record_kind
        if not isinstance(kind, PersonaReflectionRecordKind):
            kind = PersonaReflectionRecordKind(kind)
            object.__setattr__(self, "record_kind", kind)
        target = _optional_text(
            self.target_reflection_id,
            "target_reflection_id",
            maximum=256,
        )
        object.__setattr__(self, "target_reflection_id", target)
        interpretation_id = _optional_text(
            self.interpretation_id,
            "interpretation_id",
            maximum=256,
        )
        object.__setattr__(self, "interpretation_id", interpretation_id)
        if kind in (
            PersonaReflectionRecordKind.CORRECTION,
            PersonaReflectionRecordKind.REINTERPRETATION,
        ):
            if target is None:
                raise ValueError(
                    "correction and reinterpretation require target_reflection_id"
                )
            if interpretation_id is None:
                raise ValueError(
                    "correction and reinterpretation require interpretation_id"
                )
        elif target is not None:
            raise ValueError("an original reflection outcome cannot have a target")
        elif interpretation_id is not None:
            raise ValueError(
                "an original reflection outcome cannot have interpretation_id"
            )
        record = self.reflection_record
        if record is not None and not isinstance(record, PersonaReflectionRecord):
            record = PersonaReflectionRecord.from_dict(record)
            object.__setattr__(self, "reflection_record", record)
        if isinstance(decision, PersonaReflectionContentDecision):
            if record is None:
                raise ValueError("reflection decision requires a formal reflection record")
            if (
                record.relationship_id != self.relationship_id
                or record.event_id != self.event_id
                or record.record_kind != kind
                or record.target_reflection_id != target
                or record.context_provenance != provenance
                or record.interpreter_descriptor != descriptor
                or record.content != decision.content
                or record.emotional_direction != decision.emotional_direction
                or record.emotional_intensity != decision.emotional_intensity
                or record.core_meaning != decision.core_meaning
            ):
                raise ValueError("reflection decision and record do not match")
        elif record is not None:
            raise ValueError("no_reflection outcome cannot create a placeholder record")
        if kind == PersonaReflectionRecordKind.LEGACY:
            if (
                provenance.provenance_state
                != ReflectionProvenanceState.LEGACY_UNAVAILABLE
            ):
                raise ValueError(
                    "legacy reflection decision requires legacy_unavailable provenance"
                )
        elif provenance.provenance_state != ReflectionProvenanceState.COMPLETE:
            raise ValueError(
                "formal reflection decision requires complete source provenance"
            )

    @property
    def interpretation_identity(self) -> str:
        return _fingerprint(
            {
                "relationship_id": self.relationship_id,
                "event_id": self.event_id,
                "record_kind": self.record_kind.value,
                "target_reflection_id": self.target_reflection_id,
                "interpretation_id": self.interpretation_id,
            }
        )

    def same_payload_as(self, other: "PersonaReflectionDecisionRecord") -> bool:
        own = self.to_dict()
        other_data = other.to_dict()
        own.pop("recorded_at", None)
        other_data.pop("recorded_at", None)
        if own.get("reflection_record") is not None:
            own["reflection_record"].pop("recorded_at", None)
        if other_data.get("reflection_record") is not None:
            other_data["reflection_record"].pop("recorded_at", None)
        return own == other_data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "relationship_id": self.relationship_id,
            "event_id": self.event_id,
            "source_turn_id": self.source_turn_id,
            "source_revision": self.source_revision,
            "record_kind": self.record_kind.value,
            "target_reflection_id": self.target_reflection_id,
            "interpretation_id": self.interpretation_id,
            "interpretation_identity": self.interpretation_identity,
            "interpreter_descriptor": self.interpreter_descriptor.to_dict(),
            "decision": self.decision.to_dict(),
            "context_provenance": self.context_provenance.to_dict(),
            "reflection_record": (
                self.reflection_record.to_dict()
                if self.reflection_record is not None
                else None
            ),
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "PersonaReflectionDecisionRecord":
        raw_record = data.get("reflection_record")
        result = cls(
            decision_id=str(data["decision_id"]),
            relationship_id=str(data["relationship_id"]),
            event_id=str(data["event_id"]),
            source_turn_id=str(data["source_turn_id"]),
            source_revision=str(data["source_revision"]),
            record_kind=PersonaReflectionRecordKind(
                str(data.get("record_kind", "reflection"))
            ),
            target_reflection_id=data.get("target_reflection_id"),
            interpretation_id=data.get("interpretation_id"),
            interpreter_descriptor=ReflectionInterpreterDescriptor.from_dict(
                data["interpreter_descriptor"]
            ),
            decision=persona_reflection_decision_from_value(data["decision"]),
            context_provenance=ReflectionContextProvenance.from_dict(
                data["context_provenance"]
            ),
            reflection_record=(
                PersonaReflectionRecord.from_dict(raw_record)
                if raw_record is not None
                else None
            ),
            recorded_at=str(data["recorded_at"]),
        )
        supplied_identity = data.get("interpretation_identity")
        if (
            supplied_identity is not None
            and _require_fingerprint(
                supplied_identity,
                "interpretation_identity",
            )
            != result.interpretation_identity
        ):
            raise ValueError("reflection interpretation_identity is invalid")
        return result


@dataclass(frozen=True)
class PersonaReflectionInterpretationRequest:
    """Permitted context for interpreting one already accepted event."""

    relationship_id: str
    agent_id: str
    user_id: str
    source_turn_id: str
    source_revision: str
    event: RelationshipEvent
    evidence: Tuple[EvidenceReference, ...]
    blueprint: CharacterBlueprint
    baseline: RelationshipBaseline
    manifest: Optional[PersonaManifest] = None
    approved_growth: Tuple[PersonaGrowthProposal, ...] = ()
    prior_events: Tuple[RelationshipEvent, ...] = ()
    prior_reflections: Tuple[PersonaReflectionRecord, ...] = ()
    record_kind: PersonaReflectionRecordKind = (
        PersonaReflectionRecordKind.REFLECTION
    )
    target_reflection_id: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in (
            "relationship_id",
            "agent_id",
            "user_id",
            "source_turn_id",
            "source_revision",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name, maximum=256),
            )
        event = self.event
        if not isinstance(event, RelationshipEvent):
            event = RelationshipEvent.from_dict(event)
            object.__setattr__(self, "event", event)
        if event.relationship_id != self.relationship_id:
            raise ValueError("reflection request event belongs to another relationship")
        evidence = tuple(
            item
            if isinstance(item, EvidenceReference)
            else EvidenceReference.from_dict(item)
            for item in self.evidence
        )
        if len(evidence) > 16:
            raise ValueError("reflection evidence cannot exceed 16 references")
        object.__setattr__(self, "evidence", evidence)
        if not isinstance(self.blueprint, CharacterBlueprint):
            object.__setattr__(
                self,
                "blueprint",
                CharacterBlueprint.from_dict(self.blueprint),
            )
        if not isinstance(self.baseline, RelationshipBaseline):
            object.__setattr__(
                self,
                "baseline",
                RelationshipBaseline.from_dict(self.baseline),
            )
        if self.manifest is not None and not isinstance(
            self.manifest,
            PersonaManifest,
        ):
            object.__setattr__(
                self,
                "manifest",
                PersonaManifest.from_dict(self.manifest),
            )
        growth = tuple(self.approved_growth)
        if any(item.status != PersonaGrowthStatus.APPROVED for item in growth):
            raise ValueError("reflection context can include only approved growth")
        object.__setattr__(self, "approved_growth", growth)
        events = tuple(self.prior_events)
        reflections = tuple(self.prior_reflections)
        if len(events) > 32 or len(reflections) > 32:
            raise ValueError("reflection prior context exceeds its bound")
        if any(item.relationship_id != self.relationship_id for item in events):
            raise ValueError("prior event belongs to another relationship")
        if any(
            item.relationship_id != self.relationship_id for item in reflections
        ):
            raise ValueError("prior reflection belongs to another relationship")
        object.__setattr__(self, "prior_events", events)
        object.__setattr__(self, "prior_reflections", reflections)
        kind = self.record_kind
        if not isinstance(kind, PersonaReflectionRecordKind):
            kind = PersonaReflectionRecordKind(kind)
            object.__setattr__(self, "record_kind", kind)
        target = _optional_text(
            self.target_reflection_id,
            "target_reflection_id",
            maximum=256,
        )
        object.__setattr__(self, "target_reflection_id", target)
        if kind in (
            PersonaReflectionRecordKind.CORRECTION,
            PersonaReflectionRecordKind.REINTERPRETATION,
        ) and target is None:
            raise ValueError(
                "correction and reinterpretation requests require a target"
            )
        if kind == PersonaReflectionRecordKind.REFLECTION and target is not None:
            raise ValueError("original reflection request cannot have a target")


class PersonaReflectionInterpreterV1(Protocol):
    """Host capability that interprets accepted facts without rewriting them."""

    descriptor: ReflectionInterpreterDescriptor

    def interpret(
        self,
        request: PersonaReflectionInterpretationRequest,
    ) -> PersonaReflectionDecision:
        """Returns one strict reflection or no-reflection decision."""


@dataclass(frozen=True)
class Episode:
    """Rebuildable narrative unit around one explicitly linked experience."""

    episode_id: str
    relationship_id: str
    event_ids: Tuple[str, ...]
    title: str
    summary: str
    started_at: str
    ended_at: str
    history_fingerprint: str
    projection_version: str = "relationship-consolidation-v1"

    def __post_init__(self) -> None:
        for field_name in (
            "episode_id",
            "relationship_id",
            "title",
            "summary",
            "started_at",
            "ended_at",
            "projection_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(
                    getattr(self, field_name),
                    field_name,
                    maximum=4000 if field_name == "summary" else 256,
                ),
            )
        object.__setattr__(
            self,
            "event_ids",
            _unique_texts(self.event_ids, "event_ids"),
        )
        if not self.event_ids:
            raise ValueError("Episode requires at least one source event")
        object.__setattr__(
            self,
            "history_fingerprint",
            _require_fingerprint(
                self.history_fingerprint,
                "history_fingerprint",
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "relationship_id": self.relationship_id,
            "event_ids": list(self.event_ids),
            "title": self.title,
            "summary": self.summary,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "history_fingerprint": self.history_fingerprint,
            "projection_version": self.projection_version,
        }


@dataclass(frozen=True)
class RelationshipChapter:
    """Rebuildable longer narrative connected by explicit event references."""

    chapter_id: str
    relationship_id: str
    episode_ids: Tuple[str, ...]
    event_ids: Tuple[str, ...]
    title: str
    summary: str
    started_at: str
    ended_at: str
    history_fingerprint: str
    projection_version: str = "relationship-consolidation-v1"

    def __post_init__(self) -> None:
        for field_name in (
            "chapter_id",
            "relationship_id",
            "title",
            "summary",
            "started_at",
            "ended_at",
            "projection_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(
                    getattr(self, field_name),
                    field_name,
                    maximum=4000 if field_name == "summary" else 256,
                ),
            )
        object.__setattr__(
            self,
            "episode_ids",
            _unique_texts(self.episode_ids, "episode_ids"),
        )
        object.__setattr__(
            self,
            "event_ids",
            _unique_texts(self.event_ids, "event_ids"),
        )
        if len(self.episode_ids) < 2:
            raise ValueError("RelationshipChapter requires at least two Episodes")
        if not self.event_ids:
            raise ValueError("RelationshipChapter requires source events")
        object.__setattr__(
            self,
            "history_fingerprint",
            _require_fingerprint(
                self.history_fingerprint,
                "history_fingerprint",
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "relationship_id": self.relationship_id,
            "episode_ids": list(self.episode_ids),
            "event_ids": list(self.event_ids),
            "title": self.title,
            "summary": self.summary,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "history_fingerprint": self.history_fingerprint,
            "projection_version": self.projection_version,
        }


@dataclass(frozen=True)
class RelationshipConsolidation:
    """Complete deterministic narrative projection over one history snapshot."""

    relationship_id: str
    episodes: Tuple[Episode, ...]
    chapters: Tuple[RelationshipChapter, ...]
    covered_event_ids: Tuple[str, ...]
    unconsolidated_event_ids: Tuple[str, ...]
    history_fingerprint: str
    projection_version: str = "relationship-consolidation-v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relationship_id",
            _require_text(
                self.relationship_id,
                "relationship_id",
                maximum=256,
            ),
        )
        episodes = tuple(self.episodes)
        chapters = tuple(self.chapters)
        if any(item.relationship_id != self.relationship_id for item in episodes):
            raise ValueError("Episode belongs to another relationship")
        if any(item.relationship_id != self.relationship_id for item in chapters):
            raise ValueError("RelationshipChapter belongs to another relationship")
        object.__setattr__(self, "episodes", episodes)
        object.__setattr__(self, "chapters", chapters)
        covered = _unique_texts(
            self.covered_event_ids,
            "covered_event_ids",
            maximum_items=100_000,
        )
        unconsolidated = _unique_texts(
            self.unconsolidated_event_ids,
            "unconsolidated_event_ids",
            maximum_items=100_000,
        )
        if set(covered).intersection(unconsolidated):
            raise ValueError("covered and unconsolidated events must be disjoint")
        object.__setattr__(self, "covered_event_ids", covered)
        object.__setattr__(
            self,
            "unconsolidated_event_ids",
            unconsolidated,
        )
        object.__setattr__(
            self,
            "history_fingerprint",
            _require_fingerprint(
                self.history_fingerprint,
                "history_fingerprint",
            ),
        )
        object.__setattr__(
            self,
            "projection_version",
            _require_text(
                self.projection_version,
                "projection_version",
                maximum=128,
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "episodes": [item.to_dict() for item in self.episodes],
            "chapters": [item.to_dict() for item in self.chapters],
            "covered_event_ids": list(self.covered_event_ids),
            "unconsolidated_event_ids": list(
                self.unconsolidated_event_ids
            ),
            "history_fingerprint": self.history_fingerprint,
            "projection_version": self.projection_version,
        }


__all__ = [
    "ApprovedGrowthReference",
    "Episode",
    "PersonaNoReflectionDecision",
    "PersonaReflectionContentDecision",
    "PersonaReflectionDecision",
    "PersonaReflectionDecisionRecord",
    "PersonaReflectionInterpretationRequest",
    "PersonaReflectionInterpreterV1",
    "PersonaReflectionRecord",
    "PersonaReflectionRecordKind",
    "ReflectionContextProvenance",
    "ReflectionInterpreterDescriptor",
    "ReflectionProvenanceState",
    "RelationshipChapter",
    "RelationshipConsolidation",
    "RelationshipEventCandidatesDecision",
    "RelationshipEventExtractionDecision",
    "RelationshipEventExtractionRequest",
    "RelationshipEventExtractorV1",
    "RelationshipNoEventDecision",
    "RelationshipProcessingConflictError",
    "RelationshipProcessingOutcome",
    "RelationshipProcessingRun",
    "RelationshipProcessingStatus",
    "persona_reflection_decision_from_value",
    "relationship_extraction_decision_from_value",
]
