"""Public lifecycle contracts for atomic MemoryPack import publication."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import erii
import erii.data_lifecycle as lifecycle_module
from erii import (
    DataLifecycleCoordinator,
    ERIIEngine,
    FileStorage,
    LifecycleConflictError,
    LifecycleOperation,
    LifecycleOutcome,
    LifecyclePlan,
    LifecyclePlanError,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    MemoryPack,
    SQLiteStorage,
    StorageIntegrityError,
    StorageWriteError,
)
from erii.lifecycle_memory_pack_import_contracts import (
    MemoryPackStagingAdapter,
    MemoryPackStagingImportReport,
)


FIXTURE_SOURCE = (
    Path(__file__).parent
    / "fixtures"
    / "lifecycle"
    / "memory-pack-v0.4.0a7"
    / "source.erii"
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


class LifecycleMemoryPackImportCoordinatorTests(unittest.TestCase):
    """Runs atomic public imports against both built-in destinations."""

    @staticmethod
    def _target(kind: LifecycleTargetKind, path: Path) -> LifecycleTarget:
        return LifecycleTarget(kind=kind, path=str(path))

    @staticmethod
    def _destinations(root: Path, label: str):
        yield (
            LifecycleTargetKind.FILE_STORAGE,
            root / f"{label}-file-store",
            FileStorage,
            MemoryPackStagingAdapter.FILE_STORAGE,
        )
        yield (
            LifecycleTargetKind.SQLITE,
            root / f"{label}.sqlite3",
            SQLiteStorage,
            MemoryPackStagingAdapter.SQLITE,
        )

    @staticmethod
    def _sources(root: Path) -> dict[str, Path]:
        old_path = root / "source-a7.erii"
        shutil.copyfile(FIXTURE_SOURCE, old_path)
        current_document = json.loads(FIXTURE_SOURCE.read_text(encoding="utf-8"))
        current_document["metadata"]["version"] = MemoryPack.CURRENT_VERSION
        current_path = root / "source-current.erii"
        current_path.write_text(
            json.dumps(current_document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"a7": old_path, "current": current_path}

    @staticmethod
    def _request(source, destination, *, target_agent_id=None, target_user_id=None):
        request_type = getattr(lifecycle_module, "MemoryPackImportRequest")
        return request_type(
            source=source,
            destination=destination,
            target_agent_id=target_agent_id,
            target_user_id=target_user_id,
        )

    def test_import_contracts_are_exported_from_the_public_package(self) -> None:
        expected_public = {
            "MemoryPackImportOptions",
            "MemoryPackImportRequest",
            "MemoryPackStagingImportReport",
        }
        self.assertTrue(expected_public.issubset(set(erii.__all__)))

    def test_current_and_a7_sources_make_strict_zero_write_v3_plans(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            sources = self._sources(root)
            lifecycle = DataLifecycleCoordinator()

            for source_label, source_path in sources.items():
                for kind, destination_path, _, _ in self._destinations(
                    root,
                    f"dry-{source_label}",
                ):
                    with self.subTest(source=source_label, destination=kind.value):
                        source_target = self._target(
                            LifecycleTargetKind.MEMORY_PACK,
                            source_path,
                        )
                        source = lifecycle.inspect(source_target)
                        destination = self._target(kind, destination_path)
                        request = self._request(source, destination)
                        before = _tree_snapshot(root)
                        source_bytes = source_path.read_bytes()

                        first = lifecycle.plan(request)
                        second = lifecycle.plan(request)

                        self.assertEqual(_tree_snapshot(root), before)
                        self.assertEqual(source_path.read_bytes(), source_bytes)
                        self.assertFalse(os.path.lexists(destination.path))
                        self.assertEqual(first, second)
                        self.assertEqual(first.to_json(), second.to_json())
                        self.assertEqual(first.contract_version, "3")
                        self.assertEqual(first.operation, LifecycleOperation.IMPORT)
                        self.assertEqual(first.source, source)
                        self.assertEqual(first.destination.target, destination)
                        self.assertEqual(first.destination.status, LifecycleStatus.MISSING)
                        self.assertIsNone(first.backup_destination)
                        self.assertIsNone(first.backup_destination_parent)
                        self.assertIsNone(first.selector.target_agent_id)
                        self.assertIsNone(first.selector.target_user_id)
                        self.assertEqual(LifecyclePlan.from_json(first.to_json()), first)

                        document = json.loads(first.to_json())
                        self.assertEqual(set(document), _V3_PLAN_FIELDS)
                        self.assertEqual(
                            document["selector"],
                            {
                                "target_agent_id": None,
                                "target_user_id": None,
                            },
                        )
                        unknown_selector = json.loads(first.to_json())
                        unknown_selector["selector"]["overwrite"] = True
                        with self.assertRaises(LifecyclePlanError):
                            LifecyclePlan.from_json(json.dumps(unknown_selector))

                        expected_status = (
                            LifecycleStatus.MIGRATION_REQUIRED
                            if source_label == "a7"
                            else LifecycleStatus.CURRENT
                        )
                        self.assertEqual(source.status, expected_status)

    def test_import_is_atomic_semantically_equal_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            sources = self._sources(root)
            reports: dict[
                tuple[str, LifecycleTargetKind],
                MemoryPackStagingImportReport,
            ] = {}

            for source_label, source_path in sources.items():
                source_pack = MemoryPack.from_json(source_path.read_text(encoding="utf-8"))
                source_bytes = source_path.read_bytes()
                for kind, destination_path, storage_factory, expected_adapter in (
                    self._destinations(root, f"imported-{source_label}")
                ):
                    with self.subTest(source=source_label, destination=kind.value):
                        lifecycle = DataLifecycleCoordinator()
                        source = lifecycle.inspect(
                            self._target(
                                LifecycleTargetKind.MEMORY_PACK,
                                source_path,
                            )
                        )
                        destination = self._target(kind, destination_path)
                        plan = lifecycle.plan(self._request(source, destination))
                        plan_json = plan.to_json()

                        report = lifecycle.execute(plan)

                        self.assertEqual(report.operation, LifecycleOperation.IMPORT)
                        self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
                        self.assertIsInstance(
                            report.details,
                            MemoryPackStagingImportReport,
                        )
                        self.assertEqual(report.details.adapter, expected_adapter)
                        self.assertEqual(report.details.agent_id, source_pack.agent_id)
                        self.assertEqual(report.details.user_id, source_pack.user_id)
                        self.assertEqual(source_path.read_bytes(), source_bytes)
                        final = lifecycle.inspect(destination)
                        self.assertEqual(final.status, LifecycleStatus.CURRENT)
                        self.assertEqual(report.artifact_fingerprint, final.fingerprint)

                        with ERIIEngine(
                            storage_driver=storage_factory(destination.path)
                        ) as engine:
                            exported = engine.export_memory(
                                source_pack.agent_id,
                                source_pack.user_id,
                            )
                        self.assertEqual(exported.core_memory, source_pack.core_memory)
                        self.assertEqual(
                            len(exported.relationship_events),
                            len(source_pack.relationship_events),
                        )
                        self.assertEqual(
                            exported.relationship.relationship_id,
                            source_pack.relationship.relationship_id,
                        )

                        retried = DataLifecycleCoordinator().execute(
                            LifecyclePlan.from_json(plan_json)
                        )
                        self.assertEqual(
                            retried.outcome,
                            LifecycleOutcome.ALREADY_COMPLETE,
                        )
                        self.assertEqual(retried.operation_id, report.operation_id)
                        self.assertEqual(retried.details, report.details)

                        rendered = json.dumps(report.to_dict(), ensure_ascii=False)
                        rendered += repr(report)
                        private_bodies = (
                            source_pack.core_memory,
                            source_pack.relationship.blueprint.source_text,
                            source_pack.turn_records[0].transcript.user_message.content,
                        )
                        for private_body in private_bodies:
                            self.assertNotIn(private_body, rendered)

                        reports[(source_label, kind)] = report.details

            for source_label in sources:
                file_key = (source_label, LifecycleTargetKind.FILE_STORAGE)
                sqlite_key = (source_label, LifecycleTargetKind.SQLITE)
                if file_key not in reports or sqlite_key not in reports:
                    # A failing backend subtest already identifies the missing
                    # receipt; avoid obscuring it with a follow-on KeyError.
                    continue
                file_report = reports[file_key]
                sqlite_report = reports[sqlite_key]
                self.assertEqual(
                    file_report.semantic_sha256,
                    sqlite_report.semantic_sha256,
                )
                self.assertEqual(file_report.counts, sqlite_report.counts)

    def test_existing_destination_is_rejected_during_zero_write_planning(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_path = self._sources(root)["current"]
            lifecycle = DataLifecycleCoordinator()
            source = lifecycle.inspect(
                self._target(LifecycleTargetKind.MEMORY_PACK, source_path)
            )

            for kind, destination_path, storage_factory, _ in self._destinations(
                root,
                "existing",
            ):
                with self.subTest(destination=kind.value):
                    if kind is LifecycleTargetKind.FILE_STORAGE:
                        destination_path.mkdir()
                    else:
                        storage_factory(str(destination_path))
                    destination = self._target(kind, destination_path)
                    before = _tree_snapshot(root)

                    with self.assertRaises(LifecycleConflictError):
                        lifecycle.plan(self._request(source, destination))

                    self.assertEqual(_tree_snapshot(root), before)

    def test_publication_failure_leaves_target_missing_and_plan_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_path = self._sources(root)["a7"]
            source_bytes = source_path.read_bytes()

            for kind, destination_path, _, _ in self._destinations(root, "fault"):
                with self.subTest(destination=kind.value):
                    lifecycle = DataLifecycleCoordinator()
                    source = lifecycle.inspect(
                        self._target(LifecycleTargetKind.MEMORY_PACK, source_path)
                    )
                    destination = self._target(kind, destination_path)
                    plan = lifecycle.plan(self._request(source, destination))
                    real_publish = lifecycle_module._rename_no_replace
                    failed = False

                    def fail_target_publication(staging: Path, target: Path) -> None:
                        nonlocal failed
                        if not failed and Path(target) == destination_path:
                            failed = True
                            raise OSError("injected MemoryPack import publication failure")
                        real_publish(staging, target)

                    with mock.patch.object(
                        lifecycle_module,
                        "_rename_no_replace",
                        side_effect=fail_target_publication,
                    ):
                        with self.assertRaises(StorageWriteError):
                            lifecycle.execute(plan)

                    self.assertTrue(failed)
                    self.assertFalse(os.path.lexists(destination.path))
                    self.assertEqual(source_path.read_bytes(), source_bytes)

                    retried = lifecycle.execute(plan)
                    self.assertEqual(retried.outcome, LifecycleOutcome.APPLIED)
                    self.assertEqual(
                        lifecycle.inspect(destination).status,
                        LifecycleStatus.CURRENT,
                    )

    def test_bound_history_remap_fails_before_any_target_is_published(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_path = self._sources(root)["a7"]
            source_bytes = source_path.read_bytes()

            for kind, destination_path, _, _ in self._destinations(root, "remap"):
                with self.subTest(destination=kind.value):
                    lifecycle = DataLifecycleCoordinator()
                    source = lifecycle.inspect(
                        self._target(LifecycleTargetKind.MEMORY_PACK, source_path)
                    )
                    destination = self._target(kind, destination_path)
                    plan = lifecycle.plan(
                        self._request(
                            source,
                            destination,
                            target_agent_id="remapped-agent",
                            target_user_id="remapped-user",
                        )
                    )
                    self.assertEqual(
                        json.loads(plan.to_json())["selector"],
                        {
                            "target_agent_id": "remapped-agent",
                            "target_user_id": "remapped-user",
                        },
                    )

                    with self.assertRaises(
                        (ValueError, StorageIntegrityError)
                    ) as raised:
                        lifecycle.execute(plan)

                    self.assertIn("remap", str(raised.exception).lower())
                    self.assertFalse(os.path.lexists(destination.path))
                    self.assertEqual(source_path.read_bytes(), source_bytes)


if __name__ == "__main__":
    unittest.main()
