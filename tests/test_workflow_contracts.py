"""Regression contracts for repository automation workflows."""

from pathlib import Path
import unittest

import erii
from erii.models.pack import MemoryPack


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class SourceMilestoneWorkflowContractTests(unittest.TestCase):
    def test_zero_x_source_milestones_have_no_github_release_publication_path(self) -> None:
        release = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", release)
        self.assertIn("commit_sha:", release)
        self.assertIn("expected_version:", release)
        self.assertIn("full 40-character commit SHA", release)
        self.assertIn("EXPECTED_VERSION", release)
        self.assertIn("Source version mismatch", release)
        self.assertNotIn('      - "v*"', release)
        self.assertNotIn("codex/release-v*", release)
        self.assertNotIn("gh release create", release)
        self.assertNotIn("gh release upload", release)
        self.assertNotIn("contents: write", release)

    def test_source_verification_preserves_build_and_clean_install_checks(self) -> None:
        ci = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        source_verification = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("python -m build", ci)
        self.assertIn("package-install-windows:", ci)
        self.assertIn("python -m pip install $installSpec httpx", ci)
        self.assertNotIn("!codex/release-v*", ci)

        self.assertIn("python -m build", source_verification)
        self.assertIn("python -m twine check --strict dist/*", source_verification)
        self.assertIn('python -m pip install "${artifact}[server]" httpx', source_verification)
        self.assertIn("python -m pip install $installSpec httpx", source_verification)

    def test_clean_base_install_runs_the_golden_demo(self) -> None:
        ci = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        source_verification = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("erii demo --output-dir", ci)
        self.assertIn("erii demo --output-dir", source_verification)
        self.assertIn("MemoryPack.from_json", ci)
        self.assertIn("MemoryPack.from_json", source_verification)

    def test_exact_sha_verification_includes_longitudinal_continuity(self) -> None:
        source_verification = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("longitudinal:", source_verification)
        self.assertIn("Run all fixed trajectories on FileStorage and SQLite", source_verification)
        self.assertIn("--adapter both", source_verification)
        self.assertIn("--scenario all", source_verification)
        self.assertIn("v0.4.0-longitudinal.json", source_verification)
        self.assertIn("needs: [preflight, verify, longitudinal]", source_verification)


class StableSourceContractTests(unittest.TestCase):
    def test_package_and_memory_pack_versions_have_independent_lifecycles(self) -> None:
        self.assertEqual(erii.__version__, "0.5.0a1")
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
        self.assertIn("Development Status :: 4 - Beta", pyproject)
        self.assertNotIn("Development Status :: 3 - Alpha", pyproject)
        self.assertNotIn("License :: OSI Approved :: Apache Software License", pyproject)
        self.assertIn('target-version = "py311"', pyproject)
        self.assertNotIn("Programming Language :: Python :: 3.9", pyproject)
        self.assertNotIn("Programming Language :: Python :: 3.10", pyproject)
        self.assertIn("Programming Language :: Python :: 3.14", pyproject)
        self.assertIn(
            'python-version: ["3.11", "3.12", "3.13", "3.14"]',
            ci,
        )
        self.assertIn("windows-smoke:", ci)
        self.assertIn("runs-on: windows-latest", ci)
        self.assertIn('test_lifecycle_inspection.py" -v', ci)
        self.assertIn('test_lifecycle_backup_restore.py" -v', ci)
        self.assertIn(
            'python-version: ["3.11", "3.12", "3.13", "3.14"]',
            release,
        )
        self.assertNotIn('python-version: "3.9"', release)

    def test_source_package_metadata_describes_the_actual_project(self) -> None:
        pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn(
            'description = "Character continuity and long-term memory kernel for AI agents"',
            pyproject,
        )
        self.assertIn('{ name = "bailong-Hakuryu" }', pyproject)
        self.assertIn("[project.urls]", pyproject)
        self.assertIn(
            'Repository = "https://github.com/bailong-Hakuryu/E.R.I.I"',
            pyproject,
        )
        self.assertIn("keywords = [", pyproject)
        self.assertNotIn("Operating System :: OS Independent", pyproject)

    def test_source_verification_is_not_tied_to_an_old_feature_release(self) -> None:
        release = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("source milestone", release.lower())
        self.assertNotIn("for example v0.4.0a8", release)
        self.assertNotIn("Continuity Audit and Release Closeout", release)


if __name__ == "__main__":
    unittest.main()
