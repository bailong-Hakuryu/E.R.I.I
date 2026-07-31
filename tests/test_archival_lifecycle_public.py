"""Public contracts for reliable Source Turn archival."""

from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import tempfile
import threading
import time
import unittest

from erii import (
    ArchivalArtifactsDecision,
    ArchivalCapabilityError,
    ArchivalConflictError,
    ArchivalNoMemoryDecision,
    ArchivalOutcomeCode,
    ArchivalProcessingError,
    ArchivalStatus,
    ArchivalSubmissionError,
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    FileStorage,
    MemoryCandidate,
    MemoryType,
    SQLiteStorage,
    TimelineCandidate,
)


class ScriptedMemoryExtractor:
    """Small host-provided test capability; the kernel and stores stay real."""

    descriptor = ExtractorDescriptor(
        extractor_id="tests.scripted-memory-extractor",
        extractor_version="1.0",
        extraction_schema_version="1",
    )

    def __init__(self, *results):
        self._results = list(results)
        self.calls = []
        self._lock = threading.Lock()

    def extract(self, request):
        with self._lock:
            self.calls.append(request)
            if not self._results:
                raise AssertionError("unexpected extractor call")
            result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def artifact_decision():
    return ArchivalArtifactsDecision(
        timeline=(
            TimelineCandidate(
                content="We spent an ordinary afternoon together at the arcade.",
            ),
        ),
        memories=(
            MemoryCandidate(
                node_type=MemoryType.PREFERENCE,
                content="The user enjoys playing fighting games with me.",
                tags=("arcade", "shared-experience"),
                base_importance=0.72,
                emotional_score=0.35,
            ),
        ),
    )


