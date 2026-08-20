"""Read-only format inspection contracts for the v0.4 stable source line."""

import ast
import copy
from contextlib import closing
import os
from pathlib import Path
import pickle
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import erii
import erii.data_lifecycle as lifecycle_module
import erii.lifecycle_streaming as legacy_streaming_module
import erii._lifecycle.filesystem as filesystem_module
import erii._lifecycle.sqlite_semantics as sqlite_semantics_module
from erii import FileStorage, MemoryPack, SQLiteStorage, UnsupportedFormatError
from erii._lifecycle.inspection import LifecycleInspector as InternalLifecycleInspector
from erii._lifecycle.snapshots import capture_snapshot, materialize_snapshot
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
FIXTURE_BACKUP = (
    Path(__file__).parent
    / "fixtures"
    / "lifecycle"
    / "backup-v0.4.0-file-storage-v1"
    / "backup"
)
PROJECT_ROOT = Path(__file__).parents[1]


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

        self.assertEqual(catalog.package_version, "0.5.0a3")
        self.assertEqual(catalog.sqlite.current_version, "11")
        self.assertEqual(catalog.file_storage.current_version, "2")
        self.assertEqual(catalog.memory_pack.current_version, "0.5.0a3")
        self.assertEqual(catalog.lifecycle_backup.current_version, "1")
        self.assertEqual(catalog.lifecycle_plan.current_version, "3")
        self.assertEqual(catalog.lifecycle_plan.readable_versions, ("1", "2", "3"))
        self.assertEqual(catalog.python_requires, ">=3.11")
        self.assertEqual(catalog.python_tested_through, "3.14")


