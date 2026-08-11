"""Persistence-aware performance regression tests for E.R.I.I.

These tests deliberately archive completed Source Turns synchronously before
measuring reads.  They assert the resulting MemoryNode collection directly so a
Core Memory or a queued-but-unprocessed ``remember()`` call cannot create a false
positive.
"""

from contextlib import closing
import os
import sqlite3
import tempfile
import unittest

from erii import (
    ArchivalArtifactsDecision,
    ArchivalStatus,
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    MemoryCandidate,
    MemoryType,
    SQLiteStorage,
)
from erii.performance import PerformanceMonitor


def _delivery_exception() -> dict[str, object]:
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.performance-host",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-01T00:00:00+00:00",
        "reply_attempt_number": None,
    }


class _EchoMemoryExtractor:
    """Emit exactly one evidence-backed memory for every completed turn."""

    descriptor = ExtractorDescriptor(
        extractor_id="tests.performance-echo",
        extractor_version="1",
        extraction_schema_version="2",
    )

    def extract(self, request):
        message = request.transcript.user_message
        evidence = (
            {
                "citation_version": "archival-evidence-citation/v1",
                "kind": "message_span",
                "source_id": message.message_id,
                "source_revision": request.source_revision,
                "quote": message.content,
                "start": 0,
                "end": len(message.content),
            },
        )
        return ArchivalArtifactsDecision(
            memories=(
                MemoryCandidate(
                    node_type=MemoryType.EVENT,
                    content=message.content,
                    tags=("performance-fixture",),
                    evidence=evidence,
                ),
            )
        )