class ArchivalLifecyclePublicTests(unittest.TestCase):
    """Runs the same archival contract through both bundled stores."""

    def _storage_factories(self, root_dir):
        return (
            ("file", lambda: FileStorage(os.path.join(root_dir, "files"))),
            ("sqlite", lambda: SQLiteStorage(os.path.join(root_dir, "memory.db"))),
        )

    @staticmethod
    def _record_source_turn(engine, turn_id="turn-arcade"):
        engine.initialize_relationship(
            "agent_erii",
            "user_one",
            "A gentle character learning to live an ordinary life.",
        )
        return engine.record_turn(
            "agent_erii",
            "user_one",
            "Let us go to the arcade.",
            "Okay. I want to play one more round.",
            turn_id=turn_id,
        )

    def test_inline_archival_atomically_publishes_artifacts_with_provenance(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = ScriptedMemoryExtractor(artifact_decision())
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(async_archival=False),
                )
                source_receipt = self._record_source_turn(engine)

                self.assertEqual(
                    [channel.value for channel in source_receipt.processing_plan.channels],
                    ["memory_archival"],
                )
                receipt = engine.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-arcade",
                    idempotency_key="archive-turn-arcade",
                )

                self.assertEqual(receipt.status, ArchivalStatus.COMPLETED)
                self.assertEqual(
                    receipt.outcome_code,
                    ArchivalOutcomeCode.ARTIFACTS_COMMITTED,
                )
                self.assertEqual(receipt.timeline_count, 1)
                self.assertEqual(receipt.memory_node_count, 1)
                live_outcomes = engine.get_source_processing_outcomes(
                    "agent_erii",
                    "user_one",
                    "turn-arcade",
                )
                self.assertEqual(
                    live_outcomes[0].state.value,
                    "artifacts_committed",
                )
                self.assertEqual(
                    engine.get_turn(
                        "agent_erii",
                        "user_one",
                        "turn-arcade",
                    ).processing_outcomes[0].state.value,
                    "pending",
                )
                self.assertEqual(len(extractor.calls), 1)
                self.assertEqual(extractor.calls[0].source_turn_id, "turn-arcade")
                self.assertEqual(
                    extractor.calls[0].transcript.agent_message.content,
                    "Okay. I want to play one more round.",
                )

                exported = engine.export_memory("agent_erii", "user_one")
                archived = [
                    node
                    for node in exported.nodes
                    if node.source_archival_id == receipt.archival_id
                ]
                self.assertEqual(len(archived), 1)
                self.assertEqual(archived[0].source_turn_id, "turn-arcade")
                self.assertEqual(
                    archived[0].extractor_descriptor.extractor_id,
                    extractor.descriptor.extractor_id,
                )
                self.assertEqual(
                    archived[0].extractor_descriptor.extractor_version,
                    extractor.descriptor.extractor_version,
                )
                self.assertIsNotNone(
                    archived[0].extractor_descriptor.processed_at,
                )
                recalled = engine.recall(
                    "agent_erii",
                    "user_one",
                    "arcade fighting games",
                )
                self.assertIn("playing fighting games", recalled)
                self.assertIn("ordinary afternoon", recalled)

                public_data = receipt.to_dict()
                self.assertNotIn("transcript", public_data)
                self.assertNotIn("user_message", str(public_data))
                engine.close()

    def test_explicit_no_memory_is_success_and_idempotent_retry_does_not_extract_twice(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = ScriptedMemoryExtractor(
                    ArchivalNoMemoryDecision(reason_code="ordinary_acknowledgement"),
                )
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(async_archival=False),
                )
                self._record_source_turn(engine, "turn-no-memory")

                first = engine.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-no-memory",
                    idempotency_key="archive-no-memory",
                )
                second = engine.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-no-memory",
                    idempotency_key="archive-no-memory",
                )

                self.assertEqual(first, second)
                self.assertEqual(first.status, ArchivalStatus.COMPLETED)
                self.assertEqual(first.outcome_code, ArchivalOutcomeCode.NO_MEMORY)
                self.assertEqual(first.timeline_count, 0)
                self.assertEqual(first.memory_node_count, 0)
                self.assertEqual(len(extractor.calls), 1)
                engine.close()

    def test_expired_receipt_compacts_to_tombstone_without_losing_artifacts_or_idempotency(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = ScriptedMemoryExtractor(artifact_decision())
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(
                        async_archival=False,
                        archival_receipt_retention_days=0,
                    ),
                )
                self._record_source_turn(engine, "turn-compacted")
                completed = engine.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-compacted",
                    idempotency_key="archive-compacted",
                )

                self.assertEqual(completed.retention_state.value, "full")
                self.assertEqual(engine.compact_archival_receipts(), 1)
                compacted = engine.get_archival_receipt(
                    "agent_erii",
                    "user_one",
                    completed.archival_id,
                )
                self.assertEqual(compacted.retention_state.value, "compacted")
                self.assertEqual(compacted.archival_id, completed.archival_id)
                self.assertEqual(compacted.status, ArchivalStatus.COMPLETED)
                self.assertFalse(hasattr(compacted, "artifact_manifest"))

                recalled = engine.recall(
                    "agent_erii",
                    "user_one",
                    "arcade fighting games",
                )
                self.assertIn("playing fighting games", recalled)
                self.assertIn("ordinary afternoon", recalled)

                retried = engine.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-compacted",
                    idempotency_key="archive-compacted",
                )
                self.assertEqual(retried, compacted)
                self.assertEqual(len(extractor.calls), 1)

                exported = engine.export_memory("agent_erii", "user_one")
                ledger = {
                    item.archival_id: item
                    for item in exported.archival_ledger
                }
                self.assertIn(completed.archival_id, ledger)
                self.assertEqual(
                    ledger[completed.archival_id].retention_state.value,
                    "compacted",
                )
                engine.close()

    def test_deferred_submission_survives_restart_and_requires_explicit_processing(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = ScriptedMemoryExtractor(artifact_decision())
                config = ERIIConfig(async_archival=True)
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=config,
                )
                self._record_source_turn(engine, "turn-deferred")
                pending = engine.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-deferred",
                    idempotency_key="archive-deferred",
                )
                self.assertEqual(pending.status, ArchivalStatus.PENDING)
                engine.close()

                reopened = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=config,
                )
                restored = reopened.get_archival_receipt(
                    "agent_erii",
                    "user_one",
                    pending.archival_id,
                )
                self.assertEqual(restored.status, ArchivalStatus.PENDING)
                self.assertEqual(reopened.process_pending(max_tasks=1), 1)
                completed = reopened.get_archival_receipt(
                    "agent_erii",
                    "user_one",
                    pending.archival_id,
                )
                self.assertEqual(completed.status, ArchivalStatus.COMPLETED)
                self.assertEqual(len(extractor.calls), 1)
                reopened.close()

    def test_same_idempotency_key_cannot_be_rebound_to_another_source_turn(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = ScriptedMemoryExtractor(
                    ArchivalNoMemoryDecision(reason_code="none"),
                )
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(async_archival=True),
                )
                self._record_source_turn(engine, "turn-one")
                self._record_source_turn(engine, "turn-two")
                engine.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-one",
                    idempotency_key="same-intent",
                )

                with self.assertRaises(ArchivalConflictError):
                    engine.archive_turn(
                        "agent_erii",
                        "user_one",
                        "turn-two",
                        idempotency_key="same-intent",
                    )
                engine.close()

    def test_incomplete_or_abandoned_turn_is_rejected_before_receipt_creation(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = ScriptedMemoryExtractor(artifact_decision())
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                )
                engine.initialize_relationship(
                    "agent_erii",
                    "user_one",
                    "A gentle character learning to live an ordinary life.",
                )
                engine.begin_turn(
                    "agent_erii",
                    "user_one",
                    "An unanswered message.",
                    turn_id="turn-open",
                )

                with self.assertRaises(ArchivalSubmissionError):
                    engine.archive_turn(
                        "agent_erii",
                        "user_one",
                        "turn-open",
                        idempotency_key="archive-open",
                    )
                engine.abandon_turn(
                    "agent_erii",
                    "user_one",
                    "turn-open",
                    reason="host_cancelled",
                )
                with self.assertRaises(ArchivalSubmissionError):
                    engine.archive_turn(
                        "agent_erii",
                        "user_one",
                        "turn-open",
                        idempotency_key="archive-abandoned",
                    )
                self.assertEqual(
                    engine.list_archival_receipts("agent_erii", "user_one"),
                    [],
                )
                engine.close()

    def test_missing_extractor_is_a_typed_capability_error(self):
        engine = ERIIEngine(storage_driver=FileStorage(tempfile.mkdtemp()))
        self._record_source_turn(engine, "turn-no-capability")

        with self.assertRaises(ArchivalCapabilityError):
            engine.archive_turn(
                "agent_erii",
                "user_one",
                "turn-no-capability",
                idempotency_key="archive-no-capability",
            )
        engine.close()

    def test_invalid_extractor_output_retries_then_fails_without_placeholder_memory(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = ScriptedMemoryExtractor({}, {})
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(
                        async_archival=False,
                        archival_max_attempts=2,
                        archival_base_delay_seconds=0.0,
                    ),
                )
                self._record_source_turn(engine, "turn-invalid-output")

                with self.assertRaises(ArchivalProcessingError) as first_failure:
                    engine.archive_turn(
                        "agent_erii",
                        "user_one",
                        "turn-invalid-output",
                        idempotency_key="archive-invalid-output",
                    )
                receipt = first_failure.exception.receipt
                self.assertEqual(receipt.status, ArchivalStatus.RETRY_WAIT)
                self.assertNotIn("{}", str(receipt.to_dict()))

                self.assertEqual(engine.process_pending(max_tasks=1), 1)
                failed = engine.get_archival_receipt(
                    "agent_erii",
                    "user_one",
                    receipt.archival_id,
                )
                self.assertEqual(failed.status, ArchivalStatus.FAILED)
                self.assertEqual(
                    failed.outcome_code,
                    ArchivalOutcomeCode.RETRY_EXHAUSTED,
                )
                with self.assertRaises(ArchivalProcessingError) as replay:
                    engine.archive_turn(
                        "agent_erii",
                        "user_one",
                        "turn-invalid-output",
                        idempotency_key="archive-invalid-output",
                    )
                self.assertEqual(
                    replay.exception.receipt.status,
                    ArchivalStatus.FAILED,
                )
                self.assertEqual(engine.export_memory("agent_erii", "user_one").nodes, [])
                engine.close()

    def test_two_consumers_claim_one_submission_without_duplicate_effects(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = ScriptedMemoryExtractor(artifact_decision())
                config = ERIIConfig(async_archival=True)
                first = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=config,
                )
                self._record_source_turn(first, "turn-concurrent")
                pending = first.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-concurrent",
                    idempotency_key="archive-concurrent",
                )
                second = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=config,
                )

                with ThreadPoolExecutor(max_workers=2) as pool:
                    processed = list(
                        pool.map(
                            lambda engine: engine.process_pending(max_tasks=1),
                            (first, second),
                        )
                    )

                self.assertEqual(sum(processed), 1)
                self.assertEqual(len(extractor.calls), 1)
                completed = first.get_archival_receipt(
                    "agent_erii",
                    "user_one",
                    pending.archival_id,
                )
                self.assertEqual(completed.status, ArchivalStatus.COMPLETED)
                matching = [
                    node
                    for node in first.export_memory("agent_erii", "user_one").nodes
                    if node.source_archival_id == pending.archival_id
                ]
                self.assertEqual(len(matching), 1)
                first.close()
                second.close()

    def test_heartbeat_keeps_a_slow_extraction_inside_its_processing_lease(self):
        class SlowFirstExtractor(ScriptedMemoryExtractor):
            def extract(self, request):
                if not self.calls:
                    time.sleep(0.1)
                return super().extract(request)

        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = SlowFirstExtractor(
                    artifact_decision(),
                    ArchivalNoMemoryDecision(reason_code="nothing_durable"),
                )
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(
                        async_archival=False,
                        archival_lease_seconds=0.05,
                        archival_base_delay_seconds=0.0,
                    ),
                )
                self._record_source_turn(engine, "turn-expired-worker")

                completed = engine.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-expired-worker",
                    idempotency_key="archive-expired-worker",
                )
                self.assertEqual(completed.status, ArchivalStatus.COMPLETED)
                self.assertEqual(
                    completed.outcome_code,
                    ArchivalOutcomeCode.ARTIFACTS_COMMITTED,
                )
                self.assertEqual(len(extractor.calls), 1)
                self.assertEqual(
                    len(engine.export_memory("agent_erii", "user_one").nodes),
                    1,
                )
                engine.close()

    def test_single_consumer_lease_prevents_two_tasks_from_extracting_in_parallel(self):
        class BlockingExtractor:
            descriptor = ExtractorDescriptor(
                extractor_id="tests.blocking-memory-extractor",
                extractor_version="1.0",
            )

            def __init__(self):
                self.calls = []
                self.started = threading.Event()
                self.release = threading.Event()

            def extract(self, request):
                self.calls.append(request)
                if len(self.calls) == 1:
                    self.started.set()
                    self.release.wait(timeout=2.0)
                return ArchivalNoMemoryDecision(reason_code="nothing_durable")

        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = BlockingExtractor()
                config = ERIIConfig(
                    async_archival=True,
                    archival_consumer_lease_seconds=2.0,
                )
                first = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=config,
                )
                self._record_source_turn(first, "turn-consumer-one")
                self._record_source_turn(first, "turn-consumer-two")
                first.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-consumer-one",
                    idempotency_key="archive-consumer-one",
                )
                first.archive_turn(
                    "agent_erii",
                    "user_one",
                    "turn-consumer-two",
                    idempotency_key="archive-consumer-two",
                )
                second = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=config,
                )

                with ThreadPoolExecutor(max_workers=2) as pool:
                    first_run = pool.submit(first.process_pending, 1)
                    self.assertTrue(extractor.started.wait(timeout=1.0))
                    second_run = pool.submit(second.process_pending, 1)
                    self.assertEqual(second_run.result(timeout=1.0), 0)
                    extractor.release.set()
                    self.assertEqual(first_run.result(timeout=2.0), 1)

                self.assertEqual(len(extractor.calls), 1)
                self.assertEqual(second.process_pending(max_tasks=1), 1)
                self.assertEqual(len(extractor.calls), 2)
                first.close()
                second.close()

    def test_commit_retry_replays_frozen_batch_without_calling_extractor_again(self):
        root = tempfile.mkdtemp()
        db_path = os.path.join(root, "commit-retry.db")
        extractor = ScriptedMemoryExtractor(artifact_decision())
        config = ERIIConfig(
            async_archival=True,
            archival_base_delay_seconds=0.0,
        )
        engine = ERIIEngine(
            storage_driver=SQLiteStorage(db_path),
            memory_extractor=extractor,
            config=config,
        )
        self._record_source_turn(engine, "turn-commit-retry")
        pending = engine.archive_turn(
            "agent_erii",
            "user_one",
            "turn-commit-retry",
            idempotency_key="archive-commit-retry",
        )
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TRIGGER fail_archival_commit
                BEFORE INSERT ON memory_nodes
                BEGIN
                    SELECT RAISE(ABORT, 'injected commit outage');
                END
                """
            )

        self.assertEqual(engine.process_pending(max_tasks=1), 1)
        retrying = engine.get_archival_receipt(
            "agent_erii",
            "user_one",
            pending.archival_id,
        )
        self.assertEqual(retrying.status, ArchivalStatus.RETRY_WAIT)
        self.assertEqual(retrying.phase.value, "commit")
        self.assertEqual(len(extractor.calls), 1)
        self.assertEqual(engine.export_memory("agent_erii", "user_one").nodes, [])
        engine.close()

        with sqlite3.connect(db_path) as conn:
            conn.execute("DROP TRIGGER fail_archival_commit")
        reopened = ERIIEngine(
            storage_driver=SQLiteStorage(db_path),
            memory_extractor=extractor,
            config=config,
        )
        self.assertEqual(reopened.process_pending(max_tasks=1), 1)
        completed = reopened.get_archival_receipt(
            "agent_erii",
            "user_one",
            pending.archival_id,
        )
        self.assertEqual(completed.status, ArchivalStatus.COMPLETED)
        self.assertEqual(len(extractor.calls), 1)
        self.assertEqual(
            len(
                [
                    node
                    for node in reopened.export_memory(
                        "agent_erii",
                        "user_one",
                    ).nodes
                    if node.source_archival_id == pending.archival_id
                ]
            ),
            1,
        )
        reopened.close()

    def test_drain_uses_a_submission_snapshot_and_close_does_not_implicitly_drain(self):
        extractor = ScriptedMemoryExtractor(
            ArchivalNoMemoryDecision(reason_code="none"),
        )
        storage = FileStorage(tempfile.mkdtemp())
        engine = ERIIEngine(
            storage_driver=storage,
            memory_extractor=extractor,
            config=ERIIConfig(async_archival=True),
        )
        self._record_source_turn(engine, "turn-drain")
        pending = engine.archive_turn(
            "agent_erii",
            "user_one",
            "turn-drain",
            idempotency_key="archive-drain",
        )

        shutdown = engine.close(timeout=0.1)
        self.assertEqual(shutdown.unfinished_archival_ids, ())
        self.assertEqual(
            engine.get_archival_receipt(
                "agent_erii",
                "user_one",
                pending.archival_id,
            ).status,
            ArchivalStatus.PENDING,
        )

        reopened = ERIIEngine(
            storage_driver=storage,
            memory_extractor=extractor,
            config=ERIIConfig(async_archival=True),
        )
        report = reopened.drain_archival(timeout=1.0)
        self.assertEqual(report.completed, 1)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.unfinished_archival_ids, ())
        reopened.close()

    def test_memorypack_carries_structured_timeline_provenance_and_tombstone_only(self):
        root = tempfile.mkdtemp()
        extractor = ScriptedMemoryExtractor(artifact_decision())
        source = ERIIEngine(
            storage_driver=FileStorage(os.path.join(root, "source")),
            memory_extractor=extractor,
            config=ERIIConfig(async_archival=False),
        )
        self._record_source_turn(source, "turn-portable-archive")
        receipt = source.archive_turn(
            "agent_erii",
            "user_one",
            "turn-portable-archive",
            idempotency_key="archive-portable",
        )

        pack = source.export_memory("agent_erii", "user_one")
        round_tripped = type(pack).from_json(pack.to_json())
        self.assertEqual(len(round_tripped.timeline_entries), 1)
        self.assertEqual(
            round_tripped.timeline_entries[0].source_archival_id,
            receipt.archival_id,
        )
        self.assertEqual(len(round_tripped.archival_ledger), 1)
        ledger_json = round_tripped.to_dict()["archival_ledger"][0]
        self.assertEqual(ledger_json["retention_state"], "compacted")
        self.assertNotIn("artifact_manifest", ledger_json)
        self.assertNotIn("safe_summary", ledger_json)
        self.assertNotIn("extraction_attempts", ledger_json)

        target = ERIIEngine(
            storage_driver=SQLiteStorage(os.path.join(root, "target.db")),
        )
        target.import_memory(round_tripped)
        restored = target.export_memory("agent_erii", "user_one")
        self.assertEqual(
            restored.timeline_entries[0].timeline_entry_id,
            round_tripped.timeline_entries[0].timeline_entry_id,
        )
        self.assertEqual(
            restored.timeline_entries[0].source_turn_id,
            "turn-portable-archive",
        )
        self.assertEqual(
            restored.archival_ledger[0].archival_id,
            receipt.archival_id,
        )
        restored_receipt = target.get_archival_receipt(
            "agent_erii",
            "user_one",
            receipt.archival_id,
        )
        self.assertEqual(restored_receipt.retention_state.value, "compacted")
        self.assertIn(
            "ordinary afternoon",
            target.recall("agent_erii", "user_one", "arcade"),
        )
        source.close()
        target.close()


if __name__ == "__main__":
    unittest.main()
