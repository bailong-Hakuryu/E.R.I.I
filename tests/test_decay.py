"""Unit tests for MemoryDecayEvaluator and MemoryNode weight calculations."""

import unittest
from erii.core.decay import MemoryDecayEvaluator
from erii.models.node import MemoryNode, MemoryState, MemoryType


class TestMemoryDecay(unittest.TestCase):

    def test_effective_weight_calculation(self):
        node = MemoryNode(
            node_id="node_1",
            user_id="u1",
            node_type=MemoryType.FACT,
            content="User likes python",
            base_importance=0.5,
        )
        weight = node.calculate_effective_weight(decay_rate=0.05)
        self.assertTrue(0.0 <= weight <= 0.95)

    def test_recall_reinforcement(self):
        node = MemoryNode(
            node_id="node_1",
            user_id="u1",
            node_type=MemoryType.FACT,
            content="User likes python",
            base_importance=0.5,
            access_count=0,
        )
        initial_importance = node.base_importance
        node.reinforce_recall(boost=0.1)
        self.assertEqual(node.access_count, 1)
        self.assertGreater(node.base_importance, initial_importance)

    def test_decay_sweep(self):
        evaluator = MemoryDecayEvaluator(decay_rate=0.05)
        nodes = [
            MemoryNode(
                node_id="node_1",
                user_id="u1",
                node_type=MemoryType.FACT,
                content="Active high importance fact",
                base_importance=0.8,
            ),
            MemoryNode(
                node_id="node_2",
                user_id="u1",
                node_type=MemoryType.FACT,
                content="Weak low importance fact",
                base_importance=0.05,
            ),
        ]
        swept = evaluator.sweep_nodes(nodes)
        self.assertEqual(swept[0].state, MemoryState.ACTIVE)
        self.assertEqual(swept[1].state, MemoryState.WEAK)


if __name__ == "__main__":
    unittest.main()
