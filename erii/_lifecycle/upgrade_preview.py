"""Pure lifecycle upgrade previews shared by Planning and Execution."""

from __future__ import annotations

import hashlib
import json

from erii._lifecycle.inspection import FILE_STORAGE_MANIFEST
from erii._lifecycle.memory_pack_validation import (
    validate_memory_pack_semantic_graph,
)
from erii._lifecycle.plan_codec import (
    FILE_STORAGE_UPGRADE_STRATEGIES,
    MEMORY_PACK_STRATEGY_PREFIX,
    SQLITE_UPGRADE_STRATEGIES,
    canonical_json,
)
from erii._lifecycle.snapshots import PayloadSnapshot, materialize_snapshot
from erii._lifecycle.sqlite_image_upgrade import _migrate_sqlite_bytes
from erii._lifecycle.filesystem import fingerprint_files
from erii._lifecycle.contracts import (
    LifecycleContentIdentity,
    LifecycleStatus,
    LifecycleTargetKind,
)
from erii.compatibility import (
    FILE_STORAGE_FORMAT,
    MEMORY_PACK_FORMAT,
    SQLITE_FORMAT,
)
from erii.errors import LifecyclePlanError, StorageIntegrityError
from erii.models.pack import MemoryPack


def upgrade_snapshot(
    strategy_id: str,
    source: PayloadSnapshot,
) -> PayloadSnapshot:
    """Materialize and preview one supported historical payload upgrade."""
    source = materialize_snapshot(source)
    assert source.files is not None
    if strategy_id in SQLITE_UPGRADE_STRATEGIES.values():
        return _upgrade_sqlite_snapshot(strategy_id, source)
    if strategy_id.startswith(MEMORY_PACK_STRATEGY_PREFIX):
        return _upgrade_memory_pack_snapshot(strategy_id, source)
    if strategy_id not in FILE_STORAGE_UPGRADE_STRATEGIES.values():
        raise LifecyclePlanError("lifecycle upgrade strategy is unavailable")
    return _upgrade_file_storage_snapshot(strategy_id, source)


def _upgrade_file_storage_snapshot(
    strategy_id: str,
    source: PayloadSnapshot,
) -> PayloadSnapshot:
    assert source.files is not None
    expected_strategy = FILE_STORAGE_UPGRADE_STRATEGIES.get(
        source.content.detected_version
    )
    if (
        strategy_id != expected_strategy
        or source.content.kind is not LifecycleTargetKind.FILE_STORAGE
        or source.content.status is not LifecycleStatus.MIGRATION_REQUIRED
        or source.content.current_version != FILE_STORAGE_FORMAT.current_version
    ):
        raise LifecyclePlanError("FileStorage upgrade source identity is invalid")

    files = dict(source.files)
    if source.content.detected_version == "legacy":
        if FILE_STORAGE_MANIFEST in files:
            raise StorageIntegrityError(
                "legacy FileStorage unexpectedly contains a manifest"
            )
    else:
        manifest_bytes = files.get(FILE_STORAGE_MANIFEST)
        if manifest_bytes is None:
            raise StorageIntegrityError("versioned FileStorage is missing its manifest")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageIntegrityError("FileStorage manifest is malformed") from exc
        if manifest != {
            "format": FILE_STORAGE_FORMAT.format_id,
            "version": 1,
        }:
            raise StorageIntegrityError("FileStorage v1 manifest identity is invalid")

    files[FILE_STORAGE_MANIFEST] = canonical_json(
        {
            "format": FILE_STORAGE_FORMAT.format_id,
            "version": int(FILE_STORAGE_FORMAT.current_version),
        }
    )
    return PayloadSnapshot(
        content=LifecycleContentIdentity(
            kind=LifecycleTargetKind.FILE_STORAGE,
            status=LifecycleStatus.CURRENT,
            format_id=FILE_STORAGE_FORMAT.format_id,
            detected_version=FILE_STORAGE_FORMAT.current_version,
            current_version=FILE_STORAGE_FORMAT.current_version,
            fingerprint=fingerprint_files(files),
            file_count=len(files),
        ),
        files=files,
    )


