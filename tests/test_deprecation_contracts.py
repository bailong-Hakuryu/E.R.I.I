"""Executable warning contracts for interfaces scheduled to leave in v0.5."""

import tempfile
import unittest

from erii import ERIIEngine


class BetaDeprecationContractTests(unittest.TestCase):
    def test_remember_warns_and_names_the_canonical_turn_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_dir=root_dir) as engine:
                with self.assertWarnsRegex(
                    DeprecationWarning,
                    r"record a canonical Turn and call archive_turn\(\)",
                ):
                    engine.remember("agent_lumi", "user_chen")

    def test_transient_adjudication_warns_and_names_persisted_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_dir=root_dir) as engine:
                engine.initialize_relationship(
                    "agent_lumi",
                    "user_chen",
                    "Lumi values truthful shared experience.",
                )
                source_turn = {
                    "turn_id": "legacy-transient-turn",
                    "revision": "1",
                    "extractor_version": "deprecation-test-v1",
                    "messages": [
                        {
                            "source_id": "legacy-transient-user",
                            "revision": "1",
                            "role": "user",
                            "content": "We watched the first snow together.",
                            "occurred_at": "2026-08-02T00:00:00+00:00",
                        }
                    ],
                }
                candidate = {
                    "candidate_key": "first-snow",
                    "event_type": "shared_experience",
                    "summary": "We watched the first snow together.",
                    "signal": {
                        "signal_type": "shared_experience",
                        "strength": "moderate",
                        "extraction_confidence": 0.95,
                        "interpretation_confidence": 0.95,
                    },
                    "evidence": [
                        {
                            "source_id": "legacy-transient-user",
                            "quote": "We watched the first snow together.",
                        }
                    ],
                }

                with self.assertWarnsRegex(
                    DeprecationWarning,
                    r"adjudicate_turn_candidates\(\).*process_relationship_turn\(\)",
                ):
                    result = engine.adjudicate_relationship_candidates(
                        "agent_lumi",
                        "user_chen",
                        source_turn,
                        [candidate],
                    )

                self.assertEqual(len(result.records), 1)


if __name__ == "__main__":
    unittest.main()
