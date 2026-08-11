"""Concurrency tests over completed, synchronously archived Source Turns."""

from concurrent.futures import ThreadPoolExecutor
import os
import re
import tempfile
import threading
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


def _delivery_exception() -> dict[str, object]:
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.concurrency-host",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-01T00:00:00+00:00",
        "reply_attempt_number": None,
    }


class _EchoMemoryExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.concurrency-echo",
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
                    tags=("concurrency-fixture",),
                    evidence=evidence,
                ),
            )
        )


class ConcurrentArchivalTests(unittest.TestCase):
    """Use independent engine/storage objects against one real SQLite file."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "concurrency.db")
        self.storage = SQLiteStorage(self.db_path)
        self.setup_engine = self._new_engine()

    def tearDown(self) -> None:
        self.setup_engine.close()
        self.temp_dir.cleanup()

    def _new_engine(self) -> ERIIEngine:
        return ERIIEngine(
            storage_driver=SQLiteStorage(self.db_path),
            memory_extractor=_EchoMemoryExtractor(),
            config=ERIIConfig(async_archival=False),
        )

    def _initialize(self, agent_id: str, user_id: str) -> str:
        return self.setup_engine.initialize_relationship(
            agent_id,
            user_id,
            "A continuity concurrency fixture.",
        ).relationship_id

    @staticmethod
    def _record_one(
        engine: ERIIEngine,
        agent_id: str,
        user_id: str,
        turn_id: str,
        content: str,
    ) -> None:
        engine.record_turn(
            agent_id,
            user_id,
            content,
            f"Reply for {turn_id}",
            turn_id=turn_id,
            delivery_exception=_delivery_exception(),
        )

    @staticmethod
    def _archive_recorded(
        engine: ERIIEngine,
        agent_id: str,
        user_id: str,
        turn_id: str,
    ) -> str:
        receipt = engine.archive_turn(
            agent_id,
            user_id,
            turn_id,
            idempotency_key=f"archive-{turn_id}",
        )
        if receipt.status != ArchivalStatus.COMPLETED:
            raise AssertionError(f"archival did not complete: {receipt.status}")
        if receipt.memory_node_count != 1:
            raise AssertionError(
                f"expected one MemoryNode, got {receipt.memory_node_count}"
            )
        return receipt.archival_id

    @classmethod
    def _archive_one(
        cls,
        engine: ERIIEngine,
        agent_id: str,
        user_id: str,
        turn_id: str,
        content: str,
    ) -> str:
        cls._record_one(engine, agent_id, user_id, turn_id, content)
        return cls._archive_recorded(engine, agent_id, user_id, turn_id)

    def test_concurrent_archives_same_relationship_are_all_persisted(self) -> None:
        agent_id = "concurrent-agent"
        user_id = "concurrent-user"
        relationship_id = self._initialize(agent_id, user_id)
        worker_count = 3
        turns_per_worker = 6
        start = threading.Barrier(worker_count)

        def record_worker(worker_id: int) -> None:
            engine = self._new_engine()
            try:
                start.wait()
                for index in range(turns_per_worker):
                    self._record_one(
                        engine,
                        agent_id,
                        user_id,
                        f"same-{worker_id}-{index}",
                        f"same relationship worker {worker_id} memory {index}",
                    )
            finally:
                engine.close()

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            list(pool.map(record_worker, range(worker_count)))

        # The kernel does not promise multiple independent inline archival
        # consumers.  The host explicitly drains the completed concurrent turns
        # through one synchronous coordinator and then verifies the durable set.
        archival_ids = [
            self._archive_recorded(self.setup_engine, agent_id, user_id, turn_id)
            for turn_id in sorted(
                f"same-{worker_id}-{index}"
                for worker_id in range(worker_count)
                for index in range(turns_per_worker)
            )
        ]

        nodes = self.storage.load_nodes(agent_id, user_id)
        expected_turn_ids = {
            f"same-{worker_id}-{index}"
            for worker_id in range(worker_count)
            for index in range(turns_per_worker)
        }
        turns = self.setup_engine.list_turns(agent_id, user_id)

        self.assertEqual(len(nodes), worker_count * turns_per_worker)
        self.assertEqual({node.source_turn_id for node in nodes}, expected_turn_ids)
        self.assertEqual(len({node.node_id for node in nodes}), len(nodes))
        self.assertEqual({node.relationship_id for node in nodes}, {relationship_id})
        self.assertEqual(len(set(archival_ids)), len(archival_ids))
        self.assertEqual(
            {node.source_archival_id for node in nodes},
            set(archival_ids),
        )
        self.assertEqual({turn.turn_id for turn in turns}, expected_turn_ids)
        self.assertEqual({turn.status.value for turn in turns}, {"completed"})

    def test_concurrent_archives_keep_relationships_disjoint(self) -> None:
        scopes = [
            ("shared-agent", f"user-{index}", f"scope-{index}")
            for index in range(3)
        ]
        relationships = {
            (agent_id, user_id): self._initialize(agent_id, user_id)
            for agent_id, user_id, _prefix in scopes
        }
        start = threading.Barrier(len(scopes))

        def record_scope(scope: tuple[str, str, str]) -> None:
            agent_id, user_id, prefix = scope
            engine = self._new_engine()
            try:
                start.wait()
                for index in range(7):
                    self._record_one(
                        engine,
                        agent_id,
                        user_id,
                        f"{prefix}-{index}",
                        f"{prefix} private memory {index}",
                    )
            finally:
                engine.close()

        with ThreadPoolExecutor(max_workers=len(scopes)) as pool:
            list(pool.map(record_scope, scopes))

        for agent_id, user_id, prefix in scopes:
            for index in range(7):
                self._archive_recorded(
                    self.setup_engine,
                    agent_id,
                    user_id,
                    f"{prefix}-{index}",
                )

        node_id_sets: list[set[str]] = []
        for agent_id, user_id, prefix in scopes:
            nodes = self.storage.load_nodes(agent_id, user_id)
            self.assertEqual(len(nodes), 7)
            self.assertEqual(
                {node.source_turn_id for node in nodes},
                {f"{prefix}-{index}" for index in range(7)},
            )
            self.assertEqual(
                {node.relationship_id for node in nodes},
                {relationships[(agent_id, user_id)]},
            )
            self.assertTrue(all(node.content.startswith(prefix) for node in nodes))
            node_id_sets.append({node.node_id for node in nodes})

        for index, node_ids in enumerate(node_id_sets):
            for other_ids in node_id_sets[index + 1 :]:
                self.assertTrue(node_ids.isdisjoint(other_ids))

    def test_concurrent_recall_reads_only_prearchived_nodes(self) -> None:
        agent_id = "recall-agent"
        user_id = "recall-user"
        self._initialize(agent_id, user_id)
        expected_turn_ids = set()
        for index in range(10):
            turn_id = f"recall-seed-{index}"
            expected_turn_ids.add(turn_id)
            self._archive_one(
                self.setup_engine,
                agent_id,
                user_id,
                turn_id,
                f"unique recall marker {index}",
            )

        start = threading.Barrier(5)

        def recall(index: int) -> str:
            engine = self._new_engine()
            try:
                start.wait()
                return engine.recall(
                    agent_id,
                    user_id,
                    f"unique recall marker {index}",
                    top_k=10,
                )
            finally:
                engine.close()

        with ThreadPoolExecutor(max_workers=5) as pool:
            contexts = list(pool.map(recall, range(5)))

        expected_contents = {f"unique recall marker {index}" for index in range(10)}
        for context in contexts:
            recalled = set(re.findall(r"unique recall marker \d+", context))
            self.assertTrue(recalled)
            self.assertTrue(recalled.issubset(expected_contents))
        nodes = self.storage.load_nodes(agent_id, user_id)
        self.assertEqual(len(nodes), 10)
        self.assertEqual({node.source_turn_id for node in nodes}, expected_turn_ids)
        self.assertEqual(len({node.node_id for node in nodes}), 10)

    def test_duplicate_turn_identity_converges_before_one_explicit_archive(self) -> None:
        agent_id = "idempotent-agent"
        user_id = "idempotent-user"
        relationship_id = self._initialize(agent_id, user_id)
        turn_id = "idempotent-turn"
        worker_count = 4
        start = threading.Barrier(worker_count)

        def record_same_turn(_worker_id: int):
            start.wait()
            return self.setup_engine.record_turn(
                agent_id,
                user_id,
                "one durable idempotent memory",
                "Acknowledged",
                turn_id=turn_id,
                delivery_exception=_delivery_exception(),
            )

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            source_receipts = list(pool.map(record_same_turn, range(worker_count)))

        receipt = self.setup_engine.archive_turn(
            agent_id,
            user_id,
            turn_id,
            idempotency_key="archive-idempotent-turn",
        )

        nodes = self.storage.load_nodes(agent_id, user_id)
        turns = self.setup_engine.list_turns(agent_id, user_id)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(len(turns), 1)
        self.assertEqual(nodes[0].source_turn_id, turn_id)
        self.assertEqual(nodes[0].relationship_id, relationship_id)
        self.assertEqual(
            {source_receipt.source_turn_id for source_receipt in source_receipts},
            {turn_id},
        )
        self.assertEqual(receipt.status, ArchivalStatus.COMPLETED)
        self.assertEqual(receipt.memory_node_count, 1)


if __name__ == "__main__":
    unittest.main()
