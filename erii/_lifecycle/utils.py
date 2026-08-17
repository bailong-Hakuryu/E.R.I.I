"""Aliases for lifecycle filesystem helpers that have not moved yet.

R2 originally copied these helpers into this module without switching callers,
which created a second implementation. Until Inspection is extracted as one
unit, this module exposes the canonical functions from ``erii.data_lifecycle``
instead of duplicating their behavior.
"""

from erii.data_lifecycle import (
    _assert_no_link_or_reparse_ancestors as assert_no_link_or_reparse_ancestors,
    _fingerprint_files as fingerprint_files,
    _is_file_storage_runtime_lock as is_file_storage_runtime_lock,
    _lexists as lexists,
    _lstat as lstat,
    _require_regular_directory as require_regular_directory,
    _require_regular_file as require_regular_file,
    _resolved_path as resolved_path,
    _scan_directory_entries as scan_directory_entries,
    _sqlite_uri as sqlite_uri,
    _stat_is_link_or_reparse as stat_is_link_or_reparse,
    _stat_object_identity as stat_object_identity,
    _stat_signature as stat_signature,
)

__all__ = [
    "assert_no_link_or_reparse_ancestors",
    "fingerprint_files",
    "is_file_storage_runtime_lock",
    "lexists",
    "lstat",
    "require_regular_directory",
    "require_regular_file",
    "resolved_path",
    "scan_directory_entries",
    "sqlite_uri",
    "stat_is_link_or_reparse",
    "stat_object_identity",
    "stat_signature",
]
