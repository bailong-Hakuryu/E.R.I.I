"""Compare thinking modes using an explicitly selected offline or real transport."""

import argparse
import json
import os
from pathlib import Path

try:
    from .comprehensive_test import build_report, run_scenario
    from .scenario_resolver import load_scenario
except ImportError:  # Direct script execution from the evaluation directory.
    from comprehensive_test import build_report, run_scenario  # type: ignore[no-redef]
    from scenario_resolver import load_scenario  # type: ignore[no-redef]

_AXES = (
    "identity_values",
    "psychological_causality",
    "relationship_scope",
    "knowledge_memory_scope",
    "voice_style",
)


def offline_contract_transport(payload: dict) -> dict:
    """Return aligned findings to test plumbing, not model quality."""
    user_content = payload["messages"][1]["content"]
    scenario_payload = json.loads(user_content.split("\n", 1)[1])
    evidence = scenario_payload["evidence"]
    if not evidence:
        raise ValueError("offline_fixture_requires_evidence")
    ref_id = evidence[0]["ref_id"]
    reply = scenario_payload["proposed_reply"]
    quote = reply[: min(20, len(reply))]
    findings = [
        {
            "axis": axis,
            "assessment": "aligned",
            "severity": "info",
            "reason_code": "aligned",
            "reply_quote": quote,
            "occurrence": 0,
            "supporting_basis_refs": [ref_id],
            "conflicting_source_refs": [],
            "voice_activation_refs": [],
        }
        for axis in _AXES
    ]
    return {
        "choices": [
            {
                "message": {"content": json.dumps({"findings": findings})},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("offline", "real"), required=True)
    parser.add_argument(
        "--scenarios-dir",
        type=Path,
        default=Path(__file__).with_name("scenarios"),
    )
    parser.add_argument("--model", default="deepseek-v4-flash")
    args = parser.parse_args(argv)

    if args.transport == "real":
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            print(json.dumps({"status": "not_run", "reason": "missing API key"}))
            return 2
        transport = None
    else:
        api_key = "offline-fixture-key"
        transport = offline_contract_transport

    scenarios = [
        load_scenario(path) for path in sorted(args.scenarios_dir.glob("*.json"))
    ]
    runs = [
        run_scenario(
            api_key=api_key,
            scenario=scenario,
            thinking_enabled=thinking_enabled,
            model=args.model,
            transport=transport,
        )
        for scenario in scenarios
        for thinking_enabled in (True, False)
    ]
    report = build_report(runs)
    report["transport"] = args.transport
    report["offline_quality_claim"] = (
        "none; the offline transport validates contracts only"
        if args.transport == "offline"
        else None
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
