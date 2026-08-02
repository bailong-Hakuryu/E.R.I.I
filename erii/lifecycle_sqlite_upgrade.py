"""Private SQLite staging-copy migration support for the lifecycle coordinator.

This module deliberately does not expose an in-place migration API.  Callers
preview a supported historical database in memory, or ask for a migrated copy
at a missing staging path.  The source database is never opened for writing.
"""

from __future__ import annotations

import base64
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Callable
import uuid

from erii.compatibility import SQLITE_FORMAT
from erii.errors import (
    LifecycleConflictError,
    LifecyclePlanError,
    StorageIntegrityError,
)
from erii.storage.timeline_order import timeline_timestamp_sort_key


_SQLITE_HEADER = b"SQLite format 3\x00"
_SOURCE_SCHEMA_VERSION = 6
_TARGET_SCHEMA_VERSION = int(SQLITE_FORMAT.current_version)
_MIGRATION_NAMES = {
    7: "bounded-recent-timeline-alpha7",
    8: "semantic-timeline-order-alpha7",
    9: "utc-stable-timeline-order-alpha7",
}


@dataclass(frozen=True, slots=True)
class _SQLiteUpgradeResult:
    """Verifiable identity of one supported SQLite migration result."""

    source_version: int
    target_version: int
    semantic_digest: str
    byte_size: int
    already_complete: bool = False


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _canonical_sqlite_value(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StorageIntegrityError("SQLite contains a non-finite numeric value")
        return {"type": "real", "value": value.hex()}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    if isinstance(value, bytes):
        return {
            "type": "blob",
            "value": base64.b64encode(value).decode("ascii"),
        }
    raise StorageIntegrityError("SQLite contains a value with an unsupported type")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_row_values(
    table_name: str,
    columns: list[str],
    raw_values: tuple[object, ...],
) -> list[dict[str, object]]:
    values = list(raw_values)
    if table_name == "schema_migrations" and "applied_at" in columns:
        version_index = columns.index("version")
        applied_at_index = columns.index("applied_at")
        version = values[version_index]
        if isinstance(version, int) and version >= 7:
            # Lifecycle migrations record the real execution time.  It is
            # operational metadata, not logical user data, and therefore must
            # not make an otherwise identical upgrade unverifiable.
            values[applied_at_index] = "<lifecycle-migration-time>"
    return [_canonical_sqlite_value(value) for value in values]


def _write_json_array(
    digest: "hashlib._Hash",
    values: object,
) -> None:
    digest.update(_canonical_json_bytes(values))


def _stream_table_digest(
    digest: "hashlib._Hash",
    connection: sqlite3.Connection,
    table_name: str,
) -> None:
    quoted_name = _quoted_identifier(table_name)
    columns: list[str] = []
    digest.update(b'{"columns":[')
    first_column = True
    for column in connection.execute(f"PRAGMA table_xinfo({quoted_name})"):
        if not first_column:
            digest.update(b",")
        first_column = False
        _write_json_array(
            digest,
            [_canonical_sqlite_value(value) for value in tuple(column)],
        )
        if int(column[6]) == 0:
            columns.append(str(column[1]))
    digest.update(b'],"name":')
    digest.update(_canonical_json_bytes(table_name))
    digest.update(b',"rows":[')

    quoted_columns = ",".join(_quoted_identifier(column) for column in columns)
    function_name = "_erii_canonical_row"

    def canonical_sort_key(*raw_values: object) -> str:
        return _canonical_json_bytes(
            _canonical_row_values(table_name, columns, tuple(raw_values))
        ).decode("utf-8")

    try:
        connection.create_function(
            function_name,
            -1,
            canonical_sort_key,
            deterministic=True,
        )
        row_query = (
            f"SELECT {quoted_columns} FROM {quoted_name} "
            f"ORDER BY {function_name}({quoted_columns}) COLLATE BINARY"
        )
        first_row = True
        for raw_row in connection.execute(row_query):
            if not first_row:
                digest.update(b",")
            first_row = False
            digest.update(
                _canonical_json_bytes(
                    _canonical_row_values(table_name, columns, tuple(raw_row))
                )
            )
    except sqlite3.Error as exc:
        raise StorageIntegrityError(
            f"SQLite table {table_name!r} could not be canonicalized"
        ) from exc
    finally:
        try:
            connection.create_function(function_name, -1, None)
        except sqlite3.Error:
            pass
    digest.update(b"]}")


def _semantic_digest_from_connection(connection: sqlite3.Connection) -> str:
    integrity_cursor = connection.execute("PRAGMA integrity_check")
    integrity = integrity_cursor.fetchone()
    if integrity is None or str(integrity[0]) != "ok" or integrity_cursor.fetchone() is not None:
        raise StorageIntegrityError("SQLite integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StorageIntegrityError("SQLite foreign-key integrity check failed")

    digest = hashlib.sha256()
    digest.update(b'{"application_id":')
    digest.update(
        _canonical_json_bytes(
            int(connection.execute("PRAGMA application_id").fetchone()[0])
        )
    )
    digest.update(b',"encoding":')
    digest.update(
        _canonical_json_bytes(str(connection.execute("PRAGMA encoding").fetchone()[0]))
    )
    digest.update(b',"schema":[')
    first_schema = True
    for row in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name, tbl_name"
    ):
        if not first_schema:
            digest.update(b",")
        first_schema = False
        digest.update(
            _canonical_json_bytes(
                {
                    "type": str(row[0]),
                    "name": str(row[1]),
                    "table": str(row[2]),
                    "sql": None if row[3] is None else str(row[3]),
                }
            )
        )
    digest.update(b'],"tables":[')

    # The schema-name inventory is bounded by schema size, not user row count.
    # Consume its cursor before registering the canonical-row UDF: SQLite does
    # not permit create_function while another statement is still active on
    # the same connection.
    table_names = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    if connection.execute(
        "SELECT 1 FROM sqlite_schema "
        "WHERE type = 'table' AND name = 'sqlite_sequence'"
    ).fetchone() is not None:
        table_names.append("sqlite_sequence")

    first_table = True
    for table_name in table_names:
        if not first_table:
            digest.update(b",")
        first_table = False
        _stream_table_digest(digest, connection, table_name)
    digest.update(b'],"user_version":')
    digest.update(
        _canonical_json_bytes(int(connection.execute("PRAGMA user_version").fetchone()[0]))
    )
    digest.update(b"}")
    return digest.hexdigest()


def _schema_version(connection: sqlite3.Connection) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_schema "
        "WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        return 0
    rows = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    versions = [row[0] for row in rows]
    if any(isinstance(value, bool) or not isinstance(value, int) for value in versions):
        raise StorageIntegrityError("SQLite schema migration versions must be integers")
    current = max(versions, default=0)
    if versions != list(range(1, current + 1)):
        raise StorageIntegrityError("SQLite schema migration history is not contiguous")
    return current


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
                    timeline_timestamp_sort_key(timestamp),
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


def _validate_upgraded_connection(connection: sqlite3.Connection) -> None:
    if _schema_version(connection) != _TARGET_SCHEMA_VERSION:
        raise StorageIntegrityError("SQLite migration did not reach the current schema")
    timeline_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(timeline_entries)").fetchall()
    }
    if "sort_key" not in timeline_columns:
        raise StorageIntegrityError("SQLite migration did not add stable Timeline ordering")
    index_sql = connection.execute(
        "SELECT sql FROM sqlite_schema "
        "WHERE type = 'index' AND name = 'idx_timeline_recent'"
    ).fetchone()
    if index_sql is None or "sort_key" not in str(index_sql[0]):
        raise StorageIntegrityError("SQLite migration did not install the current index")
    _semantic_digest_from_connection(connection)


