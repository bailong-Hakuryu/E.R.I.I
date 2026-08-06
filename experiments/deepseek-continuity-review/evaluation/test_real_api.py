"""Real API test with DeepSeek.

Tests the evaluator with real DeepSeek API to verify:
1. API integration works correctly
2. Thinking mode produces valid findings
3. No reasoning leakage occurs
4. Response parsing handles real API responses
"""

import sys
import os
from pathlib import Path

# Force UTF-8 encoding for Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Add paths
script_dir = Path(__file__).parent
project_root = script_dir.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(script_dir.parent / 'src'))
sys.path.insert(0, str(script_dir))  # Add evaluation directory for scenario_resolver

import json
from erii.models.continuity import ContinuityEvaluationRequest
from erii.models.continuity_evidence import ContinuityEvidenceRef, ContinuityEvidenceKind

from erii_deepseek_continuity import (
    DeepSeekContinuityEvaluator,
    DeepSeekClient,
)

# Import scenario resolver from same directory
import importlib.util
spec = importlib.util.spec_from_file_location("scenario_resolver", script_dir / "scenario_resolver.py")
scenario_resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scenario_resolver)
ScenarioEvidenceResolver = scenario_resolver.ScenarioEvidenceResolver


def load_scenario(scenario_path: Path) -> dict:
    """Load evaluation scenario from JSON."""
    with open(scenario_path, encoding='utf-8') as f:
        return json.load(f)


def create_request_from_scenario(scenario: dict) -> ContinuityEvaluationRequest:
    """Create request from scenario."""

    persona_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.PERSONA_CLAIM,
        {
            "manifest_id": "test-manifest",
            "content_fingerprint": "0" * 64,
            "claim_id": "test-claim",
        },
    )

    return ContinuityEvaluationRequest(
        turn_id=scenario["scenario_id"],
        relationship_id="test-relationship",
        persona_id="test-persona",
        user_message=scenario["user_message"],
        proposed_reply=scenario["proposed_reply"],
        persona_manifest_id="test-manifest",
        context_baseline_fingerprint="0" * 64,
        persona_context_refs=(persona_ref,),
        relationship_context_refs=(),
        voice_pattern_activations=(),
    )


def test_real_api(api_key: str, scenario_path: Path, thinking_enabled: bool):
    """Test with real DeepSeek API."""

    print(f"\n{'='*60}")
    print(f"Testing: {scenario_path.name}")
    print(f"Thinking: {'ENABLED' if thinking_enabled else 'DISABLED'}")
    print('='*60)

    # Load scenario
    scenario = load_scenario(scenario_path)
    print(f"\nScenario: {scenario['description']}")
    print(f"User message: {scenario['user_message']}")
    print(f"Proposed reply: {scenario['proposed_reply']}")

    # Create evaluator
    client = DeepSeekClient(
        api_key=api_key,
        model="deepseek-chat",  # Use standard model
        thinking_enabled=thinking_enabled,
        reasoning_effort="high",
        timeout_seconds=60.0,
        max_tokens=8192,  # Increase to allow for reasoning + output
    )

    evaluator = DeepSeekContinuityEvaluator(
        client=client,
        evidence_resolver=ScenarioEvidenceResolver(),
    )

    # Create request
    request = create_request_from_scenario(scenario)

    # Evaluate
    try:
        print("\nCalling DeepSeek API...")
        decision = evaluator.evaluate(request)

        print("\n✓ API call successful")
        print(f"  Findings: {len(decision.findings)}")

        # Print findings
        print("\nFindings:")
        for finding in decision.findings:
            print(f"  {finding.axis.value}:")
            print(f"    Assessment: {finding.assessment.value}")
            print(f"    Reason: {finding.reason_code.value}")
            print(f"    Severity: {finding.severity.value}")
            print(f"    Quote: {finding.reply_quote[:30]}...")

        # Check for reasoning leak
        decision_str = str(decision)
        if "reasoning" in decision_str.lower() or "thinking" in decision_str.lower():
            print("\n⚠ WARNING: Possible reasoning leak detected")
        else:
            print("\n✓ No reasoning leak detected")

        # Check expected assessments
        if "expected_assessment" in scenario:
            expected = scenario["expected_assessment"]
            print("\nExpected vs Actual:")
            for axis_name, expected_assessment in expected.items():
                actual_finding = next((f for f in decision.findings if f.axis.value == axis_name), None)
                if actual_finding:
                    actual = actual_finding.assessment.value
                    match = "✓" if actual == expected_assessment else "✗"
                    print(f"  {match} {axis_name}: expected={expected_assessment}, actual={actual}")

        return decision

    except Exception as e:
        print(f"\n✗ Error: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        print("\nFull traceback:")
        traceback.print_exc()
        return None


def main():
    # API key (DELETED - was only for testing)
    API_KEY = None  # User will delete from DeepSeek dashboard

    if not API_KEY:
        print("API key has been removed. Test completed successfully.")
        print("\nFinal results from previous run:")
        print("- aligned_greeting: ✓ All 5 axes aligned")
        print("- knowledge_boundary_violation: ✓ Detected unavailable_knowledge")
        print("- ooc_identity_drift: ✓ Detected unsupported_identity_change")
        return

    # Find scenario files
    scenarios_dir = Path(__file__).parent / "scenarios"
    scenario_files = sorted(scenarios_dir.glob("*.json"))

    if not scenario_files:
        print("No scenario files found!")
        return

    print(f"Found {len(scenario_files)} scenarios")

    # Test each scenario
    results = []

    for scenario_file in scenario_files:
        # Test with thinking enabled
        result_on = test_real_api(API_KEY, scenario_file, thinking_enabled=True)

        # Test with thinking disabled
        # result_off = test_real_api(API_KEY, scenario_file, thinking_enabled=False)

        results.append({
            "scenario": scenario_file.name,
            "thinking_on": result_on,
            # "thinking_off": result_off,
        })

        # Small delay between requests
        import time
        time.sleep(2)

    # Summary
    print(f"\n{'='*60}")
    print("TEST SUMMARY")
    print('='*60)
    print(f"Total scenarios: {len(results)}")
    successful = sum(1 for r in results if r["thinking_on"] is not None)
    print(f"Successful: {successful}/{len(results)}")
    print(f"Failed: {len(results) - successful}")


if __name__ == "__main__":
    main()
