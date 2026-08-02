"""Public FileStorage upgrade contracts for the v0.4 Beta lifecycle Module."""

import hashlib
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
    LifecycleConflictError,
    LifecycleOperation,
    LifecycleOutcome,
    LifecyclePlan,
    LifecyclePlanError,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    RestoreRequest,
    StaleLifecyclePlanError,
    StorageWriteError,
    UpgradeRequest,
)


FIXTURE_ROOT = (
    Path(__file__).parent
    / "fixtures"
    / "lifecycle"
    / "file-storage-v0.3.1"
)
FIXTURE_SOURCE = FIXTURE_ROOT / "source"
PRODUCER_COMMIT = "b2cae61663c8612cb804ce16a358a192d3dd6d53"
UPGRADE_STRATEGY = "file-storage-legacy-to-v1"
FILE_STORAGE_V1_MANIFEST = b'{"format":"erii.file-storage","version":1}'


def file_bytes(root: Path) -> dict[str, bytes]:
    """Returns the exact regular-file payload of one fixture or live store."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def tree_snapshot(root: Path) -> dict[str, tuple[str, int, bytes]]:
    """Captures names, kinds, mtimes and bytes so a dry-run cannot write briefly."""
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


class HistoricalFileStorageFixtureTests(unittest.TestCase):
    def test_v031_fixture_has_frozen_provenance_and_file_digests(self) -> None:
        metadata = json.loads((FIXTURE_ROOT / "fixture.json").read_text(encoding="utf-8"))

        self.assertEqual(metadata["fixture_contract"], "1")
        self.assertEqual(metadata["storage_kind"], "file_storage")
        self.assertEqual(metadata["producer"]["package_version"], "0.3.1")
        self.assertEqual(metadata["producer"]["commit"], PRODUCER_COMMIT)
        self.assertEqual(metadata["producer"]["interface"], "erii.FileStorage")
        self.assertEqual(metadata["data_classification"], "synthetic_non_user_data")
        self.assertEqual(
            metadata["expected_inspection"],
            {
                "format_id": "erii.file-storage",
                "detected_version": "legacy",
                "target_version": "1",
                "file_count": 3,
            },
        )

        declared = {
            item["path"]: item["sha256"] for item in metadata["source_files"]
        }
        self.assertEqual(
            set(declared),
            {
                "fixture_agent_3074860a/fixture_user_47a7552d/core_memory.json",
                "fixture_agent_3074860a/fixture_user_47a7552d/nodes.json",
                "fixture_agent_3074860a/fixture_user_47a7552d/timeline.json",
            },
        )
        actual = file_bytes(FIXTURE_SOURCE)
        self.assertEqual(set(actual), set(declared))
        for relative_name, content in actual.items():
            with self.subTest(path=relative_name):
                self.assertEqual(hashlib.sha256(content).hexdigest(), declared[relative_name])


class LifecycleUpgradeTests(unittest.TestCase):
    @staticmethod
    def target(kind: LifecycleTargetKind, path: Path) -> LifecycleTarget:
        return LifecycleTarget(kind=kind, path=str(path))

    def copied_source(self, root: Path) -> tuple[LifecycleTarget, Path]:
        source_path = root / "legacy-store"
        shutil.copytree(FIXTURE_SOURCE, source_path)
        return self.target(LifecycleTargetKind.FILE_STORAGE, source_path), source_path

    def upgrade_request(
        self,
        lifecycle: DataLifecycleCoordinator,
        root: Path,
    ) -> tuple[UpgradeRequest, LifecycleTarget, LifecycleTarget, Path]:
        source_target, source_path = self.copied_source(root)
        source = lifecycle.inspect(source_target)
        destination = self.target(
            LifecycleTargetKind.FILE_STORAGE,
            root / "upgraded-store",
        )
        backup_destination = self.target(
            LifecycleTargetKind.BACKUP,
            root / "legacy-source.eriibak",
        )
        request = UpgradeRequest(
            source=source,
            destination=destination,
            backup_destination=backup_destination,
        )
        return request, destination, backup_destination, source_path

    def test_upgrade_plan_is_a_stable_zero_write_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            lifecycle = DataLifecycleCoordinator()
            request, destination, backup_destination, _ = self.upgrade_request(
                lifecycle,
                root,
            )
            before = tree_snapshot(root)

            first = lifecycle.plan(request)
            second = lifecycle.plan(request)

            self.assertEqual(tree_snapshot(root), before)
            self.assertFalse(Path(destination.path).exists())
            self.assertFalse(Path(backup_destination.path).exists())
            self.assertEqual(first, second)
            self.assertEqual(first.to_json(), second.to_json())
            self.assertEqual(LifecyclePlan.from_json(first.to_json()), first)
            self.assertEqual(first.contract_version, "3")
            self.assertIsNone(first.selector)
            self.assertEqual(first.operation, LifecycleOperation.UPGRADE)
            self.assertEqual(first.strategy_id, UPGRADE_STRATEGY)
            self.assertEqual(first.source, request.source)
            self.assertEqual(first.source.status, LifecycleStatus.MIGRATION_REQUIRED)
            self.assertEqual(first.source.detected_version, "legacy")
            self.assertEqual(first.destination.target, destination)
            self.assertEqual(first.destination.status, LifecycleStatus.MISSING)
            self.assertEqual(first.backup_destination.target, backup_destination)
            self.assertEqual(
                first.backup_destination.status,
                LifecycleStatus.MISSING,
            )
            self.assertEqual(
                first.backup_destination_parent,
                first.destination_parent,
            )
            self.assertEqual(first.content.kind, LifecycleTargetKind.FILE_STORAGE)
            self.assertEqual(first.content.status, LifecycleStatus.CURRENT)
            self.assertEqual(first.content.format_id, "erii.file-storage")
            self.assertEqual(first.content.detected_version, "1")
            self.assertEqual(first.content.current_version, "1")
            self.assertEqual(first.content.file_count, first.source.file_count + 1)
            self.assertNotEqual(first.content.fingerprint, first.source.fingerprint)
            self.assertIn("UpgradeRequest", erii.__all__)

    def test_upgrade_preserves_source_and_publishes_verified_backup_then_v1(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            lifecycle = DataLifecycleCoordinator()
            request, destination, backup_destination, source_path = self.upgrade_request(
                lifecycle,
                root,
            )
            plan = lifecycle.plan(request)
            source_before = tree_snapshot(source_path)
            plan_json = plan.to_json()

            report = lifecycle.execute(plan)

            self.assertEqual(report.operation, LifecycleOperation.UPGRADE)
            self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
            self.assertEqual(report.content_fingerprint, plan.content.fingerprint)
            self.assertEqual(report.file_count, plan.content.file_count)
            self.assertEqual(tree_snapshot(source_path), source_before)

            backup = lifecycle.inspect(backup_destination)
            self.assertEqual(backup.status, LifecycleStatus.CURRENT)
            self.assertEqual(backup.detected_version, "1")
            upgraded = lifecycle.inspect(destination)
            self.assertEqual(upgraded.status, LifecycleStatus.CURRENT)
            self.assertEqual(upgraded.detected_version, "1")
            self.assertEqual(upgraded.fingerprint, plan.content.fingerprint)
            self.assertEqual(upgraded.file_count, request.source.file_count + 1)

            original_files = file_bytes(source_path)
            upgraded_files = file_bytes(Path(destination.path))
            self.assertEqual(
                upgraded_files.pop(".erii-store.json"),
                FILE_STORAGE_V1_MANIFEST,
            )
            self.assertEqual(upgraded_files, original_files)

            restored_target = self.target(
                LifecycleTargetKind.FILE_STORAGE,
                root / "restored-legacy-source",
            )
            lifecycle.execute(
                lifecycle.plan(
                    RestoreRequest(
                        backup=backup,
                        destination=restored_target,
                    )
                )
            )
            restored = lifecycle.inspect(restored_target)
            self.assertEqual(restored.status, LifecycleStatus.MIGRATION_REQUIRED)
            self.assertEqual(restored.detected_version, "legacy")
            self.assertEqual(restored.fingerprint, request.source.fingerprint)
            self.assertEqual(restored.file_count, request.source.file_count)

            retried = DataLifecycleCoordinator().execute(
                LifecyclePlan.from_json(plan_json)
            )
            self.assertEqual(retried.outcome, LifecycleOutcome.ALREADY_COMPLETE)
            self.assertEqual(retried.operation_id, report.operation_id)

    def test_target_publication_failure_preserves_backup_and_plan_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            lifecycle = DataLifecycleCoordinator()
            request, destination, backup_destination, _ = self.upgrade_request(
                lifecycle,
                root,
            )
            plan = lifecycle.plan(request)
            plan_json = plan.to_json()
            real_publish = lifecycle_module._rename_no_replace

            def fail_only_target_publication(staging: Path, target: Path) -> None:
                if Path(target) == Path(destination.path):
                    raise OSError("injected upgrade target publication failure")
                real_publish(staging, target)

            with mock.patch.object(
                lifecycle_module,
                "_rename_no_replace",
                side_effect=fail_only_target_publication,
            ):
                with self.assertRaises(StorageWriteError):
                    lifecycle.execute(plan)

            backup = lifecycle.inspect(backup_destination)
            self.assertEqual(backup.status, LifecycleStatus.CURRENT)
            self.assertEqual(backup.detected_version, "1")
            self.assertFalse(Path(destination.path).exists())
            backup_before_retry = tree_snapshot(Path(backup_destination.path))

            retried = DataLifecycleCoordinator().execute(
                LifecyclePlan.from_json(plan_json)
            )

            self.assertEqual(retried.outcome, LifecycleOutcome.APPLIED)
            self.assertEqual(
                tree_snapshot(Path(backup_destination.path)),
                backup_before_retry,
            )
            upgraded = DataLifecycleCoordinator().inspect(destination)
            self.assertEqual(upgraded.status, LifecycleStatus.CURRENT)
            self.assertEqual(upgraded.fingerprint, plan.content.fingerprint)

    def test_matching_upgrade_backup_is_reused_without_republication(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            lifecycle = DataLifecycleCoordinator()
            request, destination, backup_destination, _ = self.upgrade_request(
                lifecycle,
                root,
            )
            plan = lifecycle.plan(request)
            real_publish = lifecycle_module._rename_no_replace

            def fail_only_target_publication(staging: Path, target: Path) -> None:
                if Path(target) == Path(destination.path):
                    raise OSError("injected upgrade target publication failure")
                real_publish(staging, target)

            with mock.patch.object(
                lifecycle_module,
                "_rename_no_replace",
                side_effect=fail_only_target_publication,
            ):
                with self.assertRaises(StorageWriteError):
                    lifecycle.execute(plan)

            backup_path = Path(backup_destination.path)
            backup_before_retry = tree_snapshot(backup_path)

            def forbid_backup_republication(staging: Path, target: Path) -> None:
                if Path(target) == backup_path:
                    raise AssertionError("a matching verified backup must not be rewritten")
                real_publish(staging, target)

            with mock.patch.object(
                lifecycle_module,
                "_rename_no_replace",
                side_effect=forbid_backup_republication,
            ):
                report = DataLifecycleCoordinator().execute(
                    LifecyclePlan.from_json(plan.to_json())
                )

            self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
            self.assertEqual(tree_snapshot(backup_path), backup_before_retry)
            self.assertEqual(
                DataLifecycleCoordinator().inspect(destination).fingerprint,
                plan.content.fingerprint,
            )

    def test_upgrade_rejects_occupied_destinations_and_unsafe_path_overlap(self) -> None:
        occupied_cases = (
            ("upgrade destination", LifecycleTargetKind.FILE_STORAGE),
            ("backup destination", LifecycleTargetKind.BACKUP),
        )
        for label, occupied_kind in occupied_cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                lifecycle = DataLifecycleCoordinator()
                source_target, _ = self.copied_source(root)
                source = lifecycle.inspect(source_target)
                destination = self.target(
                    LifecycleTargetKind.FILE_STORAGE,
                    root / "upgraded-store",
                )
                backup_destination = self.target(
                    LifecycleTargetKind.BACKUP,
                    root / "legacy-source.eriibak",
                )
                occupied = (
                    destination
                    if occupied_kind is LifecycleTargetKind.FILE_STORAGE
                    else backup_destination
                )
                Path(occupied.path).mkdir()

                with self.assertRaises(LifecycleConflictError):
                    lifecycle.plan(
                        UpgradeRequest(
                            source=source,
                            destination=destination,
                            backup_destination=backup_destination,
                        )
                    )

        overlap_cases = ("source_destination", "source_backup", "destination_backup")
        for case in overlap_cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                lifecycle = DataLifecycleCoordinator()
                source_target, source_path = self.copied_source(root)
                source = lifecycle.inspect(source_target)
                destination_path = root / "upgraded-store"
                backup_path = root / "legacy-source.eriibak"
                if case == "source_destination":
                    destination_path = source_path / "upgraded-store"
                elif case == "source_backup":
                    backup_path = source_path / "legacy-source.eriibak"
                else:
                    backup_path = destination_path

                with self.assertRaises(LifecyclePlanError):
                    lifecycle.plan(
                        UpgradeRequest(
                            source=source,
                            destination=self.target(
                                LifecycleTargetKind.FILE_STORAGE,
                                destination_path,
                            ),
                            backup_destination=self.target(
                                LifecycleTargetKind.BACKUP,
                                backup_path,
                            ),
                        )
                    )

    def test_source_change_after_planning_fails_before_backup_or_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            lifecycle = DataLifecycleCoordinator()
            request, destination, backup_destination, source_path = self.upgrade_request(
                lifecycle,
                root,
            )
            plan = lifecycle.plan(request)
            core_path = next(source_path.rglob("core_memory.json"))
            changed = json.loads(core_path.read_text(encoding="utf-8"))
            changed["content"] = "规划完成后，来源发生了变化。"
            core_path.write_text(
                json.dumps(changed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            changed_bytes = core_path.read_bytes()

            with self.assertRaises(StaleLifecyclePlanError):
                lifecycle.execute(plan)

            self.assertEqual(core_path.read_bytes(), changed_bytes)
            self.assertFalse(Path(destination.path).exists())
            self.assertFalse(Path(backup_destination.path).exists())


if __name__ == "__main__":
    unittest.main()