class _SynchronousArchiveCase(unittest.TestCase):
    """Shared real-SQLite fixture with explicit synchronous archival."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "performance.db")
        self.storage = SQLiteStorage(self.db_path)
        self.engine = ERIIEngine(
            storage_driver=self.storage,
            memory_extractor=_EchoMemoryExtractor(),
            config=ERIIConfig(async_archival=False),
        )

    def tearDown(self) -> None:
        self.engine.close()
        self.temp_dir.cleanup()

    def initialize(self, agent_id: str, user_id: str) -> str:
        profile = self.engine.initialize_relationship(
            agent_id,
            user_id,
            "A continuity test persona.",
        )
        return profile.relationship_id

    def archive_range(
        self,
        agent_id: str,
        user_id: str,
        prefix: str,
        count: int,
    ) -> set[str]:
        turn_ids = {f"{prefix}-{index}" for index in range(count)}
        for index in range(count):
            turn_id = f"{prefix}-{index}"
            self.engine.record_turn(
                agent_id,
                user_id,
                f"{prefix} durable memory {index}",
                f"Acknowledged {index}",
                turn_id=turn_id,
                delivery_exception=_delivery_exception(),
            )
            receipt = self.engine.archive_turn(
                agent_id,
                user_id,
                turn_id,
                idempotency_key=f"archive-{turn_id}",
            )
            self.assertEqual(receipt.status, ArchivalStatus.COMPLETED)
            self.assertEqual(receipt.memory_node_count, 1)
        return turn_ids


class TestPersistedRecallPerformance(_SynchronousArchiveCase):
    """Exercise recall only after exact persisted fixtures are verified."""

    def test_recall_reads_a_verified_persisted_collection(self) -> None:
        agent_id = "perf-agent"
        user_id = "perf-user"
        relationship_id = self.initialize(agent_id, user_id)
        expected_turn_ids = self.archive_range(agent_id, user_id, "perf", 30)

        nodes = self.storage.load_nodes(agent_id, user_id)
        self.assertEqual(len(nodes), 30)
        self.assertEqual({node.source_turn_id for node in nodes}, expected_turn_ids)
        self.assertEqual(len({node.node_id for node in nodes}), 30)
        self.assertEqual(len({node.source_archival_id for node in nodes}), 30)
        self.assertEqual({node.relationship_id for node in nodes}, {relationship_id})

        monitor = PerformanceMonitor()
        with monitor.measure("recall"):
            context = self.engine.recall(
                agent_id,
                user_id,
                "perf durable memory 17",
                top_k=10,
            )

        self.assertIn("perf durable memory 17", context)
        measurement = monitor.stats()["recall"]
        self.assertEqual(measurement["count"], 1)
        self.assertGreaterEqual(measurement["mean"], 0.0)

    def test_archival_measurement_corresponds_to_exact_node_count(self) -> None:
        agent_id = "batch-agent"
        user_id = "batch-user"
        self.initialize(agent_id, user_id)
        monitor = PerformanceMonitor()

        with monitor.measure("archive-20-turns"):
            expected_turn_ids = self.archive_range(agent_id, user_id, "batch", 20)

        nodes = self.storage.load_nodes(agent_id, user_id)
        self.assertEqual(len(nodes), 20)
        self.assertEqual({node.source_turn_id for node in nodes}, expected_turn_ids)
        self.assertEqual(len({node.node_id for node in nodes}), 20)
        self.assertEqual(len({node.source_archival_id for node in nodes}), 20)
        self.assertEqual(monitor.stats()["archive-20-turns"]["count"], 1)

    def test_persisted_collection_survives_engine_restart(self) -> None:
        agent_id = "restart-agent"
        user_id = "restart-user"
        relationship_id = self.initialize(agent_id, user_id)
        expected_turn_ids = self.archive_range(agent_id, user_id, "restart", 12)
        self.engine.close()

        reopened_storage = SQLiteStorage(self.db_path)
        self.engine = ERIIEngine(
            storage_driver=reopened_storage,
            memory_extractor=_EchoMemoryExtractor(),
            config=ERIIConfig(async_archival=False),
        )
        nodes = reopened_storage.load_nodes(agent_id, user_id)

        self.assertEqual(len(nodes), 12)
        self.assertEqual({node.source_turn_id for node in nodes}, expected_turn_ids)
        self.assertEqual(len({node.node_id for node in nodes}), 12)
        self.assertEqual(len({node.source_archival_id for node in nodes}), 12)
        self.assertEqual({node.relationship_id for node in nodes}, {relationship_id})


class TestRelationshipIsolationAtScale(_SynchronousArchiveCase):
    """Verify stored node identity and content for two independent relations."""

    def test_relationship_collections_remain_exact_and_disjoint(self) -> None:
        agent_id = "shared-agent"
        first_user = "first-user"
        second_user = "second-user"
        first_relationship = self.initialize(agent_id, first_user)
        second_relationship = self.initialize(agent_id, second_user)

        first_turns = self.archive_range(agent_id, first_user, "first-scope", 20)
        second_turns = self.archive_range(agent_id, second_user, "second-scope", 20)
        first_nodes = self.storage.load_nodes(agent_id, first_user)
        second_nodes = self.storage.load_nodes(agent_id, second_user)

        self.assertEqual(len(first_nodes), 20)
        self.assertEqual(len(second_nodes), 20)
        self.assertEqual({node.source_turn_id for node in first_nodes}, first_turns)
        self.assertEqual({node.source_turn_id for node in second_nodes}, second_turns)
        self.assertEqual(
            {node.relationship_id for node in first_nodes},
            {first_relationship},
        )
        self.assertEqual(
            {node.relationship_id for node in second_nodes},
            {second_relationship},
        )
        self.assertTrue(
            {node.node_id for node in first_nodes}.isdisjoint(
                {node.node_id for node in second_nodes}
            )
        )
        self.assertTrue(all("second-scope" not in node.content for node in first_nodes))
        self.assertTrue(all("first-scope" not in node.content for node in second_nodes))


class TestQueryPlanVerification(_SynchronousArchiveCase):
    """Inspect the exact SQLite plan used by scoped MemoryNode loads."""

    def test_memory_load_plan_uses_agent_user_index(self) -> None:
        self.initialize("query-agent", "query-user")
        self.archive_range("query-agent", "query-user", "query", 5)

        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT node_id, agent_id, user_id, data
                FROM memory_nodes
                WHERE agent_id = ? AND user_id = ?
                """,
                ("query-agent", "query-user"),
            ).fetchall()

        plan = "\n".join(str(row[3]) for row in rows)
        self.assertIn("USING INDEX idx_agent_user", plan)


if __name__ == "__main__":
    unittest.main()
