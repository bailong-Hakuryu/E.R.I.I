"""Correctness contracts for the R0 refactoring benchmark."""

import unittest
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from benchmarks.run_refactoring_baseline import (
    BASELINE_JITTER_THRESHOLD_PCT,
    SUITE_VERSION,
    compare_reports,
    main,
    render_comparison,
    run,
)
from scripts.run_refactoring_performance_gate import (
    evaluate_performance_gate,
    render_performance_gate,
    run_gate,
)


BASELINE_PATH = Path(__file__).parents[1] / "benchmarks" / "baselines" / "v0.5.0a3-refactoring-r0.json"


class RefactoringBaselineTests(unittest.TestCase):
    def _baseline(self) -> dict[str, object]:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    def test_requires_at_least_five_samples(self) -> None:
        for value in (0, 4, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "at least 5"):
                    run(value)

    def test_report_is_content_free_and_covers_both_storage_adapters(self) -> None:
        with patch(
            "benchmarks.run_refactoring_baseline._full_sha",
            return_value="a" * 40,
        ):
            report = run(5)

        self.assertEqual(report["suite_version"], SUITE_VERSION)
        self.assertEqual(report["commit"], "a" * 40)
        metrics = report["metrics"]
        expected = {
            "memory_pack_export_file_ms",
            "memory_pack_export_sqlite_ms",
            "memory_pack_import_file_to_file_ms",
            "memory_pack_import_file_to_sqlite_ms",
            "memory_pack_import_sqlite_to_file_ms",
            "memory_pack_import_sqlite_to_sqlite_ms",
            "lifecycle_inspect_file_ms",
            "lifecycle_inspect_sqlite_ms",
            "lifecycle_plan_backup_file_ms",
            "lifecycle_plan_backup_sqlite_ms",
            "lifecycle_execute_backup_file_ms",
            "lifecycle_execute_backup_sqlite_ms",
        }
        self.assertEqual(set(metrics), expected)
        for measurement in metrics.values():
            self.assertEqual(len(measurement["samples_ms"]), 5)
            self.assertGreaterEqual(measurement["median_ms"], 0.0)
            self.assertLessEqual(measurement["minimum_ms"], measurement["maximum_ms"])
        self.assertNotIn("Synthetic portable memory", repr(report))
        self.assertNotIn("original synthetic character", repr(report).lower())

    def test_compare_reports_uses_medians_and_flags_regressions(self) -> None:
        baseline = self._baseline()
        current = deepcopy(baseline)
        current["environment"] = deepcopy(baseline["environment"])
        current["metrics"]["memory_pack_export_sqlite_ms"]["median_ms"] = (
            baseline["metrics"]["memory_pack_export_sqlite_ms"]["median_ms"] * 1.11
        )

        comparison = compare_reports(current, baseline)

        self.assertTrue(comparison.compatible)
        self.assertEqual(len(comparison.regressions), 1)
        regression = comparison.regressions[0]
        self.assertAlmostEqual(regression.delta_pct, 11.0, places=6)
        self.assertIn("REGRESSION", render_comparison(comparison))

    def test_compare_reports_reports_unstable_frozen_baseline(self) -> None:
        baseline = self._baseline()
        current = deepcopy(baseline)
        metric = baseline["metrics"]["memory_pack_export_file_ms"]
        metric["samples_ms"] = [metric["median_ms"] * 0.90, metric["median_ms"] * 1.10]

        comparison = compare_reports(current, baseline)

        self.assertTrue(comparison.compatible)
        self.assertEqual(comparison.regressions, ())
        self.assertGreater(
            comparison.unstable_baseline_metrics[0].baseline_jitter_pct,
            BASELINE_JITTER_THRESHOLD_PCT,
        )
        rendered = render_comparison(comparison)
        self.assertIn("Baseline unstable", rendered)
        self.assertIn("gate inconclusive", rendered)

    def test_file_import_durability_budget_is_explicit_and_enforced(self) -> None:
        baseline = self._baseline()
        current = deepcopy(baseline)
        metric = current["metrics"]["memory_pack_import_file_to_file_ms"]
        metric["median_ms"] = (
            baseline["metrics"]["memory_pack_import_file_to_file_ms"]["median_ms"]
            * 1.50
        )

        accepted = compare_reports(current, baseline)
        self.assertEqual(accepted.blocking_regressions, ())
        self.assertIn("WITHIN DURABILITY BUDGET", render_comparison(accepted))

        metric["median_ms"] = (
            baseline["metrics"]["memory_pack_import_file_to_file_ms"]["median_ms"]
            * 1.60
        )
        rejected = compare_reports(current, baseline)
        self.assertEqual(
            [item.name for item in rejected.blocking_regressions],
            ["memory_pack_import_file_to_file_ms"],
        )

    def test_compare_reports_skips_different_python_or_platform(self) -> None:
        baseline = self._baseline()
        current = deepcopy(baseline)
        current["environment"] = {
            "python": "3.14.7",
            "implementation": "CPython",
            "platform": "Linux-6.8.0",
        }

        comparison = compare_reports(current, baseline)

        self.assertFalse(comparison.compatible)
        self.assertEqual(comparison.metrics, ())
        self.assertIn("Python major/minor differs", comparison.environment_reason)

    def test_compare_reports_skips_different_windows_build(self) -> None:
        baseline = self._baseline()
        current = deepcopy(baseline)
        current["environment"]["platform"] = "Windows-2025Server-10.0.26100-SP0"

        comparison = compare_reports(current, baseline)

        self.assertFalse(comparison.compatible)
        self.assertEqual(comparison.metrics, ())
        self.assertIn("platform build differs", comparison.environment_reason)

    def test_main_returns_nonzero_for_comparison_regression(self) -> None:
        baseline = self._baseline()
        current = deepcopy(baseline)
        current["metrics"]["memory_pack_export_sqlite_ms"]["median_ms"] *= 1.11
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            path.write_text(json.dumps(baseline), encoding="utf-8")
            with patch(
                "benchmarks.run_refactoring_baseline.run",
                return_value=current,
            ):
                result = main(["--compare", str(path), "--iterations", "5"])
        self.assertEqual(result, 1)

    def test_main_skips_comparison_for_incompatible_environment(self) -> None:
        baseline = self._baseline()
        current = deepcopy(baseline)
        current["environment"]["python"] = "3.14.7"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            path.write_text(json.dumps(baseline), encoding="utf-8")
            with patch(
                "benchmarks.run_refactoring_baseline.run",
                return_value=current,
            ):
                result = main(["--compare", str(path), "--iterations", "5"])
        self.assertEqual(result, 0)

    def test_same_environment_gate_enforces_comparability_and_stability(self) -> None:
        baseline = self._baseline()
        current = deepcopy(baseline)

        passed = evaluate_performance_gate(current, baseline)
        self.assertTrue(passed.passed)
        self.assertIn("gate passed", render_performance_gate(passed))

        current["environment"]["python"] = "3.14.7"
        incompatible = evaluate_performance_gate(current, baseline)
        self.assertFalse(incompatible.passed)
        self.assertIn("not comparable", render_performance_gate(incompatible))

        current = deepcopy(baseline)
        metric = current["metrics"]["memory_pack_export_file_ms"]
        metric["samples_ms"] = [
            metric["median_ms"] * 0.8,
            metric["median_ms"] * 1.2,
        ]
        unstable = evaluate_performance_gate(current, baseline)
        self.assertFalse(unstable.passed)
        self.assertIn("unstable same-environment current", render_performance_gate(unstable))

    def test_same_environment_gate_retries_an_unstable_measurement_pair(self) -> None:
        baseline_commit = "a" * 40
        stable_baseline = self._baseline()
        stable_baseline["commit"] = baseline_commit
        stable_current = deepcopy(stable_baseline)
        stable_current["commit"] = "b" * 40
        unstable_baseline = deepcopy(stable_baseline)
        metric = unstable_baseline["metrics"]["memory_pack_export_file_ms"]
        metric["samples_ms"] = [
            metric["median_ms"] * 0.8,
            metric["median_ms"] * 1.2,
        ]
        reports = iter(
            [
                unstable_baseline,
                stable_current,
                stable_baseline,
                stable_current,
            ]
        )

        def write_next_report(_source_root, output, _iterations):
            output.write_text(json.dumps(next(reports)), encoding="utf-8")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps({"commit": baseline_commit}),
                encoding="utf-8",
            )
            with (
                patch(
                    "scripts.run_refactoring_performance_gate._run_checked"
                ),
                patch(
                    "scripts.run_refactoring_performance_gate._run_benchmark",
                    side_effect=write_next_report,
                ) as run_benchmark,
            ):
                evaluation = run_gate(
                    5,
                    manifest,
                    root / "results",
                    stability_attempts=2,
                )

        self.assertTrue(evaluation.passed)
        self.assertEqual(run_benchmark.call_count, 4)


if __name__ == "__main__":
    unittest.main()
