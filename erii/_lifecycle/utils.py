"""
Lifecycle utilities: filesystem operations and validation helpers.

This module provides low-level filesystem utilities and validation functions
for lifecycle operations. All functions are read-only with no side effects.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Dict

from erii.errors import StorageIntegrityError

__all__ = [
    "lexists",
    "lstat",
    "stat_is_link_or_reparse",
    "stat_signature",
    "stat_object_identity",
    "require_regular_file",
    "require_regular_directory",
    "assert_no_link_or_reparse_ancestors",
    "resolved_path",
    "scan_directory_entries",
    "is_file_storage_runtime_lock",
    "fingerprint_files",
    "sqlite_uri",
]


def lexists(path: Path) -> bool:
    """Check if path exists without following symlinks."""
    try:
        os.lstat(path)
        return True
    except FileNotFoundError:
        return False


def stat_is_link_or_reparse(info: os.stat_result) -> bool:
    """Check if stat result represents a symlink or reparse point."""
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def stat_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    """Extract comparable signature from stat result."""
    return (
        info.st_mode,
        info.st_ino,
        info.st_dev,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
    )


def stat_object_identity(info: os.stat_result) -> tuple[int, int, int]:
    """Extract object identity (mode, device, inode) from stat result."""
    return (info.st_mode, info.st_dev, info.st_ino)


def lstat(path: Path, *, label: str) -> os.stat_result:
    """Get stat result without following symlinks."""
    try:
        return os.lstat(path)
    except OSError as exc:
        raise StorageIntegrityError(f"{label} cannot be inspected") from exc


def require_regular_file(path: Path, *, label: str) -> os.stat_result:
    """Verify path is a regular file and return its stat."""
    info = lstat(path, label=label)
    if not stat.S_ISREG(info.st_mode):
        raise StorageIntegrityError(f"{label} is not a regular file")
    return info


def require_regular_directory(path: Path, *, label: str) -> os.stat_result:
    """Verify path is a regular directory and return its stat."""
    info = lstat(path, label=label)
    if not stat.S_ISDIR(info.st_mode):
        raise StorageIntegrityError(f"{label} is not a directory")
    return info


def assert_no_link_or_reparse_ancestors(path: Path, *, label: str) -> None:
    """Verify no ancestor is a symlink or reparse point."""
    current = path.parent
    while current != current.parent:
        info = lstat(current, label=label)
        if stat_is_link_or_reparse(info):
            raise StorageIntegrityError(
                f"{label} has a symlink or reparse point ancestor"
            )
        current = current.parent


def resolved_path(path: Path, *, label: str) -> str:
    """Get canonical resolved path string."""
    try:
        return str(path.resolve(strict=True))
    except (OSError, RuntimeError) as exc:
        raise StorageIntegrityError(f"{label} cannot be resolved") from exc


def scan_directory_entries(
    root: Path,
    *,
    label: str,
) -> tuple[Dict[str, os.stat_result], list[str]]:
    """Scan directory and return (files, directories).
    
    Returns:
        files: Dict mapping relative path to stat result
        directories: List of relative directory paths
    """
    files: Dict[str, os.stat_result] = {}
    directories: list[str] = []
    
    try:
        for entry in root.rglob("*"):
            try:
                info = os.lstat(entry)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise StorageIntegrityError(f"{label} entry cannot be inspected") from exc
            
            relative = entry.relative_to(root).as_posix()
            
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
            elif stat.S_ISREG(info.st_mode):
                files[relative] = info
            elif stat_is_link_or_reparse(info):
                raise StorageIntegrityError(f"{label} contains symbolic link or reparse point")
            else:
                raise StorageIntegrityError(f"{label} contains non-regular entry")
                
    except OSError as exc:
        raise StorageIntegrityError(f"{label} cannot be scanned") from exc
    
    return files, directories


def is_file_storage_runtime_lock(relative_name: str) -> bool:
    """Check if filename is a FileStorage runtime lock."""
    return relative_name.endswith("/.lock") or "/.lock/" in relative_name


def fingerprint_files(content_by_name: Dict[str, bytes]) -> str:
    """Calculate SHA-256 fingerprint of file contents."""
    hasher = hashlib.sha256()
    for name in sorted(content_by_name):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\\0")
        hasher.update(content_by_name[name])
        hasher.update(b"\\0")
    return hasher.hexdigest()


def sqlite_uri(path: Path, *, immutable: bool) -> str:
    """Construct SQLite URI with appropriate flags."""
    absolute = path.resolve()
    uri = f"file:{absolute.as_posix()}"
    if immutable:
        uri += "?mode=ro&immutable=1"
    return uri





