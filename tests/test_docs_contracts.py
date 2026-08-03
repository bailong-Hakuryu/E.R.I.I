"""Public documentation contracts for the rc1 source milestone."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class FirstAdoptionDocumentationContractTests(unittest.TestCase):
    def test_first_adoption_docs_define_demo_host_flow_and_api_levels(self) -> None:
        getting_started = (
            REPOSITORY_ROOT / "docs" / "getting-started.md"
        ).read_text(encoding="utf-8")
        host_integration = (
            REPOSITORY_ROOT / "docs" / "host-integration.md"
        ).read_text(encoding="utf-8")
        api_stability = (
            REPOSITORY_ROOT / "docs" / "api-stability.md"
        ).read_text(encoding="utf-8")

        self.assertIn("erii demo --output-dir ./erii-demo", getting_started)
        self.assertIn("User A", getting_started)
        self.assertIn("User B", getting_started)
        self.assertIn("restart", getting_started.lower())
        self.assertIn("provenance", getting_started.lower())
        self.assertIn("user-a.erii", getting_started)
        self.assertIn("Persona projection", getting_started)
        self.assertIn("same synthetic Blueprint", getting_started)

        canonical_flow = (
            "record_turn() → archive_turn() / process_relationship_turn() "
            "→ recall_structured() → export_memory()"
        )
        self.assertIn(canonical_flow, host_integration)
        self.assertIn("the only recommended path", host_integration.lower())

        for level in ("Golden Path", "Advanced", "Experimental", "Internal"):
            self.assertIn(f"## {level}", api_stability)
        golden_path = api_stability.split("## Advanced", maxsplit=1)[0]
        self.assertIn("erii demo --output-dir PATH", golden_path)

    def test_readmes_lead_from_value_to_demo_and_reference(self) -> None:
        readme_contracts = (
            (
                REPOSITORY_ROOT / "README.md",
                (
                    "## 为什么是 E.R.I.I.",
                    "## 从源码安装",
                    "## 一键运行 Golden Continuity Demo",
                    "## 接入真实聊天宿主",
                    "## Reference",
                ),
            ),
            (
                REPOSITORY_ROOT / "README_EN.md",
                (
                    "## Why E.R.I.I.",
                    "## Install from source",
                    "## Run the Golden Continuity Demo",
                    "## Integrate a real chat host",
                    "## Reference",
                ),
            ),
        )

        for path, headings in readme_contracts:
            text = path.read_text(encoding="utf-8")
            positions = [text.index(heading) for heading in headings]
            self.assertEqual(positions, sorted(positions), path.name)
            self.assertIn("erii demo --output-dir ./erii-demo", text)
            self.assertIn("0.x", text)
            self.assertIn("1.0", text)
            self.assertIn("full commit SHA", text)

    def test_identity_and_rc1_execution_status_are_described_precisely(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        readme_en = (REPOSITORY_ROOT / "README_EN.md").read_text(encoding="utf-8")
        strategy = (
            REPOSITORY_ROOT / "docs" / "development-strategy.md"
        ).read_text(encoding="utf-8")
        strategy_en = (
            REPOSITORY_ROOT / "docs" / "development-strategy.en.md"
        ).read_text(encoding="utf-8")

        for text in (readme, readme_en):
            self.assertIn("agent_id", text)
            self.assertIn("relationship_id", text)
            self.assertIn("persona_id", text)
        self.assertIn("共享角色身份", readme)
        self.assertIn("shared character identity", readme_en)
        self.assertNotIn(
            "每个 `Agent × User` 独立的 relationship、persona 和 identity",
            readme,
        )
        self.assertNotIn(
            "independent relationship, persona, and identity for every `Agent × User`",
            readme_en,
        )

        self.assertIn("**已接受：**", strategy)
        self.assertIn("**已在 rc1 实现：**", strategy)
        self.assertIn("**rc1 进行中：**", strategy)
        self.assertIn("**Accepted:**", strategy_en)
        self.assertIn("**Implemented in rc1:**", strategy_en)
        self.assertIn("**In progress for rc1:**", strategy_en)

    def test_b1_is_an_accepted_source_baseline_and_rc1_stays_in_scope(self) -> None:
        baseline_commit = "f6dca322379c4ea88320c69d752cab471d035e95"
        changelog = (REPOSITORY_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        b1_contract = (
            REPOSITORY_ROOT / "docs" / "b1-implementation-contract.md"
        ).read_text(encoding="utf-8")
        b1_notes = (
            REPOSITORY_ROOT / "docs" / "release-notes-0.4.0b1.md"
        ).read_text(encoding="utf-8")
        rc1_notes = (
            REPOSITORY_ROOT / "docs" / "release-notes-0.4.0rc1.md"
        ).read_text(encoding="utf-8")

        self.assertIn("## [0.4.0b1] - 2026-08-03", changelog)
        self.assertIn(baseline_commit, changelog)
        self.assertIn("accepted source baseline", b1_contract.lower())
        self.assertIn("accepted source baseline", b1_notes.lower())
        self.assertIn(baseline_commit, b1_contract)
        self.assertIn(baseline_commit, b1_notes)
        self.assertNotIn("awaiting the final", b1_contract.lower())

        self.assertIn("0.4.0rc1.dev0", rc1_notes)
        self.assertIn("source-closure development snapshot", rc1_notes.lower())
        self.assertIn("does not implement v0.5 relationship consequence", rc1_notes.lower())
        self.assertIn("does not persist deepseek", rc1_notes.lower())

    def test_relative_link_checker_validates_repository_and_reports_failures(self) -> None:
        checker = REPOSITORY_ROOT / "scripts" / "check_docs.py"
        repository_check = subprocess.run(
            [sys.executable, str(checker), str(REPOSITORY_ROOT)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(repository_check.returncode, 0, repository_check.stdout)
        self.assertIn("Markdown files", repository_check.stdout)
        self.assertIn("local links", repository_check.stdout)

        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory)
            (fixture_root / "good.md").write_text(
                "# Real Heading\n\n[valid](good.md#real-heading)\n",
                encoding="utf-8",
            )
            (fixture_root / "broken.md").write_text(
                "[missing file](missing.md)\n"
                "[missing anchor](good.md#not-a-heading)\n",
                encoding="utf-8",
            )
            failure_check = subprocess.run(
                [sys.executable, str(checker), str(fixture_root)],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

        self.assertNotEqual(failure_check.returncode, 0)
        self.assertIn("missing.md", failure_check.stdout)
        self.assertIn("#not-a-heading", failure_check.stdout)

    def test_contribution_templates_require_reproducible_privacy_safe_evidence(
        self,
    ) -> None:
        bug_report = (
            REPOSITORY_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.md"
        ).read_text(encoding="utf-8")
        feature_request = (
            REPOSITORY_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.md"
        ).read_text(encoding="utf-8")
        pull_request = (
            REPOSITORY_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
        ).read_text(encoding="utf-8")

        for text in (bug_report, feature_request, pull_request):
            self.assertIn("synthetic", text.lower())
            self.assertIn("private persona", text.lower())
            self.assertIn("production database", text.lower())
            self.assertIn("api key", text.lower())

        for field in (
            "full 40-character commit SHA",
            "Python version",
            "Operating system",
            "Storage backend",
            "Expected behavior",
            "Actual behavior",
            "Lifecycle step",
        ):
            self.assertIn(field, bug_report)

        for field in (
            "Current observable behavior",
            "Desired observable behavior",
            "Core",
            "Host Integration",
            "Adapter",
            "Labs",
            "Data-format impact",
            "Compatibility impact",
        ):
            self.assertIn(field, feature_request)

        for check in (
            "Tests added or updated",
            "Documentation updated",
            "Contract snapshots updated",
            "relationship isolation",
            "python scripts/check_docs.py",
        ):
            self.assertIn(check, pull_request)


if __name__ == "__main__":
    unittest.main()
