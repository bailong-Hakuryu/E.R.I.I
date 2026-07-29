"""Safety regressions for reliable archival lifecycle boundaries."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import os
import sqlite3
import tempfile
import threading
import time
import unittest

from erii import (
    ArchivalArtifactsDecision,
    ArchivalConflictError,
    ArchivalNoMemoryDecision,
    ArchivalOutcomeCode,
    ArchivalStatus,
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    FileStorage,
    MemoryCandidate,
    MemoryType,
    SQLiteStorage,
)


AGENT_ID = "agent_erii"
USER_ID = "user_one"


def _artifact_decision():
    return ArchivalArtifactsDecision(
        memories=(
            MemoryCandidate(
                node_type=MemoryType.EVENT,
                content="We promised to meet at the arcade again.",
                tags=("arcade", "promise"),
                base_importance=0.8,
            ),
        ),
    )


class AlwaysSlowExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.always-slow-extractor",
        extractor_version="1.0",
        extraction_schema_version="1",
    )

    def __init__(self, delay_seconds=0.08):
        self.delay_seconds = delay_seconds
        self.calls = []
        self._lock = threading.Lock()

    def extract(self, request):
        with self._lock:
            self.calls.append(request)
        time.sleep(self.delay_seconds)
        return ArchivalNoMemoryDecision(reason_code="nothing_durable")


class BlockingFirstExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.blocking-first-extractor",
        extractor_version="1.0",
        extraction_schema_version="1",
    )

    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    def extract(self, request):
        with self._lock:
            self.calls.append(request)
            call_number = len(self.calls)
        if call_number == 1:
            self.started.set()
            self.release.wait(timeout=2.0)
        return ArchivalNoMemoryDecision(reason_code="nothing_durable")


class ImmediateArtifactExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.immediate-artifact-extractor",
        extractor_version="1.0",
        extraction_schema_version="1",
    )

    def __init__(self):
        self.calls = []

    def extract(self, request):
        self.calls.append(request)
        return _artifact_decision()


class ArchivalSafetyRegressionTests(unittest.TestCase):
    @staticmethod
    def _storage_factories(root_dir):
        return (
            ("file", lambda: FileStorage(os.path.join(root_dir, "files"))),
            ("sqlite", lambda: SQLiteStorage(os.path.join(root_dir, "memory.db"))),
        )

    @staticmethod
    def _record_source_turn(engine, turn_id):
        engine.initialize_relationship(
            AGENT_ID,
            USER_ID,
            "A gentle character learning to live an ordinary life.",
        )
        return engine.record_turn(
            AGENT_ID,
            USER_ID,
            "Shall we go to the arcade again?",
            "Yes. I want to keep that promise.",
            turn_id=turn_id,
        )

    def test_expired_extraction_lease_cannot_bypass_attempt_budget(self):
        """A crashed charged attempt is failed without another model call."""
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = AlwaysSlowExtractor()
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(
                        async_archival=True,
                        archival_max_attempts=1,
                        archival_base_delay_seconds=0.0,
                        archival_lease_seconds=0.1,
                        archival_consumer_lease_seconds=0.1,
                    ),
                )
                try:
                    self._record_source_turn(engine, "turn-slow-lease")
                    pending = engine.archive_turn(
                        AGENT_ID,
                        USER_ID,
                        "turn-slow-lease",
                        idempotency_key="archive-slow-lease",
                    )
                    store = engine.storage.atomic_archival_store_v1()
                    now = time.time()
                    self.assertTrue(
                        store.acquire_archival_consumer(
                            "crashed-test-worker",
                            now=now,
                            lease_seconds=0.01,
                        )
                    )
                    claimed = store.claim_next_archival_record(
                        now=now,
                        lease_seconds=0.01,
                        permit_seconds=0.01,
                        archival_id=pending.archival_id,
                    )
                    self.assertIsNotNone(claimed)
                    self.assertEqual(claimed.receipt.extraction_attempts, 1)
                    time.sleep(0.03)
                    self.assertEqual(engine.process_pending(max_tasks=1), 1)

                    terminal = engine.get_archival_receipt(
                        AGENT_ID,
                        USER_ID,
                        pending.archival_id,
                    )
                    self.assertEqual(len(extractor.calls), 0)
                    self.assertEqual(terminal.status, ArchivalStatus.FAILED)
                    self.assertEqual(
                        terminal.outcome_code,
                        ArchivalOutcomeCode.RETRY_EXHAUSTED,
                    )
                finally:
                    engine.close()

    def test_close_waits_for_inflight_work_and_fences_the_next_claim(self):
        """Closing during extraction must not let the same drain claim task two."""
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = BlockingFirstExtractor()
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(
                        async_archival=True,
                        archival_lease_seconds=1.0,
                        archival_consumer_lease_seconds=1.0,
                    ),
                )
                self._record_source_turn(engine, "turn-close-first")
                self._record_source_turn(engine, "turn-close-second")
                first = engine.archive_turn(
                    AGENT_ID,
                    USER_ID,
                    "turn-close-first",
                    idempotency_key="archive-close-first",
                )
                engine.archive_turn(
                    AGENT_ID,
                    USER_ID,
                    "turn-close-second",
                    idempotency_key="archive-close-second",
                )

                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(engine.process_pending)
                    self.assertTrue(extractor.started.wait(timeout=1.0))
                    started_at = time.monotonic()
                    report = engine.close(timeout=0.1)
                    close_elapsed = time.monotonic() - started_at
                    extractor.release.set()
                    processed = future.result(timeout=2.0)

                self.assertGreaterEqual(close_elapsed, 0.07)
                self.assertFalse(report.worker_stopped)
                self.assertIn(first.archival_id, report.unfinished_archival_ids)
                self.assertEqual(processed, 1)
                self.assertEqual(
                    [request.source_turn_id for request in extractor.calls],
                    ["turn-close-first"],
                )
                final_report = engine.close(timeout=0.5)
                self.assertTrue(final_report.worker_stopped)

    def test_one_engine_rejects_overlapping_process_pending_calls(self):
        """Threads sharing one Engine cannot reuse its consumer identity in parallel."""
        extractor = BlockingFirstExtractor()
        engine = ERIIEngine(
            storage_driver=FileStorage(tempfile.mkdtemp()),
            memory_extractor=extractor,
            config=ERIIConfig(async_archival=True),
        )
        try:
            self._record_source_turn(engine, "turn-overlap")
            engine.archive_turn(
                AGENT_ID,
                USER_ID,
                "turn-overlap",
                idempotency_key="archive-overlap",
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                active = pool.submit(engine.process_pending, 1)
                self.assertTrue(extractor.started.wait(timeout=1.0))
                self.assertEqual(engine.process_pending(max_tasks=1), 0)
                extractor.release.set()
                self.assertEqual(active.result(timeout=2.0), 1)
            self.assertEqual(len(extractor.calls), 1)
        finally:
            extractor.release.set()
            engine.close()

    def test_sqlite_stale_save_cannot_delete_atomically_archived_nodes(self):
        """A legacy load/mutate/save cycle cannot erase a concurrent commit."""
        with tempfile.TemporaryDirectory() as root:
            extractor = ImmediateArtifactExtractor()
            engine = ERIIEngine(
                storage_driver=SQLiteStorage(os.path.join(root, "memory.db")),
                memory_extractor=extractor,
                config=ERIIConfig(async_archival=False),
            )
            try:
                self._record_source_turn(engine, "turn-sqlite-stale-save")
                stale_nodes = engine.storage.load_nodes(AGENT_ID, USER_ID)
                receipt = engine.archive_turn(
                    AGENT_ID,
                    USER_ID,
                    "turn-sqlite-stale-save",
                    idempotency_key="archive-sqlite-stale-save",
                )
                before_stale_save = {
                    node.node_id: node
                    for node in engine.storage.load_nodes(AGENT_ID, USER_ID)
                    if node.source_archival_id == receipt.archival_id
                }
                self.assertEqual(len(before_stale_save), 1)

                engine.storage.save_nodes(AGENT_ID, USER_ID, stale_nodes)

                after_stale_save = {
                    node.node_id: node
                    for node in engine.storage.load_nodes(AGENT_ID, USER_ID)
                    if node.source_archival_id == receipt.archival_id
                }
                self.assertEqual(after_stale_save, before_stale_save)
            finally:
                engine.close()

    def test_first_commit_consumes_the_commit_attempt_budget(self):
        """The commit performed after extraction is attempt one, not attempt zero."""
        with tempfile.TemporaryDirectory() as root:
            db_path = os.path.join(root, "memory.db")
            extractor = ImmediateArtifactExtractor()
            engine = ERIIEngine(
                storage_driver=SQLiteStorage(db_path),
                memory_extractor=extractor,
                config=ERIIConfig(
                    async_archival=True,
                    archival_max_attempts=1,
                    archival_base_delay_seconds=0.0,
                ),
            )
            try:
                self._record_source_turn(engine, "turn-first-commit")
                pending = engine.archive_turn(
                    AGENT_ID,
                    USER_ID,
                    "turn-first-commit",
                    idempotency_key="archive-first-commit",
                )
                with closing(sqlite3.connect(db_path)) as conn:
                    conn.execute(
                        """
                        CREATE TRIGGER fail_first_commit_budget
                        BEFORE INSERT ON memory_nodes
                        BEGIN
                            SELECT RAISE(ABORT, 'injected commit outage');
                        END
                        """
                    )
                    conn.commit()

                self.assertEqual(engine.process_pending(max_tasks=1), 1)
                failed = engine.get_archival_receipt(
                    AGENT_ID,
                    USER_ID,
                    pending.archival_id,
                )
                self.assertEqual(failed.status, ArchivalStatus.FAILED)
                self.assertEqual(
                    failed.outcome_code,
                    ArchivalOutcomeCode.RETRY_EXHAUSTED,
                )
                self.assertEqual(failed.extraction_attempts, 1)
                self.assertEqual(failed.commit_attempts, 1)
                self.assertEqual(len(extractor.calls), 1)
            finally:
                engine.close()

    def test_live_receipt_rejects_conflicting_tombstone_import_atomically(self):
        """Imported tombstones cannot poison a live terminal archival identity."""
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = ImmediateArtifactExtractor()
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(
                        async_archival=False,
                        archival_receipt_retention_days=0,
                    ),
                )
                try:
                    self._record_source_turn(engine, "turn-live-receipt")
                    receipt = engine.archive_turn(
                        AGENT_ID,
                        USER_ID,
                        "turn-live-receipt",
                        idempotency_key="archive-live-receipt",
                    )
                    original = next(
                        item
                        for item in engine.export_memory(
                            AGENT_ID,
                            USER_ID,
                        ).archival_ledger
                        if item.archival_id == receipt.archival_id
                    )
                    unrelated = replace(
                        original,
                        archival_id=f"portable-{original.archival_id}",
                        source_turn_id="portable-unrelated-turn",
                        request_fingerprint="1" * 64,
                        idempotency_fingerprint="2" * 64,
                    )
                    conflicting = replace(
                        original,
                        outcome_code=ArchivalOutcomeCode.NO_MEMORY,
                        request_fingerprint=(
                            "0" * 64
                            if original.request_fingerprint != "0" * 64
                            else "f" * 64
                        ),
                    )

                    with self.assertRaises(ArchivalConflictError):
                        engine.storage.import_archival_tombstones(
                            receipt.relationship_id,
                            [unrelated, conflicting],
                        )
                    portable = engine.export_memory(AGENT_ID, USER_ID)
                    portable.core_memory = "must not be partially imported"
                    portable.archival_ledger = [conflicting]
                    with self.assertRaises(ArchivalConflictError):
                        engine.import_memory(portable, overwrite=True)
                    self.assertEqual(
                        engine.get_core_memory(AGENT_ID, USER_ID),
                        "",
                    )

                    durable_ids = {
                        item.archival_id
                        for item in engine.storage.list_archival_tombstones(
                            receipt.relationship_id
                        )
                    }
                    self.assertEqual(durable_ids, {receipt.archival_id})
                    self.assertEqual(engine.compact_archival_receipts(), 1)
                    compacted = engine.get_archival_receipt(
                        AGENT_ID,
                        USER_ID,
                        receipt.archival_id,
                    )
                    self.assertEqual(compacted.retention_state.value, "compacted")
                    self.assertEqual(
                        compacted.request_fingerprint,
                        original.request_fingerprint,
                    )
                    self.assertEqual(compacted.outcome_code, original.outcome_code)
                    self.assertIn(
                        "promised to meet",
                        engine.recall(AGENT_ID, USER_ID, "arcade promise"),
                    )
                finally:
                    engine.close()


if __name__ == "__main__":
    unittest.main()
