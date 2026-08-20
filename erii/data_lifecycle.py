"""Inspection, durable planning, backup, upgrade, and restore for v0.4."""

from contextlib import ExitStack, closing, contextmanager
import ctypes
import errno
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import sys
from typing import Dict

from erii.compatibility import (
    FILE_STORAGE_FORMAT,
    LIFECYCLE_BACKUP_FORMAT,
    LIFECYCLE_PLAN_FORMAT,
)
from erii.errors import (
    LifecycleConflictError,
    LifecyclePlanError,
    LifecycleVerificationError,
    StaleLifecyclePlanError,
    StorageIntegrityError,
    StorageWriteError,
    UnsupportedFormatError,
)
from erii.models.pack import MemoryPack
from erii.lifecycle_erasure_contracts import (
    ErasureInventory,
    ErasureScope,
    ErasureSelector,
    ErasureTransformResult,
    RelationshipRebuildProof,
)
from erii.lifecycle_memory_pack_import_contracts import (
    MemoryPackStagingAdapter,
    MemoryPackStagingImportReport,
)
from erii._lifecycle.plan_codec import (
    canonical_json as _canonical_json,
    decode_strict_json as _decode_strict_json,
    erasure_storage_kind as _erasure_storage_kind,
    validate_assessment as _validate_assessment,
)
from erii._lifecycle.inspection import (
    FILE_STORAGE_MANIFEST,
    LIFECYCLE_BACKUP_MANIFEST,
    BackupBundle as _BackupBundle,
    LifecycleInspector,
    MAX_LIFECYCLE_BACKUP_MANIFEST_BYTES,
    assessment_matches_content as _assessment_matches_content,
    read_backup_bundle as _read_backup_bundle,
    require_complete_bundle as _require_complete_bundle,
)
from erii._lifecycle.filesystem import (
    assert_no_link_or_reparse_ancestors as _assert_no_link_or_reparse_ancestors,
    copy_regular_file_exclusive,
    directory_identity as _directory_identity,
    lexists as _lexists,
    lstat as _lstat,
    read_stable_bytes as _read_stable_bytes,
    require_regular_directory as _require_regular_directory,
    require_regular_file as _require_regular_file,
    scan_directory_entries as _scan_directory_entries,
    stat_is_link_or_reparse as _stat_is_link_or_reparse,
    stat_object_identity as _stat_object_identity,
    stat_signature as _stat_signature,
    validate_relative_payload_path as _validate_relative_payload_path,
)
from erii._lifecycle.serializers import (
    content_to_dict as _content_to_dict,
)
from erii._lifecycle.snapshots import (
    MAX_LIFECYCLE_MEMORY_PACK_BYTES,
    MAX_LIFECYCLE_TRANSFORM_BYTES,
    PayloadSnapshot as _PayloadSnapshot,
    capture_snapshot as _capture_snapshot,
    materialize_snapshot as _materialize_snapshot,
    payload_entry_for_kind as _payload_entry_for_kind,
)
from erii._lifecycle.sqlite_semantics import (
    read_sqlite_schema_version as read_sqlite_schema_version,
)
from erii._lifecycle.topology import (
    require_destinations_do_not_overlap as _require_destinations_do_not_overlap,
    require_safe_destination as _require_safe_destination,
)
from erii._lifecycle.memory_pack_validation import (
    validate_memory_pack_semantic_graph as _validate_memory_pack_semantic_graph,
)
from erii._lifecycle.planning import LifecyclePlanner
from erii._lifecycle.upgrade_preview import upgrade_snapshot as _upgrade_snapshot
from erii._lifecycle.contracts import (
    BackupRequest, EraseRequest, LifecycleAssessment,
    LifecycleContentIdentity, LifecycleDirectoryIdentity, LifecycleOperation,
    LifecycleOutcome, LifecyclePlan, LifecyclePlanSelector, LifecycleReport,
    LifecycleRequest, LifecycleStatus, LifecycleTarget, LifecycleTargetKind,
    MemoryPackImportOptions, MemoryPackImportRequest, RebuildRequest,
    RestoreRequest, UpgradeRequest,
)
LIFECYCLE_PLAN_CONTRACT_VERSION = LIFECYCLE_PLAN_FORMAT.current_version


def _snapshot_file_manifest(
    snapshot: _PayloadSnapshot,
) -> list[Dict[str, object]]:
    if snapshot.files is not None:
        return _payload_file_manifest(snapshot.files)
    assert snapshot.identities is not None
    return [
        {
            "path": relative_name,
            "size": snapshot.identities[relative_name].size,
            "sha256": snapshot.identities[relative_name].sha256,
        }
        for relative_name in sorted(snapshot.identities)
    ]


def _write_snapshot_file(
    snapshot: _PayloadSnapshot,
    relative_name: str,
    destination: Path,
) -> None:
    if snapshot.files is not None:
        _write_durable_file(destination, snapshot.files[relative_name])
        return
    assert snapshot.source_paths is not None and snapshot.identities is not None
    try:
        copy_regular_file_exclusive(
            snapshot.source_paths[relative_name],
            destination,
            expected=snapshot.identities[relative_name],
        )
    except FileExistsError as exc:
        raise StorageWriteError("lifecycle snapshot destination already exists") from exc


def _write_snapshot_files(root: Path, snapshot: _PayloadSnapshot) -> None:
    names = (
        snapshot.files.keys()
        if snapshot.files is not None
        else snapshot.source_paths.keys()  # type: ignore[union-attr]
    )
    for relative_name in sorted(names):
        relative = PurePosixPath(relative_name)
        _validate_relative_payload_path(relative_name)
        _ensure_private_payload_parent(root, relative)
        _write_snapshot_file(
            snapshot,
            relative_name,
            root.joinpath(*relative.parts),
        )


class _LifecycleFormatAdapter:
    kind: LifecycleTargetKind

    def restored_target(self, staging_path: Path) -> LifecycleTarget:
        return LifecycleTarget(self.kind, str(staging_path))

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        raise NotImplementedError


class _FileLifecycleAdapter(_LifecycleFormatAdapter):
    kind = LifecycleTargetKind.FILE_STORAGE

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        _create_private_directory(staging_path)
        _write_snapshot_files(staging_path, snapshot)


class _SQLiteLifecycleAdapter(_LifecycleFormatAdapter):
    kind = LifecycleTargetKind.SQLITE

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        _write_snapshot_file(snapshot, "database.sqlite3", staging_path)


class _MemoryPackLifecycleAdapter(_LifecycleFormatAdapter):
    kind = LifecycleTargetKind.MEMORY_PACK

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        _write_snapshot_file(snapshot, "memory-pack.erii", staging_path)


_FORMAT_ADAPTERS: Dict[LifecycleTargetKind, _LifecycleFormatAdapter] = {
    LifecycleTargetKind.FILE_STORAGE: _FileLifecycleAdapter(),
    LifecycleTargetKind.SQLITE: _SQLiteLifecycleAdapter(),
    LifecycleTargetKind.MEMORY_PACK: _MemoryPackLifecycleAdapter(),
}


def _adapter_for_kind(kind: LifecycleTargetKind) -> _LifecycleFormatAdapter:
    try:
        return _FORMAT_ADAPTERS[kind]
    except KeyError as exc:
        raise LifecyclePlanError(f"no live-data lifecycle adapter for {kind.value!r}") from exc


