"""Strict continuity-evaluation contracts and temporary voice projections."""

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Dict, Mapping, Protocol, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from erii.models.persona import PersonaScope
from erii.models.relationship import RELATIONSHIP_DIMENSIONS, RelationshipEvent
from erii.models.turn import (
    ContextSignalSource,
    ContinuityAssessmentStatus,
    InteractionContextSignal,
    ReplyContinuityAssessment,
)


_DESCRIPTOR_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, field_name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    clean = value.strip()
    if len(clean) > maximum:
        raise ValueError(f"{field_name} must not exceed {maximum} characters")
    return clean


def _descriptor_part(value: object, field_name: str) -> str:
    clean = _text(value, field_name, maximum=128)
    if not _DESCRIPTOR_PART.fullmatch(clean):
        raise ValueError(
            f"{field_name} must be a non-sensitive version identifier"
        )
    return clean


def _unique_text(values: Sequence[object], field_name: str) -> Tuple[str, ...]:
    normalized = tuple(_text(item, field_name, maximum=256) for item in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


class ContinuityBoundaryModel(BaseModel):
    """Strict base for untrusted evaluator output."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ContinuityAxis(str, Enum):
    """Independent semantic axes required from ContinuityEvaluatorV1."""

    IDENTITY_VALUES = "identity_values"
    PSYCHOLOGICAL_CAUSALITY = "psychological_causality"
    RELATIONSHIP_SCOPE = "relationship_scope"
    KNOWLEDGE_MEMORY_SCOPE = "knowledge_memory_scope"
    VOICE_STYLE = "voice_style"


class ContinuityFindingAssessment(str, Enum):
    """One evaluator finding before deterministic aggregation."""

    ALIGNED = "aligned"
    SUPPORTED = "supported"
    REVIEW = "review"
    UNSUPPORTED = "unsupported"


class ContinuityFindingSeverity(str, Enum):
    """Bounded severity independent from the aggregate verdict."""

    INFO = "info"
    ADVISORY = "advisory"
    WARNING = "warning"
    CRITICAL = "critical"


class ContinuityReasonCode(str, Enum):
    """Stable reason codes understood by the aggregation policy."""

    ALIGNED = "aligned"
    SUPPORTED_NEW_CHOICE = "supported_new_choice"
    SUPPORTED_CONTEXTUAL_VOICE = "supported_contextual_voice"
    VALUE_TENSION = "value_tension"
    CAUSAL_TENSION = "causal_tension"
    RELATIONSHIP_CROSSOVER = "relationship_crossover"
    INHERITED_INTIMACY = "inherited_intimacy"
    UNAVAILABLE_KNOWLEDGE = "unavailable_knowledge"
    UNSUPPORTED_IDENTITY_CHANGE = "unsupported_identity_change"
    UNSUPPORTED_CAUSAL_CHANGE = "unsupported_causal_change"
    VOICE_STYLE_DEVIATION = "voice_style_deviation"


_REASON_AXES = {
    ContinuityReasonCode.SUPPORTED_CONTEXTUAL_VOICE: {ContinuityAxis.VOICE_STYLE},
    ContinuityReasonCode.VALUE_TENSION: {ContinuityAxis.IDENTITY_VALUES},
    ContinuityReasonCode.CAUSAL_TENSION: {
        ContinuityAxis.PSYCHOLOGICAL_CAUSALITY
    },
    ContinuityReasonCode.RELATIONSHIP_CROSSOVER: {
        ContinuityAxis.RELATIONSHIP_SCOPE
    },
    ContinuityReasonCode.INHERITED_INTIMACY: {
        ContinuityAxis.RELATIONSHIP_SCOPE
    },
    ContinuityReasonCode.UNAVAILABLE_KNOWLEDGE: {
        ContinuityAxis.KNOWLEDGE_MEMORY_SCOPE
    },
    ContinuityReasonCode.UNSUPPORTED_IDENTITY_CHANGE: {
        ContinuityAxis.IDENTITY_VALUES
    },
    ContinuityReasonCode.UNSUPPORTED_CAUSAL_CHANGE: {
        ContinuityAxis.PSYCHOLOGICAL_CAUSALITY
    },
    ContinuityReasonCode.VOICE_STYLE_DEVIATION: {ContinuityAxis.VOICE_STYLE},
}


class ContinuityFinding(ContinuityBoundaryModel):
    """One source-backed finding on exactly one semantic axis."""

    finding_id: str = Field(min_length=1, max_length=256)
    axis: ContinuityAxis
    assessment: ContinuityFindingAssessment
    severity: ContinuityFindingSeverity
    reason_code: ContinuityReasonCode
    reply_start: int = Field(ge=0)
    reply_end: int = Field(ge=1)
    reply_quote: str = Field(min_length=1, max_length=4000)
    supporting_basis_refs: Tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    conflicting_source_refs: Tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=64,
    )

    @model_validator(mode="after")
    def finding_is_bounded_and_causally_sourced(self) -> "ContinuityFinding":
        if self.reply_end <= self.reply_start:
            raise ValueError("reply_end must be greater than reply_start")
        if self.reply_end - self.reply_start != len(self.reply_quote):
            raise ValueError("reply span length must match reply_quote")
        if len(self.supporting_basis_refs) != len(set(self.supporting_basis_refs)):
            raise ValueError("supporting_basis_refs must not contain duplicates")
        if len(self.conflicting_source_refs) != len(
            set(self.conflicting_source_refs)
        ):
            raise ValueError("conflicting_source_refs must not contain duplicates")
        if not self.supporting_basis_refs and not self.conflicting_source_refs:
            raise ValueError("every finding requires a supporting or conflicting source")
        if (
            self.assessment
            in {
                ContinuityFindingAssessment.REVIEW,
                ContinuityFindingAssessment.UNSUPPORTED,
            }
            and not self.conflicting_source_refs
        ):
            raise ValueError("review and unsupported findings require a conflict source")
        expected_axes = _REASON_AXES.get(self.reason_code)
        if expected_axes is not None and self.axis not in expected_axes:
            raise ValueError("reason_code is incompatible with the finding axis")
        if (
            self.reason_code == ContinuityReasonCode.VOICE_STYLE_DEVIATION
            and self.severity != ContinuityFindingSeverity.ADVISORY
        ):
            raise ValueError("voice-style deviation is advisory severity")
        if (
            self.reason_code
            in {
                ContinuityReasonCode.RELATIONSHIP_CROSSOVER,
                ContinuityReasonCode.INHERITED_INTIMACY,
                ContinuityReasonCode.UNAVAILABLE_KNOWLEDGE,
            }
            and self.severity != ContinuityFindingSeverity.CRITICAL
        ):
            raise ValueError("hard continuity conflicts require critical severity")
        return self


@dataclass(frozen=True)
class ContinuityEvaluationDecision:
    """Strict evaluator output containing exactly one finding per axis."""

    findings: Tuple[ContinuityFinding, ...]

    kind = "findings"

    def __post_init__(self) -> None:
        parsed = tuple(
            item
            if isinstance(item, ContinuityFinding)
            else ContinuityFinding.model_validate(item)
            for item in self.findings
        )
        if len(parsed) != len(ContinuityAxis):
            raise ValueError("ContinuityEvaluatorV1 requires exactly five findings")
        axes = tuple(item.axis for item in parsed)
        if set(axes) != set(ContinuityAxis) or len(axes) != len(set(axes)):
            raise ValueError("ContinuityEvaluatorV1 requires one finding for every axis")
        ids = tuple(item.finding_id for item in parsed)
        if len(ids) != len(set(ids)):
            raise ValueError("continuity finding IDs must be unique")
        object.__setattr__(
            self,
            "findings",
            tuple(sorted(parsed, key=lambda item: list(ContinuityAxis).index(item.axis))),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "findings": [item.model_dump(mode="json") for item in self.findings],
        }


def continuity_evaluation_decision_from_value(
    value: object,
) -> ContinuityEvaluationDecision:
    """Validates evaluator output without accepting an aggregate verdict."""
    if isinstance(value, ContinuityEvaluationDecision):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("continuity evaluator output must be a mapping")
    if set(value) != {"kind", "findings"} or value.get("kind") != "findings":
        raise ValueError("continuity evaluator output requires only kind=findings")
    findings = value.get("findings")
    if isinstance(findings, (str, bytes)) or not isinstance(findings, Sequence):
        raise ValueError("continuity findings must be a sequence")
    return ContinuityEvaluationDecision(
        findings=tuple(ContinuityFinding.model_validate(item) for item in findings)
    )


@dataclass(frozen=True)
class ContinuityEvaluatorDescriptor:
    """Non-sensitive identity for one host-provided evaluator contract."""

    evaluator_id: str
    evaluator_version: str
    evaluation_schema_version: str = "1"

    def __post_init__(self) -> None:
        for field_name in (
            "evaluator_id",
            "evaluator_version",
            "evaluation_schema_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _descriptor_part(getattr(self, field_name), field_name),
            )

    @property
    def public_version(self) -> str:
        return (
            f"{self.evaluator_id}@{self.evaluator_version}"
            f"/{self.evaluation_schema_version}"
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "evaluation_schema_version": self.evaluation_schema_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContinuityEvaluatorDescriptor":
        if set(data) != {
            "evaluator_id",
            "evaluator_version",
            "evaluation_schema_version",
        }:
            raise ValueError(
                "ContinuityEvaluatorDescriptor contains unknown or missing fields"
            )
        return cls(
            evaluator_id=data["evaluator_id"],
            evaluator_version=data["evaluator_version"],
            evaluation_schema_version=data["evaluation_schema_version"],
        )


@dataclass(frozen=True)
class InteractionContextEvaluatorDescriptor:
    """Non-sensitive identity for one pre-generation context evaluator."""

    evaluator_id: str
    evaluator_version: str
    evaluation_schema_version: str = "1"

    def __post_init__(self) -> None:
        for field_name in (
            "evaluator_id",
            "evaluator_version",
            "evaluation_schema_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _descriptor_part(getattr(self, field_name), field_name),
            )

    @property
    def public_version(self) -> str:
        return (
            f"{self.evaluator_id}@{self.evaluator_version}"
            f"/{self.evaluation_schema_version}"
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "evaluation_schema_version": self.evaluation_schema_version,
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "InteractionContextEvaluatorDescriptor":
        if set(data) != {
            "evaluator_id",
            "evaluator_version",
            "evaluation_schema_version",
        }:
            raise ValueError(
                "InteractionContextEvaluatorDescriptor contains unknown "
                "or missing fields"
            )
        return cls(
            evaluator_id=data["evaluator_id"],
            evaluator_version=data["evaluator_version"],
            evaluation_schema_version=data["evaluation_schema_version"],
        )


class InteractionContextNoSignalsReason(str, Enum):
    """Stable reasons for declining to infer a current emotion."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_DISTINCT_EMOTION = "no_distinct_emotion"
    NOT_APPLICABLE = "not_applicable"


class EvaluatorInferredEmotionCandidate(ContinuityBoundaryModel):
    """One bounded emotion claim proposed by an independent evaluator."""

    candidate_key: str = Field(min_length=1, max_length=256)
    value: str = Field(min_length=1, max_length=256)
    evidence_refs: Tuple[str, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def evidence_is_unique(self) -> "EvaluatorInferredEmotionCandidate":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("emotion evidence_refs must not contain duplicates")
        return self


@dataclass(frozen=True)
class InteractionContextSignalsDecision:
    """Strict evaluator result containing one or more sourced emotions."""

    signals: Tuple[EvaluatorInferredEmotionCandidate, ...]

    kind = "signals"

    def __post_init__(self) -> None:
        parsed = tuple(
            item
            if isinstance(item, EvaluatorInferredEmotionCandidate)
            else EvaluatorInferredEmotionCandidate.model_validate(item)
            for item in self.signals
        )
        if not parsed or len(parsed) > 8:
            raise ValueError(
                "interaction context evaluator requires between one and "
                "eight emotion signals"
            )
        keys = tuple(item.candidate_key for item in parsed)
        values = tuple(item.value.casefold() for item in parsed)
        if len(keys) != len(set(keys)):
            raise ValueError("emotion candidate keys must be unique")
        if len(values) != len(set(values)):
            raise ValueError("inferred emotion values must be unique")
        object.__setattr__(self, "signals", parsed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "signals": [
                item.model_dump(mode="json")
                for item in self.signals
            ],
        }


@dataclass(frozen=True)
class InteractionContextNoSignalsDecision:
    """Explicit successful decision that no emotion is supportable."""

    reason_code: InteractionContextNoSignalsReason

    kind = "no_signals"

    def __post_init__(self) -> None:
        if not isinstance(self.reason_code, InteractionContextNoSignalsReason):
            object.__setattr__(
                self,
                "reason_code",
                InteractionContextNoSignalsReason(self.reason_code),
            )

    def to_dict(self) -> Dict[str, str]:
        return {
            "kind": self.kind,
            "reason_code": self.reason_code.value,
        }


InteractionContextEvaluationDecision = Union[
    InteractionContextSignalsDecision,
    InteractionContextNoSignalsDecision,
]


def interaction_context_evaluation_decision_from_value(
    value: object,
) -> InteractionContextEvaluationDecision:
    """Validates strict emotion-signals/no-signals evaluator output."""
    if isinstance(
        value,
        (
            InteractionContextSignalsDecision,
            InteractionContextNoSignalsDecision,
        ),
    ):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("interaction context evaluator output must be a mapping")
    kind = value.get("kind")
    if kind == "signals":
        if set(value) != {"kind", "signals"}:
            raise ValueError(
                "signals output contains unknown or missing fields"
            )
        signals = value.get("signals")
        if isinstance(signals, (str, bytes)) or not isinstance(
            signals,
            Sequence,
        ):
            raise ValueError("interaction context signals must be a sequence")
        return InteractionContextSignalsDecision(
            signals=tuple(
                EvaluatorInferredEmotionCandidate.model_validate(item)
                for item in signals
            )
        )
    if kind == "no_signals":
        if set(value) != {"kind", "reason_code"}:
            raise ValueError(
                "no_signals output contains unknown or missing fields"
            )
        return InteractionContextNoSignalsDecision(
            reason_code=InteractionContextNoSignalsReason(
                str(value["reason_code"])
            )
        )
    raise ValueError(
        "interaction context evaluator output requires kind=signals "
        "or kind=no_signals"
    )


@dataclass(frozen=True)
class InteractionContextEvaluationRequest:
    """Bounded current-turn evidence passed to an emotion evaluator."""

    turn_id: str
    relationship_id: str
    persona_id: str
    persona_manifest_id: str
    user_message_id: str
    user_message: str
    emotion_values: Tuple[str, ...]
    relationship_state: Mapping[str, float]
    recent_events: Tuple[RelationshipEvent, ...] = ()
    host_observed_signals: Tuple[InteractionContextSignal, ...] = ()

    def __post_init__(self) -> None:
        for field_name, maximum in (
            ("turn_id", 256),
            ("relationship_id", 256),
            ("persona_id", 256),
            ("persona_manifest_id", 256),
            ("user_message_id", 256),
            ("user_message", 200_000),
        ):
            object.__setattr__(
                self,
                field_name,
                _text(
                    getattr(self, field_name),
                    field_name,
                    maximum=maximum,
                ),
            )
        emotion_values = _unique_text(
            self.emotion_values,
            "emotion_value",
        )
        if not emotion_values or len(emotion_values) > 32:
            raise ValueError(
                "interaction context request requires 1-32 emotion values"
            )
        folded_values = tuple(item.casefold() for item in emotion_values)
        if len(folded_values) != len(set(folded_values)):
            raise ValueError("emotion values must be case-insensitively unique")
        object.__setattr__(self, "emotion_values", emotion_values)

        if set(self.relationship_state) != set(RELATIONSHIP_DIMENSIONS):
            raise ValueError(
                "relationship_state must contain every relationship dimension"
            )
        state = {}
        for dimension in RELATIONSHIP_DIMENSIONS:
            value = self.relationship_state[dimension]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("relationship state values must be numeric")
            numeric = float(value)
            if not 0.0 <= numeric <= 1.0:
                raise ValueError(
                    "relationship state values must remain within 0.0-1.0"
                )
            state[dimension] = numeric
        object.__setattr__(
            self,
            "relationship_state",
            MappingProxyType(state),
        )

        events = tuple(
            item
            if isinstance(item, RelationshipEvent)
            else RelationshipEvent.from_dict(item)
            for item in self.recent_events
        )
        if len(events) > 16:
            raise ValueError(
                "interaction context request accepts at most 16 recent events"
            )
        event_ids = tuple(item.event_id for item in events)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("recent relationship events must be unique")
        if any(
            item.relationship_id != self.relationship_id
            for item in events
        ):
            raise ValueError(
                "recent events must belong to the evaluated relationship"
            )
        object.__setattr__(self, "recent_events", events)

        host_signals = tuple(
            item
            if isinstance(item, InteractionContextSignal)
            else InteractionContextSignal.from_dict(item)
            for item in self.host_observed_signals
        )
        signal_ids = tuple(item.signal_id for item in host_signals)
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("host-observed context signals must be unique")
        if any(
            item.source != ContextSignalSource.HOST_OBSERVED
            for item in host_signals
        ):
            raise ValueError(
                "emotion evaluation accepts only host_observed context input"
            )
        if any(
            item.relationship_id is not None
            and (
                item.relationship_id != self.relationship_id
                or item.source_turn_id != self.turn_id
            )
            for item in host_signals
        ):
            raise ValueError(
                "scoped host-observed signals must belong to the evaluated "
                "relationship and Turn"
            )
        object.__setattr__(
            self,
            "host_observed_signals",
            host_signals,
        )

    @property
    def user_message_evidence_ref(self) -> str:
        return f"turn-message:{self.user_message_id}"

    @property
    def allowed_evidence_refs(self) -> Tuple[str, ...]:
        return (
            self.user_message_evidence_ref,
            *(
                f"relationship-event:{item.event_id}"
                for item in self.recent_events
            ),
            *(
                f"host-signal:{item.signal_id}"
                for item in self.host_observed_signals
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "relationship_id": self.relationship_id,
            "persona_id": self.persona_id,
            "persona_manifest_id": self.persona_manifest_id,
            "user_message_id": self.user_message_id,
            "user_message": self.user_message,
            "emotion_values": list(self.emotion_values),
            "relationship_state": dict(self.relationship_state),
            "recent_events": [
                item.to_dict()
                for item in self.recent_events
            ],
            "host_observed_signals": [
                item.to_dict()
                for item in self.host_observed_signals
            ],
        }


class InteractionContextEvaluatorV1(Protocol):
    """Host seam for independently inferring current-turn emotions."""

    descriptor: InteractionContextEvaluatorDescriptor

    def evaluate(
        self,
        request: InteractionContextEvaluationRequest,
    ) -> Union[InteractionContextEvaluationDecision, Mapping[str, Any]]:
        """Returns strict sourced emotion signals or an explicit no-signal."""


@dataclass(frozen=True)
class VoicePatternActivation:
    """Temporary, deterministic projection of one matched expression register."""

    activation_id: str
    relationship_id: str
    source_turn_id: str
    persona_id: str
    manifest_id: str
    pattern_id: str
    pattern_scope: PersonaScope
    matcher_version: str
    supporting_signal_ids: Tuple[str, ...]
    condition_ids: Tuple[str, ...]
    input_fingerprint: str

    def __post_init__(self) -> None:
        for field_name in (
            "activation_id",
            "relationship_id",
            "source_turn_id",
            "persona_id",
            "manifest_id",
            "pattern_id",
            "matcher_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name, maximum=256),
            )
        if not isinstance(self.pattern_scope, PersonaScope):
            object.__setattr__(
                self,
                "pattern_scope",
                PersonaScope(self.pattern_scope),
            )
        signals = _unique_text(
            self.supporting_signal_ids,
            "supporting_signal_id",
        )
        conditions = _unique_text(self.condition_ids, "condition_id")
        if not signals or len(signals) != len(conditions):
            raise ValueError(
                "an activation requires one supporting signal per condition"
            )
        object.__setattr__(self, "supporting_signal_ids", signals)
        object.__setattr__(self, "condition_ids", conditions)
        fingerprint = _text(
            self.input_fingerprint,
            "input_fingerprint",
            maximum=64,
        ).lower()
        if not _HEX_64.fullmatch(fingerprint):
            raise ValueError("input_fingerprint must be a SHA-256 digest")
        object.__setattr__(self, "input_fingerprint", fingerprint)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "activation_id": self.activation_id,
            "relationship_id": self.relationship_id,
            "source_turn_id": self.source_turn_id,
            "persona_id": self.persona_id,
            "manifest_id": self.manifest_id,
            "pattern_id": self.pattern_id,
            "pattern_scope": self.pattern_scope.value,
            "matcher_version": self.matcher_version,
            "supporting_signal_ids": list(self.supporting_signal_ids),
            "condition_ids": list(self.condition_ids),
            "input_fingerprint": self.input_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VoicePatternActivation":
        required = {
            "activation_id",
            "relationship_id",
            "source_turn_id",
            "persona_id",
            "manifest_id",
            "pattern_id",
            "pattern_scope",
            "matcher_version",
            "supporting_signal_ids",
            "condition_ids",
            "input_fingerprint",
        }
        if set(data) != required:
            raise ValueError("VoicePatternActivation contains unknown or missing fields")
        return cls(
            activation_id=data["activation_id"],
            relationship_id=data["relationship_id"],
            source_turn_id=data["source_turn_id"],
            persona_id=data["persona_id"],
            manifest_id=data["manifest_id"],
            pattern_id=data["pattern_id"],
            pattern_scope=PersonaScope(data["pattern_scope"]),
            matcher_version=data["matcher_version"],
            supporting_signal_ids=tuple(data["supporting_signal_ids"]),
            condition_ids=tuple(data["condition_ids"]),
            input_fingerprint=data["input_fingerprint"],
        )


@dataclass(frozen=True)
class ContinuityEvaluationRequest:
    """Bounded pre-delivery input passed to a host evaluator."""

    turn_id: str
    relationship_id: str
    persona_id: str
    user_message: str
    proposed_reply: str
    persona_manifest_id: str
    persona_context_refs: Tuple[str, ...]
    relationship_context_refs: Tuple[str, ...] = ()
    voice_pattern_activations: Tuple[VoicePatternActivation, ...] = ()

    def __post_init__(self) -> None:
        for field_name, maximum in (
            ("turn_id", 256),
            ("relationship_id", 256),
            ("persona_id", 256),
            ("user_message", 200_000),
            ("proposed_reply", 200_000),
            ("persona_manifest_id", 256),
        ):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name, maximum=maximum),
            )
        persona_refs = _unique_text(
            self.persona_context_refs,
            "persona_context_ref",
        )
        if not persona_refs:
            raise ValueError("continuity evaluation requires approved persona context")
        relationship_refs = _unique_text(
            self.relationship_context_refs,
            "relationship_context_ref",
        )
        activations = tuple(
            item
            if isinstance(item, VoicePatternActivation)
            else VoicePatternActivation.from_dict(item)
            for item in self.voice_pattern_activations
        )
        activation_ids = tuple(item.activation_id for item in activations)
        if len(activation_ids) != len(set(activation_ids)):
            raise ValueError("voice pattern activations must be unique")
        if any(
            item.relationship_id != self.relationship_id
            or item.source_turn_id != self.turn_id
            or item.persona_id != self.persona_id
            or item.manifest_id != self.persona_manifest_id
            for item in activations
        ):
            raise ValueError(
                "voice pattern activations must belong to the evaluated Persona Instance"
            )
        object.__setattr__(self, "persona_context_refs", persona_refs)
        object.__setattr__(
            self,
            "relationship_context_refs",
            relationship_refs,
        )
        object.__setattr__(self, "voice_pattern_activations", activations)


class ContinuityEvaluatorV1(Protocol):
    """Host implementation seam; it proposes findings and never a verdict."""

    descriptor: ContinuityEvaluatorDescriptor

    def evaluate(
        self,
        request: ContinuityEvaluationRequest,
    ) -> Union[ContinuityEvaluationDecision, Mapping[str, Any]]:
        """Returns strict source-backed findings without rewriting the reply."""


@dataclass(frozen=True)
class ContinuityEvaluationResult:
    """Deterministically aggregated result safe to persist as a receipt."""

    assessment: ReplyContinuityAssessment
    findings: Tuple[ContinuityFinding, ...]
    evaluator_descriptor: ContinuityEvaluatorDescriptor
    aggregation_policy_version: str
    style_revision_advised: bool = False
    voice_pattern_activations: Tuple[VoicePatternActivation, ...] = ()

    def __post_init__(self) -> None:
        if self.assessment.status != ContinuityAssessmentStatus.COMPLETED:
            raise ValueError("continuity result requires a completed assessment")
        if self.assessment.verdict is None:
            raise ValueError("continuity result requires an aggregate verdict")
        object.__setattr__(
            self,
            "aggregation_policy_version",
            _descriptor_part(
                self.aggregation_policy_version,
                "aggregation_policy_version",
            ),
        )


__all__ = [
    "ContinuityAxis",
    "ContinuityEvaluationDecision",
    "ContinuityEvaluationRequest",
    "ContinuityEvaluationResult",
    "ContinuityEvaluatorDescriptor",
    "ContinuityEvaluatorV1",
    "ContinuityFinding",
    "ContinuityFindingAssessment",
    "ContinuityFindingSeverity",
    "ContinuityReasonCode",
    "EvaluatorInferredEmotionCandidate",
    "InteractionContextEvaluationDecision",
    "InteractionContextEvaluationRequest",
    "InteractionContextEvaluatorDescriptor",
    "InteractionContextEvaluatorV1",
    "InteractionContextNoSignalsDecision",
    "InteractionContextNoSignalsReason",
    "InteractionContextSignalsDecision",
    "VoicePatternActivation",
    "continuity_evaluation_decision_from_value",
    "interaction_context_evaluation_decision_from_value",
]
