"""Correctness contracts for the R0 refactoring benchmark."""

import unittest
from unittest.mock import patch

from benchmarks.run_refactoring_baseline import SUITE_VERSION, run


class RefactoringBaselineTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
