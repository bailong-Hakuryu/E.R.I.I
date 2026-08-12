"""Integration tests for the public CD-1 Shadow harness interfaces."""

from dataclasses import replace

import pytest

from erii.deliberation.core_validator import TrustedAuthoritySecret
from erii.deliberation.schemas import MessagePart, VisibleReplyEnvelopeV1
from erii.labs.deliberation.shadow_eval.blinding import blind_for_judgment
from erii.labs.deliberation.shadow_eval.metrics import compute_aggregate_metrics
from erii.labs.deliberation.shadow_eval.runner import ShadowEvaluationRunner
from erii.labs.deliberation.shadow_eval.scenarios import SYNTHETIC_SCENARIOS

from .fixtures import make_shadow_input


@pytest.mark.parametrize(
    ("config_label", "expected_route"),
    (
        ("D0", "direct"),
        ("D1", "compact"),
        ("D2", "staged"),
        ("D3", "compact"),
        ("D4", "equal_compute_direct"),
    ),
)
def test_runner_executes_every_offline_configuration(
    config_label: str,
    expected_route: str,
) -> None:
    secret = TrustedAuthoritySecret(b"s" * 32)
    runner = ShadowEvaluationRunner(secret)
    shadow_input = make_shadow_input(config_label)

    output = runner.run_single(shadow_input)

    assert output.transport_completed
    assert output.schema_valid
    assert output.scope_and_binding_valid
    assert output.route_taken == expected_route
    assert output.reply_envelope is not None
    assert output.shadow_binding is not None
    assert output.shadow_binding.verify_with_secret(secret)
    assert runner.verify_output(shadow_input, output) == (True, ())


def test_compact_and_staged_outputs_keep_distinct_artifacts() -> None:
    runner = ShadowEvaluationRunner(TrustedAuthoritySecret(b"s" * 32))

    compact = runner.run_single(make_shadow_input("D1"))
    staged = runner.run_single(make_shadow_input("D2"))

    assert compact.decision is not None
    assert compact.core_result_binding is not None
    assert compact.plan is None
    assert compact.realization is None
    assert staged.decision is None
    assert staged.plan is not None
    assert staged.realization is not None
    assert staged.core_result_binding is not None


def test_adaptive_router_escalates_only_the_structural_fixture() -> None:
    runner = ShadowEvaluationRunner(TrustedAuthoritySecret(b"s" * 32))

    ordinary = runner.run_single(make_shadow_input("D3"))
    structural = runner.run_single(
        make_shadow_input(
            "D3",
            scenario_id="adaptive-escalate-structural-20",
        )
    )

    assert ordinary.route_taken == "compact"
    assert not ordinary.escalation_occurred
    assert structural.route_taken == "staged"
    assert structural.escalation_occurred


@pytest.mark.parametrize("comparison_target", ("D1", "D2", "D3"))
def test_d4_matches_model_and_compute_of_its_target(
    comparison_target: str,
) -> None:
    scenario_id = (
        "adaptive-escalate-structural-20"
        if comparison_target == "D3"
        else "test-scenario"
    )
    runner = ShadowEvaluationRunner(TrustedAuthoritySecret(b"s" * 32))
    target_input = make_shadow_input(comparison_target, scenario_id=scenario_id)
    control_input = make_shadow_input(
        "D4",
        scenario_id=scenario_id,
        d4_comparison_target=comparison_target,
    )

    target = runner.run_single(target_input)
    control = runner.run_single(control_input)

    assert target_input.config.model_id == control_input.config.model_id
    assert target.attempt_count == control.attempt_count
    assert target.input_tokens == control.input_tokens
    assert target.output_tokens == control.output_tokens


def test_same_seed_is_deterministic_and_different_seed_changes_reply() -> None:
    runner = ShadowEvaluationRunner(TrustedAuthoritySecret(b"s" * 32))

    first = runner.run_single(make_shadow_input("D1", seed=42))
    repeated = runner.run_single(make_shadow_input("D1", seed=42))
    changed = runner.run_single(make_shadow_input("D1", seed=99))

    assert first.reply_envelope == repeated.reply_envelope
    assert first.shadow_binding == repeated.shadow_binding
    assert first.reply_envelope != changed.reply_envelope


