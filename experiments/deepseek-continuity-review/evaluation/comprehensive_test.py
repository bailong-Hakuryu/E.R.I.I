"""Opt-in real-provider evaluation with separate parse and expectation metrics."""

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import time
from typing import Callable

from erii_deepseek_continuity import (
    CrossRelationshipLeakError,
    DeepSeekAPIError,
    DeepSeekClient,
    DeepSeekContinuityEvaluator,
    EvidenceResolutionError,
    ParsingError,
    PromptBudgetError,
)

try:
    from .scenario_resolver import (
        ScenarioEvidenceResolver,
        create_request_from_scenario,
        load_scenario,
        score_expected_assessments,
    )
except ImportError:  # Direct script execution from the evaluation directory.
    from scenario_resolver import (  # type: ignore[no-redef]
        ScenarioEvidenceResolver,
        create_request_from_scenario,
        load_scenario,
        score_expected_assessments,
    )


@dataclass(frozen=True)
class EvaluationRun:
    """Auditable outcome of one provider call."""

    scenario_id: str
    thinking_enabled: bool
    parse_succeeded: bool
    error_code: str | None
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    findings: dict[str, dict[str, str]]
    expectation_score: dict


def run_scenario(
    *,
    api_key: str,
    scenario: dict,
    thinking_enabled: bool,
    model: str = "deepseek-v4-flash",
    timeout_seconds: float = 60.0,
    max_tokens: int = 4096,
    transport: Callable[[dict], dict] | None = None,
) -> EvaluationRun:
    """Run one call; parsing and fixture agreement remain distinct outcomes."""
    request = create_request_from_scenario(scenario)
    client = DeepSeekClient(
        api_key=api_key,
        model=model,
        thinking_enabled=thinking_enabled,
        reasoning_effort="high",
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        transport=transport,
    )
    evaluator = DeepSeekContinuityEvaluator(
        client=client,
        evidence_resolver=ScenarioEvidenceResolver(scenario, request),
    )

    started = time.monotonic()
    decision = None
    error_code = None
    try:
        decision = evaluator.evaluate(request)
    except (
        CrossRelationshipLeakError,
        DeepSeekAPIError,
        EvidenceResolutionError,
        ParsingError,
        PromptBudgetError,
    ) as exc:
        error_code = str(exc) or type(exc).__name__
    except Exception:
        error_code = "unexpected_evaluation_error"
    latency_ms = int((time.monotonic() - started) * 1000)

    usage = client.last_usage
    details = usage.get("completion_tokens_details", {})
    if not isinstance(details, dict):
        details = {}
    findings = {
        finding.axis.value: {
            "assessment": finding.assessment.value,
            "reason_code": finding.reason_code.value,
            "severity": finding.severity.value,
        }
        for finding in decision.findings
    } if decision is not None else {}
    return EvaluationRun(
        scenario_id=scenario["scenario_id"],
        thinking_enabled=thinking_enabled,
        parse_succeeded=decision is not None,
        error_code=error_code,
        latency_ms=latency_ms,
        prompt_tokens=_usage_int(usage.get("prompt_tokens")),
        completion_tokens=_usage_int(usage.get("completion_tokens")),
        reasoning_tokens=_usage_int(details.get("reasoning_tokens")),
        findings=findings,
        expectation_score=score_expected_assessments(decision, scenario),
    )


def build_report(runs: list[EvaluationRun]) -> dict:
    """Aggregate declared-axis matches without treating parseability as accuracy."""
    expected_total = sum(
        run.expectation_score["expected_axes_total"] for run in runs
    )
    expected_matched = sum(
        run.expectation_score["expected_axes_matched"] for run in runs
    )
    return {
        "schema_version": "deepseek-continuity-eval-v2",
        "provider_calls": len(runs),
        "parse_succeeded": sum(1 for run in runs if run.parse_succeeded),
        "expected_axes_total": expected_total,
        "expected_axes_matched": expected_matched,
        "expected_axis_match_rate": (
            expected_matched / expected_total if expected_total else None
        ),
        "runs": [asdict(run) for run in runs],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios-dir",
        type=Path,
        default=Path(__file__).with_name("scenarios"),
    )
    parser.add_argument(
        "--thinking",
        choices=("on", "off", "both"),
        default="both",
    )
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print(
            json.dumps(
                {
                    "status": "not_run",
                    "reason": "DEEPSEEK_API_KEY environment variable is missing",
                }
            )
        )
        return 2

    scenario_paths = sorted(args.scenarios_dir.glob("*.json"))
    scenarios = [load_scenario(path) for path in scenario_paths]
    modes = {
        "on": (True,),
        "off": (False,),
        "both": (True, False),
    }[args.thinking]

    runs: list[EvaluationRun] = []
    for scenario in scenarios:
        for thinking_enabled in modes:
            runs.append(
                run_scenario(
                    api_key=api_key,
                    scenario=scenario,
                    thinking_enabled=thinking_enabled,
                    model=args.model,
                    timeout_seconds=args.timeout_seconds,
                    max_tokens=args.max_tokens,
                )
            )
            if args.delay_seconds > 0:
                time.sleep(args.delay_seconds)

    report = build_report(runs)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)
    if args.output is not None:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0


def _usage_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


if __name__ == "__main__":
    raise SystemExit(main())
