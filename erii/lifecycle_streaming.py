"""Bounded-memory, link-safe file transport for lifecycle operations.

The helpers in this module intentionally carry bytes only one bounded chunk at
a time.  A tree manifest retains paths, sizes, and digests, never file bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Callable, Iterable

from erii.errors import StorageIntegrityError, StorageWriteError


MAX_STREAM_CHUNK_BYTES = 1024 * 1024
DEFAULT_STREAM_CHUNK_BYTES = MAX_STREAM_CHUNK_BYTES


@dataclass(frozen=True, slots=True)
class RegularFileIdentity:
    """Stable content identity observed while one regular file was open."""

    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class TreeFileEntry:
    """Canonical no-content manifest entry for one tree file."""

    relative_path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class RegularTreeManifest:
    """Canonical tree identity compatible with E.R.I.I.'s v1 fingerprint."""

    files: tuple[TreeFileEntry, ...]
    tree_fingerprint: str
    total_bytes: int

    @property
    def file_count(self) -> int:
        return len(self.files)

    def to_dict(self) -> dict[str, object]:
        """Returns the canonical no-content representation used in bundles."""

        return {
            "files": [entry.to_dict() for entry in self.files],
            "total_bytes": self.total_bytes,
            "tree_fingerprint": self.tree_fingerprint,
        }


_StatSignature = tuple[int, int, int, int, int, int]


def _stat_signature(info: os.stat_result) -> _StatSignature:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mode),
        int(info.st_size),
        int(info.st_mtime_ns),
        # Windows exposes creation time as st_ctime and may finalize it around
        # handle close.  It is not a useful mutation signal there; identity,
        # size, and mtime remain stable across path and handle observations.
        0 if os.name == "nt" else int(info.st_ctime_ns),
    )


def _directory_signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mode),
        int(info.st_mtime_ns),
        0 if os.name == "nt" else int(info.st_ctime_ns),
    )


def _is_link_or_reparse(info: os.stat_result) -> bool:
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _lstat(path: Path, *, label: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise StorageIntegrityError(f"cannot inspect {label}") from exc


def _assert_unlinked_ancestors(path: Path, *, label: str) -> None:
    current = Path(os.path.abspath(path))
    while True:
        if os.path.lexists(current):
            if _is_link_or_reparse(_lstat(current, label=label)):
                raise StorageIntegrityError(f"{label} cannot use a linked or reparse path")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _require_regular_file(path: Path, *, label: str) -> os.stat_result:
    info = _lstat(path, label=label)
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise StorageIntegrityError(f"{label} is not an unlinked regular file")
    return info


def _require_regular_directory(path: Path, *, label: str) -> os.stat_result:
    info = _lstat(path, label=label)
    if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise StorageIntegrityError(f"{label} is not an unlinked regular directory")
    return info


def _validate_chunk_size(chunk_size: int) -> int:
    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or chunk_size < 1
        or chunk_size > MAX_STREAM_CHUNK_BYTES
    ):
        raise ValueError(
            f"chunk_size must be between 1 and {MAX_STREAM_CHUNK_BYTES} bytes"
        )
    return chunk_size


