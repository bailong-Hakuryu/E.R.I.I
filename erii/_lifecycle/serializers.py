"""
Lifecycle Plan Serializers: type conversion for durable lifecycle plans.

This module provides serialization/deserialization functions for lifecycle
plan types, ensuring strict JSON compatibility with historical plan formats.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Any

from erii._lifecycle.plan_codec import is_sha256
from erii.compatibility import FormatCompatibility, require_supported_version
from erii.errors import LifecyclePlanError

if TYPE_CHECKING:
    from erii.data_lifecycle import (
        LifecycleTarget,
        LifecycleAssessment,
        LifecycleContentIdentity,
        LifecycleDirectoryIdentity,
        LifecyclePlan,
        LifecycleOperation,
        LifecyclePlanSelector,
        LifecycleTargetKind,
        LifecycleStatus,
    )

__all__ = [
    "target_to_dict",
    "target_from_dict",
    "assessment_to_dict",
    "assessment_from_dict",
    "content_to_dict",
    "content_from_dict",
    "content_from_backup_manifest",
    "directory_identity_to_dict",
    "directory_identity_from_dict",
    "selector_to_dict",
    "selector_from_dict",
    "plan_intent_dict",
    "plan_body_dict",
    "plan_document_dict",
]


def target_to_dict(target: LifecycleTarget) -> Dict[str, object]:
    """Convert LifecycleTarget to JSON-serializable dict."""
    return {"kind": target.kind.value, "path": target.path}


def target_from_dict(value: object) -> LifecycleTarget:
    """Deserialize LifecycleTarget from dict."""
    from erii.data_lifecycle import LifecycleTarget, LifecycleTargetKind
    
    if not isinstance(value, dict) or set(value) != {"kind", "path"}:
        raise LifecyclePlanError("lifecycle target fields are invalid")
    return LifecycleTarget(
        kind=LifecycleTargetKind(value["kind"]),
        path=value["path"],
    )


def assessment_to_dict(assessment: LifecycleAssessment) -> Dict[str, object]:
    """Convert LifecycleAssessment to JSON-serializable dict."""
    return {
        "target": target_to_dict(assessment.target),
        "status": assessment.status.value,
        "format_id": assessment.format_id,
        "detected_version": assessment.detected_version,
        "current_version": assessment.current_version,
        "fingerprint": assessment.fingerprint,
        "file_count": assessment.file_count,
        "warnings": list(assessment.warnings),
    }


def assessment_from_dict(value: object) -> LifecycleAssessment:
    """Deserialize LifecycleAssessment from dict."""
    from erii.data_lifecycle import LifecycleAssessment, LifecycleStatus
    
    fields = {
        "target",
        "status",
        "format_id",
        "detected_version",
        "current_version",
        "fingerprint",
        "file_count",
        "warnings",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise LifecyclePlanError("lifecycle assessment fields are invalid")
    warnings = value["warnings"]
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise LifecyclePlanError("lifecycle assessment warnings are invalid")
    file_count = value["file_count"]
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count < 0:
        raise LifecyclePlanError("lifecycle assessment file_count is invalid")
    fingerprint = value["fingerprint"]
    if fingerprint is not None and not is_sha256(fingerprint):
        raise LifecyclePlanError("lifecycle assessment fingerprint is invalid")
    return LifecycleAssessment(
        target=target_from_dict(value["target"]),
        status=LifecycleStatus(value["status"]),
        format_id=value["format_id"],
        detected_version=value["detected_version"],
        current_version=value["current_version"],
        fingerprint=fingerprint,
        file_count=file_count,
        warnings=tuple(warnings),
    )


def content_to_dict(content: LifecycleContentIdentity) -> Dict[str, object]:
    """Convert LifecycleContentIdentity to JSON-serializable dict."""
    return {
        "kind": content.kind.value,
        "status": content.status.value,
        "format_id": content.format_id,
        "detected_version": content.detected_version,
        "current_version": content.current_version,
        "fingerprint": content.fingerprint,
        "file_count": content.file_count,
    }


def content_from_dict(value: object) -> LifecycleContentIdentity:
    """Deserialize LifecycleContentIdentity from dict."""
    from erii.data_lifecycle import (
        LifecycleContentIdentity,
        LifecycleStatus,
        LifecycleTargetKind,
    )
    
    fields = {
        "kind",
        "status",
        "format_id",
        "detected_version",
        "current_version",
        "fingerprint",
        "file_count",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise LifecyclePlanError("lifecycle content identity fields are invalid")
    return LifecycleContentIdentity(
        kind=LifecycleTargetKind(value["kind"]),
        status=LifecycleStatus(value["status"]),
        format_id=value["format_id"],
        detected_version=value["detected_version"],
        current_version=value["current_version"],
        fingerprint=value["fingerprint"],
        file_count=value["file_count"],
    )


def content_from_backup_manifest(value: object) -> LifecycleContentIdentity:
    """Normalize frozen Backup-v1 producer catalogs before payload checks.

    Backup v1 persists the producer's current_version and status. Frozen
    released/source catalogs can therefore describe an older format as current,
    while this reader correctly classifies it as a migration source. Known
    producer views are validated against their exact old readable sets, then
    reclassified against the current catalog.
    """
    from erii.compatibility import (
        FILE_STORAGE_FORMAT,
        MEMORY_PACK_FORMAT,
        SQLITE_FORMAT,
    )
    from erii.data_lifecycle import (
        LifecycleContentIdentity,
        LifecycleStatus,
        LifecycleTargetKind,
    )
    
    # Historical producer format catalogs for Backup-v1 compatibility
    _BACKUP_V1_HISTORICAL_PRODUCER_FORMATS: dict = {
        (LifecycleTargetKind.FILE_STORAGE, "1"): FormatCompatibility(
            format_id="erii.file-storage",
            current_version="1",
            readable_versions=("legacy", "1"),
        ),
        (LifecycleTargetKind.SQLITE, "9"): FormatCompatibility(
            format_id="erii.sqlite",
            current_version="9",
            readable_versions=tuple(str(version) for version in range(10)),
        ),
        (LifecycleTargetKind.MEMORY_PACK, "0.4.0a8"): FormatCompatibility(
            format_id="erii.memory-pack",
            current_version="0.4.0a8",
            readable_versions=(
                "0.1.0",
                "0.2.0",
                "0.4.0",
                "0.4.0a2",
                "0.4.0a3",
                "0.4.0a4",
                "0.4.0a5",
                "0.4.0a6",
                "0.4.0a7",
                "0.4.0a8",
            ),
        ),
    }
    
    if not isinstance(value, dict):
        return content_from_dict(value)
    try:
        kind = LifecycleTargetKind(value.get("kind"))
    except (TypeError, ValueError):
        return content_from_dict(value)
    producer_current = value.get("current_version")
    historical = (
        _BACKUP_V1_HISTORICAL_PRODUCER_FORMATS.get((kind, producer_current))
        if isinstance(producer_current, str)
        else None
    )
    if (
        historical is None
        or value.get("format_id") != historical.format_id
        or value.get("current_version") != historical.current_version
    ):
        return content_from_dict(value)
    
    detected_version = value.get("detected_version")
    if detected_version not in historical.readable_versions:
        return content_from_dict(value)
    
    # Get current catalog and reclassify status
    from erii.data_lifecycle import _compatibility_for_kind
    current_catalog = _compatibility_for_kind(kind)
    
    # Determine normalized status
    if detected_version is None:
        normalized_status = LifecycleStatus.EMPTY
    else:
        detected = require_supported_version(current_catalog, detected_version)
        normalized_status = (
            LifecycleStatus.CURRENT
            if detected == current_catalog.current_version
            else LifecycleStatus.MIGRATION_REQUIRED
        )
    
    return LifecycleContentIdentity(
        kind=kind,
        status=normalized_status,
        format_id=current_catalog.format_id,
        detected_version=detected_version,
        current_version=current_catalog.current_version,
        fingerprint=value.get("fingerprint"),
        file_count=value.get("file_count", 0),
    )


def directory_identity_to_dict(
    identity: LifecycleDirectoryIdentity,
) -> Dict[str, object]:
    """Convert LifecycleDirectoryIdentity to JSON-serializable dict."""
    return {
        "resolved_path": identity.resolved_path,
        "device": str(identity.device),
        "inode": str(identity.inode),
    }


def directory_identity_from_dict(value: object) -> LifecycleDirectoryIdentity:
    """Deserialize LifecycleDirectoryIdentity from dict."""
    from erii.data_lifecycle import LifecycleDirectoryIdentity
    
    if not isinstance(value, dict) or set(value) != {"resolved_path", "device", "inode"}:
        raise LifecyclePlanError("lifecycle directory identity fields are invalid")
    device = value["device"]
    inode = value["inode"]
    if (
        not isinstance(device, str)
        or not device.isdecimal()
        or not isinstance(inode, str)
        or not inode.isdecimal()
    ):
        raise LifecyclePlanError("lifecycle directory identity numbers are invalid")
    return LifecycleDirectoryIdentity(
        resolved_path=value["resolved_path"],
        device=int(device),
        inode=int(inode),
    )


def selector_to_dict(
    selector: LifecyclePlanSelector | None,
) -> Dict[str, object] | None:
    """Convert lifecycle plan selector to JSON-serializable dict."""
    from erii.lifecycle_erasure_contracts import ErasureSelector
    from erii.data_lifecycle import MemoryPackImportOptions
    
    if selector is None:
        return None
    if isinstance(selector, ErasureSelector):
        return selector.to_dict()
    if isinstance(selector, MemoryPackImportOptions):
        return {
            "target_agent_id": selector.target_agent_id,
            "target_user_id": selector.target_user_id,
        }
    raise LifecyclePlanError("lifecycle plan selector is invalid")


def selector_from_dict(
    operation: LifecycleOperation,
    value: object,
) -> LifecyclePlanSelector | None:
    """Deserialize lifecycle plan selector from dict."""
    from erii.lifecycle_erasure_contracts import ErasureSelector
    from erii.data_lifecycle import LifecycleOperation, MemoryPackImportOptions
    
    if value is None:
        return None
    if operation in {LifecycleOperation.ERASE, LifecycleOperation.REBUILD}:
        if not isinstance(value, dict):
            raise LifecyclePlanError("lifecycle erasure selector is invalid")
        try:
            return ErasureSelector.from_dict(value)
        except (TypeError, ValueError) as exc:
            raise LifecyclePlanError("lifecycle erasure selector is invalid") from exc
    if operation is LifecycleOperation.IMPORT:
        fields = {"target_agent_id", "target_user_id"}
        if not isinstance(value, dict) or set(value) != fields:
            raise LifecyclePlanError("lifecycle MemoryPack import options are invalid")
        return MemoryPackImportOptions(
            target_agent_id=value["target_agent_id"],
            target_user_id=value["target_user_id"],
        )
    raise LifecyclePlanError("this lifecycle operation cannot carry a selector")


def plan_intent_dict(plan: LifecyclePlan) -> Dict[str, object]:
    """Extract plan intent fields for digest calculation."""
    intent: Dict[str, object] = {
        "contract_version": plan.contract_version,
        "operation": plan.operation.value,
        "source": assessment_to_dict(plan.source),
        "destination": assessment_to_dict(plan.destination),
        "destination_parent": directory_identity_to_dict(plan.destination_parent),
        "content": content_to_dict(plan.content),
    }
    if plan.contract_version in {"2", "3"}:
        intent.update(
            {
                "strategy_id": plan.strategy_id,
                "backup_destination": (
                    None
                    if plan.backup_destination is None
                    else assessment_to_dict(plan.backup_destination)
                ),
                "backup_destination_parent": (
                    None
                    if plan.backup_destination_parent is None
                    else directory_identity_to_dict(plan.backup_destination_parent)
                ),
            }
        )
    if plan.contract_version == "3":
        intent["selector"] = selector_to_dict(plan.selector)
    return intent


def plan_body_dict(plan: LifecyclePlan) -> Dict[str, object]:
    """Convert plan body (intent + operation_id) to dict."""
    return {**plan_intent_dict(plan), "operation_id": plan.operation_id}


def plan_document_dict(plan: LifecyclePlan) -> Dict[str, object]:
    """Convert complete plan document to dict."""
    return {**plan_body_dict(plan), "plan_digest": plan.plan_digest}



