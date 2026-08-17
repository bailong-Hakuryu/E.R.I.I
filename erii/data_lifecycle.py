"""Inspection, durable planning, backup, upgrade, and restore for v0.4."""

from contextlib import ExitStack, closing, contextmanager
import ctypes
from dataclasses import dataclass
from enum import Enum
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import sys
from typing import Any, Dict, TypeAlias

from erii.compatibility import (
    FILE_STORAGE_FORMAT,
    LIFECYCLE_BACKUP_FORMAT,
    LIFECYCLE_PLAN_FORMAT,
    MEMORY_PACK_FORMAT,
    SQLITE_FORMAT,
    FormatCompatibility,
    decode_memory_pack_json,
    require_supported_version,
)
from erii.errors import (
    LifecycleConflictError,
    LifecyclePlanError,
    LifecycleVerificationError,
    StaleLifecyclePlanError,
    StorageIntegrityError,
    StorageWriteError,
    UnsupportedFormatError,
)
from erii.models.pack import MemoryPack
from erii.lifecycle_erasure_contracts import (
    ErasureInventory,
    ErasureScope,
    ErasureSelectionError,
    ErasureSelector,
    ErasureStorageKind,
    ErasureTransformResult,
    RelationshipRebuildProof,
)
from erii.lifecycle_memory_pack_import_contracts import (
    MemoryPackStagingAdapter,
    MemoryPackStagingImportReport,
)
from erii._lifecycle.plan_codec import (
    canonical_json as _canonical_json,
    decode_strict_json as _decode_strict_json,
    is_sha256 as _is_sha256,
    sha256_json as _sha256_json,
)
from erii._lifecycle.serializers import (
    assessment_from_dict as _assessment_from_dict,
    assessment_to_dict as _assessment_to_dict,
    content_from_backup_manifest as _content_from_backup_manifest,
    content_from_dict as _content_from_dict,
    content_to_dict as _content_to_dict,
    directory_identity_from_dict as _directory_identity_from_dict,
    directory_identity_to_dict as _directory_identity_to_dict,
    plan_body_dict as _plan_body_dict,
    plan_document_dict as _plan_document_dict,
    plan_intent_dict as _plan_intent_dict,
    selector_from_dict as _selector_from_dict,
    selector_to_dict as _selector_to_dict,
    target_from_dict as _target_from_dict,
    target_to_dict as _target_to_dict,
)
from erii.lifecycle_streaming import (
    RegularFileIdentity,
    copy_regular_file_exclusive,
    stream_regular_file_identity,
    stream_regular_tree_manifest,
)


FILE_STORAGE_MANIFEST = ".erii-store.json"
LIFECYCLE_BACKUP_MANIFEST = "manifest.json"
LIFECYCLE_PLAN_CONTRACT_VERSION = LIFECYCLE_PLAN_FORMAT.current_version
_READABLE_LIFECYCLE_PLAN_CONTRACT_VERSIONS = frozenset(
    LIFECYCLE_PLAN_FORMAT.readable_versions
)
_BACKUP_STRATEGY_ID = "backup-byte-preserving-v1"
_RESTORE_STRATEGY_ID = "restore-byte-preserving-v1"
_FILE_STORAGE_LEGACY_TO_V2_STRATEGY_ID = "file-storage-legacy-to-v2"
_FILE_STORAGE_V1_TO_V2_STRATEGY_ID = "file-storage-v1-to-v2"
_SQLITE_SCHEMA_6_TO_10_STRATEGY_ID = "sqlite-schema-6-to-10"
_SQLITE_SCHEMA_9_TO_10_STRATEGY_ID = "sqlite-schema-9-to-10"
_FILE_STORAGE_UPGRADE_STRATEGIES = {
    "legacy": _FILE_STORAGE_LEGACY_TO_V2_STRATEGY_ID,
    "1": _FILE_STORAGE_V1_TO_V2_STRATEGY_ID,
}
_SQLITE_UPGRADE_STRATEGIES = {
    "6": _SQLITE_SCHEMA_6_TO_10_STRATEGY_ID,
    "9": _SQLITE_SCHEMA_9_TO_10_STRATEGY_ID,
}
_MEMORY_PACK_STRATEGY_PREFIX = "memory-pack-"
_ERASE_STRATEGY_PREFIX = "erase-staged-"
_REBUILD_STRATEGY_PREFIX = "rebuild-staged-"
_IMPORT_STRATEGY_PREFIX = "memory-pack-import-to-"
MAX_LIFECYCLE_MEMORY_PACK_BYTES = 256 * 1024 * 1024
MAX_LIFECYCLE_TRANSFORM_BYTES = 512 * 1024 * 1024
MAX_LIFECYCLE_BACKUP_MANIFEST_BYTES = 16 * 1024 * 1024
_FILE_STORAGE_MANIFEST_FIELDS = frozenset({"format", "version"})
_LEGACY_BASENAMES = frozenset(
    {
        "nodes.json",
        "core_memory.json",
        "timeline.json",
        "relationship.json",
        "_relationship_identities.json",
        "_archival_state.json",
    }
)
_LEGACY_TOP_LEVEL_DIRECTORIES = frozenset(
    {
        "_relationship_events",
        "_relationship_adjudications",
        "_relationship_consequences",
        "_narrative_tension_links",
        "_persona_growth",
        "_turn_records",
        "_reply_attempts",
        "_relationship_processing",
        "_persona_compilations",
        "_persona_approval_transactions",
    }
)
_FILE_STORAGE_RUNTIME_LOCK_DIRECTORIES = frozenset(
    {
        "_relationship_history_locks",
        "_relationship_processing_locks",
        "_turn_locks",
    }
)


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
        object.__setattr__(self, "path", os.path.abspath(self.path))


@dataclass(frozen=True, slots=True)
class LifecycleAssessment:
    """No-content inspection result suitable for later durable planning."""

    target: LifecycleTarget
    status: LifecycleStatus
    format_id: str
    detected_version: str | None
    current_version: str
    fingerprint: str | None
    file_count: int
    warnings: tuple[str, ...] = ()


class LifecycleOperation(str, Enum):
    """Mutating operations implemented by the current lifecycle contract."""

    BACKUP = "backup"
    RESTORE = "restore"
    UPGRADE = "upgrade"
    ERASE = "erase"
    REBUILD = "rebuild"
    IMPORT = "import"


class LifecycleOutcome(str, Enum):
    """Verified terminal outcome for one lifecycle execution."""

    APPLIED = "applied"
    ALREADY_COMPLETE = "already_complete"


@dataclass(frozen=True, slots=True)
class LifecycleContentIdentity:
    """Path-free identity of durable data captured by a plan or backup."""

    kind: LifecycleTargetKind
    status: LifecycleStatus
    format_id: str
    detected_version: str | None
    current_version: str
    fingerprint: str
    file_count: int

    @classmethod
    def from_assessment(
        cls,
        assessment: LifecycleAssessment,
    ) -> "LifecycleContentIdentity":
        if assessment.fingerprint is None:
            raise LifecyclePlanError("missing lifecycle data cannot be captured")
        return cls(
            kind=assessment.target.kind,
            status=assessment.status,
            format_id=assessment.format_id,
            detected_version=assessment.detected_version,
            current_version=assessment.current_version,
            fingerprint=assessment.fingerprint,
            file_count=assessment.file_count,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LifecycleTargetKind) or not isinstance(
            self.status, LifecycleStatus
        ):
            raise LifecyclePlanError("lifecycle content kind or status is invalid")
        if self.kind is LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("backup content must name its original storage kind")
        if self.status is LifecycleStatus.MISSING:
            raise LifecyclePlanError("missing lifecycle data has no content identity")
        if not _is_sha256(self.fingerprint):
            raise LifecyclePlanError("lifecycle content fingerprint must be SHA-256")
        if (
            isinstance(self.file_count, bool)
            or not isinstance(self.file_count, int)
            or self.file_count < 0
        ):
            raise LifecyclePlanError("lifecycle content file_count must be non-negative")
        compatibility = _compatibility_for_kind(self.kind)
        if self.format_id != compatibility.format_id:
            raise LifecyclePlanError("lifecycle content format identity is invalid")
        if self.current_version != compatibility.current_version:
            raise LifecyclePlanError("lifecycle content current version is invalid")
        if self.status is LifecycleStatus.EMPTY:
            if self.detected_version is not None:
                raise LifecyclePlanError("empty lifecycle content cannot have a version")
        else:
            detected = require_supported_version(compatibility, self.detected_version)
            expected_status = (
                LifecycleStatus.CURRENT
                if detected == compatibility.current_version
                else LifecycleStatus.MIGRATION_REQUIRED
            )
            if self.status is not expected_status:
                raise LifecyclePlanError("lifecycle content status does not match its version")


@dataclass(frozen=True, slots=True)
class BackupRequest:
    """Requests a verified, portable backup of one inspected source."""

    source: LifecycleAssessment
    destination: LifecycleTarget

    def __post_init__(self) -> None:
        if not isinstance(self.source, LifecycleAssessment):
            raise TypeError("BackupRequest source must be a LifecycleAssessment")
        if not isinstance(self.destination, LifecycleTarget):
            raise TypeError("BackupRequest destination must be a LifecycleTarget")
        if self.source.target.kind is LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("a backup bundle cannot be backed up as live storage")
        if self.destination.kind is not LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("BackupRequest destination must be a backup target")


@dataclass(frozen=True, slots=True)
class RestoreRequest:
    """Requests byte-preserving restoration to a missing live-data target."""

    backup: LifecycleAssessment
    destination: LifecycleTarget

    def __post_init__(self) -> None:
        if not isinstance(self.backup, LifecycleAssessment):
            raise TypeError("RestoreRequest backup must be a LifecycleAssessment")
        if not isinstance(self.destination, LifecycleTarget):
            raise TypeError("RestoreRequest destination must be a LifecycleTarget")
        if self.backup.target.kind is not LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("RestoreRequest source must be a backup assessment")
        if self.destination.kind is LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("RestoreRequest destination must be live storage")


@dataclass(frozen=True, slots=True)
class UpgradeRequest:
    """Requests a source-preserving upgrade with a verified pre-upgrade backup."""

    source: LifecycleAssessment
    destination: LifecycleTarget
    backup_destination: LifecycleTarget

    def __post_init__(self) -> None:
        if not isinstance(self.source, LifecycleAssessment):
            raise TypeError("UpgradeRequest source must be a LifecycleAssessment")
        if not isinstance(self.destination, LifecycleTarget):
            raise TypeError("UpgradeRequest destination must be a LifecycleTarget")
        if not isinstance(self.backup_destination, LifecycleTarget):
            raise TypeError("UpgradeRequest backup_destination must be a LifecycleTarget")
        if self.source.target.kind is LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("UpgradeRequest source must be live storage")
        if self.destination.kind is not self.source.target.kind:
            raise LifecyclePlanError("UpgradeRequest destination kind must match its source")
        if self.backup_destination.kind is not LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError(
                "UpgradeRequest backup_destination must be a backup target"
            )


@dataclass(frozen=True, slots=True)
class EraseRequest:
    """Requests a backup-first, exact-scope erasure of current live storage."""

    source: LifecycleAssessment
    selector: ErasureSelector
    backup_destination: LifecycleTarget

    def __post_init__(self) -> None:
        if not isinstance(self.source, LifecycleAssessment):
            raise TypeError("EraseRequest source must be a LifecycleAssessment")
        if not isinstance(self.selector, ErasureSelector):
            raise TypeError("EraseRequest selector must be an ErasureSelector")
        if not isinstance(self.backup_destination, LifecycleTarget):
            raise TypeError("EraseRequest backup_destination must be a LifecycleTarget")
        if self.source.target.kind not in {
            LifecycleTargetKind.FILE_STORAGE,
            LifecycleTargetKind.SQLITE,
        }:
            raise LifecyclePlanError("EraseRequest source must be FileStorage or SQLite")
        if self.backup_destination.kind is not LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError(
                "EraseRequest backup_destination must be a backup target"
            )


@dataclass(frozen=True, slots=True)
class RebuildRequest:
    """Requests backup-first deterministic projection rebuild for one relationship."""

    source: LifecycleAssessment
    selector: ErasureSelector
    backup_destination: LifecycleTarget

    def __post_init__(self) -> None:
        if not isinstance(self.source, LifecycleAssessment):
            raise TypeError("RebuildRequest source must be a LifecycleAssessment")
        if not isinstance(self.selector, ErasureSelector):
            raise TypeError("RebuildRequest selector must be an ErasureSelector")
        if not isinstance(self.backup_destination, LifecycleTarget):
            raise TypeError("RebuildRequest backup_destination must be a LifecycleTarget")
        if self.source.target.kind not in {
            LifecycleTargetKind.FILE_STORAGE,
            LifecycleTargetKind.SQLITE,
        }:
            raise LifecyclePlanError("RebuildRequest source must be FileStorage or SQLite")
        if self.backup_destination.kind is not LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError(
                "RebuildRequest backup_destination must be a backup target"
            )


@dataclass(frozen=True, slots=True)
class MemoryPackImportOptions:
    """No-content identity remapping parameters frozen into an import plan."""

    target_agent_id: str | None = None
    target_user_id: str | None = None

    def __post_init__(self) -> None:
        if (self.target_agent_id is None) != (self.target_user_id is None):
            raise LifecyclePlanError(
                "MemoryPack import target_agent_id and target_user_id are required together"
            )
        for label, value in (
            ("target_agent_id", self.target_agent_id),
            ("target_user_id", self.target_user_id),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise LifecyclePlanError(f"MemoryPack import {label} must be non-empty")


@dataclass(frozen=True, slots=True)
class MemoryPackImportRequest:
    """Requests atomic import of one inspected pack into missing fresh storage."""

    source: LifecycleAssessment
    destination: LifecycleTarget
    target_agent_id: str | None = None
    target_user_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, LifecycleAssessment):
            raise TypeError("MemoryPackImportRequest source must be a LifecycleAssessment")
        if not isinstance(self.destination, LifecycleTarget):
            raise TypeError("MemoryPackImportRequest destination must be a LifecycleTarget")
        if self.source.target.kind is not LifecycleTargetKind.MEMORY_PACK:
            raise LifecyclePlanError("MemoryPackImportRequest source must be a MemoryPack")
        if self.destination.kind not in {
            LifecycleTargetKind.FILE_STORAGE,
            LifecycleTargetKind.SQLITE,
        }:
            raise LifecyclePlanError(
                "MemoryPackImportRequest destination must be FileStorage or SQLite"
            )
        MemoryPackImportOptions(
            target_agent_id=self.target_agent_id,
            target_user_id=self.target_user_id,
        )


LifecyclePlanSelector: TypeAlias = ErasureSelector | MemoryPackImportOptions
LifecycleRequest: TypeAlias = (
    BackupRequest
    | RestoreRequest
    | UpgradeRequest
    | EraseRequest
    | RebuildRequest
    | MemoryPackImportRequest
)


