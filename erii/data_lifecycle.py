"""Inspection, durable planning, backup, and restore for the v0.4 Beta lifecycle."""

from contextlib import closing, contextmanager
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


FILE_STORAGE_MANIFEST = ".erii-store.json"
LIFECYCLE_BACKUP_MANIFEST = "manifest.json"
LIFECYCLE_PLAN_CONTRACT_VERSION = "1"
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


LifecycleRequest: TypeAlias = BackupRequest | RestoreRequest


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
    plan_digest: str

    def __post_init__(self) -> None:
        if self.contract_version != LIFECYCLE_PLAN_CONTRACT_VERSION:
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


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _decode_strict_json(json_text: str, *, label: str) -> Any:
    if not isinstance(json_text, str):
        raise ValueError(f"{label} must be JSON text")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate field {key!r}")
            result[key] = value
        return result

    return json.loads(json_text, object_pairs_hook=reject_duplicates)


def _target_to_dict(target: LifecycleTarget) -> Dict[str, object]:
    return {"kind": target.kind.value, "path": target.path}


def _target_from_dict(value: object) -> LifecycleTarget:
    if not isinstance(value, dict) or set(value) != {"kind", "path"}:
        raise LifecyclePlanError("lifecycle target fields are invalid")
    return LifecycleTarget(
        kind=LifecycleTargetKind(value["kind"]),
        path=value["path"],
    )


def _assessment_to_dict(assessment: LifecycleAssessment) -> Dict[str, object]:
    return {
        "target": _target_to_dict(assessment.target),
        "status": assessment.status.value,
        "format_id": assessment.format_id,
        "detected_version": assessment.detected_version,
        "current_version": assessment.current_version,
        "fingerprint": assessment.fingerprint,
        "file_count": assessment.file_count,
        "warnings": list(assessment.warnings),
    }


def _assessment_from_dict(value: object) -> LifecycleAssessment:
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
    if fingerprint is not None and not _is_sha256(fingerprint):
        raise LifecyclePlanError("lifecycle assessment fingerprint is invalid")
    return LifecycleAssessment(
        target=_target_from_dict(value["target"]),
        status=LifecycleStatus(value["status"]),
        format_id=value["format_id"],
        detected_version=value["detected_version"],
        current_version=value["current_version"],
        fingerprint=fingerprint,
        file_count=file_count,
        warnings=tuple(warnings),
    )


def _content_to_dict(content: LifecycleContentIdentity) -> Dict[str, object]:
    return {
        "kind": content.kind.value,
        "status": content.status.value,
        "format_id": content.format_id,
        "detected_version": content.detected_version,
        "current_version": content.current_version,
        "fingerprint": content.fingerprint,
        "file_count": content.file_count,
    }


def _content_from_dict(value: object) -> LifecycleContentIdentity:
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


def _directory_identity_to_dict(
    identity: LifecycleDirectoryIdentity,
) -> Dict[str, object]:
    return {
        "resolved_path": identity.resolved_path,
        # Decimal strings avoid IEEE-754 precision loss in JSON tooling on
        # Windows, where file identities commonly exceed 2**53.
        "device": str(identity.device),
        "inode": str(identity.inode),
    }


def _directory_identity_from_dict(value: object) -> LifecycleDirectoryIdentity:
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


def _plan_intent_dict(plan: LifecyclePlan) -> Dict[str, object]:
    return {
        "contract_version": plan.contract_version,
        "operation": plan.operation.value,
        "source": _assessment_to_dict(plan.source),
        "destination": _assessment_to_dict(plan.destination),
        "destination_parent": _directory_identity_to_dict(plan.destination_parent),
        "content": _content_to_dict(plan.content),
    }


def _plan_body_dict(plan: LifecyclePlan) -> Dict[str, object]:
    return {**_plan_intent_dict(plan), "operation_id": plan.operation_id}


def _plan_document_dict(plan: LifecyclePlan) -> Dict[str, object]:
    return {**_plan_body_dict(plan), "plan_digest": plan.plan_digest}