class LifecycleInspectionArchitectureTests(unittest.TestCase):
    def test_read_modules_have_no_facade_or_function_local_project_imports(self) -> None:
        read_modules = (
            PROJECT_ROOT / "erii" / "_lifecycle" / "filesystem.py",
            PROJECT_ROOT / "erii" / "_lifecycle" / "inspection.py",
            PROJECT_ROOT / "erii" / "_lifecycle" / "snapshots.py",
            PROJECT_ROOT / "erii" / "_lifecycle" / "sqlite_semantics.py",
        )
        forbidden_prefixes = (
            "erii._lifecycle.planning",
            "erii.data_lifecycle",
            "erii.engine",
            "erii.lifecycle_erasure",
            "erii.lifecycle_memory_pack_import",
            "erii.lifecycle_sqlite_upgrade",
            "erii.lifecycle_streaming",
            "erii.storage",
        )
        violations: list[str] = []

        def imported_names(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
            if isinstance(node, ast.Import):
                return tuple(alias.name for alias in node.names)
            module = node.module or ""
            names = [module]
            if module == "erii":
                names.extend(f"erii.{alias.name}" for alias in node.names)
            return tuple(names)

        def dynamic_import_name(node: ast.Call) -> str | None:
            is_dynamic_import = (
                isinstance(node.func, ast.Name) and node.func.id == "__import__"
            ) or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
            )
            if not is_dynamic_import or not node.args:
                return None
            name = node.args[0]
            return name.value if isinstance(name, ast.Constant) and isinstance(name.value, str) else None

        for source_path in read_modules:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for imported in imported_names(node):
                        if imported.startswith(forbidden_prefixes):
                            violations.append(
                                f"{source_path.name}:{node.lineno}:{imported}"
                            )
                elif isinstance(node, ast.Call):
                    imported = dynamic_import_name(node)
                    if imported is not None and imported.startswith("erii"):
                        violations.append(
                            f"{source_path.name}:{node.lineno}:dynamic:{imported}"
                        )
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for nested in ast.walk(node):
                    if isinstance(nested, (ast.Import, ast.ImportFrom)):
                        for imported in imported_names(nested):
                            if imported.startswith("erii"):
                                violations.append(
                                    f"{source_path.name}:{nested.lineno}:local:"
                                    f"{imported}"
                                )

        for source_path in (PROJECT_ROOT / "erii" / "_lifecycle").glob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for imported in imported_names(node):
                        if imported.startswith("erii.data_lifecycle"):
                            violations.append(
                                f"{source_path.name}:{node.lineno}:{imported}"
                            )
                elif isinstance(node, ast.Call):
                    imported = dynamic_import_name(node)
                    if imported is not None and imported.startswith(
                        "erii.data_lifecycle"
                    ):
                        violations.append(
                            f"{source_path.name}:{node.lineno}:dynamic:{imported}"
                        )

        self.assertEqual(violations, [])

    def test_planning_seams_have_no_execution_or_facade_dependencies(self) -> None:
        planning_modules = (
            PROJECT_ROOT / "erii" / "_lifecycle" / "planning.py",
            PROJECT_ROOT / "erii" / "_lifecycle" / "upgrade_preview.py",
            PROJECT_ROOT / "erii" / "_lifecycle" / "erasure_inspection.py",
            PROJECT_ROOT / "erii" / "_lifecycle" / "memory_pack_validation.py",
            PROJECT_ROOT / "erii" / "_lifecycle" / "sqlite_image_upgrade.py",
        )
        forbidden_prefixes = (
            "erii.data_lifecycle",
            "erii.engine",
            "erii.lifecycle_erasure",
            "erii.lifecycle_memory_pack_import",
            "erii.lifecycle_sqlite_upgrade",
            "erii.lifecycle_streaming",
            "erii.storage",
        )
        violations: list[str] = []

        for source_path in planning_modules:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    imported = (module,)
                    if module == "erii":
                        imported += tuple(
                            f"erii.{alias.name}" for alias in node.names
                        )
                else:
                    imported = ()
                for name in imported:
                    if any(
                        name == prefix or name.startswith(f"{prefix}.")
                        for prefix in forbidden_prefixes
                    ):
                        violations.append(
                            f"{source_path.name}:{node.lineno}:{name}"
                        )
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                is_dynamic = (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "__import__"
                ) or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                )
                argument = node.args[0]
                if (
                    is_dynamic
                    and isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                    and argument.value.startswith("erii")
                ):
                    violations.append(
                        f"{source_path.name}:{node.lineno}:dynamic:{argument.value}"
                    )
            for function in (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ):
                for nested in ast.walk(function):
                    if isinstance(nested, ast.Import):
                        names = tuple(alias.name for alias in nested.names)
                    elif isinstance(nested, ast.ImportFrom):
                        names = (nested.module or "",)
                    else:
                        names = ()
                    for name in names:
                        if name.startswith("erii"):
                            violations.append(
                                f"{source_path.name}:{nested.lineno}:local:{name}"
                            )

        facade_tree = ast.parse(
            (PROJECT_ROOT / "erii" / "data_lifecycle.py").read_text(
                encoding="utf-8"
            )
        )
        facade_functions = {
            node.name
            for node in ast.walk(facade_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn("_make_plan", facade_functions)
        self.assertFalse(any(name.startswith("_plan_") for name in facade_functions))
        self.assertEqual(violations, [])

    def test_streaming_compatibility_module_reexports_filesystem_authority(self) -> None:
        for name in legacy_streaming_module.__all__:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(legacy_streaming_module, name),
                    getattr(filesystem_module, name),
                )
        for name in (
            "RegularFileIdentity",
            "RegularTreeManifest",
            "TreeFileEntry",
        ):
            with self.subTest(pickle=name):
                contract = getattr(filesystem_module, name)
                self.assertEqual(contract.__module__, "erii.lifecycle_streaming")
                self.assertIs(pickle.loads(pickle.dumps(contract)), contract)


class LifecycleInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inspector = LifecycleInspector()

    @staticmethod
    def target(kind: LifecycleTargetKind, path: Path) -> LifecycleTarget:
        return LifecycleTarget(kind=kind, path=str(path))

    def test_public_inspector_identity_module_and_pickle_are_frozen(self) -> None:
        self.assertIs(erii.LifecycleInspector, lifecycle_module.LifecycleInspector)
        self.assertIs(lifecycle_module.LifecycleInspector, InternalLifecycleInspector)
        self.assertIn("LifecycleInspector", erii.__all__)
        self.assertIn("LifecycleInspector", lifecycle_module.__all__)
        self.assertEqual(
            lifecycle_module.LifecycleInspector.__module__,
            "erii.data_lifecycle",
        )
        self.assertEqual(
            InternalLifecycleInspector.inspect.__module__,
            "erii._lifecycle.inspection",
        )
        self.assertIs(
            pickle.loads(pickle.dumps(lifecycle_module.LifecycleInspector)),
            lifecycle_module.LifecycleInspector,
        )
        self.assertIs(
            lifecycle_module.read_sqlite_schema_version,
            sqlite_semantics_module.read_sqlite_schema_version,
        )

    def test_missing_target_is_reported_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            for kind in LifecycleTargetKind:
                with self.subTest(kind=kind.value):
                    missing = Path(root_dir) / f"does-not-exist-{kind.value}"

                    assessment = self.inspector.inspect(self.target(kind, missing))

                    self.assertEqual(assessment.status, LifecycleStatus.MISSING)
                    self.assertIsNone(assessment.fingerprint)
                    self.assertFalse(missing.exists())

    def test_empty_backup_and_invalid_empty_memory_pack_remain_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            backup = root / "empty.eriibak"
            backup.mkdir()
            backup_mtime = backup.stat().st_mtime_ns

            assessment = self.inspector.inspect(
                self.target(LifecycleTargetKind.BACKUP, backup)
            )

            self.assertEqual(assessment.status, LifecycleStatus.EMPTY)
            self.assertEqual(assessment.file_count, 0)
            self.assertEqual(tuple(backup.iterdir()), ())
            self.assertEqual(backup.stat().st_mtime_ns, backup_mtime)

            pack = root / "empty.erii"
            pack.write_bytes(b"")
            before = file_snapshot(root)
            with self.assertRaises(StorageIntegrityError):
                self.inspector.inspect(
                    self.target(LifecycleTargetKind.MEMORY_PACK, pack)
                )
            self.assertEqual(file_snapshot(root), before)

    def test_live_inspection_and_capture_reject_hard_linked_payloads(self) -> None:
        for kind in (
            LifecycleTargetKind.FILE_STORAGE,
            LifecycleTargetKind.SQLITE,
            LifecycleTargetKind.MEMORY_PACK,
        ):
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root_dir:
                root = Path(root_dir)
                if kind is LifecycleTargetKind.FILE_STORAGE:
                    target_path = root / "store"
                    target_path.mkdir()
                    payload_path = target_path / ".erii-store.json"
                    payload_path.write_text(
                        '{"format":"erii.file-storage","version":2}',
                        encoding="utf-8",
                    )
                elif kind is LifecycleTargetKind.SQLITE:
                    target_path = root / "current.db"
                    storage = SQLiteStorage(str(target_path))
                    storage.save_core_memory(
                        AGENT_ID,
                        USER_ID,
                        "A stable persona source.",
                    )
                    payload_path = target_path
                else:
                    target_path = root / "portable.erii"
                    target_path.write_text(
                        MemoryPack(
                            agent_id=AGENT_ID,
                            user_id=USER_ID,
                            core_memory="A stable persona source.",
                        ).to_json(),
                        encoding="utf-8",
                    )
                    payload_path = target_path

                target = self.target(kind, target_path)
                assessment = self.inspector.inspect(target)
                try:
                    os.link(payload_path, root / f"hard-link-{kind.value}")
                except OSError as exc:
                    self.skipTest(f"hard links are unavailable: {exc}")

                with self.assertRaisesRegex(StorageIntegrityError, "link|hard"):
                    self.inspector.inspect(target)
                with self.assertRaisesRegex(StorageIntegrityError, "link|hard"):
                    capture_snapshot(assessment)

    def test_streamed_and_materialized_snapshots_are_defensively_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            pack_path = Path(root_dir) / "portable.erii"
            pack_path.write_text(
                MemoryPack(
                    agent_id=AGENT_ID,
                    user_id=USER_ID,
                    core_memory="A stable persona source.",
                ).to_json(),
                encoding="utf-8",
            )
            assessment = self.inspector.inspect(
                self.target(LifecycleTargetKind.MEMORY_PACK, pack_path)
            )
            streamed = capture_snapshot(assessment)
            assert streamed.source_paths is not None
            with self.assertRaises(TypeError):
                streamed.source_paths["injected"] = str(pack_path)  # type: ignore[index]

            materialized = materialize_snapshot(streamed)
            assert materialized.files is not None
            with self.assertRaises(TypeError):
                materialized.files["memory-pack.erii"] = b"mutated"  # type: ignore[index]

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
            self.assertEqual(assessment.detected_version, "11")
            self.assertEqual(file_snapshot(Path(root_dir)), before)

            future_path = Path(root_dir) / "future.db"
            with closing(sqlite3.connect(future_path)) as connection:
                connection.execute(
                    "CREATE TABLE schema_migrations ("
                    "version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
                )
                connection.execute(
                    "INSERT INTO schema_migrations VALUES (12, 'future', 'future')"
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

    def test_sqlite_storage_uses_the_leaf_schema_reader(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            database = Path(root_dir) / "storage.db"
            real_reader = sqlite_semantics_module.read_sqlite_schema_version
            with mock.patch.object(
                sqlite_semantics_module,
                "read_sqlite_schema_version",
                wraps=real_reader,
            ) as reader:
                SQLiteStorage(str(database))

            self.assertGreaterEqual(reader.call_count, 1)
            reader.assert_any_call(str(database), immutable=False)

    def test_verified_backup_scan_is_stable_and_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            backup_path = Path(root_dir) / "historical.eriibak"
            shutil.copytree(FIXTURE_BACKUP, backup_path)
            target = self.target(LifecycleTargetKind.BACKUP, backup_path)
            before = file_snapshot(backup_path)
            before_entries = (
                (".", True, backup_path.stat().st_mtime_ns),
                *(
                    (
                        path.relative_to(backup_path).as_posix(),
                        path.is_dir(),
                        path.stat().st_mtime_ns,
                    )
                    for path in sorted(backup_path.rglob("*"))
                ),
            )

            internal = InternalLifecycleInspector().inspect(target)
            public = self.inspector.inspect(target)

            self.assertEqual(internal, public)
            self.assertEqual(internal.status, LifecycleStatus.CURRENT)
            self.assertEqual(internal.detected_version, "1")
            self.assertEqual(internal.file_count, len(before))
            self.assertEqual(file_snapshot(backup_path), before)
            self.assertEqual(
                (
                    (".", True, backup_path.stat().st_mtime_ns),
                    *(
                        (
                            path.relative_to(backup_path).as_posix(),
                            path.is_dir(),
                            path.stat().st_mtime_ns,
                        )
                        for path in sorted(backup_path.rglob("*"))
                    ),
                ),
                before_entries,
            )

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
            self.assertEqual(assessment.detected_version, "0.5.0a3")
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
