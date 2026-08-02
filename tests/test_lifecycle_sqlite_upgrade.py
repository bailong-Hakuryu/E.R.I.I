"""Regression proof for explicit, source-preserving SQLite schema upgrades."""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
import uuid

from erii import MigrationRequiredError, SQLiteStorage
from erii.errors import LifecycleConflictError, LifecyclePlanError
from erii.lifecycle_sqlite_upgrade import (
    _migrate_sqlite_staging_copy,
    _preview_sqlite_staging_upgrade,
    _semantic_digest_from_path,
)


FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "lifecycle" / "sqlite-v0.4.0a7"
)
FIXTURE_DATABASE = FIXTURE_ROOT / "schema6.sqlite3"
FIXTURE_METADATA = FIXTURE_ROOT / "fixture.json"


def _schema_version(path: Path) -> int:
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        return int(
            connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
        )


class SQLiteHistoricalFixtureTests(unittest.TestCase):
    def test_a7_fixture_has_frozen_provenance_and_rich_synthetic_scopes(self) -> None:
        metadata = json.loads(FIXTURE_METADATA.read_text(encoding="utf-8"))
        content = FIXTURE_DATABASE.read_bytes()

        self.assertEqual(metadata["producer"]["package_version"], "0.4.0a7")
        self.assertEqual(
            metadata["producer"]["commit"],
            "9693e6a52f6179789c16debf831f95674a1861bd",
        )
        self.assertEqual(metadata["producer"]["schema_version"], 6)
        self.assertTrue(metadata["data_provenance"]["kind"] == "synthetic")
        self.assertFalse(metadata["data_provenance"]["contains_user_data"])
        self.assertFalse(
            metadata["data_provenance"]["contains_copyrighted_character_content"]
        )
        self.assertEqual(len(content), metadata["database"]["size"])
        self.assertEqual(
            hashlib.sha256(content).hexdigest(),
            metadata["database"]["sha256"],
        )
        self.assertEqual(_schema_version(FIXTURE_DATABASE), 6)
        self.assertEqual(
            _semantic_digest_from_path(FIXTURE_DATABASE),
            metadata["database"]["semantic_digest"],
        )

        uri = f"{FIXTURE_DATABASE.resolve().as_uri()}?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            relationships = connection.execute(
                "SELECT agent_id, user_id FROM relationships ORDER BY relationship_id"
            ).fetchall()
            turns = connection.execute(
                "SELECT relationship_id, turn_id FROM source_turns "
                "ORDER BY relationship_id"
            ).fetchall()
            events = connection.execute(
                "SELECT relationship_id, event_id FROM relationship_events "
                "ORDER BY relationship_id"
            ).fetchall()
            timeline = connection.execute(
                "SELECT content, timestamp FROM timeline_entries ORDER BY id"
            ).fetchall()
        self.assertEqual(len(relationships), 2)
        self.assertEqual(len({tuple(row) for row in relationships}), 2)
        self.assertEqual(len(turns), 2)
        self.assertEqual(len(events), 2)
        self.assertIn("雪晶", timeline[0][0])
        self.assertIn("+08:00", timeline[0][1])
        self.assertIn("-05:00", timeline[2][1])


