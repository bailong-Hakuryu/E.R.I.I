"""Vector Retrieval package for E.R.I.I. Engine."""

from erii.vector.base import BaseEmbeddingProvider, BaseVectorStore, VectorIsolationError
from erii.vector.in_memory_vector import (
    CallableEmbeddingAdapter,
    DummyEmbeddingProvider,
    InMemoryVectorStore,
)

__all__ = [
    "BaseEmbeddingProvider",
    "BaseVectorStore",
    "VectorIsolationError",
    "CallableEmbeddingAdapter",
    "DummyEmbeddingProvider",
    "InMemoryVectorStore",
]
