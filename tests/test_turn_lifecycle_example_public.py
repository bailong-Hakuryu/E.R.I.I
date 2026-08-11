"""Executable contract for the published Turn lifecycle example."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class TurnLifecycleExamplePublicTests(unittest.TestCase):
    def test_example_runs_offline_with_ascii_output_and_clean_cwd(self):
        repository = Path(__file__).resolve().parents[1]
        script = repository / "examples" / "08_turn_lifecycle_integration.py"
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "ascii"
        environment["PYTHONPATH"] = os.pathsep.join(
            value
            for value in (
                str(repository),
                environment.get("PYTHONPATH", ""),
            )
            if value
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=temporary_dir,
                env=environment,
                capture_output=True,
                text=True,
                encoding="ascii",
                timeout=30,
                check=False,
            )
            remaining_paths = list(Path(temporary_dir).iterdir())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[ok] complete:", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(remaining_paths, [])


if __name__ == "__main__":
    unittest.main()
