"""Build a persistent continuity receipt for one exact delivered reply."""

from typing import Mapping

from erii.core.continuity import ContinuityAggregationPolicyV1
from erii.models.continuity import (
    ContinuityEvaluationDecision,
    ContinuityEvaluationResult,
    ContinuityEvaluatorDescriptor,
    ContinuityFinding,
)
from erii.models.continuity_review import ContinuityReviewReceipt
from erii.models.turn import DeliveryDisposition


def build_continuity_review_receipt(
    result: ContinuityEvaluationResult,
    delivered_reply: str,
    delivery_disposition: DeliveryDisposition,
) -> ContinuityReviewReceipt:
    """Bind an evaluated draft to the exact reply the host actually delivered."""
    if not isinstance(result, ContinuityEvaluationResult):
        raise TypeError("result must be a ContinuityEvaluationResult")
    if not isinstance(delivered_reply, str) or not delivered_reply:
        raise ValueError("delivered_reply must be a non-empty string")
    binding = result.review_binding
    binding.verify_reply(delivered_reply)

    decision = ContinuityEvaluationDecision(findings=tuple(result.findings))
    if (
        result.aggregation_policy_version
        != ContinuityAggregationPolicyV1.VERSION
    ):
        raise ValueError("unsupported continuity aggregation policy version")
    if result.assessment.verdict != ContinuityAggregationPolicyV1.aggregate(
        decision
    ):
        raise ValueError("continuity assessment conflicts with its findings")
    if result.style_revision_advised != (
        ContinuityAggregationPolicyV1.style_revision_advised(decision)
    ):
        raise ValueError("continuity style advisory conflicts with its finding")
    descriptor = result.evaluator_descriptor
    if not isinstance(descriptor, ContinuityEvaluatorDescriptor):
        if not isinstance(descriptor, Mapping):
            raise TypeError("result requires a ContinuityEvaluatorDescriptor")
        descriptor = ContinuityEvaluatorDescriptor.from_dict(descriptor)
    allowed_refs = set(binding.allowed_evidence_refs)
    for finding in decision.findings:
        _validate_finding(finding, delivered_reply, allowed_refs)

    return ContinuityReviewReceipt(
        review_binding=binding,
        delivery_disposition=delivery_disposition,
        assessment=result.assessment,
        findings=decision.findings,
        evaluator_descriptor=descriptor,
        aggregation_policy_version=result.aggregation_policy_version,
        style_revision_advised=result.style_revision_advised,
        voice_activation_traces=result.voice_activation_traces,
    )


def _validate_finding(
    finding: ContinuityFinding,
    delivered_reply: str,
    allowed_refs: set,
) -> None:
    if (
        finding.reply_end > len(delivered_reply)
        or delivered_reply[finding.reply_start : finding.reply_end]
        != finding.reply_quote
    ):
        raise ValueError(
            "continuity finding reply span does not match the delivered reply"
        )
    unknown_refs = {
        *finding.supporting_basis_refs,
        *finding.conflicting_source_refs,
    }.difference(allowed_refs)
    if unknown_refs:
        raise ValueError(
            "continuity finding cites context outside the evaluated request: "
            + ", ".join(sorted(unknown_refs))
        )


__all__ = ["build_continuity_review_receipt"]
