"""Regression contracts for repository automation workflows."""

from pathlib import Path
import unittest


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


if __name__ == "__main__":
    unittest.main()
