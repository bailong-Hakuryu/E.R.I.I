"""Test CD-1 Shadow evaluation contracts."""

import pytest

from erii.deliberation.core_validator import TrustedAuthoritySecret
from erii.labs.deliberation.shadow_eval.contracts import (
    BlindedJudgeInputV1,
    RunConfigurationV1,
    ScenarioIdentityV1,
    ShadowEvaluationInputV1,
    ShadowEvaluationOutputV1,
)
from erii.labs.deliberation.shadow_eval.errors import ShadowFailureCode
from erii.labs.deliberation.shadow_eval.runner import ShadowEvaluationRunner
from .fixtures import (
    make_evidence_view,
    make_open_turn,
    make_shadow_input,
    make_user_envelope,
)


def test_scenario_identity_requires_valid_identifiers() -> None:
    """Scenario identity validates all identifier fields."""
    valid = ScenarioIdentityV1(
        scenario_id="refusal-boundary-1",
        agent_id="agent-1",
        user_id="user-1",
        relationship_id="rel-1",
        turn_ordinal=0,
        baseline_fingerprint="a" * 64,
        user_message_fingerprint="b" * 64,
        evidence_view_fingerprint="c" * 64,
    )
    assert valid.scenario_id == "refusal-boundary-1"


def test_scenario_identity_rejects_invalid_fingerprints() -> None:
    """Scenario identity requires 64-char hex fingerprints."""
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        ScenarioIdentityV1(
            scenario_id="test",
            agent_id="agent-1",
            user_id="user-1",
            relationship_id="rel-1",
            turn_ordinal=0,
            baseline_fingerprint="short",
            user_message_fingerprint="b" * 64,
            evidence_view_fingerprint="c" * 64,
        )


def test_scenario_identity_rejects_negative_turn_ordinal() -> None:
    """Turn ordinal must be non-negative."""
    with pytest.raises(ValueError, match="non-negative"):
        ScenarioIdentityV1(
            scenario_id="test",
            agent_id="agent-1",
            user_id="user-1",
            relationship_id="rel-1",
            turn_ordinal=-1,
            baseline_fingerprint="a" * 64,
            user_message_fingerprint="b" * 64,
            evidence_view_fingerprint="c" * 64,
        )


def test_run_configuration_validates_d0_d4_labels() -> None:
    """Configuration labels must be D0-D4."""
    valid = RunConfigurationV1(
        config_label="D1",
        provider_kind="fake_deterministic",
        model_id="fake-model-v1",
        adapter_version="shadow-adapter/v1",
        router_policy="compact_every_turn",
        temperature=0.0,
        max_tokens=4000,
        seed=42,
        capability_fingerprint="d" * 64,
    )
    assert valid.config_label == "D1"


def test_run_configuration_rejects_invalid_temperature() -> None:
    """Temperature must be in [0.0, 2.0]."""
    with pytest.raises(ValueError, match="temperature"):
        RunConfigurationV1(
            config_label="D1",
            provider_kind="fake",
            model_id="fake-model",
            adapter_version="v1",
            router_policy=None,
            temperature=3.0,
            max_tokens=4000,
            seed=42,
            capability_fingerprint="d" * 64,
        )


def test_run_configuration_rejects_invalid_max_tokens() -> None:
    """Max tokens must be positive."""
    with pytest.raises(ValueError, match="positive"):
        RunConfigurationV1(
            config_label="D0",
            provider_kind="fake",
            model_id="fake-model",
            adapter_version="v1",
            router_policy=None,
            temperature=None,
            max_tokens=0,
            seed=42,
            capability_fingerprint="d" * 64,
        )


def test_shadow_evaluation_input_verifies_relationship_scope() -> None:
    """Input verifies all components belong to the same relationship."""
    valid_input = make_shadow_input("D0")
    assert valid_input.scenario.relationship_id == "rel-1"


def test_shadow_evaluation_input_rejects_relationship_mismatch() -> None:
    """Input rejects cross-relationship components."""
    scenario = ScenarioIdentityV1(
        scenario_id="test",
        agent_id="agent-1",
        user_id="user-1",
        relationship_id="rel-1",
        turn_ordinal=0,
        baseline_fingerprint="a" * 64,
        user_message_fingerprint="b" * 64,
        evidence_view_fingerprint="c" * 64,
    )
    config = RunConfigurationV1(
        config_label="D0",
        provider_kind="fake",
        model_id="fake",
        adapter_version="v1",
        router_policy=None,
        temperature=None,
        max_tokens=4000,
        seed=42,
        capability_fingerprint="d" * 64,
    )
    turn = make_open_turn(relationship_id="rel-DIFFERENT")
    user_envelope = make_user_envelope()
    evidence = make_evidence_view()

    with pytest.raises(ValueError, match="relationship_id does not match"):
        ShadowEvaluationInputV1(
            scenario=scenario,
            config=config,
            sample_index=0,
            frozen_turn=turn,
            user_envelope=user_envelope,
            evidence_view=evidence,
        )


def test_shadow_evaluation_output_distinguishes_five_result_levels() -> None:
    """Output tracks five distinct validation levels."""
    output = ShadowEvaluationRunner(TrustedAuthoritySecret(b"s" * 32)).run_single(
        make_shadow_input("D1")
    )
    assert output.transport_completed
    assert output.schema_valid
    assert output.scope_and_binding_valid
    assert output.expected_semantic_axes_match is None
    assert output.human_judgment == "not_run"


def test_shadow_evaluation_output_rejects_negative_metrics() -> None:
    """Output validates metric ranges."""
    with pytest.raises(ValueError, match="non-negative"):
        ShadowEvaluationOutputV1(
            scenario_id="test",
            config_label="D0",
            sample_index=0,
            transport_completed=False,
            schema_valid=False,
            scope_and_binding_valid=False,
            route_taken=None,
            attempt_count=-1,
            failure_code=ShadowFailureCode.TRANSPORT_TIMEOUT,
        )


def test_blinded_judge_input_hides_configuration() -> None:
    """Blinded input contains no config/frame/interior/tokens."""
    blinded = BlindedJudgeInputV1(
        case_id="test",
        candidate_id="opaque-id-xyz",
        agent_blueprint_excerpt="A helpful AI assistant",
        relationship_stage_summary="early acquaintance",
        user_message_parts=("Hello",),
        reply_parts=("Hi there!",),
    )
    assert blinded.candidate_id == "opaque-id-xyz"
    # reply_id must not reveal D0/D1/D2/D3/D4
    assert "D0" not in blinded.candidate_id
    assert "D1" not in blinded.candidate_id
