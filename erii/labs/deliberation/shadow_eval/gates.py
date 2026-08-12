"""Promotion and stop gates for CD-1 evaluation.

Gates enforce:
- Zero-tolerance safety requirements
- Non-inferiority on key dimensions
- Statistical significance thresholds
- Latency and cost budgets
- Reliability requirements

Gates CANNOT promote based on 20 fake fixtures alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ..shadow_eval.metrics import AggregateMetrics
from ..shadow_eval.preregistration import PreregistrationV1


class GateStatus(str, Enum):
    """Gate evaluation status."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_PREREGISTERED = "not_preregistered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class GateResult:
    """Result from evaluating one gate."""

    gate_id: str
    gate_name: str
    status: GateStatus
    observed_value: float | None
    threshold_value: float | None
    reason: str


@dataclass(frozen=True)
class PromotionEvaluation:
    """Complete promotion gate evaluation."""

    config_label: Literal["D0", "D1", "D2", "D3", "D4"]
    preregistration: PreregistrationV1

    # Individual gate results
    cross_relationship_leak_gate: GateResult
    schema_reliability_gate: GateResult
    latency_budget_gate: GateResult
    cost_budget_gate: GateResult

    # Behavioral gates (require human judgment)
    psychological_causality_gate: GateResult
    naturalness_gate: GateResult
    sharp_expression_gate: GateResult

    # Overall
    all_gates_passed: bool
    can_promote: bool
    blocking_reasons: tuple[str, ...]


def evaluate_promotion_gates(
    config_label: Literal["D0", "D1", "D2", "D3", "D4"],
    metrics: AggregateMetrics,
    preregistration: PreregistrationV1,
    *,
    has_human_judgment: bool = False,
    latency_budget_ms: int | None = None,
    cost_budget_tokens: int | None = None,
) -> PromotionEvaluation:
    """Evaluate all promotion gates for one configuration.

    Returns evaluation with specific reasons for any failures.
    Does NOT auto-promote based on fake fixtures.
    """
    gates: list[GateResult] = []
    blocking: list[str] = []

    # Safety gate: Zero cross-relationship leakage
    leak_gate = _evaluate_leak_gate(metrics)
    gates.append(leak_gate)
    if leak_gate.status != GateStatus.PASSED:
        blocking.append(leak_gate.reason)

    # Reliability gate: Schema failure rate ≤ 2%
    reliability_gate = _evaluate_reliability_gate(metrics, preregistration)
    gates.append(reliability_gate)
    if reliability_gate.status != GateStatus.PASSED:
        blocking.append(reliability_gate.reason)

    # Latency budget gate
    latency_gate = _evaluate_latency_gate(metrics, latency_budget_ms)
    gates.append(latency_gate)
    if latency_gate.status != GateStatus.PASSED:
        blocking.append(latency_gate.reason)

    # Cost budget gate
    cost_gate = _evaluate_cost_gate(metrics, cost_budget_tokens)
    gates.append(cost_gate)
    if cost_gate.status != GateStatus.PASSED:
        blocking.append(cost_gate.reason)

    # Behavioral gates require human judgment
    if not has_human_judgment:
        psych_gate = GateResult(
            gate_id="psychological-causality",
            gate_name="Psychological Causality",
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            observed_value=None,
            threshold_value=None,
            reason="Human judgment not available",
        )
        naturalness_gate = GateResult(
            gate_id="naturalness",
            gate_name="Naturalness",
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            observed_value=None,
            threshold_value=None,
            reason="Human judgment not available",
        )
        sharp_gate = GateResult(
            gate_id="sharp-expression",
            gate_name="Sharp Expression Preservation",
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            observed_value=None,
            threshold_value=None,
            reason="Human judgment not available",
        )
        blocking.append("Human judgment required for behavioral dimensions")
    else:
        # Placeholder for when human judgment is available
        psych_gate = GateResult(
            gate_id="psychological-causality",
            gate_name="Psychological Causality",
            status=GateStatus.NOT_PREREGISTERED,
            observed_value=None,
            threshold_value=None,
            reason="Threshold awaiting Pilot calibration",
        )
        naturalness_gate = GateResult(
            gate_id="naturalness",
            gate_name="Naturalness",
            status=GateStatus.NOT_PREREGISTERED,
            observed_value=None,
            threshold_value=None,
            reason="Threshold awaiting Pilot calibration",
        )
        sharp_gate = GateResult(
            gate_id="sharp-expression",
            gate_name="Sharp Expression Preservation",
            status=GateStatus.NOT_PREREGISTERED,
            observed_value=None,
            threshold_value=None,
            reason="Threshold awaiting Pilot calibration",
        )
        blocking.append("Thresholds not yet calibrated by Pilot")

    all_passed = all(
        g.status == GateStatus.PASSED
        for g in [leak_gate, reliability_gate, latency_gate, cost_gate]
    ) and has_human_judgment and all(
        g.status == GateStatus.PASSED
        for g in [psych_gate, naturalness_gate, sharp_gate]
    )

    can_promote = all_passed and len(blocking) == 0

    return PromotionEvaluation(
        config_label=config_label,
        preregistration=preregistration,
        cross_relationship_leak_gate=leak_gate,
        schema_reliability_gate=reliability_gate,
        latency_budget_gate=latency_gate,
        cost_budget_gate=cost_gate,
        psychological_causality_gate=psych_gate,
        naturalness_gate=naturalness_gate,
        sharp_expression_gate=sharp_gate,
        all_gates_passed=all_passed,
        can_promote=can_promote,
        blocking_reasons=tuple(blocking),
    )


