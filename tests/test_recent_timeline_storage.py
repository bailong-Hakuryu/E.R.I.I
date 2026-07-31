"""Bounded structured-Timeline retrieval contracts for storage drivers."""

from contextlib import closing
import os
import sqlite3
import tempfile
import unittest

from erii import FileStorage, SQLiteStorage
from erii.models.archival import TimelineEntry
from erii.models.provenance import ArtifactProvenanceState
from erii.storage.base import BaseStorage


class _LegacyStructuredStorage(BaseStorage):
    """Represents a custom adapter written before the bounded read API."""

    def __init__(self, entries):
        super().__init__()
        self.entries = list(entries)

    def save_nodes(self, agent_id, user_id, nodes):
        del agent_id, user_id, nodes

    def load_nodes(self, agent_id, user_id):
        del agent_id, user_id
        return []

    def get_core_memory(self, agent_id, user_id):
        del agent_id, user_id
        return ""

    def save_core_memory(self, agent_id, user_id, content):
        del agent_id, user_id, content

    def add_timeline_entry(
        self,
        agent_id,
        user_id,
        entry,
        timestamp=None,
    ):
        del agent_id, user_id, entry, timestamp

    def get_recent_timeline(self, agent_id, user_id, limit=5):
        del agent_id, user_id, limit
        return []

    def list_timeline_entries(self, agent_id, user_id):
        del agent_id, user_id
        return list(self.entries)


def _entry(index):
    return TimelineEntry(
        timeline_entry_id=f"timeline-{index}",
        relationship_id="relationship-1",
        agent_id="agent-lumi",
        user_id="user-chen",
        content=f"memory-{index}",
        recorded_at=None,
        legacy_timestamp=f"2026-07-31 00:00:0{index}",
        provenance_state=ArtifactProvenanceState.LEGACY_UNAVAILABLE,
    )


def _legacy_entry(entry_id, timestamp, content=None):
    return TimelineEntry(
        timeline_entry_id=entry_id,
        relationship_id="relationship-1",
        agent_id="agent-lumi",
        user_id="user-chen",
        content=content or entry_id,
        recorded_at=None,
        legacy_timestamp=timestamp,
        provenance_state=ArtifactProvenanceState.LEGACY_UNAVAILABLE,
    )