def _stream_regular_file(
    path: Path,
    *,
    expected_signature: _StatSignature | None,
    chunk_size: int,
    extra_digests: Iterable["hashlib._Hash"] = (),
) -> RegularFileIdentity:
    chunk_size = _validate_chunk_size(chunk_size)
    label = f"lifecycle source file {path.name!r}"
    _assert_unlinked_ancestors(path, label=label)
    before = _require_regular_file(path, label=label)
    before_signature = _stat_signature(before)
    if expected_signature is not None and before_signature != expected_signature:
        raise StorageIntegrityError("lifecycle source changed before it was read")

    descriptor = -1
    digest = hashlib.sha256()
    byte_count = 0
    opened: os.stat_result | None = None
    after_open: os.stat_result | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if _is_link_or_reparse(opened) or not stat.S_ISREG(opened.st_mode):
            raise StorageIntegrityError(f"{label} is not an unlinked regular file")
        if before_signature != _stat_signature(opened):
            raise StorageIntegrityError("lifecycle source changed before it was opened")
        while True:
            chunk = os.read(descriptor, chunk_size)
            if not chunk:
                break
            byte_count += len(chunk)
            digest.update(chunk)
            for extra_digest in extra_digests:
                extra_digest.update(chunk)
        after_open = os.fstat(descriptor)
        _assert_unlinked_ancestors(path, label=label)
        after_path = _require_regular_file(path, label=label)
    except StorageIntegrityError:
        raise
    except OSError as exc:
        raise StorageIntegrityError(f"cannot read {label}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    assert opened is not None and after_open is not None
    if not (
        before_signature
        == _stat_signature(opened)
        == _stat_signature(after_open)
        == _stat_signature(after_path)
    ):
        raise StorageIntegrityError("lifecycle source changed while it was read")
    if expected_signature is not None and _stat_signature(after_path) != expected_signature:
        raise StorageIntegrityError("lifecycle source changed while it was read")
    if byte_count != before.st_size:
        raise StorageIntegrityError("lifecycle source size changed while it was read")
    return RegularFileIdentity(size=byte_count, sha256=digest.hexdigest())


def stream_regular_file_identity(
    path: str | os.PathLike[str],
    *,
    chunk_size: int = DEFAULT_STREAM_CHUNK_BYTES,
) -> RegularFileIdentity:
    """Hashes one stable regular file without following links or buffering it."""

    return _stream_regular_file(
        Path(path),
        expected_signature=None,
        chunk_size=chunk_size,
    )


def _same_object(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        int(first.st_dev),
        int(first.st_ino),
        int(first.st_mode),
    ) == (
        int(second.st_dev),
        int(second.st_ino),
        int(second.st_mode),
    )


def _remove_created_file(path: Path, opened: os.stat_result | None) -> None:
    if opened is None:
        return
    try:
        current = os.lstat(path)
        if (
            not _is_link_or_reparse(current)
            and stat.S_ISREG(current.st_mode)
            and _same_object(opened, current)
        ):
            path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        # Preserve the original copy failure.  The caller must treat an
        # unremovable partial file as occupied on its next O_EXCL attempt.
        return


def copy_regular_file_exclusive(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    expected: RegularFileIdentity | None = None,
    chunk_size: int = DEFAULT_STREAM_CHUNK_BYTES,
) -> RegularFileIdentity:
    """Copies and verifies one regular file to an O_EXCL destination.

    The source must remain unchanged from stat-before through stat-after.  The
    destination is removed on a detectable failed copy, but an existing path is
    never opened, truncated, or replaced.
    """

    chunk_size = _validate_chunk_size(chunk_size)
    source_path = Path(source)
    destination_path = Path(destination)
    _assert_unlinked_ancestors(source_path, label="lifecycle copy source")
    _assert_unlinked_ancestors(
        destination_path.parent,
        label="lifecycle copy destination parent",
    )
    _require_regular_directory(
        destination_path.parent,
        label="lifecycle copy destination parent",
    )
    before = _require_regular_file(source_path, label="lifecycle copy source")
    before_signature = _stat_signature(before)

    source_descriptor = -1
    destination_descriptor = -1
    destination_opened: os.stat_result | None = None
    succeeded = False
    content_digest = hashlib.sha256()
    byte_count = 0
    try:
        source_descriptor = os.open(
            source_path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        source_opened = os.fstat(source_descriptor)
        if (
            _is_link_or_reparse(source_opened)
            or not stat.S_ISREG(source_opened.st_mode)
            or _stat_signature(source_opened) != before_signature
        ):
            raise StorageIntegrityError("lifecycle copy source changed before opening")

        destination_descriptor = os.open(
            destination_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        destination_opened = os.fstat(destination_descriptor)
        destination_path_info = _lstat(
            destination_path,
            label="lifecycle copy destination",
        )
        if (
            _is_link_or_reparse(destination_opened)
            or _is_link_or_reparse(destination_path_info)
            or not stat.S_ISREG(destination_opened.st_mode)
            or destination_opened.st_nlink != 1
            or not _same_object(destination_opened, destination_path_info)
        ):
            raise StorageWriteError(
                "lifecycle copy destination is not a private regular file"
            )

        while True:
            chunk = os.read(source_descriptor, chunk_size)
            if not chunk:
                break
            byte_count += len(chunk)
            content_digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise OSError("short lifecycle copy write")
                view = view[written:]
        os.fsync(destination_descriptor)
        source_after_open = os.fstat(source_descriptor)
        destination_after_open = os.fstat(destination_descriptor)
        _assert_unlinked_ancestors(source_path, label="lifecycle copy source")
        source_after_path = _require_regular_file(
            source_path,
            label="lifecycle copy source",
        )
        _assert_unlinked_ancestors(
            destination_path,
            label="lifecycle copy destination",
        )
        destination_after_path = _require_regular_file(
            destination_path,
            label="lifecycle copy destination",
        )
        if not (
            before_signature
            == _stat_signature(source_opened)
            == _stat_signature(source_after_open)
            == _stat_signature(source_after_path)
        ):
            raise StorageIntegrityError("lifecycle copy source changed while it was read")
        if not (
            _same_object(destination_opened, destination_after_open)
            and _same_object(destination_opened, destination_after_path)
            and destination_after_open.st_size == byte_count
        ):
            raise StorageWriteError("lifecycle copy destination changed while it was written")
        copied = RegularFileIdentity(size=byte_count, sha256=content_digest.hexdigest())
        if expected is not None and copied != expected:
            raise StorageIntegrityError("lifecycle copy source differs from its expected identity")
        succeeded = True
    except FileExistsError:
        raise
    except (StorageIntegrityError, StorageWriteError):
        raise
    except OSError as exc:
        raise StorageWriteError("could not create the lifecycle copy") from exc
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if not succeeded:
            _remove_created_file(destination_path, destination_opened)

    verified = stream_regular_file_identity(destination_path, chunk_size=chunk_size)
    if verified != copied:
        _remove_created_file(destination_path, destination_opened)
        raise StorageIntegrityError("lifecycle copy failed destination verification")
    return copied


def _scan_tree_entries(
    root: Path,
    *,
    exclude_relative_name: Callable[[str], bool] | None,
) -> tuple[dict[str, _StatSignature], dict[str, tuple[int, int, int, int, int]]]:
    label = "lifecycle source tree"
    _assert_unlinked_ancestors(root, label=label)
    files: dict[str, _StatSignature] = {}
    directories: dict[str, tuple[int, int, int, int, int]] = {}
    folded_names: set[str] = set()

    def walk(directory: Path, parts: tuple[str, ...]) -> None:
        directory_info = _require_regular_directory(directory, label=label)
        relative_directory = PurePosixPath(*parts).as_posix() if parts else "."
        directories[relative_directory] = _directory_signature(directory_info)
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise StorageIntegrityError("lifecycle source tree cannot be scanned") from exc
        for entry in entries:
            relative_name = PurePosixPath(*parts, entry.name).as_posix()
            info = _lstat(Path(entry.path), label="lifecycle source tree entry")
            if _is_link_or_reparse(info):
                raise StorageIntegrityError(
                    "lifecycle source tree contains a link or reparse entry"
                )
            if stat.S_ISDIR(info.st_mode):
                walk(Path(entry.path), (*parts, entry.name))
            elif stat.S_ISREG(info.st_mode):
                if exclude_relative_name is not None and exclude_relative_name(relative_name):
                    continue
                folded = relative_name.casefold()
                if folded in folded_names:
                    raise StorageIntegrityError(
                        "lifecycle source tree has non-portable duplicate paths"
                    )
                folded_names.add(folded)
                files[relative_name] = _stat_signature(info)
            else:
                raise StorageIntegrityError(
                    "lifecycle source tree contains a non-regular entry"
                )
        final = _require_regular_directory(directory, label=label)
        if _directory_signature(final) != directories[relative_directory]:
            raise StorageIntegrityError("lifecycle source tree changed during inspection")

    walk(root, ())
    return files, directories


def stream_regular_tree_manifest(
    root: str | os.PathLike[str],
    *,
    exclude_relative_name: Callable[[str], bool] | None = None,
    chunk_size: int = DEFAULT_STREAM_CHUNK_BYTES,
) -> RegularTreeManifest:
    """Builds a canonical, no-content manifest for a stable regular-file tree.

    The tree fingerprint is byte-for-byte compatible with the lifecycle v1
    ``_fingerprint_files`` framing: sorted UTF-8 path length/path followed by
    big-endian content length and raw content bytes.
    """

    chunk_size = _validate_chunk_size(chunk_size)
    root_path = Path(root)
    files, directories = _scan_tree_entries(
        root_path,
        exclude_relative_name=exclude_relative_name,
    )
    tree_digest = hashlib.sha256()
    manifest_entries: list[TreeFileEntry] = []
    total_bytes = 0
    for relative_name in sorted(files):
        signature = files[relative_name]
        name_bytes = relative_name.encode("utf-8")
        tree_digest.update(len(name_bytes).to_bytes(8, "big"))
        tree_digest.update(name_bytes)
        tree_digest.update(signature[3].to_bytes(8, "big"))
        identity = _stream_regular_file(
            root_path.joinpath(*PurePosixPath(relative_name).parts),
            expected_signature=signature,
            chunk_size=chunk_size,
            extra_digests=(tree_digest,),
        )
        manifest_entries.append(
            TreeFileEntry(
                relative_path=relative_name,
                size=identity.size,
                sha256=identity.sha256,
            )
        )
        total_bytes += identity.size

    final_files, final_directories = _scan_tree_entries(
        root_path,
        exclude_relative_name=exclude_relative_name,
    )
    if files != final_files or directories != final_directories:
        raise StorageIntegrityError("lifecycle source tree changed during inspection")
    return RegularTreeManifest(
        files=tuple(manifest_entries),
        tree_fingerprint=tree_digest.hexdigest(),
        total_bytes=total_bytes,
    )


__all__ = [
    "DEFAULT_STREAM_CHUNK_BYTES",
    "MAX_STREAM_CHUNK_BYTES",
    "RegularFileIdentity",
    "RegularTreeManifest",
    "TreeFileEntry",
    "copy_regular_file_exclusive",
    "stream_regular_file_identity",
    "stream_regular_tree_manifest",
]
