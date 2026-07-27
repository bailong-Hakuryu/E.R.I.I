"""Tests for explicit REST server engine lifecycle management."""

import importlib
import os
import tempfile
import unittest

server_app = importlib.import_module("erii.server.app")


class TestServerLifecycle(unittest.TestCase):
    def setUp(self):
        server_app.close_engine()

    def tearDown(self):
        server_app.close_engine()

    def test_import_does_not_initialize_engine(self):
        self.assertIsNone(server_app._engine)

    def test_configure_engine_uses_selected_storage_directory(self):
        with tempfile.TemporaryDirectory() as storage_dir:
            engine = server_app.configure_engine(storage_dir=storage_dir)

            self.assertIs(engine, server_app._engine)
            self.assertEqual(engine.storage.root_dir, os.path.abspath(storage_dir))
            self.assertEqual(
                engine.archiver_worker.task_queue.db_path,
                os.path.join(os.path.abspath(storage_dir), "erii_tasks.db"),
            )
            self.assertTrue(engine.archiver_worker.running)
            server_app.close_engine()

        self.assertIsNone(server_app._engine)

    @unittest.skipIf(server_app.app is None, "FastAPI is not installed")
    def test_relationship_adjudication_reports_missing_relationship_as_404(self):
        with tempfile.TemporaryDirectory() as storage_dir:
            server_app.configure_engine(storage_dir=storage_dir)
            try:
                request = server_app.RelationshipAdjudicationBody(
                    user_id="missing-user",
                    source_turn={
                        "turn_id": "missing-relationship-turn",
                        "messages": [
                            {
                                "source_id": "message-1",
                                "role": "user",
                                "content": "Hello.",
                            }
                        ],
                    },
                    candidates=[
                        {
                            "candidate_key": "observation-1",
                            "event_type": "observation",
                            "summary": "The user said hello.",
                            "signal": {
                                "signal_type": "neutral",
                                "strength": "weak",
                                "extraction_confidence": 0.9,
                                "interpretation_confidence": 0.9,
                            },
                            "evidence": [
                                {
                                    "source_id": "message-1",
                                    "quote": "Hello.",
                                }
                            ],
                        }
                    ],
                )

                with self.assertRaises(server_app.HTTPException) as raised:
                    server_app.api_adjudicate_relationship(request)

                self.assertEqual(raised.exception.status_code, 404)
            finally:
                server_app.close_engine()


if __name__ == "__main__":
    unittest.main()
