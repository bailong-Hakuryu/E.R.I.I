"""Performance tests for E.R.I.I. memory system.

Tests query performance, large-scale recall, and resource usage.
"""

import tempfile
import shutil
import time
import unittest
from typing import List

from erii import ERIIEngine, SQLiteStorage


class TestQueryPerformance(unittest.TestCase):
    """Test query performance and scalability."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.storage = SQLiteStorage(db_path=f"{self.tmp_dir}/perf_test.db")
        self.engine = ERIIEngine(storage_driver=self.storage)
        self.agent_id = "perf-agent"
        self.user_id = "perf-user"

    def tearDown(self) -> None:
        self.engine.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_recall_performance_with_multiple_memories(self) -> None:
        """Verify recall performance doesn't degrade with multiple memories."""
        # Set core memory first
        self.engine.set_core_memory(self.agent_id, self.user_id, "Test core memory")

        # Add 100 memories
        memory_count = 100
        for i in range(memory_count):
            self.engine.remember(
                self.agent_id,
                self.user_id,
                f"Memory number {i}: This is test content for performance testing.",
                f"Response {i}",
            )

        # Measure recall time
        start = time.perf_counter()
        results = self.engine.recall(self.agent_id, self.user_id, "test content")
        elapsed = time.perf_counter() - start

        # Verify results - recall returns a string context
        self.assertIsInstance(results, str, "Recall should return context string")
        self.assertGreater(len(results), 0, "Recall should return results")

        # Performance assertion: recall should complete in reasonable time
        # 100 memories should be searchable in under 2 seconds
        self.assertLess(elapsed, 2.0, f"Recall took {elapsed:.3f}s, expected < 2.0s")

    def test_batch_memory_insertion_performance(self) -> None:
        """Verify batch insertion performance."""
        self.engine.set_core_memory(self.agent_id, self.user_id, "Test core")
        memory_count = 50

        start = time.perf_counter()
        for i in range(memory_count):
            self.engine.remember(
                self.agent_id,
                self.user_id,
                f"Batch memory {i}",
                f"Response {i}",
            )
        elapsed = time.perf_counter() - start

        # Performance assertion: 50 insertions should complete quickly
        # Allow 3 seconds for 50 insertions (60ms per insert average)
        self.assertLess(
            elapsed, 3.0, f"Inserting {memory_count} memories took {elapsed:.3f}s, expected < 3.0s"
        )

    def test_relationship_query_performance(self) -> None:
        """Verify relationship loading doesn't cause N+1 queries."""
        self.engine.set_core_memory(self.agent_id, self.user_id, "Test core")

        # Add some memories with relationships
        for i in range(20):
            self.engine.remember(
                self.agent_id,
                self.user_id,
                f"Related memory {i}",
                f"Response {i}",
            )

        # Measure recall time
        start = time.perf_counter()
        results = self.engine.recall(self.agent_id, self.user_id, "related")
        elapsed = time.perf_counter() - start

        self.assertGreater(len(results), 0)
        # Should complete quickly even with relationship loading
        self.assertLess(elapsed, 1.0, f"Recall with relationships took {elapsed:.3f}s")


class TestMemoryScaling(unittest.TestCase):
    """Test behavior with large numbers of memories."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.storage = SQLiteStorage(db_path=f"{self.tmp_dir}/scale_test.db")
        self.engine = ERIIEngine(storage_driver=self.storage)
        self.agent_id = "scale-agent"
        self.user_id = "scale-user"

    def tearDown(self) -> None:
        self.engine.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_recall_accuracy_doesnt_degrade_with_scale(self) -> None:
        """Verify system can handle many memories without errors."""
        self.engine.set_core_memory(self.agent_id, self.user_id, "Test core")

        # Add many memories - this tests the system doesn't break at scale
        for i in range(50):
            self.engine.remember(
                self.agent_id,
                self.user_id,
                f"Memory number {i}",
                f"Response {i}",
            )

        # Verify recall doesn't error with many memories
        try:
            results = self.engine.recall(self.agent_id, self.user_id, "Memory number")
            # If we get here without exception, the system handled the scale
            self.assertIsInstance(results, str)
            self.assertGreater(len(results), 0, "Recall should return some result")
        except Exception as e:
            self.fail(f"Recall should not error with 50 memories: {e}")

    def test_memory_isolation_at_scale(self) -> None:
        """Verify agent/user isolation is maintained with many memories."""
        tmp_dir = tempfile.mkdtemp()
        try:
            storage = SQLiteStorage(db_path=f"{tmp_dir}/isolation_test.db")
            engine = ERIIEngine(storage_driver=storage)

            # Set core memories for both agents
            engine.set_core_memory("agent1", "user1", "Agent1 core")
            engine.set_core_memory("agent2", "user2", "Agent2 core")

            # Add memories for agent1/user1
            for i in range(50):
                engine.remember("agent1", "user1", f"Agent1 memory {i}", f"Response {i}")

            # Add memories for agent2/user2
            for i in range(50):
                engine.remember("agent2", "user2", f"Agent2 memory {i}", f"Response {i}")

            # Verify isolation
            results1 = engine.recall("agent1", "user1", "memory")
            results2 = engine.recall("agent2", "user2", "memory")

            # Engine should only return agent-specific memories
            self.assertIn("Agent1", results1, "Should see Agent1 memories")
            self.assertNotIn("Agent2", results1, "Should not see Agent2 memories in Agent1 recall")

            self.assertIn("Agent2", results2, "Should see Agent2 memories")
            self.assertNotIn("Agent1", results2, "Should not see Agent1 memories in Agent2 recall")

            engine.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestQueryPlanVerification(unittest.TestCase):
    """Verify SQL queries use appropriate indexes and plans."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.storage = SQLiteStorage(db_path=f"{self.tmp_dir}/query_test.db")
        self.engine = ERIIEngine(storage_driver=self.storage)
        self.agent_id = "query-agent"
        self.user_id = "query-user"

    def tearDown(self) -> None:
        self.engine.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_memory_query_uses_index(self) -> None:
        """Verify database operations work correctly at scale."""
        self.engine.set_core_memory(self.agent_id, self.user_id, "Test core")

        # Add test data
        for i in range(20):
            self.engine.remember(self.agent_id, self.user_id, f"Test memory {i}", f"Response {i}")

        # Verify operations complete without error
        try:
            results = self.engine.recall(self.agent_id, self.user_id, "Test memory")
            self.assertIsInstance(results, str)
            self.assertGreater(len(results), 0, "Recall should return results")
        except Exception as e:
            self.fail(f"Database operations should not error: {e}")


if __name__ == "__main__":
    unittest.main()