def _migrate_loaded_connection(
    connection: sqlite3.Connection,
) -> tuple[bytes, _SQLiteUpgradeResult]:
    connection.row_factory = sqlite3.Row
    try:
        if _schema_version(connection) != _SOURCE_SCHEMA_VERSION:
            raise LifecyclePlanError(
                "SQLite staging migration supports only schema 6 to schema 9"
            )
        if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
            raise StorageIntegrityError("SQLite upgrade source failed integrity check")
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
        ):
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
        source_version=_SOURCE_SCHEMA_VERSION,
        target_version=_TARGET_SCHEMA_VERSION,
        semantic_digest=semantic_digest,
        byte_size=len(migrated),
    )


def _migrate_sqlite_bytes(source_bytes: bytes) -> tuple[bytes, _SQLiteUpgradeResult]:
    """Migrates one rollback-journal schema-6 image entirely in memory."""
    if not isinstance(source_bytes, bytes) or not source_bytes.startswith(_SQLITE_HEADER):
        raise StorageIntegrityError("SQLite upgrade source has an invalid header")
    with closing(sqlite3.connect(":memory:")) as connection:
        try:
            connection.deserialize(source_bytes)
        except sqlite3.Error as exc:
            raise StorageIntegrityError("SQLite upgrade source image is unreadable") from exc
        return _migrate_loaded_connection(connection)


