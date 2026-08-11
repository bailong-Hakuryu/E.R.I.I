"""Base interfaces for Embedding Providers and Vector Stores.

Follows Google Python Style Guide.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


class VectorIsolationError(RuntimeError):
    """Raised when a vector backend cannot prove the requested data scope."""


class BaseEmbeddingProvider(ABC):
    """Abstract interface for text embedding generation."""

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """Embeds a single string into a vector of float values."""
        pass

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embeds a list of strings into a list of vectors."""
        return [self.embed_text(t) for t in texts]


class BaseVectorStore(ABC):
    """Abstract interface for vector similarity search."""

    @abstractmethod
    def upsert(
        self,
        node_id: str,
        vector: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Upserts a vector record with node_id and metadata."""
        pass

    @abstractmethod
    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[str, float]]:
        """Searches nearest neighbor node_ids.

        Returns:
            List of (node_id, similarity_score) tuples sorted by score descending.
        """
        pass
