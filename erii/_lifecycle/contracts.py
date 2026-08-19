"""Authoritative contracts for the data lifecycle interface."""

from dataclasses import dataclass
from enum import Enum
import os
from typing import Dict, TypeAlias

from erii.compatibility import require_supported_version
from erii.errors import LifecyclePlanError
from erii.lifecycle_erasure_contracts import ErasureSelector, ErasureTransformResult
from erii.lifecycle_memory_pack_import_contracts import MemoryPackStagingImportReport
from erii._lifecycle.plan_codec import (
    READABLE_LIFECYCLE_PLAN_CONTRACT_VERSIONS as _READABLE_LIFECYCLE_PLAN_CONTRACT_VERSIONS,
    compatibility_for_kind as _compatibility_for_kind,
    decode_plan as _decode_plan,
    encode_plan as _encode_plan,
    is_sha256 as _is_sha256,
    sha256_json as _sha256_json,
    validate_plan as _validate_plan_shape,
)


def _plan_intent_dict(plan: "LifecyclePlan") -> Dict[str, object]:
    from erii._lifecycle.serializers import plan_intent_dict
    return plan_intent_dict(plan)


def _plan_body_dict(plan: "LifecyclePlan") -> Dict[str, object]:
    from erii._lifecycle.serializers import plan_body_dict
    return plan_body_dict(plan)


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
        return _encode_plan(self)

    @classmethod
    def from_json(cls, json_text: str) -> "LifecyclePlan":
        """Loads a strict plan document and rejects unknown or duplicate fields."""
        return _decode_plan(json_text)


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


# Keep the historical public path used by introspection and serialized class references.
for _contract_type in (
    LifecycleTargetKind,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleAssessment,
    LifecycleOperation,
    LifecycleOutcome,
    LifecycleContentIdentity,
    BackupRequest,
    RestoreRequest,
    UpgradeRequest,
    EraseRequest,
    RebuildRequest,
    MemoryPackImportOptions,
    MemoryPackImportRequest,
    LifecycleDirectoryIdentity,
    LifecyclePlan,
    LifecycleReport,
):
    _contract_type.__module__ = "erii.data_lifecycle"
del _contract_type

__all__ = [
    "BackupRequest",
    "EraseRequest",
    "LifecycleAssessment",
    "LifecycleContentIdentity",
    "LifecycleDirectoryIdentity",
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
    "RebuildRequest",
    "RestoreRequest",
    "UpgradeRequest",
]
