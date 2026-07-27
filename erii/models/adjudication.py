"""Schemas and durable records for relationship candidate adjudication.

Pydantic models in this module form the untrusted LLM/host input seam.  Frozen
dataclasses represent the validated records that may be persisted and exported.
"""

from dataclasses import dataclass, field
from enum import Enum
import json
import math
from types import MappingProxyType
from typing import Annotated, Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from erii.models.relationship import RelationshipEvent, RelationshipEventType, utc_now
from erii.models.temporal import (
    OpenLoopResolution,
    OpenLoopResolutionKind,
    OpenLoopSpec,
    PromiseCondition,
    PromiseConditionConfirmation,
    PromiseResolution,
    PromiseResolutionKind,
    PromiseResponsibleParty,
    PromiseSpec,
    WorldMoment,
)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


class CandidateConflictError(ValueError):
    """Raised when an idempotency key is reused with different candidate input."""


class PersonaGrowthConflictError(ValueError):
    """Raised when a stale or invalid persona-growth decision is attempted."""


class SourceRole(str, Enum):
    """Roles supported by transient source messages."""

    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    TOOL = "tool"
    HOST = "host"


class SourceProcessingMode(str, Enum):
    """Whether a turn is new input or an explicit append-only historical review."""

    NORMAL = "normal"
    HISTORICAL_REPROCESSING = "historical_reprocessing"


class RelationshipSignalType(str, Enum):
    """Qualitative relationship meanings an extractor may propose."""

    NEUTRAL = "neutral"
    GRATITUDE = "gratitude"
    DISCLOSURE = "disclosure"
    RELIABILITY = "reliability"
    BOUNDARY_RESPECTED = "boundary_respected"
    BOUNDARY_VIOLATION = "boundary_violation"
    CONFLICT = "conflict"
    REPAIR = "repair"
    SHARED_EXPERIENCE = "shared_experience"
    REMEMBRANCE = "remembrance"
    COMMITMENT = "commitment"
    DISAPPOINTMENT = "disappointment"
    SUPPORT = "support"


class SignalStrength(str, Enum):
    """Bounded qualitative strength, never a numeric state suggestion."""

    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class GrowthTriggerKind(str, Enum):
    """Ways accepted history may justify a later independent inner review."""

    NONE = "none"
    ACCUMULATION = "accumulation"
    PIVOTAL = "pivotal"


class DecisionOutcome(str, Enum):
    """Durable outcomes produced by deterministic adjudication."""

    ACCEPTED = "accepted"
    CORROBORATED = "corroborated"
    IGNORED = "ignored"
    REJECTED = "rejected"


class PersonaGrowthStatus(str, Enum):
    """Out-of-band host decision state for persona growth."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class PersonaGrowthDecision(str, Enum):
    """Actions available only through the explicit host interface."""

    APPROVE = "approve"
    REJECT = "reject"
    REVOKE = "revoke"


class BoundaryModel(BaseModel):
    """Strict base for data crossing the untrusted extraction seam."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class WorldMomentCandidate(BoundaryModel):
    """Strict candidate form of a host-verifiable World Moment."""

    clock_id: str = Field(min_length=1, max_length=256)
    display_value: str = Field(min_length=1, max_length=4000)
    order_value: Optional[float] = None

    @field_validator("order_value", mode="before")
    @classmethod
    def order_is_numeric(cls, value):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("order_value must be numeric when supplied")
        return value

    @model_validator(mode="after")
    def order_is_finite(self) -> "WorldMomentCandidate":
        if self.order_value is not None and not math.isfinite(self.order_value):
            raise ValueError("order_value must be finite")
        return self

    def to_durable(self) -> WorldMoment:
        return WorldMoment(
            clock_id=self.clock_id,
            display_value=self.display_value,
            order_value=self.order_value,
        )


class PromiseConditionCandidate(BoundaryModel):
    """Strict candidate form of one Promise activation condition."""

    condition_id: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=4000)

    def to_durable(self) -> PromiseCondition:
        return PromiseCondition(
            condition_id=self.condition_id,
            description=self.description,
        )


