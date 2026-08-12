"""Promotion gates remain blocked until Pilot thresholds and judgments exist."""

from dataclasses import replace

from erii.deliberation.core_validator import TrustedAuthoritySecret
from erii.labs.deliberation.shadow_eval.gates import (
    GateStatus,
    evaluate_promotion_gates,
)
from erii.labs.deliberation.shadow_eval.contracts import ShadowEvaluationOutputV1
from erii.labs.deliberation.shadow_eval.errors import ShadowFailureCode
from erii.labs.deliberation.shadow_eval.metrics import compute_aggregate_metrics
from erii.labs.deliberation.shadow_eval.preregistration import (
    create_cd1_preregistration,
)
from erii.labs.deliberation.shadow_eval.runner import ShadowEvaluationRunner

from .fixtures import make_shadow_input


def _passing_fake_metrics():
    runner = ShadowEvaluationRunner(TrustedAuthoritySecret(b"s" * 32))
    return compute_aggregate_metrics(
        [runner.run_single(make_shadow_input("D1", sample_index=index)) for index in range(3)]
    )


def test_fake_outputs_cannot_pass_unregistered_promotion_gates() -> None:
    preregistration = create_cd1_preregistration()
    evaluation = evaluate_promotion_gates(
        "D1",
        _passing_fake_metrics(),
        preregistration,
    )

    assert preregistration.inter_rater_target_kappa is None
    assert evaluation.schema_reliability_gate.status is GateStatus.NOT_PREREGISTERED
    assert evaluation.latency_budget_gate.status is GateStatus.NOT_PREREGISTERED
    assert evaluation.cost_budget_gate.status is GateStatus.NOT_PREREGISTERED
    assert evaluation.psychological_causality_gate.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert not evaluation.can_promote


def test_reliability_threshold_must_come_from_preregistration() -> None:
    preregistration = replace(
        create_cd1_preregistration(),
        maximum_reliability_failure_rate=0.0,
    )
    evaluation = evaluate_promotion_gates(
        "D1",
        _passing_fake_metrics(),
        preregistration,
    )

    assert evaluation.schema_reliability_gate.status is GateStatus.PASSED
    assert evaluation.schema_reliability_gate.threshold_value == 0.0
    assert not evaluation.can_promote


def test_binding_failure_does_not_masquerade_as_cross_relationship_leak() -> None:
    binding_failure = ShadowEvaluationOutputV1(
        scenario_id="test-scenario",
        config_label="D1",
        sample_index=0,
        route_taken="compact",
        transport_completed=True,
        schema_valid=True,
        scope_and_binding_valid=False,
        failure_code=ShadowFailureCode.BINDING_MISMATCH,
        failure_stage="scope-binding",
    )
    metrics = compute_aggregate_metrics([binding_failure])

    evaluation = evaluate_promotion_gates(
        "D1",
        metrics,
        create_cd1_preregistration(),
    )

    assert metrics.scope_binding_failures == 1
    assert metrics.cross_relationship_leak_count == 0
    assert evaluation.cross_relationship_leak_gate.status is GateStatus.PASSED


def test_cross_relationship_failure_trips_zero_tolerance_gate() -> None:
    leak = ShadowEvaluationOutputV1(
        scenario_id="test-scenario",
        config_label="D1",
        sample_index=0,
        route_taken="compact",
        transport_completed=True,
        schema_valid=True,
        scope_and_binding_valid=False,
        failure_code=ShadowFailureCode.CROSS_RELATIONSHIP_LEAK,
        failure_stage="scope-binding",
    )
    metrics = compute_aggregate_metrics([leak])

    evaluation = evaluate_promotion_gates(
        "D1",
        metrics,
        create_cd1_preregistration(),
    )

    assert metrics.cross_relationship_leak_count == 1
    assert evaluation.cross_relationship_leak_gate.status is GateStatus.FAILED


def test_empty_metrics_never_pass_zero_tolerance_gate() -> None:
    evaluation = evaluate_promotion_gates(
        "D1",
        compute_aggregate_metrics([]),
        create_cd1_preregistration(),
    )

    assert (
        evaluation.cross_relationship_leak_gate.status
        is GateStatus.INSUFFICIENT_EVIDENCE
    )
    assert not evaluation.can_promote
