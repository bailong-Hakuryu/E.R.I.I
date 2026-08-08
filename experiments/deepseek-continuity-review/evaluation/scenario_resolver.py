"""Synthetic evaluation scenarios and auditable expectation scoring."""

import hashlib
import json
from pathlib import Path
from typing import Sequence

from erii.models.continuity import (
    ContinuityEvaluationDecision,
    ContinuityEvaluationRequest,
    VoicePatternActivation,
)
from erii.models.continuity_evidence import (
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)

from erii_deepseek_continuity.evidence_resolver import (
    CrossRelationshipLeakError,
    EvidenceResolutionError,
    ResolvedEvidence,
    ResolvedVoiceActivation,
)

AXES = (
    "identity_values",
    "psychological_causality",
    "relationship_scope",
    "knowledge_memory_scope",
    "voice_style",
)
ASSESSMENTS = frozenset({"aligned", "supported", "review", "unsupported"})


def load_scenario(path: Path) -> dict:
    """Load and validate one versioned, synthetic scenario fixture."""
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"scenario_id", "description", "persona", "user_message", "proposed_reply"}
    if not required.issubset(data):
        raise ValueError("scenario_missing_required_fields")
    traits = data["persona"].get("key_traits")
    if not isinstance(traits, list) or not traits or any(
        not isinstance(item, str) or not item.strip() for item in traits
    ):
        raise ValueError("scenario_requires_persona_traits")
    expected = data.get("expected_assessment", {})
    if not isinstance(expected, dict) or any(
        axis not in AXES or assessment not in ASSESSMENTS
        for axis, assessment in expected.items()
    ):
        raise ValueError("scenario_has_invalid_expectation")
    return data


def create_request_from_scenario(
    scenario: dict,
    *,
    relationship_id: str = "synthetic-evaluation-relationship",
) -> ContinuityEvaluationRequest:
    """Construct refs for every persona trait and relationship premise excerpt."""
    scenario_id = scenario["scenario_id"]
    traits = scenario["persona"]["key_traits"]
    manifest_fingerprint = _fingerprint(json.dumps(traits, ensure_ascii=False))
    persona_refs = tuple(
        ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.PERSONA_CLAIM,
            {
                "manifest_id": "synthetic-manifest",
                "content_fingerprint": manifest_fingerprint,
                "claim_id": f"{scenario_id}-trait-{index}",
            },
        )
        for index, _ in enumerate(traits)
    )
    relationship_refs = tuple(
        ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.RELATIONSHIP_PREMISE,
            {
                "relationship_id": relationship_id,
                "premise_id": f"{scenario_id}-premise-{index}",
                "content_fingerprint": _fingerprint(excerpt),
            },
        )
        for index, excerpt in enumerate(scenario.get("relationship_evidence", []))
    )
    return ContinuityEvaluationRequest(
        turn_id=scenario_id,
        relationship_id=relationship_id,
        persona_id="synthetic-persona-lin-che",
        user_message=scenario["user_message"],
        proposed_reply=scenario["proposed_reply"],
        persona_manifest_id="synthetic-manifest",
        context_baseline_fingerprint=_fingerprint(
            json.dumps(scenario.get("relationship_evidence", []), ensure_ascii=False)
        ),
        persona_context_refs=persona_refs,
        relationship_context_refs=relationship_refs,
        voice_pattern_activations=(),
    )


class ScenarioEvidenceResolver:
    """Resolve only the refs constructed for one original synthetic scenario."""

    def __init__(self, scenario: dict, request: ContinuityEvaluationRequest) -> None:
        traits = scenario["persona"]["key_traits"]
        relationship_excerpts = scenario.get("relationship_evidence", [])
        self._relationship_id = request.relationship_id
        self._persona = {
            ref.ref_id: ResolvedEvidence(ref.ref_id, ref.kind.value, excerpt[:200])
            for ref, excerpt in zip(request.persona_context_refs, traits, strict=True)
        }
        self._relationship = {
            ref.ref_id: ResolvedEvidence(ref.ref_id, ref.kind.value, excerpt[:200])
            for ref, excerpt in zip(
                request.relationship_context_refs,
                relationship_excerpts,
                strict=True,
            )
        }

    def resolve(
        self,
        persona_refs: Sequence[ContinuityEvidenceRef],
        relationship_refs: Sequence[ContinuityEvidenceRef],
        relationship_id: str,
    ) -> Sequence[ResolvedEvidence]:
        if relationship_id != self._relationship_id:
            raise CrossRelationshipLeakError("scenario_relationship_scope_mismatch")
        try:
            persona = [self._persona[ref.ref_id] for ref in persona_refs]
            relationship = [
                self._relationship[ref.ref_id]
                for ref in relationship_refs
                if ref.locator.get("relationship_id") == relationship_id
            ]
        except KeyError:
            raise EvidenceResolutionError("scenario_ref_not_whitelisted") from None
        if len(relationship) != len(relationship_refs):
            raise CrossRelationshipLeakError("scenario_relationship_scope_mismatch")
        return tuple(persona + relationship)

    def resolve_voice_activations(
        self,
        activations: Sequence[VoicePatternActivation],
    ) -> Sequence[ResolvedVoiceActivation]:
        return tuple(
            ResolvedVoiceActivation(
                activation_id=item.activation_id,
                pattern_id=item.pattern_id,
                condition_ids=item.condition_ids,
            )
            for item in activations
        )


def score_expected_assessments(
    decision: ContinuityEvaluationDecision | None,
    scenario: dict,
) -> dict:
    """Score only declared axes and retain every expected/actual comparison."""
    expected = scenario.get("expected_assessment", {})
    actual = (
        {finding.axis.value: finding.assessment.value for finding in decision.findings}
        if decision is not None
        else {}
    )
    axes = [
        {
            "axis": axis,
            "expected": expected_assessment,
            "actual": actual.get(axis),
            "matched": actual.get(axis) == expected_assessment,
        }
        for axis, expected_assessment in expected.items()
    ]
    matched = sum(1 for item in axes if item["matched"])
    return {
        "expected_axes_total": len(axes),
        "expected_axes_matched": matched,
        "expectations_met": bool(axes) and matched == len(axes),
        "axes": axes,
    }


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
