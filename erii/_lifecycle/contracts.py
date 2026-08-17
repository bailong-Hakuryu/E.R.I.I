"""
Lifecycle contracts: public types for lifecycle operations.

This module contains the stable public contracts for E.R.I.I. data lifecycle
operations. These types are re-exported from erii.data_lifecycle for backward
compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from erii.compatibility import FormatCompatibility

__all__ = [
    "LifecycleTargetKind",
    "LifecycleStatus",
    "LifecycleTarget",
    "LifecycleAssessment",
    "LifecycleOperation",
    "LifecycleOutcome",
    "LifecycleContentIdentity",
    "BackupRequest",
    "RestoreRequest", 
    "UpgradeRequest",
    "EraseRequest",
    "RebuildRequest",
    "MemoryPackImportOptions",
    "MemoryPackImportRequest",
    "LifecycleDirectoryIdentity",
    "LifecyclePlan",
    "LifecycleReport",
]


class LifecycleTargetKind(str, Enum):
    """Durable source families understood by the Beta inspector."""

    FILE_STORAGE = "file_storage"
    SQLITE = "sqlite"
    MEMORY_PACK = "memory_pack"
    BACKUP = "backup"


class LifecycleStatus(str, Enum):
    """Read-only compatibility result for one source."""

    MISSING = "missing"
    EMPTY = "empty"
    CURRENT = "current"
    MIGRATION_REQUIRED = "migration_required"


class LifecycleOperation(str, Enum):
    """Approved lifecycle operations."""

    BACKUP = "backup"
    RESTORE = "restore"
    UPGRADE = "upgrade"
    ERASE = "erase"
    REBUILD = "rebuild"
    MEMORY_PACK_IMPORT = "memory_pack_import"


class LifecycleOutcome(str, Enum):
    """Final result after plan execution."""

    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class LifecycleTarget:
    """Names one physical lifecycle source without opening it."""

    kind: LifecycleTargetKind
    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LifecycleTargetKind):
            raise TypeError("LifecycleTarget kind must be a LifecycleTargetKind")
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("LifecycleTarget path must be a non-empty string")
        import os
        object.__setattr__(self, "path", os.path.abspath(self.path))


@dataclass(frozen=True, slots=True)
class LifecycleAssessment:
    """Read-only inspection result."""

    target: LifecycleTarget
    status: LifecycleStatus
    format_id: str
    detected_version: Optional[str]
    current_version: str
    fingerprint: Optional[str]
    file_count: int
    warnings: Tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LifecycleContentIdentity:
    """Normalized content identification for plan verification."""

    kind: LifecycleTargetKind
    status: LifecycleStatus
    format_id: str
    detected_version: Optional[str]
    current_version: str
    fingerprint: Optional[str]
    file_count: int


# Request types will be added next