class RecentTimelineStorageTest(unittest.TestCase):
    def test_base_fallback_keeps_existing_custom_adapters_compatible(self):
        storage = _LegacyStructuredStorage([_entry(1), _entry(2), _entry(3)])

        recent = storage.get_recent_timeline_entries(
            "agent-lumi",
            "user-chen",
            limit=2,
        )

        self.assertEqual([item.timeline_entry_id for item in recent], [
            "timeline-2",
            "timeline-3",
        ])
        self.assertEqual(
            storage.get_recent_timeline_entries(
                "agent-lumi",
                "user-chen",
                limit=0,
            ),
            [],
        )

    def test_builtin_drivers_return_only_the_recent_ordered_entries(self):
        with tempfile.TemporaryDirectory() as root:
            factories = (
                ("file", lambda: FileStorage(os.path.join(root, "file"))),
                (
                    "sqlite",
                    lambda: SQLiteStorage(os.path.join(root, "memory.db")),
                ),
            )
            for name, factory in factories:
                with self.subTest(driver=name):
                    storage = factory()
                    for index in range(1, 6):
                        storage.add_timeline_entry(
                            "agent-lumi",
                            "user-chen",
                            f"memory-{index}",
                            timestamp=f"2026-07-31 00:00:0{index}",
                        )

                    recent = storage.get_recent_timeline_entries(
                        "agent-lumi",
                        "user-chen",
                        limit=2,
                    )

                    self.assertEqual(
                        [item.content for item in recent],
                        ["memory-4", "memory-5"],
                    )
                    self.assertEqual(
                        storage.get_recent_timeline_entries(
                            "agent-lumi",
                            "user-chen",
                            limit=-1,
                        ),
                        [],
                    )

    def test_sqlite_bounded_read_does_not_materialize_the_full_timeline(self):
        with tempfile.TemporaryDirectory() as root:
            storage = SQLiteStorage(os.path.join(root, "memory.db"))
            for index in range(1, 6):
                storage.import_timeline_entries(
                    "agent-lumi",
                    "user-chen",
                    [_entry(index)],
                )

            def fail_if_called(*args, **kwargs):
                del args, kwargs
                raise AssertionError("full Timeline materialization is forbidden")

            storage.list_timeline_entries = fail_if_called
            recent = storage.get_recent_timeline_entries(
                "agent-lumi",
                "user-chen",
                limit=2,
            )

            self.assertEqual(
                [item.timeline_entry_id for item in recent],
                ["timeline-4", "timeline-5"],
            )

    def test_recent_entries_use_semantic_time_not_insertion_order(self):
        with tempfile.TemporaryDirectory() as root:
            factories = (
                ("file", lambda: FileStorage(os.path.join(root, "file"))),
                (
                    "sqlite",
                    lambda: SQLiteStorage(os.path.join(root, "memory.db")),
                ),
            )
            for name, factory in factories:
                with self.subTest(driver=name):
                    storage = factory()
                    storage.add_timeline_entry(
                        "agent-lumi",
                        "user-chen",
                        "future-memory",
                        timestamp="2030-01-01 00:00:00",
                    )
                    storage.add_timeline_entry(
                        "agent-lumi",
                        "user-chen",
                        "older-memory-inserted-later",
                        timestamp="2020-01-01 00:00:00",
                    )

                    recent = storage.get_recent_timeline_entries(
                        "agent-lumi",
                        "user-chen",
                        limit=1,
                    )

                    self.assertEqual(
                        [item.content for item in recent],
                        ["future-memory"],
                    )
                    self.assertEqual(
                        [
                            item.content
                            for item in storage.list_timeline_entries(
                                "agent-lumi",
                                "user-chen",
                            )
                        ],
                        ["older-memory-inserted-later", "future-memory"],
                    )

    def test_iso_offsets_and_fractional_seconds_sort_by_instant(self):
        with tempfile.TemporaryDirectory() as root:
            factories = (
                ("file", lambda: FileStorage(os.path.join(root, "file"))),
                (
                    "sqlite",
                    lambda: SQLiteStorage(os.path.join(root, "memory.db")),
                ),
            )
            entries = [
                _legacy_entry(
                    "timeline-fraction-900",
                    "2026-07-31T00:00:00.9Z",
                ),
                _legacy_entry(
                    "timeline-offset-earlier",
                    "2026-07-30T20:30:00.500000-03:30",
                ),
                _legacy_entry(
                    "timeline-fraction-100",
                    "2026-07-31T00:00:00.10+00:00",
                ),
            ]
            for name, factory in factories:
                with self.subTest(driver=name):
                    storage = factory()
                    storage.import_timeline_entries(
                        "agent-lumi",
                        "user-chen",
                        entries,
                    )

                    ordered = storage.list_timeline_entries(
                        "agent-lumi",
                        "user-chen",
                    )

                    self.assertEqual(
                        [item.timeline_entry_id for item in ordered],
                        [
                            "timeline-fraction-100",
                            "timeline-offset-earlier",
                            "timeline-fraction-900",
                        ],
                    )

    def test_equal_instants_use_timeline_identity_as_stable_tie_break(self):
        with tempfile.TemporaryDirectory() as root:
            factories = (
                ("file", lambda: FileStorage(os.path.join(root, "file"))),
                (
                    "sqlite",
                    lambda: SQLiteStorage(os.path.join(root, "memory.db")),
                ),
            )
            entries = [
                _legacy_entry(
                    "timeline-b",
                    "2026-07-31T00:00:00Z",
                ),
                _legacy_entry(
                    "timeline-a",
                    "2026-07-31T00:00:00+00:00",
                ),
            ]
            for name, factory in factories:
                with self.subTest(driver=name):
                    storage = factory()
                    storage.import_timeline_entries(
                        "agent-lumi",
                        "user-chen",
                        entries,
                    )

                    self.assertEqual(
                        [
                            item.timeline_entry_id
                            for item in storage.list_timeline_entries(
                                "agent-lumi",
                                "user-chen",
                            )
                        ],
                        ["timeline-a", "timeline-b"],
                    )
                    self.assertEqual(
                        [
                            item.timeline_entry_id
                            for item in storage.get_recent_timeline_entries(
                                "agent-lumi",
                                "user-chen",
                                limit=1,
                            )
                        ],
                        ["timeline-b"],
                    )

    def test_sqlite_v9_backfills_stable_timeline_sort_fields(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "memory.db")
            storage = SQLiteStorage(path)
            storage.add_timeline_entry(
                "agent-lumi",
                "user-chen",
                "legacy-row",
                timestamp="2026-07-31T00:00:00Z",
            )
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version = 9"
                )
                connection.execute("DROP INDEX IF EXISTS idx_timeline_recent")
                connection.execute(
                    """
                    UPDATE timeline_entries
                    SET timeline_entry_id = NULL, sort_key = ''
                    """
                )
                connection.commit()

            reopened = SQLiteStorage(path)
            with closing(reopened._get_connection()) as connection:
                row = connection.execute(
                    """
                    SELECT timeline_entry_id, sort_key
                    FROM timeline_entries
                    """
                ).fetchone()

            self.assertEqual(reopened.schema_version, 9)
            self.assertTrue(row["timeline_entry_id"])
            self.assertTrue(row["sort_key"])

    def test_sqlite_v9_preserves_null_legacy_time_as_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "legacy-null.db")
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE timeline_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        agent_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        timestamp TEXT,
                        timeline_entry_id TEXT,
                        source_archival_id TEXT,
                        data JSON
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE relationships (
                        relationship_id TEXT PRIMARY KEY,
                        agent_id TEXT NOT NULL,
                        user_id TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO schema_migrations (version, name, applied_at)
                    VALUES (8, 'semantic-timeline-order-alpha7', '2026-07-31')
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO timeline_entries (
                        agent_id, user_id, content, timestamp, timeline_entry_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "agent-lumi",
                            "user-chen",
                            "unknown-time",
                            None,
                            "timeline-unknown",
                        ),
                        (
                            "agent-lumi",
                            "user-chen",
                            "known-time",
                            "2026-07-31T00:00:00Z",
                            "timeline-known",
                        ),
                    ],
                )
                connection.commit()

            storage = SQLiteStorage(path)

            self.assertEqual(
                [
                    item.content
                    for item in storage.list_timeline_entries(
                        "agent-lumi",
                        "user-chen",
                    )
                ],
                ["unknown-time", "known-time"],
            )
            self.assertEqual(
                [
                    item.content
                    for item in storage.get_recent_timeline_entries(
                        "agent-lumi",
                        "user-chen",
                        limit=1,
                    )
                ],
                ["known-time"],
            )

    def test_sqlite_has_a_scope_and_recency_index(self):
        with tempfile.TemporaryDirectory() as root:
            storage = SQLiteStorage(os.path.join(root, "memory.db"))

            connection = storage._get_connection()
            try:
                index_row = connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                    WHERE type = 'index' AND name = 'idx_timeline_recent'
                    """
                ).fetchone()
            finally:
                connection.close()

            self.assertIsNotNone(index_row)
            self.assertIn("sort_key DESC", index_row["sql"])
            self.assertIn("timeline_entry_id DESC", index_row["sql"])


if __name__ == "__main__":
    unittest.main()