def _upgrade_sqlite_snapshot(
    strategy_id: str,
    source: PayloadSnapshot,
) -> PayloadSnapshot:
    assert source.files is not None
    expected_strategy = SQLITE_UPGRADE_STRATEGIES.get(
        source.content.detected_version
    )
    if (
        strategy_id != expected_strategy
        or source.content.kind is not LifecycleTargetKind.SQLITE
        or source.content.status is not LifecycleStatus.MIGRATION_REQUIRED
        or source.content.current_version != SQLITE_FORMAT.current_version
        or set(source.files) != {"database.sqlite3"}
    ):
        raise LifecyclePlanError("SQLite upgrade source identity is invalid")

    migrated, result = _migrate_sqlite_bytes(source.files["database.sqlite3"])
    if (
        str(result.source_version) != source.content.detected_version
        or str(result.target_version) != source.content.current_version
    ):
        raise StorageIntegrityError("SQLite upgrade result has the wrong schema")
    return PayloadSnapshot(
        content=LifecycleContentIdentity(
            kind=LifecycleTargetKind.SQLITE,
            status=LifecycleStatus.CURRENT,
            format_id=SQLITE_FORMAT.format_id,
            detected_version=SQLITE_FORMAT.current_version,
            current_version=SQLITE_FORMAT.current_version,
            fingerprint=result.semantic_digest,
            file_count=1,
        ),
        files={"database.sqlite3": migrated},
    )


def _upgrade_memory_pack_snapshot(
    strategy_id: str,
    source: PayloadSnapshot,
) -> PayloadSnapshot:
    assert source.files is not None
    expected_strategy = (
        f"{MEMORY_PACK_STRATEGY_PREFIX}{source.content.detected_version}"
        f"-to-{MEMORY_PACK_FORMAT.current_version}"
    )
    if (
        strategy_id != expected_strategy
        or source.content.kind is not LifecycleTargetKind.MEMORY_PACK
        or source.content.status is not LifecycleStatus.MIGRATION_REQUIRED
        or source.content.current_version != MEMORY_PACK_FORMAT.current_version
        or set(source.files) != {"memory-pack.erii"}
    ):
        raise LifecyclePlanError("MemoryPack upgrade source identity is invalid")
    try:
        pack = MemoryPack.from_json(source.files["memory-pack.erii"].decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise StorageIntegrityError("MemoryPack upgrade source is malformed") from exc
    if pack.version != source.content.detected_version:
        raise StorageIntegrityError("MemoryPack upgrade version changed during capture")
    validate_memory_pack_semantic_graph(pack)

    pack.version = MEMORY_PACK_FORMAT.current_version
    upgraded_content = pack.to_json().encode("utf-8")
    try:
        verified = MemoryPack.from_json(upgraded_content.decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise StorageIntegrityError("MemoryPack upgrade result is malformed") from exc
    if verified.version != MEMORY_PACK_FORMAT.current_version:
        raise StorageIntegrityError("MemoryPack upgrade result has the wrong version")
    validate_memory_pack_semantic_graph(verified)

    return PayloadSnapshot(
        content=LifecycleContentIdentity(
            kind=LifecycleTargetKind.MEMORY_PACK,
            status=LifecycleStatus.CURRENT,
            format_id=MEMORY_PACK_FORMAT.format_id,
            detected_version=MEMORY_PACK_FORMAT.current_version,
            current_version=MEMORY_PACK_FORMAT.current_version,
            fingerprint=hashlib.sha256(upgraded_content).hexdigest(),
            file_count=1,
        ),
        files={"memory-pack.erii": upgraded_content},
    )


__all__ = ["upgrade_snapshot"]