@dataclass(frozen=True, slots=True)
class LifecycleDirectoryIdentity:
    """Stable identity of the existing directory that owns a destination name."""

    resolved_path: str
    device: int
    inode: int

    def __post_init__(self) -> None:
        if not isinstance(self.resolved_path, str) or not os.path.isabs(self.resolved_path):
            raise LifecyclePlanError("lifecycle directory identity path must be absolute")
        for label, value in (("device", self.device), ("inode", self.inode)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise LifecyclePlanError(
                    f"lifecycle directory identity {label} must be a non-negative integer"
                )


@dataclass(frozen=True, slots=True)
class LifecyclePlan:
    """Immutable, strictly serializable execution credential."""

    contract_version: str
    operation: LifecycleOperation
    operation_id: str
    source: LifecycleAssessment
    destination: LifecycleAssessment
    destination_parent: LifecycleDirectoryIdentity
    content: LifecycleContentIdentity
    strategy_id: str
    backup_destination: LifecycleAssessment | None
    backup_destination_parent: LifecycleDirectoryIdentity | None
    plan_digest: str
    selector: LifecyclePlanSelector | None = None

    def __post_init__(self) -> None:
        if self.contract_version not in _READABLE_LIFECYCLE_PLAN_CONTRACT_VERSIONS:
            raise LifecyclePlanError("unsupported lifecycle plan contract version")
        if not isinstance(self.operation, LifecycleOperation):
            raise LifecyclePlanError("lifecycle plan operation is invalid")
        if not isinstance(self.source, LifecycleAssessment) or not isinstance(
            self.destination, LifecycleAssessment
        ):
            raise LifecyclePlanError("lifecycle plan assessments are invalid")
        if not isinstance(self.destination_parent, LifecycleDirectoryIdentity):
            raise LifecyclePlanError("lifecycle plan destination parent identity is invalid")
        if not isinstance(self.content, LifecycleContentIdentity):
            raise LifecyclePlanError("lifecycle plan content identity is invalid")
        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            raise LifecyclePlanError("lifecycle plan strategy identity is invalid")
        if self.backup_destination is not None and not isinstance(
            self.backup_destination, LifecycleAssessment
        ):
            raise LifecyclePlanError("lifecycle plan backup destination is invalid")
        if self.backup_destination_parent is not None and not isinstance(
            self.backup_destination_parent, LifecycleDirectoryIdentity
        ):
            raise LifecyclePlanError("lifecycle plan backup parent identity is invalid")
        if self.selector is not None and not isinstance(
            self.selector,
            (ErasureSelector, MemoryPackImportOptions),
        ):
            raise LifecyclePlanError("lifecycle plan selector is invalid")
        _validate_plan_shape(self)
        expected_operation_id = _sha256_json(_plan_intent_dict(self))
        if self.operation_id != expected_operation_id:
            raise LifecyclePlanError("lifecycle plan operation identity is invalid")
        expected_digest = _sha256_json(_plan_body_dict(self))
        if self.plan_digest != expected_digest:
            raise LifecyclePlanError("lifecycle plan digest is invalid")

    def to_json(self) -> str:
        """Returns the canonical no-content plan document."""
        return _canonical_json(_plan_document_dict(self)).decode("utf-8")

    @classmethod
    def from_json(cls, json_text: str) -> "LifecyclePlan":
        """Loads a strict plan document and rejects unknown or duplicate fields."""
        try:
            document = _decode_strict_json(json_text, label="lifecycle plan")
            return _plan_from_document(document)
        except LifecyclePlanError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise LifecyclePlanError("lifecycle plan document is invalid") from exc


@dataclass(frozen=True, slots=True)
class LifecycleReport:
    """Verified terminal result without conversation or persona content."""

    operation_id: str
    plan_digest: str
    operation: LifecycleOperation
    outcome: LifecycleOutcome
    content_fingerprint: str
    artifact_fingerprint: str
    file_count: int
    details: ErasureTransformResult | MemoryPackStagingImportReport | None = None

    def __post_init__(self) -> None:
        if not _is_sha256(self.operation_id) or not _is_sha256(self.plan_digest):
            raise ValueError("lifecycle report operation identity is invalid")
        if not isinstance(self.operation, LifecycleOperation) or not isinstance(
            self.outcome, LifecycleOutcome
        ):
            raise ValueError("lifecycle report terminal state is invalid")
        if not _is_sha256(self.content_fingerprint) or not _is_sha256(self.artifact_fingerprint):
            raise ValueError("lifecycle report fingerprint is invalid")
        if (
            isinstance(self.file_count, bool)
            or not isinstance(self.file_count, int)
            or self.file_count < 0
        ):
            raise ValueError("lifecycle report file_count is invalid")
        if self.details is not None and not isinstance(
            self.details,
            (ErasureTransformResult, MemoryPackStagingImportReport),
        ):
            raise ValueError("lifecycle report details are invalid")

    def to_dict(self) -> Dict[str, object]:
        """Returns a JSON-compatible report containing no durable content bodies."""
        return {
            "operation_id": self.operation_id,
            "plan_digest": self.plan_digest,
            "operation": self.operation.value,
            "outcome": self.outcome.value,
            "content_fingerprint": self.content_fingerprint,
            "artifact_fingerprint": self.artifact_fingerprint,
            "file_count": self.file_count,
            "details": None if self.details is None else self.details.to_dict(),
        }


# Moved to erii._lifecycle.plan_codec
# Moved to erii._lifecycle.plan_codec
# Moved to erii._lifecycle.plan_codec
# Moved to erii._lifecycle.plan_codec
def _plan_from_document(value: object) -> LifecyclePlan:
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
            _BACKUP_STRATEGY_ID
            if operation is LifecycleOperation.BACKUP
            else _RESTORE_STRATEGY_ID
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
            else _assessment_from_dict(raw_backup_destination)
        )
        raw_backup_parent = value["backup_destination_parent"]
        backup_destination_parent = (
            None
            if raw_backup_parent is None
            else _directory_identity_from_dict(raw_backup_parent)
        )
        selector = (
            None
            if contract_version == "2"
            else _selector_from_dict(operation, value["selector"])
        )
    return LifecyclePlan(
        contract_version=contract_version,
        operation=operation,
        operation_id=value["operation_id"],
        source=_assessment_from_dict(value["source"]),
        destination=_assessment_from_dict(value["destination"]),
        destination_parent=_directory_identity_from_dict(value["destination_parent"]),
        content=_content_from_dict(value["content"]),
        strategy_id=strategy_id,
        backup_destination=backup_destination,
        backup_destination_parent=backup_destination_parent,
        selector=selector,
        plan_digest=value["plan_digest"],
    )


def _upgrade_strategy_id(source: LifecycleAssessment) -> str:
    if (
        source.target.kind is LifecycleTargetKind.FILE_STORAGE
        and source.status is LifecycleStatus.MIGRATION_REQUIRED
        and source.current_version == FILE_STORAGE_FORMAT.current_version
    ):
        strategy_id = _FILE_STORAGE_UPGRADE_STRATEGIES.get(source.detected_version)
        if strategy_id is not None:
            return strategy_id
    if (
        source.target.kind is LifecycleTargetKind.SQLITE
        and source.status is LifecycleStatus.MIGRATION_REQUIRED
        and source.current_version == SQLITE_FORMAT.current_version
    ):
        strategy_id = _SQLITE_UPGRADE_STRATEGIES.get(source.detected_version)
        if strategy_id is not None:
            return strategy_id
    if (
        source.target.kind is LifecycleTargetKind.MEMORY_PACK
        and source.status is LifecycleStatus.MIGRATION_REQUIRED
        and source.detected_version is not None
        and source.current_version == MEMORY_PACK_FORMAT.current_version
    ):
        return (
            f"{_MEMORY_PACK_STRATEGY_PREFIX}{source.detected_version}"
            f"-to-{source.current_version}"
        )
    raise LifecyclePlanError(
        "no verified lifecycle upgrade strategy exists for this source version"
    )


def _erasure_storage_kind(kind: LifecycleTargetKind) -> ErasureStorageKind:
    if kind is LifecycleTargetKind.FILE_STORAGE:
        return ErasureStorageKind.FILE_STORAGE
    if kind is LifecycleTargetKind.SQLITE:
        return ErasureStorageKind.SQLITE
    raise LifecyclePlanError("lifecycle erasure requires FileStorage or SQLite")


def _erasure_strategy_id(
    operation: LifecycleOperation,
    kind: LifecycleTargetKind,
) -> str:
    storage_kind = _erasure_storage_kind(kind)
    prefix = (
        _ERASE_STRATEGY_PREFIX
        if operation is LifecycleOperation.ERASE
        else _REBUILD_STRATEGY_PREFIX
    )
    return f"{prefix}{storage_kind.value}-v1"


def _import_strategy_id(kind: LifecycleTargetKind) -> str:
    if kind not in {LifecycleTargetKind.FILE_STORAGE, LifecycleTargetKind.SQLITE}:
        raise LifecyclePlanError("MemoryPack import destination is unsupported")
    return f"{_IMPORT_STRATEGY_PREFIX}{kind.value}-v1"


