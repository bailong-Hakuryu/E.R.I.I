"""Run the R0 and current refactoring benchmarks in one enforced environment."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.run_refactoring_baseline import (  # noqa: E402
    BaselineComparison,
    compare_reports,
    render_comparison,
    unstable_report_metrics,
)


DEFAULT_BASELINE_MANIFEST = (
    ROOT / "benchmarks" / "baselines" / "v0.5.0a3-refactoring-r0.json"
)
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class PerformanceGateEvaluation:
    """Enforcing verdict for one same-environment benchmark pair."""

    comparison: BaselineComparison
    unstable_baseline_metrics: tuple[str, ...]
    unstable_current_metrics: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return bool(
            self.comparison.compatible
            and not self.comparison.blocking_regressions
            and not self.unstable_baseline_metrics
            and not self.unstable_current_metrics
        )


def evaluate_performance_gate(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> PerformanceGateEvaluation:
    """Requires comparable, stable reports with no blocking regressions."""
    comparison = compare_reports(current, baseline)
    return PerformanceGateEvaluation(
        comparison=comparison,
        unstable_baseline_metrics=unstable_report_metrics(baseline),
        unstable_current_metrics=unstable_report_metrics(current),
    )


def render_performance_gate(evaluation: PerformanceGateEvaluation) -> str:
    """Renders an explicit pass/fail verdict for CI and local handoff."""
    lines = [render_comparison(evaluation.comparison)]
    if evaluation.unstable_baseline_metrics:
        lines.append(
            "Enforced gate failed: unstable same-environment R0 metrics: "
            + ", ".join(evaluation.unstable_baseline_metrics)
        )
    if evaluation.unstable_current_metrics:
        lines.append(
            "Enforced gate failed: unstable same-environment current metrics: "
            + ", ".join(evaluation.unstable_current_metrics)
        )
    if not evaluation.comparison.compatible:
        lines.append("Enforced gate failed: benchmark environments are not comparable.")
    lines.append(
        "Same-environment performance gate passed."
        if evaluation.passed
        else "Same-environment performance gate failed."
    )
    return "\n".join(lines)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=15)
    parser.add_argument(
        "--baseline-manifest",
        type=Path,
        default=DEFAULT_BASELINE_MANIFEST,
        help="frozen R0 report whose commit selects the baseline checkout",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="retain baseline.json and current.json in this directory",
    )
    return parser.parse_args(argv)


def _load_report(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"benchmark report {path} must contain a JSON object")
    return value


def _baseline_commit(path: Path) -> str:
    value = _load_report(path).get("commit")
    if type(value) is not str or _COMMIT_PATTERN.fullmatch(value) is None:
        raise ValueError("frozen R0 baseline commit must be a full lowercase SHA")
    return value


def _run_checked(command: list[str], *, cwd: Path, env=None) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {' '.join(command)}"
        )


def _benchmark_environment(source_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(source_root)
        if not existing
        else str(source_root) + os.pathsep + existing
    )
    return environment


def _run_benchmark(
    source_root: Path,
    output: Path,
    iterations: int,
) -> None:
    script = source_root / "benchmarks" / "run_refactoring_baseline.py"
    if not script.is_file():
        raise RuntimeError(f"benchmark script is missing from {source_root}")
    _run_checked(
        [
            sys.executable,
            str(script),
            "--iterations",
            str(iterations),
            "--output",
            str(output),
        ],
        cwd=source_root,
        env=_benchmark_environment(source_root),
    )


def run_gate(
    iterations: int,
    baseline_manifest: Path,
    output_dir: Path | None,
) -> PerformanceGateEvaluation:
    """Checks out the frozen R0 commit and measures both revisions in one job."""
    if type(iterations) is not int or iterations < 5:
        raise ValueError("iterations must be an integer of at least 5")
    baseline_commit = _baseline_commit(baseline_manifest)
    with tempfile.TemporaryDirectory(prefix="erii-refactoring-gate-") as directory:
        temporary_root = Path(directory)
        baseline_checkout = temporary_root / "baseline-checkout"
        retained = output_dir.resolve() if output_dir is not None else temporary_root
        retained.mkdir(parents=True, exist_ok=True)
        baseline_output = retained / "baseline.json"
        current_output = retained / "current.json"

        _run_checked(
            [
                "git",
                "clone",
                "--quiet",
                "--no-checkout",
                "--shared",
                str(ROOT),
                str(baseline_checkout),
            ],
            cwd=ROOT,
        )
        _run_checked(
            [
                "git",
                "checkout",
                "--quiet",
                "--detach",
                baseline_commit,
            ],
            cwd=baseline_checkout,
        )
        baseline_benchmark = (
            baseline_checkout / "benchmarks" / "run_refactoring_baseline.py"
        )
        baseline_benchmark.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            ROOT / "benchmarks" / "run_refactoring_baseline.py",
            baseline_benchmark,
        )
        _run_benchmark(baseline_checkout, baseline_output, iterations)
        _run_benchmark(ROOT, current_output, iterations)

        baseline = _load_report(baseline_output)
        current = _load_report(current_output)
        if baseline.get("commit") != baseline_commit:
            raise RuntimeError("R0 benchmark did not run at the frozen baseline commit")
        evaluation = evaluate_performance_gate(current, baseline)
        print(render_performance_gate(evaluation))
        if output_dir is not None:
            print(f"Wrote paired benchmark reports to {retained}")
        return evaluation


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        evaluation = run_gate(
            args.iterations,
            args.baseline_manifest,
            args.output_dir,
        )
        return 0 if evaluation.passed else 1
    except Exception as exc:
        print(f"Same-environment performance gate failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
