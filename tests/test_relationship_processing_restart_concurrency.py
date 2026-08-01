"""Restart and cross-instance serialization contracts for a7 processing."""

from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from erii import ERIIEngine, FileStorage, SQLiteStorage
from erii.core.relationship_processing import (
    RelationshipProcessingCapabilityError,
)
from erii.models.consolidation import (
    RelationshipProcessingOutcome,
    RelationshipProcessingStatus,
)
from erii.storage.base import cross_process_file_lock
from tests.test_relationship_processing_public import (
    _ReflectionInterpreter,
    _RelationshipExtractor,
)


def _preexisting_delivery_exception():
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.relationship-processing-restart/v1",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-01T08:00:00+08:00",
        "reply_attempt_number": None,
    }


class _BlockingExtractor(_RelationshipExtractor):
    """Holds the first extraction open so a competing Engine reaches the guard."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def extract(self, request):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("test did not release extractor")
        return super().extract(request)


class RelationshipProcessingRestartTests(unittest.TestCase):
    @staticmethod
    def _engine(root, extractor=None, interpreter=None):
        engine = ERIIEngine(
            storage_driver=FileStorage(root),
            relationship_event_extractor=extractor,
            persona_reflection_interpreter=interpreter,
        )
        engine.initialize_relationship(
            "agent-lumi",
            "user-chen",
            "Lumi values grounded shared experiences.",
        )
        return engine

    @staticmethod
    def _record_turn(engine):
        engine.record_turn(
            "agent-lumi",
            "user-chen",
            "The snow is beautiful.",
            "Yes. I want to remember this quiet moment.",
            turn_id="turn-snow",
            delivery_exception=_preexisting_delivery_exception(),
        )

    def test_terminal_run_is_readable_after_restart_without_extractor(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _RelationshipExtractor("none")
            with self._engine(root, extractor) as first:
                self._record_turn(first)
                expected = first.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )

            with self._engine(root) as restarted:
                actual = restarted.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )

            self.assertEqual(actual, expected)
            self.assertEqual(
                actual.outcome,
                RelationshipProcessingOutcome.NO_RELATIONSHIP_EVENT,
            )

    def test_partial_reflection_resumes_without_reconfiguring_extractor(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _RelationshipExtractor()
            failing = _ReflectionInterpreter(fail_first=True)
            with self._engine(root, extractor, failing) as first:
                self._record_turn(first)
                partial = first.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                self.assertEqual(
                    partial.status,
                    RelationshipProcessingStatus.PARTIAL_FAILED,
                )

            with self._engine(root) as missing_interpreter:
                with self.assertRaises(RelationshipProcessingCapabilityError):
                    missing_interpreter.process_relationship_turn(
                        "agent-lumi",
                        "user-chen",
                        "turn-snow",
                    )

            replacement = _ReflectionInterpreter()
            with self._engine(root, interpreter=replacement) as restarted:
                completed = restarted.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )

            self.assertEqual(
                completed.status,
                RelationshipProcessingStatus.COMPLETED,
            )
            self.assertEqual(len(extractor.requests), 1)
            self.assertEqual(len(replacement.requests), 1)


class RelationshipProcessingCrossInstanceTests(unittest.TestCase):
    @staticmethod
    def _storage_pairs(root):
        return (
            (
                "file",
                FileStorage(os.path.join(root, "files")),
                FileStorage(os.path.join(root, "files")),
            ),
            (
                "sqlite",
                SQLiteStorage(os.path.join(root, "memory.db")),
                SQLiteStorage(os.path.join(root, "memory.db")),
            ),
        )

    def test_extraction_and_reflection_are_invoked_once_across_engine_instances(self):
        with tempfile.TemporaryDirectory() as root:
            for name, first_storage, second_storage in self._storage_pairs(root):
                with self.subTest(storage=name):
                    extractor = _BlockingExtractor()
                    interpreter = _ReflectionInterpreter()
                    first = ERIIEngine(
                        storage_driver=first_storage,
                        relationship_event_extractor=extractor,
                        persona_reflection_interpreter=interpreter,
                    )
                    second = ERIIEngine(
                        storage_driver=second_storage,
                        relationship_event_extractor=extractor,
                        persona_reflection_interpreter=interpreter,
                    )
                    try:
                        first.initialize_relationship(
                            "agent-lumi",
                            "user-chen",
                            "Lumi values grounded shared experiences.",
                        )
                        second.initialize_relationship(
                            "agent-lumi",
                            "user-chen",
                            "Lumi values grounded shared experiences.",
                        )
                        first.record_turn(
                            "agent-lumi",
                            "user-chen",
                            "The snow is beautiful.",
                            "Yes. I want to remember this quiet moment.",
                            turn_id="turn-snow",
                            delivery_exception=_preexisting_delivery_exception(),
                        )
                        barrier = threading.Barrier(2)

                        def process(engine):
                            barrier.wait(timeout=5)
                            return engine.process_relationship_turn(
                                "agent-lumi",
                                "user-chen",
                                "turn-snow",
                            )

                        with ThreadPoolExecutor(max_workers=2) as pool:
                            futures = (
                                pool.submit(process, first),
                                pool.submit(process, second),
                            )
                            self.assertTrue(extractor.entered.wait(timeout=5))
                            time.sleep(0.15)
                            extractor.release.set()
                            runs = tuple(future.result(timeout=10) for future in futures)

                        self.assertEqual(runs[0], runs[1])
                        self.assertEqual(len(extractor.requests), 1)
                        self.assertEqual(len(interpreter.requests), 1)
                    finally:
                        extractor.release.set()
                        first.close()
                        second.close()


class CrossProcessFileLockTests(unittest.TestCase):
    def test_same_thread_can_reenter_one_file_lock(self):
        with tempfile.TemporaryDirectory() as root:
            lock_path = os.path.join(root, "relationship.lock")
            with cross_process_file_lock(lock_path):
                with cross_process_file_lock(lock_path):
                    self.assertTrue(os.path.exists(lock_path))

    def test_second_process_waits_until_the_os_lock_is_released(self):
        with tempfile.TemporaryDirectory() as root:
            lock_path = os.path.join(root, "relationship.lock")
            ready_path = os.path.join(root, "child-ready")
            acquired_path = os.path.join(root, "child-acquired")
            script = (
                "from pathlib import Path\n"
                "from erii.storage.base import cross_process_file_lock\n"
                f"Path({ready_path!r}).write_text('ready', encoding='utf-8')\n"
                f"with cross_process_file_lock({lock_path!r}):\n"
                f"    Path({acquired_path!r}).write_text("
                "'acquired', encoding='utf-8')\n"
            )

            child = None
            with cross_process_file_lock(lock_path):
                child = subprocess.Popen(
                    [sys.executable, "-c", script],
                    cwd=os.path.dirname(os.path.dirname(__file__)),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                deadline = time.monotonic() + 5
                while (
                    not os.path.exists(ready_path)
                    and child.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                self.assertTrue(os.path.exists(ready_path))
                time.sleep(0.15)
                self.assertIsNone(child.poll())
                self.assertFalse(os.path.exists(acquired_path))

            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, (stdout, stderr))
            self.assertTrue(os.path.exists(acquired_path))


if __name__ == "__main__":
    unittest.main()
