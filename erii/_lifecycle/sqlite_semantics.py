"""Read-only canonical semantic identity for SQLite lifecycle data."""

from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from erii._lifecycle.filesystem import (
    assert_no_link_or_reparse_ancestors,
    lexists,
    read_stable_bytes,
    require_regular_file,
    sqlite_uri,
)
from erii.compatibility import SQLITE_FORMAT
from erii.errors import StorageIntegrityError, UnsupportedFormatError


def read_sqlite_schema_version_from_connection(
    connection: sqlite3.Connection,
    *,
    maximum_supported_version: int | None = None,
) -> int:
    """Read and validate the canonical contiguous schema migration history."""
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        return 0
    rows = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    versions = [row[0] for row in rows]
    if any(
        isinstance(version, bool) or not isinstance(version, int)
        for version in versions
    ):
        raise StorageIntegrityError(
            "SQLite schema migration versions must be integers"
        )
    current = max(versions, default=0)
    if (
        maximum_supported_version is not None
        and current > maximum_supported_version
    ):
        raise UnsupportedFormatError(
            f"unsupported {SQLITE_FORMAT.format_id} version {current!r}; "
            f"current reader is {SQLITE_FORMAT.current_version!r}"
        )
    if versions != list(range(1, current + 1)):
        raise StorageIntegrityError(
            "SQLite schema migration history is not contiguous"
        )
    return current


def read_sqlite_schema_version(path_value: str, *, immutable: bool) -> int | None:
    """Read an existing SQLite schema identity without creating the database."""
    path = Path(path_value)
    if not lexists(path):
        return None
    assert_no_link_or_reparse_ancestors(path, label="SQLite lifecycle target")
    info = require_regular_file(path, label="SQLite lifecycle target")
    if info.st_size == 0:
        return 0
    try:
        if read_stable_bytes(path, read_limit=16) != b"SQLite format 3\x00":
            raise StorageIntegrityError(
                "SQLite lifecycle target has an invalid header"
            )
        with closing(
            sqlite3.connect(sqlite_uri(path, immutable=immutable), uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            current = read_sqlite_schema_version_from_connection(
                connection,
                maximum_supported_version=int(SQLITE_FORMAT.current_version),
            )
            return current
    except (UnsupportedFormatError, StorageIntegrityError):
        raise
    except (OSError, sqlite3.Error) as exc:
        raise StorageIntegrityError(
            "SQLite schema metadata is unreadable or malformed"
        ) from exc


def quoted_identifier(value: str) -> str:
    """Return a safely quoted SQLite identifier."""
    return '"' + value.replace('"', '""') + '"'


def _canonical_sqlite_value(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StorageIntegrityError(
                "SQLite contains a non-finite numeric value"
            )
        return {"type": "real", "value": value.hex()}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    if isinstance(value, bytes):
        return {
            "type": "blob",
            "value": base64.b64encode(value).decode("ascii"),
        }
    raise StorageIntegrityError(
        "SQLite contains a value with an unsupported type"
    )


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
            values[applied_at_index] = "<lifecycle-migration-time>"
    return [_canonical_sqlite_value(value) for value in values]


def _write_json_array(digest: "hashlib._Hash", values: object) -> None:
    digest.update(_canonical_json_bytes(values))


def _stream_table_digest(
    digest: "hashlib._Hash",
    connection: sqlite3.Connection,
    table_name: str,
) -> None:
    quoted_name = quoted_identifier(table_name)
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

    quoted_columns = ",".join(quoted_identifier(column) for column in columns)
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
                    _canonical_row_values(
                        table_name,
                        columns,
                        tuple(raw_row),
                    )
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


def semantic_digest_from_connection(connection: sqlite3.Connection) -> str:
    """Return the canonical logical identity of an open SQLite database."""
    integrity_cursor = connection.execute("PRAGMA integrity_check")
    integrity = integrity_cursor.fetchone()
    if (
        integrity is None
        or str(integrity[0]) != "ok"
        or integrity_cursor.fetchone() is not None
    ):
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
        _canonical_json_bytes(
            str(connection.execute("PRAGMA encoding").fetchone()[0])
        )
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
        _canonical_json_bytes(
            int(connection.execute("PRAGMA user_version").fetchone()[0])
        )
    )
    digest.update(b"}")
    return digest.hexdigest()


def semantic_digest_from_path(path: Path) -> str:
    """Return the canonical logical identity of a read-only SQLite path."""
    uri = sqlite_uri(path, immutable=True)
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            return semantic_digest_from_connection(connection)
    except StorageIntegrityError:
        raise
    except sqlite3.Error as exc:
        raise StorageIntegrityError(
            "migrated SQLite staging copy is unreadable"
        ) from exc


__all__ = [
    "quoted_identifier",
    "read_sqlite_schema_version",
    "read_sqlite_schema_version_from_connection",
    "semantic_digest_from_connection",
    "semantic_digest_from_path",
]
