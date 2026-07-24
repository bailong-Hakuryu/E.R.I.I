"""Tests for E.R.I.I. Inner Monologue, Diary Timeline & Narrative Tension features."""

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

from erii import ERIIEngine, MemoryNode, MemoryType, MemoryVisibility


class TestMonologueNarrative(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.engine = ERIIEngine(storage_dir=self.tmp_dir)

    def tearDown(self):
        self.engine.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_remember_thought_and_diary_timeline(self):
        """Tests creating thoughts with timestamps and querying public diary timeline."""
        ts1 = "2026-07-24 10:00:00"
        ts2 = "2026-07-24 12:00:00"

        node1 = self.engine.remember_thought(
            agent_id="sakura_agent",
            user_id="user_bob",
            content="sakura要带我去公园我好开心",
            visibility="public_log",
            is_unresolved=True,
            emotional_score=0.9,
            foreshadowing_tags=["park_visit", "joy"],
            created_at=ts1,
        )

        node2 = self.engine.remember_thought(
            agent_id="sakura_agent",
            user_id="user_bob",
            content="在公园买到了好吃的风筝型冰淇淋",
            visibility="public_log",
            is_unresolved=False,
            emotional_score=0.5,
            created_at=ts2,
        )

        # Retrieve public diary timeline
        timeline = self.engine.get_diary_timeline(
            agent_id="sakura_agent",
            user_id="user_bob",
        )

        self.assertTrue(len(timeline) >= 2)
        # Check unresolved thought is placed at top priority
        self.assertEqual(timeline[0]["content"], "sakura要带我去公园我好开心")
        self.assertTrue(timeline[0]["is_unresolved"])
        self.assertEqual(timeline[0]["created_at"], ts1)

    def test_visibility_isolation(self):
        """Tests that INTERNAL_MONOLOGUE thoughts are hidden from PUBLIC_LOG queries."""
        self.engine.remember_thought(
            agent_id="agent_secret",
            user_id="user_secret",
            content="公开日记：今天天气真好",
            visibility="public_log",
        )
        self.engine.remember_thought(
            agent_id="agent_secret",
            user_id="user_secret",
            content="绝密独白：我是第三幕的犯人，绝不能让主角发现",
            visibility="internal_monologue",
        )

        # Public query
        public_logs = self.engine.get_inner_monologue(
            agent_id="agent_secret",
            user_id="user_secret",
            visibility="public_log",
        )
        self.assertEqual(len(public_logs), 1)
        self.assertEqual(public_logs[0]["content"], "公开日记：今天天气真好")

        # Internal query
        internal_logs = self.engine.get_inner_monologue(
            agent_id="agent_secret",
            user_id="user_secret",
            visibility="internal_monologue",
        )
        self.assertEqual(len(internal_logs), 1)
        self.assertIn("犯人", internal_logs[0]["content"])

    def test_unresolved_suspense_holdback_decay(self):
        """Tests that unresolved thoughts retain max dynamic weight despite time decay."""
        past_time = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        node = MemoryNode(
            node_id="node_suspense",
            user_id="user_1",
            agent_id="agent_1",
            node_type=MemoryType.THOUGHT,
            content="明天体检希望一切平安...",
            base_importance=0.8,
            is_unresolved=True,
            last_accessed_at=past_time,
            created_at=past_time,
        )

        # Effective weight for unresolved thought should not decay to weak state
        weight = node.calculate_effective_weight(decay_rate=0.1)
        self.assertGreaterEqual(weight, 0.7)

    def test_resolve_thought_status(self):
        """Tests resolving an unresolved suspense thought node."""
        node = self.engine.remember_thought(
            agent_id="sakura_agent",
            user_id="user_bob",
            content="不知道能不能赶上末班车",
            is_unresolved=True,
        )

        # Verify initial unresolved status
        unresolved_logs = self.engine.get_inner_monologue(
            agent_id="sakura_agent",
            user_id="user_bob",
            unresolved_only=True,
        )
        self.assertEqual(len(unresolved_logs), 1)
        self.assertEqual(unresolved_logs[0]["node_id"], node.node_id)

        # Resolve thought
        success = self.engine.resolve_thought("sakura_agent", "user_bob", node.node_id)
        self.assertTrue(success)

        # Verify unresolved query returns empty
        unresolved_after = self.engine.get_inner_monologue(
            agent_id="sakura_agent",
            user_id="user_bob",
            unresolved_only=True,
        )
        self.assertEqual(len(unresolved_after), 0)


if __name__ == "__main__":
    unittest.main()
