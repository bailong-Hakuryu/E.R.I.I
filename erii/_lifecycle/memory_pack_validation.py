"""Pure semantic validation for lifecycle MemoryPack planning."""

from __future__ import annotations

from erii._engine.memory_pack_analysis import (
    analyze_memory_pack,
    validate_memory_pack_persisted_turn_adjudications,
    validate_memory_pack_relationship_processing,
    validate_memory_pack_turn_records,
)
from erii.core.memory_pack_evidence import validate_memory_pack_archival_evidence
from erii.errors import StorageIntegrityError
from erii.models.pack import MemoryPack


def validate_memory_pack_semantic_graph(pack: MemoryPack) -> None:
    """Validate every portable graph without opening Storage or an Engine."""
    if not isinstance(pack, MemoryPack):
        raise TypeError("MemoryPack semantic validation requires a MemoryPack")
    try:
        analyze_memory_pack(pack)
        validate_memory_pack_turn_records(pack)
        validate_memory_pack_archival_evidence(pack)
        validate_memory_pack_persisted_turn_adjudications(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
        )
        validate_memory_pack_relationship_processing(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
        )
    except ValueError as exc:
        raise StorageIntegrityError(
            "MemoryPack semantic graph validation failed"
        ) from exc


__all__ = ["validate_memory_pack_semantic_graph"]
