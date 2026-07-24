"""Base Storage Driver interface for E.R.I.I. Engine.

Follows Google Python Style Guide.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
import threading
from typing import Dict, List, Optional
from erii.models.node import MemoryNode


class KeyLockManager:
    """Manages thread-safe locks per (agent_id, user_id) key pair."""

    def __init__(self) -> None:
        self._locks: Dict[str, threading.RLock] = {}
        self._global_lock = threading.Lock()

    def get_lock(self, agent_id: str, user_id: str) -> threading.RLock:
        key = f"{agent_id}:{user_id}"
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = threading.RLock()
            return self._locks[key]

    @contextmanager
    def lock(self, agent_id: str, user_id: str):
        lock = self.get_lock(agent_id, user_id)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


class BaseStorage(ABC):
    """Abstract interface for memory persistence drivers."""

    def __init__(self) -> None:
        self.lock_manager = KeyLockManager()

    @abstractmethod
    def save_nodes(
        self, agent_id: str, user_id: str, nodes: List[MemoryNode]
    ) -> None:
        """Saves memory nodes to storage.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            nodes: List of MemoryNode objects to save.
        """
        pass

    @abstractmethod
    def load_nodes(self, agent_id: str, user_id: str) -> List[MemoryNode]:
        """Loads memory nodes from storage.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.

        Returns:
            List of MemoryNode objects.
        """
        pass

    @abstractmethod
    def get_core_memory(self, agent_id: str, user_id: str) -> str:
        """Retrieves core memory text for target agent and user.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.

        Returns:
            String content of core memory.
        """
        pass

    @abstractmethod
    def save_core_memory(self, agent_id: str, user_id: str, content: str) -> None:
        """Saves core memory text for target agent and user.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            content: Core memory content string.
        """
        pass

    @abstractmethod
    def add_timeline_entry(
        self, agent_id: str, user_id: str, entry: str, timestamp: Optional[str] = None
    ) -> None:
        """Appends a first-person experiential timeline entry.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            entry: First-person experience timeline text.
            timestamp: Timestamp string (optional).
        """
        pass

    @abstractmethod
    def get_recent_timeline(
        self, agent_id: str, user_id: str, limit: int = 5
    ) -> List[str]:
        """Retrieves recent first-person timeline entries.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            limit: Maximum entries to return.

        Returns:
            List of formatted timeline entry strings.
        """
        pass
