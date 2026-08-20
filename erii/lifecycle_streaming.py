"""Compatibility re-exports for authoritative lifecycle filesystem helpers."""

from __future__ import annotations

from erii._lifecycle.filesystem import (
    DEFAULT_STREAM_CHUNK_BYTES as DEFAULT_STREAM_CHUNK_BYTES,
    MAX_STREAM_CHUNK_BYTES as MAX_STREAM_CHUNK_BYTES,
    RegularFileIdentity as RegularFileIdentity,
    RegularTreeManifest as RegularTreeManifest,
    TreeFileEntry as TreeFileEntry,
    copy_regular_file_exclusive as copy_regular_file_exclusive,
    stream_regular_file_identity as stream_regular_file_identity,
    stream_regular_tree_manifest as stream_regular_tree_manifest,
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
