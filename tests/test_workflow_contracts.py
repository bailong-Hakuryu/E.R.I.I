"""Regression contracts for repository automation workflows."""

from pathlib import Path
import unittest

import erii
from erii.models.pack import MemoryPack


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class WorkflowTriggerContractTests(unittest.TestCase):
    def test_release_recovery_branch_is_excluded_from_general_ci(self) -> None:
        ci_lines = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        ).splitlines()
        release_lines = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8").splitlines()

        general_branch_pattern = '      - "**"'
        recovery_exclusion = '      - "!codex/release-v*"'
        recovery_trigger = '      - "codex/release-v*"'

        self.assertIn(recovery_trigger, release_lines)
        self.assertIn(general_branch_pattern, ci_lines)
        self.assertIn(recovery_exclusion, ci_lines)
        self.assertLess(
            ci_lines.index(general_branch_pattern),
            ci_lines.index(recovery_exclusion),
            "GitHub evaluates branch patterns in order, so the exclusion must follow **",
        )


class BetaDevelopmentContractTests(unittest.TestCase):
    def test_package_and_memory_pack_versions_have_independent_lifecycles(self) -> None:
        self.assertEqual(erii.__version__, "0.4.0b1.dev0")
        self.assertEqual(MemoryPack.CURRENT_VERSION, "0.4.0a8")

    def test_python_support_contract_is_synchronized(self) -> None:
        pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        ci = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        release = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('requires-python = ">=3.11"', pyproject)
        self.assertIn('requires = ["setuptools>=77.0.0"]', pyproject)
        self.assertIn('license = "Apache-2.0"', pyproject)
        self.assertIn('license-files = ["LICENSE"]', pyproject)
        self.assertNotIn("License :: OSI Approved :: Apache Software License", pyproject)
        self.assertIn('target-version = "py311"', pyproject)
        self.assertNotIn("Programming Language :: Python :: 3.9", pyproject)
        self.assertNotIn("Programming Language :: Python :: 3.10", pyproject)
        self.assertIn("Programming Language :: Python :: 3.14", pyproject)
        self.assertIn('python-version: ["3.11", "3.14"]', ci)
        self.assertIn("windows-smoke:", ci)
        self.assertIn("runs-on: windows-latest", ci)
        self.assertIn('test_lifecycle_inspection.py" -v', ci)
        self.assertIn('test_lifecycle_backup_restore.py" -v', ci)
        self.assertIn('python-version: ["3.11", "3.14"]', release)
        self.assertNotIn('python-version: "3.9"', release)

    def test_prerelease_title_is_not_tied_to_an_old_feature_release(self) -> None:
        release = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('--title "E.R.I.I. ${RELEASE_TAG}"', release)
        self.assertIn("for example v0.4.0b1", release)
        self.assertNotIn("for example v0.4.0a8", release)
        self.assertNotIn("Continuity Audit and Release Closeout", release)


if __name__ == "__main__":
    unittest.main()
