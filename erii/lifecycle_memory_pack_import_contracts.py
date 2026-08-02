"""Serializable contracts for staging-only MemoryPack imports.

This module intentionally has no dependency on the engine or a storage
implementation so the lifecycle coordinator can use the contracts without
creating an import cycle.
"""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional

from erii.models.pack import MemoryPack


STAGING_IMPORT_REPORT_FORMAT = "erii.memory-pack-staging-import-report/v1"


class MemoryPackStagingAdapter(str, Enum):
    """Storage implementation used by an isolated staging target."""

    FILE_STORAGE = "file_storage"
    SQLITE = "sqlite"


@dataclass(frozen=True, slots=True)
class MemoryPackStagingImportRequest:
    """Describes one production import performed inside an isolated target."""

    adapter: MemoryPackStagingAdapter
    staging_path: str
    pack: MemoryPack
    target_agent_id: Optional[str] = None
    target_user_id: Optional[str] = None
    overwrite: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.adapter, MemoryPackStagingAdapter):
            raise TypeError("adapter must be a MemoryPackStagingAdapter")
        if not isinstance(self.staging_path, str) or not self.staging_path.strip():
            raise ValueError("staging_path must be a non-empty string")
        if not isinstance(self.pack, MemoryPack):
            raise TypeError("pack must be a parsed MemoryPack")
        if (self.target_agent_id is None) != (self.target_user_id is None):
            raise ValueError(
                "target_agent_id and target_user_id must be supplied together"
            )
        if not isinstance(self.overwrite, bool):
            raise TypeError("overwrite must be a bool")


@dataclass(frozen=True, slots=True)
class MemoryPackStagingImportReport:
    """Content-free receipt for a completed staging import."""

    adapter: MemoryPackStagingAdapter
    agent_id: str
    user_id: str
    relationship_id: Optional[str]
    semantic_sha256: str
    counts: Mapping[str, int]
    report_format: str = STAGING_IMPORT_REPORT_FORMAT

    def __post_init__(self) -> None:
        if not isinstance(self.adapter, MemoryPackStagingAdapter):
            raise TypeError("adapter must be a MemoryPackStagingAdapter")
        for label, value in (("agent_id", self.agent_id), ("user_id", self.user_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string")
        if self.relationship_id is not None and (
            not isinstance(self.relationship_id, str)
            or not self.relationship_id.strip()
        ):
            raise ValueError("relationship_id must be None or a non-empty string")
        if (
            not isinstance(self.semantic_sha256, str)
            or len(self.semantic_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.semantic_sha256)
        ):
            raise ValueError("semantic_sha256 must be a lowercase SHA-256 digest")
        if self.report_format != STAGING_IMPORT_REPORT_FORMAT:
            raise ValueError("unsupported MemoryPack staging import report format")
        normalized: dict[str, int] = {}
        for name, count in self.counts.items():
            if not isinstance(name, str) or not name:
                raise ValueError("MemoryPack import count names must be non-empty")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("MemoryPack import counts must be non-negative integers")
            normalized[name] = count
        object.__setattr__(self, "counts", MappingProxyType(normalized))

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-compatible report without imported content."""
        return {
            "report_format": self.report_format,
            "adapter": self.adapter.value,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "relationship_id": self.relationship_id,
            "semantic_sha256": self.semantic_sha256,
            "counts": dict(self.counts),
        }


__all__ = [
    "MemoryPackStagingAdapter",
    "MemoryPackStagingImportReport",
    "MemoryPackStagingImportRequest",
    "STAGING_IMPORT_REPORT_FORMAT",
]
