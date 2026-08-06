"""Scenario-specific evidence resolver for testing.

Provides realistic persona evidence based on test scenarios.
"""

from dataclasses import dataclass
from typing import Sequence
from erii.models.continuity import ContinuityEvidenceRef, VoicePatternActivation

from erii_deepseek_continuity.evidence_resolver import (
    ResolvedEvidence,
    ResolvedVoiceActivation,
)


class ScenarioEvidenceResolver:
    """Evidence resolver with scenario-specific persona information."""

    def __init__(self, persona_name: str = "绘梨衣"):
        self.persona_name = persona_name

    def resolve(
        self,
        persona_refs: Sequence[ContinuityEvidenceRef],
        relationship_refs: Sequence[ContinuityEvidenceRef],
        relationship_id: str,
    ) -> Sequence[ResolvedEvidence]:
        """Resolve to scenario-specific excerpts."""
        resolved = []

        # 绘梨衣的核心人设
        persona_claims = [
            "绘梨衣患有严重的失语症，无法说话，只能通过手写板与他人交流。",
            "绘梨衣性格温柔、内向、害羞，不擅长与陌生人交流。",
            "绘梨衣的世界观仅限于她的房间和少数照顾她的人，对外界了解极少。",
            "绘梨衣喜欢安静的活动，如画画、看书，害怕嘈杂的环境。",
            "绘梨衣没有接受过正规的现代教育，不了解计算机、互联网、AI 等现代科技概念。",
        ]

        for i, ref in enumerate(persona_refs):
            claim_text = persona_claims[i % len(persona_claims)]
            resolved.append(
                ResolvedEvidence(
                    ref_id=ref.ref_id,
                    kind=ref.kind.value,
                    excerpt=claim_text,
                )
            )

        for ref in relationship_refs:
            resolved.append(
                ResolvedEvidence(
                    ref_id=ref.ref_id,
                    kind=ref.kind.value,
                    excerpt="与用户的关系：初次见面，尚在建立信任阶段。",
                )
            )

        return tuple(resolved)

    def resolve_voice_activations(
        self,
        activations: Sequence[VoicePatternActivation],
    ) -> Sequence[ResolvedVoiceActivation]:
        """Resolve voice activations."""
        resolved = []

        for activation in activations:
            resolved.append(
                ResolvedVoiceActivation(
                    activation_id=activation.activation_id,
                    pattern_id=activation.pattern_id,
                    condition_ids=activation.condition_ids,
                )
            )

        return tuple(resolved)
