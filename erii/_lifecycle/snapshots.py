"""Immutable read observations shared by lifecycle inspection and execution."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Dict, Mapping

from erii._lifecycle.contracts import (
    LifecycleAssessment,
    LifecycleContentIdentity,
    LifecycleStatus,
    LifecycleTargetKind,
)
from erii._lifecycle.filesystem import (
    RegularFileIdentity,
    fingerprint_files,
    read_stable_bytes,
    stream_regular_file_identity,
    stream_regular_tree_manifest,
    validate_relative_payload_path,
)
from erii._lifecycle.sqlite_semantics import semantic_digest_from_path
from erii.errors import LifecyclePlanError, StaleLifecyclePlanError, StorageIntegrityError


MAX_LIFECYCLE_MEMORY_PACK_BYTES = 256 * 1024 * 1024
MAX_LIFECYCLE_TRANSFORM_BYTES = 512 * 1024 * 1024
_FILE_STORAGE_RUNTIME_LOCK_DIRECTORIES = frozenset(
    {
        "_relationship_history_locks",
        "_relationship_processing_locks",
        "_turn_locks",
    }
)
_PAYLOAD_ENTRY_BY_KIND = {
    LifecycleTargetKind.FILE_STORAGE: "payload",
    LifecycleTargetKind.SQLITE: "payload/database.sqlite3",
    LifecycleTargetKind.MEMORY_PACK: "payload/memory-pack.erii",
}


@dataclass(frozen=True, slots=True)
class PayloadSnapshot:
    """A materialized or stable streamed view of lifecycle payload content."""

    content: LifecycleContentIdentity
    files: Mapping[str, bytes] | None = None
    source_paths: Mapping[str, str] | None = None
    identities: Mapping[str, RegularFileIdentity] | None = None

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
            source_paths = dict(self.source_paths)
            identities = dict(self.identities)
            if any(not isinstance(value, str) for value in source_paths.values()):
                raise ValueError("streamed payload snapshot paths must be strings")
            if any(
                not isinstance(value, RegularFileIdentity)
                for value in identities.values()
            ):
                raise ValueError("streamed payload snapshot identities are invalid")
            self._validate_entry_names(source_paths)
            self._validate_file_count(source_paths)
            object.__setattr__(
                self,
                "source_paths",
                MappingProxyType(source_paths),
            )
            object.__setattr__(
                self,
                "identities",
                MappingProxyType(identities),
            )
            return

        assert self.files is not None
        files = dict(self.files)
        if any(not isinstance(value, bytes) for value in files.values()):
            raise ValueError("materialized payload snapshot values must be bytes")
        self._validate_entry_names(files)
        self._validate_file_count(files)
        object.__setattr__(self, "files", MappingProxyType(files))

    @staticmethod
    def _validate_entry_names(entries: Mapping[str, object]) -> None:
        folded_names: set[str] = set()
        for value in entries:
            try:
                relative_name = validate_relative_payload_path(value)
            except StorageIntegrityError as exc:
                raise ValueError(
                    "payload snapshot path is not canonical"
                ) from exc
            folded = relative_name.casefold()
            if folded in folded_names:
                raise ValueError(
                    "payload snapshot paths contain a case-insensitive duplicate"
                )
            folded_names.add(folded)

    def _validate_file_count(self, entries: Mapping[str, object]) -> None:
        if len(entries) != self.content.file_count:
            raise ValueError(
                "payload snapshot entries do not match the content file count"
            )


def is_file_storage_runtime_lock(relative_name: str) -> bool:
    """Return whether a relative path is an excluded runtime lock."""
    path = PurePosixPath(relative_name)
    if path.parts == ("_turn_context_snapshot.lock",):
        return True
    if (
        len(path.parts) != 2
        or path.parts[0] not in _FILE_STORAGE_RUNTIME_LOCK_DIRECTORIES
    ):
        return False
    filename = path.parts[1]
    if not filename.endswith(".lock"):
        return False
    digest = filename.removesuffix(".lock")
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def payload_entry_for_kind(kind: LifecycleTargetKind) -> str:
    """Return the frozen Backup-v1 payload entry for a live target kind."""
    try:
        return _PAYLOAD_ENTRY_BY_KIND[kind]
    except KeyError as exc:
        raise LifecyclePlanError(
            f"no live-data lifecycle adapter for {kind.value!r}"
        ) from exc


def capture_snapshot(assessment: LifecycleAssessment) -> PayloadSnapshot:
    """Capture a stable streamed payload view bound to an assessment."""
    content = LifecycleContentIdentity.from_assessment(assessment)
    if assessment.target.kind is LifecycleTargetKind.FILE_STORAGE:
        root = Path(assessment.target.path)
        manifest = stream_regular_tree_manifest(
            root,
            exclude_relative_name=is_file_storage_runtime_lock,
        )
        snapshot = PayloadSnapshot(
            content=content,
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
            manifest.tree_fingerprint != content.fingerprint
            or manifest.file_count != content.file_count
        ):
            raise StaleLifecyclePlanError(
                "FileStorage changed while it was captured"
            )
        return snapshot
    if assessment.target.kind is LifecycleTargetKind.SQLITE:
        source_path = Path(assessment.target.path)
        identity = stream_regular_file_identity(source_path)
        snapshot = PayloadSnapshot(
            content=content,
            source_paths={"database.sqlite3": str(source_path)},
            identities={"database.sqlite3": identity},
        )
        if assessment.status is LifecycleStatus.EMPTY:
            if identity.size != 0:
                raise StaleLifecyclePlanError(
                    "SQLite changed while it was captured"
                )
            actual_fingerprint = fingerprint_files({"database.sqlite3": b""})
        else:
            actual_fingerprint = semantic_digest_from_path(source_path)
        if actual_fingerprint != content.fingerprint:
            raise StaleLifecyclePlanError(
                "SQLite changed while it was captured"
            )
        return snapshot
    if assessment.target.kind is LifecycleTargetKind.MEMORY_PACK:
        source_path = Path(assessment.target.path)
        identity = stream_regular_file_identity(source_path)
        if identity.size > MAX_LIFECYCLE_MEMORY_PACK_BYTES:
            raise StorageIntegrityError(
                "MemoryPack exceeds the supported lifecycle size limit"
            )
        snapshot = PayloadSnapshot(
            content=content,
            source_paths={"memory-pack.erii": str(source_path)},
            identities={"memory-pack.erii": identity},
        )
        if identity.sha256 != content.fingerprint:
            raise StaleLifecyclePlanError(
                "MemoryPack changed while it was captured"
            )
        return snapshot
    raise ValueError(
        f"unsupported lifecycle snapshot kind {assessment.target.kind!r}"
    )


def materialize_snapshot(source: PayloadSnapshot) -> PayloadSnapshot:
    """Materialize a bounded streamed snapshot after revalidating every file."""
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
        content = read_stable_bytes(
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
    return PayloadSnapshot(content=source.content, files=files)


__all__ = [
    "MAX_LIFECYCLE_MEMORY_PACK_BYTES",
    "MAX_LIFECYCLE_TRANSFORM_BYTES",
    "PayloadSnapshot",
    "capture_snapshot",
    "fingerprint_files",
    "is_file_storage_runtime_lock",
    "materialize_snapshot",
    "payload_entry_for_kind",
]