class PromiseSpecCandidate(BoundaryModel):
    """Strict untrusted candidate for a structured Promise event."""

    payload_type: Literal["promise"] = "promise"
    responsible_parties: List[PromiseResponsibleParty] = Field(min_length=1, max_length=2)
    action: str = Field(min_length=1, max_length=8000)
    due_at: Optional[WorldMomentCandidate] = None
    activation_condition: Optional[PromiseConditionCandidate] = None

    @model_validator(mode="after")
    def parties_are_unique_and_canonical(self) -> "PromiseSpecCandidate":
        if len(self.responsible_parties) != len(set(self.responsible_parties)):
            raise ValueError("responsible_parties must not contain duplicates")
        canonical = sorted(
            self.responsible_parties,
            key={PromiseResponsibleParty.AGENT: 0, PromiseResponsibleParty.USER: 1}.__getitem__,
        )
        if canonical != self.responsible_parties:
            object.__setattr__(self, "responsible_parties", canonical)
        return self

    def to_durable(self) -> PromiseSpec:
        return PromiseSpec(
            responsible_parties=self.responsible_parties,
            action=self.action,
            due_at=self.due_at.to_durable() if self.due_at else None,
            activation_condition=(
                self.activation_condition.to_durable()
                if self.activation_condition
                else None
            ),
        )


class PromiseConditionConfirmationCandidate(BoundaryModel):
    """Strict candidate for append-only Promise activation evidence."""

    payload_type: Literal["promise_condition_confirmed"] = (
        "promise_condition_confirmed"
    )
    promise_event_id: str = Field(min_length=1, max_length=256)
    condition_id: str = Field(min_length=1, max_length=256)
    confirmed_at: Optional[WorldMomentCandidate] = None

    def to_durable(self) -> PromiseConditionConfirmation:
        return PromiseConditionConfirmation(
            promise_event_id=self.promise_event_id,
            condition_id=self.condition_id,
            confirmed_at=self.confirmed_at.to_durable() if self.confirmed_at else None,
        )


class PromiseResolutionCandidate(BoundaryModel):
    """Strict candidate for an append-only Promise resolution."""

    payload_type: Literal["promise_resolution"] = "promise_resolution"
    promise_event_id: str = Field(min_length=1, max_length=256)
    resolution_kind: PromiseResolutionKind
    resolved_at: Optional[WorldMomentCandidate] = None
    superseding_promise_event_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    note: Optional[str] = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def supersession_reference_is_consistent(self) -> "PromiseResolutionCandidate":
        if self.resolution_kind == PromiseResolutionKind.SUPERSEDED:
            if self.superseding_promise_event_id is None:
                raise ValueError(
                    "superseded Promise resolution requires superseding_promise_event_id"
                )
            if self.superseding_promise_event_id == self.promise_event_id:
                raise ValueError("a Promise cannot supersede itself")
        elif self.superseding_promise_event_id is not None:
            raise ValueError(
                "superseding_promise_event_id is only valid for a superseded resolution"
            )
        return self

    def to_durable(self) -> PromiseResolution:
        return PromiseResolution(
            promise_event_id=self.promise_event_id,
            resolution_kind=self.resolution_kind,
            resolved_at=self.resolved_at.to_durable() if self.resolved_at else None,
            superseding_promise_event_id=self.superseding_promise_event_id,
            note=self.note,
        )


class OpenLoopSpecCandidate(BoundaryModel):
    """Strict untrusted candidate for an Open Loop without responsibility."""

    payload_type: Literal["open_loop"] = "open_loop"
    subject: str = Field(min_length=1, max_length=8000)
    expected_continuation: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=8000,
    )
    origin_memory_node_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
    )

    def to_durable(self) -> OpenLoopSpec:
        return OpenLoopSpec(
            subject=self.subject,
            expected_continuation=self.expected_continuation,
            origin_memory_node_id=self.origin_memory_node_id,
        )


