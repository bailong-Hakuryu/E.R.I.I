"""Response parser: DeepSeek response → ContinuityEvaluationDecision.

Key features:
- Constructs real ContinuityFinding objects
- Uses span_calculator for deterministic span computation
- Validates all constraints (5 axes, valid enums, sources, etc.)
- Fails closed on missing fields, unknown values, or invalid combinations
- Returns real ContinuityEvaluationDecision
"""

import json
from typing import Any, Mapping, Sequence
from erii.models.continuity import (
    ContinuityEvaluationRequest,
    ContinuityEvaluationDecision,
    ContinuityFinding,
    ContinuityAxis,
    ContinuityFindingAssessment,
    ContinuityReasonCode,
    ContinuityFindingSeverity,
)
from .evidence_resolver import ResolvedEvidence, ResolvedVoiceActivation
from .span_calculator import calculate_span, SpanCalculationError


def parse_to_decision(
    response: Mapping[str, Any],
    request: ContinuityEvaluationRequest,
    resolved_evidence: Sequence[ResolvedEvidence],
    resolved_activations: Sequence[ResolvedVoiceActivation],
) -> ContinuityEvaluationDecision:
    """
    Parse DeepSeek response to ContinuityEvaluationDecision.

    Validates:
    - Exactly 5 findings, one per axis
    - Valid enums (assessment, reason_code, severity)
    - Evidence refs exist in request
    - Voice activation refs exist in request
    - Reply spans are valid
    - All required fields present

    Fails closed on any validation error.
    """

    # Parse JSON
    try:
        data = json.loads(response["content"])
        raw_findings = data["findings"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise ParsingError("invalid_json")

    if not isinstance(raw_findings, list):
        raise ParsingError("findings_must_be_list")

    # Validate exactly 5 findings
    if len(raw_findings) != 5:
        raise ParsingError(
            f"expected_5_findings_got_{len(raw_findings)}"
        )

    # Build available refs set
    available_refs = {
        ref.ref_id
        for ref in (
            request.persona_context_refs + request.relationship_context_refs
        )
    }

    available_activation_ids = {
        act.activation_id for act in request.voice_pattern_activations
    }

    # Parse each finding
    findings = []
    axes_seen = set()

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

    # Validate all five axes present
    required_axes = {
        ContinuityAxis.IDENTITY_VALUES,
        ContinuityAxis.PSYCHOLOGICAL_CAUSALITY,
        ContinuityAxis.RELATIONSHIP_SCOPE,
        ContinuityAxis.KNOWLEDGE_MEMORY_SCOPE,
        ContinuityAxis.VOICE_STYLE,
    }

    if axes_seen != required_axes:
        missing = required_axes - axes_seen
        raise ParsingError(f"missing_axes_{[a.value for a in missing]}")

    # Construct real ContinuityEvaluationDecision
    return ContinuityEvaluationDecision(
        findings=tuple(findings),
    )


def _parse_finding(
    raw_finding: dict,
    request: ContinuityEvaluationRequest,
    available_refs: set[str],
    available_activation_ids: set[str],
    axes_seen: set[ContinuityAxis],
) -> ContinuityFinding:
    """Parse and validate one finding."""

    # Extract required fields (fail closed if missing)
    try:
        axis_str = raw_finding["axis"]
        assessment_str = raw_finding["assessment"]
        severity_str = raw_finding["severity"]
        reason_code_str = raw_finding["reason_code"]
        reply_quote = raw_finding["reply_quote"]
    except KeyError as exc:
        raise ParsingError(f"missing_required_field_{exc.args[0]}")

    # Validate enums
    try:
        axis = ContinuityAxis(axis_str)
        assessment = ContinuityFindingAssessment(assessment_str)
        severity = ContinuityFindingSeverity(severity_str)
        reason_code = ContinuityReasonCode(reason_code_str)
    except ValueError:
        raise ParsingError(f"invalid_enum_value")

    # Check for duplicate axis
    if axis in axes_seen:
        raise ParsingError(f"duplicate_axis_{axis.value}")

    # Get occurrence (optional, defaults to 0)
    occurrence = raw_finding.get("occurrence", 0)

    # Calculate span deterministically
    try:
        span_result = calculate_span(
            proposed_reply=request.proposed_reply,
            reply_quote=reply_quote,
            occurrence=occurrence,
        )
    except SpanCalculationError as exc:
        raise ParsingError(f"span_calculation_failed_{exc.args[0]}")

    # Validate evidence refs
    supporting_refs = raw_finding.get("supporting_basis_refs", [])
    conflicting_refs = raw_finding.get("conflicting_source_refs", [])
    voice_refs = raw_finding.get("voice_activation_refs", [])

    if not isinstance(supporting_refs, list):
        raise ParsingError("supporting_basis_refs_must_be_list")
    if not isinstance(conflicting_refs, list):
        raise ParsingError("conflicting_source_refs_must_be_list")
    if not isinstance(voice_refs, list):
        raise ParsingError("voice_activation_refs_must_be_list")

    # Validate all refs exist
    for ref_id in supporting_refs:
        if ref_id not in available_refs:
            raise ParsingError(f"unknown_evidence_ref_{ref_id}")

    for ref_id in conflicting_refs:
        if ref_id not in available_refs:
            raise ParsingError(f"unknown_evidence_ref_{ref_id}")

    for act_id in voice_refs:
        if act_id not in available_activation_ids:
            raise ParsingError(f"unknown_activation_id_{act_id}")

    # Generate finding_id
    finding_id = f"deepseek-{request.turn_id}-{axis.value}"

    # Construct real ContinuityFinding
    # Let Pydantic validate all constraints
    return ContinuityFinding(
        finding_id=finding_id,
        axis=axis,
        assessment=assessment,
        severity=severity,
        reason_code=reason_code,
        reply_start=span_result.reply_start,
        reply_end=span_result.reply_end,
        reply_quote=span_result.reply_quote,
        supporting_basis_refs=tuple(supporting_refs),
        conflicting_source_refs=tuple(conflicting_refs),
        voice_activation_refs=tuple(voice_refs),
    )


class ParsingError(Exception):
    """Response parsing failed (contains no sensitive info)."""
    pass
