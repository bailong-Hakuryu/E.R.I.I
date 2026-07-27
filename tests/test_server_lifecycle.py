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


if __name__ == "__main__":
    unittest.main()
