"""Canonical aliases for the current lifecycle contracts.

The contracts still live in :mod:`erii.data_lifecycle` until the complete R2
contract extraction can move every dependent type atomically. Keeping aliases
here avoids a second, behaviorally different set of enums and dataclasses while
preserving the intended private import seam.
"""

from erii.data_lifecycle import (
    BackupRequest,
    EraseRequest,
    LifecycleAssessment,
    LifecycleContentIdentity,
    LifecycleDirectoryIdentity,
    LifecycleOperation,
    LifecycleOutcome,
    LifecyclePlan,
    LifecycleReport,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    MemoryPackImportOptions,
    MemoryPackImportRequest,
    RebuildRequest,
    RestoreRequest,
    UpgradeRequest,
)

__all__ = [
    "BackupRequest",
    "EraseRequest",
    "LifecycleAssessment",
    "LifecycleContentIdentity",
    "LifecycleDirectoryIdentity",
    "LifecycleOperation",
    "LifecycleOutcome",
    "LifecyclePlan",
    "LifecycleReport",
    "LifecycleStatus",
    "LifecycleTarget",
    "LifecycleTargetKind",
    "MemoryPackImportOptions",
    "MemoryPackImportRequest",
    "RebuildRequest",
    "RestoreRequest",
    "UpgradeRequest",
]
