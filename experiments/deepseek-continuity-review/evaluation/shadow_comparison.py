"""Shadow comparison: thinking enabled vs disabled.

Runs the same evaluation scenarios with thinking on and off,
compares the results to measure the impact of thinking mode.

Usage:
    python evaluation/shadow_comparison.py --scenarios scenarios/*.json --api-key YOUR_KEY
"""

import sys
from pathlib import Path

# Add paths relative to this file
script_dir = Path(__file__).parent
project_root = script_dir.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(script_dir.parent / 'src'))

import json
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Sequence

from erii.models.continuity import (
    ContinuityEvaluationRequest,
    ContinuityEvaluationDecision,
    ContinuityAxis,
)
from erii.models.continuity_evidence import ContinuityEvidenceRef, ContinuityEvidenceKind

from erii_deepseek_continuity import (
    DeepSeekContinuityEvaluator,
    DeepSeekClient,
    FakeEvidenceResolver,
)


@dataclass
class ComparisonResult:
    """Result of one thinking on/off comparison."""
    scenario_id: str
    proposed_reply: str

    thinking_enabled: bool
    thinking_present: bool  # Was reasoning actually generated

    # Findings per axis
    identity_values_assessment: str
    psychological_causality_assessment: str
    relationship_scope_assessment: str
    knowledge_memory_scope_assessment: str
    voice_style_assessment: str

    # Metrics
    latency_ms: int
    input_tokens: int
    output_tokens: int


@dataclass
class ComparisonSummary:
    """Summary of shadow comparison."""
    total_scenarios: int
    thinking_on_results: Sequence[ComparisonResult]
    thinking_off_results: Sequence[ComparisonResult]

    # Aggregate metrics
    thinking_on_avg_latency_ms: float
    thinking_off_avg_latency_ms: float
    thinking_on_total_tokens: int
    thinking_off_total_tokens: int

    # Assessment differences
    differing_assessments: int
    differing_scenarios: Sequence[str]


def load_scenario(scenario_path: Path) -> dict:
    """Load evaluation scenario from JSON."""
    with open(scenario_path) as f:
        return json.load(f)


def create_request_from_scenario(scenario: dict) -> tuple[ContinuityEvaluationRequest, str]:
    """Create ContinuityEvaluationRequest from scenario and return ref_id."""

    # Create minimal persona ref
    persona_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.PERSONA_CLAIM,
        {
            "manifest_id": scenario.get("persona_manifest_id", "test-manifest"),
            "content_fingerprint": "0" * 64,
            "claim_id": "test-claim",
        },
    )

    request = ContinuityEvaluationRequest(
        turn_id=scenario["scenario_id"],
        relationship_id=scenario.get("relationship_id", "test-relationship"),
        persona_id=scenario.get("persona_id", "test-persona"),
        user_message=scenario["user_message"],
        proposed_reply=scenario["proposed_reply"],
        persona_manifest_id=scenario.get("persona_manifest_id", "test-manifest"),
        context_baseline_fingerprint="0" * 64,
        persona_context_refs=(persona_ref,),
        relationship_context_refs=(),
        voice_pattern_activations=(),
    )

    return request, persona_ref.ref_id


def evaluate_with_thinking(
    evaluator: DeepSeekContinuityEvaluator,
    request: ContinuityEvaluationRequest,
    scenario: dict,
) -> ComparisonResult:
    """Evaluate with thinking mode and extract result."""

    start_time = time.time()
    decision = evaluator.evaluate(request)
    latency_ms = int((time.time() - start_time) * 1000)

    # Extract assessments per axis
    assessments = {}
    for finding in decision.findings:
        assessments[finding.axis.value] = finding.assessment.value

    return ComparisonResult(
        scenario_id=scenario["scenario_id"],
        proposed_reply=scenario["proposed_reply"],
        thinking_enabled=evaluator._client._thinking_enabled,
        thinking_present=False,  # We don't have direct access to this
        identity_values_assessment=assessments.get("identity_values", "unknown"),
        psychological_causality_assessment=assessments.get("psychological_causality", "unknown"),
        relationship_scope_assessment=assessments.get("relationship_scope", "unknown"),
        knowledge_memory_scope_assessment=assessments.get("knowledge_memory_scope", "unknown"),
        voice_style_assessment=assessments.get("voice_style", "unknown"),
        latency_ms=latency_ms,
        input_tokens=0,  # Would need to extract from client
        output_tokens=0,
    )


