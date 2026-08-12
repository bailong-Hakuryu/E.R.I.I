"""Shadow evaluation metrics collection.

Metrics distinguish:
- Parse success vs semantic match
- Offline fixture behavior vs human judgment
- Process metrics (latency, tokens) vs outcome quality
"""

from __future__ import annotations

from dataclasses import dataclass

from ..shadow_eval.contracts import ShadowEvaluationOutputV1
from ..shadow_eval.errors import ShadowFailureCode


@dataclass(frozen=True)
class AggregateMetrics:
    """Aggregate metrics across multiple shadow runs."""

    total_runs: int
    transport_completed_count: int
    schema_valid_count: int
    scope_and_binding_valid_count: int

    # Process metrics
    p50_latency_ms: int
    p95_latency_ms: int
    p50_input_tokens: int
    p95_input_tokens: int
    p50_output_tokens: int
    p95_output_tokens: int
    total_tokens: int

    # Failure breakdown
    transport_failures: int
    schema_failures: int
    scope_binding_failures: int
    cross_relationship_leak_count: int

    # D3 specific
    escalation_count: int

    @property
    def transport_completion_rate(self) -> float:
        """Transport completion rate (not a guarantee of correctness)."""
        if self.total_runs == 0:
            return 0.0
        return self.transport_completed_count / self.total_runs

    @property
    def schema_valid_rate(self) -> float:
        """Schema validation rate (not semantic correctness)."""
        if self.total_runs == 0:
            return 0.0
        return self.schema_valid_count / self.total_runs

    @property
    def scope_binding_valid_rate(self) -> float:
        """Scope and binding validation rate."""
        if self.total_runs == 0:
            return 0.0
        return self.scope_and_binding_valid_count / self.total_runs

    @property
    def escalation_rate(self) -> float:
        """D3 escalation rate."""
        if self.total_runs == 0:
            return 0.0
        return self.escalation_count / self.total_runs


def compute_aggregate_metrics(
    outputs: list[ShadowEvaluationOutputV1],
) -> AggregateMetrics:
    """Compute aggregate metrics from shadow outputs."""
    if not outputs:
        return AggregateMetrics(
            total_runs=0,
            transport_completed_count=0,
            schema_valid_count=0,
            scope_and_binding_valid_count=0,
            p50_latency_ms=0,
            p95_latency_ms=0,
            p50_input_tokens=0,
            p95_input_tokens=0,
            p50_output_tokens=0,
            p95_output_tokens=0,
            total_tokens=0,
            transport_failures=0,
            schema_failures=0,
            scope_binding_failures=0,
            cross_relationship_leak_count=0,
            escalation_count=0,
        )

    total_runs = len(outputs)
    transport_completed = sum(1 for o in outputs if o.transport_completed)
    schema_valid = sum(1 for o in outputs if o.schema_valid)
    scope_binding_valid = sum(1 for o in outputs if o.scope_and_binding_valid)

    # Latency percentiles
    latencies = sorted(o.latency_ms for o in outputs)
    p50_lat = latencies[len(latencies) // 2] if latencies else 0
    p95_lat = latencies[int(len(latencies) * 0.95)] if latencies else 0

    # Token percentiles
    input_tokens = sorted(o.input_tokens for o in outputs)
    output_tokens = sorted(o.output_tokens for o in outputs)
    p50_in = input_tokens[len(input_tokens) // 2] if input_tokens else 0
    p95_in = input_tokens[int(len(input_tokens) * 0.95)] if input_tokens else 0
    p50_out = output_tokens[len(output_tokens) // 2] if output_tokens else 0
    p95_out = output_tokens[int(len(output_tokens) * 0.95)] if output_tokens else 0
    total_tok = sum(o.input_tokens + o.output_tokens for o in outputs)

    # Failure breakdown
    transport_fail = sum(1 for o in outputs if not o.transport_completed)
    schema_fail = sum(1 for o in outputs if o.transport_completed and not o.schema_valid)
    scope_fail = sum(
        1 for o in outputs if o.schema_valid and not o.scope_and_binding_valid
    )
    cross_relationship_leaks = sum(
        1
        for output in outputs
        if output.failure_code is ShadowFailureCode.CROSS_RELATIONSHIP_LEAK
    )

    # D3 escalation
    escalations = sum(1 for o in outputs if o.escalation_occurred)

    return AggregateMetrics(
        total_runs=total_runs,
        transport_completed_count=transport_completed,
        schema_valid_count=schema_valid,
        scope_and_binding_valid_count=scope_binding_valid,
        p50_latency_ms=p50_lat,
        p95_latency_ms=p95_lat,
        p50_input_tokens=p50_in,
        p95_input_tokens=p95_in,
        p50_output_tokens=p50_out,
        p95_output_tokens=p95_out,
        total_tokens=total_tok,
        transport_failures=transport_fail,
        schema_failures=schema_fail,
        scope_binding_failures=scope_fail,
        cross_relationship_leak_count=cross_relationship_leaks,
        escalation_count=escalations,
    )


__all__ = ["AggregateMetrics", "compute_aggregate_metrics"]
