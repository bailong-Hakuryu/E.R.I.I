"""REST contracts for reliable Source Turn archival."""

import importlib
import tempfile
import unittest

from fastapi.testclient import TestClient

from erii import (
    ArchivalNoMemoryDecision,
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    FileStorage,
)

server_module = importlib.import_module("erii.server.app")
TEST_API_KEY = "test-archival-rest-key-1234567890"


def _visible_exchange_delivery_exception():
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.archival-fixture-host",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-01T00:00:00+00:00",
        "reply_attempt_number": None,
    }


class NoMemoryExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.no-memory",
        extractor_version="1.0",
        extraction_schema_version="1",
    )

    def extract(self, request):
        return ArchivalNoMemoryDecision(reason_code="nothing_durable")


class ArchivalRestPublicTests(unittest.TestCase):
    def setUp(self):
        self.engine = ERIIEngine(
            storage_driver=FileStorage(tempfile.mkdtemp()),
            memory_extractor=NoMemoryExtractor(),
            config=ERIIConfig(async_archival=True),
        )
        self.engine.initialize_relationship(
            "agent_erii",
            "user_one",
            "A gentle character learning to live an ordinary life.",
        )
        self.engine.record_turn(
            "agent_erii",
            "user_one",
            "Thank you.",
            "You are welcome.",
            turn_id="turn-rest-archive",
            delivery_exception=_visible_exchange_delivery_exception(),
        )
        server_module._engine = self.engine
        server_module.configure_server_access(TEST_API_KEY)
        self.client = TestClient(
            server_module.app,
            headers={"X-API-Key": TEST_API_KEY},
        )

    def tearDown(self):
        self.client.close()
        server_module._engine = None
        server_module.configure_server_access(None)
        self.engine.close()

    def test_submit_query_and_process_archival_without_exposing_transcript(self):
        submitted = self.client.post(
            "/api/v1/archivals",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "source_turn_id": "turn-rest-archive",
                "idempotency_key": "archive-rest",
            },
        )

        self.assertEqual(submitted.status_code, 202)
        pending = submitted.json()["receipt"]
        self.assertEqual(pending["status"], "pending")
        self.assertNotIn("transcript", pending)

        self.assertEqual(self.engine.process_pending(max_tasks=1), 1)

        queried = self.client.get(
            f"/api/v1/archivals/{pending['archival_id']}",
            params={"agent_id": "agent_erii", "user_id": "user_one"},
        )
        self.assertEqual(queried.status_code, 200)
        self.assertEqual(queried.json()["receipt"]["status"], "completed")
        self.assertEqual(
            queried.json()["receipt"]["outcome_code"],
            "no_memory",
        )

    def test_rest_maps_invalid_source_missing_capability_and_scope(self):
        invalid = self.client.post(
            "/api/v1/archivals",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "source_turn_id": "missing-turn",
                "idempotency_key": "archive-missing",
            },
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["detail"]["code"], "invalid_source_turn")

        valid = self.client.post(
            "/api/v1/archivals",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "source_turn_id": "turn-rest-archive",
                "idempotency_key": "archive-scope",
            },
        )
        archival_id = valid.json()["receipt"]["archival_id"]
        wrong_scope = self.client.get(
            f"/api/v1/archivals/{archival_id}",
            params={"agent_id": "agent_erii", "user_id": "someone_else"},
        )
        self.assertEqual(wrong_scope.status_code, 404)

        server_module._engine = ERIIEngine(
            storage_driver=FileStorage(tempfile.mkdtemp()),
        )
        unavailable = self.client.post(
            "/api/v1/archivals",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "source_turn_id": "turn-rest-archive",
                "idempotency_key": "archive-unavailable",
            },
        )
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(
            unavailable.json()["detail"]["code"],
            "archival_capability_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
