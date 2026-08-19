"""
Lifecycle Plan Codec: strict serialization and validation for durable plans.

This module owns zero-tolerance JSON encoding/decoding, version-specific
document construction, and plan-shape invariants while preserving byte-level
compatibility with historical plan formats.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from erii.compatibility import (
    FILE_STORAGE_FORMAT,
    LIFECYCLE_BACKUP_FORMAT,
    LIFECYCLE_PLAN_FORMAT,
    MEMORY_PACK_FORMAT,
    SQLITE_FORMAT,
    FormatCompatibility,
    require_supported_version,
)
from erii.errors import LifecyclePlanError
from erii.lifecycle_erasure_contracts import ErasureSelector, ErasureStorageKind

if TYPE_CHECKING:
    from erii._lifecycle.contracts import (
        LifecycleAssessment,
        LifecycleOperation,
        LifecyclePlan,
        LifecycleTargetKind,
    )


BACKUP_STRATEGY_ID = "backup-byte-preserving-v1"
RESTORE_STRATEGY_ID = "restore-byte-preserving-v1"
FILE_STORAGE_UPGRADE_STRATEGIES = {
    "legacy": "file-storage-legacy-to-v2",
    "1": "file-storage-v1-to-v2",
}
SQLITE_UPGRADE_STRATEGIES = {
    "6": "sqlite-schema-6-to-11",
    "9": "sqlite-schema-9-to-11",
    "10": "sqlite-schema-10-to-11",
}
MEMORY_PACK_STRATEGY_PREFIX = "memory-pack-"
ERASE_STRATEGY_PREFIX = "erase-staged-"
REBUILD_STRATEGY_PREFIX = "rebuild-staged-"
IMPORT_STRATEGY_PREFIX = "memory-pack-import-to-"
READABLE_LIFECYCLE_PLAN_CONTRACT_VERSIONS = frozenset(
    LIFECYCLE_PLAN_FORMAT.readable_versions
)

__all__ = [
    "BACKUP_STRATEGY_ID",
    "FILE_STORAGE_UPGRADE_STRATEGIES",
    "MEMORY_PACK_STRATEGY_PREFIX",
    "READABLE_LIFECYCLE_PLAN_CONTRACT_VERSIONS",
    "RESTORE_STRATEGY_ID",
    "SQLITE_UPGRADE_STRATEGIES",
    "canonical_json",
    "compatibility_for_kind",
    "decode_plan",
    "decode_strict_json",
    "encode_plan",
    "erasure_storage_kind",
    "erasure_strategy_id",
    "import_strategy_id",
    "is_sha256",
    "sha256_json",
    "upgrade_strategy_id",
    "validate_assessment",
    "validate_plan",
]


def is_sha256(value: object) -> bool:
    """Check if value looks like a SHA-256 hex digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def canonical_json(value: object) -> bytes:
    """Encode value as canonical JSON bytes (sorted keys, no whitespace)."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    """Return SHA-256 hex digest of canonical JSON encoding."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def decode_strict_json(json_text: str, *, label: str) -> Any:
    """Decode JSON with strict error messages and duplicate key detection."""
    if not isinstance(json_text, str):
        raise ValueError(f"{label} must be JSON text")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate field {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains non-standard number {value!r}")

    return json.loads(
        json_text,
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_constant,
    )


def compatibility_for_kind(kind: "LifecycleTargetKind") -> FormatCompatibility:
    """Return the frozen format catalog for one lifecycle target family."""
    from erii._lifecycle.contracts import LifecycleTargetKind

    if kind is LifecycleTargetKind.FILE_STORAGE:
        return FILE_STORAGE_FORMAT
    if kind is LifecycleTargetKind.SQLITE:
        return SQLITE_FORMAT
    if kind is LifecycleTargetKind.MEMORY_PACK:
        return MEMORY_PACK_FORMAT
    if kind is LifecycleTargetKind.BACKUP:
        return LIFECYCLE_BACKUP_FORMAT
    raise LifecyclePlanError(f"unsupported lifecycle target kind {kind!r}")


def upgrade_strategy_id(source: "LifecycleAssessment") -> str:
    """Return the only supported upgrade strategy for a validated source."""
    from erii._lifecycle.contracts import LifecycleStatus, LifecycleTargetKind

    if (
        source.target.kind is LifecycleTargetKind.FILE_STORAGE
        and source.status is LifecycleStatus.MIGRATION_REQUIRED
        and source.current_version == FILE_STORAGE_FORMAT.current_version
    ):
        strategy_id = FILE_STORAGE_UPGRADE_STRATEGIES.get(source.detected_version)
        if strategy_id is not None:
            return strategy_id
    if (
        source.target.kind is LifecycleTargetKind.SQLITE
        and source.status is LifecycleStatus.MIGRATION_REQUIRED
        and source.current_version == SQLITE_FORMAT.current_version
    ):
        strategy_id = SQLITE_UPGRADE_STRATEGIES.get(source.detected_version)
        if strategy_id is not None:
            return strategy_id
    if (
        source.target.kind is LifecycleTargetKind.MEMORY_PACK
        and source.status is LifecycleStatus.MIGRATION_REQUIRED
        and source.detected_version is not None
        and source.current_version == MEMORY_PACK_FORMAT.current_version
    ):
        return (
            f"{MEMORY_PACK_STRATEGY_PREFIX}{source.detected_version}"
            f"-to-{source.current_version}"
        )
    raise LifecyclePlanError(
        "no verified lifecycle upgrade strategy exists for this source version"
    )


def erasure_storage_kind(kind: "LifecycleTargetKind") -> ErasureStorageKind:
    """Map a lifecycle storage family to the erasure implementation family."""
    from erii._lifecycle.contracts import LifecycleTargetKind

    if kind is LifecycleTargetKind.FILE_STORAGE:
        return ErasureStorageKind.FILE_STORAGE
    if kind is LifecycleTargetKind.SQLITE:
        return ErasureStorageKind.SQLITE
    raise LifecyclePlanError("lifecycle erasure requires FileStorage or SQLite")


def erasure_strategy_id(
    operation: "LifecycleOperation",
    kind: "LifecycleTargetKind",
) -> str:
    """Return the deterministic erase or rebuild strategy identity."""
    from erii._lifecycle.contracts import LifecycleOperation

    storage_kind = erasure_storage_kind(kind)
    prefix = (
        ERASE_STRATEGY_PREFIX
        if operation is LifecycleOperation.ERASE
        else REBUILD_STRATEGY_PREFIX
    )
    return f"{prefix}{storage_kind.value}-v1"


def import_strategy_id(kind: "LifecycleTargetKind") -> str:
    """Return the deterministic MemoryPack import strategy identity."""
    from erii._lifecycle.contracts import LifecycleTargetKind

    if kind not in {LifecycleTargetKind.FILE_STORAGE, LifecycleTargetKind.SQLITE}:
        raise LifecyclePlanError("MemoryPack import destination is unsupported")
    return f"{IMPORT_STRATEGY_PREFIX}{kind.value}-v1"


def validate_assessment(assessment: "LifecycleAssessment") -> None:
    """Validate one no-content lifecycle assessment against its format catalog."""
    from erii._lifecycle.contracts import (
        LifecycleStatus,
        LifecycleTarget,
    )

    if not isinstance(assessment.target, LifecycleTarget) or not isinstance(
        assessment.status, LifecycleStatus
    ):
        raise LifecyclePlanError("lifecycle assessment target or status is invalid")
    compatibility = compatibility_for_kind(assessment.target.kind)
    if assessment.format_id != compatibility.format_id:
        raise LifecyclePlanError("lifecycle assessment format identity is invalid")
    if assessment.current_version != compatibility.current_version:
        raise LifecyclePlanError("lifecycle assessment current version is invalid")
    if (
        isinstance(assessment.file_count, bool)
        or not isinstance(assessment.file_count, int)
        or assessment.file_count < 0
    ):
        raise LifecyclePlanError("lifecycle assessment file_count is invalid")
    if not isinstance(assessment.warnings, tuple) or any(
        not isinstance(item, str) for item in assessment.warnings
    ):
        raise LifecyclePlanError("lifecycle assessment warnings are invalid")
    if assessment.status is LifecycleStatus.MISSING:
        if (
            assessment.detected_version is not None
            or assessment.fingerprint is not None
            or assessment.file_count != 0
        ):
            raise LifecyclePlanError("missing lifecycle assessment fields are invalid")
        return
    if assessment.fingerprint is None or not is_sha256(assessment.fingerprint):
        raise LifecyclePlanError("lifecycle assessment fingerprint is invalid")
    if assessment.status is LifecycleStatus.EMPTY:
        if assessment.detected_version is not None:
            raise LifecyclePlanError("empty lifecycle assessment cannot have a version")
        return
    detected = require_supported_version(compatibility, assessment.detected_version)
    expected_status = (
        LifecycleStatus.CURRENT
        if detected == compatibility.current_version
        else LifecycleStatus.MIGRATION_REQUIRED
    )
    if assessment.status is not expected_status:
        raise LifecyclePlanError("lifecycle assessment status does not match its version")


def validate_plan(plan: "LifecyclePlan") -> None:
    """Validate versioned operation shape and strategy invariants for one plan."""
    from erii._lifecycle.contracts import (
        LifecycleContentIdentity,
        LifecycleOperation,
        LifecycleStatus,
        LifecycleTargetKind,
        MemoryPackImportOptions,
    )

    validate_assessment(plan.source)
    validate_assessment(plan.destination)
    if plan.backup_destination is not None:
        validate_assessment(plan.backup_destination)
    if plan.contract_version == "1" and (
        plan.backup_destination is not None
        or plan.backup_destination_parent is not None
    ):
        raise LifecyclePlanError("lifecycle plan contract v1 cannot bind a backup target")
    if plan.contract_version != "3" and plan.selector is not None:
        raise LifecyclePlanError("legacy lifecycle plans cannot carry a selector")
    if plan.operation is LifecycleOperation.BACKUP:
        if plan.source.target.kind is LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("backup plan source must be live storage")
        if plan.destination.target.kind is not LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("backup plan destination must be a backup bundle")
        if plan.destination.status is not LifecycleStatus.MISSING:
            raise LifecyclePlanError("backup plan destination must be missing")
        if plan.content != LifecycleContentIdentity.from_assessment(plan.source):
            raise LifecyclePlanError("backup plan content does not match its source")
        if plan.strategy_id != BACKUP_STRATEGY_ID:
            raise LifecyclePlanError("backup plan strategy identity is invalid")
        if plan.backup_destination is not None or plan.backup_destination_parent is not None:
            raise LifecyclePlanError("backup plan cannot bind a second backup target")
        if plan.selector is not None:
            raise LifecyclePlanError("backup plan cannot carry a selector")
    elif plan.operation is LifecycleOperation.RESTORE:
        if plan.source.target.kind is not LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("restore plan source must be a backup bundle")
        if plan.destination.target.kind is LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("restore plan destination must be live storage")
        if plan.destination.status is not LifecycleStatus.MISSING:
            raise LifecyclePlanError("restore plan destination must be missing")
        if plan.content.kind is not plan.destination.target.kind:
            raise LifecyclePlanError("restore destination kind does not match backup content")
        if plan.strategy_id != RESTORE_STRATEGY_ID:
            raise LifecyclePlanError("restore plan strategy identity is invalid")
        if plan.backup_destination is not None or plan.backup_destination_parent is not None:
            raise LifecyclePlanError("restore plan cannot bind a second backup target")
        if plan.selector is not None:
            raise LifecyclePlanError("restore plan cannot carry a selector")
    elif plan.operation is LifecycleOperation.UPGRADE:
        if plan.contract_version not in {"2", "3"}:
            raise LifecyclePlanError("upgrade plans require lifecycle contract v2 or v3")
        if plan.source.target.kind is LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("upgrade plan source must be live storage")
        if plan.source.status is not LifecycleStatus.MIGRATION_REQUIRED:
            raise LifecyclePlanError("upgrade plan source must require migration")
        if plan.destination.target.kind is not plan.source.target.kind:
            raise LifecyclePlanError("upgrade destination kind does not match its source")
        if plan.destination.status is not LifecycleStatus.MISSING:
            raise LifecyclePlanError("upgrade plan destination must be missing")
        if (
            plan.content.kind is not plan.destination.target.kind
            or plan.content.status is not LifecycleStatus.CURRENT
            or plan.content.detected_version != plan.content.current_version
        ):
            raise LifecyclePlanError("upgrade plan result identity is invalid")
        if plan.backup_destination is None or plan.backup_destination_parent is None:
            raise LifecyclePlanError("upgrade plan requires a backup destination")
        if (
            plan.backup_destination.target.kind is not LifecycleTargetKind.BACKUP
            or plan.backup_destination.status is not LifecycleStatus.MISSING
        ):
            raise LifecyclePlanError("upgrade plan backup destination must be missing")
        if plan.strategy_id != upgrade_strategy_id(plan.source):
            raise LifecyclePlanError("upgrade plan strategy identity is invalid")
        if plan.selector is not None:
            raise LifecyclePlanError("upgrade plan cannot carry a selector")
    elif plan.operation in {LifecycleOperation.ERASE, LifecycleOperation.REBUILD}:
        if plan.contract_version != "3":
            raise LifecyclePlanError("erase and rebuild plans require lifecycle contract v3")
        if not isinstance(plan.selector, ErasureSelector):
            raise LifecyclePlanError("erase and rebuild plans require an exact selector")
        if plan.source.status is not LifecycleStatus.CURRENT:
            raise LifecyclePlanError("erase and rebuild source must be current")
        erasure_storage_kind(plan.source.target.kind)
        if plan.destination != plan.source:
            raise LifecyclePlanError("erase and rebuild destination must be the live source")
        if plan.content != LifecycleContentIdentity.from_assessment(plan.source):
            raise LifecyclePlanError("erase and rebuild content must bind the source")
        if plan.backup_destination is None or plan.backup_destination_parent is None:
            raise LifecyclePlanError("erase and rebuild require a backup destination")
        if (
            plan.backup_destination.target.kind is not LifecycleTargetKind.BACKUP
            or plan.backup_destination.status is not LifecycleStatus.MISSING
        ):
            raise LifecyclePlanError("erase and rebuild backup destination must be missing")
        if plan.strategy_id != erasure_strategy_id(
            plan.operation,
            plan.source.target.kind,
        ):
            raise LifecyclePlanError("erase or rebuild plan strategy identity is invalid")
    elif plan.operation is LifecycleOperation.IMPORT:
        if plan.contract_version != "3":
            raise LifecyclePlanError("MemoryPack import plans require lifecycle contract v3")
        if not isinstance(plan.selector, MemoryPackImportOptions):
            raise LifecyclePlanError("MemoryPack import plan options are missing")
        if plan.source.target.kind is not LifecycleTargetKind.MEMORY_PACK:
            raise LifecyclePlanError("MemoryPack import plan source is invalid")
        if plan.source.status not in {
            LifecycleStatus.CURRENT,
            LifecycleStatus.MIGRATION_REQUIRED,
        }:
            raise LifecyclePlanError("MemoryPack import source must be readable")
        if plan.destination.target.kind not in {
            LifecycleTargetKind.FILE_STORAGE,
            LifecycleTargetKind.SQLITE,
        }:
            raise LifecyclePlanError("MemoryPack import destination is invalid")
        if plan.destination.status is not LifecycleStatus.MISSING:
            raise LifecyclePlanError("MemoryPack import destination must be missing")
        if plan.content != LifecycleContentIdentity.from_assessment(plan.source):
            raise LifecyclePlanError("MemoryPack import content must bind its source")
        if plan.backup_destination is not None or plan.backup_destination_parent is not None:
            raise LifecyclePlanError("fresh MemoryPack import cannot bind a backup target")
        if plan.strategy_id != import_strategy_id(plan.destination.target.kind):
            raise LifecyclePlanError("MemoryPack import strategy identity is invalid")
    else:  # pragma: no cover - Enum construction closes this branch.
        raise LifecyclePlanError("lifecycle plan operation is unsupported")


def _plan_from_document(value: object) -> "LifecyclePlan":
    from erii._lifecycle.contracts import LifecycleOperation, LifecyclePlan
    from erii._lifecycle.serializers import (
        assessment_from_dict,
        content_from_dict,
        directory_identity_from_dict,
        selector_from_dict,
    )

    v1_fields = {
        "contract_version",
        "operation",
        "operation_id",
        "source",
        "destination",
        "destination_parent",
        "content",
        "plan_digest",
    }
    v2_fields = v1_fields | {
        "strategy_id",
        "backup_destination",
        "backup_destination_parent",
    }
    v3_fields = v2_fields | {"selector"}
    if not isinstance(value, dict):
        raise LifecyclePlanError("lifecycle plan fields are invalid")
    contract_version = value.get("contract_version")
    if contract_version == "1":
        if set(value) != v1_fields:
            raise LifecyclePlanError("lifecycle plan fields are invalid")
    elif contract_version == "2":
        if set(value) != v2_fields:
            raise LifecyclePlanError("lifecycle plan fields are invalid")
    elif contract_version == "3":
        if set(value) != v3_fields:
            raise LifecyclePlanError("lifecycle plan fields are invalid")
    else:
        raise LifecyclePlanError("unsupported lifecycle plan contract version")
    operation = LifecycleOperation(value["operation"])
    if contract_version == "1":
        if operation not in {LifecycleOperation.BACKUP, LifecycleOperation.RESTORE}:
            raise LifecyclePlanError(
                "lifecycle plan contract v1 supports only backup and restore"
            )
        strategy_id = (
            BACKUP_STRATEGY_ID
            if operation is LifecycleOperation.BACKUP
            else RESTORE_STRATEGY_ID
        )
        backup_destination = None
        backup_destination_parent = None
        selector = None
    else:
        strategy_id = value["strategy_id"]
        raw_backup_destination = value["backup_destination"]
        backup_destination = (
            None
            if raw_backup_destination is None
            else assessment_from_dict(raw_backup_destination)
        )
        raw_backup_parent = value["backup_destination_parent"]
        backup_destination_parent = (
            None
            if raw_backup_parent is None
            else directory_identity_from_dict(raw_backup_parent)
        )
        selector = (
            None
            if contract_version == "2"
            else selector_from_dict(operation, value["selector"])
        )
    return LifecyclePlan(
        contract_version=contract_version,
        operation=operation,
        operation_id=value["operation_id"],
        source=assessment_from_dict(value["source"]),
        destination=assessment_from_dict(value["destination"]),
        destination_parent=directory_identity_from_dict(value["destination_parent"]),
        content=content_from_dict(value["content"]),
        strategy_id=strategy_id,
        backup_destination=backup_destination,
        backup_destination_parent=backup_destination_parent,
        selector=selector,
        plan_digest=value["plan_digest"],
    )


def encode_plan(plan: "LifecyclePlan") -> str:
    """Return the canonical no-content document for one validated plan."""
    from erii._lifecycle.serializers import plan_document_dict

    return canonical_json(plan_document_dict(plan)).decode("utf-8")


def decode_plan(json_text: str) -> "LifecyclePlan":
    """Load a strict plan document and preserve stable public error semantics."""
    try:
        document = decode_strict_json(json_text, label="lifecycle plan")
        return _plan_from_document(document)
    except LifecyclePlanError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise LifecyclePlanError("lifecycle plan document is invalid") from exc
