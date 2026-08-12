"""Canonical offline fixtures for the CD-1 Shadow harness tests."""

from __future__ import annotations

from erii.deliberation.host_bridge import (
    fingerprint_evidence_view,
    fingerprint_user_envelope,
)
from erii.deliberation.schemas import EvidenceViewV1, MessagePart, UserMessageEnvelope
from erii.labs.deliberation.shadow_eval.configurations import (
    create_d0_direct_generation,
    create_d1_compact_deliberation,
    create_d2_staged_deliberation,
    create_d3_adaptive_router,
    create_d4_equal_compute_control,
)
from erii.labs.deliberation.shadow_eval.contracts import (
    ComparisonTarget,
    ScenarioIdentityV1,
    ShadowEvaluationInputV1,
)
from erii.models.turn import SourceTranscript, TurnMessage, TurnRecord, TurnRole, TurnStatus
from erii.models.turn_context import (
    TurnBlueprintReference,
    TurnContextBaseline,
    TurnPremiseReference,
)


def make_user_envelope(
    *,
    message_id: str = "user-message-1",
    content: str = "Hello",
) -> UserMessageEnvelope:
    provisional = UserMessageEnvelope(
        parts=(MessagePart(part_id=message_id, kind="text", exact_utf8=content),),
        canonical_fingerprint="0" * 64,
    )
    return provisional.model_copy(
        update={"canonical_fingerprint": fingerprint_user_envelope(provisional)}
    )


def make_evidence_view(
    *,
    relationship_id: str = "rel-1",
    turn_id: str = "turn-1",
) -> EvidenceViewV1:
    provisional = EvidenceViewV1(
        view_id="view-1",
        relationship_id=relationship_id,
        turn_id=turn_id,
        items=(),
        allowed_claim_kinds=(),
        view_fingerprint="0" * 64,
    )
    return provisional.model_copy(
        update={"view_fingerprint": fingerprint_evidence_view(provisional)}
    )


def make_open_turn(
    *,
    relationship_id: str = "rel-1",
    turn_id: str = "turn-1",
    persona_id: str = "agent-1",
    message_id: str = "user-message-1",
    content: str = "Hello",
) -> TurnRecord:
    baseline = TurnContextBaseline.create(
        relationship_id=relationship_id,
        turn_id=turn_id,
        persona_id=persona_id,
        blueprint=TurnBlueprintReference(
            blueprint_id="blueprint-1",
            revision=1,
            source_sha256="a" * 64,
        ),
        manifest=None,
        approved_growth_refs=(),
        premise=TurnPremiseReference(
            premise_id="premise-1",
            content_fingerprint="b" * 64,
        ),
        direct_event_count=0,
        adjudication_count=0,
        history_prefix_fingerprint="c" * 64,
        policy_versions={
            "relationship_baseline_policy": "v1",
            "relationship_history_projection": "v1",
            "relationship_safety_policy": "v1",
            "interaction_context_policy": "v1",
            "voice_matcher_policy": "v1",
        },
    )
    return TurnRecord(
        turn_id=turn_id,
        relationship_id=relationship_id,
        status=TurnStatus.OPEN,
        transcript=SourceTranscript(
            user_message=TurnMessage(
                message_id=message_id,
                role=TurnRole.USER,
                content=content,
            )
        ),
        context_baseline=baseline,
    )


def make_shadow_input(
    config_label: str = "D1",
    *,
    seed: int = 42,
    sample_index: int = 0,
    scenario_id: str = "test-scenario",
    d4_comparison_target: ComparisonTarget = "D1",
) -> ShadowEvaluationInputV1:
    turn = make_open_turn()
    baseline = turn.context_baseline
    if baseline is None:
        raise AssertionError("fixture turn requires a baseline")
    user_envelope = make_user_envelope()
    evidence_view = make_evidence_view()
    scenario = ScenarioIdentityV1(
        scenario_id=scenario_id,
        agent_id=baseline.persona_id,
        user_id="user-1",
        relationship_id=turn.relationship_id,
        turn_ordinal=0,
        baseline_fingerprint=baseline.baseline_fingerprint,
        user_message_fingerprint=user_envelope.canonical_fingerprint,
        evidence_view_fingerprint=evidence_view.view_fingerprint,
    )
    factories = {
        "D0": lambda: create_d0_direct_generation(seed=seed),
        "D1": lambda: create_d1_compact_deliberation(seed=seed),
        "D2": lambda: create_d2_staged_deliberation(seed=seed),
        "D3": lambda: create_d3_adaptive_router(seed=seed),
        "D4": lambda: create_d4_equal_compute_control(
            seed=seed,
            comparison_target=d4_comparison_target,
        ),
    }
    try:
        config = factories[config_label]()
    except KeyError:
        raise ValueError(f"Unsupported config: {config_label}") from None
    return ShadowEvaluationInputV1(
        scenario=scenario,
        config=config,
        sample_index=sample_index,
        frozen_turn=turn,
        user_envelope=user_envelope,
        evidence_view=evidence_view,
    )
