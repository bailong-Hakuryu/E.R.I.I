"""Compatibility tests for storage adapters implemented before v0.4.0a3."""

import os
import tempfile
import unittest

from erii import ERIIEngine, FileStorage, RelationshipEventType


class Alpha2OnlyFileStorage(FileStorage):
    """A functional a2 adapter that deliberately lacks a3 persona methods."""

    def save_persona_compilation_proposal(self, *args, **kwargs):
        raise NotImplementedError("a3 persona compilation is not supported")

    def list_persona_compilation_proposals(self, *args, **kwargs):
        raise NotImplementedError("a3 persona compilation is not supported")

    def approve_persona_manifest(self, *args, **kwargs):
        raise NotImplementedError("a3 persona manifests are not supported")

    def get_persona_manifest(self, *args, **kwargs):
        raise NotImplementedError("a3 persona manifests are not supported")

    def list_persona_manifests(self, *args, **kwargs):
        raise NotImplementedError("a3 persona manifests are not supported")

    def bind_relationship_manifest(self, *args, **kwargs):
        raise NotImplementedError("a3 persona manifests are not supported")


class StorageAdapterCompatibilityTest(unittest.TestCase):
    def test_export_keeps_a2_relationship_data_when_a3_lists_are_unsupported(self):
        with tempfile.TemporaryDirectory() as root_dir:
            storage = Alpha2OnlyFileStorage(root_dir=os.path.join(root_dir, "source"))
            with ERIIEngine(storage_driver=storage) as engine:
                profile = engine.initialize_relationship(
                    "agent_lumi",
                    "user_chen",
                    "Lumi is an original patient character.",
                )
                event = engine.record_relationship_event(
                    "agent_lumi",
                    "user_chen",
                    RelationshipEventType.SHARED_EXPERIENCE,
                    "They watched the first snow together.",
                )

                pack = engine.export_memory("agent_lumi", "user_chen")

            self.assertEqual(pack.relationship, profile)
            self.assertEqual([item.event_id for item in pack.relationship_events], [event.event_id])
            self.assertEqual(pack.persona_compilation_proposals, [])
            self.assertEqual(pack.persona_manifests, [])

    def test_import_of_a2_pack_does_not_call_a3_storage_methods(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_dir=os.path.join(root_dir, "source")) as source:
                source.initialize_relationship(
                    "agent_lumi",
                    "user_chen",
                    "Lumi is an original patient character.",
                )
                source_event = source.record_relationship_event(
                    "agent_lumi",
                    "user_chen",
                    RelationshipEventType.SHARED_EXPERIENCE,
                    "They watched the first snow together.",
                )
                old_pack = source.export_memory("agent_lumi", "user_chen")

            target_storage = Alpha2OnlyFileStorage(
                root_dir=os.path.join(root_dir, "target")
            )
            with ERIIEngine(storage_driver=target_storage) as target:
                target.import_memory(old_pack)

                imported = target.get_relationship_snapshot("agent_lumi", "user_chen")
                events = target.list_relationship_events("agent_lumi", "user_chen")

            self.assertEqual(imported.event_count, 1)
            self.assertEqual([item.event_id for item in events], [source_event.event_id])


if __name__ == "__main__":
    unittest.main()
