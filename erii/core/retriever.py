"""Memory Retriever and Diversity Cap Engine for E.R.I.I.

Implements RRF (Reciprocal Rank Fusion) hybrid retrieval combining keyword matching,
vector embeddings, and dynamic exponential weight decay.

Follows Google Python Style Guide.
"""

from collections import defaultdict
import re
from typing import Dict, List, Optional, Set

from erii.models.node import MemoryNode, MemoryType
from erii.vector.base import BaseEmbeddingProvider, BaseVectorStore


class MemoryRetriever:
    """Retrieves relevant memory nodes using RRF hybrid search and diversity cap filtering."""

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
        words = re.findall(r"[\w\u4e00-\u9fa5]+", text.lower())
        tokens = set(words)
        for word in words:
            if re.search(r"[\u4e00-\u9fa5]", word):
                tokens.update(list(word))
        return tokens

    def rank_candidates(
        self,
        query: str,
        candidates: List[MemoryNode],
        vector_store: Optional[BaseVectorStore] = None,
        embedding_provider: Optional[BaseEmbeddingProvider] = None,
        rrf_k: float = 60.0,
    ) -> List[MemoryNode]:
        """Ranks candidate nodes using RRF (Reciprocal Rank Fusion) or term overlap.

        Args:
            query: Search query string.
            candidates: List of MemoryNode candidates.
            vector_store: Optional BaseVectorStore instance.
            embedding_provider: Optional BaseEmbeddingProvider instance.
            rrf_k: RRF constant factor (default 60.0).

        Returns:
            Ranked list of MemoryNode objects sorted by score descending.
        """
        if not candidates:
            return []

        # 1. Compute Keyword Overlap Scores & Ranks
        query_tokens = self.tokenize(query)
        kw_scores: List[Tuple[float, MemoryNode]] = []
        for node in candidates:
            if not query_tokens:
                overlap = 0.0
            else:
                node_text = f"{node.content} {' '.join(node.tags)}"
                node_tokens = self.tokenize(node_text)
                overlap = float(len(query_tokens.intersection(node_tokens)))
            kw_scores.append((overlap, node))

        # Sort descending by keyword overlap
        kw_scores.sort(key=lambda item: item[0], reverse=True)
        kw_rank_map: Dict[str, int] = {
            item[1].node_id: idx + 1 for idx, item in enumerate(kw_scores)
        }

        # 2. If vector retrieval is active, compute Vector Ranks
        vec_rank_map: Dict[str, int] = {}
        if vector_store is not None and embedding_provider is not None and query:
            try:
                query_vector = embedding_provider.embed_text(query)
                # Ensure all candidate nodes are indexed in vector store
                for node in candidates:
                    text_to_embed = f"{node.content} {' '.join(node.tags)}"
                    node_vec = embedding_provider.embed_text(text_to_embed)
                    vector_store.upsert(node.node_id, node_vec, {"node_id": node.node_id})

                vec_results = vector_store.search(query_vector, top_k=len(candidates))
                for idx, (node_id, _sim) in enumerate(vec_results):
                    vec_rank_map[node_id] = idx + 1
            except Exception:
                vec_rank_map = {}

        # 3. Combine scores using RRF & Effective Weight multiplier
        scored_nodes = []
        for node in candidates:
            kw_rank = kw_rank_map.get(node.node_id, len(candidates))
            kw_rrf = 1.0 / (rrf_k + kw_rank)

            if vec_rank_map:
                vec_rank = vec_rank_map.get(node.node_id, len(candidates))
                vec_rrf = 1.0 / (rrf_k + vec_rank)
            else:
                vec_rrf = 0.0

            rrf_score = kw_rrf + vec_rrf
            eff_weight = node.calculate_effective_weight(self.decay_rate)
            final_score = rrf_score * eff_weight

            scored_nodes.append((final_score, node))

        scored_nodes.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored_nodes]

    def retrieve_relevant_nodes(
        self,
        query: str,
        all_nodes: List[MemoryNode],
        top_k: int = 5,
        max_per_type: int = 2,
        vector_store: Optional[BaseVectorStore] = None,
        embedding_provider: Optional[BaseEmbeddingProvider] = None,
    ) -> List[MemoryNode]:
        """Retrieves top relevant nodes applying RRF and Category Diversity Cap.

        Args:
            query: Search query string.
            all_nodes: Master list of all MemoryNode items for user.
            top_k: Maximum total nodes to retrieve.
            max_per_type: Diversity cap per MemoryType.
            vector_store: Optional BaseVectorStore instance.
            embedding_provider: Optional BaseEmbeddingProvider instance.

        Returns:
            List of selected, reinforced MemoryNode objects.
        """
        if not all_nodes:
            return []

        ranked_nodes = self.rank_candidates(
            query=query,
            candidates=all_nodes,
            vector_store=vector_store,
            embedding_provider=embedding_provider,
        )
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
