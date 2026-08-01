"""Durable audit receipt for one delivered continuity-reviewed reply."""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from erii.models.continuity import (
    CONTINUITY_AGGREGATION_POLICY_V1_VERSION,
    ContinuityEvaluationDecision,
    ContinuityEvaluatorDescriptor,
    ContinuityFinding,
    ContinuityReviewBinding,
    _aggregate_continuity_decision_v1,
    _continuity_evaluator_descriptor_from_wire,
    _continuity_finding_from_wire,
    _continuity_style_revision_advised_v1,
    _reply_continuity_assessment_from_wire,
    _validate_voice_activation_traces,
)
from erii.models.turn import (
    ContinuityVerdict,
    DeliveryDisposition,
    ReplyContinuityAssessment,
)
from erii.models.voice_trace import VoiceActivationTrace


CONTINUITY_REVIEW_RECEIPT_VERSION = "continuity-review-receipt/v1"
CONTINUITY_REVIEW_RECORD_VERSION = "continuity-review-record/v1"
DELIVERY_EXCEPTION_RECORD_VERSION = "delivery-exception-record/v1"
_VERSION_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _required_version(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _VERSION_PART.fullmatch(value):
        raise ValueError(f"{field_name} is not a safe version identifier")
    return value


def _required_sequence(value: object, field_name: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return value


@dataclass(frozen=True)
class ContinuityReviewReceipt:
    """Minimal persistent explanation of one exact reviewed delivery."""

    review_binding: ContinuityReviewBinding
    delivery_disposition: DeliveryDisposition
    assessment: ReplyContinuityAssessment
    findings: Tuple[ContinuityFinding, ...]
    evaluator_descriptor: ContinuityEvaluatorDescriptor
    aggregation_policy_version: str
    style_revision_advised: bool = False
    voice_activation_traces: Tuple[VoiceActivationTrace, ...] = ()
    receipt_version: str = CONTINUITY_REVIEW_RECEIPT_VERSION

    def __post_init__(self) -> None:
        if self.receipt_version != CONTINUITY_REVIEW_RECEIPT_VERSION:
            raise ValueError("unsupported ContinuityReviewReceipt version")
        binding = self.review_binding
        if not isinstance(binding, ContinuityReviewBinding):
            binding = ContinuityReviewBinding.from_dict(binding)
            object.__setattr__(self, "review_binding", binding)
        disposition = self.delivery_disposition
        if not isinstance(disposition, DeliveryDisposition):
            disposition = DeliveryDisposition(disposition)
            object.__setattr__(self, "delivery_disposition", disposition)
        assessment = self.assessment
        if not isinstance(assessment, ReplyContinuityAssessment):
            assessment = ReplyContinuityAssessment.from_dict(assessment)
            object.__setattr__(self, "assessment", assessment)
        descriptor = self.evaluator_descriptor
        if not isinstance(descriptor, ContinuityEvaluatorDescriptor):
            descriptor = ContinuityEvaluatorDescriptor.from_dict(descriptor)
            object.__setattr__(self, "evaluator_descriptor", descriptor)
        policy_version = _required_version(
            self.aggregation_policy_version,
            "aggregation_policy_version",
        )
        if policy_version != CONTINUITY_AGGREGATION_POLICY_V1_VERSION:
            raise ValueError("unsupported continuity aggregation policy version")
        object.__setattr__(self, "aggregation_policy_version", policy_version)
        expected_evaluator_version = (
            f"{descriptor.public_version}+{policy_version}"
        )
        if assessment.evaluator_version != expected_evaluator_version:
            raise ValueError(
                "assessment evaluator version does not match the receipt descriptors"
            )
        allowed_dispositions = {
            ContinuityVerdict.ALIGNED: DeliveryDisposition.SHOWN,
            ContinuityVerdict.SUPPORTED_NEW_CHOICE: DeliveryDisposition.SHOWN,
            ContinuityVerdict.REVIEW_REQUIRED: DeliveryDisposition.OVERRIDDEN,
            ContinuityVerdict.UNSUPPORTED_DRIFT: DeliveryDisposition.OVERRIDDEN,
        }
        if allowed_dispositions[assessment.verdict] != disposition:
            raise ValueError(
                "delivery disposition is incompatible with the reviewed verdict"
            )

        decision = ContinuityEvaluationDecision(findings=tuple(self.findings))
        if assessment.verdict != _aggregate_continuity_decision_v1(decision):
            raise ValueError("continuity assessment conflicts with its findings")
        if not isinstance(self.style_revision_advised, bool):
            raise ValueError("style_revision_advised must be a boolean")
        if self.style_revision_advised != _continuity_style_revision_advised_v1(
            decision
        ):
            raise ValueError("continuity style advisory conflicts with its findings")
        if any(item.reply_end > binding.reply_length for item in decision.findings):
            raise ValueError("continuity finding lies outside the delivered reply")
        allowed_refs = set(binding.allowed_evidence_refs)
        unknown_refs = {
            reference
            for item in decision.findings
            for reference in (
                *item.supporting_basis_refs,
                *item.conflicting_source_refs,
            )
            if reference not in allowed_refs
        }
        if unknown_refs:
            raise ValueError(
                "continuity receipt cites evidence outside its review binding: "
                + ", ".join(sorted(unknown_refs))
            )
        object.__setattr__(self, "findings", decision.findings)

        traces = _validate_voice_activation_traces(
            binding,
            decision.findings,
            self.voice_activation_traces,
        )
        object.__setattr__(self, "voice_activation_traces", traces)

    @property
    def relationship_id(self) -> str:
        return self.review_binding.relationship_id

    @property
    def turn_id(self) -> str:
        return self.review_binding.turn_id

    @property
    def reply_sha256(self) -> str:
        return self.review_binding.reply_sha256

    @property
    def reply_length(self) -> int:
        return self.review_binding.reply_length

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_version": self.receipt_version,
            "review_binding": self.review_binding.to_dict(),
            "delivery_disposition": self.delivery_disposition.value,
            "assessment": self.assessment.to_dict(),
            "findings": [item.model_dump(mode="json") for item in self.findings],
            "evaluator_descriptor": self.evaluator_descriptor.to_dict(),
            "aggregation_policy_version": self.aggregation_policy_version,
            "style_revision_advised": self.style_revision_advised,
            "voice_activation_traces": [
                item.to_dict() for item in self.voice_activation_traces
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContinuityReviewReceipt":
        if not isinstance(data, Mapping):
            raise ValueError("ContinuityReviewReceipt must be a mapping")
        required = {
            "receipt_version",
            "review_binding",
            "delivery_disposition",
            "assessment",
            "findings",
            "evaluator_descriptor",
            "aggregation_policy_version",
            "style_revision_advised",
            "voice_activation_traces",
        }
        if set(data) != required:
            raise ValueError(
                "ContinuityReviewReceipt contains unknown or missing fields"
            )
        return cls(
            receipt_version=data["receipt_version"],
            review_binding=ContinuityReviewBinding.from_dict(
                data["review_binding"]
            ),
            delivery_disposition=data["delivery_disposition"],
            assessment=_reply_continuity_assessment_from_wire(data["assessment"]),
            findings=tuple(
                _continuity_finding_from_wire(item)
                for item in _required_sequence(data["findings"], "findings")
            ),
            evaluator_descriptor=_continuity_evaluator_descriptor_from_wire(
                data["evaluator_descriptor"]
            ),
            aggregation_policy_version=data["aggregation_policy_version"],
            style_revision_advised=data["style_revision_advised"],
            voice_activation_traces=tuple(
                VoiceActivationTrace.from_dict(item)
                for item in _required_sequence(
                    data["voice_activation_traces"],
                    "voice_activation_traces",
                )
            ),
        )


class ContinuityReviewKind(str, Enum):
    """Exactly one durable review state for a completed Turn."""

    REVIEWED = "reviewed"
    NOT_EVALUATED = "not_evaluated"
    FAILED = "failed"
    LEGACY_UNAVAILABLE = "legacy_unavailable"


class ContinuityNotEvaluatedReason(str, Enum):
    """Bounded reasons why a modern continuity review did not run."""

    EVALUATOR_UNCONFIGURED = "evaluator_unconfigured"
    EVALUATION_NOT_REQUESTED = "evaluation_not_requested"
    PREEXISTING_VISIBLE_EXCHANGE = "preexisting_visible_exchange"
    CONTINUITY_AUTHORITY_UNAVAILABLE = "continuity_authority_unavailable"
    LEGACY_OPEN_WITHOUT_CONTEXT_BASELINE = "legacy_open_without_context_baseline"


class ContinuityFailureClassification(str, Enum):
    """Sanitized technical classes for an attempted review failure."""

    EVALUATOR_UNAVAILABLE = "evaluator_unavailable"
    EVALUATOR_FAILED = "evaluator_failed"
    INVALID_EVALUATOR_OUTPUT = "invalid_evaluator_output"
    REVIEW_BINDING_MISMATCH = "review_binding_mismatch"
    AUTHORITY_REVOKED = "authority_revoked"


class DeliveryExceptionActorKind(str, Enum):
    """Declared source of one host delivery exception decision."""

    HOST_POLICY = "host_policy"
    HUMAN_OPERATOR = "human_operator"
    DATA_OWNER = "data_owner"


class DeliveryExceptionReasonCode(str, Enum):
    """Affective-neutral reasons why a host still displayed a reply."""

    AVAILABILITY_FALLBACK = "availability_fallback"
    CONFIGURED_DELIVERY_POLICY = "configured_delivery_policy"
    OUT_OF_BAND_JUDGMENT = "out_of_band_judgment"
    PREEXISTING_VISIBLE_EXCHANGE = "preexisting_visible_exchange"
    LEGACY_TURN_COMPLETION = "legacy_turn_completion"


@dataclass(frozen=True)
class DeliveryExceptionRecord:
    """Portable declaration that an exceptional delivery was explicit."""

    disposition: DeliveryDisposition
    actor_kind: DeliveryExceptionActorKind
    actor_id: str
    reason_code: DeliveryExceptionReasonCode
    decided_at: str
    reply_attempt_number: Optional[int] = None
    exception_record_version: str = DELIVERY_EXCEPTION_RECORD_VERSION

    def __post_init__(self) -> None:
        if self.exception_record_version != DELIVERY_EXCEPTION_RECORD_VERSION:
            raise ValueError("unsupported DeliveryExceptionRecord version")
        disposition = self.disposition
        if not isinstance(disposition, DeliveryDisposition):
            disposition = DeliveryDisposition(disposition)
            object.__setattr__(self, "disposition", disposition)
        if disposition not in {
            DeliveryDisposition.OVERRIDDEN,
            DeliveryDisposition.SHOWN_UNREVIEWED,
        }:
            raise ValueError("ordinary shown delivery cannot have an exception record")
        actor = self.actor_kind
        if not isinstance(actor, DeliveryExceptionActorKind):
            actor = DeliveryExceptionActorKind(actor)
            object.__setattr__(self, "actor_kind", actor)
        reason = self.reason_code
        if not isinstance(reason, DeliveryExceptionReasonCode):
            reason = DeliveryExceptionReasonCode(reason)
            object.__setattr__(self, "reason_code", reason)
        object.__setattr__(self, "actor_id", _required_version(self.actor_id, "actor_id"))
        if not isinstance(self.decided_at, str) or not self.decided_at.strip():
            raise ValueError("decided_at must be a non-empty timestamp")
        attempt = self.reply_attempt_number
        if attempt is not None and (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt < 1
        ):
            raise ValueError("reply_attempt_number must be a positive integer")

        if disposition == DeliveryDisposition.OVERRIDDEN and reason not in {
            DeliveryExceptionReasonCode.AVAILABILITY_FALLBACK,
            DeliveryExceptionReasonCode.CONFIGURED_DELIVERY_POLICY,
            DeliveryExceptionReasonCode.OUT_OF_BAND_JUDGMENT,
        }:
            raise ValueError("overridden delivery has an illegal reason code")
        if (
            reason == DeliveryExceptionReasonCode.CONFIGURED_DELIVERY_POLICY
            and actor != DeliveryExceptionActorKind.HOST_POLICY
        ):
            raise ValueError(
                "configured_delivery_policy requires a host_policy actor"
            )
        if (
            reason == DeliveryExceptionReasonCode.OUT_OF_BAND_JUDGMENT
            and actor
            not in {
                DeliveryExceptionActorKind.HUMAN_OPERATOR,
                DeliveryExceptionActorKind.DATA_OWNER,
            }
        ):
            raise ValueError(
                "out_of_band_judgment requires a human_operator or data_owner"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exception_record_version": self.exception_record_version,
            "disposition": self.disposition.value,
            "actor_kind": self.actor_kind.value,
            "actor_id": self.actor_id,
            "reason_code": self.reason_code.value,
            "decided_at": self.decided_at,
            "reply_attempt_number": self.reply_attempt_number,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DeliveryExceptionRecord":
        if not isinstance(data, Mapping):
            raise ValueError("DeliveryExceptionRecord must be a mapping")
        required = {
            "exception_record_version",
            "disposition",
            "actor_kind",
            "actor_id",
            "reason_code",
            "decided_at",
            "reply_attempt_number",
        }
        if set(data) != required:
            raise ValueError(
                "DeliveryExceptionRecord contains unknown or missing fields"
            )
        return cls(
            exception_record_version=data["exception_record_version"],
            disposition=data["disposition"],
            actor_kind=data["actor_kind"],
            actor_id=data["actor_id"],
            reason_code=data["reason_code"],
            decided_at=data["decided_at"],
            reply_attempt_number=data["reply_attempt_number"],
        )


@dataclass(frozen=True)
class ContinuityReviewRecord:
    """Strict discriminated union for one completed Turn's review truth."""

    kind: ContinuityReviewKind
    receipt: Optional[ContinuityReviewReceipt] = None
    reason_code: Optional[ContinuityNotEvaluatedReason] = None
    failure_classification: Optional[ContinuityFailureClassification] = None
    evaluator_descriptor: Optional[ContinuityEvaluatorDescriptor] = None
    reply_attempt_number: Optional[int] = None
    legacy_summary: Optional[ReplyContinuityAssessment] = None
    review_record_version: str = CONTINUITY_REVIEW_RECORD_VERSION

    def __post_init__(self) -> None:
        if self.review_record_version != CONTINUITY_REVIEW_RECORD_VERSION:
            raise ValueError("unsupported ContinuityReviewRecord version")
        kind = self.kind
        if not isinstance(kind, ContinuityReviewKind):
            kind = ContinuityReviewKind(kind)
            object.__setattr__(self, "kind", kind)

        receipt = self.receipt
        if receipt is not None and not isinstance(receipt, ContinuityReviewReceipt):
            receipt = ContinuityReviewReceipt.from_dict(receipt)
            object.__setattr__(self, "receipt", receipt)
        reason = self.reason_code
        if reason is not None and not isinstance(reason, ContinuityNotEvaluatedReason):
            reason = ContinuityNotEvaluatedReason(reason)
            object.__setattr__(self, "reason_code", reason)
        failure = self.failure_classification
        if failure is not None and not isinstance(
            failure,
            ContinuityFailureClassification,
        ):
            failure = ContinuityFailureClassification(failure)
            object.__setattr__(self, "failure_classification", failure)
        descriptor = self.evaluator_descriptor
        if descriptor is not None and not isinstance(
            descriptor,
            ContinuityEvaluatorDescriptor,
        ):
            descriptor = ContinuityEvaluatorDescriptor.from_dict(descriptor)
            object.__setattr__(self, "evaluator_descriptor", descriptor)
        attempt = self.reply_attempt_number
        if attempt is not None and (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt < 1
        ):
            raise ValueError("reply_attempt_number must be a positive integer")
        legacy = self.legacy_summary
        if legacy is not None and not isinstance(legacy, ReplyContinuityAssessment):
            legacy = ReplyContinuityAssessment.from_dict(legacy)
            object.__setattr__(self, "legacy_summary", legacy)

        populated = {
            "receipt": receipt is not None,
            "reason_code": reason is not None,
            "failure_classification": failure is not None,
            "evaluator_descriptor": descriptor is not None,
            "reply_attempt_number": attempt is not None,
            "legacy_summary": legacy is not None,
        }
        if kind == ContinuityReviewKind.REVIEWED:
            if not populated["receipt"] or any(
                populated[name]
                for name in populated
                if name != "receipt"
            ):
                raise ValueError("reviewed ContinuityReviewRecord requires only receipt")
        elif kind == ContinuityReviewKind.NOT_EVALUATED:
            if not populated["reason_code"] or any(
                populated[name]
                for name in populated
                if name != "reason_code"
            ):
                raise ValueError(
                    "not_evaluated ContinuityReviewRecord requires only reason_code"
                )
        elif kind == ContinuityReviewKind.FAILED:
            if not populated["failure_classification"] or any(
                populated[name]
                for name in ("receipt", "reason_code", "legacy_summary")
            ):
                raise ValueError(
                    "failed ContinuityReviewRecord has an illegal branch combination"
                )
        elif any(
            populated[name]
            for name in (
                "receipt",
                "reason_code",
                "failure_classification",
                "evaluator_descriptor",
                "reply_attempt_number",
            )
        ):
            raise ValueError(
                "legacy_unavailable ContinuityReviewRecord may contain only legacy_summary"
            )

    @property
    def assessment(self) -> Optional[ReplyContinuityAssessment]:
        """Returns the deprecated modern summary view, fail-closed for Legacy."""
        if self.kind == ContinuityReviewKind.REVIEWED:
            return self.receipt.assessment
        if self.kind == ContinuityReviewKind.NOT_EVALUATED:
            return ReplyContinuityAssessment()
        if self.kind == ContinuityReviewKind.FAILED:
            return ReplyContinuityAssessment(
                status="failed",
                evaluator_version=(
                    self.evaluator_descriptor.public_version
                    if self.evaluator_descriptor is not None
                    else None
                ),
            )
        return None

    @classmethod
    def reviewed(cls, receipt: ContinuityReviewReceipt) -> "ContinuityReviewRecord":
        return cls(kind=ContinuityReviewKind.REVIEWED, receipt=receipt)

    @classmethod
    def not_evaluated(
        cls,
        reason_code: ContinuityNotEvaluatedReason,
    ) -> "ContinuityReviewRecord":
        return cls(
            kind=ContinuityReviewKind.NOT_EVALUATED,
            reason_code=reason_code,
        )

    @classmethod
    def failed(
        cls,
        failure_classification: ContinuityFailureClassification,
        *,
        evaluator_descriptor: Optional[ContinuityEvaluatorDescriptor] = None,
        reply_attempt_number: Optional[int] = None,
    ) -> "ContinuityReviewRecord":
        return cls(
            kind=ContinuityReviewKind.FAILED,
            failure_classification=failure_classification,
            evaluator_descriptor=evaluator_descriptor,
            reply_attempt_number=reply_attempt_number,
        )

    @classmethod
    def legacy_unavailable(
        cls,
        legacy_summary: Optional[ReplyContinuityAssessment] = None,
    ) -> "ContinuityReviewRecord":
        return cls(
            kind=ContinuityReviewKind.LEGACY_UNAVAILABLE,
            legacy_summary=legacy_summary,
        )

    def to_dict(self) -> Dict[str, Any]:
        common = {
            "review_record_version": self.review_record_version,
            "kind": self.kind.value,
        }
        if self.kind == ContinuityReviewKind.REVIEWED:
            return {**common, "receipt": self.receipt.to_dict()}
        if self.kind == ContinuityReviewKind.NOT_EVALUATED:
            return {**common, "reason_code": self.reason_code.value}
        if self.kind == ContinuityReviewKind.FAILED:
            return {
                **common,
                "failure_classification": self.failure_classification.value,
                "evaluator_descriptor": (
                    self.evaluator_descriptor.to_dict()
                    if self.evaluator_descriptor is not None
                    else None
                ),
                "reply_attempt_number": self.reply_attempt_number,
            }
        return {
            **common,
            "legacy_summary": (
                self.legacy_summary.to_dict()
                if self.legacy_summary is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContinuityReviewRecord":
        if not isinstance(data, Mapping):
            raise ValueError("ContinuityReviewRecord must be a mapping")
        common = {"review_record_version", "kind"}
        if not common.issubset(data):
            raise ValueError("ContinuityReviewRecord is missing its version or kind")
        kind = ContinuityReviewKind(data["kind"])
        branch_fields = {
            ContinuityReviewKind.REVIEWED: {"receipt"},
            ContinuityReviewKind.NOT_EVALUATED: {"reason_code"},
            ContinuityReviewKind.FAILED: {
                "failure_classification",
                "evaluator_descriptor",
                "reply_attempt_number",
            },
            ContinuityReviewKind.LEGACY_UNAVAILABLE: {"legacy_summary"},
        }[kind]
        if set(data) != common | branch_fields:
            raise ValueError(
                "ContinuityReviewRecord contains unknown, missing, or cross-branch fields"
            )
        if kind == ContinuityReviewKind.REVIEWED:
            return cls(
                review_record_version=data["review_record_version"],
                kind=kind,
                receipt=ContinuityReviewReceipt.from_dict(data["receipt"]),
            )
        if kind == ContinuityReviewKind.NOT_EVALUATED:
            return cls(
                review_record_version=data["review_record_version"],
                kind=kind,
                reason_code=data["reason_code"],
            )
        if kind == ContinuityReviewKind.FAILED:
            return cls(
                review_record_version=data["review_record_version"],
                kind=kind,
                failure_classification=data["failure_classification"],
                evaluator_descriptor=(
                    _continuity_evaluator_descriptor_from_wire(
                        data["evaluator_descriptor"]
                    )
                    if data["evaluator_descriptor"] is not None
                    else None
                ),
                reply_attempt_number=data["reply_attempt_number"],
            )
        return cls(
            review_record_version=data["review_record_version"],
            kind=kind,
            legacy_summary=(
                _reply_continuity_assessment_from_wire(data["legacy_summary"])
                if data["legacy_summary"] is not None
                else None
            ),
        )


__all__ = [
    "CONTINUITY_REVIEW_RECEIPT_VERSION",
    "CONTINUITY_REVIEW_RECORD_VERSION",
    "DELIVERY_EXCEPTION_RECORD_VERSION",
    "ContinuityFailureClassification",
    "ContinuityNotEvaluatedReason",
    "ContinuityReviewKind",
    "ContinuityReviewRecord",
    "ContinuityReviewReceipt",
    "DeliveryExceptionActorKind",
    "DeliveryExceptionReasonCode",
    "DeliveryExceptionRecord",
]
