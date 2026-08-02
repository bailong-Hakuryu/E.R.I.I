"""Release-contract snapshot tests for v0.4.0b1."""

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

            expected_names = sorted(path.name for path in COMMITTED.glob("*.json"))
            actual_names = sorted(path.name for path in generated.glob("*.json"))
            self.assertEqual(actual_names, expected_names)
            self.assertGreaterEqual(len(actual_names), 4)
            for name in actual_names:
                committed_bytes = (COMMITTED / name).read_bytes()
                generated_bytes = (generated / name).read_bytes()
                self.assertEqual(generated_bytes, committed_bytes, name)
                self.assertIsInstance(json.loads(generated_bytes), dict)

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
            public_api = generated / "v0.4.0b1-python-api.json"
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
            self.assertIn("v0.4.0b1-python-api.json", check.stderr)
            self.assertIn("contract snapshot is stale", check.stderr)
            self.assertIn("freeze_contracts.py", check.stderr)
            self.assertEqual(public_api.read_text(encoding="utf-8"), stale_text)


if __name__ == "__main__":
    unittest.main()
