"""REST contracts for the canonical Turn Recording lifecycle."""

import importlib
import tempfile
import unittest

from fastapi.testclient import TestClient

from erii import ERIIEngine, FileStorage
server_module = importlib.import_module("erii.server.app")


class TurnRestPublicTests(unittest.TestCase):
    def setUp(self):
        self.engine = ERIIEngine(
            storage_driver=FileStorage(tempfile.mkdtemp()),
        )
        self.engine.initialize_relationship(
            "agent_erii",
            "user_one",
            "A quiet character who values honest companionship.",
        )
        server_module._engine = self.engine
        self.client = TestClient(server_module.app)

    def tearDown(self):
        self.client.close()
        server_module._engine = None
        self.engine.close()

    def test_open_complete_and_read_turn_without_echoing_transcript_in_receipt(self):
        opened = self.client.post(
            "/api/v1/turns/open",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "turn_id": "turn-rest",
                "user_message": "Can we keep this memory?",
            },
        )

        self.assertEqual(opened.status_code, 201)
        self.assertEqual(opened.json()["turn"]["status"], "open")

        attempt = self.client.post(
            "/api/v1/turns/turn-rest/reply-attempts",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "attempt_number": 1,
                "stage": "generation",
                "capability_descriptor": "test-provider/model-v1",
                "failure_classification": "temporary_provider_error",
            },
        )
        self.assertEqual(attempt.status_code, 201)
        self.assertNotIn("draft", attempt.json()["attempt"])

        completed = self.client.post(
            "/api/v1/turns/turn-rest/complete",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "agent_message": "Yes. We will keep the whole moment.",
                "processing_channels": [],
                "continuity_assessment": {
                    "status": "completed",
                    "evaluator_version": "test-continuity-v1",
                    "verdict": "aligned",
                },
            },
        )

        self.assertEqual(completed.status_code, 200)
        receipt = completed.json()["receipt"]
        self.assertEqual(receipt["source_turn_id"], "turn-rest")
        self.assertNotIn("transcript", receipt)

        restored = self.client.get(
            "/api/v1/turns/turn-rest",
            params={"agent_id": "agent_erii", "user_id": "user_one"},
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(
            restored.json()["turn"]["transcript"]["agent_message"]["content"],
            "Yes. We will keep the whole moment.",
        )
        self.assertEqual(
            restored.json()["turn"]["continuity_assessment"]["status"],
            "completed",
        )
        listed = self.client.get(
            "/api/v1/turns",
            params={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "status": "completed",
            },
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [turn["turn_id"] for turn in listed.json()["turns"]],
            ["turn-rest"],
        )


if __name__ == "__main__":
    unittest.main()
