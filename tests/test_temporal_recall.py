"""Unit test for temporal timestamp anchoring in recall() context formatting.

Follows Google Python Style Guide.
"""

import shutil
import tempfile
import unittest

from erii.engine import ERIIEngine
from erii.models.node import MemoryNode, MemoryType


class TestTemporalRecall(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.engine = ERIIEngine(storage_dir=self.test_dir)

    def tearDown(self):
        self.engine.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_recall_includes_created_at_timestamp(self):
        node = MemoryNode(
            node_id="test_time_node_1",
            agent_id="agent_lumi",
            user_id="player_1",
            content="Lumi promised to take me to the park tomorrow",
            node_type=MemoryType.EVENT,
            created_at="2026-07-23 15:00:00",
        )
        self.engine.storage.save_nodes("agent_lumi", "player_1", [node])

        context = self.engine.recall("agent_lumi", "player_1", query="park tomorrow")
        self.assertIn("[2026-07-23 15:00:00]", context)
        self.assertIn("Lumi promised to take me to the park tomorrow", context)


if __name__ == "__main__":
    unittest.main()
