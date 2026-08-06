"""Public coordinator proof for source-preserving SQLite upgrades."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import erii.data_lifecycle as lifecycle_module
from erii import (
    DataLifecycleCoordinator,
    LifecycleOutcome,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    RestoreRequest,
    SQLiteStorage,
    StorageWriteError,
    UpgradeRequest,
)
from erii.lifecycle_sqlite_upgrade import _semantic_digest_from_path


FIXTURE_DATABASE = (
    Path(__file__).parent
    / "fixtures"
    / "lifecycle"
    / "sqlite-v0.4.0a7"
    / "schema6.sqlite3"
)


class LifecycleSQLiteCoordinatorTests(unittest.TestCase):
    @staticmethod
    def _target(kind: LifecycleTargetKind, path: Path) -> LifecycleTarget:
        return LifecycleTarget(kind, str(path))

    def _request(
        self,
        root: Path,
    ) -> tuple[
        DataLifecycleCoordinator,
        UpgradeRequest,
        Path,
        LifecycleTarget,
        LifecycleTarget,
    ]:
        source_path = root / "source-schema6.sqlite3"
        shutil.copyfile(FIXTURE_DATABASE, source_path)
        lifecycle = DataLifecycleCoordinator()
        source = lifecycle.inspect(
            self._target(LifecycleTargetKind.SQLITE, source_path)
        )
        destination = self._target(
            LifecycleTargetKind.SQLITE,
            root / "upgraded-schema9.sqlite3",
        )
        backup = self._target(
            LifecycleTargetKind.BACKUP,
            root / "source-schema6.eriibak",
        )
        return (
            lifecycle,
            UpgradeRequest(source, destination, backup),
            source_path,
            destination,
            backup,
        )

    def test_plan_is_repeatable_zero_write_and_binds_semantic_result(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            lifecycle, request, source_path, destination, backup = self._request(root)
            source_bytes = source_path.read_bytes()

            first = lifecycle.plan(request)
            second = lifecycle.plan(request)

            self.assertEqual(first, second)
            self.assertEqual(first.strategy_id, "sqlite-schema-6-to-10")
            self.assertEqual(first.source.status, LifecycleStatus.MIGRATION_REQUIRED)
            self.assertEqual(first.source.detected_version, "6")
            self.assertEqual(first.content.status, LifecycleStatus.CURRENT)
            self.assertEqual(first.content.detected_version, "10")
            self.assertEqual(first.content.fingerprint, second.content.fingerprint)
            self.assertEqual(source_path.read_bytes(), source_bytes)
            self.assertFalse(Path(destination.path).exists())
            self.assertFalse(Path(backup.path).exists())

    def test_execute_preserves_source_and_publishes_backup_before_schema9(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            lifecycle, request, source_path, destination, backup = self._request(root)
            source_bytes = source_path.read_bytes()
            plan = lifecycle.plan(request)

            report = lifecycle.execute(plan)

            self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
            self.assertEqual(source_path.read_bytes(), source_bytes)
            self.assertEqual(
                _semantic_digest_from_path(Path(destination.path)),
                plan.content.fingerprint,
            )
            current = SQLiteStorage(destination.path)
            self.assertEqual(current.schema_version, 9)
            with closing(sqlite3.connect(destination.path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM relationships").fetchone()[0],
                    2,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM source_turns").fetchone()[0],
                    2,
                )

            backup_assessment = lifecycle.inspect(backup)
            restored = self._target(
                LifecycleTargetKind.SQLITE,
                root / "restored-schema6.sqlite3",
            )
            lifecycle.execute(
                lifecycle.plan(
                    RestoreRequest(backup=backup_assessment, destination=restored)
                )
            )
            self.assertEqual(Path(restored.path).read_bytes(), source_bytes)

            retried = DataLifecycleCoordinator().execute(plan)
            self.assertEqual(retried.outcome, LifecycleOutcome.ALREADY_COMPLETE)

    def test_target_publish_failure_keeps_verified_backup_for_exact_retry(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            lifecycle, request, source_path, destination, backup = self._request(root)
            source_bytes = source_path.read_bytes()
            plan = lifecycle.plan(request)
            real_publish = lifecycle_module._rename_no_replace

            def fail_target_only(staging: Path, target: Path) -> None:
                if Path(target) == Path(destination.path):
                    raise OSError("injected SQLite target publication failure")
                real_publish(staging, target)

            with mock.patch.object(
                lifecycle_module,
                "_rename_no_replace",
                side_effect=fail_target_only,
            ):
                with self.assertRaises(StorageWriteError):
                    lifecycle.execute(plan)

            self.assertEqual(source_path.read_bytes(), source_bytes)
            self.assertFalse(Path(destination.path).exists())
            backup_before_retry = {
                item.relative_to(Path(backup.path)).as_posix(): item.read_bytes()
                for item in Path(backup.path).rglob("*")
                if item.is_file()
            }
            self.assertTrue(backup_before_retry)

            report = DataLifecycleCoordinator().execute(plan)

            self.assertEqual(report.outcome, LifecycleOutcome.APPLIED)
            self.assertEqual(
                {
                    item.relative_to(Path(backup.path)).as_posix(): item.read_bytes()
                    for item in Path(backup.path).rglob("*")
                    if item.is_file()
                },
                backup_before_retry,
            )
            self.assertEqual(SQLiteStorage(destination.path).schema_version, 9)


if __name__ == "__main__":
    unittest.main()
