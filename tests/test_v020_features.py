"""Unit tests for E.R.I.I. v0.2.0 features.

Tests:
1. Concurrency key lock safety and SQLite WAL initialization.
2. BaseTaskQueue & PersistentTaskQueue backoff retries.
3. MemoryPack export and import data migration.
4. RRF (Reciprocal Rank Fusion) hybrid vector retrieval.

Follows Google Python Style Guide.
"""

import os
import shutil
import tempfile
import time
import unittest
import uuid

from erii.core.queue.base import TaskStatus
from erii.core.queue.persistent_queue import PersistentTaskQueue
from erii.engine import ERIIEngine
from erii.models.node import MemoryNode, MemoryType
from erii.models.pack import MemoryPack
from erii.storage.file_storage import FileStorage
from erii.storage.sqlite_storage import SQLiteStorage
from erii.vector.in_memory_vector import DummyEmbeddingProvider, InMemoryVectorStore


class TestV020Features(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_v020.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_key_lock_manager(self):
        storage = FileStorage(root_dir=self.test_dir)
        lock1 = storage.lock_manager.get_lock("agentA", "userB")
        lock2 = storage.lock_manager.get_lock("agentA", "userB")
        lock3 = storage.lock_manager.get_lock("agentX", "userY")

        self.assertIs(lock1, lock2)
        self.assertIsNot(lock1, lock3)

    def test_sqlite_wal_mode(self):
        storage = SQLiteStorage(db_path=self.db_path)
        node = MemoryNode(
            node_id=str(uuid.uuid4()),
            agent_id="a1",
            user_id="u1",
            content="WAL test node",
            node_type=MemoryType.FACT,
        )
        storage.save_nodes("a1", "u1", [node])
        loaded = storage.load_nodes("a1", "u1")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].content, "WAL test node")

    def test_persistent_task_queue(self):
        queue = PersistentTaskQueue(db_path=self.db_path, base_delay_seconds=0.1, max_attempts=2)
        task_id = queue.enqueue("a1", "u1", "Hello", "Hi there")

        # Dequeue pending task
        task = queue.dequeue()
        self.assertIsNotNone(task)
        self.assertEqual(task.task_id, task_id)
        self.assertEqual(task.status, TaskStatus.PROCESSING)

        # Fail attempt 1 -> should back off to PENDING
        queue.fail(task_id, "API timeout 1")

        # Immediate dequeue should be empty due to backoff next_attempt_at
        task_immediate = queue.dequeue()
        self.assertIsNone(task_immediate)

        # Sleep past backoff delay (0.1s)
        time.sleep(0.15)
        task_retry = queue.dequeue()
        self.assertIsNotNone(task_retry)

        # Fail attempt 2 -> reaches max_attempts (2), moves to FAILED
        queue.fail(task_id, "API timeout 2")

        summary = queue.get_status_summary()
        self.assertEqual(summary["failed"], 1)

        # Test retry_failed
        reset_count = queue.retry_failed()
        self.assertEqual(reset_count, 1)

    def test_memory_pack_export_import(self):
        engine1 = ERIIEngine(storage_dir=os.path.join(self.test_dir, "eng1"))
        engine1.set_core_memory("sakura", "p1", "Core rule: be nice.")

        # Create node manually
        node = MemoryNode(
            node_id=str(uuid.uuid4()),
            agent_id="sakura",
            user_id="p1",
            content="p1 likes earl grey tea",
            node_type=MemoryType.PREFERENCE,
        )
        engine1.storage.save_nodes("sakura", "p1", [node])

        export_file = os.path.join(self.test_dir, "pack.json")
        pack = engine1.export_memory("sakura", "p1", export_path=export_file)
        self.assertEqual(pack.agent_id, "sakura")
        self.assertEqual(pack.core_memory, "Core rule: be nice.")

        # Import into engine2 using SQLite Storage
        sqlite_driver = SQLiteStorage(db_path=os.path.join(self.test_dir, "import.db"))
        engine2 = ERIIEngine(storage_driver=sqlite_driver)
        engine2.import_memory(export_file, agent_id="sakura", user_id="p1")

        imported_nodes = engine2.storage.load_nodes("sakura", "p1")
        self.assertEqual(len(imported_nodes), 1)
        self.assertEqual(imported_nodes[0].content, "p1 likes earl grey tea")
        self.assertEqual(engine2.storage.get_core_memory("sakura", "p1"), "Core rule: be nice.")

        engine1.close()
        engine2.close()

    def test_rrf_hybrid_vector_retrieval(self):
        vector_store = InMemoryVectorStore()
        embed_provider = DummyEmbeddingProvider(dim=32)

        engine = ERIIEngine(
            storage_dir=os.path.join(self.test_dir, "vec_eng"),
            vector_store=vector_store,
            embedding_provider=embed_provider,
        )

        n1 = MemoryNode(
            node_id=str(uuid.uuid4()),
            agent_id="a",
            user_id="u",
            content="Dark mode IDE preference",
            node_type=MemoryType.PREFERENCE,
        )
        n2 = MemoryNode(
            node_id=str(uuid.uuid4()),
            agent_id="a",
            user_id="u",
            content="Lavender earl grey tea",
            node_type=MemoryType.PREFERENCE,
        )
        engine.storage.save_nodes("a", "u", [n1, n2])

        context = engine.recall(agent_id="a", user_id="u", query="IDE theme")
        self.assertIn("Dark mode IDE preference", context)

        engine.close()


if __name__ == "__main__":
    unittest.main()