def _plan_from_document(value: object) -> LifecyclePlan:
    fields = {
        "contract_version",
        "operation",
        "operation_id",
        "source",
        "destination",
        "destination_parent",
        "content",
        "plan_digest",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise LifecyclePlanError("lifecycle plan fields are invalid")
    return LifecyclePlan(
        contract_version=value["contract_version"],
        operation=LifecycleOperation(value["operation"]),
        operation_id=value["operation_id"],
        source=_assessment_from_dict(value["source"]),
        destination=_assessment_from_dict(value["destination"]),
        destination_parent=_directory_identity_from_dict(value["destination_parent"]),
        content=_content_from_dict(value["content"]),
        plan_digest=value["plan_digest"],
    )


def _validate_plan_shape(plan: LifecyclePlan) -> None:
    _validate_assessment(plan.source)
    _validate_assessment(plan.destination)
    if plan.operation is LifecycleOperation.BACKUP:
        if plan.source.target.kind is LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("backup plan source must be live storage")
        if plan.destination.target.kind is not LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("backup plan destination must be a backup bundle")
        if plan.content != LifecycleContentIdentity.from_assessment(plan.source):
            raise LifecyclePlanError("backup plan content does not match its source")
    else:
        if plan.source.target.kind is not LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("restore plan source must be a backup bundle")
        if plan.destination.target.kind is LifecycleTargetKind.BACKUP:
            raise LifecyclePlanError("restore plan destination must be live storage")
        if plan.content.kind is not plan.destination.target.kind:
            raise LifecyclePlanError("restore destination kind does not match backup content")


def _make_plan(
    *,
    operation: LifecycleOperation,
    source: LifecycleAssessment,
    destination: LifecycleAssessment,
    destination_parent: LifecycleDirectoryIdentity,
    content: LifecycleContentIdentity,
) -> LifecyclePlan:
    intent = {
        "contract_version": LIFECYCLE_PLAN_CONTRACT_VERSION,
        "operation": operation.value,
        "source": _assessment_to_dict(source),
        "destination": _assessment_to_dict(destination),
        "destination_parent": _directory_identity_to_dict(destination_parent),
        "content": _content_to_dict(content),
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
) -> bytes:
    label = f"lifecycle source file {path.name!r}"
    _assert_no_link_or_reparse_ancestors(path, label=label)
    before = _require_regular_file(path, label=label)
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
            chunk = os.read(descriptor, 1024 * 1024)
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
        if _read_stable_bytes(path)[:16] != b"SQLite format 3\x00":
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
        content_by_name = cls._scan_file_storage(root)
        for relative_name, content in content_by_name.items():
            if not relative_name.endswith(".json"):
                continue
            try:
                json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StorageIntegrityError(
                    f"FileStorage JSON document {Path(relative_name).name!r} is malformed"
                ) from exc

        manifest_content = content_by_name.get(FILE_STORAGE_MANIFEST)
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
        elif not content_by_name:
            detected_version = None
            status = LifecycleStatus.EMPTY
        else:
            recognized = any(
                Path(name).name in _LEGACY_BASENAMES
                or Path(name).parts[0] in _LEGACY_TOP_LEVEL_DIRECTORIES
                for name in content_by_name
            )
            if not recognized:
                raise StorageIntegrityError(
                    "directory does not contain a recognizable E.R.I.I. FileStorage layout"
                )
            detected_version = "legacy"
            status = LifecycleStatus.MIGRATION_REQUIRED
            if any(not name.endswith(".json") for name in content_by_name):
                warnings = ("unrecognized non-JSON files are included in the fingerprint",)

        return LifecycleAssessment(
            target=target,
            status=status,
            format_id=FILE_STORAGE_FORMAT.format_id,
            detected_version=detected_version,
            current_version=FILE_STORAGE_FORMAT.current_version,
            fingerprint=_fingerprint_files(content_by_name),
            file_count=len(content_by_name),
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
        before = {
            item.name: _read_stable_bytes(
                item,
                expected_signature=initial_signatures[item.name],
            )
            for item in observed
        }
        version = read_sqlite_schema_version(str(path), immutable=True)
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
        after = {
            item.name: _read_stable_bytes(
                item,
                expected_signature=initial_signatures[item.name],
            )
            for item in observed_after
        }
        if after != before:
            raise StorageIntegrityError("SQLite lifecycle target changed during inspection")
        if before[path.name] == b"":
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
            fingerprint=_fingerprint_files({"database.sqlite3": before[path.name]}),
            file_count=1,
        )

    @staticmethod
    def _inspect_memory_pack(target: LifecycleTarget) -> LifecycleAssessment:
        path = Path(target.path)
        if not _lexists(path):
            return _missing_assessment(target, MEMORY_PACK_FORMAT)
        _require_regular_file(path, label="MemoryPack lifecycle target")
        content = _read_stable_bytes(path)
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
    files: Dict[str, bytes]


@dataclass(frozen=True, slots=True)
class _BackupBundle:
    assessment: LifecycleAssessment
    content: LifecycleContentIdentity | None
    operation_id: str | None
    plan_digest: str | None
    snapshot: _PayloadSnapshot | None


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
        files = LifecycleInspector._scan_file_storage(Path(assessment.target.path))
        snapshot = _PayloadSnapshot(
            content=LifecycleContentIdentity.from_assessment(assessment),
            files=files,
        )
        if _fingerprint_files(files) != snapshot.content.fingerprint:
            raise StaleLifecyclePlanError("FileStorage changed while it was captured")
        return snapshot

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        _create_private_directory(staging_path)
        _write_payload_files(staging_path, snapshot.files)


class _SQLiteLifecycleAdapter(_LifecycleFormatAdapter):
    kind = LifecycleTargetKind.SQLITE
    payload_entry = "payload/database.sqlite3"

    def capture(self, assessment: LifecycleAssessment) -> _PayloadSnapshot:
        content = _read_stable_bytes(Path(assessment.target.path))
        files = {"database.sqlite3": content}
        snapshot = _PayloadSnapshot(
            content=LifecycleContentIdentity.from_assessment(assessment),
            files=files,
        )
        if _fingerprint_files(files) != snapshot.content.fingerprint:
            raise StaleLifecyclePlanError("SQLite changed while it was captured")
        return snapshot

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        _write_durable_file(staging_path, snapshot.files["database.sqlite3"])


class _MemoryPackLifecycleAdapter(_LifecycleFormatAdapter):
    kind = LifecycleTargetKind.MEMORY_PACK
    payload_entry = "payload/memory-pack.erii"

    def capture(self, assessment: LifecycleAssessment) -> _PayloadSnapshot:
        content = _read_stable_bytes(Path(assessment.target.path))
        files = {"memory-pack.erii": content}
        snapshot = _PayloadSnapshot(
            content=LifecycleContentIdentity.from_assessment(assessment),
            files=files,
        )
        if hashlib.sha256(content).hexdigest() != snapshot.content.fingerprint:
            raise StaleLifecyclePlanError("MemoryPack changed while it was captured")
        return snapshot

    def write_restored(self, snapshot: _PayloadSnapshot, staging_path: Path) -> None:
        _write_durable_file(staging_path, snapshot.files["memory-pack.erii"])


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


def _write_payload_files(root: Path, files: Dict[str, bytes]) -> None:
    for relative_name in sorted(files):
        relative = PurePosixPath(relative_name)
        _validate_relative_payload_path(relative_name)
        _ensure_private_payload_parent(root, relative)
        _write_durable_file(root.joinpath(*relative.parts), files[relative_name])


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


def _scan_directory_strict(root: Path) -> Dict[str, bytes]:
    initial, initial_directories = _scan_directory_entries(
        root,
        label="backup lifecycle target",
    )
    content = {
        name: _read_stable_bytes(
            root.joinpath(*PurePosixPath(name).parts),
            expected_signature=initial[name],
        )
        for name in sorted(initial)
    }
    final, final_directories = _scan_directory_entries(
        root,
        label="backup lifecycle target",
    )
    if final != initial or final_directories != initial_directories:
        raise StorageIntegrityError("backup lifecycle target changed during inspection")
    for directory in final_directories:
        if directory == "payload":
            continue
        prefix = f"{directory}/"
        if not any(name.startswith(prefix) for name in final):
            raise StorageIntegrityError(
                "backup lifecycle target contains an undeclared empty directory"
            )
    return content


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
            "files": _payload_file_manifest(snapshot.files),
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

    content_by_name = _scan_directory_strict(root)
    manifest_bytes = content_by_name.get(LIFECYCLE_BACKUP_MANIFEST)
    if manifest_bytes is None:
        raise StorageIntegrityError("backup manifest is missing")
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
        source_content = _content_from_dict(manifest["source"])
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

    actual_files = {
        relative_name.removeprefix("payload/"): content
        for relative_name, content in content_by_name.items()
        if relative_name.startswith("payload/")
    }
    if set(actual_files) != set(expected_files):
        raise StorageIntegrityError("backup payload files do not match the manifest")
    for relative_name, content in actual_files.items():
        size, digest = expected_files[relative_name]
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise StorageIntegrityError("backup payload file verification failed")
    if source_content.file_count != len(actual_files):
        raise StorageIntegrityError("backup source file count does not match its payload")

    payload_target = adapter.restored_target(
        root.joinpath(*PurePosixPath(adapter.payload_entry).parts)
    )
    payload_assessment = LifecycleInspector().inspect(payload_target)
    if not _assessment_matches_content(payload_assessment, source_content):
        raise StorageIntegrityError("backup payload does not match its source identity")

    assessment = LifecycleAssessment(
        target=target,
        status=LifecycleStatus.CURRENT,
        format_id=LIFECYCLE_BACKUP_FORMAT.format_id,
        detected_version=LIFECYCLE_BACKUP_FORMAT.current_version,
        current_version=LIFECYCLE_BACKUP_FORMAT.current_version,
        fingerprint=_fingerprint_files(content_by_name),
        file_count=len(content_by_name),
    )
    return _BackupBundle(
        assessment=assessment,
        content=source_content,
        operation_id=operation_id,
        plan_digest=plan_digest,
        snapshot=_PayloadSnapshot(content=source_content, files=actual_files),
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


def _assert_plan_destination_topology(plan: LifecyclePlan) -> None:
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
        raise StaleLifecyclePlanError("lifecycle destination parent changed after planning")


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


def _prepare_staging(plan: LifecyclePlan, staging: Path, owner: Path) -> None:
    expected_owner = {
        "operation_id": plan.operation_id,
        "plan_digest": plan.plan_digest,
    }
    if _lexists(staging) or _lexists(owner):
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
        try:
            owner.unlink()
        except OSError as exc:
            raise LifecycleConflictError("could not clear lifecycle staging ownership") from exc
    _write_durable_file(owner, _owner_document(plan))
    _fsync_directory(owner.parent)


def _cleanup_staging(plan: LifecyclePlan, staging: Path, owner: Path) -> None:
    if _lexists(owner):
        try:
            _require_regular_file(owner, label="lifecycle staging ownership")
        except StorageIntegrityError as exc:
            raise LifecycleConflictError("lifecycle staging ownership changed") from exc
        expected = {"operation_id": plan.operation_id, "plan_digest": plan.plan_digest}
        if _read_owner(owner) != expected:
            raise LifecycleConflictError("lifecycle staging ownership changed")
        _remove_staging_path(staging)
        try:
            owner.unlink()
        except OSError as exc:
            raise LifecycleConflictError("could not clear lifecycle staging ownership") from exc
        _fsync_directory(owner.parent)
    elif _lexists(staging):
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


def _stage_paths(destination: Path, plan: LifecyclePlan) -> tuple[Path, Path]:
    stem = f".{destination.name}.{plan.operation_id[:12]}.{plan.operation.value}.tmp"
    staging = destination.parent / stem
    return staging, destination.parent / f"{stem}.owner"


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
    return (
        bundle.assessment.status is LifecycleStatus.CURRENT
        and bundle.operation_id == plan.operation_id
        and bundle.plan_digest == plan.plan_digest
        and bundle.content == plan.content
        and bundle.snapshot is not None
    )


def _report(
    plan: LifecyclePlan,
    *,
    outcome: LifecycleOutcome,
    artifact_fingerprint: str,
) -> LifecycleReport:
    return LifecycleReport(
        operation_id=plan.operation_id,
        plan_digest=plan.plan_digest,
        operation=plan.operation,
        outcome=outcome,
        content_fingerprint=plan.content.fingerprint,
        artifact_fingerprint=artifact_fingerprint,
        file_count=plan.content.file_count,
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
        """Freezes a zero-write backup or restore plan."""
        if isinstance(request, BackupRequest):
            return self._plan_backup(request)
        if isinstance(request, RestoreRequest):
            return self._plan_restore(request)
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
        )

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
                _write_payload_files(payload_root, snapshot.files)
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


__all__ = [
    "BackupRequest",
    "DataLifecycleCoordinator",
    "FILE_STORAGE_MANIFEST",
    "LIFECYCLE_BACKUP_MANIFEST",
    "LIFECYCLE_PLAN_CONTRACT_VERSION",
    "LifecycleAssessment",
    "LifecycleContentIdentity",
    "LifecycleDirectoryIdentity",
    "LifecycleInspector",
    "LifecycleOperation",
    "LifecycleOutcome",
    "LifecyclePlan",
    "LifecycleReport",
    "LifecycleRequest",
    "LifecycleStatus",
    "LifecycleTarget",
    "LifecycleTargetKind",
    "RestoreRequest",
]