class OpenLoopResolutionCandidate(BoundaryModel):
    """Strict candidate for an append-only Open Loop resolution."""

    payload_type: Literal["open_loop_resolution"] = "open_loop_resolution"
    open_loop_event_id: str = Field(min_length=1, max_length=256)
    resolution_kind: OpenLoopResolutionKind
    superseding_open_loop_event_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    note: Optional[str] = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def supersession_reference_is_consistent(self) -> "OpenLoopResolutionCandidate":
        if self.resolution_kind == OpenLoopResolutionKind.SUPERSEDED:
            if self.superseding_open_loop_event_id is None:
                raise ValueError(
                    "superseded Open Loop resolution requires superseding_open_loop_event_id"
                )
            if self.superseding_open_loop_event_id == self.open_loop_event_id:
                raise ValueError("an Open Loop cannot supersede itself")
        elif self.superseding_open_loop_event_id is not None:
            raise ValueError(
                "superseding_open_loop_event_id is only valid for a superseded resolution"
            )
        return self

    def to_durable(self) -> OpenLoopResolution:
        return OpenLoopResolution(
            open_loop_event_id=self.open_loop_event_id,
            resolution_kind=self.resolution_kind,
            superseding_open_loop_event_id=self.superseding_open_loop_event_id,
            note=self.note,
        )


TemporalPayloadCandidate = Annotated[
    Union[
        PromiseSpecCandidate,
        PromiseConditionConfirmationCandidate,
        PromiseResolutionCandidate,
        OpenLoopSpecCandidate,
        OpenLoopResolutionCandidate,
    ],
    Field(discriminator="payload_type"),
]


class SourceMessage(BoundaryModel):
    """One transient message supplied by the host for evidence verification."""

    source_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(default="1", min_length=1, max_length=64)
    role: SourceRole
    content: str = Field(min_length=1, max_length=200_000)
    occurred_at: Optional[str] = Field(default=None, min_length=1)


class SourceTurn(BoundaryModel):
    """A stable, versioned source unit adjudicated idempotently."""

    turn_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(default="1", min_length=1, max_length=64)
    messages: List[SourceMessage] = Field(min_length=1, max_length=32)
    extractor_version: str = Field(default="unspecified", min_length=1, max_length=128)
    contract_version: str = Field(default="0.4.0a4", min_length=1, max_length=64)
    processing_mode: SourceProcessingMode = SourceProcessingMode.NORMAL
    reprocessing_id: Optional[str] = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def source_ids_are_unique(self) -> "SourceTurn":
        identities = [(message.source_id, message.revision) for message in self.messages]
        if len(identities) != len(set(identities)):
            raise ValueError("source message identities must be unique within a turn")
        if self.processing_mode == SourceProcessingMode.HISTORICAL_REPROCESSING:
            if self.reprocessing_id is None:
                raise ValueError("historical reprocessing requires reprocessing_id")
        elif self.reprocessing_id is not None:
            raise ValueError("reprocessing_id is only valid for historical reprocessing")
        return self


class EvidenceCitation(BoundaryModel):
    """An extractor's claim that an exact source span supports a candidate."""

    source_id: str = Field(min_length=1, max_length=256)
    source_revision: str = Field(default="1", min_length=1, max_length=64)
    quote: str = Field(min_length=1, max_length=4000)
    start: Optional[int] = Field(default=None, ge=0)
    end: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def offsets_are_complete(self) -> "EvidenceCitation":
        if (self.start is None) != (self.end is None):
            raise ValueError("evidence start and end must be supplied together")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("evidence end must be greater than start")
        return self


class RelationshipSignal(BoundaryModel):
    """A qualitative LLM suggestion interpreted by deterministic rules."""

    signal_type: RelationshipSignalType
    strength: SignalStrength = SignalStrength.MODERATE
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    interpretation_confidence: float = Field(ge=0.0, le=1.0)


