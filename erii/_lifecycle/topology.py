"""Read-only path topology checks shared by lifecycle planning and execution."""

from __future__ import annotations

import os
from pathlib import Path

from erii._lifecycle.contracts import LifecycleDirectoryIdentity, LifecycleTarget
from erii._lifecycle.filesystem import directory_identity, resolved_path
from erii.errors import LifecyclePlanError, StorageIntegrityError


def paths_overlap(first_value: str, second_value: str) -> bool:
    """Return whether either resolved lifecycle path contains the other."""
    first = resolved_path(Path(first_value), label="lifecycle source")
    second = resolved_path(Path(second_value), label="lifecycle destination")
    try:
        common = os.path.commonpath((first, second))
    except ValueError:
        return False
    return common == first or common == second


def require_safe_destination(
    *,
    source: LifecycleTarget,
    destination: LifecycleTarget,
) -> LifecycleDirectoryIdentity:
    """Bind a missing destination parent and reject source overlap."""
    try:
        overlaps = paths_overlap(source.path, destination.path)
    except StorageIntegrityError as exc:
        raise LifecyclePlanError(
            "lifecycle source or destination path is unsafe"
        ) from exc
    if overlaps:
        raise LifecyclePlanError(
            "lifecycle source and destination cannot overlap"
        )
    try:
        return directory_identity(Path(destination.path).parent)
    except StorageIntegrityError as exc:
        raise LifecyclePlanError(
            "lifecycle destination parent must be an existing regular directory"
        ) from exc


def require_destinations_do_not_overlap(
    first: LifecycleTarget,
    second: LifecycleTarget,
) -> None:
    """Reject two lifecycle destinations whose resolved paths overlap."""
    try:
        overlaps = paths_overlap(first.path, second.path)
    except StorageIntegrityError as exc:
        raise LifecyclePlanError(
            "lifecycle destination paths are unsafe"
        ) from exc
    if overlaps:
        raise LifecyclePlanError("lifecycle destinations cannot overlap")


__all__ = [
    "paths_overlap",
    "require_destinations_do_not_overlap",
    "require_safe_destination",
]
