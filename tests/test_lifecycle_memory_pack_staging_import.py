"""Staging-only MemoryPack import contract for the v0.4 Beta lifecycle."""

from pathlib import Path
import json
import re
import tempfile
import unittest

from erii.engine import ERIIEngine
from erii.lifecycle_memory_pack_import import (
    MemoryPackStagingAdapter,
    MemoryPackStagingImportRequest,
    MemoryPackStagingImporter,
)
from erii.models.pack import MemoryPack
from erii.storage.file_storage import FileStorage
from erii.storage.sqlite_storage import SQLiteStorage


FIXTURE_SOURCE = (
    Path(__file__).parent
    / "fixtures"
    / "lifecycle"
    / "memory-pack-v0.4.0a7"
    / "source.erii"
)


class MemoryPackStagingImportTests(unittest.TestCase):
    @staticmethod
    def _staging_path(
        root: Path,
        adapter: MemoryPackStagingAdapter,
    ) -> Path:
        if adapter == MemoryPackStagingAdapter.FILE_STORAGE:
            return root / "file-stage"
        return root / "sqlite-stage.sqlite3"

    @staticmethod
    def _engine(
        staging_path: Path,
        adapter: MemoryPackStagingAdapter,
    ) -> ERIIEngine:
        if adapter == MemoryPackStagingAdapter.FILE_STORAGE:
            storage = FileStorage(root_dir=str(staging_path))
        else:
            storage = SQLiteStorage(db_path=str(staging_path))
        return ERIIEngine(storage_driver=storage)

    def test_real_a7_pack_imports_and_exact_retry_is_stable(self) -> None:
        pack = MemoryPack.from_json(FIXTURE_SOURCE.read_text("utf-8"))
        reports = []

        for adapter in MemoryPackStagingAdapter:
            with self.subTest(adapter=adapter.value), tempfile.TemporaryDirectory() as raw:
                staging_path = self._staging_path(Path(raw), adapter)
                request = MemoryPackStagingImportRequest(
                    adapter=adapter,
                    staging_path=str(staging_path),
                    pack=pack,
                )
                importer = MemoryPackStagingImporter()

                first = importer.import_pack(request)
                second = importer.import_pack(request)

                self.assertEqual(second.to_dict(), first.to_dict())
                self.assertEqual(first.adapter, adapter)
                self.assertEqual(first.agent_id, pack.agent_id)
                self.assertEqual(first.user_id, pack.user_id)
                self.assertIsNotNone(first.relationship_id)
                self.assertTrue(
                    re.fullmatch(r"[0-9a-f]{64}", first.semantic_sha256)
                )
                self.assertEqual(
                    first.semantic_sha256,
                    "e73a3b8f742f3f5c9a43ac539682347702d532a99ab1d412b3b241eb7beb189f",
                )
                self.assertEqual(first.counts["turn_records"], 1)
                self.assertEqual(first.counts["relationship_events"], 1)
                self.assertEqual(first.counts["relationship_consequences"], 0)
                self.assertEqual(first.counts["narrative_tension_links"], 0)
                serialized = json.dumps(first.to_dict(), ensure_ascii=False)
                self.assertNotIn(pack.core_memory, serialized)
                self.assertNotIn(
                    pack.turn_records[0].transcript.user_message.content,
                    serialized,
                )
                self.assertNotIn(
                    pack.relationship.blueprint.source_text,
                    serialized,
                )
                reports.append(first)

        self.assertEqual(reports[0].semantic_sha256, reports[1].semantic_sha256)
        self.assertEqual(reports[0].counts, reports[1].counts)

    def test_success_preserves_an_unrelated_relationship(self) -> None:
        pack = MemoryPack.from_json(FIXTURE_SOURCE.read_text("utf-8"))

        for adapter in MemoryPackStagingAdapter:
            with self.subTest(adapter=adapter.value), tempfile.TemporaryDirectory() as raw:
                staging_path = self._staging_path(Path(raw), adapter)
                engine = self._engine(staging_path, adapter)
                try:
                    unrelated = engine.initialize_relationship(
                        "other_agent",
                        "other_user",
                        "A separate synthetic character authority.",
                    )
                    engine.set_core_memory(
                        "other_agent",
                        "other_user",
                        "unrelated synthetic memory",
                    )
                finally:
                    engine.close()

                MemoryPackStagingImporter().import_pack(
                    MemoryPackStagingImportRequest(
                        adapter=adapter,
                        staging_path=str(staging_path),
                        pack=pack,
                    )
                )

                engine = self._engine(staging_path, adapter)
                try:
                    preserved = engine.export_memory("other_agent", "other_user")
                finally:
                    engine.close()
                self.assertEqual(
                    preserved.relationship.relationship_id,
                    unrelated.relationship_id,
                )
                self.assertEqual(
                    preserved.core_memory,
                    "unrelated synthetic memory",
                )

    def test_bound_source_history_cannot_be_remapped(self) -> None:
        pack = MemoryPack.from_json(FIXTURE_SOURCE.read_text("utf-8"))

        for adapter in MemoryPackStagingAdapter:
            with self.subTest(adapter=adapter.value), tempfile.TemporaryDirectory() as raw:
                staging_path = self._staging_path(Path(raw), adapter)

                with self.assertRaisesRegex(
                    ValueError,
                    "archival provenance cannot be remapped",
                ):
                    MemoryPackStagingImporter().import_pack(
                        MemoryPackStagingImportRequest(
                            adapter=adapter,
                            staging_path=str(staging_path),
                            pack=pack,
                            target_agent_id="remapped_agent",
                            target_user_id="remapped_user",
                        )
                    )

                engine = self._engine(staging_path, adapter)
                try:
                    self.assertIsNone(
                        engine.storage.get_relationship(
                            "remapped_agent",
                            "remapped_user",
                        )
                    )
                    self.assertIsNone(
                        engine.storage.get_relationship(pack.agent_id, pack.user_id)
                    )
                finally:
                    engine.close()

    def test_unbound_relationship_uses_production_remap_rules(self) -> None:
        with tempfile.TemporaryDirectory() as source_raw:
            source_engine = ERIIEngine(storage_dir=source_raw)
            try:
                source_engine.initialize_relationship(
                    "source_agent",
                    "source_user",
                    "A synthetic authority that can be remapped before history exists.",
                )
                pack = source_engine.export_memory("source_agent", "source_user")
            finally:
                source_engine.close()

        for adapter in MemoryPackStagingAdapter:
            with self.subTest(adapter=adapter.value), tempfile.TemporaryDirectory() as raw:
                staging_path = self._staging_path(Path(raw), adapter)
                request = MemoryPackStagingImportRequest(
                    adapter=adapter,
                    staging_path=str(staging_path),
                    pack=pack,
                    target_agent_id="target_agent",
                    target_user_id="target_user",
                )
                importer = MemoryPackStagingImporter()

                first = importer.import_pack(request)
                second = importer.import_pack(request)

                self.assertEqual(second.to_dict(), first.to_dict())
                self.assertEqual(first.agent_id, "target_agent")
                self.assertEqual(first.user_id, "target_user")
                self.assertNotEqual(
                    first.relationship_id,
                    pack.relationship.relationship_id,
                )
                engine = self._engine(staging_path, adapter)
                try:
                    self.assertIsNone(
                        engine.storage.get_relationship("source_agent", "source_user")
                    )
                    stored = engine.storage.get_relationship(
                        "target_agent",
                        "target_user",
                    )
                finally:
                    engine.close()
                self.assertEqual(stored.relationship_id, first.relationship_id)

    def test_invalid_graph_fails_before_business_records_are_written(self) -> None:
        pack = MemoryPack.from_json(FIXTURE_SOURCE.read_text("utf-8"))
        pack.turn_records.append(pack.turn_records[0])

        for adapter in MemoryPackStagingAdapter:
            with self.subTest(adapter=adapter.value), tempfile.TemporaryDirectory() as raw:
                staging_path = self._staging_path(Path(raw), adapter)

                with self.assertRaisesRegex(ValueError, "duplicate turn_id"):
                    MemoryPackStagingImporter().import_pack(
                        MemoryPackStagingImportRequest(
                            adapter=adapter,
                            staging_path=str(staging_path),
                            pack=pack,
                        )
                    )

                engine = self._engine(staging_path, adapter)
                try:
                    self.assertIsNone(
                        engine.storage.get_relationship(pack.agent_id, pack.user_id)
                    )
                    self.assertEqual(
                        engine.storage.load_nodes(pack.agent_id, pack.user_id),
                        [],
                    )
                finally:
                    engine.close()


if __name__ == "__main__":
    unittest.main()
