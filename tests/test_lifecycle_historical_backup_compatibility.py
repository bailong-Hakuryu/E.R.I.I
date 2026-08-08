"""Frozen compatibility contracts for Backup v1 producer catalog identities."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from erii import (
    BackupRequest,
    DataLifecycleCoordinator,
    LifecycleContentIdentity,
    LifecycleOutcome,
    LifecyclePlanError,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    RestoreRequest,
    StorageIntegrityError,
    UnsupportedFormatError,
)
from erii.lifecycle_sqlite_upgrade import _migrate_sqlite_staging_copy


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "lifecycle"
HISTORICAL_BACKUP_ROOT = FIXTURE_ROOT / "backup-v0.4.0-file-storage-v1"
FILE_STORAGE_SOURCE = FIXTURE_ROOT / "file-storage-v0.3.1" / "source"
SQLITE_SCHEMA6_SOURCE = (
    FIXTURE_ROOT / "sqlite-v0.4.0a7" / "schema6.sqlite3"
)
MEMORY_PACK_A7_SOURCE = (
    FIXTURE_ROOT / "memory-pack-v0.4.0a7" / "source.erii"
)
PRODUCER_COMMIT = "f6dca322379c4ea88320c69d752cab471d035e95"
FILE_STORAGE_V1_MANIFEST = b'{"format":"erii.file-storage","version":1}'


def _target(kind: LifecycleTargetKind, path: Path) -> LifecycleTarget:
    return LifecycleTarget(kind, str(path))


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _canonical_json(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _rewrite_backup_source_view(
    backup_path: Path,
    *,
    current_version: str,
    status: str,
    detected_version: str | None = None,
    replace_detected_version: bool = False,
) -> None:
    manifest_path = backup_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["source"]["current_version"] = current_version
    manifest["source"]["status"] = status
    if replace_detected_version:
        manifest["source"]["detected_version"] = detected_version
    manifest_path.write_bytes(_canonical_json(manifest))


def _copy_v10_as_schema9(source: Path, target: Path) -> None:
    """Builds a schema-9 source from a private current staging artifact."""
    shutil.copyfile(source, target)
    with closing(sqlite3.connect(target)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP TABLE narrative_tension_links")
        connection.execute("DROP TABLE relationship_consequences")
        connection.execute("DELETE FROM schema_migrations WHERE version = 10")
        connection.commit()


class HistoricalBackupCompatibilityTests(unittest.TestCase):
    def _create_source(
        self,
        root: Path,
        case: str,
    ) -> tuple[LifecycleTarget, Path]:
        if case in {"file-storage-legacy", "file-storage-v1"}:
            path = root / case
            shutil.copytree(FILE_STORAGE_SOURCE, path)
            if case == "file-storage-v1":
                (path / ".erii-store.json").write_bytes(
                    FILE_STORAGE_V1_MANIFEST
                )
            return _target(LifecycleTargetKind.FILE_STORAGE, path), path
        if case == "file-storage-empty":
            path = root / case
            path.mkdir()
            return _target(LifecycleTargetKind.FILE_STORAGE, path), path
        if case == "sqlite-6":
            path = root / "schema6.sqlite3"
            shutil.copyfile(SQLITE_SCHEMA6_SOURCE, path)
            return _target(LifecycleTargetKind.SQLITE, path), path
        if case == "sqlite-9":
            current_path = root / "private-current.sqlite3"
            _migrate_sqlite_staging_copy(SQLITE_SCHEMA6_SOURCE, current_path)
            path = root / "schema9.sqlite3"
            _copy_v10_as_schema9(current_path, path)
            return _target(LifecycleTargetKind.SQLITE, path), path
        if case == "sqlite-empty":
            path = root / "empty.sqlite3"
            path.touch()
            return _target(LifecycleTargetKind.SQLITE, path), path
        if case == "memory-pack-a7":
            path = root / "pack-a7.erii"
            shutil.copyfile(MEMORY_PACK_A7_SOURCE, path)
            return _target(LifecycleTargetKind.MEMORY_PACK, path), path
        if case == "memory-pack-a8":
            path = root / "pack-a8.erii"
            document = json.loads(MEMORY_PACK_A7_SOURCE.read_text("utf-8"))
            document["metadata"]["version"] = "0.4.0a8"
            path.write_text(
                json.dumps(document, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return _target(LifecycleTargetKind.MEMORY_PACK, path), path
        raise AssertionError(f"unknown historical source case {case!r}")

    @staticmethod
    def _source_bytes(kind: LifecycleTargetKind, path: Path) -> object:
        if kind is LifecycleTargetKind.FILE_STORAGE:
            return _tree_bytes(path)
        return path.read_bytes()

    def test_frozen_v040_file_storage_backup_restores_with_current_catalog(
        self,
    ) -> None:
        metadata = json.loads(
            (HISTORICAL_BACKUP_ROOT / "fixture.json").read_text("utf-8")
        )
        fixture_files = {
            item["path"]: item for item in metadata["backup_files"]
        }
        actual_files = {
            path.relative_to(HISTORICAL_BACKUP_ROOT).as_posix(): path
            for path in sorted(HISTORICAL_BACKUP_ROOT.rglob("*"))
            if path.is_file() and path.name != "fixture.json"
        }

        self.assertEqual(metadata["fixture_contract"], "1")
        self.assertEqual(metadata["storage_kind"], "lifecycle_backup")
        self.assertEqual(metadata["producer"]["package_version"], "0.4.0b1")
        self.assertEqual(metadata["producer"]["commit"], PRODUCER_COMMIT)
        self.assertEqual(
            metadata["producer"]["interface"],
            "erii.DataLifecycleCoordinator.execute(BackupRequest)",
        )
        self.assertEqual(metadata["data_classification"], "synthetic_non_user_data")
        self.assertEqual(set(actual_files), set(fixture_files))
        for relative_name, path in actual_files.items():
            with self.subTest(path=relative_name):
                self.assertEqual(path.stat().st_size, fixture_files[relative_name]["size"])
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    fixture_files[relative_name]["sha256"],
                )

        raw_manifest = json.loads(
            (HISTORICAL_BACKUP_ROOT / "backup" / "manifest.json").read_text(
                "utf-8"
            )
        )
        self.assertEqual(raw_manifest["source"], metadata["source_identity"])

        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            backup_path = root / "historical-v1.eriibak"
            shutil.copytree(HISTORICAL_BACKUP_ROOT / "backup", backup_path)
            lifecycle = DataLifecycleCoordinator()
            backup = lifecycle.inspect(
                _target(LifecycleTargetKind.BACKUP, backup_path)
            )
            restored_target = _target(
                LifecycleTargetKind.FILE_STORAGE,
                root / "restored-v1-store",
            )

            report = lifecycle.execute(
                lifecycle.plan(
                    RestoreRequest(
                        backup=backup,
                        destination=restored_target,
                    )
                )
            )

            self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
            restored = lifecycle.inspect(restored_target)
            self.assertEqual(restored.status, LifecycleStatus.MIGRATION_REQUIRED)
            self.assertEqual(restored.detected_version, "1")
            self.assertEqual(restored.current_version, "2")
            self.assertEqual(
                _tree_bytes(Path(restored_target.path)),
                _tree_bytes(backup_path / "payload"),
            )

    def test_v040_current_migration_and_empty_views_restore_byte_exactly(
        self,
    ) -> None:
        cases = (
            (
                "file-storage-legacy",
                LifecycleTargetKind.FILE_STORAGE,
                "legacy",
                "1",
                "migration_required",
                "2",
                LifecycleStatus.MIGRATION_REQUIRED,
            ),
            (
                "file-storage-v1",
                LifecycleTargetKind.FILE_STORAGE,
                "1",
                "1",
                "current",
                "2",
                LifecycleStatus.MIGRATION_REQUIRED,
            ),
            (
                "file-storage-empty",
                LifecycleTargetKind.FILE_STORAGE,
                None,
                "1",
                "empty",
                "2",
                LifecycleStatus.EMPTY,
            ),
            (
                "sqlite-6",
                LifecycleTargetKind.SQLITE,
                "6",
                "9",
                "migration_required",
                "10",
                LifecycleStatus.MIGRATION_REQUIRED,
            ),
            (
                "sqlite-9",
                LifecycleTargetKind.SQLITE,
                "9",
                "9",
                "current",
                "10",
                LifecycleStatus.MIGRATION_REQUIRED,
            ),
            (
                "sqlite-empty",
                LifecycleTargetKind.SQLITE,
                None,
                "9",
                "empty",
                "10",
                LifecycleStatus.EMPTY,
            ),
            (
                "memory-pack-a7",
                LifecycleTargetKind.MEMORY_PACK,
                "0.4.0a7",
                "0.4.0a8",
                "migration_required",
                "0.5.0a1",
                LifecycleStatus.MIGRATION_REQUIRED,
            ),
            (
                "memory-pack-a8",
                LifecycleTargetKind.MEMORY_PACK,
                "0.4.0a8",
                "0.4.0a8",
                "current",
                "0.5.0a1",
                LifecycleStatus.MIGRATION_REQUIRED,
            ),
        )
        for (
            case,
            kind,
            detected_version,
            producer_current,
            producer_status,
            reader_current,
            reader_status,
        ) in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                lifecycle = DataLifecycleCoordinator()
                source_target, source_path = self._create_source(root, case)
                source_bytes = self._source_bytes(kind, source_path)
                source = lifecycle.inspect(source_target)
                backup_target = _target(
                    LifecycleTargetKind.BACKUP,
                    root / "historical.eriibak",
                )
                lifecycle.execute(
                    lifecycle.plan(
                        BackupRequest(
                            source=source,
                            destination=backup_target,
                        )
                    )
                )
                _rewrite_backup_source_view(
                    Path(backup_target.path),
                    current_version=producer_current,
                    status=producer_status,
                )

                raw_manifest = json.loads(
                    (Path(backup_target.path) / "manifest.json").read_text("utf-8")
                )
                self.assertEqual(
                    raw_manifest["source"]["current_version"], producer_current
                )
                self.assertEqual(raw_manifest["source"]["status"], producer_status)
                self.assertEqual(
                    raw_manifest["source"]["detected_version"], detected_version
                )

                backup = lifecycle.inspect(backup_target)
                restored_path = root / (
                    "restored-store"
                    if kind is LifecycleTargetKind.FILE_STORAGE
                    else "restored.sqlite3"
                    if kind is LifecycleTargetKind.SQLITE
                    else "restored.erii"
                )
                restored_target = _target(kind, restored_path)
                report = lifecycle.execute(
                    lifecycle.plan(
                        RestoreRequest(
                            backup=backup,
                            destination=restored_target,
                        )
                    )
                )

                self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
                restored = lifecycle.inspect(restored_target)
                self.assertEqual(restored.status, reader_status)
                self.assertEqual(restored.detected_version, detected_version)
                self.assertEqual(restored.current_version, reader_current)
                self.assertEqual(
                    self._source_bytes(kind, restored_path),
                    source_bytes,
                )

    def test_unknown_or_mismatched_historical_producer_views_are_rejected(
        self,
    ) -> None:
        variants = (
            (
                "file-storage-unknown-current",
                "file-storage-v1",
                "0",
                "migration_required",
                "1",
                False,
            ),
            (
                "file-storage-status-mismatch",
                "file-storage-v1",
                "1",
                "migration_required",
                "1",
                False,
            ),
            (
                "file-storage-future-detected",
                "file-storage-v1",
                "1",
                "migration_required",
                "2",
                True,
            ),
            (
                "sqlite-status-mismatch",
                "sqlite-6",
                "9",
                "current",
                "6",
                False,
            ),
            (
                "sqlite-future-detected",
                "sqlite-6",
                "9",
                "migration_required",
                "10",
                True,
            ),
            (
                "memory-pack-status-mismatch",
                "memory-pack-a7",
                "0.4.0a8",
                "current",
                "0.4.0a7",
                False,
            ),
            (
                "memory-pack-future-detected",
                "memory-pack-a7",
                "0.4.0a8",
                "migration_required",
                "0.5.0a1",
                True,
            ),
        )
        for name, source_case, current, status, detected, unsupported in variants:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                lifecycle = DataLifecycleCoordinator()
                source_target, _ = self._create_source(root, source_case)
                backup_target = _target(
                    LifecycleTargetKind.BACKUP,
                    root / "historical.eriibak",
                )
                lifecycle.execute(
                    lifecycle.plan(
                        BackupRequest(
                            source=lifecycle.inspect(source_target),
                            destination=backup_target,
                        )
                    )
                )
                _rewrite_backup_source_view(
                    Path(backup_target.path),
                    current_version=current,
                    status=status,
                    detected_version=detected,
                    replace_detected_version=unsupported,
                )

                expected_error = (
                    UnsupportedFormatError if unsupported else StorageIntegrityError
                )
                with self.assertRaises(expected_error):
                    lifecycle.inspect(backup_target)

    def test_historical_producer_identity_is_not_accepted_as_live_plan_content(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            LifecyclePlanError,
            "current version is invalid",
        ):
            LifecycleContentIdentity(
                kind=LifecycleTargetKind.FILE_STORAGE,
                status=LifecycleStatus.CURRENT,
                format_id="erii.file-storage",
                detected_version="1",
                current_version="1",
                fingerprint="0" * 64,
                file_count=4,
            )


if __name__ == "__main__":
    unittest.main()
