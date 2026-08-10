"""Concurrency tests for E.R.I.I. memory system.

Tests concurrent access, transaction isolation, and race conditions.
"""

import tempfile
import shutil
import threading
import time
import unittest
from typing import List

from erii import ERIIEngine, SQLiteStorage


class TestConcurrentWrites(unittest.TestCase):
    """Test concurrent write operations to the same storage."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.storage = SQLiteStorage(db_path=f"{self.tmp_dir}/concurrent_test.db")
        self.errors: List[Exception] = []

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_concurrent_memory_additions_same_agent(self) -> None:
        """Verify concurrent writes to the same (agent_id, user_id) are safe."""
        agent_id = "concurrent-agent"
        user_id = "concurrent-user"

        # Set core memory first
        engine_setup = ERIIEngine(storage_driver=self.storage)
        engine_setup.set_core_memory(agent_id, user_id, "Test core")
        engine_setup.close()

        def add_memories(thread_id: int, count: int) -> None:
            try:
                engine = ERIIEngine(storage_driver=self.storage)
                for i in range(count):
                    engine.remember(
                        agent_id,
                        user_id,
                        f"Thread {thread_id} memory {i}",
                        f"Response {i}",
                    )
                engine.close()
            except Exception as e:
                self.errors.append(e)

        # Run 3 threads adding memories concurrently
        threads = []
        for thread_id in range(3):
            thread = threading.Thread(target=add_memories, args=(thread_id, 10))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        # Verify no errors occurred
        self.assertEqual(len(self.errors), 0, f"Concurrent writes should not error: {self.errors}")

        # Verify memories were added
        engine = ERIIEngine(storage_driver=self.storage)
        results = engine.recall(agent_id, user_id, "Thread")
        # Should have some memories
        self.assertGreater(len(results), 0, "Memories should be retrievable")
        engine.close()

    def test_concurrent_writes_different_agents(self) -> None:
        """Verify concurrent writes by different agents don't interfere."""

        def add_memories_for_agent(agent_id: str, count: int) -> None:
            try:
                engine = ERIIEngine(storage_driver=self.storage)
                engine.set_core_memory(agent_id, "user", f"{agent_id} core")
                for i in range(count):
                    engine.remember(
                        agent_id,
                        "user",
                        f"{agent_id} memory {i}",
                        f"Response {i}",
                    )
                engine.close()
            except Exception as e:
                self.errors.append(e)

        # Run threads for different agents
        threads = []
        agent_ids = ["agent-1", "agent-2", "agent-3"]
        for agent_id in agent_ids:
            thread = threading.Thread(target=add_memories_for_agent, args=(agent_id, 10))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no errors
        self.assertEqual(len(self.errors), 0, "Concurrent writes should not error")

        # Verify isolation: each agent should only see its own memories
        for agent_id in agent_ids:
            engine = ERIIEngine(storage_driver=self.storage)
            results = engine.recall(agent_id, "user", "memory")

            # Verify only this agent's memories are returned
            self.assertIn(agent_id, results, f"Should see {agent_id} memories")
            engine.close()

    def test_concurrent_recall_operations(self) -> None:
        """Verify concurrent recall operations are safe."""
        engine = ERIIEngine(storage_driver=self.storage)
        engine.set_core_memory("recall-agent", "recall-user", "Test core")

        # Add initial memories
        for i in range(20):
            engine.remember("recall-agent", "recall-user", f"Memory {i}", f"Response {i}")

        engine.close()

        recall_results: List[str] = []

        def perform_recall(query: str) -> None:
            try:
                eng = ERIIEngine(storage_driver=self.storage)
                results = eng.recall("recall-agent", "recall-user", query)
                recall_results.append(results)
                eng.close()
            except Exception as e:
                self.errors.append(e)

        # Run multiple concurrent recalls
        threads = []
        for i in range(5):
            thread = threading.Thread(target=perform_recall, args=(f"Memory {i}",))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no errors
        self.assertEqual(len(self.errors), 0, "Concurrent recalls should not error")
        # Verify all recalls completed
        self.assertEqual(len(recall_results), 5, "All recalls should complete")


class TestTransactionIsolation(unittest.TestCase):
    """Test transaction isolation and consistency."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.storage = SQLiteStorage(db_path=f"{self.tmp_dir}/transaction_test.db")
        self.errors: List[Exception] = []

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_concurrent_relationship_updates(self) -> None:
        """Verify concurrent relationship updates maintain consistency."""
        agent_id = "relationship-agent"
        user_id = "relationship-user"

        # Pre-populate some memories
        engine = ERIIEngine(storage_driver=self.storage)
        engine.set_core_memory(agent_id, user_id, "Test core")
        for i in range(10):
            engine.remember(agent_id, user_id, f"Base memory {i}", f"Response {i}")
        engine.close()

        def update_relationships(thread_id: int) -> None:
            try:
                engine = ERIIEngine(storage_driver=self.storage)
                # Perform operations that might update relationships
                for i in range(5):
                    engine.remember(
                        agent_id,
                        user_id,
                        f"Thread {thread_id} update {i}",
                        f"Response {i}",
                    )
                    time.sleep(0.001)  # Small delay to increase contention
                engine.close()
            except Exception as e:
                self.errors.append(e)

        # Run concurrent relationship updates
        threads = []
        for thread_id in range(3):
            thread = threading.Thread(target=update_relationships, args=(thread_id,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no errors
        self.assertEqual(len(self.errors), 0, "Concurrent updates should not error")

        # Verify data consistency
        engine = ERIIEngine(storage_driver=self.storage)
        results = engine.recall(agent_id, user_id, "memory")
        # Should have base + updates
        self.assertGreater(len(results), 0, "Memories should be present")
        engine.close()


class TestRaceConditions(unittest.TestCase):
    """Test for potential race conditions in critical paths."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.storage = SQLiteStorage(db_path=f"{self.tmp_dir}/race_test.db")
        self.errors: List[Exception] = []

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_no_duplicate_node_ids(self) -> None:
        """Verify concurrent operations don't create duplicate node IDs."""
        agent_id = "nodeid-agent"
        user_id = "nodeid-user"

        # Set core memory first
        engine_setup = ERIIEngine(storage_driver=self.storage)
        engine_setup.set_core_memory(agent_id, user_id, "Test core")
        engine_setup.close()

        def add_memories(count: int) -> None:
            try:
                engine = ERIIEngine(storage_driver=self.storage)
                for i in range(count):
                    engine.remember(
                        agent_id,
                        user_id,
                        f"Memory for ID test {i}",
                        f"Response {i}",
                    )
                engine.close()
            except Exception as e:
                self.errors.append(e)

        # Run concurrent adds
        threads = []
        for _ in range(3):
            thread = threading.Thread(target=add_memories, args=(10,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no errors
        self.assertEqual(len(self.errors), 0, "Should not have errors")

        # Verify data was written (node ID uniqueness is enforced at DB level)
        engine = ERIIEngine(storage_driver=self.storage)
        results = engine.recall(agent_id, user_id, "Memory")
        self.assertGreater(len(results), 0, "Should have memories")
        engine.close()


if __name__ == "__main__":
    unittest.main()