def test_exact_reply_change_invalidates_shadow_and_core_bindings() -> None:
    secret = TrustedAuthoritySecret(b"s" * 32)
    runner = ShadowEvaluationRunner(secret)
    shadow_input = make_shadow_input("D1")
    output = runner.run_single(shadow_input)
    tampered_reply = VisibleReplyEnvelopeV1(
        parts=(
            MessagePart(
                part_id="reply-1",
                exact_utf8="tampered visible reply",
            ),
        )
    )

    tampered = replace(output, reply_envelope=tampered_reply)
    valid, errors = runner.verify_output(shadow_input, tampered)

    assert not valid
    assert "reply envelope does not match compact decision" in errors
    assert "Shadow binding does not match exact output" in errors


def test_blinding_hides_configuration_and_private_artifacts() -> None:
    runner = ShadowEvaluationRunner(TrustedAuthoritySecret(b"s" * 32))
    output = runner.run_single(make_shadow_input("D1"))

    blinded = blind_for_judgment(
        output,
        agent_blueprint_excerpt="Helpful assistant",
        relationship_stage_summary="early",
        user_message_parts=("Hello",),
        blinding_seed=100,
    )

    assert blinded.case_id == "test-scenario"
    assert blinded.user_message_parts == ("Hello",)
    assert blinded.reply_parts == tuple(
        part.exact_utf8 for part in output.reply_envelope.parts
    )
    for label in ("D0", "D1", "D2", "D3", "D4"):
        assert label not in blinded.candidate_id
    rendered = repr(blinded)
    assert "Helpful assistant" not in rendered
    assert "Hello" not in rendered
    assert "Offline fixture" not in rendered


def test_blinding_assigns_stable_distinct_ids_to_paired_candidates() -> None:
    runner = ShadowEvaluationRunner(TrustedAuthoritySecret(b"s" * 32))
    direct = runner.run_single(make_shadow_input("D0"))
    compact = runner.run_single(make_shadow_input("D1"))

    def blind(output):
        return blind_for_judgment(
            output,
            agent_blueprint_excerpt="Helpful assistant",
            relationship_stage_summary="early",
            user_message_parts=("Hello",),
            blinding_seed=100,
        )

    direct_id = blind(direct).candidate_id
    compact_id = blind(compact).candidate_id

    assert direct_id != compact_id
    assert direct_id == blind(direct).candidate_id
    assert compact_id == blind(compact).candidate_id


def test_aggregate_metrics_keep_validation_levels_distinct() -> None:
    runner = ShadowEvaluationRunner(TrustedAuthoritySecret(b"s" * 32))
    outputs = [
        runner.run_single(make_shadow_input("D1", sample_index=index))
        for index in range(5)
    ]

    metrics = compute_aggregate_metrics(outputs)

    assert metrics.total_runs == 5
    assert metrics.transport_completed_count == 5
    assert metrics.schema_valid_count == 5
    assert metrics.scope_and_binding_valid_count == 5
    assert metrics.transport_completion_rate == 1.0
    assert metrics.schema_valid_rate == 1.0
    assert metrics.scope_binding_valid_rate == 1.0


def test_shadow_harness_does_not_mutate_turn() -> None:
    runner = ShadowEvaluationRunner(TrustedAuthoritySecret(b"s" * 32))
    shadow_input = make_shadow_input("D1")
    original = (
        shadow_input.frozen_turn.status,
        shadow_input.frozen_turn.record_version,
        shadow_input.frozen_turn.context_baseline,
    )

    runner.run_single(shadow_input)

    assert (
        shadow_input.frozen_turn.status,
        shadow_input.frozen_turn.record_version,
        shadow_input.frozen_turn.context_baseline,
    ) == original


def test_twenty_scenarios_cover_required_categories() -> None:
    assert len(SYNTHETIC_SCENARIOS) == 20
    categories = {scenario.category for scenario in SYNTHETIC_SCENARIOS}
    assert {
        "refusal",
        "anger_sharp_expression",
        "boundary_assertion",
        "unwilling_reconciliation",
        "user_intent_unclear",
        "psychological_conflict",
        "knowledge_boundary",
    }.issubset(categories)
    for scenario in SYNTHETIC_SCENARIOS:
        assert "-" in scenario.scenario_id
        assert scenario.scenario_id.islower()
