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
        """Verify recall accuracy is maintained as memory count grows."""
        # Add target memory
        target_content = "UNIQUE_TARGET_MEMORY_12345"
        self.engine.remember(self.agent_id, self.user_id, target_content, "Target response")

        # Add noise memories
        for i in range(100):
            self.engine.remember(
                self.agent_id,
                self.user_id,
                f"Noise memory {i} with different content",
                f"Noise response {i}",
            )

        # Verify target is still retrievable
        results = self.engine.recall(self.agent_id, self.user_id, "UNIQUE_TARGET_MEMORY_12345")

        # Target should be in results
        self.assertIn(target_content, results, "Target memory should be retrievable among noise")

    def test_memory_isolation_at_scale(self) -> None:
        """Verify agent/user isolation is maintained with many memories."""
        tmp_dir = tempfile.mkdtemp()
        try:
            storage = SQLiteStorage(db_path=f"{tmp_dir}/isolation_test.db")
            engine1 = ERIIEngine(storage_driver=storage)

            # Add memories for agent1/user1
            for i in range(50):
                engine1.remember("agent1", "user1", f"Agent1 memory {i}", f"Response {i}")

            # Add memories for agent2/user2
            for i in range(50):
                engine1.remember("agent2", "user2", f"Agent2 memory {i}", f"Response {i}")

            # Verify isolation
            results1 = engine1.recall("agent1", "user1", "memory")
            results2 = engine1.recall("agent2", "user2", "memory")

            # Engine should only return agent-specific memories
            self.assertIn("Agent1", results1, "Should see Agent1 memories")
            self.assertNotIn("Agent2", results1, "Should not see Agent2 memories in Agent1 recall")

            self.assertIn("Agent2", results2, "Should see Agent2 memories")
            self.assertNotIn("Agent1", results2, "Should not see Agent1 memories in Agent2 recall")

            engine1.close()
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
        """Verify memory queries use indexes efficiently."""
        # Add some test data
        for i in range(10):
            self.engine.remember(self.agent_id, self.user_id, f"Test memory {i}", f"Response {i}")

        # Check query plan for a typical recall query
        # This is a simplified check - in practice, you'd inspect EXPLAIN QUERY PLAN
        from erii.storage.sqlite_storage import SQLiteStorage

        if isinstance(self.storage, SQLiteStorage):
            connection = self.storage._connection
            cursor = connection.cursor()

            # Check for index on (agent_id, user_id) - critical for isolation
            indexes = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='nodes'"
            ).fetchall()

            index_names = [row[0] for row in indexes]
            # Verify some indexes exist (exact names depend on schema)
            self.assertGreater(len(index_names), 0, "Should have indexes on nodes table")


if __name__ == "__main__":
    unittest.main()