class RelationshipEventCandidate(BoundaryModel):
    """One untrusted event candidate. Numeric relationship deltas are absent by design."""

    candidate_key: str = Field(min_length=1, max_length=128)
    event_type: RelationshipEventType
    summary: str = Field(min_length=1, max_length=4000)
    signal: RelationshipSignal
    temporal_payload: Optional[TemporalPayloadCandidate] = None
    evidence: List[EvidenceCitation] = Field(min_length=1, max_length=16)
    occurred_at: Optional[str] = Field(default=None, min_length=1)
    occurrence_key: Optional[str] = Field(default=None, min_length=1, max_length=256)
    references: List[str] = Field(default_factory=list, max_length=16)
    depends_on: List[str] = Field(default_factory=list, max_length=16)
    persona_reflection: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    growth_trigger: GrowthTriggerKind = GrowthTriggerKind.NONE

    @model_validator(mode="after")
    def candidate_lists_are_unique(self) -> "RelationshipEventCandidate":
        for field_name in ("references", "depends_on"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
        evidence_keys = [
            (item.source_id, item.source_revision, item.start, item.end, item.quote)
            for item in self.evidence
        ]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("evidence citations must not contain duplicates")

        expected_payload_types = {
            RelationshipEventType.PROMISE: PromiseSpecCandidate,
            RelationshipEventType.PROMISE_CONDITION_CONFIRMED: (
                PromiseConditionConfirmationCandidate
            ),
            RelationshipEventType.PROMISE_RESOLUTION: PromiseResolutionCandidate,
            RelationshipEventType.OPEN_LOOP: OpenLoopSpecCandidate,
            RelationshipEventType.OPEN_LOOP_RESOLUTION: OpenLoopResolutionCandidate,
        }
        expected_payload_type = expected_payload_types.get(self.event_type)
        if self.event_type == RelationshipEventType.PROMISE and self.temporal_payload is None:
            # Legacy generic Promise candidates remain parseable.
            pass
        elif expected_payload_type is not None:
            if not isinstance(self.temporal_payload, expected_payload_type):
                raise ValueError(
                    f"{self.event_type.value} requires "
                    f"{expected_payload_type.__name__} temporal_payload"
                )
        elif self.temporal_payload is not None:
            raise ValueError(
                f"{self.event_type.value} candidates cannot contain temporal_payload"
            )
        if (
            self.event_type == RelationshipEventType.PROMISE
            and self.temporal_payload is not None
            and self.signal.signal_type != RelationshipSignalType.COMMITMENT
        ):
            raise ValueError("structured Promise candidates require a commitment signal")
        return self


class RelationshipCandidateBatch(BoundaryModel):
    """A bounded set of independently adjudicated candidates for one source turn."""

    candidates: List[RelationshipEventCandidate] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def candidate_keys_are_unique(self) -> "RelationshipCandidateBatch":
        keys = [candidate.candidate_key for candidate in self.candidates]
        if len(keys) != len(set(keys)):
            raise ValueError("candidate_key must be unique within a batch")
        return self


class RelationshipPolicySpec(BoundaryModel):
    """Versioned, globally bounded relationship modifiers selected by a blueprint."""

    version: str = Field(default="default-v1", min_length=1, max_length=128)
    signal_modifiers: Dict[RelationshipSignalType, float] = Field(default_factory=dict)
    pivotal_signals: List[RelationshipSignalType] = Field(default_factory=list)

    @model_validator(mode="after")
    def modifiers_are_bounded(self) -> "RelationshipPolicySpec":
        for modifier in self.signal_modifiers.values():
            if not 0.5 <= modifier <= 1.5:
                raise ValueError("relationship policy modifiers must be between 0.5 and 1.5")
        if len(self.pivotal_signals) != len(set(self.pivotal_signals)):
            raise ValueError("pivotal_signals must not contain duplicates")
        return self


class PersonaGrowthIntentCandidate(BoundaryModel):
    """A later inner-review output based only on already accepted history IDs."""

    intent_key: str = Field(min_length=1, max_length=128)
    review_id: str = Field(min_length=1, max_length=256)
    statement: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=8000)
    proposed_changes: Dict[str, Any]
    supporting_event_ids: List[str] = Field(min_length=1, max_length=64)
    trigger_kind: GrowthTriggerKind

    @model_validator(mode="after")
    def intent_is_well_formed(self) -> "PersonaGrowthIntentCandidate":
        if self.trigger_kind == GrowthTriggerKind.NONE:
            raise ValueError("persona growth intent must name an accumulation or pivotal trigger")
        if len(self.supporting_event_ids) != len(set(self.supporting_event_ids)):
            raise ValueError("supporting_event_ids must not contain duplicates")
        try:
            serialized_changes = json.dumps(self.proposed_changes, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("proposed_changes must contain JSON-compatible values") from exc
        if len(serialized_changes.encode("utf-8")) > 65_536:
            raise ValueError("proposed_changes must not exceed 64 KiB")
        reserved_blueprint_fields = {
            "blueprint",
            "blueprint_id",
            "character_blueprint",
            "core_identity",
            "persona_source",
            "relationship_policy",
            "source_text",
        }
        forbidden = reserved_blueprint_fields.intersection(
            str(key).casefold() for key in self.proposed_changes
        )
        if forbidden:
            raise ValueError("persona growth cannot modify Character Blueprint authority fields")
        return self


@dataclass(frozen=True)
class EvidenceReference:
    """Minimal durable evidence derived from a verified transient source span."""

    evidence_id: str
    source_id: str
    source_revision: str
    role: SourceRole
    quote: str
    message_sha256: str
    start: int
    end: int
    occurred_at: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in (
            "evidence_id",
            "source_id",
            "source_revision",
            "quote",
            "message_sha256",
        ):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name)
            )
        role = self.role
        if isinstance(role, str):
            object.__setattr__(self, "role", SourceRole(role))
        if self.start < 0 or self.end <= self.start:
            raise ValueError("evidence offsets are invalid")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "role": self.role.value,
            "quote": self.quote,
            "message_sha256": self.message_sha256,
            "start": self.start,
            "end": self.end,
            "occurred_at": self.occurred_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceReference":
        return cls(
            evidence_id=str(data["evidence_id"]),
            source_id=str(data["source_id"]),
            source_revision=str(data.get("source_revision", "1")),
            role=SourceRole(data["role"]),
            quote=str(data["quote"]),
            message_sha256=str(data["message_sha256"]),
            start=int(data["start"]),
            end=int(data["end"]),
            occurred_at=data.get("occurred_at"),
        )


