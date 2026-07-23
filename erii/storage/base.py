"""Base Storage Driver interface for E.R.I.I. Engine.

Follows Google Python Style Guide.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from erii.models.node import MemoryNode


class BaseStorage(ABC):
    """Abstract interface for memory persistence drivers."""

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
