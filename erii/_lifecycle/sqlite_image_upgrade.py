"""Pure in-memory SQLite image upgrades for lifecycle planning and execution.

The Module accepts one rollback-journal SQLite image, migrates it entirely in
an in-memory connection, and returns a new image plus its verified semantic
identity.  It has no filesystem, Storage, Inspection, or facade dependency.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3
import uuid

from erii._lifecycle.sqlite_semantics import (
    quoted_identifier as _quoted_identifier,
    read_sqlite_schema_version_from_connection as _schema_version,
    semantic_digest_from_connection as _semantic_digest_from_connection,
)
from erii.compatibility import SQLITE_FORMAT
from erii.errors import LifecyclePlanError, StorageIntegrityError


_SQLITE_HEADER = b"SQLite format 3\x00"
_SOURCE_SCHEMA_VERSIONS = frozenset({6, 9, 10})
_TARGET_SCHEMA_VERSION = int(SQLITE_FORMAT.current_version)
_MIGRATION_NAMES = {
    7: "bounded-recent-timeline-alpha7",
    8: "semantic-timeline-order-alpha7",
    9: "utc-stable-timeline-order-alpha7",
    10: "relationship-consequence-journal-alpha1",
    11: "memory-pack-write-receipts-v1",
}
_ISO_FRACTION = re.compile(
    r"^(?P<seconds>.+[T ]\d{2}:\d{2}:\d{2})"
    r"\.(?P<fraction>\d+)"
    r"(?P<offset>[+-]\d{2}(?::?\d{2})?)?$"
)


@dataclass(frozen=True, slots=True)
class _SQLiteUpgradeResult:
    """Verifiable identity of one supported SQLite migration result."""

    source_version: int
    target_version: int
    semantic_digest: str
    byte_size: int
    already_complete: bool = False


def _timeline_timestamp_sort_key(timestamp: str | None) -> str:
    """Return the frozen UTC ordering key used by the schema-v9 backfill."""
    if timestamp is None:
        return ""
    value = str(timestamp).strip()
    if not value:
        return ""
    if value.endswith(("Z", "z")):
        value = f"{value[:-1]}+00:00"
    match = _ISO_FRACTION.fullmatch(value)
    if match is not None:
        fraction = match.group("fraction")[:6].ljust(6, "0")
        value = (
            f"{match.group('seconds')}.{fraction}"
            f"{match.group('offset') or ''}"
        )
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        instant = parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return ""
    return instant.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _migrate_recent_timeline_index_v7(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_timeline_recent "
        "ON timeline_entries(agent_id, user_id, id DESC)"
    )


def _migrate_semantic_timeline_order_v8(connection: sqlite3.Connection) -> None:
    connection.execute("DROP INDEX IF EXISTS idx_timeline_recent")
    connection.execute(
        "CREATE INDEX idx_timeline_recent "
        "ON timeline_entries(agent_id, user_id, timestamp DESC, id DESC)"
    )


def _migrate_stable_timeline_order_v9(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(timeline_entries)").fetchall()
    }
    if "sort_key" not in columns:
        connection.execute(
            "ALTER TABLE timeline_entries "
            "ADD COLUMN sort_key TEXT NOT NULL DEFAULT ''"
        )

    last_id = 0
    while True:
        rows = connection.execute(
            "SELECT id, agent_id, user_id, timestamp, timeline_entry_id, data "
            "FROM timeline_entries WHERE id > ? ORDER BY id LIMIT 512",
            (last_id,),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            entry_id = str(row["timeline_entry_id"] or "").strip()
            timestamp = row["timestamp"]
            if row["data"]:
                try:
                    data = json.loads(row["data"])
                except (TypeError, ValueError):
                    data = None
                if isinstance(data, dict):
                    entry_id = str(
                        data.get("timeline_entry_id") or entry_id
                    ).strip()
                    timestamp = (
                        data.get("recorded_at")
                        or data.get("legacy_timestamp")
                        or timestamp
                    )
            if not entry_id:
                entry_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        (
                            f"erii:legacy-timeline:{row['agent_id']}:"
                            f"{row['user_id']}:{row['id']}"
                        ),
                    )
                )
            connection.execute(
                "UPDATE timeline_entries "
                "SET timeline_entry_id = ?, sort_key = ? WHERE id = ?",
                (
                    entry_id,
                    _timeline_timestamp_sort_key(timestamp),
                    row["id"],
                ),
            )
            last_id = int(row["id"])

    connection.execute("DROP INDEX IF EXISTS idx_timeline_recent")
    connection.execute(
        "CREATE INDEX idx_timeline_recent "
        "ON timeline_entries("
        "agent_id, user_id, sort_key DESC, timeline_entry_id DESC)"
    )


def _migrate_relationship_consequence_v10(
    executor: sqlite3.Connection | sqlite3.Cursor,
) -> None:
    """Install the append-only consequence and Narrative Tension journals."""
    executor.execute(
        """
            CREATE TABLE IF NOT EXISTS relationship_consequences (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                consequence_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                tension_id TEXT NOT NULL,
                source_decision_id TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                data JSON NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE (
                    relationship_id, source_decision_id, source_event_id
                ),
                FOREIGN KEY (relationship_id)
                    REFERENCES relationships(relationship_id) ON DELETE CASCADE
            )
            """
    )
    executor.execute(
        """
            CREATE INDEX IF NOT EXISTS idx_relationship_consequences_order
            ON relationship_consequences(relationship_id, sequence)
            """
    )
    executor.execute(
        """
            CREATE INDEX IF NOT EXISTS idx_relationship_consequences_tension
            ON relationship_consequences(relationship_id, tension_id, sequence)
            """
    )
    executor.execute(
        """
            CREATE TABLE IF NOT EXISTS narrative_tension_links (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                tension_id TEXT NOT NULL,
                consequence_id TEXT NOT NULL,
                source_decision_id TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                data JSON NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE (tension_id, source_decision_id, source_event_id),
                FOREIGN KEY (relationship_id)
                    REFERENCES relationships(relationship_id) ON DELETE CASCADE,
                FOREIGN KEY (consequence_id)
                    REFERENCES relationship_consequences(consequence_id)
                    ON DELETE CASCADE
            )
            """
    )
    executor.execute(
        """
            CREATE INDEX IF NOT EXISTS idx_narrative_tension_links_order
            ON narrative_tension_links(relationship_id, sequence)
            """
    )
    executor.execute(
        """
            CREATE INDEX IF NOT EXISTS idx_narrative_tension_links_tension
            ON narrative_tension_links(relationship_id, tension_id, sequence)
            """
    )
    executor.execute(
        """
            CREATE INDEX IF NOT EXISTS idx_narrative_tension_links_consequence
            ON narrative_tension_links(consequence_id, sequence)
            """
    )


def _migrate_memory_pack_write_receipts_v11(
    executor: sqlite3.Connection | sqlite3.Cursor,
) -> None:
    """Install content-free exactly-once receipts for whole-pack writes."""
    executor.execute(
        """
            CREATE TABLE IF NOT EXISTS memory_pack_write_receipts (
                operation_id TEXT PRIMARY KEY,
                receipt_version INTEGER NOT NULL CHECK (receipt_version = 1),
                target_agent TEXT NOT NULL,
                target_user TEXT NOT NULL,
                relationship_id TEXT,
                result_json JSON NOT NULL,
                committed_at TEXT NOT NULL
            )
            """
    )
    executor.execute(
        """
            CREATE INDEX IF NOT EXISTS idx_memory_pack_write_receipts_scope
            ON memory_pack_write_receipts(
                target_agent, target_user, relationship_id
            )
            """
    )


def _index_columns(
    connection: sqlite3.Connection,
    index_name: str,
) -> tuple[str, ...]:
    quoted_name = _quoted_identifier(index_name)
    return tuple(
        str(row[2])
        for row in connection.execute(f"PRAGMA index_info({quoted_name})")
    )


def _validate_relationship_consequence_schema(
    connection: sqlite3.Connection,
) -> None:
    """Validate the complete v10 journal schema, not only its version row."""
    expected_columns = {
        "relationship_consequences": (
            "sequence",
            "consequence_id",
            "relationship_id",
            "tension_id",
            "source_decision_id",
            "source_event_id",
            "data",
            "recorded_at",
        ),
        "narrative_tension_links": (
            "sequence",
            "link_id",
            "relationship_id",
            "tension_id",
            "consequence_id",
            "source_decision_id",
            "source_event_id",
            "data",
            "recorded_at",
        ),
    }
    for table_name, columns in expected_columns.items():
        quoted_name = _quoted_identifier(table_name)
        actual = tuple(
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({quoted_name})")
        )
        if actual != columns:
            raise StorageIntegrityError(
                f"SQLite migration did not install the {table_name} table"
            )

    expected_indexes = {
        "idx_relationship_consequences_order": (
            "relationship_id",
            "sequence",
        ),
        "idx_relationship_consequences_tension": (
            "relationship_id",
            "tension_id",
            "sequence",
        ),
        "idx_narrative_tension_links_order": (
            "relationship_id",
            "sequence",
        ),
        "idx_narrative_tension_links_tension": (
            "relationship_id",
            "tension_id",
            "sequence",
        ),
        "idx_narrative_tension_links_consequence": (
            "consequence_id",
            "sequence",
        ),
    }
    for index_name, columns in expected_indexes.items():
        if _index_columns(connection, index_name) != columns:
            raise StorageIntegrityError(
                f"SQLite migration did not install the {index_name} index"
            )

    expected_unique_indexes = {
        "relationship_consequences": {
            ("consequence_id",),
            (
                "relationship_id",
                "source_decision_id",
                "source_event_id",
            ),
        },
        "narrative_tension_links": {
            ("link_id",),
            ("tension_id", "source_decision_id", "source_event_id"),
        },
    }
    for table_name, expected in expected_unique_indexes.items():
        quoted_name = _quoted_identifier(table_name)
        actual = {
            _index_columns(connection, str(row[1]))
            for row in connection.execute(f"PRAGMA index_list({quoted_name})")
            if int(row[2]) == 1
        }
        if not expected.issubset(actual):
            raise StorageIntegrityError(
                f"SQLite migration did not install {table_name} identity constraints"
            )

    expected_foreign_keys = {
        "relationship_consequences": {
            ("relationships", "relationship_id", "relationship_id", "CASCADE"),
        },
        "narrative_tension_links": {
            ("relationships", "relationship_id", "relationship_id", "CASCADE"),
            (
                "relationship_consequences",
                "consequence_id",
                "consequence_id",
                "CASCADE",
            ),
        },
    }
    for table_name, expected in expected_foreign_keys.items():
        quoted_name = _quoted_identifier(table_name)
        actual = {
            (str(row[2]), str(row[3]), str(row[4]), str(row[6]))
            for row in connection.execute(
                f"PRAGMA foreign_key_list({quoted_name})"
            )
        }
        if actual != expected:
            raise StorageIntegrityError(
                f"SQLite migration did not install {table_name} foreign keys"
            )


def _validate_upgraded_connection(connection: sqlite3.Connection) -> None:
    if _schema_version(connection) != _TARGET_SCHEMA_VERSION:
        raise StorageIntegrityError("SQLite migration did not reach the current schema")
    timeline_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(timeline_entries)").fetchall()
    }
    if "sort_key" not in timeline_columns:
        raise StorageIntegrityError(
            "SQLite migration did not add stable Timeline ordering"
        )
    index_sql = connection.execute(
        "SELECT sql FROM sqlite_schema "
        "WHERE type = 'index' AND name = 'idx_timeline_recent'"
    ).fetchone()
    if index_sql is None or "sort_key" not in str(index_sql[0]):
        raise StorageIntegrityError(
            "SQLite migration did not install the current index"
        )
    for version in (10, 11):
        migration_name = connection.execute(
            "SELECT name FROM schema_migrations WHERE version = ?",
            (version,),
        ).fetchone()
        if (
            migration_name is None
            or str(migration_name[0]) != _MIGRATION_NAMES[version]
        ):
            raise StorageIntegrityError(
                f"SQLite migration did not record the v{version} schema identity"
            )
    _validate_relationship_consequence_schema(connection)
    receipt_columns = tuple(
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(memory_pack_write_receipts)"
        )
    )
    if receipt_columns != (
        "operation_id",
        "receipt_version",
        "target_agent",
        "target_user",
        "relationship_id",
        "result_json",
        "committed_at",
    ):
        raise StorageIntegrityError(
            "SQLite migration did not install the MemoryPack receipt table"
        )
    if _index_columns(
        connection,
        "idx_memory_pack_write_receipts_scope",
    ) != ("target_agent", "target_user", "relationship_id"):
        raise StorageIntegrityError(
            "SQLite migration did not install the MemoryPack receipt scope index"
        )
    _semantic_digest_from_connection(connection)


def _migrate_loaded_connection(
    connection: sqlite3.Connection,
) -> tuple[bytes, _SQLiteUpgradeResult]:
    """Migrate a loaded SQLite connection without consulting external state."""
    connection.row_factory = sqlite3.Row
    try:
        source_version = _schema_version(connection)
        if source_version not in _SOURCE_SCHEMA_VERSIONS:
            raise LifecyclePlanError(
                "SQLite staging migration supports only schema 6, 9, or 10 "
                f"sources to schema {_TARGET_SCHEMA_VERSION}"
            )
        if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
            raise StorageIntegrityError(
                "SQLite upgrade source failed integrity check"
            )
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise StorageIntegrityError(
                "SQLite upgrade source failed foreign-key integrity check"
            )
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for version, migrate in (
            (7, _migrate_recent_timeline_index_v7),
            (8, _migrate_semantic_timeline_order_v8),
            (9, _migrate_stable_timeline_order_v9),
            (10, _migrate_relationship_consequence_v10),
            (11, _migrate_memory_pack_write_receipts_v11),
        ):
            if version <= source_version:
                continue
            migrate(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (
                    version,
                    _MIGRATION_NAMES[version],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        _validate_upgraded_connection(connection)
        connection.commit()
        _validate_upgraded_connection(connection)
        migrated = connection.serialize()
        semantic_digest = _semantic_digest_from_connection(connection)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    return migrated, _SQLiteUpgradeResult(
        source_version=source_version,
        target_version=_TARGET_SCHEMA_VERSION,
        semantic_digest=semantic_digest,
        byte_size=len(migrated),
    )


def _migrate_sqlite_bytes(
    source_bytes: bytes,
) -> tuple[bytes, _SQLiteUpgradeResult]:
    """Migrate one supported rollback-journal image entirely in memory."""
    if not isinstance(source_bytes, bytes) or not source_bytes.startswith(_SQLITE_HEADER):
        raise StorageIntegrityError("SQLite upgrade source has an invalid header")
    with closing(sqlite3.connect(":memory:")) as connection:
        try:
            connection.deserialize(source_bytes)
        except sqlite3.Error as exc:
            raise StorageIntegrityError(
                "SQLite upgrade source image is unreadable"
            ) from exc
        return _migrate_loaded_connection(connection)


__all__: list[str] = []
