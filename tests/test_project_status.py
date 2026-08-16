"""Contracts for the machine-readable project-status catalog."""

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.project_status import (
    CatalogError,
    DEFAULT_SOURCE,
    ROOT,
    _tracked_paths,
    load_catalog,
    render_catalog,
    validate_catalog,
)


class ProjectStatusCatalogTests(unittest.TestCase):
    def test_repository_catalog_is_valid_and_dashboard_is_current(self) -> None:
        catalog = load_catalog()
        validate_catalog(catalog)
        dashboard = (ROOT / "docs" / "PROJECT_STATUS.md").read_text(encoding="utf-8")

        self.assertEqual(dashboard, render_catalog(catalog))
        self.assertEqual(catalog["program"]["phase"], "R1B")
        self.assertEqual(catalog["program"]["status"], "complete")
        self.assertIn("Character Deliberation C0/G2", dashboard)
        self.assertIn("`placeholder`", dashboard)
        self.assertIn("`planned`", dashboard)

    def test_duplicate_module_ids_are_rejected(self) -> None:
        catalog = load_catalog()
        duplicate = deepcopy(catalog["modules"][0])
        catalog["modules"].append(duplicate)

        with self.assertRaisesRegex(CatalogError, "module ids must be unique"):
            validate_catalog(catalog)

    def test_untracked_or_unsafe_paths_are_rejected(self) -> None:
        for invalid_path, message in (
            ("../outside", "unsafe path"),
            ("erii/does-not-exist", "not tracked"),
        ):
            with self.subTest(path=invalid_path):
                catalog = load_catalog()
                catalog["modules"][0]["paths"] = [invalid_path]
                with self.assertRaisesRegex(CatalogError, message):
                    validate_catalog(catalog)

    def test_unignored_candidate_path_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "candidate.py").write_text("value = 1\n", encoding="utf-8")

            self.assertIn("candidate.py", _tracked_paths(root))

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
            with self.assertRaisesRegex(CatalogError, "duplicate JSON key"):
                load_catalog(path)

    def test_check_mode_fails_for_a_stale_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "status.md"
            output.write_text("stale\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "project_status.py"),
                    "--source",
                    str(DEFAULT_SOURCE),
                    "--output",
                    str(output),
                    "--check",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("dashboard is stale", completed.stdout)


if __name__ == "__main__":
    unittest.main()
