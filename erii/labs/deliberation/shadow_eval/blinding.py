"""Blinding utilities for CD-1 evaluation.

Blinded artifacts hide:
- D0-D4 configuration
- Actor/Provider names
- Router decisions
- Frame/Interior Scene
- Token/cost metrics
- File paths or internal IDs revealing groups
"""

from __future__ import annotations

import hashlib
import random

from ..shadow_eval.contracts import (
    BlindedJudgeInputV1,
    ShadowEvaluationOutputV1,
)


def blind_for_judgment(
    output: ShadowEvaluationOutputV1,
    agent_blueprint_excerpt: str,
    relationship_stage_summary: str,
    user_message_parts: tuple[str, ...],
    *,
    blinding_seed: int,
) -> BlindedJudgeInputV1:
    """Create blinded judge input from shadow output.

    The opaque reply_id is randomized but deterministic (reproducible with
    same blinding_seed). Judges cannot infer configuration from the ID.
    """
    if output.reply_envelope is None:
        raise ValueError("Cannot blind output without reply_envelope")
    if not output.scope_and_binding_valid or output.shadow_binding is None:
        raise ValueError("Cannot blind an unbound Shadow output")

    reply_parts = tuple(part.exact_utf8 for part in output.reply_envelope.parts)

    # Generate opaque reply ID that doesn't reveal config
    # Use scenario_id + sample_index + blinding_seed for determinism
    id_seed = (
        f"{output.scenario_id}:{output.sample_index}:"
        f"{output.shadow_binding.result_fingerprint}:{blinding_seed}"
    )
    candidate_digest = hashlib.sha256(id_seed.encode("utf-8")).hexdigest()
    candidate_id = f"candidate-{candidate_digest[:20]}"

    return BlindedJudgeInputV1(
        case_id=output.scenario_id,
        candidate_id=candidate_id,
        agent_blueprint_excerpt=agent_blueprint_excerpt,
        relationship_stage_summary=relationship_stage_summary,
        user_message_parts=user_message_parts,
        reply_parts=reply_parts,
    )


def shuffle_blinded_inputs(
    blinded_inputs: list[BlindedJudgeInputV1],
    *,
    shuffle_seed: int,
) -> list[BlindedJudgeInputV1]:
    """Shuffle blinded inputs deterministically for presentation order.

    This removes temporal clustering of configs.
    """
    rng = random.Random(shuffle_seed)
    shuffled = list(blinded_inputs)
    rng.shuffle(shuffled)
    return shuffled


__all__ = ["blind_for_judgment", "shuffle_blinded_inputs"]