def _validate_plan_shape(plan: LifecyclePlan) -> None:
    _validate_assessment(plan.source)
    _validate_assessment(plan.destination)
    if plan.backup_destination is not None:
        _validate_assessment(plan.backup_destination)
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
        if plan.strategy_id != _BACKUP_STRATEGY_ID:
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
        if plan.strategy_id != _RESTORE_STRATEGY_ID:
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
        if plan.strategy_id != _upgrade_strategy_id(plan.source):
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
        _erasure_storage_kind(plan.source.target.kind)
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
        if plan.strategy_id != _erasure_strategy_id(
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
        if plan.strategy_id != _import_strategy_id(plan.destination.target.kind):
            raise LifecyclePlanError("MemoryPack import strategy identity is invalid")
    else:  # pragma: no cover - Enum construction closes this branch.
        raise LifecyclePlanError("lifecycle plan operation is unsupported")


def _make_plan(
    *,
    operation: LifecycleOperation,
    source: LifecycleAssessment,
    destination: LifecycleAssessment,
    destination_parent: LifecycleDirectoryIdentity,
    content: LifecycleContentIdentity,
    strategy_id: str,
    backup_destination: LifecycleAssessment | None = None,
    backup_destination_parent: LifecycleDirectoryIdentity | None = None,
    selector: LifecyclePlanSelector | None = None,
) -> LifecyclePlan:
    intent = {
        "contract_version": LIFECYCLE_PLAN_CONTRACT_VERSION,
        "operation": operation.value,
        "source": _assessment_to_dict(source),
        "destination": _assessment_to_dict(destination),
        "destination_parent": _directory_identity_to_dict(destination_parent),
        "content": _content_to_dict(content),
        "strategy_id": strategy_id,
        "backup_destination": (
            None
            if backup_destination is None
            else _assessment_to_dict(backup_destination)
        ),
        "backup_destination_parent": (
            None
            if backup_destination_parent is None
            else _directory_identity_to_dict(backup_destination_parent)
        ),
        "selector": _selector_to_dict(selector),
    }
    operation_id = _sha256_json(intent)
    plan_digest = _sha256_json({**intent, "operation_id": operation_id})
    return LifecyclePlan(
        contract_version=LIFECYCLE_PLAN_CONTRACT_VERSION,
        operation=operation,
        operation_id=operation_id,
        source=source,
        destination=destination,
        destination_parent=destination_parent,
        content=content,
        strategy_id=strategy_id,
        backup_destination=backup_destination,
        backup_destination_parent=backup_destination_parent,
        selector=selector,
        plan_digest=plan_digest,
    )


def _missing_assessment(
    target: LifecycleTarget,
    compatibility: FormatCompatibility,
) -> LifecycleAssessment:
    return LifecycleAssessment(
        target=target,
        status=LifecycleStatus.MISSING,
        format_id=compatibility.format_id,
        detected_version=None,
        current_version=compatibility.current_version,
        fingerprint=None,
        file_count=0,
    )


def _compatibility_for_kind(kind: LifecycleTargetKind) -> FormatCompatibility:
    if kind is LifecycleTargetKind.FILE_STORAGE:
        return FILE_STORAGE_FORMAT
    if kind is LifecycleTargetKind.SQLITE:
        return SQLITE_FORMAT
    if kind is LifecycleTargetKind.MEMORY_PACK:
        return MEMORY_PACK_FORMAT
    if kind is LifecycleTargetKind.BACKUP:
        return LIFECYCLE_BACKUP_FORMAT
    raise LifecyclePlanError(f"unsupported lifecycle target kind {kind!r}")


def _validate_assessment(assessment: LifecycleAssessment) -> None:
    if not isinstance(assessment.target, LifecycleTarget) or not isinstance(
        assessment.status, LifecycleStatus
    ):
        raise LifecyclePlanError("lifecycle assessment target or status is invalid")
    compatibility = _compatibility_for_kind(assessment.target.kind)
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
    if assessment.fingerprint is None or not _is_sha256(assessment.fingerprint):
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


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _stat_is_link_or_reparse(info: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _stat_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        stat.S_IFMT(info.st_mode),
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_nlink,
    )


def _stat_object_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (stat.S_IFMT(info.st_mode), info.st_dev, info.st_ino)


def _lstat(path: Path, *, label: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise StorageIntegrityError(f"cannot inspect {label}") from exc


def _require_regular_file(path: Path, *, label: str) -> os.stat_result:
    info = _lstat(path, label=label)
    if _stat_is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise StorageIntegrityError(f"{label} must be a regular file without links")
    return info


def _require_regular_directory(path: Path, *, label: str) -> os.stat_result:
    info = _lstat(path, label=label)
    if _stat_is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise StorageIntegrityError(f"{label} must be a regular directory without links")
    return info


def _assert_no_link_or_reparse_ancestors(path: Path, *, label: str) -> None:
    current = Path(os.path.abspath(path))
    while True:
        if _lexists(current):
            info = _lstat(current, label=label)
            if _stat_is_link_or_reparse(info):
                raise StorageIntegrityError(f"{label} cannot use a linked or reparse path")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _resolved_path(path: Path, *, label: str) -> str:
    _assert_no_link_or_reparse_ancestors(path, label=label)
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise StorageIntegrityError(f"cannot resolve {label}") from exc
    _assert_no_link_or_reparse_ancestors(path, label=label)
    return os.path.normcase(os.path.normpath(str(resolved)))


def _directory_identity(path: Path) -> LifecycleDirectoryIdentity:
    _assert_no_link_or_reparse_ancestors(path, label="lifecycle destination parent")
    info = _require_regular_directory(path, label="lifecycle destination parent")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise StorageIntegrityError("cannot resolve lifecycle destination parent") from exc
    final = _require_regular_directory(path, label="lifecycle destination parent")
    if _stat_signature(info) != _stat_signature(final):
        raise StorageIntegrityError("lifecycle destination parent changed during inspection")
    return LifecycleDirectoryIdentity(
        resolved_path=os.path.normcase(os.path.normpath(str(resolved))),
        device=final.st_dev,
        inode=final.st_ino,
    )


def _scan_directory_entries(
    root: Path,
    *,
    label: str,
) -> tuple[Dict[str, tuple[int, int, int, int, int, int]], set[str]]:
    _assert_no_link_or_reparse_ancestors(root, label=label)
    _require_regular_directory(root, label=label)
    files: Dict[str, tuple[int, int, int, int, int, int]] = {}
    directories: set[str] = set()

    def walk(
        directory: Path,
        relative_parts: tuple[str, ...],
        expected_identity: tuple[int, int, int],
    ) -> None:
        current = _require_regular_directory(directory, label=label)
        if _stat_object_identity(current) != expected_identity:
            raise StorageIntegrityError(f"{label} changed during inspection")
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise StorageIntegrityError(f"{label} cannot be scanned") from exc
        for entry in entries:
            relative = PurePosixPath(*relative_parts, entry.name).as_posix()
            # On Windows/Python 3.11, DirEntry.stat(follow_symlinks=False) can
            # report (st_dev, st_ino) as (0, 0) while os.lstat reports the real
            # identity. Use one identity source throughout the scan.
            info = _lstat(Path(entry.path), label=f"{label} entry")
            if _stat_is_link_or_reparse(info):
                raise StorageIntegrityError(f"{label} contains a link or reparse entry")
            if stat.S_ISDIR(info.st_mode):
                directories.add(relative)
                walk(
                    Path(entry.path),
                    (*relative_parts, entry.name),
                    _stat_object_identity(info),
                )
            elif stat.S_ISREG(info.st_mode):
                files[relative] = _stat_signature(info)
            else:
                raise StorageIntegrityError(f"{label} contains a non-regular entry")
        final = _require_regular_directory(directory, label=label)
        if _stat_object_identity(final) != expected_identity:
            raise StorageIntegrityError(f"{label} changed during inspection")

    root_info = _require_regular_directory(root, label=label)
    walk(root, (), _stat_object_identity(root_info))
    return files, directories


def _is_file_storage_runtime_lock(relative_name: str) -> bool:
    path = PurePosixPath(relative_name)
    if path.parts == ("_turn_context_snapshot.lock",):
        return True
    if len(path.parts) != 2 or path.parts[0] not in _FILE_STORAGE_RUNTIME_LOCK_DIRECTORIES:
        return False
    filename = path.parts[1]
    if not filename.endswith(".lock"):
        return False
    digest = filename.removesuffix(".lock")
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def _read_stable_bytes(
    path: Path,
    *,
    expected_signature: tuple[int, int, int, int, int, int] | None = None,
    read_limit: int | None = None,
    size_limit: int | None = None,
) -> bytes:
    label = f"lifecycle source file {path.name!r}"
    _assert_no_link_or_reparse_ancestors(path, label=label)
    before = _require_regular_file(path, label=label)
    if size_limit is not None and before.st_size > size_limit:
        raise StorageIntegrityError(
            f"{label} exceeds the supported lifecycle size limit"
        )
    if expected_signature is not None and _stat_signature(before) != expected_signature:
        raise StorageIntegrityError("lifecycle source changed before it was read")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if _stat_is_link_or_reparse(opened) or not stat.S_ISREG(opened.st_mode):
            raise StorageIntegrityError(f"{label} is not a regular file")
        if _stat_signature(before) != _stat_signature(opened):
            raise StorageIntegrityError("lifecycle source changed before it was opened")
        chunks = bytearray()
        while True:
            if read_limit is not None:
                remaining = read_limit - len(chunks)
                if remaining <= 0:
                    break
                chunk_size = min(1024 * 1024, remaining)
            else:
                chunk_size = 1024 * 1024
            chunk = os.read(descriptor, chunk_size)
            if not chunk:
                break
            chunks.extend(chunk)
        after_open = os.fstat(descriptor)
        _assert_no_link_or_reparse_ancestors(path, label=label)
        after_path = _require_regular_file(path, label=label)
    except StorageIntegrityError:
        raise
    except OSError as exc:
        raise StorageIntegrityError(f"cannot read {label}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not (
        _stat_signature(before)
        == _stat_signature(opened)
        == _stat_signature(after_open)
        == _stat_signature(after_path)
    ):
        raise StorageIntegrityError("lifecycle source changed during inspection")
    if expected_signature is not None and _stat_signature(after_path) != expected_signature:
        raise StorageIntegrityError("lifecycle source changed during inspection")
    return bytes(chunks)


def _fingerprint_files(content_by_name: Dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(content_by_name):
        name_bytes = name.encode("utf-8")
        content = content_by_name[name]
        digest.update(len(name_bytes).to_bytes(8, "big"))
        digest.update(name_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _sqlite_uri(path: Path, *, immutable: bool) -> str:
    query = "mode=ro"
    if immutable:
        query += "&immutable=1"
    return f"{path.resolve().as_uri()}?{query}"


def read_sqlite_schema_version(path_value: str, *, immutable: bool) -> int | None:
    """Reads an existing SQLite schema identity without creating the database."""
    path = Path(path_value)
    if not _lexists(path):
        return None
    _assert_no_link_or_reparse_ancestors(path, label="SQLite lifecycle target")
    info = _require_regular_file(path, label="SQLite lifecycle target")
    if info.st_size == 0:
        return 0
    try:
        if _read_stable_bytes(path, read_limit=16) != b"SQLite format 3\x00":
            raise StorageIntegrityError("SQLite lifecycle target has an invalid header")
        with closing(
            sqlite3.connect(_sqlite_uri(path, immutable=immutable), uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            if exists is None:
                return 0
            rows = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
    except UnsupportedFormatError:
        raise
    except StorageIntegrityError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise StorageIntegrityError("SQLite schema metadata is unreadable or malformed") from exc
    versions = [row[0] for row in rows]
    if any(isinstance(version, bool) or not isinstance(version, int) for version in versions):
        raise StorageIntegrityError("SQLite schema migration versions must be integers")
    current = max(versions, default=0)
    if current > int(SQLITE_FORMAT.current_version):
        raise UnsupportedFormatError(
            f"unsupported {SQLITE_FORMAT.format_id} version {current!r}; "
            f"current reader is {SQLITE_FORMAT.current_version!r}"
        )
    if versions != list(range(1, current + 1)):
        raise StorageIntegrityError("SQLite schema migration history is not contiguous")
    return current


class LifecycleInspector:
    """Inspects supported lifecycle targets without instantiating storage drivers."""

    def inspect(self, target: LifecycleTarget) -> LifecycleAssessment:
        if not isinstance(target, LifecycleTarget):
            raise TypeError("inspect() requires a LifecycleTarget")
        _assert_no_link_or_reparse_ancestors(
            Path(target.path),
            label="lifecycle target",
        )
        if target.kind is LifecycleTargetKind.FILE_STORAGE:
            return self._inspect_file_storage(target)
        if target.kind is LifecycleTargetKind.SQLITE:
            return self._inspect_sqlite(target)
        if target.kind is LifecycleTargetKind.MEMORY_PACK:
            return self._inspect_memory_pack(target)
        if target.kind is LifecycleTargetKind.BACKUP:
            return _read_backup_bundle(target).assessment
        raise ValueError(f"unsupported lifecycle target kind {target.kind!r}")

    @staticmethod
    def _scan_file_storage(root: Path) -> Dict[str, bytes]:
        initial_files, _ = _scan_directory_entries(
            root,
            label="FileStorage lifecycle target",
        )
        if any(PurePosixPath(name).name.endswith(".tmp") for name in initial_files):
            raise StorageIntegrityError(
                "FileStorage lifecycle target contains an incomplete temporary file"
            )
        initial = {
            name: signature
            for name, signature in initial_files.items()
            if not _is_file_storage_runtime_lock(name)
        }
        content_by_name = {
            name: _read_stable_bytes(
                root.joinpath(*PurePosixPath(name).parts),
                expected_signature=initial[name],
            )
            for name in sorted(initial)
        }
        final_files, _ = _scan_directory_entries(
            root,
            label="FileStorage lifecycle target",
        )
        if any(PurePosixPath(name).name.endswith(".tmp") for name in final_files):
            raise StorageIntegrityError("FileStorage lifecycle target changed during inspection")
        final = {
            name: signature
            for name, signature in final_files.items()
            if not _is_file_storage_runtime_lock(name)
        }
        if final != initial:
            raise StorageIntegrityError("FileStorage lifecycle target changed during inspection")
        return content_by_name

    @classmethod
    def _inspect_file_storage(cls, target: LifecycleTarget) -> LifecycleAssessment:
        root = Path(target.path)
        if not _lexists(root):
            return _missing_assessment(target, FILE_STORAGE_FORMAT)
        _require_regular_directory(root, label="FileStorage lifecycle target")
        from erii.lifecycle_streaming import stream_regular_tree_manifest

        streamed = stream_regular_tree_manifest(
            root,
            exclude_relative_name=_is_file_storage_runtime_lock,
        )
        relative_names = tuple(entry.relative_path for entry in streamed.files)
        if any(PurePosixPath(name).name.endswith(".tmp") for name in relative_names):
            raise StorageIntegrityError(
                "FileStorage lifecycle target contains an incomplete temporary file"
            )
        manifest_content: bytes | None = None
        for relative_name in relative_names:
            if not relative_name.endswith(".json"):
                continue
            content = _read_stable_bytes(
                root.joinpath(*PurePosixPath(relative_name).parts),
                size_limit=MAX_LIFECYCLE_TRANSFORM_BYTES,
            )
            try:
                json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StorageIntegrityError(
                    f"FileStorage JSON document {Path(relative_name).name!r} is malformed"
                ) from exc
            if relative_name == FILE_STORAGE_MANIFEST:
                manifest_content = content
        final_streamed = stream_regular_tree_manifest(
            root,
            exclude_relative_name=_is_file_storage_runtime_lock,
        )
        if final_streamed != streamed:
            raise StorageIntegrityError(
                "FileStorage lifecycle target changed during inspection"
            )

        warnings = ()
        if manifest_content is not None:
            try:
                manifest = json.loads(manifest_content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StorageIntegrityError("FileStorage manifest is malformed") from exc
            if not isinstance(manifest, dict) or set(manifest) != _FILE_STORAGE_MANIFEST_FIELDS:
                raise StorageIntegrityError("FileStorage manifest fields are invalid")
            if manifest.get("format") != FILE_STORAGE_FORMAT.format_id:
                raise StorageIntegrityError("FileStorage manifest format identity is invalid")
            raw_version = manifest.get("version")
            if isinstance(raw_version, bool) or not isinstance(raw_version, int):
                raise StorageIntegrityError("FileStorage manifest version must be an integer")
            detected_version = str(raw_version)
            require_supported_version(FILE_STORAGE_FORMAT, detected_version)
            status = (
                LifecycleStatus.CURRENT
                if detected_version == FILE_STORAGE_FORMAT.current_version
                else LifecycleStatus.MIGRATION_REQUIRED
            )
        elif not relative_names:
            detected_version = None
            status = LifecycleStatus.EMPTY
        else:
            recognized = any(
                Path(name).name in _LEGACY_BASENAMES
                or Path(name).parts[0] in _LEGACY_TOP_LEVEL_DIRECTORIES
                for name in relative_names
            )
            if not recognized:
                raise StorageIntegrityError(
                    "directory does not contain a recognizable E.R.I.I. FileStorage layout"
                )
            detected_version = "legacy"
            status = LifecycleStatus.MIGRATION_REQUIRED
            if any(not name.endswith(".json") for name in relative_names):
                warnings = ("unrecognized non-JSON files are included in the fingerprint",)

        return LifecycleAssessment(
            target=target,
            status=status,
            format_id=FILE_STORAGE_FORMAT.format_id,
            detected_version=detected_version,
            current_version=FILE_STORAGE_FORMAT.current_version,
            fingerprint=streamed.tree_fingerprint,
            file_count=streamed.file_count,
            warnings=warnings,
        )

    @staticmethod
    def _inspect_sqlite(target: LifecycleTarget) -> LifecycleAssessment:
        path = Path(target.path)
        if not _lexists(path):
            return _missing_assessment(target, SQLITE_FORMAT)
        _require_regular_file(path, label="SQLite lifecycle target")
        sidecars = {suffix: Path(f"{path}{suffix}") for suffix in ("-wal", "-shm", "-journal")}
        for suffix, item in sidecars.items():
            if not _lexists(item):
                continue
            sidecar_info = _require_regular_file(item, label="SQLite lifecycle sidecar")
            if suffix in {"-wal", "-journal"} and sidecar_info.st_size:
                raise StorageIntegrityError(
                    "SQLite lifecycle inspection requires a quiescent database without WAL or journal data"
                )
        observed = [path] + [
            Path(f"{path}{suffix}")
            for suffix in ("-wal", "-shm", "-journal")
            if _lexists(Path(f"{path}{suffix}"))
        ]
        initial_signatures = {
            item.name: _stat_signature(_require_regular_file(item, label="SQLite lifecycle file"))
            for item in observed
        }
        version = read_sqlite_schema_version(str(path), immutable=True)
        main_size = initial_signatures[path.name][3]
        try:
            with closing(
                sqlite3.connect(_sqlite_uri(path, immutable=True), uri=True)
            ) as connection:
                connection.execute("PRAGMA query_only=ON")
                quick_check = [row[0] for row in connection.execute("PRAGMA quick_check")]
        except sqlite3.Error as exc:
            raise StorageIntegrityError("SQLite integrity check could not be completed") from exc
        if quick_check != ["ok"]:
            raise StorageIntegrityError("SQLite quick integrity check failed")
        semantic_fingerprint = None
        if main_size:
            # Imported lazily because the private migration support imports the
            # public schema reader above.  SQLite identity is semantic: page
            # layout and migration timestamps must not make a planned upgrade
            # impossible to verify on a later execution.
            from erii.lifecycle_sqlite_upgrade import _semantic_digest_from_path

            semantic_fingerprint = _semantic_digest_from_path(path)
        observed_after = [path] + [
            Path(f"{path}{suffix}")
            for suffix in ("-wal", "-shm", "-journal")
            if _lexists(Path(f"{path}{suffix}"))
        ]
        if [item.name for item in observed_after] != [item.name for item in observed]:
            raise StorageIntegrityError("SQLite lifecycle target changed during inspection")
        if any(
            item.name.endswith(("-wal", "-journal"))
            and _require_regular_file(item, label="SQLite lifecycle sidecar").st_size
            for item in observed_after
        ):
            raise StorageIntegrityError("SQLite lifecycle target changed during inspection")
        final_signatures = {
            item.name: _stat_signature(
                _require_regular_file(item, label="SQLite lifecycle file")
            )
            for item in observed_after
        }
        if final_signatures != initial_signatures:
            raise StorageIntegrityError("SQLite lifecycle target changed during inspection")
        if main_size == 0:
            return LifecycleAssessment(
                target=target,
                status=LifecycleStatus.EMPTY,
                format_id=SQLITE_FORMAT.format_id,
                detected_version=None,
                current_version=SQLITE_FORMAT.current_version,
                fingerprint=_fingerprint_files({"database.sqlite3": b""}),
                file_count=1,
            )
        detected_version = str(version or 0)
        return LifecycleAssessment(
            target=target,
            status=(
                LifecycleStatus.CURRENT
                if detected_version == SQLITE_FORMAT.current_version
                else LifecycleStatus.MIGRATION_REQUIRED
            ),
            format_id=SQLITE_FORMAT.format_id,
            detected_version=detected_version,
            current_version=SQLITE_FORMAT.current_version,
            fingerprint=semantic_fingerprint,
            file_count=1,
        )

    @staticmethod
    def _inspect_memory_pack(target: LifecycleTarget) -> LifecycleAssessment:
        path = Path(target.path)
        if not _lexists(path):
            return _missing_assessment(target, MEMORY_PACK_FORMAT)
        _require_regular_file(path, label="MemoryPack lifecycle target")
        content = _read_stable_bytes(
            path,
            size_limit=MAX_LIFECYCLE_MEMORY_PACK_BYTES,
        )
        try:
            decoded = decode_memory_pack_json(content.decode("utf-8"))
        except UnsupportedFormatError:
            raise
        except (UnicodeDecodeError, ValueError) as exc:
            raise StorageIntegrityError("MemoryPack is unreadable or malformed") from exc
        version = decoded["metadata"]["version"]
        return LifecycleAssessment(
            target=target,
            status=(
                LifecycleStatus.CURRENT
                if version == MEMORY_PACK_FORMAT.current_version
                else LifecycleStatus.MIGRATION_REQUIRED
            ),
            format_id=MEMORY_PACK_FORMAT.format_id,
            detected_version=version,
            current_version=MEMORY_PACK_FORMAT.current_version,
            fingerprint=hashlib.sha256(content).hexdigest(),
            file_count=1,
        )


@dataclass(frozen=True, slots=True)
class _PayloadSnapshot:
    content: LifecycleContentIdentity
    files: Dict[str, bytes] | None = None
    source_paths: Dict[str, str] | None = None
    identities: Dict[str, RegularFileIdentity] | None = None

    def __post_init__(self) -> None:
        materialized = self.files is not None
        streamed = self.source_paths is not None or self.identities is not None
        if materialized == streamed:
            raise ValueError(
                "payload snapshot must be exactly one of materialized or streamed"
            )
        if streamed:
            if self.source_paths is None or self.identities is None:
                raise ValueError("streamed payload snapshot is incomplete")
            if set(self.source_paths) != set(self.identities):
                raise ValueError("streamed payload snapshot entries do not match")


@dataclass(frozen=True, slots=True)
class _BackupBundle:
    assessment: LifecycleAssessment
    content: LifecycleContentIdentity | None
    operation_id: str | None
    plan_digest: str | None
    snapshot: _PayloadSnapshot | None


def _materialize_snapshot(source: _PayloadSnapshot) -> _PayloadSnapshot:
    if source.files is not None:
        return source
    assert source.source_paths is not None and source.identities is not None
    total_size = sum(identity.size for identity in source.identities.values())
    limit = (
        MAX_LIFECYCLE_MEMORY_PACK_BYTES
        if source.content.kind is LifecycleTargetKind.MEMORY_PACK
        else MAX_LIFECYCLE_TRANSFORM_BYTES
    )
    if total_size > limit:
        raise StorageIntegrityError(
            "lifecycle transform input exceeds its bounded materialization limit"
        )
    files: Dict[str, bytes] = {}
    for relative_name in sorted(source.source_paths):
        content = _read_stable_bytes(
            Path(source.source_paths[relative_name]),
            size_limit=limit,
        )
        identity = RegularFileIdentity(
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        if identity != source.identities[relative_name]:
            raise StaleLifecyclePlanError(
                "lifecycle source changed while its transform was materialized"
            )
        files[relative_name] = content
    return _PayloadSnapshot(content=source.content, files=files)


def _snapshot_file_manifest(
    snapshot: _PayloadSnapshot,
) -> list[Dict[str, object]]:
    if snapshot.files is not None:
        return _payload_file_manifest(snapshot.files)
    assert snapshot.identities is not None
    return [
        {
            "path": relative_name,
            "size": snapshot.identities[relative_name].size,
            "sha256": snapshot.identities[relative_name].sha256,
        }
        for relative_name in sorted(snapshot.identities)
    ]


def _write_snapshot_file(
    snapshot: _PayloadSnapshot,
    relative_name: str,
    destination: Path,
) -> None:
    if snapshot.files is not None:
        _write_durable_file(destination, snapshot.files[relative_name])
        return
    assert snapshot.source_paths is not None and snapshot.identities is not None
    try:
        copy_regular_file_exclusive(
            snapshot.source_paths[relative_name],
            destination,
            expected=snapshot.identities[relative_name],
        )
    except FileExistsError as exc:
        raise StorageWriteError("lifecycle snapshot destination already exists") from exc


def _write_snapshot_files(root: Path, snapshot: _PayloadSnapshot) -> None:
    names = (
        snapshot.files.keys()
        if snapshot.files is not None
        else snapshot.source_paths.keys()  # type: ignore[union-attr]
    )
    for relative_name in sorted(names):
        relative = PurePosixPath(relative_name)
        _validate_relative_payload_path(relative_name)
        _ensure_private_payload_parent(root, relative)
        _write_snapshot_file(
            snapshot,
            relative_name,
            root.joinpath(*relative.parts),
        )


class _LifecycleFormatAdapter:
    kind: LifecycleTargetKind
    payload_entry: str

    def capture(self, assessment: LifecycleAssessment) -> _PayloadSnapshot:
        raise NotImplementedError

    def restored_target(self, staging_path: Path) -> LifecycleTarget:
        return LifecycleTarget(self.kind, str(staging_path))

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        raise NotImplementedError


class _FileLifecycleAdapter(_LifecycleFormatAdapter):
    kind = LifecycleTargetKind.FILE_STORAGE
    payload_entry = "payload"

    def capture(self, assessment: LifecycleAssessment) -> _PayloadSnapshot:
        root = Path(assessment.target.path)
        manifest = stream_regular_tree_manifest(
            root,
            exclude_relative_name=_is_file_storage_runtime_lock,
        )
        snapshot = _PayloadSnapshot(
            content=LifecycleContentIdentity.from_assessment(assessment),
            source_paths={
                entry.relative_path: str(
                    root.joinpath(*PurePosixPath(entry.relative_path).parts)
                )
                for entry in manifest.files
            },
            identities={
                entry.relative_path: RegularFileIdentity(
                    size=entry.size,
                    sha256=entry.sha256,
                )
                for entry in manifest.files
            },
        )
        if (
            manifest.tree_fingerprint != snapshot.content.fingerprint
            or manifest.file_count != snapshot.content.file_count
        ):
            raise StaleLifecyclePlanError("FileStorage changed while it was captured")
        return snapshot

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        _create_private_directory(staging_path)
        _write_snapshot_files(staging_path, snapshot)


class _SQLiteLifecycleAdapter(_LifecycleFormatAdapter):
    kind = LifecycleTargetKind.SQLITE
    payload_entry = "payload/database.sqlite3"

    def capture(self, assessment: LifecycleAssessment) -> _PayloadSnapshot:
        source_path = Path(assessment.target.path)
        identity = stream_regular_file_identity(source_path)
        snapshot = _PayloadSnapshot(
            content=LifecycleContentIdentity.from_assessment(assessment),
            source_paths={"database.sqlite3": str(source_path)},
            identities={"database.sqlite3": identity},
        )
        if assessment.status is LifecycleStatus.EMPTY:
            if identity.size != 0:
                raise StaleLifecyclePlanError("SQLite changed while it was captured")
            actual_fingerprint = _fingerprint_files({"database.sqlite3": b""})
        else:
            from erii.lifecycle_sqlite_upgrade import _semantic_digest_from_path

            actual_fingerprint = _semantic_digest_from_path(
                Path(assessment.target.path)
            )
        if actual_fingerprint != snapshot.content.fingerprint:
            raise StaleLifecyclePlanError("SQLite changed while it was captured")
        return snapshot

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        _write_snapshot_file(snapshot, "database.sqlite3", staging_path)


class _MemoryPackLifecycleAdapter(_LifecycleFormatAdapter):
    kind = LifecycleTargetKind.MEMORY_PACK
    payload_entry = "payload/memory-pack.erii"

    def capture(self, assessment: LifecycleAssessment) -> _PayloadSnapshot:
        source_path = Path(assessment.target.path)
        identity = stream_regular_file_identity(source_path)
        if identity.size > MAX_LIFECYCLE_MEMORY_PACK_BYTES:
            raise StorageIntegrityError(
                "MemoryPack exceeds the supported lifecycle size limit"
            )
        snapshot = _PayloadSnapshot(
            content=LifecycleContentIdentity.from_assessment(assessment),
            source_paths={"memory-pack.erii": str(source_path)},
            identities={"memory-pack.erii": identity},
        )
        if identity.sha256 != snapshot.content.fingerprint:
            raise StaleLifecyclePlanError("MemoryPack changed while it was captured")
        return snapshot

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        _write_snapshot_file(snapshot, "memory-pack.erii", staging_path)


_FORMAT_ADAPTERS: Dict[LifecycleTargetKind, _LifecycleFormatAdapter] = {
    LifecycleTargetKind.FILE_STORAGE: _FileLifecycleAdapter(),
    LifecycleTargetKind.SQLITE: _SQLiteLifecycleAdapter(),
    LifecycleTargetKind.MEMORY_PACK: _MemoryPackLifecycleAdapter(),
}


def _adapter_for_kind(kind: LifecycleTargetKind) -> _LifecycleFormatAdapter:
    try:
        return _FORMAT_ADAPTERS[kind]
    except KeyError as exc:
        raise LifecyclePlanError(f"no live-data lifecycle adapter for {kind.value!r}") from exc


def _upgrade_snapshot(
    strategy_id: str,
    source: _PayloadSnapshot,
) -> _PayloadSnapshot:
    source = _materialize_snapshot(source)
    assert source.files is not None
    if strategy_id in _SQLITE_UPGRADE_STRATEGIES.values():
        return _upgrade_sqlite_snapshot(strategy_id, source)
    if strategy_id.startswith(_MEMORY_PACK_STRATEGY_PREFIX):
        return _upgrade_memory_pack_snapshot(strategy_id, source)
    if strategy_id not in _FILE_STORAGE_UPGRADE_STRATEGIES.values():
        raise LifecyclePlanError("lifecycle upgrade strategy is unavailable")
    return _upgrade_file_storage_snapshot(strategy_id, source)


def _upgrade_file_storage_snapshot(
    strategy_id: str,
    source: _PayloadSnapshot,
) -> _PayloadSnapshot:
    assert source.files is not None
    expected_strategy = _FILE_STORAGE_UPGRADE_STRATEGIES.get(
        source.content.detected_version
    )
    if (
        strategy_id != expected_strategy
        or source.content.kind is not LifecycleTargetKind.FILE_STORAGE
        or source.content.status is not LifecycleStatus.MIGRATION_REQUIRED
        or source.content.current_version != FILE_STORAGE_FORMAT.current_version
    ):
        raise LifecyclePlanError("FileStorage upgrade source identity is invalid")

    files = dict(source.files)
    if source.content.detected_version == "legacy":
        if FILE_STORAGE_MANIFEST in files:
            raise StorageIntegrityError(
                "legacy FileStorage unexpectedly contains a manifest"
            )
    else:
        manifest_bytes = files.get(FILE_STORAGE_MANIFEST)
        if manifest_bytes is None:
            raise StorageIntegrityError("versioned FileStorage is missing its manifest")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageIntegrityError("FileStorage manifest is malformed") from exc
        if manifest != {
            "format": FILE_STORAGE_FORMAT.format_id,
            "version": 1,
        }:
            raise StorageIntegrityError("FileStorage v1 manifest identity is invalid")

    files[FILE_STORAGE_MANIFEST] = _canonical_json(
        {
            "format": FILE_STORAGE_FORMAT.format_id,
            "version": int(FILE_STORAGE_FORMAT.current_version),
        }
    )
    return _PayloadSnapshot(
        content=LifecycleContentIdentity(
            kind=LifecycleTargetKind.FILE_STORAGE,
            status=LifecycleStatus.CURRENT,
            format_id=FILE_STORAGE_FORMAT.format_id,
            detected_version=FILE_STORAGE_FORMAT.current_version,
            current_version=FILE_STORAGE_FORMAT.current_version,
            fingerprint=_fingerprint_files(files),
            file_count=len(files),
        ),
        files=files,
    )


def _upgrade_sqlite_snapshot(
    strategy_id: str,
    source: _PayloadSnapshot,
) -> _PayloadSnapshot:
    expected_strategy = _SQLITE_UPGRADE_STRATEGIES.get(
        source.content.detected_version
    )
    if (
        strategy_id != expected_strategy
        or source.content.kind is not LifecycleTargetKind.SQLITE
        or source.content.status is not LifecycleStatus.MIGRATION_REQUIRED
        or source.content.current_version != SQLITE_FORMAT.current_version
        or set(source.files) != {"database.sqlite3"}
    ):
        raise LifecyclePlanError("SQLite upgrade source identity is invalid")

    from erii.lifecycle_sqlite_upgrade import _migrate_sqlite_bytes

    migrated, result = _migrate_sqlite_bytes(source.files["database.sqlite3"])
    if (
        str(result.source_version) != source.content.detected_version
        or str(result.target_version) != source.content.current_version
    ):
        raise StorageIntegrityError("SQLite upgrade result has the wrong schema")
    return _PayloadSnapshot(
        content=LifecycleContentIdentity(
            kind=LifecycleTargetKind.SQLITE,
            status=LifecycleStatus.CURRENT,
            format_id=SQLITE_FORMAT.format_id,
            detected_version=SQLITE_FORMAT.current_version,
            current_version=SQLITE_FORMAT.current_version,
            fingerprint=result.semantic_digest,
            file_count=1,
        ),
        files={"database.sqlite3": migrated},
    )


def _upgrade_memory_pack_snapshot(
    strategy_id: str,
    source: _PayloadSnapshot,
) -> _PayloadSnapshot:
    expected_strategy = (
        f"{_MEMORY_PACK_STRATEGY_PREFIX}{source.content.detected_version}"
        f"-to-{MEMORY_PACK_FORMAT.current_version}"
    )
    if (
        strategy_id != expected_strategy
        or source.content.kind is not LifecycleTargetKind.MEMORY_PACK
        or source.content.status is not LifecycleStatus.MIGRATION_REQUIRED
        or source.content.current_version != MEMORY_PACK_FORMAT.current_version
        or set(source.files) != {"memory-pack.erii"}
    ):
        raise LifecyclePlanError("MemoryPack upgrade source identity is invalid")
    try:
        pack = MemoryPack.from_json(source.files["memory-pack.erii"].decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise StorageIntegrityError("MemoryPack upgrade source is malformed") from exc
    if pack.version != source.content.detected_version:
        raise StorageIntegrityError("MemoryPack upgrade version changed during capture")
    _validate_memory_pack_semantic_graph(pack)

    pack.version = MEMORY_PACK_FORMAT.current_version
    upgraded_content = pack.to_json().encode("utf-8")
    try:
        verified = MemoryPack.from_json(upgraded_content.decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise StorageIntegrityError("MemoryPack upgrade result is malformed") from exc
    if verified.version != MEMORY_PACK_FORMAT.current_version:
        raise StorageIntegrityError("MemoryPack upgrade result has the wrong version")
    _validate_memory_pack_semantic_graph(verified)

    return _PayloadSnapshot(
        content=LifecycleContentIdentity(
            kind=LifecycleTargetKind.MEMORY_PACK,
            status=LifecycleStatus.CURRENT,
            format_id=MEMORY_PACK_FORMAT.format_id,
            detected_version=MEMORY_PACK_FORMAT.current_version,
            current_version=MEMORY_PACK_FORMAT.current_version,
            fingerprint=hashlib.sha256(upgraded_content).hexdigest(),
            file_count=1,
        ),
        files={"memory-pack.erii": upgraded_content},
    )


def _validate_memory_pack_semantic_graph(pack: MemoryPack) -> None:
    """Runs the production import graph checks without opening any Storage."""
    from erii._engine.memory_pack_analysis import (
        analyze_memory_pack,
        validate_memory_pack_persisted_turn_adjudications,
        validate_memory_pack_turn_records,
    )
    from erii.core.memory_pack_evidence import validate_memory_pack_archival_evidence
    from erii.engine import ERIIEngine

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
        ERIIEngine._validate_relationship_processing_pack(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
        )
    except ValueError as exc:
        raise StorageIntegrityError(
            "MemoryPack semantic graph validation failed"
        ) from exc


def _write_durable_file(path: Path, content: bytes) -> None:
    descriptor = -1
    try:
        _assert_no_link_or_reparse_ancestors(
            path.parent,
            label="staged lifecycle file parent",
        )
        _require_regular_directory(path.parent, label="staged lifecycle file parent")
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        opened = os.fstat(descriptor)
        linked = os.lstat(path)
        if (
            _stat_is_link_or_reparse(opened)
            or _stat_is_link_or_reparse(linked)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _stat_object_identity(opened) != _stat_object_identity(linked)
        ):
            raise StorageWriteError("staged lifecycle path is not a private regular file")
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "short lifecycle staging write")
            view = view[written:]
        os.fsync(descriptor)
        final_open = os.fstat(descriptor)
        _assert_no_link_or_reparse_ancestors(
            path,
            label="staged lifecycle file",
        )
        final_path = _require_regular_file(path, label="staged lifecycle file")
        if not (
            _stat_object_identity(opened)
            == _stat_object_identity(final_open)
            == _stat_object_identity(final_path)
        ) or final_open.st_size != len(content):
            raise StorageWriteError("staged lifecycle file changed while it was written")
    except StorageWriteError:
        raise
    except StorageIntegrityError as exc:
        raise StorageWriteError(f"could not safely write staged file {path.name!r}") from exc
    except OSError as exc:
        raise StorageWriteError(f"could not write staged lifecycle file {path.name!r}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _create_private_directory(path: Path) -> None:
    try:
        _assert_no_link_or_reparse_ancestors(
            path.parent,
            label="staged lifecycle directory parent",
        )
        os.mkdir(path, 0o700)
        _require_regular_directory(path, label="staged lifecycle directory")
        _assert_no_link_or_reparse_ancestors(
            path,
            label="staged lifecycle directory",
        )
    except FileExistsError as exc:
        raise LifecycleConflictError("staged lifecycle directory was occupied") from exc
    except StorageIntegrityError as exc:
        raise StorageWriteError("could not safely create staged lifecycle directory") from exc
    except OSError as exc:
        raise StorageWriteError("could not create staged lifecycle directory") from exc


def _ensure_private_payload_parent(root: Path, relative: PurePosixPath) -> None:
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if not _lexists(current):
            _create_private_directory(current)
        else:
            try:
                _require_regular_directory(current, label="staged payload directory")
                _assert_no_link_or_reparse_ancestors(
                    current,
                    label="staged payload directory",
                )
            except StorageIntegrityError as exc:
                raise StorageWriteError("staged payload directory is unsafe") from exc


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically publishes one staged path without replacing an existing name."""

    if os.name == "nt":
        try:
            os.rename(source, destination)
        except OSError as exc:
            if (
                isinstance(exc, FileExistsError)
                or exc.errno
                in {
                    errno.EEXIST,
                    errno.ENOTEMPTY,
                }
                or getattr(exc, "winerror", None) in {80, 183}
            ):
                raise LifecycleConflictError(
                    "lifecycle destination changed before publication"
                ) from exc
            raise
        return
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform.startswith("linux"):
        try:
            renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
        except AttributeError as exc:
            raise StorageWriteError(
                "this platform cannot atomically publish lifecycle data without replacement"
            ) from exc
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, source_bytes, -100, destination_bytes, 1)
    elif sys.platform == "darwin":
        try:
            renamex_np = ctypes.CDLL(None, use_errno=True).renamex_np
        except AttributeError as exc:
            raise StorageWriteError(
                "this platform cannot atomically publish lifecycle data without replacement"
            ) from exc
        renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_bytes, destination_bytes, 0x00000004)
    else:
        raise StorageWriteError(
            "this platform cannot atomically publish lifecycle data without replacement"
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise LifecycleConflictError("lifecycle destination changed before publication")
    raise OSError(error_number, os.strerror(error_number), os.fspath(destination))


def _validate_relative_payload_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise StorageIntegrityError("backup payload path must be a non-empty string")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in value
    ):
        raise StorageIntegrityError("backup payload contains an unsafe relative path")
    return value


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if os.name == "nt" and (
            isinstance(exc, PermissionError)
            or exc.errno in {errno.EACCES, errno.EINVAL}
            or getattr(exc, "winerror", None) in {1, 5, 6, 87}
        ):
            # CPython cannot portably open a directory handle for FlushFileBuffers.
            # File contents remain fsynced; directory-entry persistence is best effort.
            return
        raise StorageWriteError(f"could not open lifecycle directory {path.name!r}") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if os.name == "nt" and (
            exc.errno in {errno.EBADF, errno.EINVAL} or getattr(exc, "winerror", None) in {1, 6, 87}
        ):
            return
        raise StorageWriteError(f"could not synchronize lifecycle directory {path.name!r}") from exc
    finally:
        os.close(descriptor)


def _fsync_tree_directories(root: Path) -> None:
    _, directories = _scan_directory_entries(root, label="staged lifecycle tree")
    paths = [root.joinpath(*PurePosixPath(name).parts) for name in directories]
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(path)
    _fsync_directory(root)


def _payload_file_manifest(files: Dict[str, bytes]) -> list[Dict[str, object]]:
    return [
        {
            "path": relative_name,
            "size": len(files[relative_name]),
            "sha256": hashlib.sha256(files[relative_name]).hexdigest(),
        }
        for relative_name in sorted(files)
    ]


def _backup_manifest(plan: LifecyclePlan, snapshot: _PayloadSnapshot) -> Dict[str, object]:
    adapter = _adapter_for_kind(snapshot.content.kind)
    return {
        "format": LIFECYCLE_BACKUP_FORMAT.format_id,
        "version": LIFECYCLE_BACKUP_FORMAT.current_version,
        "operation_id": plan.operation_id,
        "plan_digest": plan.plan_digest,
        "source": _content_to_dict(snapshot.content),
        "payload": {
            "entry": adapter.payload_entry,
            "files": _snapshot_file_manifest(snapshot),
            "tree_fingerprint": snapshot.content.fingerprint,
        },
    }


def _assessment_matches_content(
    assessment: LifecycleAssessment,
    content: LifecycleContentIdentity,
) -> bool:
    return (
        assessment.target.kind is content.kind
        and assessment.status is content.status
        and assessment.format_id == content.format_id
        and assessment.detected_version == content.detected_version
        and assessment.current_version == content.current_version
        and assessment.fingerprint == content.fingerprint
        and assessment.file_count == content.file_count
    )


def _read_backup_bundle(target: LifecycleTarget) -> _BackupBundle:
    if target.kind is not LifecycleTargetKind.BACKUP:
        raise TypeError("backup inspection requires a backup lifecycle target")
    root = Path(target.path)
    if not _lexists(root):
        return _BackupBundle(
            assessment=_missing_assessment(target, LIFECYCLE_BACKUP_FORMAT),
            content=None,
            operation_id=None,
            plan_digest=None,
            snapshot=None,
        )
    _require_regular_directory(root, label="backup lifecycle target")
    try:
        direct_children = {child.name: child for child in root.iterdir()}
    except OSError as exc:
        raise StorageIntegrityError("backup lifecycle target cannot be read") from exc
    if not direct_children:
        empty_fingerprint = _fingerprint_files({})
        return _BackupBundle(
            assessment=LifecycleAssessment(
                target=target,
                status=LifecycleStatus.EMPTY,
                format_id=LIFECYCLE_BACKUP_FORMAT.format_id,
                detected_version=None,
                current_version=LIFECYCLE_BACKUP_FORMAT.current_version,
                fingerprint=empty_fingerprint,
                file_count=0,
            ),
            content=None,
            operation_id=None,
            plan_digest=None,
            snapshot=None,
        )
    if set(direct_children) != {LIFECYCLE_BACKUP_MANIFEST, "payload"}:
        raise StorageIntegrityError("backup bundle root entries are invalid")
    manifest_path = direct_children[LIFECYCLE_BACKUP_MANIFEST]
    payload_root = direct_children["payload"]
    _require_regular_file(manifest_path, label="backup manifest")
    _require_regular_directory(payload_root, label="backup payload")

    initial_files, initial_directories = _scan_directory_entries(
        root,
        label="backup lifecycle target",
    )
    streamed = stream_regular_tree_manifest(root)
    streamed_entries = {entry.relative_path: entry for entry in streamed.files}
    if set(streamed_entries) != set(initial_files):
        raise StorageIntegrityError("backup lifecycle target changed during inspection")
    for directory in initial_directories:
        if directory == "payload":
            continue
        prefix = f"{directory}/"
        if not any(name.startswith(prefix) for name in initial_files):
            raise StorageIntegrityError(
                "backup lifecycle target contains an undeclared empty directory"
            )
    manifest_bytes = _read_stable_bytes(
        manifest_path,
        expected_signature=initial_files[LIFECYCLE_BACKUP_MANIFEST],
        size_limit=MAX_LIFECYCLE_BACKUP_MANIFEST_BYTES,
    )
    try:
        manifest = _decode_strict_json(
            manifest_bytes.decode("utf-8"),
            label="backup manifest",
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise StorageIntegrityError("backup manifest is unreadable or malformed") from exc
    manifest_fields = {
        "format",
        "version",
        "operation_id",
        "plan_digest",
        "source",
        "payload",
    }
    if not isinstance(manifest, dict) or set(manifest) != manifest_fields:
        raise StorageIntegrityError("backup manifest fields are invalid")
    if manifest["format"] != LIFECYCLE_BACKUP_FORMAT.format_id:
        raise StorageIntegrityError("backup manifest format identity is invalid")
    require_supported_version(LIFECYCLE_BACKUP_FORMAT, manifest["version"])
    operation_id = manifest["operation_id"]
    plan_digest = manifest["plan_digest"]
    if not _is_sha256(operation_id) or not _is_sha256(plan_digest):
        raise StorageIntegrityError("backup operation identity is invalid")
    try:
        source_content = _content_from_backup_manifest(manifest["source"])
    except UnsupportedFormatError:
        raise
    except (LifecyclePlanError, TypeError, ValueError) as exc:
        raise StorageIntegrityError("backup source identity is invalid") from exc

    payload = manifest["payload"]
    if not isinstance(payload, dict) or set(payload) != {
        "entry",
        "files",
        "tree_fingerprint",
    }:
        raise StorageIntegrityError("backup payload manifest fields are invalid")
    adapter = _adapter_for_kind(source_content.kind)
    if payload["entry"] != adapter.payload_entry:
        raise StorageIntegrityError("backup payload entry does not match its storage kind")
    if payload["tree_fingerprint"] != source_content.fingerprint:
        raise StorageIntegrityError("backup payload fingerprint does not match its source")
    listed_files = payload["files"]
    if not isinstance(listed_files, list):
        raise StorageIntegrityError("backup payload file manifest must be an array")
    expected_files: Dict[str, tuple[int, str]] = {}
    casefold_names: set[str] = set()
    for item in listed_files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise StorageIntegrityError("backup payload file entry is invalid")
        relative_name = _validate_relative_payload_path(item["path"])
        folded = relative_name.casefold()
        if relative_name in expected_files or folded in casefold_names:
            raise StorageIntegrityError("backup payload file paths are duplicated")
        size = item["size"]
        digest = item["sha256"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise StorageIntegrityError("backup payload file size is invalid")
        if not _is_sha256(digest):
            raise StorageIntegrityError("backup payload file digest is invalid")
        expected_files[relative_name] = (size, digest)
        casefold_names.add(folded)
    if [item["path"] for item in listed_files] != sorted(expected_files):
        raise StorageIntegrityError("backup payload file manifest is not canonical")

    actual_entries = {
        relative_name.removeprefix("payload/"): entry
        for relative_name, entry in streamed_entries.items()
        if relative_name.startswith("payload/")
    }
    if set(actual_entries) != set(expected_files):
        raise StorageIntegrityError("backup payload files do not match the manifest")
    for relative_name, entry in actual_entries.items():
        size, digest = expected_files[relative_name]
        if entry.size != size or entry.sha256 != digest:
            raise StorageIntegrityError("backup payload file verification failed")
    if source_content.file_count != len(actual_entries):
        raise StorageIntegrityError("backup source file count does not match its payload")

    payload_target = adapter.restored_target(
        root.joinpath(*PurePosixPath(adapter.payload_entry).parts)
    )
    payload_assessment = LifecycleInspector().inspect(payload_target)
    if not _assessment_matches_content(payload_assessment, source_content):
        raise StorageIntegrityError("backup payload does not match its source identity")

    final_files, final_directories = _scan_directory_entries(
        root,
        label="backup lifecycle target",
    )
    if final_files != initial_files or final_directories != initial_directories:
        raise StorageIntegrityError("backup lifecycle target changed during inspection")

    assessment = LifecycleAssessment(
        target=target,
        status=LifecycleStatus.CURRENT,
        format_id=LIFECYCLE_BACKUP_FORMAT.format_id,
        detected_version=LIFECYCLE_BACKUP_FORMAT.current_version,
        current_version=LIFECYCLE_BACKUP_FORMAT.current_version,
        fingerprint=streamed.tree_fingerprint,
        file_count=streamed.file_count,
    )
    return _BackupBundle(
        assessment=assessment,
        content=source_content,
        operation_id=operation_id,
        plan_digest=plan_digest,
        snapshot=_PayloadSnapshot(
            content=source_content,
            source_paths={
                relative_name: str(
                    payload_root.joinpath(*PurePosixPath(relative_name).parts)
                )
                for relative_name in actual_entries
            },
            identities={
                relative_name: RegularFileIdentity(
                    size=entry.size,
                    sha256=entry.sha256,
                )
                for relative_name, entry in actual_entries.items()
            },
        ),
    )


def _paths_overlap(first_value: str, second_value: str) -> bool:
    first = _resolved_path(Path(first_value), label="lifecycle source")
    second = _resolved_path(Path(second_value), label="lifecycle destination")
    try:
        common = os.path.commonpath((first, second))
    except ValueError:
        return False
    return common == first or common == second


def _require_safe_destination(
    *,
    source: LifecycleTarget,
    destination: LifecycleTarget,
) -> LifecycleDirectoryIdentity:
    try:
        overlaps = _paths_overlap(source.path, destination.path)
    except StorageIntegrityError as exc:
        raise LifecyclePlanError("lifecycle source or destination path is unsafe") from exc
    if overlaps:
        raise LifecyclePlanError("lifecycle source and destination cannot overlap")
    parent = Path(destination.path).parent
    try:
        return _directory_identity(parent)
    except StorageIntegrityError as exc:
        raise LifecyclePlanError(
            "lifecycle destination parent must be an existing regular directory"
        ) from exc


def _require_destinations_do_not_overlap(
    first: LifecycleTarget,
    second: LifecycleTarget,
) -> None:
    try:
        overlaps = _paths_overlap(first.path, second.path)
    except StorageIntegrityError as exc:
        raise LifecyclePlanError("lifecycle destination paths are unsafe") from exc
    if overlaps:
        raise LifecyclePlanError("lifecycle destinations cannot overlap")


def _assert_plan_destination_topology(plan: LifecyclePlan) -> None:
    if plan.operation in {LifecycleOperation.ERASE, LifecycleOperation.REBUILD}:
        if plan.destination.target != plan.source.target:
            raise LifecyclePlanError("in-place lifecycle target identity is invalid")
        try:
            current_parent = _directory_identity(Path(plan.destination.target.path).parent)
        except StorageIntegrityError as exc:
            raise StaleLifecyclePlanError(
                "lifecycle source parent became unsafe"
            ) from exc
        if current_parent != plan.destination_parent:
            raise StaleLifecyclePlanError(
                "lifecycle source parent changed after planning"
            )
    else:
        try:
            current_parent = _require_safe_destination(
                source=plan.source.target,
                destination=plan.destination.target,
            )
        except LifecyclePlanError as exc:
            raise StaleLifecyclePlanError(
                "lifecycle source/destination topology became unsafe"
            ) from exc
        if current_parent != plan.destination_parent:
            raise StaleLifecyclePlanError(
                "lifecycle destination parent changed after planning"
            )
    if plan.operation in {
        LifecycleOperation.UPGRADE,
        LifecycleOperation.ERASE,
        LifecycleOperation.REBUILD,
    }:
        if plan.backup_destination is None or plan.backup_destination_parent is None:
            raise LifecyclePlanError("lifecycle plan backup topology is incomplete")
        try:
            current_backup_parent = _require_safe_destination(
                source=plan.source.target,
                destination=plan.backup_destination.target,
            )
            if plan.operation is LifecycleOperation.UPGRADE:
                _require_destinations_do_not_overlap(
                    plan.destination.target,
                    plan.backup_destination.target,
                )
        except LifecyclePlanError as exc:
            raise StaleLifecyclePlanError(
                "lifecycle backup destination topology became unsafe"
            ) from exc
        if current_backup_parent != plan.backup_destination_parent:
            raise StaleLifecyclePlanError(
                "lifecycle backup destination parent changed after planning"
            )


def _owner_document(plan: LifecyclePlan) -> bytes:
    return _canonical_json({"operation_id": plan.operation_id, "plan_digest": plan.plan_digest})


def _read_owner(path: Path) -> Dict[str, object]:
    try:
        document = _decode_strict_json(
            _read_stable_bytes(path).decode("utf-8"),
            label="lifecycle staging owner",
        )
    except (OSError, StorageIntegrityError, UnicodeDecodeError, ValueError) as exc:
        raise LifecycleConflictError("lifecycle staging ownership is unreadable") from exc
    if not isinstance(document, dict) or set(document) != {"operation_id", "plan_digest"}:
        raise LifecycleConflictError("lifecycle staging ownership is invalid")
    return document


def _remove_staging_path(path: Path) -> None:
    if not _lexists(path):
        return
    info = _lstat(path, label="lifecycle staging path")
    if _stat_is_link_or_reparse(info):
        raise LifecycleConflictError("refusing to remove a linked lifecycle staging path")
    try:
        if stat.S_ISDIR(info.st_mode):
            _scan_directory_entries(path, label="owned lifecycle staging tree")
            shutil.rmtree(path)
        elif stat.S_ISREG(info.st_mode):
            path.unlink()
        else:
            raise LifecycleConflictError("refusing to remove a non-regular staging path")
    except OSError as exc:
        raise LifecycleConflictError("could not clean an owned lifecycle staging path") from exc


def _staging_auxiliary_paths(
    plan: LifecyclePlan,
    staging: Path,
) -> tuple[Path, ...]:
    if (
        plan.operation is LifecycleOperation.IMPORT
        and plan.destination.target.kind is LifecycleTargetKind.SQLITE
    ):
        return (Path(f"{staging}.relationship_processing_locks"),)
    return ()


def _is_sqlite_relationship_processing_lock(relative_name: str) -> bool:
    path = PurePosixPath(relative_name)
    if len(path.parts) != 1 or not path.name.endswith(".lock"):
        return False
    digest = path.name.removesuffix(".lock")
    return len(digest) == 64 and all(
        character in "0123456789abcdef"
        for character in digest
    )


def _remove_legacy_sqlite_staging_locks(path: Path) -> None:
    try:
        files, directories = _scan_directory_entries(
            path,
            label="legacy SQLite staging relationship locks",
        )
    except StorageIntegrityError as exc:
        raise LifecycleConflictError(
            "legacy SQLite staging locks are not a private runtime directory"
        ) from exc
    if directories or any(
        not _is_sqlite_relationship_processing_lock(name)
        or signature[3] != 1
        or signature[5] != 1
        for name, signature in files.items()
    ):
        raise LifecycleConflictError(
            "legacy SQLite staging locks contain non-runtime data"
        )
    try:
        if any(
            _read_stable_bytes(path / name) != b"\0"
            for name in files
        ):
            raise LifecycleConflictError(
                "legacy SQLite staging locks contain non-runtime data"
            )
    except StorageIntegrityError as exc:
        raise LifecycleConflictError(
            "legacy SQLite staging locks changed during inspection"
        ) from exc
    try:
        _remove_staging_path(path)
    except StorageIntegrityError as exc:
        raise LifecycleConflictError(
            "legacy SQLite staging locks changed during cleanup"
        ) from exc
    _fsync_directory(path.parent)


def _prepare_staging(plan: LifecyclePlan, staging: Path, owner: Path) -> None:
    expected_owner = {
        "operation_id": plan.operation_id,
        "plan_digest": plan.plan_digest,
    }
    auxiliary_paths = _staging_auxiliary_paths(plan, staging)
    if not _lexists(staging) and not _lexists(owner):
        for path in auxiliary_paths:
            if not _lexists(path):
                continue
            if not _lexists(Path(plan.destination.target.path)):
                raise LifecycleConflictError(
                    "unowned lifecycle staging data remains"
                )
            _remove_legacy_sqlite_staging_locks(path)
    if (
        _lexists(staging)
        or _lexists(owner)
        or any(_lexists(path) for path in auxiliary_paths)
    ):
        if not _lexists(owner):
            raise LifecycleConflictError("lifecycle staging path belongs to another operation")
        try:
            _require_regular_file(owner, label="lifecycle staging ownership")
        except StorageIntegrityError as exc:
            raise LifecycleConflictError(
                "lifecycle staging path belongs to another operation"
            ) from exc
        if _read_owner(owner) != expected_owner:
            raise LifecycleConflictError("lifecycle staging path belongs to another operation")
        _remove_staging_path(staging)
        for path in auxiliary_paths:
            _remove_staging_path(path)
        try:
            owner.unlink()
        except OSError as exc:
            raise LifecycleConflictError("could not clear lifecycle staging ownership") from exc
    _write_durable_file(owner, _owner_document(plan))
    _fsync_directory(owner.parent)


def _cleanup_staging(plan: LifecyclePlan, staging: Path, owner: Path) -> None:
    auxiliary_paths = _staging_auxiliary_paths(plan, staging)
    if _lexists(owner):
        try:
            _require_regular_file(owner, label="lifecycle staging ownership")
        except StorageIntegrityError as exc:
            raise LifecycleConflictError("lifecycle staging ownership changed") from exc
        expected = {"operation_id": plan.operation_id, "plan_digest": plan.plan_digest}
        if _read_owner(owner) != expected:
            raise LifecycleConflictError("lifecycle staging ownership changed")
        _remove_staging_path(staging)
        for path in auxiliary_paths:
            _remove_staging_path(path)
        try:
            owner.unlink()
        except OSError as exc:
            raise LifecycleConflictError("could not clear lifecycle staging ownership") from exc
        _fsync_directory(owner.parent)
    elif _lexists(staging) or any(_lexists(path) for path in auxiliary_paths):
        raise LifecycleConflictError("unowned lifecycle staging data remains")


@contextmanager
def _destination_lock(destination: Path):
    lock_name = f".{destination.name}.erii-lifecycle.lock"
    lock_path = destination.parent / lock_name
    descriptor = -1
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        opened = os.fstat(descriptor)
        linked = os.lstat(lock_path)
        if (
            _stat_is_link_or_reparse(opened)
            or _stat_is_link_or_reparse(linked)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _stat_signature(opened) != _stat_signature(linked)
        ):
            raise LifecycleConflictError("lifecycle destination lock is not a private file")
        handle = os.fdopen(descriptor, "r+b", closefd=True)
        descriptor = -1
    except LifecycleConflictError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise LifecycleConflictError("could not open lifecycle destination lock") from exc
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
            _fsync_directory(lock_path.parent)
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise LifecycleConflictError("lifecycle destination is busy") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise LifecycleConflictError("lifecycle destination is busy") from exc
        locked = True
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
        # Keep the non-sensitive lock file. Removing a shared lock pathname after
        # unlocking allows a third process to create a new inode while another
        # waiter still holds the old one, which would split the exclusion domain.


@contextmanager
def _destination_locks(*destinations: Path):
    unique = {
        os.path.normcase(os.path.abspath(str(destination))): destination
        for destination in destinations
    }
    with ExitStack() as stack:
        for key in sorted(unique):
            stack.enter_context(_destination_lock(unique[key]))
        yield


def _stage_paths(destination: Path, plan: LifecyclePlan) -> tuple[Path, Path]:
    stem = f".{destination.name}.{plan.operation_id[:12]}.{plan.operation.value}.tmp"
    staging = destination.parent / stem
    return staging, destination.parent / f"{stem}.owner"


def _recovery_path(destination: Path, plan: LifecyclePlan) -> Path:
    return destination.parent / (
        f".{destination.name}.{plan.operation_id[:12]}.{plan.operation.value}.recovery"
    )


def _quiesce_sqlite_staging(path: Path) -> None:
    """Checkpoints a private SQLite artifact and removes only empty sidecars."""
    try:
        with closing(sqlite3.connect(str(path))) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            connection.commit()
    except sqlite3.Error as exc:
        raise StorageIntegrityError("staged SQLite database could not be checkpointed") from exc
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        if not _lexists(sidecar):
            continue
        info = _require_regular_file(sidecar, label="staged SQLite sidecar")
        if suffix in {"-wal", "-journal"} and info.st_size:
            raise StorageIntegrityError("staged SQLite sidecar still contains pending data")
        try:
            sidecar.unlink()
        except OSError as exc:
            raise StorageWriteError("staged SQLite sidecar could not be removed") from exc
    _fsync_directory(path.parent)


def _with_lifecycle_backup_inventory(
    result: ErasureTransformResult,
) -> ErasureTransformResult:
    counts = result.inventory.to_dict()
    counts["unverified_external"]["lifecycle_backup"] = 1
    return ErasureTransformResult(
        storage_kind=result.storage_kind,
        selector=result.selector,
        affected_relationship_ids=result.affected_relationship_ids,
        rebuild_proofs=result.rebuild_proofs,
        inventory=ErasureInventory(counts=counts),
    )


def _same_assessment(
    expected: LifecycleAssessment,
    actual: LifecycleAssessment,
) -> bool:
    return expected == actual


def _require_complete_bundle(bundle: _BackupBundle) -> _BackupBundle:
    if (
        bundle.assessment.status is not LifecycleStatus.CURRENT
        or bundle.content is None
        or bundle.operation_id is None
        or bundle.plan_digest is None
        or bundle.snapshot is None
    ):
        raise LifecyclePlanError("restore requires a complete verified backup bundle")
    return bundle


def _matching_backup_for_plan(bundle: _BackupBundle, plan: LifecyclePlan) -> bool:
    expected_content = (
        LifecycleContentIdentity.from_assessment(plan.source)
        if plan.operation is LifecycleOperation.UPGRADE
        else plan.content
    )
    return (
        bundle.assessment.status is LifecycleStatus.CURRENT
        and bundle.operation_id == plan.operation_id
        and bundle.plan_digest == plan.plan_digest
        and bundle.content == expected_content
        and bundle.snapshot is not None
    )


def _report(
    plan: LifecyclePlan,
    *,
    outcome: LifecycleOutcome,
    artifact_fingerprint: str,
    file_count: int | None = None,
    details: ErasureTransformResult | MemoryPackStagingImportReport | None = None,
) -> LifecycleReport:
    return LifecycleReport(
        operation_id=plan.operation_id,
        plan_digest=plan.plan_digest,
        operation=plan.operation,
        outcome=outcome,
        content_fingerprint=plan.content.fingerprint,
        artifact_fingerprint=artifact_fingerprint,
        file_count=plan.content.file_count if file_count is None else file_count,
        details=details,
    )


def _published_target_recovery_status() -> str:
    # Once a target name is visible, another host may already have opened or
    # extended it. Automatic rollback could therefore destroy newer host data.
    return "published_target_preserved_manual_cleanup_required"


class DataLifecycleCoordinator:
    """Deep lifecycle Module for inspection, durable planning, and execution."""

    def __init__(self) -> None:
        self._inspector = LifecycleInspector()

    def inspect(self, target: LifecycleTarget) -> LifecycleAssessment:
        """Inspects a live target or backup bundle without writing to it."""
        assessment = self._inspector.inspect(target)
        _validate_assessment(assessment)
        return assessment

    def plan(self, request: LifecycleRequest) -> LifecyclePlan:
        """Freezes a zero-write, strictly serializable lifecycle plan."""
        if isinstance(request, BackupRequest):
            return self._plan_backup(request)
        if isinstance(request, RestoreRequest):
            return self._plan_restore(request)
        if isinstance(request, UpgradeRequest):
            return self._plan_upgrade(request)
        if isinstance(request, EraseRequest):
            return self._plan_erasure(request, LifecycleOperation.ERASE)
        if isinstance(request, RebuildRequest):
            return self._plan_erasure(request, LifecycleOperation.REBUILD)
        if isinstance(request, MemoryPackImportRequest):
            return self._plan_memory_pack_import(request)
        raise TypeError("plan() requires a supported lifecycle request")

    def execute(self, plan: LifecyclePlan) -> LifecycleReport:
        """Executes and terminally verifies an immutable lifecycle plan."""
        if not isinstance(plan, LifecyclePlan):
            raise TypeError("execute() requires a LifecyclePlan")
        validated = LifecyclePlan.from_json(plan.to_json())
        if validated != plan:
            raise LifecyclePlanError("lifecycle plan is not canonical")
        if plan.operation is LifecycleOperation.BACKUP:
            return self._execute_backup(plan)
        if plan.operation is LifecycleOperation.RESTORE:
            return self._execute_restore(plan)
        if plan.operation is LifecycleOperation.UPGRADE:
            return self._execute_upgrade(plan)
        if plan.operation in {LifecycleOperation.ERASE, LifecycleOperation.REBUILD}:
            return self._execute_erasure_or_rebuild(plan)
        if plan.operation is LifecycleOperation.IMPORT:
            return self._execute_memory_pack_import(plan)
        raise LifecyclePlanError("lifecycle plan operation is unsupported")

    def _plan_backup(self, request: BackupRequest) -> LifecyclePlan:
        _validate_assessment(request.source)
        current_source = self.inspect(request.source.target)
        if not _same_assessment(request.source, current_source):
            raise StaleLifecyclePlanError("backup source changed before planning")
        if current_source.status is LifecycleStatus.MISSING:
            raise LifecyclePlanError("missing lifecycle data cannot be backed up")
        destination = self.inspect(request.destination)
        if destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError("backup destination must not already exist")
        destination_parent = _require_safe_destination(
            source=current_source.target,
            destination=destination.target,
        )
        return _make_plan(
            operation=LifecycleOperation.BACKUP,
            source=current_source,
            destination=destination,
            destination_parent=destination_parent,
            content=LifecycleContentIdentity.from_assessment(current_source),
            strategy_id=_BACKUP_STRATEGY_ID,
        )

    def _plan_restore(self, request: RestoreRequest) -> LifecyclePlan:
        _validate_assessment(request.backup)
        current_bundle = _require_complete_bundle(_read_backup_bundle(request.backup.target))
        if not _same_assessment(request.backup, current_bundle.assessment):
            raise StaleLifecyclePlanError("backup bundle changed before planning")
        destination = self.inspect(request.destination)
        if destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError(
                "this restore slice only publishes to a missing destination"
            )
        assert current_bundle.content is not None
        if destination.target.kind is not current_bundle.content.kind:
            raise LifecyclePlanError("restore destination kind does not match backup content")
        destination_parent = _require_safe_destination(
            source=current_bundle.assessment.target,
            destination=destination.target,
        )
        return _make_plan(
            operation=LifecycleOperation.RESTORE,
            source=current_bundle.assessment,
            destination=destination,
            destination_parent=destination_parent,
            content=current_bundle.content,
            strategy_id=_RESTORE_STRATEGY_ID,
        )

    def _plan_upgrade(self, request: UpgradeRequest) -> LifecyclePlan:
        _validate_assessment(request.source)
        current_source = self.inspect(request.source.target)
        if not _same_assessment(request.source, current_source):
            raise StaleLifecyclePlanError("upgrade source changed before planning")
        if current_source.status is not LifecycleStatus.MIGRATION_REQUIRED:
            raise LifecyclePlanError("upgrade source does not require migration")
        strategy_id = _upgrade_strategy_id(current_source)

        destination = self.inspect(request.destination)
        if destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError("upgrade destination must not already exist")
        backup_destination = self.inspect(request.backup_destination)
        if backup_destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError("upgrade backup destination must not already exist")
        destination_parent = _require_safe_destination(
            source=current_source.target,
            destination=destination.target,
        )
        backup_destination_parent = _require_safe_destination(
            source=current_source.target,
            destination=backup_destination.target,
        )
        _require_destinations_do_not_overlap(
            destination.target,
            backup_destination.target,
        )

        adapter = _adapter_for_kind(current_source.target.kind)
        source_snapshot = adapter.capture(current_source)
        final_source = self.inspect(current_source.target)
        if not _same_assessment(current_source, final_source):
            raise StaleLifecyclePlanError("upgrade source changed during planning")
        upgraded = _upgrade_snapshot(strategy_id, source_snapshot)
        return _make_plan(
            operation=LifecycleOperation.UPGRADE,
            source=current_source,
            destination=destination,
            destination_parent=destination_parent,
            content=upgraded.content,
            strategy_id=strategy_id,
            backup_destination=backup_destination,
            backup_destination_parent=backup_destination_parent,
        )

    def _plan_erasure(
        self,
        request: EraseRequest | RebuildRequest,
        operation: LifecycleOperation,
    ) -> LifecyclePlan:
        _validate_assessment(request.source)
        current_source = self.inspect(request.source.target)
        if not _same_assessment(request.source, current_source):
            raise StaleLifecyclePlanError("lifecycle mutation source changed before planning")
        if current_source.status is not LifecycleStatus.CURRENT:
            raise LifecyclePlanError("erase and rebuild require current live storage")
        storage_kind = _erasure_storage_kind(current_source.target.kind)
        if (
            operation is LifecycleOperation.REBUILD
            and request.selector.scope is not ErasureScope.RELATIONSHIP
        ):
            raise LifecyclePlanError(
                "deterministic rebuild currently requires a relationship selector"
            )

        from erii.lifecycle_erasure import inspect_erasure_scope

        try:
            inspect_erasure_scope(
                current_source.target.path,
                storage_kind,
                request.selector,
            )
        except ErasureSelectionError as exc:
            raise LifecyclePlanError("erasure selector does not match live storage") from exc
        final_source = self.inspect(current_source.target)
        if not _same_assessment(current_source, final_source):
            raise StaleLifecyclePlanError("lifecycle mutation source changed during planning")

        backup_destination = self.inspect(request.backup_destination)
        if backup_destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError(
                "erase and rebuild backup destination must not already exist"
            )
        try:
            destination_parent = _directory_identity(
                Path(current_source.target.path).parent
            )
        except StorageIntegrityError as exc:
            raise LifecyclePlanError("lifecycle source parent is unsafe") from exc
        backup_destination_parent = _require_safe_destination(
            source=current_source.target,
            destination=backup_destination.target,
        )
        return _make_plan(
            operation=operation,
            source=current_source,
            destination=current_source,
            destination_parent=destination_parent,
            content=LifecycleContentIdentity.from_assessment(current_source),
            strategy_id=_erasure_strategy_id(operation, current_source.target.kind),
            backup_destination=backup_destination,
            backup_destination_parent=backup_destination_parent,
            selector=request.selector,
        )

    def _plan_memory_pack_import(
        self,
        request: MemoryPackImportRequest,
    ) -> LifecyclePlan:
        _validate_assessment(request.source)
        current_source = self.inspect(request.source.target)
        if not _same_assessment(request.source, current_source):
            raise StaleLifecyclePlanError("MemoryPack source changed before planning")
        if current_source.status not in {
            LifecycleStatus.CURRENT,
            LifecycleStatus.MIGRATION_REQUIRED,
        }:
            raise LifecyclePlanError("MemoryPack import source must be readable")
        destination = self.inspect(request.destination)
        if destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError("MemoryPack import destination must not exist")
        destination_parent = _require_safe_destination(
            source=current_source.target,
            destination=destination.target,
        )
        snapshot = _materialize_snapshot(
            _MemoryPackLifecycleAdapter().capture(current_source)
        )
        assert snapshot.files is not None
        try:
            pack = MemoryPack.from_json(snapshot.files["memory-pack.erii"].decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            raise StorageIntegrityError("MemoryPack import source is malformed") from exc
        _validate_memory_pack_semantic_graph(pack)
        final_source = self.inspect(current_source.target)
        if not _same_assessment(current_source, final_source):
            raise StaleLifecyclePlanError("MemoryPack source changed during planning")
        options = MemoryPackImportOptions(
            target_agent_id=request.target_agent_id,
            target_user_id=request.target_user_id,
        )
        return _make_plan(
            operation=LifecycleOperation.IMPORT,
            source=current_source,
            destination=destination,
            destination_parent=destination_parent,
            content=LifecycleContentIdentity.from_assessment(current_source),
            strategy_id=_import_strategy_id(destination.target.kind),
            selector=options,
        )

    def _ensure_verified_prechange_backup(
        self,
        plan: LifecyclePlan,
        *,
        backup_staging: Path,
        backup_owner: Path,
    ) -> _BackupBundle:
        """Returns the plan-bound backup, publishing it only from the exact source."""
        if plan.backup_destination is None:
            raise LifecyclePlanError("lifecycle mutation has no backup destination")
        backup_destination = Path(plan.backup_destination.target.path)
        if _lexists(backup_destination):
            try:
                bundle = _require_complete_bundle(
                    _read_backup_bundle(plan.backup_destination.target)
                )
            except (
                LifecyclePlanError,
                StorageIntegrityError,
                UnsupportedFormatError,
            ) as exc:
                raise LifecycleConflictError(
                    "lifecycle backup destination contains a damaged artifact"
                ) from exc
            if not _matching_backup_for_plan(bundle, plan):
                raise LifecycleConflictError(
                    "lifecycle backup destination belongs to a different plan"
                )
            _cleanup_staging(plan, backup_staging, backup_owner)
            return bundle

        current_source = self.inspect(plan.source.target)
        if not _same_assessment(plan.source, current_source):
            raise StaleLifecyclePlanError(
                "lifecycle source changed before its required backup"
            )
        adapter = _adapter_for_kind(plan.source.target.kind)
        source_snapshot = adapter.capture(current_source)
        final_source = self.inspect(plan.source.target)
        if not _same_assessment(plan.source, final_source):
            raise StaleLifecyclePlanError(
                "lifecycle source changed during required backup capture"
            )

        _prepare_staging(plan, backup_staging, backup_owner)
        backup_published = False
        try:
            _create_private_directory(backup_staging)
            payload_root = backup_staging / "payload"
            _create_private_directory(payload_root)
            _write_snapshot_files(payload_root, source_snapshot)
            _fsync_tree_directories(payload_root)
            _write_durable_file(
                backup_staging / LIFECYCLE_BACKUP_MANIFEST,
                _canonical_json(_backup_manifest(plan, source_snapshot)),
            )
            _fsync_directory(backup_staging)
            staged = _require_complete_bundle(
                _read_backup_bundle(
                    LifecycleTarget(
                        LifecycleTargetKind.BACKUP,
                        str(backup_staging),
                    )
                )
            )
            if not _matching_backup_for_plan(staged, plan):
                raise LifecycleVerificationError(
                    "staged lifecycle backup does not match its plan",
                    recovery_status="source_unchanged",
                )
            _assert_plan_destination_topology(plan)
            try:
                _rename_no_replace(backup_staging, backup_destination)
            except LifecycleConflictError:
                raise
            except OSError as exc:
                raise StorageWriteError("lifecycle backup publication failed") from exc
            backup_published = True
            _fsync_directory(backup_destination.parent)
            bundle = _require_complete_bundle(
                _read_backup_bundle(plan.backup_destination.target)
            )
            if not _matching_backup_for_plan(bundle, plan):
                raise LifecycleVerificationError(
                    "published lifecycle backup does not match its plan",
                    recovery_status=_published_target_recovery_status(),
                )
            _cleanup_staging(plan, backup_staging, backup_owner)
            return bundle
        except Exception:
            if not backup_published:
                _cleanup_staging(plan, backup_staging, backup_owner)
            raise

    def _execute_backup(self, plan: LifecyclePlan) -> LifecycleReport:
        destination = Path(plan.destination.target.path)
        staging, owner = _stage_paths(destination, plan)
        _assert_plan_destination_topology(plan)
        with _destination_lock(destination):
            _assert_plan_destination_topology(plan)
            if _lexists(destination):
                try:
                    existing = _read_backup_bundle(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleConflictError(
                        "backup destination contains a different or damaged artifact"
                    ) from exc
                if not _matching_backup_for_plan(existing, plan):
                    raise LifecycleConflictError(
                        "backup destination belongs to a different lifecycle plan"
                    )
                _cleanup_staging(plan, staging, owner)
                assert existing.assessment.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.ALREADY_COMPLETE,
                    artifact_fingerprint=existing.assessment.fingerprint,
                )

            current_source = self.inspect(plan.source.target)
            if not _same_assessment(plan.source, current_source):
                raise StaleLifecyclePlanError("backup source changed after planning")
            adapter = _adapter_for_kind(plan.content.kind)
            snapshot = adapter.capture(current_source)
            if snapshot.content != plan.content:
                raise StaleLifecyclePlanError("captured backup no longer matches its plan")
            final_source = self.inspect(plan.source.target)
            if not _same_assessment(plan.source, final_source):
                raise StaleLifecyclePlanError("backup source changed during capture")

            _prepare_staging(plan, staging, owner)
            published = False
            try:
                _create_private_directory(staging)
                payload_root = staging / "payload"
                _create_private_directory(payload_root)
                _write_snapshot_files(payload_root, snapshot)
                _fsync_tree_directories(payload_root)
                manifest_bytes = _canonical_json(_backup_manifest(plan, snapshot))
                _write_durable_file(staging / LIFECYCLE_BACKUP_MANIFEST, manifest_bytes)
                _fsync_directory(staging)
                try:
                    staged_bundle = _read_backup_bundle(
                        LifecycleTarget(LifecycleTargetKind.BACKUP, str(staging))
                    )
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleVerificationError(
                        "staged lifecycle backup failed verification",
                        recovery_status="source_unchanged",
                    ) from exc
                if not _matching_backup_for_plan(staged_bundle, plan):
                    raise LifecycleVerificationError(
                        "staged lifecycle backup does not match its plan",
                        recovery_status="source_unchanged",
                    )
                _assert_plan_destination_topology(plan)
                try:
                    _rename_no_replace(staging, destination)
                except LifecycleConflictError:
                    raise
                except OSError as exc:
                    raise StorageWriteError("backup publication failed") from exc
                published = True
                _fsync_directory(destination.parent)
                try:
                    final_bundle = _read_backup_bundle(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleVerificationError(
                        "published lifecycle backup failed verification",
                        recovery_status=_published_target_recovery_status(),
                    ) from exc
                if not _matching_backup_for_plan(final_bundle, plan):
                    raise LifecycleVerificationError(
                        "published lifecycle backup does not match its plan",
                        recovery_status=_published_target_recovery_status(),
                    )
                _cleanup_staging(plan, staging, owner)
                assert final_bundle.assessment.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.APPLIED,
                    artifact_fingerprint=final_bundle.assessment.fingerprint,
                )
            except Exception:
                if not published:
                    _cleanup_staging(plan, staging, owner)
                raise

    def _execute_restore(self, plan: LifecyclePlan) -> LifecycleReport:
        destination = Path(plan.destination.target.path)
        staging, owner = _stage_paths(destination, plan)
        _assert_plan_destination_topology(plan)
        with _destination_lock(destination):
            _assert_plan_destination_topology(plan)
            current_bundle = _require_complete_bundle(_read_backup_bundle(plan.source.target))
            if not _same_assessment(plan.source, current_bundle.assessment):
                raise StaleLifecyclePlanError("backup bundle changed after planning")
            if current_bundle.content != plan.content or current_bundle.snapshot is None:
                raise StaleLifecyclePlanError("backup content no longer matches its restore plan")
            current_destination = self.inspect(plan.destination.target)
            if _assessment_matches_content(current_destination, plan.content):
                _cleanup_staging(plan, staging, owner)
                assert current_bundle.assessment.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.ALREADY_COMPLETE,
                    artifact_fingerprint=current_bundle.assessment.fingerprint,
                )
            if current_destination.status is not LifecycleStatus.MISSING:
                raise StaleLifecyclePlanError("restore destination changed after planning")

            adapter = _adapter_for_kind(plan.content.kind)
            _prepare_staging(plan, staging, owner)
            published = False
            try:
                adapter.write_restored(current_bundle.snapshot, staging)
                if staging.is_dir():
                    _fsync_tree_directories(staging)
                _fsync_directory(staging.parent)
                staged_assessment = self.inspect(adapter.restored_target(staging))
                if not _assessment_matches_content(staged_assessment, plan.content):
                    raise LifecycleVerificationError(
                        "staged restore does not match the backup content",
                        recovery_status="target_missing",
                    )
                _assert_plan_destination_topology(plan)
                try:
                    _rename_no_replace(staging, destination)
                except LifecycleConflictError:
                    raise
                except OSError as exc:
                    raise StorageWriteError("restore publication failed") from exc
                published = True
                _fsync_directory(destination.parent)
                try:
                    final_assessment = self.inspect(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleVerificationError(
                        "published restore failed verification",
                        recovery_status=_published_target_recovery_status(),
                    ) from exc
                if not _assessment_matches_content(final_assessment, plan.content):
                    raise LifecycleVerificationError(
                        "published restore does not match the backup content",
                        recovery_status=_published_target_recovery_status(),
                    )
                _cleanup_staging(plan, staging, owner)
                assert current_bundle.assessment.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.APPLIED,
                    artifact_fingerprint=current_bundle.assessment.fingerprint,
                )
            except Exception:
                if not published:
                    _cleanup_staging(plan, staging, owner)
                raise

    def _execute_upgrade(self, plan: LifecyclePlan) -> LifecycleReport:
        if plan.backup_destination is None or plan.backup_destination_parent is None:
            raise LifecyclePlanError("upgrade plan backup destination is incomplete")

        destination = Path(plan.destination.target.path)
        backup_destination = Path(plan.backup_destination.target.path)
        staging, owner = _stage_paths(destination, plan)
        backup_staging, backup_owner = _stage_paths(backup_destination, plan)
        _assert_plan_destination_topology(plan)

        with _destination_locks(backup_destination, destination):
            _assert_plan_destination_topology(plan)
            current_destination = self.inspect(plan.destination.target)
            if current_destination.status is not LifecycleStatus.MISSING:
                try:
                    existing_backup = _require_complete_bundle(
                        _read_backup_bundle(plan.backup_destination.target)
                    )
                except (
                    LifecyclePlanError,
                    StorageIntegrityError,
                    UnsupportedFormatError,
                ) as exc:
                    raise LifecycleConflictError(
                        "completed upgrade target has no matching verified backup"
                    ) from exc
                if not _assessment_matches_content(current_destination, plan.content) or not (
                    _matching_backup_for_plan(existing_backup, plan)
                ):
                    raise LifecycleConflictError(
                        "upgrade destination contains a different or damaged artifact"
                    )
                _cleanup_staging(plan, backup_staging, backup_owner)
                _cleanup_staging(plan, staging, owner)
                assert current_destination.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.ALREADY_COMPLETE,
                    artifact_fingerprint=current_destination.fingerprint,
                )

            current_source = self.inspect(plan.source.target)
            if not _same_assessment(plan.source, current_source):
                raise StaleLifecyclePlanError("upgrade source changed after planning")
            adapter = _adapter_for_kind(plan.source.target.kind)

            if _lexists(backup_destination):
                try:
                    backup_bundle = _require_complete_bundle(
                        _read_backup_bundle(plan.backup_destination.target)
                    )
                except (
                    LifecyclePlanError,
                    StorageIntegrityError,
                    UnsupportedFormatError,
                ) as exc:
                    raise LifecycleConflictError(
                        "upgrade backup destination contains a damaged artifact"
                    ) from exc
                if not _matching_backup_for_plan(backup_bundle, plan):
                    raise LifecycleConflictError(
                        "upgrade backup destination belongs to a different plan"
                    )
                _cleanup_staging(plan, backup_staging, backup_owner)
            else:
                source_snapshot = adapter.capture(current_source)
                final_source = self.inspect(plan.source.target)
                if not _same_assessment(plan.source, final_source):
                    raise StaleLifecyclePlanError("upgrade source changed during backup capture")

                _prepare_staging(plan, backup_staging, backup_owner)
                backup_published = False
                try:
                    _create_private_directory(backup_staging)
                    payload_root = backup_staging / "payload"
                    _create_private_directory(payload_root)
                    _write_snapshot_files(payload_root, source_snapshot)
                    _fsync_tree_directories(payload_root)
                    manifest_bytes = _canonical_json(_backup_manifest(plan, source_snapshot))
                    _write_durable_file(
                        backup_staging / LIFECYCLE_BACKUP_MANIFEST,
                        manifest_bytes,
                    )
                    _fsync_directory(backup_staging)
                    try:
                        staged_backup = _read_backup_bundle(
                            LifecycleTarget(
                                LifecycleTargetKind.BACKUP,
                                str(backup_staging),
                            )
                        )
                    except (StorageIntegrityError, UnsupportedFormatError) as exc:
                        raise LifecycleVerificationError(
                            "staged upgrade backup failed verification",
                            recovery_status="source_unchanged",
                        ) from exc
                    if not _matching_backup_for_plan(staged_backup, plan):
                        raise LifecycleVerificationError(
                            "staged upgrade backup does not match its plan",
                            recovery_status="source_unchanged",
                        )
                    _assert_plan_destination_topology(plan)
                    try:
                        _rename_no_replace(backup_staging, backup_destination)
                    except LifecycleConflictError:
                        raise
                    except OSError as exc:
                        raise StorageWriteError("upgrade backup publication failed") from exc
                    backup_published = True
                    _fsync_directory(backup_destination.parent)
                    try:
                        backup_bundle = _read_backup_bundle(plan.backup_destination.target)
                    except (StorageIntegrityError, UnsupportedFormatError) as exc:
                        raise LifecycleVerificationError(
                            "published upgrade backup failed verification",
                            recovery_status=_published_target_recovery_status(),
                        ) from exc
                    if not _matching_backup_for_plan(backup_bundle, plan):
                        raise LifecycleVerificationError(
                            "published upgrade backup does not match its plan",
                            recovery_status=_published_target_recovery_status(),
                        )
                    _cleanup_staging(plan, backup_staging, backup_owner)
                except Exception:
                    if not backup_published:
                        _cleanup_staging(plan, backup_staging, backup_owner)
                    raise

            if backup_bundle.snapshot is None:
                raise LifecycleVerificationError(
                    "verified upgrade backup has no restorable payload",
                    recovery_status="verified_backup_preserved_target_missing",
                )
            current_source = self.inspect(plan.source.target)
            if not _same_assessment(plan.source, current_source):
                raise StaleLifecyclePlanError(
                    "upgrade source changed after its backup was verified"
                )
            upgraded = _upgrade_snapshot(plan.strategy_id, backup_bundle.snapshot)
            if upgraded.content != plan.content:
                raise StaleLifecyclePlanError(
                    "upgrade result no longer matches its planned identity"
                )

            _prepare_staging(plan, staging, owner)
            published = False
            try:
                adapter.write_restored(upgraded, staging)
                if staging.is_dir():
                    _fsync_tree_directories(staging)
                _fsync_directory(staging.parent)
                staged_assessment = self.inspect(adapter.restored_target(staging))
                if not _assessment_matches_content(staged_assessment, plan.content):
                    raise LifecycleVerificationError(
                        "staged upgrade does not match its planned result",
                        recovery_status="verified_backup_preserved_target_missing",
                    )
                final_source = self.inspect(plan.source.target)
                if not _same_assessment(plan.source, final_source):
                    raise StaleLifecyclePlanError(
                        "upgrade source changed before target publication"
                    )
                current_backup = _require_complete_bundle(
                    _read_backup_bundle(plan.backup_destination.target)
                )
                if not _matching_backup_for_plan(current_backup, plan):
                    raise StaleLifecyclePlanError(
                        "upgrade backup changed before target publication"
                    )
                _assert_plan_destination_topology(plan)
                try:
                    _rename_no_replace(staging, destination)
                except LifecycleConflictError:
                    raise
                except OSError as exc:
                    raise StorageWriteError("upgrade publication failed") from exc
                published = True
                _fsync_directory(destination.parent)
                try:
                    final_assessment = self.inspect(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleVerificationError(
                        "published upgrade failed verification",
                        recovery_status=_published_target_recovery_status(),
                    ) from exc
                if not _assessment_matches_content(final_assessment, plan.content):
                    raise LifecycleVerificationError(
                        "published upgrade does not match its planned result",
                        recovery_status=_published_target_recovery_status(),
                    )
                _cleanup_staging(plan, staging, owner)
                assert final_assessment.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.APPLIED,
                    artifact_fingerprint=final_assessment.fingerprint,
                )
            except Exception:
                if not published:
                    _cleanup_staging(plan, staging, owner)
                raise

    def _execute_erasure_or_rebuild(self, plan: LifecyclePlan) -> LifecycleReport:
        if (
            plan.backup_destination is None
            or plan.backup_destination_parent is None
            or not isinstance(plan.selector, ErasureSelector)
        ):
            raise LifecyclePlanError("erase or rebuild plan is incomplete")

        destination = Path(plan.destination.target.path)
        backup_destination = Path(plan.backup_destination.target.path)
        staging, owner = _stage_paths(destination, plan)
        backup_staging, backup_owner = _stage_paths(backup_destination, plan)
        recovery = _recovery_path(destination, plan)
        adapter = _adapter_for_kind(plan.source.target.kind)
        storage_kind = _erasure_storage_kind(plan.source.target.kind)
        _assert_plan_destination_topology(plan)

        with _destination_locks(backup_destination, destination):
            _assert_plan_destination_topology(plan)

            # A crash between the two same-directory renames leaves the exact
            # original under the deterministic recovery name. Restore it before
            # re-entering the ordinary backup-first path.
            if _lexists(recovery) and not _lexists(destination):
                if not _lexists(backup_destination):
                    raise LifecycleConflictError(
                        "lifecycle recovery exists without its verified backup"
                    )
                recovery_assessment = self.inspect(
                    LifecycleTarget(plan.source.target.kind, str(recovery))
                )
                if not _assessment_matches_content(recovery_assessment, plan.content):
                    raise LifecycleConflictError(
                        "lifecycle recovery does not match the planned source"
                    )
                recovery_bundle = _require_complete_bundle(
                    _read_backup_bundle(plan.backup_destination.target)
                )
                if not _matching_backup_for_plan(recovery_bundle, plan):
                    raise LifecycleConflictError(
                        "lifecycle recovery backup does not match the plan"
                    )
                try:
                    _rename_no_replace(recovery, destination)
                except OSError as exc:
                    raise StorageWriteError(
                        "lifecycle recovery restoration failed"
                    ) from exc
                _fsync_directory(destination.parent)

            backup_preexisted = _lexists(backup_destination)
            backup_bundle = self._ensure_verified_prechange_backup(
                plan,
                backup_staging=backup_staging,
                backup_owner=backup_owner,
            )
            if backup_bundle.snapshot is None:
                raise LifecycleVerificationError(
                    "verified lifecycle backup has no restorable payload",
                    recovery_status="verified_backup_preserved",
                )

            _prepare_staging(plan, staging, owner)
            try:
                adapter.write_restored(backup_bundle.snapshot, staging)
                from erii.lifecycle_erasure import (
                    erase_staged_storage,
                    rebuild_staged_storage,
                )

                if plan.operation is LifecycleOperation.ERASE:
                    details = erase_staged_storage(
                        str(staging),
                        storage_kind,
                        plan.selector,
                    )
                else:
                    details = rebuild_staged_storage(
                        str(staging),
                        storage_kind,
                        plan.selector,
                    )
                details = _with_lifecycle_backup_inventory(details)
                if plan.source.target.kind is LifecycleTargetKind.SQLITE:
                    _quiesce_sqlite_staging(staging)
                elif staging.is_dir():
                    _fsync_tree_directories(staging)
                _fsync_directory(staging.parent)
                staged_assessment = self.inspect(adapter.restored_target(staging))
                if staged_assessment.status is not LifecycleStatus.CURRENT:
                    raise LifecycleVerificationError(
                        "staged lifecycle mutation is not current storage",
                        recovery_status="verified_backup_preserved_source_unchanged",
                    )
                staged_content = LifecycleContentIdentity.from_assessment(
                    staged_assessment
                )

                current = self.inspect(plan.destination.target)
                if _assessment_matches_content(current, staged_content) and backup_preexisted:
                    if _lexists(recovery):
                        recovery_assessment = self.inspect(
                            LifecycleTarget(plan.source.target.kind, str(recovery))
                        )
                        if not _assessment_matches_content(
                            recovery_assessment,
                            plan.content,
                        ):
                            raise LifecycleConflictError(
                                "lifecycle recovery contains unexpected data"
                            )
                        _remove_staging_path(recovery)
                        _fsync_directory(recovery.parent)
                    _cleanup_staging(plan, staging, owner)
                    assert current.fingerprint is not None
                    return _report(
                        plan,
                        outcome=LifecycleOutcome.ALREADY_COMPLETE,
                        artifact_fingerprint=current.fingerprint,
                        file_count=current.file_count,
                        details=details,
                    )
                if not _same_assessment(current, plan.source):
                    raise LifecycleConflictError(
                        "live lifecycle source changed after planning"
                    )
                if _lexists(recovery):
                    raise LifecycleConflictError(
                        "lifecycle recovery path is unexpectedly occupied"
                    )
                current_backup = _require_complete_bundle(
                    _read_backup_bundle(plan.backup_destination.target)
                )
                if not _matching_backup_for_plan(current_backup, plan):
                    raise StaleLifecyclePlanError(
                        "lifecycle backup changed before live publication"
                    )
                _assert_plan_destination_topology(plan)

                try:
                    _rename_no_replace(destination, recovery)
                    _fsync_directory(destination.parent)
                    try:
                        _rename_no_replace(staging, destination)
                    except Exception:
                        try:
                            _rename_no_replace(recovery, destination)
                            _fsync_directory(destination.parent)
                        except Exception as rollback_exc:
                            raise LifecycleVerificationError(
                                "lifecycle publication and automatic rollback failed",
                                recovery_status=(
                                    "verified_backup_and_recovery_preserved_"
                                    "live_target_missing"
                                ),
                            ) from rollback_exc
                        raise
                except LifecycleVerificationError:
                    raise
                except LifecycleConflictError:
                    raise
                except OSError as exc:
                    raise StorageWriteError(
                        "lifecycle live publication failed and original was restored"
                    ) from exc

                _fsync_directory(destination.parent)
                try:
                    final = self.inspect(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    final = None
                    verification_error: Exception | None = exc
                else:
                    verification_error = None
                if final is None or not _assessment_matches_content(final, staged_content):
                    try:
                        if _lexists(destination):
                            _rename_no_replace(destination, staging)
                        if _lexists(recovery):
                            _rename_no_replace(recovery, destination)
                        _fsync_directory(destination.parent)
                    except Exception as rollback_exc:
                        raise LifecycleVerificationError(
                            "published lifecycle mutation failed verification and rollback",
                            recovery_status=(
                                "verified_backup_preserved_manual_recovery_required"
                            ),
                        ) from rollback_exc
                    raise LifecycleVerificationError(
                        "published lifecycle mutation failed verification; original restored",
                        recovery_status="verified_backup_preserved_source_restored",
                    ) from verification_error

                _remove_staging_path(recovery)
                _fsync_directory(destination.parent)
                _cleanup_staging(plan, staging, owner)
                assert final.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.APPLIED,
                    artifact_fingerprint=final.fingerprint,
                    file_count=final.file_count,
                    details=details,
                )
            except Exception:
                # The publication path may already have moved staging into the
                # live name; cleanup is therefore ownership-aware and only runs
                # while the staging owner still exists.
                if _lexists(owner):
                    _cleanup_staging(plan, staging, owner)
                raise

    def _execute_memory_pack_import(self, plan: LifecyclePlan) -> LifecycleReport:
        if not isinstance(plan.selector, MemoryPackImportOptions):
            raise LifecyclePlanError("MemoryPack import plan options are missing")
        destination = Path(plan.destination.target.path)
        staging, owner = _stage_paths(destination, plan)
        _assert_plan_destination_topology(plan)

        with _destination_lock(destination):
            _assert_plan_destination_topology(plan)
            current_source = self.inspect(plan.source.target)
            if not _same_assessment(plan.source, current_source):
                raise StaleLifecyclePlanError("MemoryPack source changed after planning")
            source_snapshot = _materialize_snapshot(
                _MemoryPackLifecycleAdapter().capture(current_source)
            )
            assert source_snapshot.files is not None
            if source_snapshot.content != plan.content:
                raise StaleLifecyclePlanError(
                    "MemoryPack import source no longer matches its plan"
                )
            try:
                pack = MemoryPack.from_json(
                    source_snapshot.files["memory-pack.erii"].decode("utf-8")
                )
            except (UnicodeDecodeError, TypeError, ValueError) as exc:
                raise StorageIntegrityError("MemoryPack import source is malformed") from exc
            _validate_memory_pack_semantic_graph(pack)

            _prepare_staging(plan, staging, owner)
            published = False
            try:
                from erii.lifecycle_memory_pack_import import (
                    MemoryPackStagingImportRequest,
                    MemoryPackStagingImporter,
                )

                staging_adapter = (
                    MemoryPackStagingAdapter.FILE_STORAGE
                    if plan.destination.target.kind is LifecycleTargetKind.FILE_STORAGE
                    else MemoryPackStagingAdapter.SQLITE
                )
                importer = MemoryPackStagingImporter()
                details = importer.import_pack(
                    MemoryPackStagingImportRequest(
                        adapter=staging_adapter,
                        staging_path=str(staging),
                        pack=pack,
                        target_agent_id=plan.selector.target_agent_id,
                        target_user_id=plan.selector.target_user_id,
                        overwrite=False,
                    )
                )
                if plan.destination.target.kind is LifecycleTargetKind.FILE_STORAGE:
                    manifest = staging / FILE_STORAGE_MANIFEST
                    if _lexists(manifest):
                        raise StorageIntegrityError(
                            "fresh FileStorage import unexpectedly created a format manifest"
                        )
                    _write_durable_file(
                        manifest,
                        _canonical_json(
                            {
                                "format": FILE_STORAGE_FORMAT.format_id,
                                "version": int(FILE_STORAGE_FORMAT.current_version),
                            }
                        ),
                    )
                    _fsync_tree_directories(staging)
                else:
                    _quiesce_sqlite_staging(staging)
                _fsync_directory(staging.parent)
                staged_assessment = self.inspect(
                    LifecycleTarget(plan.destination.target.kind, str(staging))
                )
                if staged_assessment.status is not LifecycleStatus.CURRENT:
                    raise LifecycleVerificationError(
                        "staged MemoryPack import is not current storage",
                        recovery_status="source_pack_preserved_target_missing",
                    )
                staged_content = LifecycleContentIdentity.from_assessment(
                    staged_assessment
                )

                current_destination = self.inspect(plan.destination.target)
                if current_destination.status is not LifecycleStatus.MISSING:
                    if current_destination.status is LifecycleStatus.CURRENT:
                        try:
                            existing_details = importer.inspect_target(
                                adapter=staging_adapter,
                                staging_path=plan.destination.target.path,
                                agent_id=details.agent_id,
                                user_id=details.user_id,
                            )
                        except Exception as exc:
                            raise LifecycleConflictError(
                                "MemoryPack import destination contains unreadable data"
                            ) from exc
                        import_matches = (
                            existing_details.to_dict() == details.to_dict()
                        )
                        if not import_matches:
                            import_matches = (
                                importer.target_matches_import_with_legacy_compatibility(
                                    adapter=staging_adapter,
                                    existing_path=plan.destination.target.path,
                                    desired_path=str(staging),
                                    agent_id=details.agent_id,
                                    user_id=details.user_id,
                                )
                            )
                        if import_matches:
                            _cleanup_staging(plan, staging, owner)
                            assert current_destination.fingerprint is not None
                            return _report(
                                plan,
                                outcome=LifecycleOutcome.ALREADY_COMPLETE,
                                artifact_fingerprint=current_destination.fingerprint,
                                file_count=current_destination.file_count,
                                details=existing_details,
                            )
                    raise LifecycleConflictError(
                        "MemoryPack import destination contains different data"
                    )
                final_source = self.inspect(plan.source.target)
                if not _same_assessment(plan.source, final_source):
                    raise StaleLifecyclePlanError(
                        "MemoryPack source changed before target publication"
                    )
                _assert_plan_destination_topology(plan)
                try:
                    _rename_no_replace(staging, destination)
                except LifecycleConflictError:
                    raise
                except OSError as exc:
                    raise StorageWriteError("MemoryPack import publication failed") from exc
                published = True
                _fsync_directory(destination.parent)
                try:
                    final = self.inspect(plan.destination.target)
                except (StorageIntegrityError, UnsupportedFormatError) as exc:
                    raise LifecycleVerificationError(
                        "published MemoryPack import failed verification",
                        recovery_status=_published_target_recovery_status(),
                    ) from exc
                if not _assessment_matches_content(final, staged_content):
                    raise LifecycleVerificationError(
                        "published MemoryPack import does not match staging",
                        recovery_status=_published_target_recovery_status(),
                    )
                _cleanup_staging(plan, staging, owner)
                assert final.fingerprint is not None
                return _report(
                    plan,
                    outcome=LifecycleOutcome.APPLIED,
                    artifact_fingerprint=final.fingerprint,
                    file_count=final.file_count,
                    details=details,
                )
            except Exception:
                if not published and _lexists(owner):
                    _cleanup_staging(plan, staging, owner)
                raise


__all__ = [
    "BackupRequest",
    "DataLifecycleCoordinator",
    "EraseRequest",
    "ErasureInventory",
    "ErasureScope",
    "ErasureSelector",
    "ErasureTransformResult",
    "FILE_STORAGE_MANIFEST",
    "LIFECYCLE_BACKUP_MANIFEST",
    "LIFECYCLE_PLAN_CONTRACT_VERSION",
    "MAX_LIFECYCLE_BACKUP_MANIFEST_BYTES",
    "MAX_LIFECYCLE_MEMORY_PACK_BYTES",
    "MAX_LIFECYCLE_TRANSFORM_BYTES",
    "LifecycleAssessment",
    "LifecycleContentIdentity",
    "LifecycleDirectoryIdentity",
    "LifecycleInspector",
    "LifecycleOperation",
    "LifecycleOutcome",
    "LifecyclePlan",
    "LifecyclePlanSelector",
    "LifecycleReport",
    "LifecycleRequest",
    "LifecycleStatus",
    "LifecycleTarget",
    "LifecycleTargetKind",
    "MemoryPackImportOptions",
    "MemoryPackImportRequest",
    "MemoryPackStagingImportReport",
    "RebuildRequest",
    "RelationshipRebuildProof",
    "RestoreRequest",
    "UpgradeRequest",
]
