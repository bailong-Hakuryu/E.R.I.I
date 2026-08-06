"""Prompt builder for continuity review.

Constructs messages for DeepSeek API including:
- Resolved evidence excerpts
- Resolved voice activations
- Clear instructions for five-axis review
- Real assessment/reason/severity options
"""

from erii.models.continuity import ContinuityEvaluationRequest
from .evidence_resolver import ResolvedEvidence, ResolvedVoiceActivation
from typing import Sequence


def build_review_prompt(
    request: ContinuityEvaluationRequest,
    resolved_evidence: Sequence[ResolvedEvidence],
    resolved_activations: Sequence[ResolvedVoiceActivation],
) -> list[dict]:
    """Build review prompt with resolved evidence."""

    system_prompt = _build_system_instruction()
    user_prompt = _build_review_request(
        request,
        resolved_evidence,
        resolved_activations,
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _build_system_instruction() -> str:
    """Build system instruction defining five-axis review task."""

    return """You are a character continuity reviewer.

Your task is to check the character's reply against five dimensions:

1. **identity_values**: Identity and values consistency
2. **psychological_causality**: Psychological causality reasonableness
3. **relationship_scope**: Relationship scope boundaries
4. **knowledge_memory_scope**: Knowledge and memory boundaries
5. **voice_style**: Voice and style consistency

## Assessment Options

**assessment** (choose one):
- `aligned`: Fully consistent
- `supported`: Supported new choice
- `review`: Tension requiring review
- `unsupported`: Unsupported drift

**reason_code** (choose one):
- `aligned` - fully consistent with persona
- `supported_new_choice` - justified character growth
- `supported_contextual_voice` - **ONLY use if voice_activation_refs is provided**
- `value_tension` - conflicting values detected
- `causal_tension` - psychological inconsistency
- `relationship_crossover` - inappropriate relationship knowledge
- `inherited_intimacy` - unjustified closeness
- `unavailable_knowledge` - knowledge beyond character scope
- `unsupported_identity_change` - unjustified identity shift
- `unsupported_causal_change` - unexplained behavior change
- `voice_style_deviation` - inconsistent expression style

**IMPORTANT**:
- If using `supported_contextual_voice`, you MUST include voice_activation_refs
- If no voice activations are available, use `aligned` instead

**severity** (choose one):
- `info` - use with `aligned` reason
- `advisory` - use with `supported_*` reasons or `voice_style_deviation`
- `warning` - use with `*_tension` reasons
- `critical` - use with `unsupported_*` or `unavailable_*` reasons

**Severity Rules**:
- `voice_style_deviation` → MUST be `advisory`
- `unavailable_knowledge` → MUST be `critical`
- `unsupported_identity_change` → MUST be `critical`
- `aligned` → MUST be `info`

## Critical Rules

1. **EVERY finding MUST cite at least one evidence reference**
   - Use `supporting_basis_refs` for supporting evidence
   - Use `conflicting_source_refs` for conflicting evidence
   - Reference IDs are provided in brackets like [ref-id-1]

2. **Knowledge boundary violations are CRITICAL**
   - Check if character mentions concepts NOT in persona evidence
   - Technical terms (Transformer, GPT-4, AI architectures) → `unavailable_knowledge`
   - Modern technology unknown to character → `unavailable_knowledge`
   - If character shows abilities beyond persona definition → `unsupported_identity_change`

3. **Identity consistency is STRICT**
   - Check if behavior CONTRADICTS core traits in persona evidence
   - If silent character suddenly speaks loudly → `unsupported_identity_change`
   - If shy character acts bold without justification → `unsupported_identity_change`
   - If behavior contradicts emotional pattern → `unsupported_causal_change`

4. **Compare reply against persona evidence explicitly**
   - Read the persona evidence carefully
   - If reply contradicts ANY claim in evidence → mark as unsupported/critical
   - Only mark as aligned if reply is CONSISTENT with evidence

## Output Format

For each dimension:
1. Quote the exact relevant span from the reply (`reply_quote`)
2. If the span appears multiple times, specify which occurrence (`occurrence`, 0-indexed)
3. **MUST cite evidence IDs** (`supporting_basis_refs` or `conflicting_source_refs`)
4. If applicable, cite voice activation IDs (`voice_activation_refs`)

Return JSON:
```json
{
  "findings": [
    {
      "axis": "identity_values",
      "assessment": "aligned",
      "severity": "info",
      "reason_code": "aligned",
      "reply_quote": "exact span from reply",
      "occurrence": 0,
      "supporting_basis_refs": ["ref-id-1"],
      "conflicting_source_refs": [],
      "voice_activation_refs": []
    }
  ]
}
```

**Must return exactly 5 findings, one per axis.**
**Every finding MUST include at least one reference ID.**
"""


def _build_review_request(
    request: ContinuityEvaluationRequest,
    resolved_evidence: Sequence[ResolvedEvidence],
    resolved_activations: Sequence[ResolvedVoiceActivation],
) -> str:
    """Build concrete review request."""

    lines = [
        "## Reply to Review",
        f"```\n{request.proposed_reply}\n```",
        "",
        "## User Message",
        f"```\n{request.user_message}\n```",
        "",
    ]

    # Add resolved persona evidence
    persona_evidence = [e for e in resolved_evidence if "persona" in e.kind]
    if persona_evidence:
        lines.append("## Persona Evidence")
        for evidence in persona_evidence:
            lines.append(f"**[{evidence.ref_id}]** ({evidence.kind})")
            lines.append(f"> {evidence.excerpt}")
            lines.append("")

    # Add resolved relationship evidence
    relationship_evidence = [e for e in resolved_evidence if "persona" not in e.kind]
    if relationship_evidence:
        lines.append("## Relationship Evidence")
        for evidence in relationship_evidence:
            lines.append(f"**[{evidence.ref_id}]** ({evidence.kind})")
            lines.append(f"> {evidence.excerpt}")
            lines.append("")

    # Add resolved voice activations
    if resolved_activations:
        lines.append("## Voice Pattern Activations")
        for activation in resolved_activations:
            lines.append(f"**[{activation.activation_id}]**")
            lines.append(f"- Pattern: {activation.pattern_id}")
            lines.append(f"- Conditions: {', '.join(activation.condition_ids)}")
            lines.append("")

    lines.append("Please review all five dimensions and return JSON.")
    lines.append("")
    lines.append("Requirements:")
    lines.append("- Exactly 5 findings (one per axis)")
    lines.append("- reply_quote must be exact span from reply")
    lines.append("- If span appears multiple times, specify occurrence")
    lines.append("- Must cite provided evidence IDs")

    return "\n".join(lines)