class SQLiteStorageMigrationBoundaryTests(unittest.TestCase):
    def test_new_database_initializes_at_v9_and_current_database_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            path = Path(root_dir) / "new.sqlite3"

            created = SQLiteStorage(str(path))
            self.assertEqual(created.schema_version, 9)
            reopened = SQLiteStorage(str(path))
            self.assertEqual(reopened.schema_version, 9)

    def test_opening_schema6_fails_closed_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            path = Path(root_dir) / "historical.sqlite3"
            shutil.copyfile(FIXTURE_DATABASE, path)
            before = path.read_bytes()

            with self.assertRaisesRegex(MigrationRequiredError, "schema 6"):
                SQLiteStorage(str(path))

            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(Path(f"{path}-wal").exists())
            self.assertFalse(Path(f"{path}-journal").exists())

    def test_existing_empty_file_also_requires_explicit_lifecycle_handling(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            path = Path(root_dir) / "empty.sqlite3"
            path.touch()

            with self.assertRaisesRegex(MigrationRequiredError, "schema 0"):
                SQLiteStorage(str(path))

            self.assertEqual(path.read_bytes(), b"")


class SQLiteStagingUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata = json.loads(FIXTURE_METADATA.read_text(encoding="utf-8"))

    def test_preview_is_zero_write_and_repeatably_identifies_v9(self) -> None:
        before = {
            item.name: (item.read_bytes(), item.stat().st_mtime_ns)
            for item in FIXTURE_ROOT.iterdir()
            if item.is_file()
        }

        first = _preview_sqlite_staging_upgrade(FIXTURE_DATABASE)
        second = _preview_sqlite_staging_upgrade(FIXTURE_DATABASE)

        after = {
            item.name: (item.read_bytes(), item.stat().st_mtime_ns)
            for item in FIXTURE_ROOT.iterdir()
            if item.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(first, second)
        self.assertEqual(first.source_version, 6)
        self.assertEqual(first.target_version, 9)
        self.assertEqual(
            first.semantic_digest,
            self.metadata["expected_upgrade"]["semantic_digest"],
        )

    def test_migration_preserves_source_and_all_historical_rows(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            source = Path(root_dir) / "source.sqlite3"
            target = Path(root_dir) / "staging.sqlite3"
            shutil.copyfile(FIXTURE_DATABASE, source)
            source_before = source.read_bytes()

            result = _migrate_sqlite_staging_copy(source, target)

            self.assertFalse(result.already_complete)
            self.assertEqual(result.target_version, 9)
            self.assertEqual(_schema_version(target), 9)
            self.assertEqual(
                _semantic_digest_from_path(target),
                self.metadata["expected_upgrade"]["semantic_digest"],
            )
            self.assertEqual(source.read_bytes(), source_before)
            self.assertEqual(_schema_version(source), 6)

            uri = f"{target.resolve().as_uri()}?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                timeline = connection.execute(
                    "SELECT id, agent_id, user_id, timeline_entry_id, sort_key "
                    "FROM timeline_entries ORDER BY id"
                ).fetchall()
                counts = {
                    table: int(
                        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
                    for table in (
                        "relationships",
                        "source_turns",
                        "relationship_events",
                        "relationship_processing_runs",
                    )
                }
            self.assertEqual(counts, {name: 2 for name in counts})
            self.assertEqual(timeline[0]["sort_key"], "2026-01-17T00:31:00.000000Z")
            self.assertEqual(timeline[2]["sort_key"], "2026-01-18T00:02:00.000000Z")
            self.assertEqual(timeline[3]["sort_key"], "")
            self.assertEqual(
                timeline[1]["timeline_entry_id"],
                str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "erii:legacy-timeline:agent_orion:user_lin:2",
                    )
                ),
            )

            current = SQLiteStorage(str(target))
            self.assertEqual(current.schema_version, 9)

    def test_two_executions_have_one_semantic_identity_and_retry_is_idempotent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            source = Path(root_dir) / "source.sqlite3"
            first_target = Path(root_dir) / "first.sqlite3"
            second_target = Path(root_dir) / "second.sqlite3"
            shutil.copyfile(FIXTURE_DATABASE, source)

            first = _migrate_sqlite_staging_copy(source, first_target)
            second = _migrate_sqlite_staging_copy(source, second_target)
            first_bytes = first_target.read_bytes()
            retry = _migrate_sqlite_staging_copy(source, first_target)

            self.assertEqual(first.semantic_digest, second.semantic_digest)
            self.assertEqual(first.semantic_digest, retry.semantic_digest)
            self.assertTrue(retry.already_complete)
            self.assertEqual(first_target.read_bytes(), first_bytes)

    def test_write_failure_removes_partial_stage_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            source = Path(root_dir) / "source.sqlite3"
            target = Path(root_dir) / "staging.sqlite3"
            shutil.copyfile(FIXTURE_DATABASE, source)
            source_before = source.read_bytes()

            def fail_after_write(_path: Path) -> None:
                raise OSError("injected write failure")

            with self.assertRaisesRegex(OSError, "injected"):
                _migrate_sqlite_staging_copy(
                    source,
                    target,
                    _write_hook=fail_after_write,
                )

            self.assertFalse(target.exists())
            self.assertEqual(source.read_bytes(), source_before)

    def test_retry_rejects_an_occupied_different_staging_database(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            target = Path(root_dir) / "occupied.sqlite3"
            SQLiteStorage(str(target))
            before = target.read_bytes()

            with self.assertRaises(LifecycleConflictError):
                _migrate_sqlite_staging_copy(FIXTURE_DATABASE, target)

            self.assertEqual(target.read_bytes(), before)

    def test_only_schema6_is_accepted_by_this_historical_route(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            current = Path(root_dir) / "current.sqlite3"
            SQLiteStorage(str(current))

            with self.assertRaisesRegex(LifecyclePlanError, "schema 6 to schema 9"):
                _preview_sqlite_staging_upgrade(current)


if __name__ == "__main__":
    unittest.main()
