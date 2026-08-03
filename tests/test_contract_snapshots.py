"""Source-contract snapshot tests for the v0.4.0rc1 development line."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "freeze_contracts.py"
COMMITTED = ROOT / "docs" / "contracts"
BASELINE_RELEASE = "0.4.0b1"
CURRENT_RELEASE = "0.4.0rc1"
CONTRACT_KINDS = (
    "data-formats",
    "openapi",
    "python-api",
    "sqlite-schema",
)


def contract_path(release: str, kind: str) -> Path:
    return COMMITTED / f"v{release}-{kind}.json"


def read_contract(release: str, kind: str) -> dict:
    return json.loads(contract_path(release, kind).read_text(encoding="utf-8"))


class ContractSnapshotTests(unittest.TestCase):
    """Keeps generated public and durable contracts reviewable in Git."""

    def test_generated_snapshots_match_committed_contracts_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            generated = Path(temporary_directory) / "contracts"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--output-dir", str(generated)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            expected_names = sorted(
                f"v{CURRENT_RELEASE}-{kind}.json" for kind in CONTRACT_KINDS
            )
            actual_names = sorted(path.name for path in generated.glob("*.json"))
            self.assertEqual(actual_names, expected_names)
            for name in actual_names:
                committed_bytes = (COMMITTED / name).read_bytes()
                generated_bytes = (generated / name).read_bytes()
                self.assertEqual(generated_bytes, committed_bytes, name)
                self.assertIsInstance(json.loads(generated_bytes), dict)

    def test_b1_contracts_are_retained_and_rc1_is_backward_compatible(self) -> None:
        for kind in CONTRACT_KINDS:
            with self.subTest(kind=kind):
                self.assertTrue(contract_path(BASELINE_RELEASE, kind).is_file())
                self.assertTrue(contract_path(CURRENT_RELEASE, kind).is_file())

        b1_python = read_contract(BASELINE_RELEASE, "python-api")
        rc1_python = read_contract(CURRENT_RELEASE, "python-api")
        self.assertLessEqual(
            set(b1_python["public_api"]["symbols"]),
            set(rc1_python["public_api"]["symbols"]),
        )

        exact_contracts = ("openapi", "data-formats", "sqlite-schema")
        for kind in exact_contracts:
            with self.subTest(kind=kind):
                b1 = read_contract(BASELINE_RELEASE, kind)
                rc1 = read_contract(CURRENT_RELEASE, kind)
                b1["snapshot_release"] = CURRENT_RELEASE
                if kind == "openapi":
                    b1["openapi"]["info"]["version"] = CURRENT_RELEASE
                elif kind == "data-formats":
                    b1["compatibility_catalog"]["package_version"] = CURRENT_RELEASE
                self.assertEqual(rc1, b1)

    def test_check_mode_reports_a_readable_diff_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            generated = Path(temporary_directory) / "contracts"
            create = subprocess.run(
                [sys.executable, str(SCRIPT), "--output-dir", str(generated)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(create.returncode, 0, create.stderr)
            public_api = generated / f"v{CURRENT_RELEASE}-python-api.json"
            original = public_api.read_text(encoding="utf-8")
            stale_document = json.loads(original)
            stale_document["public_api"]["symbol_count"] = 0
            stale_text = json.dumps(
                stale_document,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n"
            public_api.write_text(
                stale_text,
                encoding="utf-8",
            )

            check = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--check",
                    "--output-dir",
                    str(generated),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(check.returncode, 1)
            self.assertIn(f"v{CURRENT_RELEASE}-python-api.json", check.stderr)
            self.assertIn("contract snapshot is stale", check.stderr)
            self.assertIn("freeze_contracts.py", check.stderr)
            self.assertEqual(public_api.read_text(encoding="utf-8"), stale_text)


if __name__ == "__main__":
    unittest.main()
