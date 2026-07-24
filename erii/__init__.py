"""E.R.I.I. — Experiential Recall & Impression Integration Engine

Export public API symbols.
Follows Google Python Style Guide.
"""

from erii.adapters.base import BaseLLMAdapter
from erii.adapters.custom_adapter import CallableLLMAdapter
from erii.adapters.openai_adapter import OpenAIAdapter
from erii.core.queue.base import BaseTaskQueue
from erii.core.queue.persistent_queue import PersistentTaskQueue
from erii.engine import ERIIEngine
from erii.models.config import ERIIConfig
from erii.models.node import MemoryNode, MemoryState, MemoryType, MemoryVisibility
from erii.models.pack import MemoryPack
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage
from erii.storage.file_storage import FileStorage
from erii.storage.sqlite_storage import SQLiteStorage
from erii.vector.base import BaseEmbeddingProvider, BaseVectorStore
from erii.vector.in_memory_vector import (
    CallableEmbeddingAdapter,
    DummyEmbeddingProvider,
    InMemoryVectorStore,
)

__version__ = "0.2.0"

__all__ = [
    "ERIIEngine",
    "ERIIConfig",
    "MemoryNode",
    "MemoryType",
    "MemoryState",
    "MemoryVisibility",
    "MemoryPack",
    "BaseLLMAdapter",
    "CallableLLMAdapter",
    "OpenAIAdapter",
    "BaseStorage",
    "FileStorage",
    "SQLiteStorage",
    "SecuritySanitizer",
    "BaseTaskQueue",
    "PersistentTaskQueue",
    "BaseEmbeddingProvider",
    "BaseVectorStore",
    "CallableEmbeddingAdapter",
    "DummyEmbeddingProvider",
    "InMemoryVectorStore",
]
