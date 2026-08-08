"""Regression test for the runnable relationship consequence example."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES_DIR))

from consequence_example import _AlignedEvaluator, main  # noqa: E402


class ConsequenceExampleTests(unittest.TestCase):
    def test_local_evaluator_has_an_auditable_identity(self):
        evaluator = _AlignedEvaluator()

        self.assertEqual(
            evaluator.descriptor.evaluator_id,
            "example.consequence-continuity",
        )

    def test_example_runs_the_complete_consequence_flow(self):
        output = StringIO()

        with redirect_stdout(output):
            main()

        rendered = output.getvalue()
        self.assertIn("Total consequences: 1", rendered)
        self.assertIn("Narrative tensions: 1", rendered)
        self.assertIn("Public narrative tensions: 0", rendered)
        self.assertIn("=== Example Complete ===", rendered)


if __name__ == "__main__":
    unittest.main()
