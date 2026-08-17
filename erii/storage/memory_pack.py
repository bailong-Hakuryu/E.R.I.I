"""Versioned deep storage seam for atomic MemoryPack payload execution."""

from typing import Any, Callable, Optional, Protocol, TypeVar
import uuid


_ResultT = TypeVar("_ResultT")


def memory_pack_remap_scope_id(target_agent: str, target_user: str) -> str:
    """Returns the stable provisional scope shared by remap guards and receipts."""
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"erii:relationship-import:{target_agent}:{target_user}",
        )
    )


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


class AtomicMemoryPackWriteStoreV2(Protocol):
    """Adds a durable, versioned result receipt to whole-pack execution."""

    def load_memory_pack_write_result(
        self,
        operation_id: str,
        target_agent: str,
        target_user: str,
        relationship_id: Optional[str],
        deserialize_result: Callable[[str], _ResultT],
    ) -> Optional[_ResultT]:
        """Returns a previously committed result without replaying payload writes."""
        ...

    def execute_memory_pack_write_v2(
        self,
        operation_id: str,
        target_agent: str,
        target_user: str,
        relationship_id: Optional[str],
        operation: Callable[[Any], _ResultT],
        serialize_result: Callable[[_ResultT], str],
        deserialize_result: Callable[[str], _ResultT],
        *,
        lock_relationship_id: Optional[str],
    ) -> _ResultT:
        """Commits payload and receipt while locking the actual target relationship."""
        ...


__all__ = [
    "AtomicMemoryPackWriteStoreV1",
    "AtomicMemoryPackWriteStoreV2",
    "memory_pack_remap_scope_id",
]