def _evaluate_leak_gate(metrics: AggregateMetrics) -> GateResult:
    """Zero tolerance for cross-relationship leakage."""
    leak_count = metrics.cross_relationship_leak_count
    if metrics.total_runs == 0:
        return GateResult(
            gate_id="cross-relationship-leak",
            gate_name="Cross-Relationship Leak",
            status=GateStatus.INSUFFICIENT_EVIDENCE,
            observed_value=None,
            threshold_value=0.0,
            reason="No Shadow runs available for leakage evaluation",
        )
    if leak_count == 0:
        return GateResult(
            gate_id="cross-relationship-leak",
            gate_name="Cross-Relationship Leak",
            status=GateStatus.PASSED,
            observed_value=0.0,
            threshold_value=0.0,
            reason="No cross-relationship leakage detected",
        )
    else:
        return GateResult(
            gate_id="cross-relationship-leak",
            gate_name="Cross-Relationship Leak",
            status=GateStatus.FAILED,
            observed_value=float(leak_count),
            threshold_value=0.0,
            reason=f"Detected {leak_count} cross-relationship leaks (zero tolerance)",
        )


def _evaluate_reliability_gate(
    metrics: AggregateMetrics,
    preregistration: PreregistrationV1,
) -> GateResult:
    """Evaluate reliability only against a Pilot-derived frozen threshold."""
    failure_rate = 1.0 - metrics.scope_binding_valid_rate
    threshold = preregistration.maximum_reliability_failure_rate
    if threshold is None:
        return GateResult(
            gate_id="schema-reliability",
            gate_name="Schema Reliability",
            status=GateStatus.NOT_PREREGISTERED,
            observed_value=failure_rate,
            threshold_value=None,
            reason="Reliability threshold not preregistered",
        )

    if failure_rate <= threshold:
        return GateResult(
            gate_id="schema-reliability",
            gate_name="Schema Reliability",
            status=GateStatus.PASSED,
            observed_value=failure_rate,
            threshold_value=threshold,
            reason=f"Failure rate {failure_rate:.1%} ≤ {threshold:.1%}",
        )
    else:
        return GateResult(
            gate_id="schema-reliability",
            gate_name="Schema Reliability",
            status=GateStatus.FAILED,
            observed_value=failure_rate,
            threshold_value=threshold,
            reason=f"Failure rate {failure_rate:.1%} > {threshold:.1%}",
        )


def _evaluate_latency_gate(
    metrics: AggregateMetrics, budget_ms: int | None
) -> GateResult:
    """P95 latency must be within budget."""
    if budget_ms is None:
        return GateResult(
            gate_id="latency-budget",
            gate_name="Latency Budget",
            status=GateStatus.NOT_PREREGISTERED,
            observed_value=float(metrics.p95_latency_ms),
            threshold_value=None,
            reason="Latency budget not preregistered",
        )

    if metrics.p95_latency_ms <= budget_ms:
        return GateResult(
            gate_id="latency-budget",
            gate_name="Latency Budget",
            status=GateStatus.PASSED,
            observed_value=float(metrics.p95_latency_ms),
            threshold_value=float(budget_ms),
            reason=f"p95 latency {metrics.p95_latency_ms}ms ≤ {budget_ms}ms",
        )
    else:
        return GateResult(
            gate_id="latency-budget",
            gate_name="Latency Budget",
            status=GateStatus.FAILED,
            observed_value=float(metrics.p95_latency_ms),
            threshold_value=float(budget_ms),
            reason=f"p95 latency {metrics.p95_latency_ms}ms > {budget_ms}ms",
        )


def _evaluate_cost_gate(
    metrics: AggregateMetrics, budget_tokens: int | None
) -> GateResult:
    """P95 total tokens must be within budget."""
    if budget_tokens is None:
        return GateResult(
            gate_id="cost-budget",
            gate_name="Cost Budget",
            status=GateStatus.NOT_PREREGISTERED,
            observed_value=float(metrics.p95_input_tokens + metrics.p95_output_tokens),
            threshold_value=None,
            reason="Cost budget not preregistered",
        )

    p95_total = metrics.p95_input_tokens + metrics.p95_output_tokens
    if p95_total <= budget_tokens:
        return GateResult(
            gate_id="cost-budget",
            gate_name="Cost Budget",
            status=GateStatus.PASSED,
            observed_value=float(p95_total),
            threshold_value=float(budget_tokens),
            reason=f"p95 tokens {p95_total} ≤ {budget_tokens}",
        )
    else:
        return GateResult(
            gate_id="cost-budget",
            gate_name="Cost Budget",
            status=GateStatus.FAILED,
            observed_value=float(p95_total),
            threshold_value=float(budget_tokens),
            reason=f"p95 tokens {p95_total} > {budget_tokens}",
        )


__all__ = [
    "GateStatus",
    "GateResult",
    "PromotionEvaluation",
    "evaluate_promotion_gates",
]
