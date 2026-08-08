"""Offline checks for scenario resolution and auditable scoring."""

from pathlib import Path
from types import SimpleNamespace

from evaluation.comprehensive_test import EvaluationRun, build_report, run_scenario
from evaluation.scenario_resolver import (
    ScenarioEvidenceResolver,
    create_request_from_scenario,
    load_scenario,
    score_expected_assessments,
)
from evaluation.shadow_comparison import offline_contract_transport

SCENARIOS = Path(__file__).parents[1] / "evaluation" / "scenarios"


def test_all_synthetic_scenarios_are_structurally_resolvable() -> None:
    for path in sorted(SCENARIOS.glob("*.json")):
        scenario = load_scenario(path)
        request = create_request_from_scenario(scenario)
        resolver = ScenarioEvidenceResolver(scenario, request)
        resolved = resolver.resolve(
            request.persona_context_refs,
            request.relationship_context_refs,
            request.relationship_id,
        )
        assert len(resolved) == (
            len(scenario["persona"]["key_traits"])
            + len(scenario.get("relationship_evidence", []))
        )


def test_score_uses_only_declared_expected_axes() -> None:
    scenario = {
        "expected_assessment": {
            "identity_values": "supported",
            "psychological_causality": "review",
        }
    }
    decision = SimpleNamespace(
        findings=(
            SimpleNamespace(
                axis=SimpleNamespace(value="identity_values"),
                assessment=SimpleNamespace(value="supported"),
            ),
            SimpleNamespace(
                axis=SimpleNamespace(value="voice_style"),
                assessment=SimpleNamespace(value="unsupported"),
            ),
        )
    )
    score = score_expected_assessments(decision, scenario)
    assert score["expected_axes_total"] == 2
    assert score["expected_axes_matched"] == 1
    assert score["expectations_met"] is False
    assert [item["axis"] for item in score["axes"]] == [
        "identity_values",
        "psychological_causality",
    ]


def test_report_does_not_treat_parse_success_as_accuracy() -> None:
    run = EvaluationRun(
        scenario_id="scenario",
        thinking_enabled=True,
        parse_succeeded=True,
        error_code=None,
        latency_ms=1,
        prompt_tokens=10,
        completion_tokens=5,
        reasoning_tokens=0,
        findings={},
        expectation_score={
            "expected_axes_total": 2,
            "expected_axes_matched": 0,
            "expectations_met": False,
            "axes": [],
        },
    )
    report = build_report([run])
    assert report["parse_succeeded"] == 1
    assert report["expected_axes_matched"] == 0
    assert report["expected_axis_match_rate"] == 0.0


def test_offline_transport_validates_contract_without_network() -> None:
    scenario = load_scenario(SCENARIOS / "aligned_greeting.json")
    run = run_scenario(
        api_key="offline-fixture-key",
        scenario=scenario,
        thinking_enabled=True,
        transport=offline_contract_transport,
    )
    assert run.parse_succeeded is True
    assert run.expectation_score["expected_axes_matched"] == 5
