"""Run fixed original longitudinal trajectories and emit content-free JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from erii.evaluation import (
    FileStorageEvalAdapter,
    LongitudinalEvalRunner,
    SQLiteEvalAdapter,
    correction_and_growth_scenario,
    default_fault_schedule,
    interleaved_relationships_scenario,
    single_relationship_scenario,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run E.R.I.I. fixed longitudinal evaluations.",
    )
    parser.add_argument(
        "--adapter",
        choices=("file", "sqlite", "both"),
        default="both",
    )
    parser.add_argument(
        "--scenario",
        choices=("single", "interleaved", "correction", "all"),
        default="all",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Persistent data directory; a temporary directory is used by default.",
    )
    return parser.parse_args()


def _scenarios(selection: str):
    values = {
        "single": single_relationship_scenario,
        "interleaved": interleaved_relationships_scenario,
        "correction": correction_and_growth_scenario,
    }
    keys = tuple(values) if selection == "all" else (selection,)
    return tuple(values[key]() for key in keys)


def _run(workspace: Path, adapter_selection: str, scenario_selection: str):
    runner = LongitudinalEvalRunner()
    reports = []
    for scenario in _scenarios(scenario_selection):
        safe_id = scenario.scenario_id.replace("/", "-")
        adapters = []
        if adapter_selection in {"file", "both"}:
            adapters.append(FileStorageEvalAdapter(workspace / safe_id / "files"))
        if adapter_selection in {"sqlite", "both"}:
            adapters.append(SQLiteEvalAdapter(workspace / safe_id / "memory.db"))
        for adapter in adapters:
            reports.append(
                runner.run(
                    scenario,
                    adapter,
                    default_fault_schedule(scenario.scenario_id),
                ).to_dict()
            )
    return {
        "suite_version": "longitudinal-eval-suite/v1",
        "passed": all(bool(report["passed"]) for report in reports),
        "reports": reports,
    }


def main() -> int:
    args = _arguments()
    temporary = None
    if args.workspace is None:
        temporary = tempfile.TemporaryDirectory()
        workspace = Path(temporary.name)
    else:
        workspace = args.workspace
        workspace.mkdir(parents=True, exist_ok=True)
    try:
        result = _run(workspace, args.adapter, args.scenario)
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.output is None:
            print(payload, end="")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        return 0 if result["passed"] else 1
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
