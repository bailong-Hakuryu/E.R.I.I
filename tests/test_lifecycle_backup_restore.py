"""Public backup/restore contracts for the v0.4 Beta lifecycle Module."""

import json
import os
from pathlib import Path
import pickle
import shutil
import tempfile
import unittest
from unittest import mock

import erii
import erii._lifecycle.filesystem as filesystem_module
import erii._lifecycle.sqlite_semantics as sqlite_semantics_module
import erii.data_lifecycle as lifecycle_module
from erii import FileStorage, MemoryPack, SQLiteStorage
from erii.data_lifecycle import (
    BackupRequest,
    DataLifecycleCoordinator,
    LifecycleOperation,
    LifecycleOutcome,
    LifecyclePlan,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    RestoreRequest,
)
from erii.errors import (
    LifecycleConflictError,
    LifecyclePlanError,
    LifecycleVerificationError,
    StaleLifecyclePlanError,
    StorageIntegrityError,
    StorageWriteError,
)


AGENT_ID = "agent_lumi"
USER_ID = "user_chen"
PERSONA_SOURCE = "A stable persona source."


def snapshot_tree(root: Path) -> dict[str, bytes]:
    """Captures regular file bytes below a temporary test root."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class LifecycleBackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lifecycle = DataLifecycleCoordinator()

    @staticmethod
    def target(kind: LifecycleTargetKind, path: Path) -> LifecycleTarget:
        return LifecycleTarget(kind=kind, path=str(path))

    def create_source(
        self,
        root: Path,
        kind: LifecycleTargetKind,
    ) -> tuple[LifecycleTarget, Path]:
        if kind is LifecycleTargetKind.FILE_STORAGE:
            path = root / "source-store"
            storage = FileStorage(str(path))
            storage.save_core_memory(AGENT_ID, USER_ID, PERSONA_SOURCE)
            storage.add_timeline_entry(AGENT_ID, USER_ID, "A shared event.")
        elif kind is LifecycleTargetKind.SQLITE:
            path = root / "source.db"
            storage = SQLiteStorage(str(path))
            storage.save_core_memory(AGENT_ID, USER_ID, PERSONA_SOURCE)
        elif kind is LifecycleTargetKind.MEMORY_PACK:
            path = root / "source.erii"
            path.write_text(
                MemoryPack(
                    agent_id=AGENT_ID,
                    user_id=USER_ID,
                    core_memory=PERSONA_SOURCE,
                ).to_json(),
                encoding="utf-8",
            )
        else:
            self.fail(f"unsupported live source kind {kind.value}")
        return self.target(kind, path), path

    def restored_target(
        self,
        root: Path,
        kind: LifecycleTargetKind,
    ) -> LifecycleTarget:
        suffix = {
            LifecycleTargetKind.FILE_STORAGE: "restored-store",
            LifecycleTargetKind.SQLITE: "restored.db",
            LifecycleTargetKind.MEMORY_PACK: "restored.erii",
        }[kind]
        return self.target(kind, root / suffix)

    def test_all_live_formats_round_trip_through_verified_backup(self) -> None:
        for kind in (
            LifecycleTargetKind.FILE_STORAGE,
            LifecycleTargetKind.SQLITE,
            LifecycleTargetKind.MEMORY_PACK,
        ):
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                source_target, source_path = self.create_source(root, kind)
                backup_target = self.target(
                    LifecycleTargetKind.BACKUP,
                    root / "snapshot.eriibak",
                )
                source = self.lifecycle.inspect(source_target)
                before_plan = snapshot_tree(root)

                plan = self.lifecycle.plan(BackupRequest(source=source, destination=backup_target))
                same_plan = self.lifecycle.plan(
                    BackupRequest(source=source, destination=backup_target)
                )

                self.assertEqual(plan, same_plan)
                self.assertEqual(LifecyclePlan.from_json(plan.to_json()), plan)
                self.assertEqual(snapshot_tree(root), before_plan)
                self.assertFalse(Path(backup_target.path).exists())

                report = self.lifecycle.execute(plan)

                self.assertEqual(report.operation, LifecycleOperation.BACKUP)
                self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
                self.assertEqual(report.content_fingerprint, source.fingerprint)
                backup = self.lifecycle.inspect(backup_target)
                self.assertEqual(backup.status, LifecycleStatus.CURRENT)
                self.assertEqual(backup.detected_version, "1")
                manifest_text = (Path(backup_target.path) / "manifest.json").read_text(
                    encoding="utf-8"
                )
                self.assertNotIn(str(source_path), manifest_text)
                self.assertNotIn(PERSONA_SOURCE, manifest_text)

                repeated_backup = self.lifecycle.execute(plan)
                self.assertEqual(
                    repeated_backup.outcome,
                    LifecycleOutcome.ALREADY_COMPLETE,
                )
                self.assertEqual(repeated_backup.operation_id, report.operation_id)

                restore_target = self.restored_target(root, kind)
                restore_plan = self.lifecycle.plan(
                    RestoreRequest(backup=backup, destination=restore_target)
                )
                restore_report = self.lifecycle.execute(restore_plan)

                self.assertEqual(restore_report.operation, LifecycleOperation.RESTORE)
                self.assertEqual(restore_report.outcome, LifecycleOutcome.APPLIED)
                restored = self.lifecycle.inspect(restore_target)
                self.assertEqual(restored.fingerprint, source.fingerprint)
                self.assertEqual(restored.detected_version, source.detected_version)
                repeated_restore = self.lifecycle.execute(restore_plan)
                self.assertEqual(
                    repeated_restore.outcome,
                    LifecycleOutcome.ALREADY_COMPLETE,
                )

    def test_lifecycle_coordinator_contract_is_available_from_package_root(self) -> None:
        for name in (
            "BackupRequest",
            "DataLifecycleCoordinator",
            "LifecycleDirectoryIdentity",
            "LifecyclePlan",
            "LifecycleRequest",
            "LifecycleReport",
            "LifecycleTarget",
            "RestoreRequest",
        ):
            with self.subTest(name=name):
                self.assertIn(name, erii.__all__)
                self.assertIsNotNone(getattr(erii, name))

    def test_lifecycle_contracts_keep_one_identity_across_import_paths(self) -> None:
        from erii._lifecycle import contracts as internal_contracts

        for name in internal_contracts.__all__:
            with self.subTest(name=name):
                root_contract = getattr(erii, name)
                facade_contract = getattr(lifecycle_module, name)
                internal_contract = getattr(internal_contracts, name)

                self.assertIn(name, erii.__all__)
                self.assertIn(name, lifecycle_module.__all__)
                self.assertIs(root_contract, facade_contract)
                self.assertIs(facade_contract, internal_contract)
                if isinstance(internal_contract, type):
                    self.assertEqual(
                        internal_contract.__module__,
                        "erii.data_lifecycle",
                    )
                    self.assertIs(
                        pickle.loads(pickle.dumps(internal_contract)),
                        internal_contract,
                    )

    def test_changed_source_is_rejected_before_backup_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.FILE_STORAGE,
            )
            source = self.lifecycle.inspect(source_target)
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            plan = self.lifecycle.plan(BackupRequest(source=source, destination=backup_target))
            FileStorage(source_target.path).save_core_memory(
                AGENT_ID,
                USER_ID,
                "Changed after planning.",
            )

            with self.assertRaises(StaleLifecyclePlanError):
                self.lifecycle.execute(plan)

            self.assertFalse(Path(backup_target.path).exists())

    def test_empty_file_storage_and_sqlite_round_trip_without_becoming_missing(self) -> None:
        for kind in (
            LifecycleTargetKind.FILE_STORAGE,
            LifecycleTargetKind.SQLITE,
        ):
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                source_path = root / (
                    "empty-store" if kind is LifecycleTargetKind.FILE_STORAGE else "empty.db"
                )
                if kind is LifecycleTargetKind.FILE_STORAGE:
                    source_path.mkdir()
                else:
                    source_path.write_bytes(b"")
                source_target = self.target(kind, source_path)
                source = self.lifecycle.inspect(source_target)
                self.assertEqual(source.status, LifecycleStatus.EMPTY)
                backup_target = self.target(
                    LifecycleTargetKind.BACKUP,
                    root / "empty.eriibak",
                )
                self.lifecycle.execute(
                    self.lifecycle.plan(BackupRequest(source=source, destination=backup_target))
                )
                restore_target = self.restored_target(root, kind)
                self.lifecycle.execute(
                    self.lifecycle.plan(
                        RestoreRequest(
                            backup=self.lifecycle.inspect(backup_target),
                            destination=restore_target,
                        )
                    )
                )

                restored = self.lifecycle.inspect(restore_target)
                self.assertEqual(restored.status, LifecycleStatus.EMPTY)
                self.assertEqual(restored.fingerprint, source.fingerprint)

    def test_backup_payload_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            plan = self.lifecycle.plan(
                BackupRequest(
                    source=self.lifecycle.inspect(source_target),
                    destination=backup_target,
                )
            )
            self.lifecycle.execute(plan)
            payload = Path(backup_target.path) / "payload" / "memory-pack.erii"
            payload.write_bytes(payload.read_bytes() + b" ")

            with self.assertRaises(StorageIntegrityError):
                self.lifecycle.inspect(backup_target)

    def test_backup_bundle_rejects_unmanifested_root_entries(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            self.lifecycle.execute(
                self.lifecycle.plan(
                    BackupRequest(
                        source=self.lifecycle.inspect(source_target),
                        destination=backup_target,
                    )
                )
            )
            (Path(backup_target.path) / "unexpected.txt").write_text(
                "not declared",
                encoding="utf-8",
            )

            with self.assertRaises(StorageIntegrityError):
                self.lifecycle.inspect(backup_target)

    def test_plan_document_rejects_unknown_fields_and_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            plan = self.lifecycle.plan(
                BackupRequest(
                    source=self.lifecycle.inspect(source_target),
                    destination=self.target(
                        LifecycleTargetKind.BACKUP,
                        root / "snapshot.eriibak",
                    ),
                )
            )
            document = json.loads(plan.to_json())
            document["future_step"] = {"run": "anything"}
            with self.assertRaises(LifecyclePlanError):
                LifecyclePlan.from_json(json.dumps(document))

            document.pop("future_step")
            document["destination"]["target"]["path"] = str(root / "elsewhere")
            with self.assertRaises(LifecyclePlanError):
                LifecyclePlan.from_json(json.dumps(document))

    def test_restore_kind_and_existing_destinations_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.FILE_STORAGE,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            backup_plan = self.lifecycle.plan(
                BackupRequest(
                    source=self.lifecycle.inspect(source_target),
                    destination=backup_target,
                )
            )
            self.lifecycle.execute(backup_plan)
            backup = self.lifecycle.inspect(backup_target)

            with self.assertRaises(LifecyclePlanError):
                self.lifecycle.plan(
                    RestoreRequest(
                        backup=backup,
                        destination=self.target(
                            LifecycleTargetKind.SQLITE,
                            root / "wrong-kind.db",
                        ),
                    )
                )

            occupied = root / "occupied-store"
            FileStorage(str(occupied)).save_core_memory(
                AGENT_ID,
                USER_ID,
                "Different data.",
            )
            with self.assertRaises(LifecycleConflictError):
                self.lifecycle.plan(
                    RestoreRequest(
                        backup=backup,
                        destination=self.target(
                            LifecycleTargetKind.FILE_STORAGE,
                            occupied,
                        ),
                    )
                )

    def test_overlapping_backup_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, source_path = self.create_source(
                root,
                LifecycleTargetKind.FILE_STORAGE,
            )
            with self.assertRaises(LifecyclePlanError):
                self.lifecycle.plan(
                    BackupRequest(
                        source=self.lifecycle.inspect(source_target),
                        destination=self.target(
                            LifecycleTargetKind.BACKUP,
                            source_path / "nested-backup.eriibak",
                        ),
                    )
                )

    def test_publication_failure_leaves_no_backup_or_owned_staging(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            plan = self.lifecycle.plan(
                BackupRequest(
                    source=self.lifecycle.inspect(source_target),
                    destination=backup_target,
                )
            )

            with mock.patch(
                "erii.data_lifecycle._rename_no_replace",
                side_effect=OSError("fault"),
            ):
                with self.assertRaises(StorageWriteError):
                    self.lifecycle.execute(plan)

            self.assertFalse(Path(backup_target.path).exists())
            leftovers = [path.name for path in root.iterdir()]
            self.assertFalse(any(name.endswith(".owner") for name in leftovers))
            self.assertFalse(any(name.endswith(".tmp") for name in leftovers))
            self.assertEqual(
                [name for name in leftovers if name.endswith(".erii-lifecycle.lock")],
                [".snapshot.eriibak.erii-lifecycle.lock"],
            )

    def test_restore_publication_failure_preserves_missing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            self.lifecycle.execute(
                self.lifecycle.plan(
                    BackupRequest(
                        source=self.lifecycle.inspect(source_target),
                        destination=backup_target,
                    )
                )
            )
            restore_target = self.restored_target(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            restore_plan = self.lifecycle.plan(
                RestoreRequest(
                    backup=self.lifecycle.inspect(backup_target),
                    destination=restore_target,
                )
            )

            with mock.patch(
                "erii.data_lifecycle._rename_no_replace",
                side_effect=OSError("fault"),
            ):
                with self.assertRaises(StorageWriteError):
                    self.lifecycle.execute(restore_plan)

            self.assertFalse(Path(restore_target.path).exists())
            self.assertFalse(any(path.name.endswith(".owner") for path in root.iterdir()))
            self.assertFalse(any(path.name.endswith(".tmp") for path in root.iterdir()))

    def test_sqlite_fingerprint_is_filename_independent(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, source_path = self.create_source(
                root,
                LifecycleTargetKind.SQLITE,
            )
            copied_path = root / "renamed.db"
            shutil.copyfile(source_path, copied_path)

            original = self.lifecycle.inspect(source_target)
            renamed = self.lifecycle.inspect(self.target(LifecycleTargetKind.SQLITE, copied_path))

            self.assertEqual(original.fingerprint, renamed.fingerprint)

    def test_empty_sqlite_with_nonempty_sidecar_is_rejected(self) -> None:
        for suffix in ("-wal", "-journal"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                database = root / "empty.db"
                database.write_bytes(b"")
                Path(f"{database}{suffix}").write_bytes(b"uncheckpointed data")

                with self.assertRaises(StorageIntegrityError):
                    self.lifecycle.inspect(self.target(LifecycleTargetKind.SQLITE, database))

    def test_sqlite_sidecar_appearing_after_planning_prevents_backup(self) -> None:
        for suffix in ("-wal", "-journal"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                database = root / "empty.db"
                database.write_bytes(b"")
                source_target = self.target(LifecycleTargetKind.SQLITE, database)
                backup_target = self.target(
                    LifecycleTargetKind.BACKUP,
                    root / "snapshot.eriibak",
                )
                plan = self.lifecycle.plan(
                    BackupRequest(
                        source=self.lifecycle.inspect(source_target),
                        destination=backup_target,
                    )
                )
                Path(f"{database}{suffix}").write_bytes(b"uncheckpointed data")

                with self.assertRaises(StorageIntegrityError):
                    self.lifecycle.execute(plan)

                self.assertFalse(Path(backup_target.path).exists())

    def test_empty_sqlite_sidecar_appearing_during_inspection_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            database = root / "empty.db"
            database.write_bytes(b"")
            target = self.target(LifecycleTargetKind.SQLITE, database)
            real_read_version = sqlite_semantics_module.read_sqlite_schema_version

            def inject_wal(path_value, *, immutable):
                version = real_read_version(path_value, immutable=immutable)
                Path(f"{database}-wal").write_bytes(b"appeared during inspection")
                return version

            with mock.patch.object(
                sqlite_semantics_module,
                "read_sqlite_schema_version",
                side_effect=inject_wal,
            ):
                with self.assertRaises(StorageIntegrityError):
                    self.lifecycle.inspect(target)

    def test_file_storage_scan_rejects_inode_replacement_after_enumeration(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, source_path = self.create_source(
                root,
                LifecycleTargetKind.FILE_STORAGE,
            )
            source_file = next(source_path.rglob("core_memory.json"))
            original_bytes = source_file.read_bytes()
            original_stat = source_file.stat()
            displaced = root / "displaced-core-memory.json"
            real_scan = filesystem_module._scan_tree_entries
            replaced = False

            def replace_after_first_scan(scan_root, *, exclude_relative_name):
                nonlocal replaced
                result = real_scan(
                    scan_root,
                    exclude_relative_name=exclude_relative_name,
                )
                if Path(scan_root) == source_path and not replaced:
                    replaced = True
                    source_file.replace(displaced)
                    source_file.write_bytes(original_bytes)
                    os.utime(
                        source_file,
                        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                    )
                return result

            with mock.patch(
                "erii._lifecycle.filesystem._scan_tree_entries",
                side_effect=replace_after_first_scan,
            ):
                with self.assertRaises(StorageIntegrityError):
                    self.lifecycle.inspect(source_target)

    def test_serialized_plans_retry_idempotently_across_coordinator_instances(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            backup_plan_json = self.lifecycle.plan(
                BackupRequest(
                    source=self.lifecycle.inspect(source_target),
                    destination=backup_target,
                )
            ).to_json()

            first_backup = DataLifecycleCoordinator().execute(
                LifecyclePlan.from_json(backup_plan_json)
            )
            retried_backup = DataLifecycleCoordinator().execute(
                LifecyclePlan.from_json(backup_plan_json)
            )

            self.assertEqual(first_backup.outcome, LifecycleOutcome.APPLIED)
            self.assertEqual(retried_backup.outcome, LifecycleOutcome.ALREADY_COMPLETE)
            self.assertEqual(retried_backup.operation_id, first_backup.operation_id)

            restore_target = self.restored_target(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            planning_coordinator = DataLifecycleCoordinator()
            restore_plan_json = planning_coordinator.plan(
                RestoreRequest(
                    backup=planning_coordinator.inspect(backup_target),
                    destination=restore_target,
                )
            ).to_json()

            first_restore = DataLifecycleCoordinator().execute(
                LifecyclePlan.from_json(restore_plan_json)
            )
            retried_restore = DataLifecycleCoordinator().execute(
                LifecyclePlan.from_json(restore_plan_json)
            )

            self.assertEqual(first_restore.outcome, LifecycleOutcome.APPLIED)
            self.assertEqual(retried_restore.outcome, LifecycleOutcome.ALREADY_COMPLETE)
            self.assertEqual(retried_restore.operation_id, first_restore.operation_id)

    def test_file_storage_preserves_unknown_lock_named_data_only(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, source_path = self.create_source(
                root,
                LifecycleTargetKind.FILE_STORAGE,
            )
            unknown_lock = source_path / "story.lock"
            unknown_lock.write_bytes(b"user-owned lock-named data")
            runtime_lock = source_path / "_turn_context_snapshot.lock"
            runtime_lock.write_bytes(b"runtime coordination state")
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )

            self.lifecycle.execute(
                self.lifecycle.plan(
                    BackupRequest(
                        source=self.lifecycle.inspect(source_target),
                        destination=backup_target,
                    )
                )
            )
            restore_target = self.restored_target(
                root,
                LifecycleTargetKind.FILE_STORAGE,
            )
            self.lifecycle.execute(
                self.lifecycle.plan(
                    RestoreRequest(
                        backup=self.lifecycle.inspect(backup_target),
                        destination=restore_target,
                    )
                )
            )
            restored = Path(restore_target.path)

            self.assertEqual(
                (restored / unknown_lock.name).read_bytes(),
                b"user-owned lock-named data",
            )
            self.assertFalse((restored / runtime_lock.name).exists())

    def test_backup_bundle_rejects_unmanifested_empty_payload_directory(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            self.lifecycle.execute(
                self.lifecycle.plan(
                    BackupRequest(
                        source=self.lifecycle.inspect(source_target),
                        destination=backup_target,
                    )
                )
            )
            (Path(backup_target.path) / "payload" / "unexpected-empty").mkdir()

            with self.assertRaises(StorageIntegrityError):
                self.lifecycle.inspect(backup_target)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO files are not supported")
    def test_backup_bundle_rejects_unmanifested_fifo(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            self.lifecycle.execute(
                self.lifecycle.plan(
                    BackupRequest(
                        source=self.lifecycle.inspect(source_target),
                        destination=backup_target,
                    )
                )
            )
            fifo_path = Path(backup_target.path) / "payload" / "unexpected-pipe"
            os.mkfifo(fifo_path)

            with self.assertRaises(StorageIntegrityError):
                self.lifecycle.inspect(backup_target)

    def test_backup_bundle_rejects_unmanifested_link(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            self.lifecycle.execute(
                self.lifecycle.plan(
                    BackupRequest(
                        source=self.lifecycle.inspect(source_target),
                        destination=backup_target,
                    )
                )
            )
            link_path = Path(backup_target.path) / "payload" / "unexpected-link"
            try:
                link_path.symlink_to(Path(backup_target.path) / "manifest.json")
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            with self.assertRaises(StorageIntegrityError):
                self.lifecycle.inspect(backup_target)

    def test_restore_publication_race_does_not_overwrite_new_target(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            self.lifecycle.execute(
                self.lifecycle.plan(
                    BackupRequest(
                        source=self.lifecycle.inspect(source_target),
                        destination=backup_target,
                    )
                )
            )
            restore_target = self.restored_target(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            restore_plan = self.lifecycle.plan(
                RestoreRequest(
                    backup=self.lifecycle.inspect(backup_target),
                    destination=restore_target,
                )
            )
            destination = Path(restore_target.path)
            real_publish = lifecycle_module._rename_no_replace

            def race_publication(source, target):
                destination.write_bytes(b"created by another process")
                return real_publish(source, target)

            with mock.patch(
                "erii.data_lifecycle._rename_no_replace",
                side_effect=race_publication,
            ):
                with self.assertRaises(LifecycleConflictError):
                    DataLifecycleCoordinator().execute(
                        LifecyclePlan.from_json(restore_plan.to_json())
                    )

            self.assertEqual(destination.read_bytes(), b"created by another process")

    def test_destination_parent_replacement_invalidates_serialized_plan(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            destination_parent = root / "destination-parent"
            destination_parent.mkdir()
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                destination_parent / "snapshot.eriibak",
            )
            plan_json = self.lifecycle.plan(
                BackupRequest(
                    source=self.lifecycle.inspect(source_target),
                    destination=backup_target,
                )
            ).to_json()
            displaced_parent = root / "original-destination-parent"
            destination_parent.rename(displaced_parent)
            destination_parent.mkdir()

            with self.assertRaises(StaleLifecyclePlanError):
                DataLifecycleCoordinator().execute(LifecyclePlan.from_json(plan_json))

            self.assertFalse(Path(backup_target.path).exists())

    def test_failed_final_verification_preserves_published_target(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source_target, _ = self.create_source(
                root,
                LifecycleTargetKind.MEMORY_PACK,
            )
            backup_target = self.target(
                LifecycleTargetKind.BACKUP,
                root / "snapshot.eriibak",
            )
            plan = self.lifecycle.plan(
                BackupRequest(
                    source=self.lifecycle.inspect(source_target),
                    destination=backup_target,
                )
            )
            destination = Path(backup_target.path)
            real_read_bundle = lifecycle_module._read_backup_bundle

            def fail_only_after_publication(target):
                if target.path == backup_target.path and destination.exists():
                    host_file = destination / "created-after-publication.txt"
                    host_file.write_bytes(b"host-owned data")
                    raise StorageIntegrityError("injected final verification failure")
                return real_read_bundle(target)

            with mock.patch(
                "erii.data_lifecycle._read_backup_bundle",
                side_effect=fail_only_after_publication,
            ):
                with self.assertRaises(LifecycleVerificationError) as raised:
                    self.lifecycle.execute(plan)

            self.assertEqual(
                raised.exception.recovery_status,
                "published_target_preserved_manual_cleanup_required",
            )
            self.assertTrue(destination.exists())
            self.assertEqual(
                (destination / "created-after-publication.txt").read_bytes(),
                b"host-owned data",
            )


if __name__ == "__main__":
    unittest.main()