@dataclass(frozen=True)
class DecisionReceipt:
    """Minimal durable explanation of one candidate's adjudication."""

    decision_id: str
    relationship_id: str
    source_turn_id: str
    source_revision: str
    candidate_key: str
    candidate_fingerprint: str
    batch_fingerprint: str
    occurrence_fingerprint: str
    outcome: DecisionOutcome
    reason_codes: Sequence[str]
    extraction_confidence: float
    interpretation_confidence: float
    extractor_version: str
    contract_version: str
    rule_version: str
    policy_version: str
    processing_mode: SourceProcessingMode = SourceProcessingMode.NORMAL
    reprocessing_id: Optional[str] = None
    evidence: Sequence[EvidenceReference] = field(default_factory=tuple)
    event_ids: Sequence[str] = field(default_factory=tuple)
    related_event_id: Optional[str] = None
    pivotal_eligible: bool = False
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for field_name in (
            "decision_id",
            "relationship_id",
            "source_turn_id",
            "source_revision",
            "candidate_key",
            "candidate_fingerprint",
            "batch_fingerprint",
            "occurrence_fingerprint",
            "extractor_version",
            "contract_version",
            "rule_version",
            "policy_version",
            "created_at",
        ):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name)
            )
        outcome = self.outcome
        if isinstance(outcome, str):
            object.__setattr__(self, "outcome", DecisionOutcome(outcome))
        processing_mode = self.processing_mode
        if isinstance(processing_mode, str):
            processing_mode = SourceProcessingMode(processing_mode)
            object.__setattr__(self, "processing_mode", processing_mode)
        if processing_mode == SourceProcessingMode.HISTORICAL_REPROCESSING:
            if not self.reprocessing_id:
                raise ValueError("historical reprocessing receipt requires reprocessing_id")
        elif self.reprocessing_id is not None:
            raise ValueError("normal receipt cannot contain reprocessing_id")
        for confidence in (self.extraction_confidence, self.interpretation_confidence):
            if not 0.0 <= float(confidence) <= 1.0:
                raise ValueError("receipt confidences must be between 0.0 and 1.0")
        object.__setattr__(self, "extraction_confidence", float(self.extraction_confidence))
        object.__setattr__(self, "interpretation_confidence", float(self.interpretation_confidence))
        object.__setattr__(self, "reason_codes", tuple(str(item) for item in self.reason_codes))
        object.__setattr__(
            self,
            "evidence",
            tuple(
                item if isinstance(item, EvidenceReference) else EvidenceReference.from_dict(item)
                for item in self.evidence
            ),
        )
        object.__setattr__(self, "event_ids", tuple(str(item) for item in self.event_ids))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "relationship_id": self.relationship_id,
            "source_turn_id": self.source_turn_id,
            "source_revision": self.source_revision,
            "candidate_key": self.candidate_key,
            "candidate_fingerprint": self.candidate_fingerprint,
            "batch_fingerprint": self.batch_fingerprint,
            "occurrence_fingerprint": self.occurrence_fingerprint,
            "outcome": self.outcome.value,
            "reason_codes": list(self.reason_codes),
            "extraction_confidence": self.extraction_confidence,
            "interpretation_confidence": self.interpretation_confidence,
            "extractor_version": self.extractor_version,
            "contract_version": self.contract_version,
            "rule_version": self.rule_version,
            "policy_version": self.policy_version,
            "processing_mode": self.processing_mode.value,
            "reprocessing_id": self.reprocessing_id,
            "evidence": [item.to_dict() for item in self.evidence],
            "event_ids": list(self.event_ids),
            "related_event_id": self.related_event_id,
            "pivotal_eligible": self.pivotal_eligible,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionReceipt":
        return cls(
            decision_id=str(data["decision_id"]),
            relationship_id=str(data["relationship_id"]),
            source_turn_id=str(data["source_turn_id"]),
            source_revision=str(data["source_revision"]),
            candidate_key=str(data["candidate_key"]),
            candidate_fingerprint=str(data["candidate_fingerprint"]),
            batch_fingerprint=str(data.get("batch_fingerprint", data["candidate_fingerprint"])),
            occurrence_fingerprint=str(data["occurrence_fingerprint"]),
            outcome=DecisionOutcome(data["outcome"]),
            reason_codes=data.get("reason_codes", []),
            extraction_confidence=float(data["extraction_confidence"]),
            interpretation_confidence=float(data["interpretation_confidence"]),
            extractor_version=str(data["extractor_version"]),
            contract_version=str(data["contract_version"]),
            rule_version=str(data["rule_version"]),
            policy_version=str(data["policy_version"]),
            processing_mode=SourceProcessingMode(
                data.get("processing_mode", SourceProcessingMode.NORMAL.value)
            ),
            reprocessing_id=data.get("reprocessing_id"),
            evidence=[EvidenceReference.from_dict(item) for item in data.get("evidence", [])],
            event_ids=data.get("event_ids", []),
            related_event_id=data.get("related_event_id"),
            pivotal_eligible=bool(data.get("pivotal_eligible", False)),
            created_at=str(data["created_at"]),
        )


