"""Public lifecycle contracts for verified in-place erasure and rebuild."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import erii
import erii.data_lifecycle as lifecycle_module
from erii.core.adjudication import list_complete_relationship_events
from erii import (
    DataLifecycleCoordinator,
    DecisionOutcome,
    ERIIEngine,
    FileStorage,
    LifecycleOperation,
    LifecycleOutcome,
    LifecyclePlan,
    LifecyclePlanError,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    RestoreRequest,
    SQLiteStorage,
    StorageWriteError,
)
from erii.models.adjudication import GrowthTriggerKind, PersonaGrowthProposal
from erii.models.consolidation import ReflectionInterpreterDescriptor
from erii.models.provenance import ExtractorDescriptor
from erii.models.turn import LEGACY_TURN_RECORD_FORMAT_VERSION
from erii.lifecycle_erasure_contracts import (
    ErasureScope,
    ErasureSelectionError,
    ErasureSelector,
    ErasureTransformResult,
)


_V3_PLAN_FIELDS = {
    "contract_version",
    "operation",
    "operation_id",
    "source",
    "destination",
    "destination_parent",
    "content",
    "strategy_id",
    "backup_destination",
    "backup_destination_parent",
    "selector",
    "plan_digest",
}

_PRIVATE_BODIES = (
    "TARGET PERSONA BODY MUST NOT ENTER A REPORT",
    "TARGET EVENT BODY MUST NOT ENTER A REPORT",
    "KEPT PERSONA BODY MUST NOT ENTER A REPORT",
    "KEPT EVENT BODY MUST NOT ENTER A REPORT",
)


def _delivery_exception():
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.lifecycle-public-erasure/v1",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-02T08:00:00+08:00",
        "reply_attempt_number": None,
    }


class _EventExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.lifecycle-public-erasure",
        extractor_version="1",
        extraction_schema_version="1",
    )

    def extract(self, request):
        message = request.transcript.user_message
        return {
            "kind": "candidates",
            "candidates": [
                {
                    "candidate_key": request.source_turn_id,
                    "event_type": "shared_experience",
                    "summary": f"Derived event for {request.source_turn_id}",
                    "signal": {
                        "signal_type": "shared_experience",
                        "strength": "moderate",
                        "extraction_confidence": 0.95,
                        "interpretation_confidence": 0.9,
                    },
                    "evidence": [
                        {
                            "source_id": message.message_id,
                            "source_revision": request.source_revision,
                            "quote": message.content,
                            "start": 0,
                            "end": len(message.content),
                        }
                    ],
                }
            ],
        }


class _ReflectionInterpreter:
    descriptor = ReflectionInterpreterDescriptor(
        interpreter_id="tests.lifecycle-public-erasure-reflection",
        interpreter_version="1",
    )

    def interpret(self, request):
        return {
            "kind": "reflection",
            "content": "PUBLIC REFLECTION PRIVATE BODY",
            "emotional_direction": "warm",
            "emotional_intensity": "moderate",
            "core_meaning": "A private derived interpretation.",
        }


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int, bytes]]:
    """Captures names, mtimes, and bytes so planning cannot write transiently."""

    if not os.path.lexists(root):
        return {}
    paths = [root, *sorted(root.rglob("*"))]
    snapshot: dict[str, tuple[str, int, bytes]] = {}
    for path in paths:
        relative = "." if path == root else path.relative_to(root).as_posix()
        info = path.lstat()
        if path.is_dir():
            snapshot[relative] = ("directory", info.st_mtime_ns, b"")
        elif path.is_file():
            snapshot[relative] = ("file", info.st_mtime_ns, path.read_bytes())
        else:
            snapshot[relative] = ("other", info.st_mtime_ns, b"")
    return snapshot


class LifecycleErasureCoordinatorTests(unittest.TestCase):
    """Runs the same public lifecycle behavior against both built-in stores."""

    @staticmethod
    def _cases(root: Path):
        yield (
            LifecycleTargetKind.FILE_STORAGE,
            root / "live-file-store",
            FileStorage,
        )
        yield (
            LifecycleTargetKind.SQLITE,
            root / "live.sqlite3",
            SQLiteStorage,
        )

    @staticmethod
    def _target(kind: LifecycleTargetKind, path: Path) -> LifecycleTarget:
        return LifecycleTarget(kind=kind, path=str(path))

    @staticmethod
    def _mark_current_file_storage(storage_factory, path: Path) -> None:
        if storage_factory is FileStorage:
            # FileStorage v2 is explicitly marked; an unmarked tree is a
            # readable legacy source and must be upgraded before mutation.
            Path(path, ".erii-store.json").write_bytes(
                b'{"format":"erii.file-storage","version":2}'
            )

    @classmethod
    def _seed(cls, storage_factory, path: Path):
        with ERIIEngine(storage_driver=storage_factory(str(path))) as engine:
            target = engine.initialize_relationship(
                "agent-target",
                "shared-user",
                _PRIVATE_BODIES[0],
            )
            kept = engine.initialize_relationship(
                "agent-kept",
                "shared-user",
                _PRIVATE_BODIES[2],
            )
            engine.record_relationship_event(
                target.agent_id,
                target.user_id,
                "shared_experience",
                _PRIVATE_BODIES[1],
                event_id="event-target",
                state_delta={"trust": 0.04},
                belief_updates=[
                    {
                        "key": "target.private",
                        "value": "TARGET BELIEF BODY MUST NOT ENTER A REPORT",
                        "confidence": 0.91,
                    }
                ],
            )
            engine.record_relationship_event(
                kept.agent_id,
                kept.user_id,
                "shared_experience",
                _PRIVATE_BODIES[3],
                event_id="event-kept",
                state_delta={"trust": 0.02},
            )
        cls._mark_current_file_storage(storage_factory, path)
        return target, kept

    def _assert_report_has_no_content(self, report, *private_bodies: str) -> None:
        rendered = json.dumps(report.to_dict(), ensure_ascii=False) + repr(report)
        for private_body in private_bodies:
            self.assertNotIn(private_body, rendered)

    @staticmethod
    def _selector(profile) -> ErasureSelector:
        return ErasureSelector(
            scope=ErasureScope.RELATIONSHIP,
            agent_id=profile.agent_id,
            user_id=profile.user_id,
            relationship_id=profile.relationship_id,
        )

    def _request(self, operation: str, source, selector, backup_target):
        request_type = getattr(
            lifecycle_module,
            "EraseRequest" if operation == "erase" else "RebuildRequest",
        )
        return request_type(
            source=source,
            selector=selector,
            backup_destination=backup_target,
        )

    def test_erasure_contracts_are_exported_from_the_public_package(self) -> None:
        expected_public = {
            "EraseRequest",
            "RebuildRequest",
            "ErasureScope",
            "ErasureSelector",
            "ErasureTransformResult",
        }
        self.assertTrue(expected_public.issubset(set(erii.__all__)))

    def test_v3_plans_are_strict_stable_zero_write_documents(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            for kind, path, storage_factory in self._cases(Path(root_dir)):
                with self.subTest(kind=kind.value):
                    target, _ = self._seed(storage_factory, path)
                    lifecycle = DataLifecycleCoordinator()
                    live_target = self._target(kind, path)
                    source = lifecycle.inspect(live_target)
                    backup_target = self._target(
                        LifecycleTargetKind.BACKUP,
                        path.parent / f"{kind.value}-dry-run.eriibak",
                    )
                    request = self._request(
                        "erase",
                        source,
                        self._selector(target),
                        backup_target,
                    )
                    before = _tree_snapshot(path.parent)

                    first = lifecycle.plan(request)
                    second = lifecycle.plan(request)

                    self.assertEqual(_tree_snapshot(path.parent), before)
                    self.assertFalse(Path(backup_target.path).exists())
                    self.assertEqual(first, second)
                    self.assertEqual(first.to_json(), second.to_json())
                    self.assertEqual(first.contract_version, "3")
                    self.assertEqual(first.operation, LifecycleOperation.ERASE)
                    self.assertEqual(first.source, source)
                    self.assertEqual(first.destination, source)
                    self.assertEqual(first.selector, request.selector)
                    self.assertEqual(LifecyclePlan.from_json(first.to_json()), first)

                    document = json.loads(first.to_json())
                    self.assertEqual(set(document), _V3_PLAN_FIELDS)
                    self.assertEqual(document["selector"], request.selector.to_dict())

                    unknown_top_level = dict(document)
                    unknown_top_level["future_unverified_action"] = True
                    with self.assertRaises(LifecyclePlanError):
                        LifecyclePlan.from_json(json.dumps(unknown_top_level))

                    unknown_selector = json.loads(first.to_json())
                    unknown_selector["selector"]["conversation_body"] = "not allowed"
                    with self.assertRaises(LifecyclePlanError):
                        LifecyclePlan.from_json(json.dumps(unknown_selector))

    def test_erase_is_backup_first_exact_restorable_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            for kind, path, storage_factory in self._cases(root):
                with self.subTest(kind=kind.value):
                    target, kept = self._seed(storage_factory, path)
                    lifecycle = DataLifecycleCoordinator()
                    live_target = self._target(kind, path)
                    original = lifecycle.inspect(live_target)
                    backup_target = self._target(
                        LifecycleTargetKind.BACKUP,
                        root / f"{kind.value}-before-erase.eriibak",
                    )
                    plan = lifecycle.plan(
                        self._request(
                            "erase",
                            original,
                            self._selector(target),
                            backup_target,
                        )
                    )
                    plan_json = plan.to_json()

                    report = lifecycle.execute(plan)

                    self.assertEqual(report.operation, LifecycleOperation.ERASE)
                    self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
                    self.assertIsInstance(report.details, ErasureTransformResult)
                    self.assertEqual(
                        report.details.affected_relationship_ids,
                        (target.relationship_id,),
                    )
                    self.assertGreater(
                        report.details.inventory.counts["deleted"].get(
                            "relationship",
                            0,
                        ),
                        0,
                    )
                    final = lifecycle.inspect(live_target)
                    self.assertEqual(report.artifact_fingerprint, final.fingerprint)

                    reopened = storage_factory(str(path))
                    self.assertIsNone(
                        reopened.get_relationship(target.agent_id, target.user_id)
                    )
                    self.assertEqual(
                        reopened.get_relationship(kept.agent_id, kept.user_id),
                        kept,
                    )
                    self.assertEqual(
                        [
                            event.event_id
                            for event in reopened.list_relationship_events(
                                kept.relationship_id
                            )
                        ],
                        ["event-kept"],
                    )

                    backup = lifecycle.inspect(backup_target)
                    self.assertEqual(backup.status, LifecycleStatus.CURRENT)
                    restored_target = self._target(
                        kind,
                        root
                        / (
                            f"restored-{kind.value}"
                            if kind is LifecycleTargetKind.FILE_STORAGE
                            else f"restored-{kind.value}.sqlite3"
                        ),
                    )
                    restore_report = lifecycle.execute(
                        lifecycle.plan(
                            RestoreRequest(
                                backup=backup,
                                destination=restored_target,
                            )
                        )
                    )
                    self.assertEqual(restore_report.outcome, LifecycleOutcome.APPLIED)
                    restored = lifecycle.inspect(restored_target)
                    self.assertEqual(restored.fingerprint, original.fingerprint)
                    restored_storage = storage_factory(restored_target.path)
                    self.assertEqual(
                        restored_storage.get_relationship(
                            target.agent_id,
                            target.user_id,
                        ),
                        target,
                    )

                    retried = DataLifecycleCoordinator().execute(
                        LifecyclePlan.from_json(plan_json)
                    )
                    self.assertEqual(retried.outcome, LifecycleOutcome.ALREADY_COMPLETE)
                    self.assertEqual(retried.operation_id, report.operation_id)
                    self.assertEqual(retried.details, report.details)

                    rendered = json.dumps(report.to_dict(), ensure_ascii=False)
                    rendered += repr(report)
                    for private_body in (
                        *_PRIVATE_BODIES,
                        "TARGET BELIEF BODY MUST NOT ENTER A REPORT",
                    ):
                        self.assertNotIn(private_body, rendered)

    def test_publication_failure_rolls_back_the_original_live_store(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            for kind, path, storage_factory in self._cases(root):
                with self.subTest(kind=kind.value):
                    target, _ = self._seed(storage_factory, path)
                    lifecycle = DataLifecycleCoordinator()
                    live_target = self._target(kind, path)
                    original = lifecycle.inspect(live_target)
                    backup_target = self._target(
                        LifecycleTargetKind.BACKUP,
                        root / f"{kind.value}-rollback.eriibak",
                    )
                    plan = lifecycle.plan(
                        self._request(
                            "erase",
                            original,
                            self._selector(target),
                            backup_target,
                        )
                    )
                    real_publish = lifecycle_module._rename_no_replace
                    failed = False

                    def fail_transformed_live_publication(
                        staging: Path,
                        destination: Path,
                    ) -> None:
                        nonlocal failed
                        if (
                            not failed
                            and Path(destination) == path
                            and f".{LifecycleOperation.ERASE.value}.tmp"
                            in Path(staging).name
                        ):
                            failed = True
                            raise OSError("injected transformed-live publication failure")
                        real_publish(staging, destination)

                    with mock.patch.object(
                        lifecycle_module,
                        "_rename_no_replace",
                        side_effect=fail_transformed_live_publication,
                    ):
                        with self.assertRaises(StorageWriteError):
                            lifecycle.execute(plan)

                    self.assertTrue(failed)
                    self.assertEqual(lifecycle.inspect(live_target), original)
                    restored_live = storage_factory(str(path))
                    self.assertEqual(
                        restored_live.get_relationship(
                            target.agent_id,
                            target.user_id,
                        ),
                        target,
                    )
                    self.assertEqual(
                        lifecycle.inspect(backup_target).status,
                        LifecycleStatus.CURRENT,
                    )

                    retried = lifecycle.execute(plan)
                    self.assertEqual(retried.outcome, LifecycleOutcome.APPLIED)

    def test_rebuild_preserves_authoritative_history_and_returns_proof(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            for kind, path, storage_factory in self._cases(root):
                with self.subTest(kind=kind.value):
                    target, kept = self._seed(storage_factory, path)
                    before_storage = storage_factory(str(path))
                    target_events = before_storage.list_relationship_events(
                        target.relationship_id
                    )
                    kept_events = before_storage.list_relationship_events(
                        kept.relationship_id
                    )
                    lifecycle = DataLifecycleCoordinator()
                    live_target = self._target(kind, path)
                    source = lifecycle.inspect(live_target)
                    backup_target = self._target(
                        LifecycleTargetKind.BACKUP,
                        root / f"{kind.value}-before-rebuild.eriibak",
                    )
                    plan = lifecycle.plan(
                        self._request(
                            "rebuild",
                            source,
                            self._selector(target),
                            backup_target,
                        )
                    )

                    report = lifecycle.execute(plan)

                    self.assertEqual(report.operation, LifecycleOperation.REBUILD)
                    self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
                    self.assertIsInstance(report.details, ErasureTransformResult)
                    self.assertEqual(report.details.inventory.counts["deleted"], {})
                    self.assertEqual(len(report.details.rebuild_proofs), 1)
                    proof = report.details.rebuild_proofs[0]
                    self.assertEqual(proof.relationship_id, target.relationship_id)
                    self.assertEqual(proof.event_count, len(target_events))

                    reopened = storage_factory(str(path))
                    self.assertEqual(
                        reopened.list_relationship_events(target.relationship_id),
                        target_events,
                    )
                    self.assertEqual(
                        reopened.list_relationship_events(kept.relationship_id),
                        kept_events,
                    )
                    self.assertEqual(
                        reopened.get_relationship(target.agent_id, target.user_id),
                        target,
                    )
                    self.assertEqual(
                        lifecycle.inspect(backup_target).status,
                        LifecycleStatus.CURRENT,
                    )

                    rendered = json.dumps(report.to_dict(), ensure_ascii=False)
                    rendered += repr(report)
                    for private_body in (
                        *_PRIVATE_BODIES,
                        "TARGET BELIEF BODY MUST NOT ENTER A REPORT",
                    ):
                        self.assertNotIn(private_body, rendered)

                    retried = lifecycle.execute(plan)
                    self.assertEqual(retried.outcome, LifecycleOutcome.ALREADY_COMPLETE)
                    self.assertEqual(retried.details, report.details)

    def test_semantic_round_trip_must_pass_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            for kind, path, storage_factory in self._cases(root):
                with self.subTest(kind=kind.value):
                    target, _ = self._seed(storage_factory, path)
                    lifecycle = DataLifecycleCoordinator()
                    live_target = self._target(kind, path)
                    original = lifecycle.inspect(live_target)
                    backup_target = self._target(
                        LifecycleTargetKind.BACKUP,
                        root / f"{kind.value}-semantic-gate.eriibak",
                    )
                    plan = lifecycle.plan(
                        self._request(
                            "rebuild",
                            original,
                            self._selector(target),
                            backup_target,
                        )
                    )

                    with mock.patch(
                        "erii.engine.ERIIEngine.import_memory",
                        side_effect=ValueError("injected semantic failure"),
                    ):
                        with self.assertRaisesRegex(
                            ErasureSelectionError,
                            "semantically portable",
                        ):
                            lifecycle.execute(plan)

                    self.assertEqual(lifecycle.inspect(live_target), original)
                    self.assertEqual(
                        lifecycle.inspect(backup_target).status,
                        LifecycleStatus.CURRENT,
                    )

    def test_source_turn_erasure_cascades_and_preserves_other_authority(self) -> None:
        private_bodies = (
            "TURN DELETE USER PRIVATE BODY",
            "TURN DELETE AGENT PRIVATE BODY",
            "TURN KEEP USER PRIVATE BODY",
            "TURN KEEP AGENT PRIVATE BODY",
            "TURN-SCOPE UNRELATED PRIVATE EVENT",
        )
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            for kind, path, storage_factory in self._cases(root):
                with self.subTest(kind=kind.value):
                    with ERIIEngine(
                        storage_driver=storage_factory(str(path)),
                        relationship_event_extractor=_EventExtractor(),
                    ) as engine:
                        target = engine.initialize_relationship(
                            "agent-turn",
                            "user-turn",
                            "TURN-SCOPE PRIVATE PERSONA",
                        )
                        kept = engine.initialize_relationship(
                            "agent-turn-kept",
                            "user-turn-kept",
                            "TURN-SCOPE UNRELATED PRIVATE PERSONA",
                        )
                        engine.record_relationship_event(
                            kept.agent_id,
                            kept.user_id,
                            "shared_experience",
                            private_bodies[4],
                            event_id="turn-scope-unrelated-event",
                        )
                        for turn_id, user_body, agent_body in (
                            ("turn-delete", private_bodies[0], private_bodies[1]),
                            ("turn-keep", private_bodies[2], private_bodies[3]),
                        ):
                            engine.begin_turn(
                                target.agent_id,
                                target.user_id,
                                user_body,
                                turn_id=turn_id,
                            )
                            engine.complete_turn(
                                target.agent_id,
                                target.user_id,
                                turn_id,
                                agent_body,
                                delivery_disposition="shown_unreviewed",
                                delivery_exception=_delivery_exception(),
                                processing_channels=(
                                    "relationship_adjudication",
                                ),
                            )
                            engine.process_relationship_turn(
                                target.agent_id,
                                target.user_id,
                                turn_id,
                            )
                    self._mark_current_file_storage(storage_factory, path)
                    persisted = storage_factory(str(path))
                    removed_event_id = next(
                        record.events[0].event_id
                        for record in persisted.list_relationship_adjudications(
                            target.relationship_id
                        )
                        if record.receipt.source_turn_id == "turn-delete"
                    )
                    lifecycle = DataLifecycleCoordinator()
                    live_target = self._target(kind, path)
                    source = lifecycle.inspect(live_target)
                    selector = ErasureSelector(
                        scope=ErasureScope.SOURCE_TURN,
                        agent_id=target.agent_id,
                        user_id=target.user_id,
                        relationship_id=target.relationship_id,
                        source_turn_id="turn-delete",
                    )
                    backup_target = self._target(
                        LifecycleTargetKind.BACKUP,
                        root / f"{kind.value}-source-turn.eriibak",
                    )
                    plan = lifecycle.plan(
                        self._request("erase", source, selector, backup_target)
                    )
                    plan_json = plan.to_json()

                    report = lifecycle.execute(plan)

                    self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
                    self.assertIsInstance(report.details, ErasureTransformResult)
                    reopened = storage_factory(str(path))
                    self.assertEqual(
                        [
                            item.turn_id
                            for item in reopened.list_turn_records(
                                target.relationship_id
                            )
                        ],
                        ["turn-keep"],
                    )
                    kept_turn = reopened.get_turn_record(
                        target.relationship_id,
                        "turn-keep",
                    )
                    self.assertEqual(
                        kept_turn.turn_format_version,
                        LEGACY_TURN_RECORD_FORMAT_VERSION,
                    )
                    self.assertIsNone(kept_turn.context_baseline)
                    self.assertEqual(
                        reopened.list_reply_attempts(
                            target.relationship_id,
                            "turn-delete",
                        ),
                        [],
                    )
                    self.assertFalse(
                        any(
                            item.receipt.source_turn_id == "turn-delete"
                            for item in reopened.list_relationship_adjudications(
                                target.relationship_id
                            )
                        )
                    )
                    self.assertFalse(
                        any(
                            item.source_turn_id == "turn-delete"
                            for item in reopened.list_relationship_processing_runs(
                                target.relationship_id
                            )
                        )
                    )
                    remaining_events = list_complete_relationship_events(
                        reopened,
                        target.relationship_id,
                    )
                    self.assertEqual(remaining_events, [])
                    self.assertNotIn(
                        removed_event_id,
                        {item.event_id for item in remaining_events},
                    )
                    self.assertEqual(
                        reopened.get_relationship(kept.agent_id, kept.user_id),
                        kept,
                    )
                    self.assertEqual(
                        [
                            item.event_id
                            for item in reopened.list_relationship_events(
                                kept.relationship_id
                            )
                        ],
                        ["turn-scope-unrelated-event"],
                    )
                    self.assertEqual(report.details.rebuild_proofs[0].event_count, 0)
                    self.assertEqual(
                        report.details.inventory.counts["rebuilt"].get(
                            "source_turn_authority",
                            0,
                        ),
                        1,
                    )
                    self.assertGreaterEqual(
                        report.details.inventory.counts["deleted"].get(
                            "relationship_adjudication",
                            0,
                        ),
                        1,
                    )
                    self._assert_report_has_no_content(
                        report,
                        *private_bodies,
                        "TURN-SCOPE PRIVATE PERSONA",
                        "TURN-SCOPE UNRELATED PRIVATE PERSONA",
                    )

                    # Erasure must leave a semantically portable relationship.
                    # Later processing runs freeze adjudication-journal
                    # baselines, so deleting an earlier prefix cannot leave
                    # those stale commitments behind.
                    pack_path = root / f"{kind.value}-post-turn-erase.erii"
                    with ERIIEngine(storage_driver=reopened) as export_engine:
                        export_engine.export_memory(
                            target.agent_id,
                            target.user_id,
                            export_path=str(pack_path),
                        )
                    imported_path = (
                        root / f"{kind.value}-post-turn-erase-import"
                        if storage_factory is FileStorage
                        else root / f"{kind.value}-post-turn-erase-import.sqlite3"
                    )
                    with ERIIEngine(
                        storage_driver=storage_factory(str(imported_path))
                    ) as import_engine:
                        import_engine.import_memory(str(pack_path))
                        self.assertIsNotNone(
                            import_engine.get_relationship_snapshot(
                                target.agent_id,
                                target.user_id,
                            )
                        )

                    retried = DataLifecycleCoordinator().execute(
                        LifecyclePlan.from_json(plan_json)
                    )
                    self.assertEqual(retried.outcome, LifecycleOutcome.ALREADY_COMPLETE)
                    self.assertEqual(retried.details, report.details)

    def test_source_turn_erasure_removes_eventless_adjudication_receipts(self) -> None:
        """Corroborated/ignored decisions still retain source evidence and must cascade."""

        first_body = "FIRST OCCURRENCE AUTHORITY"
        removed_body = "SECRET SECOND TEXT"
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            for kind, path, storage_factory in self._cases(root):
                with self.subTest(kind=kind.value):
                    with ERIIEngine(
                        storage_driver=storage_factory(str(path)),
                    ) as engine:
                        profile = engine.initialize_relationship(
                            "agent-eventless",
                            "user-eventless",
                            "EVENTLESS RECEIPT PRIVATE PERSONA",
                        )
                        for turn_id, user_body in (
                            ("turn-authority", first_body),
                            ("turn-corroborated", removed_body),
                        ):
                            engine.record_turn(
                                profile.agent_id,
                                profile.user_id,
                                user_body,
                                f"Reply for {turn_id}",
                                turn_id=turn_id,
                                delivery_exception=_delivery_exception(),
                            )
                            turn = engine.get_turn(
                                profile.agent_id,
                                profile.user_id,
                                turn_id,
                            )
                            user_message = turn.transcript.user_message
                            result = engine.adjudicate_turn_candidates(
                                profile.agent_id,
                                profile.user_id,
                                turn_id,
                                [
                                    {
                                        "candidate_key": f"candidate-{turn_id}",
                                        "event_type": "shared_experience",
                                        "summary": "The same underlying shared event.",
                                        "signal": {
                                            "signal_type": "shared_experience",
                                            "strength": "moderate",
                                            "extraction_confidence": 0.95,
                                            "interpretation_confidence": 0.9,
                                        },
                                        "evidence": [
                                            {
                                                "source_id": user_message.message_id,
                                                "quote": user_body,
                                            }
                                        ],
                                        "occurrence_key": "eventless:shared-occurrence",
                                    }
                                ],
                                extractor_version="tests.lifecycle-eventless/v1",
                            )
                        self.assertEqual(
                            result.receipts[0].outcome,
                            DecisionOutcome.CORROBORATED,
                        )
                        self.assertEqual(result.events, ())

                    self._mark_current_file_storage(storage_factory, path)
                    lifecycle = DataLifecycleCoordinator()
                    live_target = self._target(kind, path)
                    source = lifecycle.inspect(live_target)
                    backup_target = self._target(
                        LifecycleTargetKind.BACKUP,
                        root / f"{kind.value}-eventless-source-turn.eriibak",
                    )
                    plan = lifecycle.plan(
                        self._request(
                            "erase",
                            source,
                            ErasureSelector(
                                scope=ErasureScope.SOURCE_TURN,
                                agent_id=profile.agent_id,
                                user_id=profile.user_id,
                                relationship_id=profile.relationship_id,
                                source_turn_id="turn-corroborated",
                            ),
                            backup_target,
                        )
                    )

                    report = lifecycle.execute(plan)

                    reopened = storage_factory(str(path))
                    receipts = reopened.list_relationship_adjudications(
                        profile.relationship_id
                    )
                    self.assertEqual(
                        [item.receipt.source_turn_id for item in receipts],
                        ["turn-authority"],
                    )
                    self.assertEqual(
                        len(
                            list_complete_relationship_events(
                                reopened,
                                profile.relationship_id,
                            )
                        ),
                        1,
                    )
                    self.assertGreaterEqual(
                        report.details.inventory.counts["deleted"].get(
                            "relationship_adjudication",
                            0,
                        ),
                        1,
                    )
                    self._assert_report_has_no_content(
                        report,
                        first_body,
                        removed_body,
                        "EVENTLESS RECEIPT PRIVATE PERSONA",
                    )

    def test_relationship_event_erasure_cascades_but_keeps_source_turns(self) -> None:
        private_bodies = (
            "EVENT DELETE USER PRIVATE BODY",
            "EVENT DELETE AGENT PRIVATE BODY",
            "EVENT KEEP USER PRIVATE BODY",
            "EVENT KEEP AGENT PRIVATE BODY",
            "EVENT-SCOPE UNRELATED PRIVATE EVENT",
            "PUBLIC GROWTH PRIVATE BODY",
            "PUBLIC GROWTH PRIVATE RATIONALE",
            "PUBLIC REFLECTION PRIVATE BODY",
        )
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            for kind, path, storage_factory in self._cases(root):
                with self.subTest(kind=kind.value):
                    with ERIIEngine(
                        storage_driver=storage_factory(str(path)),
                        relationship_event_extractor=_EventExtractor(),
                        persona_reflection_interpreter=_ReflectionInterpreter(),
                    ) as engine:
                        target = engine.initialize_relationship(
                            "agent-event",
                            "user-event",
                            "EVENT-SCOPE PRIVATE PERSONA",
                        )
                        kept = engine.initialize_relationship(
                            "agent-event-kept",
                            "user-event-kept",
                            "EVENT-SCOPE UNRELATED PRIVATE PERSONA",
                        )
                        engine.record_relationship_event(
                            kept.agent_id,
                            kept.user_id,
                            "shared_experience",
                            private_bodies[4],
                            event_id="event-scope-unrelated-event",
                        )
                        for turn_id, user_body, agent_body in (
                            (
                                "turn-event-delete",
                                private_bodies[0],
                                private_bodies[1],
                            ),
                            (
                                "turn-event-keep",
                                private_bodies[2],
                                private_bodies[3],
                            ),
                        ):
                            engine.begin_turn(
                                target.agent_id,
                                target.user_id,
                                user_body,
                                turn_id=turn_id,
                            )
                            engine.complete_turn(
                                target.agent_id,
                                target.user_id,
                                turn_id,
                                agent_body,
                                delivery_disposition="shown_unreviewed",
                                delivery_exception=_delivery_exception(),
                                processing_channels=(
                                    "relationship_adjudication",
                                ),
                            )
                            engine.process_relationship_turn(
                                target.agent_id,
                                target.user_id,
                                turn_id,
                            )
                    persisted = storage_factory(str(path))
                    target_record = next(
                        record
                        for record in persisted.list_relationship_adjudications(
                            target.relationship_id
                        )
                        if record.receipt.source_turn_id == "turn-event-delete"
                    )
                    target_event_id = target_record.events[0].event_id
                    persisted.save_persona_growth_proposal(
                        PersonaGrowthProposal(
                            proposal_id="public-growth-delete",
                            relationship_id=target.relationship_id,
                            revision=1,
                            intent_key="public-growth-key",
                            review_id="public-growth-review",
                            statement=private_bodies[5],
                            rationale=private_bodies[6],
                            proposed_changes={"voice": "private"},
                            supporting_event_ids=(target_event_id,),
                            trigger_kind=GrowthTriggerKind.PIVOTAL,
                        )
                    )
                    self._mark_current_file_storage(storage_factory, path)
                    lifecycle = DataLifecycleCoordinator()
                    live_target = self._target(kind, path)
                    source = lifecycle.inspect(live_target)
                    selector = ErasureSelector(
                        scope=ErasureScope.RELATIONSHIP_EVENT,
                        agent_id=target.agent_id,
                        user_id=target.user_id,
                        relationship_id=target.relationship_id,
                        relationship_event_id=target_event_id,
                    )
                    backup_target = self._target(
                        LifecycleTargetKind.BACKUP,
                        root / f"{kind.value}-relationship-event.eriibak",
                    )
                    plan = lifecycle.plan(
                        self._request("erase", source, selector, backup_target)
                    )
                    plan_json = plan.to_json()

                    report = lifecycle.execute(plan)

                    reopened = storage_factory(str(path))
                    self.assertEqual(
                        [
                            item.turn_id
                            for item in reopened.list_turn_records(
                                target.relationship_id
                            )
                        ],
                        ["turn-event-delete", "turn-event-keep"],
                    )
                    kept_turn = reopened.get_turn_record(
                        target.relationship_id,
                        "turn-event-keep",
                    )
                    self.assertEqual(
                        kept_turn.turn_format_version,
                        LEGACY_TURN_RECORD_FORMAT_VERSION,
                    )
                    self.assertIsNone(kept_turn.context_baseline)
                    remaining_events = list_complete_relationship_events(
                        reopened,
                        target.relationship_id,
                    )
                    self.assertEqual(remaining_events, [])
                    self.assertNotIn(
                        target_event_id,
                        {item.event_id for item in remaining_events},
                    )
                    self.assertFalse(
                        any(
                            target_event_id in item.supporting_event_ids
                            for item in reopened.list_persona_growth_proposals(
                                target.relationship_id
                            )
                        )
                    )
                    self.assertFalse(
                        any(
                            item.event_id == target_event_id
                            for item in reopened.list_persona_reflection_records(
                                target.relationship_id
                            )
                        )
                    )
                    self.assertEqual(
                        reopened.get_relationship(kept.agent_id, kept.user_id),
                        kept,
                    )
                    self.assertEqual(
                        [
                            item.event_id
                            for item in reopened.list_relationship_events(
                                kept.relationship_id
                            )
                        ],
                        ["event-scope-unrelated-event"],
                    )
                    self.assertEqual(report.details.rebuild_proofs[0].event_count, 0)
                    self.assertEqual(
                        report.details.inventory.counts["rebuilt"].get(
                            "source_turn_authority",
                            0,
                        ),
                        1,
                    )
                    self.assertGreaterEqual(
                        report.details.inventory.counts["deleted"].get(
                            "persona_growth",
                            0,
                        ),
                        1,
                    )
                    self.assertGreaterEqual(
                        report.details.inventory.counts["deleted"].get(
                            "persona_reflection",
                            0,
                        ),
                        1,
                    )
                    self._assert_report_has_no_content(
                        report,
                        *private_bodies,
                        "EVENT-SCOPE PRIVATE PERSONA",
                        "EVENT-SCOPE UNRELATED PRIVATE PERSONA",
                    )

                    pack_path = root / f"{kind.value}-post-event-erase.erii"
                    with ERIIEngine(storage_driver=reopened) as export_engine:
                        export_engine.export_memory(
                            target.agent_id,
                            target.user_id,
                            export_path=str(pack_path),
                        )
                    imported_path = (
                        root / f"{kind.value}-post-event-erase-import"
                        if storage_factory is FileStorage
                        else root / f"{kind.value}-post-event-erase-import.sqlite3"
                    )
                    with ERIIEngine(
                        storage_driver=storage_factory(str(imported_path))
                    ) as import_engine:
                        import_engine.import_memory(str(pack_path))
                        self.assertIsNotNone(
                            import_engine.get_relationship_snapshot(
                                target.agent_id,
                                target.user_id,
                            )
                        )

                    retried = DataLifecycleCoordinator().execute(
                        LifecyclePlan.from_json(plan_json)
                    )
                    self.assertEqual(retried.outcome, LifecycleOutcome.ALREADY_COMPLETE)
                    self.assertEqual(retried.details, report.details)

    def test_complete_user_erasure_crosses_agents_but_not_user_boundary(self) -> None:
        private_bodies = (
            "COMPLETE USER FIRST PRIVATE PERSONA",
            "COMPLETE USER SECOND PRIVATE PERSONA",
            "COMPLETE USER KEPT PRIVATE PERSONA",
            "COMPLETE USER FIRST PRIVATE EVENT",
            "COMPLETE USER SECOND PRIVATE EVENT",
            "COMPLETE USER KEPT PRIVATE EVENT",
        )
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            for kind, path, storage_factory in self._cases(root):
                with self.subTest(kind=kind.value):
                    with ERIIEngine(
                        storage_driver=storage_factory(str(path))
                    ) as engine:
                        first = engine.initialize_relationship(
                            "agent-user-one",
                            "complete-user",
                            private_bodies[0],
                        )
                        second = engine.initialize_relationship(
                            "agent-user-two",
                            "complete-user",
                            private_bodies[1],
                        )
                        kept = engine.initialize_relationship(
                            "agent-user-one",
                            "kept-user",
                            private_bodies[2],
                        )
                        engine.record_relationship_event(
                            first.agent_id,
                            first.user_id,
                            "shared_experience",
                            private_bodies[3],
                            event_id="complete-user-first-event",
                        )
                        engine.record_relationship_event(
                            second.agent_id,
                            second.user_id,
                            "shared_experience",
                            private_bodies[4],
                            event_id="complete-user-second-event",
                        )
                        engine.record_relationship_event(
                            kept.agent_id,
                            kept.user_id,
                            "shared_experience",
                            private_bodies[5],
                            event_id="complete-user-kept-event",
                        )
                    self.assertEqual(first.user_identity_id, second.user_identity_id)
                    self._mark_current_file_storage(storage_factory, path)
                    lifecycle = DataLifecycleCoordinator()
                    live_target = self._target(kind, path)
                    source = lifecycle.inspect(live_target)
                    selector = ErasureSelector(
                        scope=ErasureScope.COMPLETE_USER,
                        user_id=first.user_id,
                        user_identity_id=first.user_identity_id,
                    )
                    backup_target = self._target(
                        LifecycleTargetKind.BACKUP,
                        root / f"{kind.value}-complete-user.eriibak",
                    )
                    plan = lifecycle.plan(
                        self._request("erase", source, selector, backup_target)
                    )
                    plan_json = plan.to_json()

                    report = lifecycle.execute(plan)

                    reopened = storage_factory(str(path))
                    self.assertIsNone(
                        reopened.get_relationship(first.agent_id, first.user_id)
                    )
                    self.assertIsNone(
                        reopened.get_relationship(second.agent_id, second.user_id)
                    )
                    self.assertEqual(
                        reopened.get_relationship(kept.agent_id, kept.user_id),
                        kept,
                    )
                    self.assertEqual(
                        [
                            item.event_id
                            for item in reopened.list_relationship_events(
                                kept.relationship_id
                            )
                        ],
                        ["complete-user-kept-event"],
                    )
                    self.assertEqual(
                        report.details.affected_relationship_ids,
                        tuple(sorted((first.relationship_id, second.relationship_id))),
                    )
                    self.assertEqual(
                        report.details.inventory.counts["deleted"]["relationship"],
                        2,
                    )
                    self._assert_report_has_no_content(report, *private_bodies)

                    retried = DataLifecycleCoordinator().execute(
                        LifecyclePlan.from_json(plan_json)
                    )
                    self.assertEqual(retried.outcome, LifecycleOutcome.ALREADY_COMPLETE)
                    self.assertEqual(retried.details, report.details)


if __name__ == "__main__":
    unittest.main()
