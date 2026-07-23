"""Memory Decay and Weight Evaluation Engine for E.R.I.I.

Follows Google Python Style Guide.
"""

from typing import List
from erii.models.node import MemoryNode, MemoryState


class MemoryDecayEvaluator:
    """Evaluates time decay, access frequency boosts, and memory lifecycle states."""

    def __init__(self, decay_rate: float = 0.05, max_weight_cap: float = 0.95) -> None:
        """Initializes MemoryDecayEvaluator.

        Args:
            decay_rate: Dynamic decay coefficient lambda.
            max_weight_cap: Saturation weight cap.
        """
        self.decay_rate = decay_rate
        self.max_weight_cap = max_weight_cap

    def evaluate_node(self, node: MemoryNode) -> float:
        """Calculates dynamic weight for a single node.

        Args:
            node: Target MemoryNode.

        Returns:
            Calculated dynamic effective weight float.
        """
        return node.calculate_effective_weight(
            decay_rate=self.decay_rate, max_weight_cap=self.max_weight_cap
        )

    def sweep_nodes(self, nodes: List[MemoryNode]) -> List[MemoryNode]:
        """Performs lifecycle sweep over memory node list.

        Updates effective weights and transitions low-weight active nodes to WEAK state.

        Args:
            nodes: List of MemoryNode objects.

        Returns:
            List of updated MemoryNode objects.
        """
        for node in nodes:
            weight = self.evaluate_node(node)
            if weight < 0.15 and node.state == MemoryState.ACTIVE:
                node.state = MemoryState.WEAK
        return nodes