def _migrate_source_path(path: Path) -> tuple[bytes, _SQLiteUpgradeResult]:
    """Loads a read-only source through SQLite's backup API before migration."""
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as source_connection:
            with closing(sqlite3.connect(":memory:")) as migration_connection:
                source_connection.backup(migration_connection)
                return _migrate_loaded_connection(migration_connection)
    except (LifecyclePlanError, StorageIntegrityError):
        raise
    except sqlite3.Error as exc:
        raise StorageIntegrityError("SQLite upgrade source is unreadable") from exc


def _read_stable_source(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise StorageIntegrityError("SQLite upgrade source must be a regular file")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() and sidecar.stat().st_size:
            raise StorageIntegrityError(
                "SQLite upgrade requires a quiescent source without WAL or journal data"
            )
    before = path.stat()
    content = path.read_bytes()
    after = path.stat()

    def signature(info: os.stat_result) -> tuple[int, int, int, int]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
        )
    if signature(before) != signature(after):
        raise StorageIntegrityError("SQLite upgrade source changed while it was read")
    return content


def _preview_sqlite_staging_upgrade(source_path: str | os.PathLike[str]) -> _SQLiteUpgradeResult:
    """Returns the v9 semantic identity without writing any filesystem path."""
    source = Path(source_path)
    source_bytes = _read_stable_source(source)
    _migrated, result = _migrate_source_path(source)
    if _read_stable_source(source) != source_bytes:
        raise StorageIntegrityError("SQLite upgrade source changed during preview")
    return result


def _write_new_file(
    path: Path,
    content: bytes,
    *,
    write_hook: Callable[[Path], None] | None = None,
) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if write_hook is not None:
            write_hook(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _semantic_digest_from_path(path: Path) -> str:
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            return _semantic_digest_from_connection(connection)
    except StorageIntegrityError:
        raise
    except sqlite3.Error as exc:
        raise StorageIntegrityError("migrated SQLite staging copy is unreadable") from exc


def _migrate_sqlite_staging_copy(
    source_path: str | os.PathLike[str],
    staging_path: str | os.PathLike[str],
    *,
    _write_hook: Callable[[Path], None] | None = None,
) -> _SQLiteUpgradeResult:
    """Creates or verifies a schema-9 staging copy while preserving its source."""
    source = Path(source_path)
    staging = Path(staging_path)
    if source.resolve(strict=False) == staging.resolve(strict=False):
        raise LifecyclePlanError("SQLite upgrade source and staging path must differ")
    if not staging.parent.is_dir():
        raise LifecyclePlanError("SQLite upgrade staging parent must already exist")

    source_bytes = _read_stable_source(source)
    migrated, result = _migrate_source_path(source)
    if staging.exists():
        if staging.is_symlink() or not staging.is_file():
            raise LifecycleConflictError(
                "SQLite upgrade staging destination contains a different artifact"
            )
        existing_digest = _semantic_digest_from_path(staging)
        if existing_digest != result.semantic_digest:
            raise LifecycleConflictError(
                "SQLite upgrade staging destination contains a different database"
            )
        if _read_stable_source(source) != source_bytes:
            raise StorageIntegrityError("SQLite upgrade source changed during retry")
        return _SQLiteUpgradeResult(
            source_version=result.source_version,
            target_version=result.target_version,
            semantic_digest=result.semantic_digest,
            byte_size=staging.stat().st_size,
            already_complete=True,
        )

    try:
        _write_new_file(staging, migrated, write_hook=_write_hook)
        if _semantic_digest_from_path(staging) != result.semantic_digest:
            raise StorageIntegrityError(
                "migrated SQLite staging copy differs from its semantic identity"
            )
        if _read_stable_source(source) != source_bytes:
            raise StorageIntegrityError("SQLite upgrade source changed during migration")
        return result
    except Exception:
        try:
            if staging.exists() and not staging.is_symlink() and staging.is_file():
                staging.unlink()
        except OSError:
            pass
        raise


__all__: list[str] = []
