"""Versioned deep storage seam for atomic MemoryPack payload execution."""

from typing import Any, Callable, Optional, Protocol, TypeVar


_ResultT = TypeVar("_ResultT")


class AtomicMemoryPackWriteStoreV1(Protocol):
    """Runs one frozen payload operation behind an adapter atomic boundary."""

    def execute_memory_pack_write(
        self,
        target_agent: str,
        target_user: str,
        relationship_id: Optional[str],
        operation: Callable[[Any], _ResultT],
    ) -> _ResultT:
        """Commits every operation write together or preserves the baseline."""
        ...


__all__ = ["AtomicMemoryPackWriteStoreV1"]
