"""Fail-closed storage integrity contracts for the v0.4 Beta lifecycle."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from erii import FileStorage, MemoryNode, MemoryType, SQLiteStorage
from erii.storage.errors import StorageIntegrityError, StorageWriteError


AGENT_ID = "agent_lumi"
USER_ID = "user_chen"


def memory_node(content: str = "A valid memory.") -> MemoryNode:
    return MemoryNode(
        node_id="node-one",
        agent_id=AGENT_ID,
        user_id=USER_ID,
        node_type=MemoryType.FACT,
        content=content,
        base_importance=0.8,
        decayable=False,
    )


class FileStorageIntegrityTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows extended-length path contract")
    def test_atomic_write_supports_temp_path_beyond_legacy_max_path(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            long_root = Path(root_dir) / ("x" * 130)
            storage = FileStorage(str(long_root))
            core_path = Path(storage._get_core_path(AGENT_ID, USER_ID))

            self.assertLess(len(str(core_path)), 260)
            self.assertGreaterEqual(len(str(core_path)) + 37, 260)

            storage.save_core_memory(AGENT_ID, USER_ID, "long path value")

            self.assertEqual(
                storage.get_core_memory(AGENT_ID, USER_ID),
                "long path value",
            )

    def test_missing_legacy_files_keep_their_documented_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            storage = FileStorage(root_dir)

            self.assertEqual(storage.load_nodes(AGENT_ID, USER_ID), [])
            self.assertEqual(storage.get_core_memory(AGENT_ID, USER_ID), "")
            self.assertEqual(storage.get_recent_timeline(AGENT_ID, USER_ID), [])

    def test_malformed_legacy_files_raise_and_cannot_be_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            storage = FileStorage(root_dir)
            cases = (
                (
                    storage._get_nodes_path(AGENT_ID, USER_ID),
                    lambda: storage.load_nodes(AGENT_ID, USER_ID),
                    lambda: storage.save_nodes(AGENT_ID, USER_ID, []),
                ),
                (
                    storage._get_core_path(AGENT_ID, USER_ID),
                    lambda: storage.get_core_memory(AGENT_ID, USER_ID),
                    lambda: storage.save_core_memory(AGENT_ID, USER_ID, "replacement"),
                ),
                (
                    storage._get_timeline_path(AGENT_ID, USER_ID),
                    lambda: storage.get_recent_timeline(AGENT_ID, USER_ID),
                    lambda: storage.add_timeline_entry(
                        AGENT_ID,
                        USER_ID,
                        "replacement",
                    ),
                ),
            )

            for file_name, read, write in cases:
                with self.subTest(file_name=Path(file_name).name):
                    damaged = b'{"truncated": '
                    Path(file_name).write_bytes(damaged)

                    with self.assertRaises(StorageIntegrityError):
                        read()
                    with self.assertRaises(StorageIntegrityError):
                        write()

                    self.assertEqual(Path(file_name).read_bytes(), damaged)

    def test_failed_atomic_replace_preserves_the_previous_document(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            storage = FileStorage(root_dir)
            storage.save_core_memory(AGENT_ID, USER_ID, "before")
            core_path = Path(storage._get_core_path(AGENT_ID, USER_ID))
            before = core_path.read_bytes()

            with patch(
                "erii.storage.file_storage.os.replace",
                side_effect=OSError("simulated replace failure"),
            ):
                with self.assertRaises(StorageWriteError):
                    storage.save_core_memory(AGENT_ID, USER_ID, "after")

            self.assertEqual(core_path.read_bytes(), before)
            self.assertEqual(list(core_path.parent.glob("core_memory.json.*.tmp")), [])
            self.assertEqual(storage.get_core_memory(AGENT_ID, USER_ID), "before")

    def test_node_documents_cannot_cross_relationship_scope(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            storage = FileStorage(root_dir)
            foreign = memory_node()
            foreign.user_id = "user_other"

            with self.assertRaises(ValueError):
                storage.save_nodes(AGENT_ID, USER_ID, [foreign])

            node_path = Path(storage._get_nodes_path(AGENT_ID, USER_ID))
            node_path.write_text(
                '[{"node_id":"node-one","agent_id":"agent_lumi",'
                '"user_id":"user_other","node_type":"fact",'
                '"content":"foreign","base_importance":0.8,'
                '"decayable":false}]',
                encoding="utf-8",
            )
            before = node_path.read_bytes()

            with self.assertRaises(StorageIntegrityError):
                storage.load_nodes(AGENT_ID, USER_ID)
            with self.assertRaises(StorageIntegrityError):
                storage.save_nodes(AGENT_ID, USER_ID, [])

            self.assertEqual(node_path.read_bytes(), before)


class SQLiteStorageIntegrityTests(unittest.TestCase):
    def test_damaged_node_row_never_becomes_a_partial_or_empty_collection(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            db_path = str(Path(root_dir) / "memory.db")
            storage = SQLiteStorage(db_path)
            storage.save_nodes(AGENT_ID, USER_ID, [memory_node()])
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "UPDATE memory_nodes SET data = ? WHERE node_id = ?",
                    ('{"truncated": ', "node-one"),
                )
                conn.commit()

            with self.assertRaises(StorageIntegrityError):
                storage.load_nodes(AGENT_ID, USER_ID)
            with self.assertRaises(StorageIntegrityError):
                storage.save_nodes(AGENT_ID, USER_ID, [])

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    "SELECT data FROM memory_nodes WHERE node_id = ?",
                    ("node-one",),
                ).fetchone()
            self.assertEqual(row[0], '{"truncated": ')

    def test_damaged_structured_timeline_row_never_becomes_legacy_data(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            db_path = str(Path(root_dir) / "memory.db")
            storage = SQLiteStorage(db_path)
            storage.add_timeline_entry(
                AGENT_ID,
                USER_ID,
                "A valid timeline entry.",
            )
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "UPDATE timeline_entries SET data = ? WHERE agent_id = ? AND user_id = ?",
                    ('{"truncated": ', AGENT_ID, USER_ID),
                )
                conn.commit()

            with self.assertRaises(StorageIntegrityError):
                storage.get_recent_timeline(AGENT_ID, USER_ID)
            with self.assertRaises(StorageIntegrityError):
                storage.list_timeline_entries(AGENT_ID, USER_ID)

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    "SELECT data FROM timeline_entries WHERE agent_id = ? AND user_id = ?",
                    (AGENT_ID, USER_ID),
                ).fetchone()
            self.assertEqual(row[0], '{"truncated": ')


if __name__ == "__main__":
    unittest.main()