def run_shadow_comparison(
    scenarios: Sequence[dict],
    api_key: str,
    use_fake_transport: bool = True,
) -> ComparisonSummary:
    """
    Run shadow comparison across scenarios.

    Args:
        scenarios: List of scenario dicts
        api_key: DeepSeek API key (or "fake" for testing)
        use_fake_transport: If True, use fake transport for testing
    """

    # Collect all ref_ids from scenarios
    ref_ids = []
    for scenario in scenarios:
        persona_ref = ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.PERSONA_CLAIM,
            {
                "manifest_id": scenario.get("persona_manifest_id", "test-manifest"),
                "content_fingerprint": "0" * 64,
                "claim_id": "test-claim",
            },
        )
        ref_ids.append(persona_ref.ref_id)

    # Setup fake transport if needed
    if use_fake_transport or api_key == "fake":
        # Capture proposed_reply from each evaluation
        current_reply = {"text": ""}

        def fake_transport(payload):
            import json

            # Extract proposed_reply from the prompt (it's in the user message)
            messages = payload.get("messages", [])
            proposed_reply = current_reply["text"]

            # Use first few characters as quote (ensure it exists)
            reply_quote = proposed_reply[:min(5, len(proposed_reply))] if proposed_reply else "test"

            findings = []
            for idx, axis in enumerate([
                "identity_values",
                "psychological_causality",
                "relationship_scope",
                "knowledge_memory_scope",
                "voice_style",
            ]):
                # Use first ref_id if available
                ref_id = ref_ids[0] if ref_ids else "test-ref"

                findings.append({
                    "axis": axis,
                    "assessment": "aligned",
                    "severity": "info",
                    "reason_code": "aligned",
                    "reply_quote": reply_quote,
                    "occurrence": 0,
                    "supporting_basis_refs": [ref_id],
                    "conflicting_source_refs": [],
                    "voice_activation_refs": [],
                })

            return {
                "choices": [{
                    "message": {
                        "content": json.dumps({"findings": findings}),
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        transport = fake_transport
    else:
        transport = None

    # Create evaluators
    client_on = DeepSeekClient(
        api_key=api_key,
        thinking_enabled=True,
        transport=transport,
    )
    evaluator_on = DeepSeekContinuityEvaluator(
        client=client_on,
        evidence_resolver=FakeEvidenceResolver(),
    )

    client_off = DeepSeekClient(
        api_key=api_key,
        thinking_enabled=False,
        transport=transport,
    )
    evaluator_off = DeepSeekContinuityEvaluator(
        client=client_off,
        evidence_resolver=FakeEvidenceResolver(),
    )

    # Run evaluations
    thinking_on_results = []
    thinking_off_results = []

    for scenario in scenarios:
        print(f"Evaluating scenario: {scenario['scenario_id']}")

        request, ref_id = create_request_from_scenario(scenario)

        # Set current reply for fake transport
        if use_fake_transport or api_key == "fake":
            current_reply["text"] = scenario["proposed_reply"]

        # Thinking ON
        result_on = evaluate_with_thinking(evaluator_on, request, scenario)
        thinking_on_results.append(result_on)

        # Thinking OFF
        result_off = evaluate_with_thinking(evaluator_off, request, scenario)
        thinking_off_results.append(result_off)

    # Compute summary
    differing_scenarios = []
    differing_count = 0

    for on, off in zip(thinking_on_results, thinking_off_results):
        if (
            on.identity_values_assessment != off.identity_values_assessment
            or on.psychological_causality_assessment != off.psychological_causality_assessment
            or on.relationship_scope_assessment != off.relationship_scope_assessment
            or on.knowledge_memory_scope_assessment != off.knowledge_memory_scope_assessment
            or on.voice_style_assessment != off.voice_style_assessment
        ):
            differing_count += 1
            differing_scenarios.append(on.scenario_id)

    avg_latency_on = sum(r.latency_ms for r in thinking_on_results) / len(thinking_on_results)
    avg_latency_off = sum(r.latency_ms for r in thinking_off_results) / len(thinking_off_results)

    return ComparisonSummary(
        total_scenarios=len(scenarios),
        thinking_on_results=tuple(thinking_on_results),
        thinking_off_results=tuple(thinking_off_results),
        thinking_on_avg_latency_ms=avg_latency_on,
        thinking_off_avg_latency_ms=avg_latency_off,
        thinking_on_total_tokens=sum(r.input_tokens + r.output_tokens for r in thinking_on_results),
        thinking_off_total_tokens=sum(r.input_tokens + r.output_tokens for r in thinking_off_results),
        differing_assessments=differing_count,
        differing_scenarios=tuple(differing_scenarios),
    )


def print_summary(summary: ComparisonSummary):
    """Print comparison summary."""
    print("\n" + "="*60)
    print("SHADOW COMPARISON SUMMARY")
    print("="*60)
    print(f"Total scenarios: {summary.total_scenarios}")
    print()
    print("Latency:")
    print(f"  Thinking ON:  {summary.thinking_on_avg_latency_ms:.0f} ms (avg)")
    print(f"  Thinking OFF: {summary.thinking_off_avg_latency_ms:.0f} ms (avg)")
    print(f"  Overhead:     {summary.thinking_on_avg_latency_ms - summary.thinking_off_avg_latency_ms:.0f} ms")
    print()
    print("Tokens:")
    print(f"  Thinking ON:  {summary.thinking_on_total_tokens}")
    print(f"  Thinking OFF: {summary.thinking_off_total_tokens}")
    print()
    print("Assessment Differences:")
    print(f"  Scenarios with different assessments: {summary.differing_assessments}/{summary.total_scenarios}")
    if summary.differing_scenarios:
        print(f"  Differing scenarios: {', '.join(summary.differing_scenarios)}")
    print("="*60)


if __name__ == "__main__":
    # Example scenarios for testing
    test_scenarios = [
        {
            "scenario_id": "basic-greeting",
            "user_message": "你好吗？",
            "proposed_reply": "你好！我很好，谢谢。",
            "expected": "All axes aligned",
        },
        {
            "scenario_id": "memory-reference",
            "user_message": "还记得我们上次说的吗？",
            "proposed_reply": "当然记得！那次聊天很有意思。",
            "expected": "Should reference relationship memory",
        },
    ]

    print("Running shadow comparison with fake transport...")
    summary = run_shadow_comparison(test_scenarios, api_key="fake")
    print_summary(summary)

    print("\nShadow comparison complete!")
    print("\nTo run with real DeepSeek API:")
    print("  python shadow_comparison.py --api-key YOUR_KEY")
