"""In-Memory Pure Python Vector Store driver for E.R.I.I. Engine.

Follows Google Python Style Guide.
"""

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from erii.vector.base import BaseEmbeddingProvider, BaseVectorStore


class CallableEmbeddingAdapter(BaseEmbeddingProvider):
    """Adapter for wrapping arbitrary Python callable (text) -> List[float]."""

    def __init__(self, embed_fn: Callable[[str], List[float]]) -> None:
        self.embed_fn = embed_fn

    def embed_text(self, text: str) -> List[float]:
        return self.embed_fn(text)


class DummyEmbeddingProvider(BaseEmbeddingProvider):
    """Deterministic fallback embedding generator based on character n-grams."""

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed_text(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        if not text:
            return vec
        for char in text.lower():
            idx = ord(char) % self.dim
            vec[idx] += 1.0
        # Normalize to unit vector
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


class InMemoryVectorStore(BaseVectorStore):
    """Pure Python in-memory vector store using cosine similarity."""

    def __init__(self) -> None:
        # Map node_id -> {"vector": List[float], "metadata": Dict[str, Any]}
        self.records: Dict[str, Dict[str, Any]] = {}

    def upsert(
        self,
        node_id: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.records[node_id] = {
            "vector": vector,
            "metadata": metadata or {},
        }

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[str, float]]:
        results: List[Tuple[str, float]] = []

        for node_id, record in self.records.items():
            meta = record["metadata"]
            if filter_metadata:
                match = True
                for k, v in filter_metadata.items():
                    if meta.get(k) != v:
                        match = False
                        break
                if not match:
                    continue

            sim = self._cosine_similarity(query_vector, record["vector"])
            results.append((node_id, sim))

        results.sort(key=lambda item: item[1], reverse=True)
        return results[:top_k]
