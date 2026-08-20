"""Bounded-memory lifecycle file transport and identity contracts."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from erii.errors import StorageIntegrityError, StorageWriteError
from erii.lifecycle_streaming import (
    MAX_STREAM_CHUNK_BYTES,
    RegularFileIdentity,
    copy_regular_file_exclusive,
    stream_regular_file_identity,
    stream_regular_tree_manifest,
)
from erii.lifecycle_sqlite_upgrade import _semantic_digest_from_connection


class RegularTreeManifestTests(unittest.TestCase):
    def test_tree_fingerprint_is_compatible_with_the_frozen_lifecycle_algorithm(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            (root / "nested").mkdir()
            (root / "a.txt").write_bytes(b"alpha")
            (root / "nested" / "绘梨衣.json").write_bytes(
                "雪".encode("utf-8") * 700_000
            )

            manifest = stream_regular_tree_manifest(root)

            self.assertEqual(
                manifest.tree_fingerprint,
                "63c20ab55f1497d98ad39c362c390fcf100a8ac9a8d4469820a1c11ba35c4477",
            )
            self.assertEqual(
                [entry.relative_path for entry in manifest.files],
                ["a.txt", "nested/绘梨衣.json"],
            )
            self.assertEqual(manifest.total_bytes, 2_100_005)
            self.assertEqual(
                manifest.to_dict(),
                {
                    "files": [
                        {
                            "path": "a.txt",
                            "size": 5,
                            "sha256": (
                                "8ed3f6ad685b959ead7022518e1af76cd816f8e8"
                                "ec7ccdda1ed4018e8f2223f8"
                            ),
                        },
                        {
                            "path": "nested/绘梨衣.json",
                            "size": 2_100_000,
                            "sha256": (
                                "b318dd8c6d4f61a786886a35aa5ef84d2561ef3b"
                                "445201a27f6e3a23908247d4"
                            ),
                        },
                    ],
                    "total_bytes": 2_100_005,
                    "tree_fingerprint": (
                        "63c20ab55f1497d98ad39c362c390fcf100a8ac9"
                        "a8d4469820a1c11ba35c4477"
                    ),
                },
            )

    def test_many_files_are_read_in_bounded_chunks_without_retaining_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            payload = b"x" * (2 * 1024 * 1024)
            for index in range(8):
                (root / f"part-{index:02d}.bin").write_bytes(payload)
            total_bytes = len(payload) * 8
            requested_sizes: list[int] = []
            real_read = os.read

            def observed_read(descriptor: int, size: int) -> bytes:
                requested_sizes.append(size)
                return real_read(descriptor, size)

            tracemalloc.start()
            try:
                with patch(
                    "erii._lifecycle.filesystem.os.read",
                    side_effect=observed_read,
                ):
                    manifest = stream_regular_tree_manifest(
                        root,
                        chunk_size=64 * 1024,
                    )
                _current, peak_bytes = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

            self.assertEqual(manifest.total_bytes, total_bytes)
            self.assertEqual(manifest.file_count, 8)
            self.assertTrue(requested_sizes)
            self.assertLessEqual(max(requested_sizes), 64 * 1024)
            self.assertLess(peak_bytes, total_bytes // 4)
            self.assertFalse(
                any(
                    isinstance(value, (bytes, bytearray, memoryview))
                    for entry in manifest.files
                    for value in (entry.relative_path, entry.size, entry.sha256)
                )
            )

    def test_chunk_size_cannot_exceed_the_public_memory_bound(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            source = Path(root_dir) / "source.bin"
            source.write_bytes(b"content")

            with self.assertRaisesRegex(ValueError, "chunk_size"):
                stream_regular_file_identity(
                    source,
                    chunk_size=MAX_STREAM_CHUNK_BYTES + 1,
                )

    def test_source_timestamp_change_during_copy_is_rejected_and_cleaned_up(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source = root / "source.bin"
            destination = root / "destination.bin"
            source.write_bytes(b"stale" * 300_000)
            original = source.stat()
            real_read = os.read
            changed = False

            def change_timestamp_after_read(descriptor: int, size: int) -> bytes:
                nonlocal changed
                chunk = real_read(descriptor, size)
                if chunk and not changed:
                    changed = True
                    os.utime(
                        source,
                        ns=(original.st_atime_ns, original.st_mtime_ns + 10_000_000_000),
                    )
                return chunk

            with patch(
                "erii._lifecycle.filesystem.os.read",
                side_effect=change_timestamp_after_read,
            ):
                with self.assertRaisesRegex(StorageIntegrityError, "changed"):
                    copy_regular_file_exclusive(
                        source,
                        destination,
                        chunk_size=64 * 1024,
                    )

            self.assertTrue(changed)
            self.assertFalse(destination.exists())


class ExclusiveStreamingCopyTests(unittest.TestCase):
    def test_copy_is_content_verified_and_never_replaces_an_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source = root / "source.erii"
            destination = root / "backup.erii"
            source.write_bytes((b"bounded-copy-" * 100_000) + b"end")
            expected = stream_regular_file_identity(source)

            copied = copy_regular_file_exclusive(
                source,
                destination,
                expected=expected,
                chunk_size=64 * 1024,
            )

            self.assertEqual(copied, expected)
            self.assertEqual(stream_regular_file_identity(destination), expected)
            with self.assertRaises(FileExistsError):
                copy_regular_file_exclusive(source, destination, expected=expected)
            self.assertEqual(stream_regular_file_identity(destination), expected)

    def test_copy_removes_a_partial_destination_after_a_write_fault(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source = root / "source.erii"
            destination = root / "backup.erii"
            source.write_bytes(b"fault-test" * 100_000)
            real_write = os.write
            first_write = True

            def fail_during_write(descriptor: int, content: object) -> int:
                nonlocal first_write
                if first_write:
                    first_write = False
                    view = memoryview(content)
                    real_write(descriptor, view[: max(1, len(view) // 2)])
                    raise OSError("injected copy failure")
                return real_write(descriptor, content)

            with patch(
                "erii._lifecycle.filesystem.os.write",
                side_effect=fail_during_write,
            ):
                with self.assertRaisesRegex(StorageWriteError, "could not create"):
                    copy_regular_file_exclusive(source, destination)

            self.assertFalse(destination.exists())
            self.assertEqual(source.read_bytes(), b"fault-test" * 100_000)

    def test_copy_rejects_an_unexpected_content_identity_without_leaving_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            source = root / "source.erii"
            destination = root / "backup.erii"
            source.write_bytes(b"actual")
            unexpected = RegularFileIdentity(size=6, sha256="0" * 64)

            with self.assertRaisesRegex(StorageIntegrityError, "expected identity"):
                copy_regular_file_exclusive(source, destination, expected=unexpected)

            self.assertFalse(destination.exists())

    def test_hash_and_tree_scan_reject_links(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            target = root / "target.bin"
            link = root / "linked.bin"
            target.write_bytes(b"target")
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"this Windows account cannot create symlinks: {exc}")

            with self.assertRaisesRegex(StorageIntegrityError, "link|reparse"):
                stream_regular_file_identity(link)
            with self.assertRaisesRegex(StorageIntegrityError, "link|reparse"):
                stream_regular_tree_manifest(root)

    def test_windows_reparse_attribute_is_rejected_without_symlink_privileges(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            source = Path(root_dir) / "source.bin"
            source.write_bytes(b"content")
            real_lstat = os.lstat
            source_absolute = os.path.normcase(os.path.abspath(source))

            def reparse_lstat(path: object) -> object:
                info = real_lstat(path)
                if os.path.normcase(os.path.abspath(path)) != source_absolute:
                    return info
                values = {
                    name: getattr(info, name)
                    for name in (
                        "st_mode",
                        "st_dev",
                        "st_ino",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                        "st_nlink",
                    )
                }
                return SimpleNamespace(
                    **values,
                    st_file_attributes=getattr(info, "st_file_attributes", 0) | 0x400,
                )

            with patch(
                "erii._lifecycle.filesystem.os.lstat",
                side_effect=reparse_lstat,
            ):
                with self.assertRaisesRegex(StorageIntegrityError, "link|reparse"):
                    stream_regular_file_identity(source)


class SQLiteSemanticDigestStreamingTests(unittest.TestCase):
    def test_large_tables_do_not_become_a_python_row_list(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE TABLE payloads (id INTEGER PRIMARY KEY, body TEXT)")
            body = "x" * (256 * 1024)
            connection.executemany(
                "INSERT INTO payloads (body) VALUES (?)",
                ((body,) for _ in range(64)),
            )
            connection.commit()
            del body

            tracemalloc.start()
            try:
                digest = _semantic_digest_from_connection(connection)
                _current, peak_bytes = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

        self.assertEqual(len(digest), 64)
        self.assertLess(peak_bytes, 4 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
