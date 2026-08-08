"""Read-only format inspection contracts for the v0.4 stable source line."""

import copy
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from erii import FileStorage, MemoryPack, SQLiteStorage, UnsupportedFormatError
from erii.compatibility import COMPATIBILITY_CATALOG
from erii.data_lifecycle import (
    LifecycleInspector,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
)
from erii.errors import StorageIntegrityError


AGENT_ID = "agent_lumi"
USER_ID = "user_chen"


def file_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Captures target bytes and mtimes without following external links."""
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class CompatibilityCatalogTests(unittest.TestCase):
    def test_package_and_data_format_versions_are_independent(self) -> None:
        catalog = COMPATIBILITY_CATALOG

        self.assertEqual(catalog.package_version, "0.5.0a1")
        self.assertEqual(catalog.sqlite.current_version, "10")
        self.assertEqual(catalog.file_storage.current_version, "2")
        self.assertEqual(catalog.memory_pack.current_version, "0.5.0a1")
        self.assertEqual(catalog.lifecycle_backup.current_version, "1")
        self.assertEqual(catalog.lifecycle_plan.current_version, "3")
        self.assertEqual(catalog.lifecycle_plan.readable_versions, ("1", "2", "3"))
        self.assertEqual(catalog.python_requires, ">=3.11")
        self.assertEqual(catalog.python_tested_through, "3.14")


class LifecycleInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inspector = LifecycleInspector()

    @staticmethod
    def target(kind: LifecycleTargetKind, path: Path) -> LifecycleTarget:
        return LifecycleTarget(kind=kind, path=str(path))

    def test_missing_target_is_reported_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            for kind in LifecycleTargetKind:
                with self.subTest(kind=kind.value):
                    missing = Path(root_dir) / f"does-not-exist-{kind.value}"

                    assessment = self.inspector.inspect(self.target(kind, missing))

                    self.assertEqual(assessment.status, LifecycleStatus.MISSING)
                    self.assertIsNone(assessment.fingerprint)
                    self.assertFalse(missing.exists())

    def test_file_storage_scan_is_complete_stable_and_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir) / "store"
            storage = FileStorage(str(root))
            storage.save_core_memory(AGENT_ID, USER_ID, "A stable persona source.")
            storage.add_timeline_entry(AGENT_ID, USER_ID, "A shared event.")
            before = file_snapshot(root)

            first = self.inspector.inspect(
                self.target(LifecycleTargetKind.FILE_STORAGE, root)
            )
            second = self.inspector.inspect(
                self.target(LifecycleTargetKind.FILE_STORAGE, root)
            )

            self.assertEqual(first.status, LifecycleStatus.MIGRATION_REQUIRED)
            self.assertEqual(first.detected_version, "legacy")
            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertEqual(file_snapshot(root), before)

            damaged = root / "_relationship_events" / "damaged.json"
            damaged.parent.mkdir()
            damaged.write_text('{"truncated": ', encoding="utf-8")
            with self.assertRaises(StorageIntegrityError):
                self.inspector.inspect(
                    self.target(LifecycleTargetKind.FILE_STORAGE, root)
                )

    def test_current_file_manifest_is_read_but_never_written_by_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir) / "store"
            root.mkdir()
            manifest = root / ".erii-store.json"
            manifest.write_text(
                '{"format":"erii.file-storage","version":2}',
                encoding="utf-8",
            )
            before = file_snapshot(root)

            assessment = self.inspector.inspect(
                self.target(LifecycleTargetKind.FILE_STORAGE, root)
            )

            self.assertEqual(assessment.status, LifecycleStatus.CURRENT)
            self.assertEqual(assessment.detected_version, "2")
            self.assertEqual(file_snapshot(root), before)

    def test_sqlite_inspection_is_immutable_and_future_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            current_path = Path(root_dir) / "current.db"
            storage = SQLiteStorage(str(current_path))
            storage.save_core_memory(AGENT_ID, USER_ID, "A stable persona source.")
            before = file_snapshot(Path(root_dir))

            assessment = self.inspector.inspect(
                self.target(LifecycleTargetKind.SQLITE, current_path)
            )

            self.assertEqual(assessment.status, LifecycleStatus.CURRENT)
            self.assertEqual(assessment.detected_version, "10")
            self.assertEqual(file_snapshot(Path(root_dir)), before)

            future_path = Path(root_dir) / "future.db"
            with closing(sqlite3.connect(future_path)) as connection:
                connection.execute(
                    "CREATE TABLE schema_migrations ("
                    "version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
                )
                connection.execute(
                    "INSERT INTO schema_migrations VALUES (11, 'future', 'future')"
                )
                connection.commit()
            future_before = future_path.read_bytes()

            with self.assertRaises(UnsupportedFormatError):
                self.inspector.inspect(
                    self.target(LifecycleTargetKind.SQLITE, future_path)
                )
            with self.assertRaises(UnsupportedFormatError):
                SQLiteStorage(str(future_path))

            self.assertEqual(future_path.read_bytes(), future_before)

    def test_memory_pack_envelope_is_strict_and_inspected_before_models(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            pack_path = Path(root_dir) / "portable.erii"
            payload = MemoryPack(
                agent_id=AGENT_ID,
                user_id=USER_ID,
                core_memory="A stable persona source.",
            ).to_dict()
            pack_path.write_text(
                MemoryPack.from_dict(payload).to_json(),
                encoding="utf-8",
            )
            before = file_snapshot(Path(root_dir))

            assessment = self.inspector.inspect(
                self.target(LifecycleTargetKind.MEMORY_PACK, pack_path)
            )

            self.assertEqual(assessment.status, LifecycleStatus.CURRENT)
            self.assertEqual(assessment.detected_version, "0.5.0a1")
            self.assertEqual(file_snapshot(Path(root_dir)), before)

            missing_metadata = copy.deepcopy(payload)
            missing_metadata.pop("metadata")
            with self.assertRaisesRegex(ValueError, "metadata"):
                MemoryPack.from_dict(missing_metadata)

            unknown_root = copy.deepcopy(payload)
            unknown_root["future_authority"] = True
            with self.assertRaisesRegex(ValueError, "unknown"):
                MemoryPack.from_dict(unknown_root)

            unknown_metadata = copy.deepcopy(payload)
            unknown_metadata["metadata"]["future_authority"] = True
            with self.assertRaisesRegex(ValueError, "metadata"):
                MemoryPack.from_dict(unknown_metadata)

            future = copy.deepcopy(payload)
            future["metadata"]["version"] = "0.4.0a99"
            future["nodes"] = [{"invalid": "must not be constructed"}]
            with self.assertRaises(UnsupportedFormatError):
                MemoryPack.from_dict(future)


if __name__ == "__main__":
    unittest.main()
