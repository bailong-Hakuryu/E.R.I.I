"""Fail-closed conversion of provider JSON to E.R.I.I. continuity findings."""

import hashlib
import json
from typing import Any, Mapping, Sequence

from erii.models.continuity import (
    ContinuityAxis,
    ContinuityEvaluationDecision,
    ContinuityEvaluationRequest,
    ContinuityFinding,
    ContinuityFindingAssessment,
    ContinuityFindingSeverity,
    ContinuityReasonCode,
)

from .evidence_resolver import ResolvedEvidence, ResolvedVoiceActivation
from .span_calculator import SpanCalculationError, calculate_span

_REQUIRED_AXES = frozenset(ContinuityAxis)
_REQUIRED_FINDING_FIELDS = frozenset(
    {
        "axis",
        "assessment",
        "severity",
        "reason_code",
        "reply_quote",
    }
)
_OPTIONAL_FINDING_FIELDS = frozenset(
    {
        "occurrence",
        "supporting_basis_refs",
        "conflicting_source_refs",
        "voice_activation_refs",
    }
)


def parse_to_decision(
    response: Mapping[str, Any],
    request: ContinuityEvaluationRequest,
    resolved_evidence: Sequence[ResolvedEvidence],
    resolved_activations: Sequence[ResolvedVoiceActivation],
) -> ContinuityEvaluationDecision:
    """Parse one complete provider response without retaining raw provider data."""
    if response.get("finish_reason") != "stop":
        raise ParsingError("incomplete_response") from None
    content = response.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ParsingError("empty_response_content") from None
    parse_failed = False
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        parse_failed = True
        data = None
    if parse_failed:
        raise ParsingError("invalid_json")
    if not isinstance(data, dict) or set(data) != {"findings"}:
        raise ParsingError("invalid_response_contract") from None
    raw_findings = data["findings"]
    if not isinstance(raw_findings, list):
        raise ParsingError("findings_must_be_list") from None
    if len(raw_findings) != len(_REQUIRED_AXES):
        raise ParsingError("wrong_finding_count") from None

    available_refs = {item.ref_id for item in resolved_evidence}
    available_activation_ids = {
        activation.activation_id for activation in resolved_activations
    }

    findings: list[ContinuityFinding] = []
    axes_seen: set[ContinuityAxis] = set()
    for raw_finding in raw_findings:
        finding = _parse_finding(
            raw_finding,
            request,
            available_refs,
            available_activation_ids,
            axes_seen,
        )
        findings.append(finding)
        axes_seen.add(finding.axis)

    if axes_seen != _REQUIRED_AXES:
        raise ParsingError("missing_or_unknown_axis") from None
    decision_error = False
    try:
        decision = ContinuityEvaluationDecision(findings=tuple(findings))
    except (TypeError, ValueError):
        decision_error = True
        decision = None
    if decision_error:
        raise ParsingError("invalid_decision_contract")
    return decision


def _parse_finding(
    raw_finding: object,
    request: ContinuityEvaluationRequest,
    available_refs: set[str],
    available_activation_ids: set[str],
    axes_seen: set[ContinuityAxis],
) -> ContinuityFinding:
    if not isinstance(raw_finding, dict):
        raise ParsingError("finding_must_be_object") from None
    keys = set(raw_finding)
    if not _REQUIRED_FINDING_FIELDS.issubset(keys) or not keys.issubset(
        _REQUIRED_FINDING_FIELDS | _OPTIONAL_FINDING_FIELDS
    ):
        raise ParsingError("invalid_finding_fields") from None

    scalar_fields = (
        raw_finding["axis"],
        raw_finding["assessment"],
        raw_finding["severity"],
        raw_finding["reason_code"],
        raw_finding["reply_quote"],
    )
    if any(not isinstance(value, str) for value in scalar_fields):
        raise ParsingError("invalid_finding_scalar") from None
    enum_error = False
    try:
        axis = ContinuityAxis(raw_finding["axis"])
        assessment = ContinuityFindingAssessment(raw_finding["assessment"])
        severity = ContinuityFindingSeverity(raw_finding["severity"])
        reason_code = ContinuityReasonCode(raw_finding["reason_code"])
    except ValueError:
        enum_error = True
    if enum_error:
        raise ParsingError("invalid_enum_value")
    if axis in axes_seen:
        raise ParsingError("duplicate_axis") from None

    occurrence = raw_finding.get("occurrence", 0)
    if (
        not isinstance(occurrence, int)
        or isinstance(occurrence, bool)
        or occurrence < 0
    ):
        raise ParsingError("invalid_occurrence") from None
    span_error = False
    try:
        span_result = calculate_span(
            proposed_reply=request.proposed_reply,
            reply_quote=raw_finding["reply_quote"],
            occurrence=occurrence,
        )
    except SpanCalculationError:
        span_error = True
        span_result = None
    if span_error:
        raise ParsingError("span_calculation_failed")

    supporting_refs = _validated_ref_list(
        raw_finding.get("supporting_basis_refs", []),
        available_refs,
        "invalid_supporting_refs",
    )
    conflicting_refs = _validated_ref_list(
        raw_finding.get("conflicting_source_refs", []),
        available_refs,
        "invalid_conflicting_refs",
    )
    voice_refs = _validated_ref_list(
        raw_finding.get("voice_activation_refs", []),
        available_activation_ids,
        "invalid_voice_refs",
    )

    turn_digest = hashlib.sha256(request.turn_id.encode("utf-8")).hexdigest()[:24]
    finding_error = False
    try:
        finding = ContinuityFinding(
            finding_id=f"deepseek-{turn_digest}-{axis.value}",
            axis=axis,
            assessment=assessment,
            severity=severity,
            reason_code=reason_code,
            reply_start=span_result.reply_start,
            reply_end=span_result.reply_end,
            reply_quote=span_result.reply_quote,
            supporting_basis_refs=supporting_refs,
            conflicting_source_refs=conflicting_refs,
            voice_activation_refs=voice_refs,
        )
    except (TypeError, ValueError):
        finding_error = True
        finding = None
    if finding_error:
        raise ParsingError("invalid_finding_contract")
    return finding


def _validated_ref_list(
    value: object,
    available_ids: set[str],
    error_code: str,
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ParsingError(error_code) from None
    if any(item not in available_ids for item in value):
        raise ParsingError(error_code) from None
    return tuple(value)


class ParsingError(Exception):
    """Response parsing failed without embedding provider-controlled content."""

    pass
