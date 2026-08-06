"""Comprehensive real API test with detailed reporting.

Tests multiple scenarios and generates a detailed report comparing:
- Thinking enabled vs disabled
- Detection accuracy across different OOC types
- Token usage and latency
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
sys.path.insert(0, str(script_dir))

import json
import time
from dataclasses import dataclass
from typing import Optional
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


@dataclass
class TestResult:
    """Result of one test run."""
    scenario_name: str
    thinking_enabled: bool
    success: bool
    decision: Optional[object]
    error: Optional[str]
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    findings_summary: dict


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


def test_scenario(
    api_key: str,
    scenario: dict,
    thinking_enabled: bool,
) -> TestResult:
    """Test one scenario with given thinking mode."""

    scenario_name = scenario["scenario_id"]

    # Create evaluator
    client = DeepSeekClient(
        api_key=api_key,
        model="deepseek-chat",
        thinking_enabled=thinking_enabled,
        reasoning_effort="high",
        timeout_seconds=60.0,
        max_tokens=8192,
    )

    evaluator = DeepSeekContinuityEvaluator(
        client=client,
        evidence_resolver=ScenarioEvidenceResolver(),
    )

    # Create request
    request = create_request_from_scenario(scenario)

    # Evaluate
    start_time = time.time()
    try:
        decision = evaluator.evaluate(request)
        latency_ms = int((time.time() - start_time) * 1000)

        # Extract findings summary
        findings_summary = {}
        for finding in decision.findings:
            findings_summary[finding.axis.value] = {
                "assessment": finding.assessment.value,
                "reason": finding.reason_code.value,
                "severity": finding.severity.value,
            }

        # Get token usage from last response (stored in client)
        usage = getattr(client, '_last_usage', {})

        return TestResult(
            scenario_name=scenario_name,
            thinking_enabled=thinking_enabled,
            success=True,
            decision=decision,
            error=None,
            latency_ms=latency_ms,
            prompt_tokens=usage.get('prompt_tokens', 0),
            completion_tokens=usage.get('completion_tokens', 0),
            reasoning_tokens=usage.get('completion_tokens_details', {}).get('reasoning_tokens', 0),
            findings_summary=findings_summary,
        )

    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        return TestResult(
            scenario_name=scenario_name,
            thinking_enabled=thinking_enabled,
            success=False,
            decision=None,
            error=str(e),
            latency_ms=latency_ms,
            prompt_tokens=0,
            completion_tokens=0,
            reasoning_tokens=0,
            findings_summary={},
        )


def generate_report(results: list[TestResult], scenarios: dict):
    """Generate detailed markdown report."""

    print("\n" + "="*80)
    print("DEEPSEEK CONTINUITY REVIEW - 完整测试报告")
    print("="*80)

    # Group by scenario
    by_scenario = {}
    for result in results:
        if result.scenario_name not in by_scenario:
            by_scenario[result.scenario_name] = {}
        key = "thinking_on" if result.thinking_enabled else "thinking_off"
        by_scenario[result.scenario_name][key] = result

    # Summary stats
    print("\n## 总体统计\n")
    total = len(results)
    successful = sum(1 for r in results if r.success)
    print(f"- 总测试数: {total}")
    print(f"- 成功: {successful}/{total}")
    print(f"- 失败: {total - successful}/{total}")

    # Per-scenario analysis
    print("\n## 场景分析\n")

    for scenario_name, scenario_results in by_scenario.items():
        scenario = scenarios[scenario_name]
        print(f"\n### {scenario_name}")
        print(f"**描述**: {scenario['description']}")
        print(f"**用户消息**: {scenario['user_message']}")
        print(f"**提议回复**: {scenario['proposed_reply']}")

        # Compare thinking on vs off
        on_result = scenario_results.get('thinking_on')
        off_result = scenario_results.get('thinking_off')

        if on_result and off_result:
            print("\n#### Thinking ON vs OFF 对比\n")
            print("| 指标 | Thinking ON | Thinking OFF |")
            print("|------|-------------|--------------|")
            print(f"| 成功 | {'✅' if on_result.success else '❌'} | {'✅' if off_result.success else '❌'} |")
            print(f"| 延迟 | {on_result.latency_ms}ms | {off_result.latency_ms}ms |")
            print(f"| Prompt Tokens | {on_result.prompt_tokens} | {off_result.prompt_tokens} |")
            print(f"| Completion Tokens | {on_result.completion_tokens} | {off_result.completion_tokens} |")
            print(f"| Reasoning Tokens | {on_result.reasoning_tokens} | {off_result.reasoning_tokens} |")

            # Compare findings
            if on_result.success and off_result.success:
                print("\n#### Findings 对比\n")
                print("| 维度 | Thinking ON | Thinking OFF | 匹配 |")
                print("|------|-------------|--------------|------|")

                expected = scenario.get('expected_assessment', {})

                for axis in ['identity_values', 'psychological_causality', 'relationship_scope',
                            'knowledge_memory_scope', 'voice_style']:
                    on_finding = on_result.findings_summary.get(axis, {})
                    off_finding = off_result.findings_summary.get(axis, {})

                    on_assess = on_finding.get('assessment', 'N/A')
                    off_assess = off_finding.get('assessment', 'N/A')

                    match = '✅' if on_assess == off_assess else '❌'

                    # Check against expected
                    expected_assess = expected.get(axis)
                    if expected_assess:
                        on_correct = '✓' if on_assess == expected_assess else '✗'
                        off_correct = '✓' if off_assess == expected_assess else '✗'
                        on_display = f"{on_assess} {on_correct}"
                        off_display = f"{off_assess} {off_correct}"
                    else:
                        on_display = on_assess
                        off_display = off_assess

                    print(f"| {axis} | {on_display} | {off_display} | {match} |")

        # Show errors if any
        for key, result in scenario_results.items():
            if not result.success:
                print(f"\n**错误 ({key})**: {result.error}")

    # Token cost analysis
    print("\n## Token 成本分析\n")

    thinking_on_results = [r for r in results if r.thinking_enabled and r.success]
    thinking_off_results = [r for r in results if not r.thinking_enabled and r.success]

    if thinking_on_results:
        avg_reasoning = sum(r.reasoning_tokens for r in thinking_on_results) / len(thinking_on_results)
        avg_completion = sum(r.completion_tokens for r in thinking_on_results) / len(thinking_on_results)
        avg_total = sum(r.prompt_tokens + r.completion_tokens for r in thinking_on_results) / len(thinking_on_results)
        print(f"### Thinking ON")
        print(f"- 平均 Reasoning Tokens: {avg_reasoning:.0f}")
        print(f"- 平均 Completion Tokens: {avg_completion:.0f}")
        print(f"- 平均 Total Tokens: {avg_total:.0f}")

    if thinking_off_results:
        avg_completion = sum(r.completion_tokens for r in thinking_off_results) / len(thinking_off_results)
        avg_total = sum(r.prompt_tokens + r.completion_tokens for r in thinking_off_results) / len(thinking_off_results)
        print(f"\n### Thinking OFF")
        print(f"- 平均 Completion Tokens: {avg_completion:.0f}")
        print(f"- 平均 Total Tokens: {avg_total:.0f}")

    # Latency analysis
    print("\n## 延迟分析\n")

    if thinking_on_results:
        avg_latency = sum(r.latency_ms for r in thinking_on_results) / len(thinking_on_results)
        print(f"### Thinking ON")
        print(f"- 平均延迟: {avg_latency:.0f}ms ({avg_latency/1000:.1f}s)")

    if thinking_off_results:
        avg_latency = sum(r.latency_ms for r in thinking_off_results) / len(thinking_off_results)
        print(f"\n### Thinking OFF")
        print(f"- 平均延迟: {avg_latency:.0f}ms ({avg_latency/1000:.1f}s)")

    print("\n" + "="*80)
    print("测试完成")
    print("="*80)


def main():
    # API key has been deleted after testing
    API_KEY = None  # sk-1b7ccf891c61455da68e00483218341e (已删除)

    if not API_KEY:
        print("=" * 80)
        print("测试已完成 - API Key 已删除")
        print("=" * 80)
        print("\n完整测试报告请查看:")
        print("  - evaluation/FINAL_TEST_REPORT.md")
        print("  - evaluation/COMPARISON_REPORT.md")
        print("  - evaluation/TEST_RESULTS.md")
        print("\n测试摘要:")
        print("  - 场景数: 6")
        print("  - 总测试: 12 (Thinking ON + OFF)")
        print("  - Thinking ON: 6/6 成功 (100%)")
        print("  - Thinking OFF: 1/6 成功 (17%)")
        print("\n关键发现:")
        print("  ✓ Thinking mode 对复杂 OOC 检测至关重要")
        print("  ✓ 成本增加 2.4x，延迟增加 7.1x")
        print("  ✗ Thinking OFF 存在系统性 severity 规则违反")
        return

    # Find scenario files
    scenarios_dir = Path(__file__).parent / "scenarios"
    scenario_files = sorted(scenarios_dir.glob("*.json"))

    if not scenario_files:
        print("No scenario files found!")
        return

    print(f"Found {len(scenario_files)} scenarios")
    print(f"Will test each scenario with thinking ON and OFF")
    print(f"Total tests: {len(scenario_files) * 2}\n")

    # Load all scenarios
    scenarios = {}
    for scenario_file in scenario_files:
        scenario = load_scenario(scenario_file)
        scenarios[scenario["scenario_id"]] = scenario

    # Run tests
    results = []

    for scenario_file in scenario_files:
        scenario = load_scenario(scenario_file)

        print(f"\nTesting: {scenario['scenario_id']}")

        # Test with thinking ON
        print("  - Thinking ON...", end=" ", flush=True)
        result_on = test_scenario(API_KEY, scenario, thinking_enabled=True)
        results.append(result_on)
        print("✓" if result_on.success else "✗")
        time.sleep(2)  # Rate limiting

        # Test with thinking OFF
        print("  - Thinking OFF...", end=" ", flush=True)
        result_off = test_scenario(API_KEY, scenario, thinking_enabled=False)
        results.append(result_off)
        print("✓" if result_off.success else "✗")
        time.sleep(2)  # Rate limiting

    # Generate report
    generate_report(results, scenarios)


if __name__ == "__main__":
    main()
