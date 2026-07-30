"""Durable source-turn records for visible Agent x User conversations."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

from erii.models.relationship import utc_now


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


class TurnStatus(str, Enum):
    """Lifecycle state of one durable source turn."""

    OPEN = "open"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class TurnRole(str, Enum):
    """Visible speaker role in a source transcript."""

    USER = "user"
    AGENT = "agent"


class ContextSignalSource(str, Enum):
    """Authority class for one temporary interaction-context observation."""

    HOST_OBSERVED = "host_observed"
    CORE_DERIVED = "core_derived"
    EVALUATOR_INFERRED = "evaluator_inferred"


@dataclass(frozen=True)
class InteractionContextSignal:
    """Typed, source-labelled context used only for this interaction turn."""

    signal_id: str
    source: ContextSignalSource
    signal_type: str
    value: str
    evidence_refs: Tuple[str, ...] = ()
    recorded_at: str = field(default_factory=utc_now)
    relationship_id: Optional[str] = None
    source_turn_id: Optional[str] = None
    producer_version: Optional[str] = None
    _runtime_attestation: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_id", _require_text(self.signal_id, "signal_id"))
        if not isinstance(self.source, ContextSignalSource):
            object.__setattr__(self, "source", ContextSignalSource(self.source))
        object.__setattr__(
            self,
            "signal_type",
            _require_text(self.signal_type, "signal_type"),
        )
        object.__setattr__(self, "value", _require_text(self.value, "value"))
        evidence_refs = tuple(
            _require_text(item, "evidence_ref") for item in self.evidence_refs
        )
        if len(evidence_refs) != len(set(evidence_refs)):
            raise ValueError("interaction context evidence_refs must be unique")
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(
            self,
            "recorded_at",
            _require_text(self.recorded_at, "recorded_at"),
        )
        for field_name in (
            "relationship_id",
            "source_turn_id",
            "producer_version",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _require_text(value, field_name),
                )
        scoped_values = (
            self.relationship_id,
            self.source_turn_id,
            self.producer_version,
        )
        if any(value is not None for value in scoped_values) and not all(
            value is not None for value in scoped_values
        ):
            raise ValueError(
                "scoped interaction context requires relationship_id, "
                "source_turn_id, and producer_version together"
            )

    def same_claim_as(self, other: "InteractionContextSignal") -> bool:
        """Compares host-controlled signal content without server time."""
        return (
            self.signal_id == other.signal_id
            and self.source == other.source
            and self.signal_type == other.signal_type
            and self.value == other.value
            and self.evidence_refs == other.evidence_refs
            and self.relationship_id == other.relationship_id
            and self.source_turn_id == other.source_turn_id
            and self.producer_version == other.producer_version
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "source": self.source.value,
            "signal_type": self.signal_type,
            "value": self.value,
            "evidence_refs": list(self.evidence_refs),
            "recorded_at": self.recorded_at,
            "relationship_id": self.relationship_id,
            "source_turn_id": self.source_turn_id,
            "producer_version": self.producer_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InteractionContextSignal":
        return cls(
            signal_id=str(data["signal_id"]),
            source=ContextSignalSource(str(data["source"])),
            signal_type=str(data["signal_type"]),
            value=str(data["value"]),
            evidence_refs=tuple(str(item) for item in data.get("evidence_refs", [])),
            recorded_at=str(data.get("recorded_at") or utc_now()),
            relationship_id=data.get("relationship_id"),
            source_turn_id=data.get("source_turn_id"),
            producer_version=data.get("producer_version"),
        )


class ContinuityAssessmentStatus(str, Enum):
    """Whether a reply continuity assessment was actually performed."""

    NOT_EVALUATED = "not_evaluated"
    COMPLETED = "completed"
    FAILED = "failed"


class ContinuityVerdict(str, Enum):
    """Deterministic aggregate verdict from a configured continuity evaluator."""

    ALIGNED = "aligned"
    SUPPORTED_NEW_CHOICE = "supported_new_choice"
    REVIEW_REQUIRED = "review_required"
    UNSUPPORTED_DRIFT = "unsupported_drift"


class DeliveryDisposition(str, Enum):
    """What the host did with the reply captured in this source turn."""

    SHOWN = "shown"
    OVERRIDDEN = "overridden"


class SourceProcessingChannel(str, Enum):
    """Independent derived-processing responsibilities frozen at acceptance."""

    MEMORY_ARCHIVAL = "memory_archival"
    RELATIONSHIP_ADJUDICATION = "relationship_adjudication"


class SourceProcessingState(str, Enum):
    """Observable outcome state for one declared processing channel."""

    PENDING = "pending"
    ARTIFACTS_COMMITTED = "artifacts_committed"
    NO_OUTPUT = "no_output"
    FAILED = "failed"


class ReplyAttemptStage(str, Enum):
    """Host stage that failed before any reply became visible."""

    GENERATION = "generation"
    CONTINUITY_EVALUATION = "continuity_evaluation"
    DELIVERY_PREPARATION = "delivery_preparation"


@dataclass(frozen=True)
class ReplyContinuityAssessment:
    """Non-sensitive status placeholder for pre-delivery continuity review."""

    status: ContinuityAssessmentStatus = ContinuityAssessmentStatus.NOT_EVALUATED
    evaluator_version: Optional[str] = None
    verdict: Optional[ContinuityVerdict] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ContinuityAssessmentStatus):
            object.__setattr__(
                self,
                "status",
                ContinuityAssessmentStatus(self.status),
            )
        if self.evaluator_version is not None:
            object.__setattr__(
                self,
                "evaluator_version",
                _require_text(self.evaluator_version, "evaluator_version"),
            )
        if self.verdict is not None and not isinstance(
            self.verdict,
            ContinuityVerdict,
        ):
            object.__setattr__(self, "verdict", ContinuityVerdict(self.verdict))
        if self.status == ContinuityAssessmentStatus.COMPLETED and (
            self.evaluator_version is None or self.verdict is None
        ):
            raise ValueError(
                "a completed assessment requires evaluator_version and verdict"
            )
        if self.status != ContinuityAssessmentStatus.COMPLETED and self.verdict is not None:
            raise ValueError("only a completed assessment can claim a verdict")
        if self.status == ContinuityAssessmentStatus.NOT_EVALUATED and (
            self.evaluator_version is not None
        ):
            raise ValueError("a not_evaluated assessment cannot claim evaluator output")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "evaluator_version": self.evaluator_version,
            "verdict": self.verdict.value if self.verdict is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReplyContinuityAssessment":
        return cls(
            status=ContinuityAssessmentStatus(
                str(data.get("status", ContinuityAssessmentStatus.NOT_EVALUATED.value))
            ),
            evaluator_version=data.get("evaluator_version"),
            verdict=(
                ContinuityVerdict(str(data["verdict"]))
                if data.get("verdict") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class SourceProcessingPlan:
    """Immutable set of processing channels accepted with a source turn."""

    channels: Tuple[SourceProcessingChannel, ...]
    version: str = "source-processing-plan/v1"

    def __post_init__(self) -> None:
        channels = tuple(
            item if isinstance(item, SourceProcessingChannel) else SourceProcessingChannel(item)
            for item in self.channels
        )
        if len(channels) != len(set(channels)):
            raise ValueError("processing plan channels must be unique")
        object.__setattr__(self, "channels", channels)
        object.__setattr__(self, "version", _require_text(self.version, "version"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channels": [channel.value for channel in self.channels],
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceProcessingPlan":
        return cls(
            channels=tuple(
                SourceProcessingChannel(str(item))
                for item in data.get("channels", [])
            ),
            version=str(data.get("version", "source-processing-plan/v1")),
        )


@dataclass(frozen=True)
class SourceProcessingOutcome:
    """Current independent result for one channel in a frozen plan."""

    channel: SourceProcessingChannel
    state: SourceProcessingState = SourceProcessingState.PENDING
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.channel, SourceProcessingChannel):
            object.__setattr__(
                self,
                "channel",
                SourceProcessingChannel(self.channel),
            )
        if not isinstance(self.state, SourceProcessingState):
            object.__setattr__(self, "state", SourceProcessingState(self.state))
        object.__setattr__(
            self,
            "updated_at",
            _require_text(self.updated_at, "updated_at"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel.value,
            "state": self.state.value,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceProcessingOutcome":
        return cls(
            channel=SourceProcessingChannel(str(data["channel"])),
            state=SourceProcessingState(
                str(data.get("state", SourceProcessingState.PENDING.value))
            ),
            updated_at=str(data["updated_at"]),
        )


@dataclass(frozen=True)
class ReplyAttemptRecord:
    """Sanitized operational evidence for one failed reply attempt."""

    attempt_id: str
    relationship_id: str
    turn_id: str
    attempt_number: int
    stage: ReplyAttemptStage
    capability_descriptor: str
    failure_classification: str
    attempted_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for field_name in (
            "attempt_id",
            "relationship_id",
            "turn_id",
            "capability_descriptor",
            "failure_classification",
            "attempted_at",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.attempt_number, int) or self.attempt_number < 1:
            raise ValueError("attempt_number must be a positive integer")
        if not isinstance(self.stage, ReplyAttemptStage):
            object.__setattr__(self, "stage", ReplyAttemptStage(self.stage))

    def same_payload_as(self, other: "ReplyAttemptRecord") -> bool:
        """Compares stable host input while excluding the recorded time."""
        return (
            self.attempt_id == other.attempt_id
            and self.relationship_id == other.relationship_id
            and self.turn_id == other.turn_id
            and self.attempt_number == other.attempt_number
            and self.stage == other.stage
            and self.capability_descriptor == other.capability_descriptor
            and self.failure_classification == other.failure_classification
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "relationship_id": self.relationship_id,
            "turn_id": self.turn_id,
            "attempt_number": self.attempt_number,
            "stage": self.stage.value,
            "capability_descriptor": self.capability_descriptor,
            "failure_classification": self.failure_classification,
            "attempted_at": self.attempted_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReplyAttemptRecord":
        return cls(
            attempt_id=str(data["attempt_id"]),
            relationship_id=str(data["relationship_id"]),
            turn_id=str(data["turn_id"]),
            attempt_number=int(data["attempt_number"]),
            stage=ReplyAttemptStage(str(data["stage"])),
            capability_descriptor=str(data["capability_descriptor"]),
            failure_classification=str(data["failure_classification"]),
            attempted_at=str(data["attempted_at"]),
        )


@dataclass(frozen=True)
class TurnMessage:
    """One exact, user-visible message stored by the continuity kernel."""

    message_id: str
    role: TurnRole
    content: str
    recorded_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _require_text(self.message_id, "message_id"))
        if not isinstance(self.role, TurnRole):
            object.__setattr__(self, "role", TurnRole(self.role))
        object.__setattr__(self, "content", _require_text(self.content, "content"))
        object.__setattr__(
            self,
            "recorded_at",
            _require_text(self.recorded_at, "recorded_at"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "role": self.role.value,
            "content": self.content,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TurnMessage":
        return cls(
            message_id=str(data["message_id"]),
            role=TurnRole(str(data["role"])),
            content=str(data["content"]),
            recorded_at=str(data["recorded_at"]),
        )


@dataclass(frozen=True)
class SourceTranscript:
    """Exact visible transcript for one user-to-agent exchange."""

    user_message: TurnMessage
    agent_message: Optional[TurnMessage] = None

    def __post_init__(self) -> None:
        user_message = self.user_message
        if not isinstance(user_message, TurnMessage):
            user_message = TurnMessage.from_dict(user_message)
            object.__setattr__(self, "user_message", user_message)
        if user_message.role != TurnRole.USER:
            raise ValueError("user_message must have the user role")

        agent_message = self.agent_message
        if agent_message is not None and not isinstance(agent_message, TurnMessage):
            agent_message = TurnMessage.from_dict(agent_message)
            object.__setattr__(self, "agent_message", agent_message)
        if agent_message is not None and agent_message.role != TurnRole.AGENT:
            raise ValueError("agent_message must have the agent role")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_message": self.user_message.to_dict(),
            "agent_message": (
                self.agent_message.to_dict() if self.agent_message is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceTranscript":
        raw_agent = data.get("agent_message")
        return cls(
            user_message=TurnMessage.from_dict(data["user_message"]),
            agent_message=(
                TurnMessage.from_dict(raw_agent) if raw_agent is not None else None
            ),
        )


@dataclass(frozen=True)
class TurnRecord:
    """Canonical durable record for one isolated relationship turn."""

    turn_id: str
    relationship_id: str
    status: TurnStatus
    transcript: SourceTranscript
    interaction_context: Tuple[InteractionContextSignal, ...] = ()
    source_revision: str = "1"
    record_version: int = 1
    opened_at: str = field(default_factory=utc_now)
    continuity_assessment: Optional[ReplyContinuityAssessment] = None
    delivery_disposition: Optional[DeliveryDisposition] = None
    processing_plan: Optional[SourceProcessingPlan] = None
    processing_outcomes: Tuple[SourceProcessingOutcome, ...] = ()
    completed_at: Optional[str] = None
    abandoned_at: Optional[str] = None
    abandonment_reason: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "turn_id", _require_text(self.turn_id, "turn_id"))
        object.__setattr__(
            self,
            "relationship_id",
            _require_text(self.relationship_id, "relationship_id"),
        )
        if not isinstance(self.status, TurnStatus):
            object.__setattr__(self, "status", TurnStatus(self.status))
        transcript = self.transcript
        if not isinstance(transcript, SourceTranscript):
            transcript = SourceTranscript.from_dict(transcript)
            object.__setattr__(self, "transcript", transcript)
        interaction_context = tuple(
            item
            if isinstance(item, InteractionContextSignal)
            else InteractionContextSignal.from_dict(item)
            for item in self.interaction_context
        )
        signal_ids = tuple(item.signal_id for item in interaction_context)
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("interaction context signal IDs must be unique")
        object.__setattr__(self, "interaction_context", interaction_context)
        object.__setattr__(
            self,
            "source_revision",
            _require_text(self.source_revision, "source_revision"),
        )
        if not isinstance(self.record_version, int) or self.record_version < 1:
            raise ValueError("record_version must be a positive integer")
        object.__setattr__(self, "opened_at", _require_text(self.opened_at, "opened_at"))
        assessment = self.continuity_assessment
        if assessment is not None and not isinstance(
            assessment,
            ReplyContinuityAssessment,
        ):
            assessment = ReplyContinuityAssessment.from_dict(assessment)
            object.__setattr__(self, "continuity_assessment", assessment)
        disposition = self.delivery_disposition
        if disposition is not None and not isinstance(disposition, DeliveryDisposition):
            disposition = DeliveryDisposition(disposition)
            object.__setattr__(self, "delivery_disposition", disposition)
        plan = self.processing_plan
        if plan is not None and not isinstance(plan, SourceProcessingPlan):
            plan = SourceProcessingPlan.from_dict(plan)
            object.__setattr__(self, "processing_plan", plan)
        outcomes = tuple(
            item
            if isinstance(item, SourceProcessingOutcome)
            else SourceProcessingOutcome.from_dict(item)
            for item in self.processing_outcomes
        )
        object.__setattr__(self, "processing_outcomes", outcomes)
        if self.completed_at is not None:
            object.__setattr__(
                self,
                "completed_at",
                _require_text(self.completed_at, "completed_at"),
            )
        if self.abandoned_at is not None:
            object.__setattr__(
                self,
                "abandoned_at",
                _require_text(self.abandoned_at, "abandoned_at"),
            )
        if self.abandonment_reason is not None:
            object.__setattr__(
                self,
                "abandonment_reason",
                _require_text(self.abandonment_reason, "abandonment_reason"),
            )
        self._validate_lifecycle()

    def _validate_lifecycle(self) -> None:
        if self.status == TurnStatus.OPEN:
            if self.transcript.agent_message is not None:
                raise ValueError("an open turn cannot contain an agent message")
            if any(
                value is not None
                for value in (
                    self.continuity_assessment,
                    self.delivery_disposition,
                    self.processing_plan,
                    self.completed_at,
                    self.abandoned_at,
                    self.abandonment_reason,
                )
            ) or self.processing_outcomes:
                raise ValueError("an open turn cannot contain terminal state")
            return
        if self.status == TurnStatus.COMPLETED:
            if self.transcript.agent_message is None:
                raise ValueError("a completed turn requires an agent message")
            if (
                self.continuity_assessment is None
                or self.delivery_disposition is None
                or self.processing_plan is None
                or self.completed_at is None
            ):
                raise ValueError("a completed turn requires acceptance metadata")
            if self.abandoned_at is not None or self.abandonment_reason is not None:
                raise ValueError("a completed turn cannot contain abandonment metadata")
            planned = self.processing_plan.channels
            actual = tuple(outcome.channel for outcome in self.processing_outcomes)
            if actual != planned:
                raise ValueError("processing outcomes must match the frozen plan order")
            return
        if self.transcript.agent_message is not None:
            raise ValueError("an abandoned turn cannot contain an agent message")
        if self.abandoned_at is None or self.abandonment_reason is None:
            raise ValueError("an abandoned turn requires a time and sanitized reason")
        if any(
            value is not None
            for value in (
                self.continuity_assessment,
                self.delivery_disposition,
                self.processing_plan,
                self.completed_at,
            )
        ) or self.processing_outcomes:
            raise ValueError("an abandoned turn cannot contain source processing state")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "relationship_id": self.relationship_id,
            "status": self.status.value,
            "transcript": self.transcript.to_dict(),
            "interaction_context": [
                signal.to_dict() for signal in self.interaction_context
            ],
            "source_revision": self.source_revision,
            "record_version": self.record_version,
            "opened_at": self.opened_at,
            "continuity_assessment": (
                self.continuity_assessment.to_dict()
                if self.continuity_assessment is not None
                else None
            ),
            "delivery_disposition": (
                self.delivery_disposition.value
                if self.delivery_disposition is not None
                else None
            ),
            "processing_plan": (
                self.processing_plan.to_dict()
                if self.processing_plan is not None
                else None
            ),
            "processing_outcomes": [
                outcome.to_dict() for outcome in self.processing_outcomes
            ],
            "completed_at": self.completed_at,
            "abandoned_at": self.abandoned_at,
            "abandonment_reason": self.abandonment_reason,
        }

    def same_opening_as(self, other: "TurnRecord") -> bool:
        """Compares the host-controlled opening payload, excluding server time."""
        return (
            self.turn_id == other.turn_id
            and self.relationship_id == other.relationship_id
            and self.source_revision == other.source_revision
            and len(self.interaction_context) == len(other.interaction_context)
            and all(
                left.same_claim_as(right)
                for left, right in zip(
                    self.interaction_context,
                    other.interaction_context,
                )
            )
            and self.transcript.user_message.role
            == other.transcript.user_message.role
            and self.transcript.user_message.content
            == other.transcript.user_message.content
        )

    def same_terminal_payload_as(self, other: "TurnRecord") -> bool:
        """Compares a terminal host payload without server-generated timestamps."""
        if not self.same_opening_as(other) or self.status != other.status:
            return False
        if self.status == TurnStatus.COMPLETED:
            left_agent = self.transcript.agent_message
            right_agent = other.transcript.agent_message
            return (
                left_agent is not None
                and right_agent is not None
                and left_agent.content == right_agent.content
                and self.continuity_assessment == other.continuity_assessment
                and self.delivery_disposition == other.delivery_disposition
                and self.processing_plan == other.processing_plan
                and tuple(
                    (outcome.channel, outcome.state)
                    for outcome in self.processing_outcomes
                )
                == tuple(
                    (outcome.channel, outcome.state)
                    for outcome in other.processing_outcomes
                )
            )
        if self.status == TurnStatus.ABANDONED:
            return self.abandonment_reason == other.abandonment_reason
        return True

    def is_terminal_transition_from(self, existing: "TurnRecord") -> bool:
        """Checks immutable opening fields and the exact one-step CAS revision."""
        return (
            existing.status == TurnStatus.OPEN
            and self.status in (TurnStatus.COMPLETED, TurnStatus.ABANDONED)
            and self.turn_id == existing.turn_id
            and self.relationship_id == existing.relationship_id
            and self.source_revision == existing.source_revision
            and self.record_version == existing.record_version + 1
            and self.opened_at == existing.opened_at
            and self.transcript.user_message == existing.transcript.user_message
            and self.interaction_context == existing.interaction_context
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TurnRecord":
        return cls(
            turn_id=str(data["turn_id"]),
            relationship_id=str(data["relationship_id"]),
            status=TurnStatus(str(data["status"])),
            transcript=SourceTranscript.from_dict(data["transcript"]),
            interaction_context=tuple(
                InteractionContextSignal.from_dict(item)
                for item in data.get("interaction_context", [])
            ),
            source_revision=str(data.get("source_revision", "1")),
            record_version=int(data.get("record_version", data.get("revision", 1))),
            opened_at=str(data["opened_at"]),
            continuity_assessment=(
                ReplyContinuityAssessment.from_dict(data["continuity_assessment"])
                if data.get("continuity_assessment") is not None
                else None
            ),
            delivery_disposition=(
                DeliveryDisposition(str(data["delivery_disposition"]))
                if data.get("delivery_disposition") is not None
                else None
            ),
            processing_plan=(
                SourceProcessingPlan.from_dict(data["processing_plan"])
                if data.get("processing_plan") is not None
                else None
            ),
            processing_outcomes=tuple(
                SourceProcessingOutcome.from_dict(item)
                for item in data.get("processing_outcomes", [])
            ),
            completed_at=data.get("completed_at"),
            abandoned_at=data.get("abandoned_at"),
            abandonment_reason=data.get("abandonment_reason"),
        )


@dataclass(frozen=True)
class SourceTurnReceipt:
    """Non-sensitive confirmation that a complete source turn was accepted."""

    source_turn_id: str
    relationship_id: str
    source_revision: str
    accepted_at: str
    processing_plan: SourceProcessingPlan
    processing_outcomes: Tuple[SourceProcessingOutcome, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_turn_id": self.source_turn_id,
            "relationship_id": self.relationship_id,
            "source_revision": self.source_revision,
            "accepted_at": self.accepted_at,
            "processing_plan": self.processing_plan.to_dict(),
            "processing_outcomes": [
                outcome.to_dict() for outcome in self.processing_outcomes
            ],
        }

    @classmethod
    def from_record(cls, record: TurnRecord) -> "SourceTurnReceipt":
        if record.status != TurnStatus.COMPLETED:
            raise ValueError("only a completed turn has a source receipt")
        return cls(
            source_turn_id=record.turn_id,
            relationship_id=record.relationship_id,
            source_revision=record.source_revision,
            accepted_at=str(record.completed_at),
            processing_plan=record.processing_plan,
            processing_outcomes=record.processing_outcomes,
        )


class TurnConflictError(ValueError):
    """Raised when a stable turn identity is reused for different content."""


class TurnNotFoundError(LookupError):
    """Raised when a turn is not present in the requested relationship."""


class TurnTerminalConflictError(TurnConflictError):
    """Raised when a terminal turn is given a different terminal payload."""


class ReplyAttemptConflictError(TurnConflictError):
    """Raised when an attempt number is reused for different safe metadata."""
