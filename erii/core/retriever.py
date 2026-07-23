"""Memory Retriever and Diversity Cap Engine for E.R.I.I.

Follows Google Python Style Guide.
"""

from collections import defaultdict
import re
from typing import Dict, List, Set

from erii.models.node import MemoryNode, MemoryType


class MemoryRetriever:
    """Retrieves relevant memory nodes using term matching and diversity cap filtering."""

    def __init__(self, decay_rate: float = 0.05) -> None:
        """Initializes MemoryRetriever.

        Args:
            decay_rate: Decay rate coefficient passed to node scoring.
        """
        self.decay_rate = decay_rate

    @staticmethod
    def tokenize(text: str) -> Set[str]:
        """Tokenizes input query text into normalized word/character tokens.

        Args:
            text: Raw string input text.

        Returns:
            Set of string tokens.
        """
        if not text:
            return set()
        # Clean text and split by non-alphanumeric/non-CJK characters
        words = re.findall(r"[\w\u4e00-\u9fa5]+", text.lower())
        tokens = set(words)
        # Also add individual CJK characters for better Chinese keyword matching
        for word in words:
            if re.search(r"[\u4e00-\u9fa5]", word):
                tokens.update(list(word))
        return tokens

    def rank_candidates(
        self, query: str, candidates: List[MemoryNode]
    ) -> List[MemoryNode]:
        """Ranks candidate nodes by keyword overlap and dynamic effective weight.

        Args:
            query: Search query string.
            candidates: List of MemoryNode candidates.

        Returns:
            Ranked list of MemoryNode objects sorted by relevance score descending.
        """
        query_tokens = self.tokenize(query)
        if not query_tokens:
            # If query tokens empty, rank purely by effective weight
            return sorted(
                candidates,
                key=lambda n: n.calculate_effective_weight(self.decay_rate),
                reverse=True,
            )

        scored_nodes = []
        for node in candidates:
            node_text = f"{node.content} {' '.join(node.tags)}"
            node_tokens = self.tokenize(node_text)
            overlap = len(query_tokens.intersection(node_tokens))

            # Relevance score = keyword overlap bonus + effective dynamic weight
            relevance_score = (overlap * 0.3) + node.calculate_effective_weight(
                self.decay_rate
            )
            scored_nodes.append((relevance_score, node))

        scored_nodes.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored_nodes]

    def retrieve_relevant_nodes(
        self,
        query: str,
        all_nodes: List[MemoryNode],
        top_k: int = 5,
        max_per_type: int = 2,
    ) -> List[MemoryNode]:
        """Retrieves top relevant nodes applying Category Diversity Cap filtering.

        Args:
            query: Search query string.
            all_nodes: Master list of all MemoryNode items for user.
            top_k: Maximum total nodes to retrieve.
            max_per_type: Diversity cap per MemoryType to prevent mono-topic dominance.

        Returns:
            List of selected, reinforced MemoryNode objects.
        """
        if not all_nodes:
            return []

        ranked_nodes = self.rank_candidates(query, all_nodes)
        selected_nodes: List[MemoryNode] = []
        type_counts: Dict[MemoryType, int] = defaultdict(int)

        for node in ranked_nodes:
            if len(selected_nodes) >= top_k:
                break

            # Core memories bypass diversity cap
            if node.node_type != MemoryType.CORE:
                if type_counts[node.node_type] >= max_per_type:
                    continue

            node.reinforce_recall(boost=0.08)
            selected_nodes.append(node)
            type_counts[node.node_type] += 1

        return selected_nodes