def _write_durable_file(path: Path, content: bytes) -> None:
    descriptor = -1
    try:
        _assert_no_link_or_reparse_ancestors(
            path.parent,
            label="staged lifecycle file parent",
        )
        _require_regular_directory(path.parent, label="staged lifecycle file parent")
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        opened = os.fstat(descriptor)
        linked = os.lstat(path)
        if (
            _stat_is_link_or_reparse(opened)
            or _stat_is_link_or_reparse(linked)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _stat_object_identity(opened) != _stat_object_identity(linked)
        ):
            raise StorageWriteError("staged lifecycle path is not a private regular file")
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "short lifecycle staging write")
            view = view[written:]
        os.fsync(descriptor)
        final_open = os.fstat(descriptor)
        _assert_no_link_or_reparse_ancestors(
            path,
            label="staged lifecycle file",
        )
        final_path = _require_regular_file(path, label="staged lifecycle file")
        if not (
            _stat_object_identity(opened)
            == _stat_object_identity(final_open)
            == _stat_object_identity(final_path)
        ) or final_open.st_size != len(content):
            raise StorageWriteError("staged lifecycle file changed while it was written")
    except StorageWriteError:
        raise
    except StorageIntegrityError as exc:
        raise StorageWriteError(f"could not safely write staged file {path.name!r}") from exc
    except OSError as exc:
        raise StorageWriteError(f"could not write staged lifecycle file {path.name!r}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _create_private_directory(path: Path) -> None:
    try:
        _assert_no_link_or_reparse_ancestors(
            path.parent,
            label="staged lifecycle directory parent",
        )
        os.mkdir(path, 0o700)
        _require_regular_directory(path, label="staged lifecycle directory")
        _assert_no_link_or_reparse_ancestors(
            path,
            label="staged lifecycle directory",
        )
    except FileExistsError as exc:
        raise LifecycleConflictError("staged lifecycle directory was occupied") from exc
    except StorageIntegrityError as exc:
        raise StorageWriteError("could not safely create staged lifecycle directory") from exc
    except OSError as exc:
        raise StorageWriteError("could not create staged lifecycle directory") from exc


def _ensure_private_payload_parent(root: Path, relative: PurePosixPath) -> None:
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if not _lexists(current):
            _create_private_directory(current)
        else:
            try:
                _require_regular_directory(current, label="staged payload directory")
                _assert_no_link_or_reparse_ancestors(
                    current,
                    label="staged payload directory",
                )
            except StorageIntegrityError as exc:
                raise StorageWriteError("staged payload directory is unsafe") from exc


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically publishes one staged path without replacing an existing name."""

    if os.name == "nt":
        try:
            os.rename(source, destination)
        except OSError as exc:
            if (
                isinstance(exc, FileExistsError)
                or exc.errno
                in {
                    errno.EEXIST,
                    errno.ENOTEMPTY,
                }
                or getattr(exc, "winerror", None) in {80, 183}
            ):
                raise LifecycleConflictError(
                    "lifecycle destination changed before publication"
                ) from exc
            raise
        return
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform.startswith("linux"):
        try:
            renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
        except AttributeError as exc:
            raise StorageWriteError(
                "this platform cannot atomically publish lifecycle data without replacement"
            ) from exc
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, source_bytes, -100, destination_bytes, 1)
    elif sys.platform == "darwin":
        try:
            renamex_np = ctypes.CDLL(None, use_errno=True).renamex_np
        except AttributeError as exc:
            raise StorageWriteError(
                "this platform cannot atomically publish lifecycle data without replacement"
            ) from exc
        renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_bytes, destination_bytes, 0x00000004)
    else:
        raise StorageWriteError(
            "this platform cannot atomically publish lifecycle data without replacement"
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise LifecycleConflictError("lifecycle destination changed before publication")
    raise OSError(error_number, os.strerror(error_number), os.fspath(destination))


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if os.name == "nt" and (
            isinstance(exc, PermissionError)
            or exc.errno in {errno.EACCES, errno.EINVAL}
            or getattr(exc, "winerror", None) in {1, 5, 6, 87}
        ):
            # CPython cannot portably open a directory handle for FlushFileBuffers.
            # File contents remain fsynced; directory-entry persistence is best effort.
            return
        raise StorageWriteError(f"could not open lifecycle directory {path.name!r}") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if os.name == "nt" and (
            exc.errno in {errno.EBADF, errno.EINVAL} or getattr(exc, "winerror", None) in {1, 6, 87}
        ):
            return
        raise StorageWriteError(f"could not synchronize lifecycle directory {path.name!r}") from exc
    finally:
        os.close(descriptor)


def _fsync_tree_directories(root: Path) -> None:
    _, directories = _scan_directory_entries(root, label="staged lifecycle tree")
    paths = [root.joinpath(*PurePosixPath(name).parts) for name in directories]
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(path)
    _fsync_directory(root)


def _payload_file_manifest(files: Dict[str, bytes]) -> list[Dict[str, object]]:
    return [
        {
            "path": relative_name,
            "size": len(files[relative_name]),
            "sha256": hashlib.sha256(files[relative_name]).hexdigest(),
        }
        for relative_name in sorted(files)
    ]


def _backup_manifest(plan: LifecyclePlan, snapshot: _PayloadSnapshot) -> Dict[str, object]:
    return {
        "format": LIFECYCLE_BACKUP_FORMAT.format_id,
        "version": LIFECYCLE_BACKUP_FORMAT.current_version,
        "operation_id": plan.operation_id,
        "plan_digest": plan.plan_digest,
        "source": _content_to_dict(snapshot.content),
        "payload": {
            "entry": _payload_entry_for_kind(snapshot.content.kind),
            "files": _snapshot_file_manifest(snapshot),
            "tree_fingerprint": snapshot.content.fingerprint,
        },
    }


def _assert_plan_destination_topology(plan: LifecyclePlan) -> None:
    if plan.operation in {LifecycleOperation.ERASE, LifecycleOperation.REBUILD}:
        if plan.destination.target != plan.source.target:
            raise LifecyclePlanError("in-place lifecycle target identity is invalid")
        try:
            current_parent = _directory_identity(Path(plan.destination.target.path).parent)
        except StorageIntegrityError as exc:
            raise StaleLifecyclePlanError(
                "lifecycle source parent became unsafe"
            ) from exc
        if current_parent != plan.destination_parent:
            raise StaleLifecyclePlanError(
                "lifecycle source parent changed after planning"
            )
    else:
        try:
            current_parent = _require_safe_destination(
                source=plan.source.target,
                destination=plan.destination.target,
            )
        except LifecyclePlanError as exc:
            raise StaleLifecyclePlanError(
                "lifecycle source/destination topology became unsafe"
            ) from exc
        if current_parent != plan.destination_parent:
            raise StaleLifecyclePlanError(
                "lifecycle destination parent changed after planning"
            )
    if plan.operation in {
        LifecycleOperation.UPGRADE,
        LifecycleOperation.ERASE,
        LifecycleOperation.REBUILD,
    }:
        if plan.backup_destination is None or plan.backup_destination_parent is None:
            raise LifecyclePlanError("lifecycle plan backup topology is incomplete")
        try:
            current_backup_parent = _require_safe_destination(
                source=plan.source.target,
                destination=plan.backup_destination.target,
            )
            if plan.operation is LifecycleOperation.UPGRADE:
                _require_destinations_do_not_overlap(
                    plan.destination.target,
                    plan.backup_destination.target,
                )
        except LifecyclePlanError as exc:
            raise StaleLifecyclePlanError(
                "lifecycle backup destination topology became unsafe"
            ) from exc
        if current_backup_parent != plan.backup_destination_parent:
            raise StaleLifecyclePlanError(
                "lifecycle backup destination parent changed after planning"
            )


def _owner_document(plan: LifecyclePlan) -> bytes:
    return _canonical_json({"operation_id": plan.operation_id, "plan_digest": plan.plan_digest})


def _read_owner(path: Path) -> Dict[str, object]:
    try:
        document = _decode_strict_json(
            _read_stable_bytes(path).decode("utf-8"),
            label="lifecycle staging owner",
        )
    except (OSError, StorageIntegrityError, UnicodeDecodeError, ValueError) as exc:
        raise LifecycleConflictError("lifecycle staging ownership is unreadable") from exc
    if not isinstance(document, dict) or set(document) != {"operation_id", "plan_digest"}:
        raise LifecycleConflictError("lifecycle staging ownership is invalid")
    return document


def _remove_staging_path(path: Path) -> None:
    if not _lexists(path):
        return
    info = _lstat(path, label="lifecycle staging path")
    if _stat_is_link_or_reparse(info):
        raise LifecycleConflictError("refusing to remove a linked lifecycle staging path")
    try:
        if stat.S_ISDIR(info.st_mode):
            _scan_directory_entries(path, label="owned lifecycle staging tree")
            shutil.rmtree(path)
        elif stat.S_ISREG(info.st_mode):
            path.unlink()
        else:
            raise LifecycleConflictError("refusing to remove a non-regular staging path")
    except OSError as exc:
        raise LifecycleConflictError("could not clean an owned lifecycle staging path") from exc


def _staging_auxiliary_paths(
    plan: LifecyclePlan,
    staging: Path,
) -> tuple[Path, ...]:
    if (
        plan.operation is LifecycleOperation.IMPORT
        and plan.destination.target.kind is LifecycleTargetKind.SQLITE
    ):
        return (Path(f"{staging}.relationship_processing_locks"),)
    return ()


def _is_sqlite_relationship_processing_lock(relative_name: str) -> bool:
    path = PurePosixPath(relative_name)
    if len(path.parts) != 1 or not path.name.endswith(".lock"):
        return False
    digest = path.name.removesuffix(".lock")
    return len(digest) == 64 and all(
        character in "0123456789abcdef"
        for character in digest
    )


def _remove_legacy_sqlite_staging_locks(path: Path) -> None:
    try:
        files, directories = _scan_directory_entries(
            path,
            label="legacy SQLite staging relationship locks",
        )
    except StorageIntegrityError as exc:
        raise LifecycleConflictError(
            "legacy SQLite staging locks are not a private runtime directory"
        ) from exc
    if directories or any(
        not _is_sqlite_relationship_processing_lock(name)
        or signature[3] != 1
        or signature[5] != 1
        for name, signature in files.items()
    ):
        raise LifecycleConflictError(
            "legacy SQLite staging locks contain non-runtime data"
        )
    try:
        if any(
            _read_stable_bytes(path / name) != b"\0"
            for name in files
        ):
            raise LifecycleConflictError(
                "legacy SQLite staging locks contain non-runtime data"
            )
    except StorageIntegrityError as exc:
        raise LifecycleConflictError(
            "legacy SQLite staging locks changed during inspection"
        ) from exc
    try:
        _remove_staging_path(path)
    except StorageIntegrityError as exc:
        raise LifecycleConflictError(
            "legacy SQLite staging locks changed during cleanup"
        ) from exc
    _fsync_directory(path.parent)


def _prepare_staging(plan: LifecyclePlan, staging: Path, owner: Path) -> None:
    expected_owner = {
        "operation_id": plan.operation_id,
        "plan_digest": plan.plan_digest,
    }
    auxiliary_paths = _staging_auxiliary_paths(plan, staging)
    if not _lexists(staging) and not _lexists(owner):
        for path in auxiliary_paths:
            if not _lexists(path):
                continue
            if not _lexists(Path(plan.destination.target.path)):
                raise LifecycleConflictError(
                    "unowned lifecycle staging data remains"
                )
            _remove_legacy_sqlite_staging_locks(path)
    if (
        _lexists(staging)
        or _lexists(owner)
        or any(_lexists(path) for path in auxiliary_paths)
    ):
        if not _lexists(owner):
            raise LifecycleConflictError("lifecycle staging path belongs to another operation")
        try:
            _require_regular_file(owner, label="lifecycle staging ownership")
        except StorageIntegrityError as exc:
            raise LifecycleConflictError(
                "lifecycle staging path belongs to another operation"
            ) from exc
        if _read_owner(owner) != expected_owner:
            raise LifecycleConflictError("lifecycle staging path belongs to another operation")
        _remove_staging_path(staging)
        for path in auxiliary_paths:
            _remove_staging_path(path)
        try:
            owner.unlink()
        except OSError as exc:
            raise LifecycleConflictError("could not clear lifecycle staging ownership") from exc
    _write_durable_file(owner, _owner_document(plan))
    _fsync_directory(owner.parent)


def _cleanup_staging(plan: LifecyclePlan, staging: Path, owner: Path) -> None:
    auxiliary_paths = _staging_auxiliary_paths(plan, staging)
    if _lexists(owner):
        try:
            _require_regular_file(owner, label="lifecycle staging ownership")
        except StorageIntegrityError as exc:
            raise LifecycleConflictError("lifecycle staging ownership changed") from exc
        expected = {"operation_id": plan.operation_id, "plan_digest": plan.plan_digest}
        if _read_owner(owner) != expected:
            raise LifecycleConflictError("lifecycle staging ownership changed")
        _remove_staging_path(staging)
        for path in auxiliary_paths:
            _remove_staging_path(path)
        try:
            owner.unlink()
        except OSError as exc:
            raise LifecycleConflictError("could not clear lifecycle staging ownership") from exc
        _fsync_directory(owner.parent)
    elif _lexists(staging) or any(_lexists(path) for path in auxiliary_paths):
        raise LifecycleConflictError("unowned lifecycle staging data remains")


@contextmanager
def _destination_lock(destination: Path):
    lock_name = f".{destination.name}.erii-lifecycle.lock"
    lock_path = destination.parent / lock_name
    descriptor = -1
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        opened = os.fstat(descriptor)
        linked = os.lstat(lock_path)
        if (
            _stat_is_link_or_reparse(opened)
            or _stat_is_link_or_reparse(linked)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _stat_signature(opened) != _stat_signature(linked)
        ):
            raise LifecycleConflictError("lifecycle destination lock is not a private file")
        handle = os.fdopen(descriptor, "r+b", closefd=True)
        descriptor = -1
    except LifecycleConflictError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise LifecycleConflictError("could not open lifecycle destination lock") from exc
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
            _fsync_directory(lock_path.parent)
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise LifecycleConflictError("lifecycle destination is busy") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise LifecycleConflictError("lifecycle destination is busy") from exc
        locked = True
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
        # Keep the non-sensitive lock file. Removing a shared lock pathname after
        # unlocking allows a third process to create a new inode while another
        # waiter still holds the old one, which would split the exclusion domain.


@contextmanager
def _destination_locks(*destinations: Path):
    unique = {
        os.path.normcase(os.path.abspath(str(destination))): destination
        for destination in destinations
    }
    with ExitStack() as stack:
        for key in sorted(unique):
            stack.enter_context(_destination_lock(unique[key]))
        yield


def _stage_paths(destination: Path, plan: LifecyclePlan) -> tuple[Path, Path]:
    stem = f".{destination.name}.{plan.operation_id[:12]}.{plan.operation.value}.tmp"
    staging = destination.parent / stem
    return staging, destination.parent / f"{stem}.owner"


def _recovery_path(destination: Path, plan: LifecyclePlan) -> Path:
    return destination.parent / (
        f".{destination.name}.{plan.operation_id[:12]}.{plan.operation.value}.recovery"
    )


def _quiesce_sqlite_staging(path: Path) -> None:
    """Checkpoints a private SQLite artifact and removes only empty sidecars."""
    try:
        with closing(sqlite3.connect(str(path))) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            connection.commit()
    except sqlite3.Error as exc:
        raise StorageIntegrityError("staged SQLite database could not be checkpointed") from exc
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        if not _lexists(sidecar):
            continue
        info = _require_regular_file(sidecar, label="staged SQLite sidecar")
        if suffix in {"-wal", "-journal"} and info.st_size:
            raise StorageIntegrityError("staged SQLite sidecar still contains pending data")
        try:
            sidecar.unlink()
        except OSError as exc:
            raise StorageWriteError("staged SQLite sidecar could not be removed") from exc
    _fsync_directory(path.parent)


def _with_lifecycle_backup_inventory(
    result: ErasureTransformResult,
) -> ErasureTransformResult:
    counts = result.inventory.to_dict()
    counts["unverified_external"]["lifecycle_backup"] = 1
    return ErasureTransformResult(
        storage_kind=result.storage_kind,
        selector=result.selector,
        affected_relationship_ids=result.affected_relationship_ids,
        rebuild_proofs=result.rebuild_proofs,
        inventory=ErasureInventory(counts=counts),
    )


def _same_assessment(
    expected: LifecycleAssessment,
    actual: LifecycleAssessment,
) -> bool:
    return expected == actual


def _matching_backup_for_plan(bundle: _BackupBundle, plan: LifecyclePlan) -> bool:
    expected_content = (
        LifecycleContentIdentity.from_assessment(plan.source)
        if plan.operation is LifecycleOperation.UPGRADE
        else plan.content
    )
    return (
        bundle.assessment.status is LifecycleStatus.CURRENT
        and bundle.operation_id == plan.operation_id
        and bundle.plan_digest == plan.plan_digest
        and bundle.content == expected_content
        and bundle.snapshot is not None
    )


def _report(
    plan: LifecyclePlan,
    *,
    outcome: LifecycleOutcome,
    artifact_fingerprint: str,
    file_count: int | None = None,
    details: ErasureTransformResult | MemoryPackStagingImportReport | None = None,
) -> LifecycleReport:
    return LifecycleReport(
        operation_id=plan.operation_id,
        plan_digest=plan.plan_digest,
        operation=plan.operation,
        outcome=outcome,
        content_fingerprint=plan.content.fingerprint,
        artifact_fingerprint=artifact_fingerprint,
        file_count=plan.content.file_count if file_count is None else file_count,
        details=details,
    )


def _published_target_recovery_status() -> str:
    # Once a target name is visible, another host may already have opened or
    # extended it. Automatic rollback could therefore destroy newer host data.
    return "published_target_preserved_manual_cleanup_required"


class DataLifecycleCoordinator:
    """Deep lifecycle Module for inspection, durable planning, and execution."""

    def __init__(self) -> None:
        self._inspector = LifecycleInspector()
        self._planner = LifecyclePlanner(self._inspector)

    def inspect(self, target: LifecycleTarget) -> LifecycleAssessment:
        """Inspects a live target or backup bundle without writing to it."""
        assessment = self._inspector.inspect(target)
        _validate_assessment(assessment)
        return assessment

    def plan(self, request: LifecycleRequest) -> LifecyclePlan:
        """Freezes a zero-write, strictly serializable lifecycle plan."""
        return self._planner.plan(request)

    def execute(self, plan: LifecyclePlan) -> LifecycleReport:
        """Executes and terminally verifies an immutable lifecycle plan."""
        if not isinstance(plan, LifecyclePlan):
            raise TypeError("execute() requires a LifecyclePlan")
        validated = LifecyclePlan.from_json(plan.to_json())
        if validated != plan:
            raise LifecyclePlanError("lifecycle plan is not canonical")
        if plan.operation is LifecycleOperation.BACKUP:
            return self._execute_backup(plan)
        if plan.operation is LifecycleOperation.RESTORE:
            return self._execute_restore(plan)
        if plan.operation is LifecycleOperation.UPGRADE:
            return self._execute_upgrade(plan)
        if plan.operation in {LifecycleOperation.ERASE, LifecycleOperation.REBUILD}:
            return self._execute_erasure_or_rebuild(plan)
        if plan.operation is LifecycleOperation.IMPORT:
            return self._execute_memory_pack_import(plan)
        raise LifecyclePlanError("lifecycle plan operation is unsupported")

    def _ensure_verified_prechange_backup(
        self,
        plan: LifecyclePlan,
        *,
        backup_staging: Path,
        backup_owner: Path,
    ) -> _BackupBundle:
        """Returns the plan-bound backup, publishing it only from the exact source."""
        if plan.backup_destination is None:
            raise LifecyclePlanError("lifecycle mutation has no backup destination")
        backup_destination = Path(plan.backup_destination.target.path)
        if _lexists(backup_destination):
            try:
                bundle = _require_complete_bundle(
                    _read_backup_bundle(plan.backup_destination.target)
                )
            except (
                LifecyclePlanError,
                StorageIntegrityError,
                UnsupportedFormatError,
            ) as exc:
                raise LifecycleConflictError(
                    "lifecycle backup destination contains a damaged artifact"
                ) from exc
            if not _matching_backup_for_plan(bundle, plan):
                raise LifecycleConflictError(
                    "lifecycle backup destination belongs to a different plan"
                )
            _cleanup_staging(plan, backup_staging, backup_owner)
            return bundle

        current_source = self.inspect(plan.source.target)
        if not _same_assessment(plan.source, current_source):
            raise StaleLifecyclePlanError(
                "lifecycle source changed before its required backup"
            )
        source_snapshot = _capture_snapshot(current_source)
        final_source = self.inspect(plan.source.target)
        if not _same_assessment(plan.source, final_source):
            raise StaleLifecyclePlanError(
                "lifecycle source changed during required backup capture"
            )

        _prepare_staging(plan, backup_staging, backup_owner)
        backup_published = False
        try:
            _create_private_directory(backup_staging)
            payload_root = backup_staging / "payload"
            _create_private_directory(payload_root)
            _write_snapshot_files(payload_root, source_snapshot)
            _fsync_tree_directories(payload_root)
            _write_durable_file(
                backup_staging / LIFECYCLE_BACKUP_MANIFEST,
                _canonical_json(_backup_manifest(plan, source_snapshot)),
            )
            _fsync_directory(backup_staging)
            staged = _require_complete_bundle(
                _read_backup_bundle(
                    LifecycleTarget(
                        LifecycleTargetKind.BACKUP,
                        str(backup_staging),
                    )
                )
            )
            if not _matching_backup_for_plan(staged, plan):
                raise LifecycleVerificationError(
                    "staged lifecycle backup does not match its plan",
                    recovery_status="source_unchanged",
                )
            _assert_plan_destination_topology(plan)
            try:
                _rename_no_replace(backup_staging, backup_destination)
            except LifecycleConflictError:
                raise
            except OSError as exc:
                raise StorageWriteError("lifecycle backup publication failed") from exc
            backup_published = True
            _fsync_directory(backup_destination.parent)
            bundle = _require_complete_bundle(
                _read_backup_bundle(plan.backup_destination.target)
            )
            if not _matching_backup_for_plan(bundle, plan):
                raise LifecycleVerificationError(
                    "published lifecycle backup does not match its plan",
                    recovery_status=_published_target_recovery_status(),
                )
            _cleanup_staging(plan, backup_staging, backup_owner)
            return bundle
        except Exception:
            if not backup_published:
                _cleanup_staging(plan, backup_staging, backup_owner)
            raise

    def _execute_backup(self, plan: LifecyclePlan) -> LifecycleReport:
        destination = Path(plan.destination.target.path)
        staging, owner = _stage_paths(destination, plan)
        _assert_plan_destination_topology(plan)
        with _destination_lock(destination):
            _assert_plan_destination_topology(plan)
            if _lexists(destination):
                try:
                    existing = _read_backup_bundle(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleConflictError(
                        "backup destination contains a different or damaged artifact"
                    ) from exc
                if not _matching_backup_for_plan(existing, plan):
                    raise LifecycleConflictError(
                        "backup destination belongs to a different lifecycle plan"
                    )
                _cleanup_staging(plan, staging, owner)
                assert existing.assessment.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.ALREADY_COMPLETE,
                    artifact_fingerprint=existing.assessment.fingerprint,
                )

            current_source = self.inspect(plan.source.target)
            if not _same_assessment(plan.source, current_source):
                raise StaleLifecyclePlanError("backup source changed after planning")
            snapshot = _capture_snapshot(current_source)
            if snapshot.content != plan.content:
                raise StaleLifecyclePlanError("captured backup no longer matches its plan")
            final_source = self.inspect(plan.source.target)
            if not _same_assessment(plan.source, final_source):
                raise StaleLifecyclePlanError("backup source changed during capture")

            _prepare_staging(plan, staging, owner)
            published = False
            try:
                _create_private_directory(staging)
                payload_root = staging / "payload"
                _create_private_directory(payload_root)
                _write_snapshot_files(payload_root, snapshot)
                _fsync_tree_directories(payload_root)
                manifest_bytes = _canonical_json(_backup_manifest(plan, snapshot))
                _write_durable_file(staging / LIFECYCLE_BACKUP_MANIFEST, manifest_bytes)
                _fsync_directory(staging)
                try:
                    staged_bundle = _read_backup_bundle(
                        LifecycleTarget(LifecycleTargetKind.BACKUP, str(staging))
                    )
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleVerificationError(
                        "staged lifecycle backup failed verification",
                        recovery_status="source_unchanged",
                    ) from exc
                if not _matching_backup_for_plan(staged_bundle, plan):
                    raise LifecycleVerificationError(
                        "staged lifecycle backup does not match its plan",
                        recovery_status="source_unchanged",
                    )
                _assert_plan_destination_topology(plan)
                try:
                    _rename_no_replace(staging, destination)
                except LifecycleConflictError:
                    raise
                except OSError as exc:
                    raise StorageWriteError("backup publication failed") from exc
                published = True
                _fsync_directory(destination.parent)
                try:
                    final_bundle = _read_backup_bundle(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleVerificationError(
                        "published lifecycle backup failed verification",
                        recovery_status=_published_target_recovery_status(),
                    ) from exc
                if not _matching_backup_for_plan(final_bundle, plan):
                    raise LifecycleVerificationError(
                        "published lifecycle backup does not match its plan",
                        recovery_status=_published_target_recovery_status(),
                    )
                _cleanup_staging(plan, staging, owner)
                assert final_bundle.assessment.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.APPLIED,
                    artifact_fingerprint=final_bundle.assessment.fingerprint,
                )
            except Exception:
                if not published:
                    _cleanup_staging(plan, staging, owner)
                raise

    def _execute_restore(self, plan: LifecyclePlan) -> LifecycleReport:
        destination = Path(plan.destination.target.path)
        staging, owner = _stage_paths(destination, plan)
        _assert_plan_destination_topology(plan)
        with _destination_lock(destination):
            _assert_plan_destination_topology(plan)
            current_bundle = _require_complete_bundle(_read_backup_bundle(plan.source.target))
            if not _same_assessment(plan.source, current_bundle.assessment):
                raise StaleLifecyclePlanError("backup bundle changed after planning")
            if current_bundle.content != plan.content or current_bundle.snapshot is None:
                raise StaleLifecyclePlanError("backup content no longer matches its restore plan")
            current_destination = self.inspect(plan.destination.target)
            if _assessment_matches_content(current_destination, plan.content):
                _cleanup_staging(plan, staging, owner)
                assert current_bundle.assessment.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.ALREADY_COMPLETE,
                    artifact_fingerprint=current_bundle.assessment.fingerprint,
                )
            if current_destination.status is not LifecycleStatus.MISSING:
                raise StaleLifecyclePlanError("restore destination changed after planning")

            adapter = _adapter_for_kind(plan.content.kind)
            _prepare_staging(plan, staging, owner)
            published = False
            try:
                adapter.write_restored(current_bundle.snapshot, staging)
                if staging.is_dir():
                    _fsync_tree_directories(staging)
                _fsync_directory(staging.parent)
                staged_assessment = self.inspect(adapter.restored_target(staging))
                if not _assessment_matches_content(staged_assessment, plan.content):
                    raise LifecycleVerificationError(
                        "staged restore does not match the backup content",
                        recovery_status="target_missing",
                    )
                _assert_plan_destination_topology(plan)
                try:
                    _rename_no_replace(staging, destination)
                except LifecycleConflictError:
                    raise
                except OSError as exc:
                    raise StorageWriteError("restore publication failed") from exc
                published = True
                _fsync_directory(destination.parent)
                try:
                    final_assessment = self.inspect(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleVerificationError(
                        "published restore failed verification",
                        recovery_status=_published_target_recovery_status(),
                    ) from exc
                if not _assessment_matches_content(final_assessment, plan.content):
                    raise LifecycleVerificationError(
                        "published restore does not match the backup content",
                        recovery_status=_published_target_recovery_status(),
                    )
                _cleanup_staging(plan, staging, owner)
                assert current_bundle.assessment.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.APPLIED,
                    artifact_fingerprint=current_bundle.assessment.fingerprint,
                )
            except Exception:
                if not published:
                    _cleanup_staging(plan, staging, owner)
                raise

    def _execute_upgrade(self, plan: LifecyclePlan) -> LifecycleReport:
        if plan.backup_destination is None or plan.backup_destination_parent is None:
            raise LifecyclePlanError("upgrade plan backup destination is incomplete")

        destination = Path(plan.destination.target.path)
        backup_destination = Path(plan.backup_destination.target.path)
        staging, owner = _stage_paths(destination, plan)
        backup_staging, backup_owner = _stage_paths(backup_destination, plan)
        _assert_plan_destination_topology(plan)

        with _destination_locks(backup_destination, destination):
            _assert_plan_destination_topology(plan)
            current_destination = self.inspect(plan.destination.target)
            if current_destination.status is not LifecycleStatus.MISSING:
                try:
                    existing_backup = _require_complete_bundle(
                        _read_backup_bundle(plan.backup_destination.target)
                    )
                except (
                    LifecyclePlanError,
                    StorageIntegrityError,
                    UnsupportedFormatError,
                ) as exc:
                    raise LifecycleConflictError(
                        "completed upgrade target has no matching verified backup"
                    ) from exc
                if not _assessment_matches_content(current_destination, plan.content) or not (
                    _matching_backup_for_plan(existing_backup, plan)
                ):
                    raise LifecycleConflictError(
                        "upgrade destination contains a different or damaged artifact"
                    )
                _cleanup_staging(plan, backup_staging, backup_owner)
                _cleanup_staging(plan, staging, owner)
                assert current_destination.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.ALREADY_COMPLETE,
                    artifact_fingerprint=current_destination.fingerprint,
                )

            current_source = self.inspect(plan.source.target)
            if not _same_assessment(plan.source, current_source):
                raise StaleLifecyclePlanError("upgrade source changed after planning")
            adapter = _adapter_for_kind(plan.source.target.kind)

            if _lexists(backup_destination):
                try:
                    backup_bundle = _require_complete_bundle(
                        _read_backup_bundle(plan.backup_destination.target)
                    )
                except (
                    LifecyclePlanError,
                    StorageIntegrityError,
                    UnsupportedFormatError,
                ) as exc:
                    raise LifecycleConflictError(
                        "upgrade backup destination contains a damaged artifact"
                    ) from exc
                if not _matching_backup_for_plan(backup_bundle, plan):
                    raise LifecycleConflictError(
                        "upgrade backup destination belongs to a different plan"
                    )
                _cleanup_staging(plan, backup_staging, backup_owner)
            else:
                source_snapshot = _capture_snapshot(current_source)
                final_source = self.inspect(plan.source.target)
                if not _same_assessment(plan.source, final_source):
                    raise StaleLifecyclePlanError("upgrade source changed during backup capture")

                _prepare_staging(plan, backup_staging, backup_owner)
                backup_published = False
                try:
                    _create_private_directory(backup_staging)
                    payload_root = backup_staging / "payload"
                    _create_private_directory(payload_root)
                    _write_snapshot_files(payload_root, source_snapshot)
                    _fsync_tree_directories(payload_root)
                    manifest_bytes = _canonical_json(_backup_manifest(plan, source_snapshot))
                    _write_durable_file(
                        backup_staging / LIFECYCLE_BACKUP_MANIFEST,
                        manifest_bytes,
                    )
                    _fsync_directory(backup_staging)
                    try:
                        staged_backup = _read_backup_bundle(
                            LifecycleTarget(
                                LifecycleTargetKind.BACKUP,
                                str(backup_staging),
                            )
                        )
                    except (StorageIntegrityError, UnsupportedFormatError) as exc:
                        raise LifecycleVerificationError(
                            "staged upgrade backup failed verification",
                            recovery_status="source_unchanged",
                        ) from exc
                    if not _matching_backup_for_plan(staged_backup, plan):
                        raise LifecycleVerificationError(
                            "staged upgrade backup does not match its plan",
                            recovery_status="source_unchanged",
                        )
                    _assert_plan_destination_topology(plan)
                    try:
                        _rename_no_replace(backup_staging, backup_destination)
                    except LifecycleConflictError:
                        raise
                    except OSError as exc:
                        raise StorageWriteError("upgrade backup publication failed") from exc
                    backup_published = True
                    _fsync_directory(backup_destination.parent)
                    try:
                        backup_bundle = _read_backup_bundle(plan.backup_destination.target)
                    except (StorageIntegrityError, UnsupportedFormatError) as exc:
                        raise LifecycleVerificationError(
                            "published upgrade backup failed verification",
                            recovery_status=_published_target_recovery_status(),
                        ) from exc
                    if not _matching_backup_for_plan(backup_bundle, plan):
                        raise LifecycleVerificationError(
                            "published upgrade backup does not match its plan",
                            recovery_status=_published_target_recovery_status(),
                        )
                    _cleanup_staging(plan, backup_staging, backup_owner)
                except Exception:
                    if not backup_published:
                        _cleanup_staging(plan, backup_staging, backup_owner)
                    raise

            if backup_bundle.snapshot is None:
                raise LifecycleVerificationError(
                    "verified upgrade backup has no restorable payload",
                    recovery_status="verified_backup_preserved_target_missing",
                )
            current_source = self.inspect(plan.source.target)
            if not _same_assessment(plan.source, current_source):
                raise StaleLifecyclePlanError(
                    "upgrade source changed after its backup was verified"
                )
            upgraded = _upgrade_snapshot(plan.strategy_id, backup_bundle.snapshot)
            if upgraded.content != plan.content:
                raise StaleLifecyclePlanError(
                    "upgrade result no longer matches its planned identity"
                )

            _prepare_staging(plan, staging, owner)
            published = False
            try:
                adapter.write_restored(upgraded, staging)
                if staging.is_dir():
                    _fsync_tree_directories(staging)
                _fsync_directory(staging.parent)
                staged_assessment = self.inspect(adapter.restored_target(staging))
                if not _assessment_matches_content(staged_assessment, plan.content):
                    raise LifecycleVerificationError(
                        "staged upgrade does not match its planned result",
                        recovery_status="verified_backup_preserved_target_missing",
                    )
                final_source = self.inspect(plan.source.target)
                if not _same_assessment(plan.source, final_source):
                    raise StaleLifecyclePlanError(
                        "upgrade source changed before target publication"
                    )
                current_backup = _require_complete_bundle(
                    _read_backup_bundle(plan.backup_destination.target)
                )
                if not _matching_backup_for_plan(current_backup, plan):
                    raise StaleLifecyclePlanError(
                        "upgrade backup changed before target publication"
                    )
                _assert_plan_destination_topology(plan)
                try:
                    _rename_no_replace(staging, destination)
                except LifecycleConflictError:
                    raise
                except OSError as exc:
                    raise StorageWriteError("upgrade publication failed") from exc
                published = True
                _fsync_directory(destination.parent)
                try:
                    final_assessment = self.inspect(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleVerificationError(
                        "published upgrade failed verification",
                        recovery_status=_published_target_recovery_status(),
                    ) from exc
                if not _assessment_matches_content(final_assessment, plan.content):
                    raise LifecycleVerificationError(
                        "published upgrade does not match its planned result",
                        recovery_status=_published_target_recovery_status(),
                    )
                _cleanup_staging(plan, staging, owner)
                assert final_assessment.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.APPLIED,
                    artifact_fingerprint=final_assessment.fingerprint,
                )
            except Exception:
                if not published:
                    _cleanup_staging(plan, staging, owner)
                raise

    def _execute_erasure_or_rebuild(self, plan: LifecyclePlan) -> LifecycleReport:
        if (
            plan.backup_destination is None
            or plan.backup_destination_parent is None
            or not isinstance(plan.selector, ErasureSelector)
        ):
            raise LifecyclePlanError("erase or rebuild plan is incomplete")

        destination = Path(plan.destination.target.path)
        backup_destination = Path(plan.backup_destination.target.path)
        staging, owner = _stage_paths(destination, plan)
        backup_staging, backup_owner = _stage_paths(backup_destination, plan)
        recovery = _recovery_path(destination, plan)
        adapter = _adapter_for_kind(plan.source.target.kind)
        storage_kind = _erasure_storage_kind(plan.source.target.kind)
        _assert_plan_destination_topology(plan)

        with _destination_locks(backup_destination, destination):
            _assert_plan_destination_topology(plan)

            # A crash between the two same-directory renames leaves the exact
            # original under the deterministic recovery name. Restore it before
            # re-entering the ordinary backup-first path.
            if _lexists(recovery) and not _lexists(destination):
                if not _lexists(backup_destination):
                    raise LifecycleConflictError(
                        "lifecycle recovery exists without its verified backup"
                    )
                recovery_assessment = self.inspect(
                    LifecycleTarget(plan.source.target.kind, str(recovery))
                )
                if not _assessment_matches_content(recovery_assessment, plan.content):
                    raise LifecycleConflictError(
                        "lifecycle recovery does not match the planned source"
                    )
                recovery_bundle = _require_complete_bundle(
                    _read_backup_bundle(plan.backup_destination.target)
                )
                if not _matching_backup_for_plan(recovery_bundle, plan):
                    raise LifecycleConflictError(
                        "lifecycle recovery backup does not match the plan"
                    )
                try:
                    _rename_no_replace(recovery, destination)
                except OSError as exc:
                    raise StorageWriteError(
                        "lifecycle recovery restoration failed"
                    ) from exc
                _fsync_directory(destination.parent)

            backup_preexisted = _lexists(backup_destination)
            backup_bundle = self._ensure_verified_prechange_backup(
                plan,
                backup_staging=backup_staging,
                backup_owner=backup_owner,
            )
            if backup_bundle.snapshot is None:
                raise LifecycleVerificationError(
                    "verified lifecycle backup has no restorable payload",
                    recovery_status="verified_backup_preserved",
                )

            _prepare_staging(plan, staging, owner)
            try:
                adapter.write_restored(backup_bundle.snapshot, staging)
                from erii.lifecycle_erasure import (
                    erase_staged_storage,
                    rebuild_staged_storage,
                )

                if plan.operation is LifecycleOperation.ERASE:
                    details = erase_staged_storage(
                        str(staging),
                        storage_kind,
                        plan.selector,
                    )
                else:
                    details = rebuild_staged_storage(
                        str(staging),
                        storage_kind,
                        plan.selector,
                    )
                details = _with_lifecycle_backup_inventory(details)
                if plan.source.target.kind is LifecycleTargetKind.SQLITE:
                    _quiesce_sqlite_staging(staging)
                elif staging.is_dir():
                    _fsync_tree_directories(staging)
                _fsync_directory(staging.parent)
                staged_assessment = self.inspect(adapter.restored_target(staging))
                if staged_assessment.status is not LifecycleStatus.CURRENT:
                    raise LifecycleVerificationError(
                        "staged lifecycle mutation is not current storage",
                        recovery_status="verified_backup_preserved_source_unchanged",
                    )
                staged_content = LifecycleContentIdentity.from_assessment(
                    staged_assessment
                )

                current = self.inspect(plan.destination.target)
                if _assessment_matches_content(current, staged_content) and backup_preexisted:
                    if _lexists(recovery):
                        recovery_assessment = self.inspect(
                            LifecycleTarget(plan.source.target.kind, str(recovery))
                        )
                        if not _assessment_matches_content(
                            recovery_assessment,
                            plan.content,
                        ):
                            raise LifecycleConflictError(
                                "lifecycle recovery contains unexpected data"
                            )
                        _remove_staging_path(recovery)
                        _fsync_directory(recovery.parent)
                    _cleanup_staging(plan, staging, owner)
                    assert current.fingerprint is not None
                    return _report(
                        plan,
                        outcome=LifecycleOutcome.ALREADY_COMPLETE,
                        artifact_fingerprint=current.fingerprint,
                        file_count=current.file_count,
                        details=details,
                    )
                if not _same_assessment(current, plan.source):
                    raise LifecycleConflictError(
                        "live lifecycle source changed after planning"
                    )
                if _lexists(recovery):
                    raise LifecycleConflictError(
                        "lifecycle recovery path is unexpectedly occupied"
                    )
                current_backup = _require_complete_bundle(
                    _read_backup_bundle(plan.backup_destination.target)
                )
                if not _matching_backup_for_plan(current_backup, plan):
                    raise StaleLifecyclePlanError(
                        "lifecycle backup changed before live publication"
                    )
                _assert_plan_destination_topology(plan)

                try:
                    _rename_no_replace(destination, recovery)
                    _fsync_directory(destination.parent)
                    try:
                        _rename_no_replace(staging, destination)
                    except Exception:
                        try:
                            _rename_no_replace(recovery, destination)
                            _fsync_directory(destination.parent)
                        except Exception as rollback_exc:
                            raise LifecycleVerificationError(
                                "lifecycle publication and automatic rollback failed",
                                recovery_status=(
                                    "verified_backup_and_recovery_preserved_"
                                    "live_target_missing"
                                ),
                            ) from rollback_exc
                        raise
                except LifecycleVerificationError:
                    raise
                except LifecycleConflictError:
                    raise
                except OSError as exc:
                    raise StorageWriteError(
                        "lifecycle live publication failed and original was restored"
                    ) from exc

                _fsync_directory(destination.parent)
                try:
                    final = self.inspect(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    final = None
                    verification_error: Exception | None = exc
                else:
                    verification_error = None
                if final is None or not _assessment_matches_content(final, staged_content):
                    try:
                        if _lexists(destination):
                            _rename_no_replace(destination, staging)
                        if _lexists(recovery):
                            _rename_no_replace(recovery, destination)
                        _fsync_directory(destination.parent)
                    except Exception as rollback_exc:
                        raise LifecycleVerificationError(
                            "published lifecycle mutation failed verification and rollback",
                            recovery_status=(
                                "verified_backup_preserved_manual_recovery_required"
                            ),
                        ) from rollback_exc
                    raise LifecycleVerificationError(
                        "published lifecycle mutation failed verification; original restored",
                        recovery_status="verified_backup_preserved_source_restored",
                    ) from verification_error

                _remove_staging_path(recovery)
                _fsync_directory(destination.parent)
                _cleanup_staging(plan, staging, owner)
                assert final.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.APPLIED,
                    artifact_fingerprint=final.fingerprint,
                    file_count=final.file_count,
                    details=details,
                )
            except Exception:
                # The publication path may already have moved staging into the
                # live name; cleanup is therefore ownership-aware and only runs
                # while the staging owner still exists.
                if _lexists(owner):
                    _cleanup_staging(plan, staging, owner)
                raise

    def _execute_memory_pack_import(self, plan: LifecyclePlan) -> LifecycleReport:
        if not isinstance(plan.selector, MemoryPackImportOptions):
            raise LifecyclePlanError("MemoryPack import plan options are missing")
        destination = Path(plan.destination.target.path)
        staging, owner = _stage_paths(destination, plan)
        _assert_plan_destination_topology(plan)

        with _destination_lock(destination):
            _assert_plan_destination_topology(plan)
            current_source = self.inspect(plan.source.target)
            if not _same_assessment(plan.source, current_source):
                raise StaleLifecyclePlanError("MemoryPack source changed after planning")
            source_snapshot = _materialize_snapshot(
                _capture_snapshot(current_source)
            )
            assert source_snapshot.files is not None
            if source_snapshot.content != plan.content:
                raise StaleLifecyclePlanError(
                    "MemoryPack import source no longer matches its plan"
                )
            try:
                pack = MemoryPack.from_json(
                    source_snapshot.files["memory-pack.erii"].decode("utf-8")
                )
            except (UnicodeDecodeError, TypeError, ValueError) as exc:
                raise StorageIntegrityError("MemoryPack import source is malformed") from exc
            _validate_memory_pack_semantic_graph(pack)

            _prepare_staging(plan, staging, owner)
            published = False
            try:
                from erii.lifecycle_memory_pack_import import (
                    MemoryPackStagingImportRequest,
                    MemoryPackStagingImporter,
                )

                staging_adapter = (
                    MemoryPackStagingAdapter.FILE_STORAGE
                    if plan.destination.target.kind is LifecycleTargetKind.FILE_STORAGE
                    else MemoryPackStagingAdapter.SQLITE
                )
                importer = MemoryPackStagingImporter()
                details = importer.import_pack(
                    MemoryPackStagingImportRequest(
                        adapter=staging_adapter,
                        staging_path=str(staging),
                        pack=pack,
                        target_agent_id=plan.selector.target_agent_id,
                        target_user_id=plan.selector.target_user_id,
                        overwrite=False,
                    )
                )
                if plan.destination.target.kind is LifecycleTargetKind.FILE_STORAGE:
                    manifest = staging / FILE_STORAGE_MANIFEST
                    if _lexists(manifest):
                        raise StorageIntegrityError(
                            "fresh FileStorage import unexpectedly created a format manifest"
                        )
                    _write_durable_file(
                        manifest,
                        _canonical_json(
                            {
                                "format": FILE_STORAGE_FORMAT.format_id,
                                "version": int(FILE_STORAGE_FORMAT.current_version),
                            }
                        ),
                    )
                    _fsync_tree_directories(staging)
                else:
                    _quiesce_sqlite_staging(staging)
                _fsync_directory(staging.parent)
                staged_assessment = self.inspect(
                    LifecycleTarget(plan.destination.target.kind, str(staging))
                )
                if staged_assessment.status is not LifecycleStatus.CURRENT:
                    raise LifecycleVerificationError(
                        "staged MemoryPack import is not current storage",
                        recovery_status="source_pack_preserved_target_missing",
                    )
                staged_content = LifecycleContentIdentity.from_assessment(
                    staged_assessment
                )

                current_destination = self.inspect(plan.destination.target)
                if current_destination.status is not LifecycleStatus.MISSING:
                    if current_destination.status is LifecycleStatus.CURRENT:
                        try:
                            existing_details = importer.inspect_target(
                                adapter=staging_adapter,
                                staging_path=plan.destination.target.path,
                                agent_id=details.agent_id,
                                user_id=details.user_id,
                            )
                        except Exception as exc:
                            raise LifecycleConflictError(
                                "MemoryPack import destination contains unreadable data"
                            ) from exc
                        import_matches = (
                            existing_details.to_dict() == details.to_dict()
                        )
                        if not import_matches:
                            import_matches = (
                                importer.target_matches_import_with_legacy_compatibility(
                                    adapter=staging_adapter,
                                    existing_path=plan.destination.target.path,
                                    desired_path=str(staging),
                                    agent_id=details.agent_id,
                                    user_id=details.user_id,
                                )
                            )
                        if import_matches:
                            _cleanup_staging(plan, staging, owner)
                            assert current_destination.fingerprint is not None
                            return _report(
                                plan,
                                outcome=LifecycleOutcome.ALREADY_COMPLETE,
                                artifact_fingerprint=current_destination.fingerprint,
                                file_count=current_destination.file_count,
                                details=existing_details,
                            )
                    raise LifecycleConflictError(
                        "MemoryPack import destination contains different data"
                    )
                final_source = self.inspect(plan.source.target)
                if not _same_assessment(plan.source, final_source):
                    raise StaleLifecyclePlanError(
                        "MemoryPack source changed before target publication"
                    )
                _assert_plan_destination_topology(plan)
                try:
                    _rename_no_replace(staging, destination)
                except LifecycleConflictError:
                    raise
                except OSError as exc:
                    raise StorageWriteError("MemoryPack import publication failed") from exc
                published = True
                _fsync_directory(destination.parent)
                try:
                    final = self.inspect(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleVerificationError(
                        "published MemoryPack import failed verification",
                        recovery_status=_published_target_recovery_status(),
                    ) from exc
                if not _assessment_matches_content(final, staged_content):
                    raise LifecycleVerificationError(
                        "published MemoryPack import does not match staging",
                        recovery_status=_published_target_recovery_status(),
                    )
                _cleanup_staging(plan, staging, owner)
                assert final.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.APPLIED,
                    artifact_fingerprint=final.fingerprint,
                    file_count=final.file_count,
                    details=details,
                )
            except Exception:
                if not published and _lexists(owner):
                    _cleanup_staging(plan, staging, owner)
                raise


__all__ = [
    "BackupRequest",
    "DataLifecycleCoordinator",
    "EraseRequest",
    "ErasureInventory",
    "ErasureScope",
    "ErasureSelector",
    "ErasureTransformResult",
    "FILE_STORAGE_MANIFEST",
    "LIFECYCLE_BACKUP_MANIFEST",
    "LIFECYCLE_PLAN_CONTRACT_VERSION",
    "MAX_LIFECYCLE_BACKUP_MANIFEST_BYTES",
    "MAX_LIFECYCLE_MEMORY_PACK_BYTES",
    "MAX_LIFECYCLE_TRANSFORM_BYTES",
    "LifecycleAssessment",
    "LifecycleContentIdentity",
    "LifecycleDirectoryIdentity",
    "LifecycleInspector",
    "LifecycleOperation",
    "LifecycleOutcome",
    "LifecyclePlan",
    "LifecyclePlanSelector",
    "LifecycleReport",
    "LifecycleRequest",
    "LifecycleStatus",
    "LifecycleTarget",
    "LifecycleTargetKind",
    "MemoryPackImportOptions",
    "MemoryPackImportRequest",
    "MemoryPackStagingImportReport",
    "RebuildRequest",
    "RelationshipRebuildProof",
    "RestoreRequest",
    "UpgradeRequest",
]