@dataclass(frozen=True)
class AdjudicationRecord:
    """One atomically persisted receipt plus any events it accepted."""

    receipt: DecisionReceipt
    events: Sequence[RelationshipEvent] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "events",
            tuple(
                item if isinstance(item, RelationshipEvent) else RelationshipEvent.from_dict(item)
                for item in self.events
            ),
        )
        if tuple(event.event_id for event in self.events) != tuple(self.receipt.event_ids):
            raise ValueError("receipt event_ids must match adjudication events")
        for event in self.events:
            if event.relationship_id != self.receipt.relationship_id:
                raise ValueError("adjudication event belongs to a different relationship")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt": self.receipt.to_dict(),
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AdjudicationRecord":
        return cls(
            receipt=DecisionReceipt.from_dict(data["receipt"]),
            events=[RelationshipEvent.from_dict(item) for item in data.get("events", [])],
        )


@dataclass(frozen=True)
class AdjudicationBatchResult:
    """Observable result returned through the adjudication module interface."""

    records: Sequence[AdjudicationRecord]

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))

    @property
    def receipts(self) -> Tuple[DecisionReceipt, ...]:
        return tuple(record.receipt for record in self.records)

    @property
    def events(self) -> Tuple[RelationshipEvent, ...]:
        return tuple(event for record in self.records for event in record.events)


