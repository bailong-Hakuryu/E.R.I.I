"""Release-blocking compatibility and narrative contracts for v0.4.0a3."""

import shutil
import tempfile
import unittest

from erii import ERIIEngine, MemoryNode, MemoryType, RecallOptions, RecallRequest
from erii.models.node import MemoryState
from erii.models.relationship import BeliefUpdate, RelationshipEvent, RelationshipEventType


class A3ReleaseContractsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_legacy_recall_keeps_weight_core_node_and_decay_semantics(self):
        engine = ERIIEngine(storage_dir=self.root)
        engine.set_core_memory("lumi", "chen", "authoritative legacy core")
        engine.storage.save_nodes(
            "lumi",
            "chen",
            [
                MemoryNode(
                    node_id="dynamic-core",
                    agent_id="lumi",
                    user_id="chen",
                    node_type=MemoryType.CORE,
                    content="dynamic core node",
                    base_importance=0.8,
                    decayable=False,
                ),
                MemoryNode(
                    node_id="weak-unselected",
                    agent_id="lumi",
                    user_id="chen",
                    node_type=MemoryType.FACT,
                    content="unrelated fading fact",
                    base_importance=0.01,
                    decayable=False,
                ),
            ],
        )

        rendered = engine.recall("lumi", "chen", "dynamic", top_k=1)
        stored = {node.node_id: node for node in engine.storage.load_nodes("lumi", "chen")}

        self.assertIn("# Legacy Context - provenance incomplete", rendered)
        self.assertIn("authoritative legacy core", rendered)
        self.assertIn("[CORE; LEGACY UNRESOLVED IMPRESSION] dynamic core node", rendered)
        self.assertEqual(stored["dynamic-core"].access_count, 0)
        self.assertEqual(stored["weak-unselected"].state, MemoryState.WEAK)
        engine.close()

    def test_structured_recall_reuses_stored_relationship_interpretation(self):
        engine = ERIIEngine(storage_dir=self.root)
        profile = engine.initialize_relationship("lumi", "chen", "Lumi is patient.")
        event = RelationshipEvent(
            event_id="event-first-snow",
            relationship_id=profile.relationship_id,
            event_type=RelationshipEventType.SHARED_EXPERIENCE,
            content="We watched the first snow together.",
            state_delta={"trust": 0.02},
            belief_updates=(
                BeliefUpdate(
                    key="shared_snow_memory",
                    value="This quiet moment matters to us.",
                ),
            ),
            metadata={
                "adjudication": {
                    "persona_reflection": "I want to remember this snow carefully."
                }
            },
        )
        engine.storage.append_relationship_event(event)

        result = engine.recall_structured(
            RecallRequest(
                agent_id="lumi",
                user_id="chen",
                query="snow",
                audience="agent_private",
                options=RecallOptions(persona_delivery="full"),
            )
        )
        rendered = engine.render_recall(result)
        narratives = {
            item.kind: item.content for item in result.relationship_context.narratives
        }

        self.assertEqual(
            narratives["persona_reflection"],
            "I want to remember this snow carefully.",
        )
        self.assertIn("grounded in: We watched the first snow together.", rendered)
        self.assertIn("shared_snow_memory: This quiet moment matters to us.", rendered)
        # Ensure version-like strings don't appear (avoiding timestamp false positives)
        self.assertNotIn("v0.52", rendered)
        self.assertNotIn("version 0.52", rendered)
        engine.close()


if __name__ == "__main__":
    unittest.main()
