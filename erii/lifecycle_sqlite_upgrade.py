"""Private SQLite staging-copy migration support for lifecycle execution.

The in-memory migration algorithm lives in ``_lifecycle.sqlite_image_upgrade``
so planning and execution share one pure implementation. This Module owns only
source-path observation and staging-file publication.
"""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
from typing import Callable

from erii._lifecycle.sqlite_image_upgrade import (
    _SQLiteUpgradeResult,
    _migrate_loaded_connection,
    _migrate_sqlite_bytes,  # noqa: F401 - historical private import seam
    _validate_upgraded_connection,  # noqa: F401 - historical private import seam
)
from erii._lifecycle.sqlite_semantics import (
    semantic_digest_from_connection as _semantic_digest_from_connection,  # noqa: F401
    semantic_digest_from_path as _semantic_digest_from_path,
)
from erii.errors import (
    LifecycleConflictError,
    LifecyclePlanError,
    StorageIntegrityError,
)


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
    """Returns the current semantic identity without writing any path."""
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


def _migrate_sqlite_staging_copy(
    source_path: str | os.PathLike[str],
    staging_path: str | os.PathLike[str],
    *,
    _write_hook: Callable[[Path], None] | None = None,
) -> _SQLiteUpgradeResult:
    """Creates or verifies a current staging copy while preserving its source."""
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