@dataclass(frozen=True)
class PersonaGrowthProposal:
    """Version-pinned persona growth content and its out-of-band host decision."""

    proposal_id: str
    relationship_id: str
    revision: int
    intent_key: str
    review_id: str
    statement: str
    rationale: str
    proposed_changes: Mapping[str, Any]
    supporting_event_ids: Sequence[str]
    trigger_kind: GrowthTriggerKind
    status: PersonaGrowthStatus = PersonaGrowthStatus.PENDING
    created_at: str = field(default_factory=utc_now)
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    decision_reason: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in (
            "proposal_id",
            "relationship_id",
            "intent_key",
            "review_id",
            "statement",
            "rationale",
            "created_at",
        ):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name)
            )
        if self.revision < 1:
            raise ValueError("proposal revision must be positive")
        trigger_kind = self.trigger_kind
        if isinstance(trigger_kind, str):
            trigger_kind = GrowthTriggerKind(trigger_kind)
            object.__setattr__(self, "trigger_kind", trigger_kind)
        if trigger_kind == GrowthTriggerKind.NONE:
            raise ValueError("persona growth proposal must have a growth trigger")
        status = self.status
        if isinstance(status, str):
            object.__setattr__(self, "status", PersonaGrowthStatus(status))
        try:
            json.dumps(_thaw_json(self.proposed_changes), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("proposed_changes must contain JSON-compatible values") from exc
        object.__setattr__(self, "proposed_changes", _freeze_json(self.proposed_changes))
        event_ids = tuple(str(item) for item in self.supporting_event_ids)
        if not event_ids or len(event_ids) != len(set(event_ids)):
            raise ValueError("supporting_event_ids must be non-empty and unique")
        object.__setattr__(self, "supporting_event_ids", event_ids)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "relationship_id": self.relationship_id,
            "revision": self.revision,
            "intent_key": self.intent_key,
            "review_id": self.review_id,
            "statement": self.statement,
            "rationale": self.rationale,
            "proposed_changes": _thaw_json(self.proposed_changes),
            "supporting_event_ids": list(self.supporting_event_ids),
            "trigger_kind": self.trigger_kind.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "decision_reason": self.decision_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersonaGrowthProposal":
        return cls(
            proposal_id=str(data["proposal_id"]),
            relationship_id=str(data["relationship_id"]),
            revision=int(data["revision"]),
            intent_key=str(data["intent_key"]),
            review_id=str(data["review_id"]),
            statement=str(data["statement"]),
            rationale=str(data["rationale"]),
            proposed_changes=data.get("proposed_changes", {}),
            supporting_event_ids=data.get("supporting_event_ids", []),
            trigger_kind=GrowthTriggerKind(data["trigger_kind"]),
            status=PersonaGrowthStatus(data.get("status", PersonaGrowthStatus.PENDING.value)),
            created_at=str(data["created_at"]),
            decided_by=data.get("decided_by"),
            decided_at=data.get("decided_at"),
            decision_reason=data.get("decision_reason"),
        )
