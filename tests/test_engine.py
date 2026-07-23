"""Integration tests for ERIIEngine."""

import shutil
import tempfile
import unittest
from erii import ERIIEngine, SQLiteStorage


class TestERIIEngine(unittest.TestCase):

    def test_engine_file_storage_workflow(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            engine = ERIIEngine(storage_dir=tmp_dir)

            # Set Core Memory
            engine.set_core_memory("agent1", "user1", "Core rule: be polite.")
            self.assertIn("be polite", engine.get_core_memory("agent1", "user1"))

            # Remember turn
            engine.remember("agent1", "user1", "I prefer green tea", "Noted green tea.")

            # Recall context
            context = engine.recall("agent1", "user1", "tea preference")
            self.assertIn("Core Persona Memory", context)
            self.assertIn("be polite", context)

            engine.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_engine_sqlite_storage_workflow(self):
        tmp_dir = tempfile.mkdtemp()
        db_path = f"{tmp_dir}/test.db"
        try:
            driver = SQLiteStorage(db_path=db_path)
            engine = ERIIEngine(storage_driver=driver)

            engine.set_core_memory("agent_sq", "user_sq", "SQLite core memory")
            engine.remember(
                "agent_sq", "user_sq", "User likes pizza", "Pizza is delicious!"
            )

            context = engine.recall("agent_sq", "user_sq", "pizza")
            self.assertIn("SQLite core memory", context)

            engine.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
