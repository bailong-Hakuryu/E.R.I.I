"""Authoritative zero-write inspection for durable lifecycle targets."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Dict

from erii._lifecycle.contracts import (
    LifecycleAssessment,
    LifecycleContentIdentity,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
)
from erii._lifecycle.plan_codec import decode_strict_json, is_sha256
from erii._lifecycle.filesystem import (
    RegularFileIdentity,
    assert_no_link_or_reparse_ancestors,
    fingerprint_files,
    lexists,
    read_stable_bytes,
    require_regular_directory,
    require_regular_file,
    scan_directory_entries,
    sqlite_uri,
    stat_signature,
    stream_regular_tree_manifest,
    validate_relative_payload_path,
)
from erii._lifecycle.serializers import content_from_backup_manifest
from erii._lifecycle import sqlite_semantics
from erii._lifecycle.snapshots import (
    MAX_LIFECYCLE_MEMORY_PACK_BYTES,
    MAX_LIFECYCLE_TRANSFORM_BYTES,
    PayloadSnapshot,
    is_file_storage_runtime_lock,
    payload_entry_for_kind,
)
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
    LifecyclePlanError,
    StorageIntegrityError,
    UnsupportedFormatError,
)


FILE_STORAGE_MANIFEST = ".erii-store.json"
LIFECYCLE_BACKUP_MANIFEST = "manifest.json"
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


@dataclass(frozen=True, slots=True)
class BackupBundle:
    """A verified backup observation with its source content and payload view."""

    assessment: LifecycleAssessment
    content: LifecycleContentIdentity | None
    operation_id: str | None
    plan_digest: str | None
    snapshot: PayloadSnapshot | None


def missing_assessment(
    target: LifecycleTarget,
    compatibility: FormatCompatibility,
) -> LifecycleAssessment:
    """Return the canonical missing assessment for a target format."""
    return LifecycleAssessment(
        target=target,
        status=LifecycleStatus.MISSING,
        format_id=compatibility.format_id,
        detected_version=None,
        current_version=compatibility.current_version,
        fingerprint=None,
        file_count=0,
    )


class LifecycleInspector:
    """Inspect every supported lifecycle target without writing to it."""

    def inspect(self, target: LifecycleTarget) -> LifecycleAssessment:
        if not isinstance(target, LifecycleTarget):
            raise TypeError("inspect() requires a LifecycleTarget")
        assert_no_link_or_reparse_ancestors(
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
            return self.read_backup_bundle(target).assessment
        raise ValueError(f"unsupported lifecycle target kind {target.kind!r}")

    def read_backup_bundle(self, target: LifecycleTarget) -> BackupBundle:
        """Observe a complete Backup bundle through the Inspector Interface."""
        return read_backup_bundle(target)

    @staticmethod
    def _inspect_file_storage(target: LifecycleTarget) -> LifecycleAssessment:
        root = Path(target.path)
        if not lexists(root):
            return missing_assessment(target, FILE_STORAGE_FORMAT)
        require_regular_directory(root, label="FileStorage lifecycle target")
        streamed = stream_regular_tree_manifest(
            root,
            exclude_relative_name=is_file_storage_runtime_lock,
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
            content = read_stable_bytes(
                root.joinpath(*PurePosixPath(relative_name).parts),
                size_limit=MAX_LIFECYCLE_TRANSFORM_BYTES,
            )
            try:
                json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StorageIntegrityError(
                    f"FileStorage JSON document {Path(relative_name).name!r} "
                    "is malformed"
                ) from exc
            if relative_name == FILE_STORAGE_MANIFEST:
                manifest_content = content
        final_streamed = stream_regular_tree_manifest(
            root,
            exclude_relative_name=is_file_storage_runtime_lock,
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
                raise StorageIntegrityError(
                    "FileStorage manifest is malformed"
                ) from exc
            if (
                not isinstance(manifest, dict)
                or set(manifest) != _FILE_STORAGE_MANIFEST_FIELDS
            ):
                raise StorageIntegrityError(
                    "FileStorage manifest fields are invalid"
                )
            if manifest.get("format") != FILE_STORAGE_FORMAT.format_id:
                raise StorageIntegrityError(
                    "FileStorage manifest format identity is invalid"
                )
            raw_version = manifest.get("version")
            if isinstance(raw_version, bool) or not isinstance(raw_version, int):
                raise StorageIntegrityError(
                    "FileStorage manifest version must be an integer"
                )
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
                    "directory does not contain a recognizable E.R.I.I. "
                    "FileStorage layout"
                )
            detected_version = "legacy"
            status = LifecycleStatus.MIGRATION_REQUIRED
            if any(not name.endswith(".json") for name in relative_names):
                warnings = (
                    "unrecognized non-JSON files are included in the fingerprint",
                )

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
        if not lexists(path):
            return missing_assessment(target, SQLITE_FORMAT)
        require_regular_file(path, label="SQLite lifecycle target")
        sidecars = {
            suffix: Path(f"{path}{suffix}")
            for suffix in ("-wal", "-shm", "-journal")
        }
        for suffix, item in sidecars.items():
            if not lexists(item):
                continue
            sidecar_info = require_regular_file(
                item,
                label="SQLite lifecycle sidecar",
            )
            if suffix in {"-wal", "-journal"} and sidecar_info.st_size:
                raise StorageIntegrityError(
                    "SQLite lifecycle inspection requires a quiescent database "
                    "without WAL or journal data"
                )
        observed = [path] + [
            Path(f"{path}{suffix}")
            for suffix in ("-wal", "-shm", "-journal")
            if lexists(Path(f"{path}{suffix}"))
        ]
        initial_signatures = {
            item.name: stat_signature(
                require_regular_file(item, label="SQLite lifecycle file")
            )
            for item in observed
        }
        version = sqlite_semantics.read_sqlite_schema_version(
            str(path),
            immutable=True,
        )
        main_size = initial_signatures[path.name][3]
        try:
            with closing(
                sqlite3.connect(sqlite_uri(path, immutable=True), uri=True)
            ) as connection:
                connection.execute("PRAGMA query_only=ON")
                quick_check = [
                    row[0] for row in connection.execute("PRAGMA quick_check")
                ]
        except sqlite3.Error as exc:
            raise StorageIntegrityError(
                "SQLite integrity check could not be completed"
            ) from exc
        if quick_check != ["ok"]:
            raise StorageIntegrityError("SQLite quick integrity check failed")
        semantic_fingerprint = None
        if main_size:
            semantic_fingerprint = sqlite_semantics.semantic_digest_from_path(path)
        observed_after = [path] + [
            Path(f"{path}{suffix}")
            for suffix in ("-wal", "-shm", "-journal")
            if lexists(Path(f"{path}{suffix}"))
        ]
        if [item.name for item in observed_after] != [
            item.name for item in observed
        ]:
            raise StorageIntegrityError(
                "SQLite lifecycle target changed during inspection"
            )
        if any(
            item.name.endswith(("-wal", "-journal"))
            and require_regular_file(
                item,
                label="SQLite lifecycle sidecar",
            ).st_size
            for item in observed_after
        ):
            raise StorageIntegrityError(
                "SQLite lifecycle target changed during inspection"
            )
        final_signatures = {
            item.name: stat_signature(
                require_regular_file(item, label="SQLite lifecycle file")
            )
            for item in observed_after
        }
        if final_signatures != initial_signatures:
            raise StorageIntegrityError(
                "SQLite lifecycle target changed during inspection"
            )
        if main_size == 0:
            return LifecycleAssessment(
                target=target,
                status=LifecycleStatus.EMPTY,
                format_id=SQLITE_FORMAT.format_id,
                detected_version=None,
                current_version=SQLITE_FORMAT.current_version,
                fingerprint=fingerprint_files({"database.sqlite3": b""}),
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
        if not lexists(path):
            return missing_assessment(target, MEMORY_PACK_FORMAT)
        require_regular_file(path, label="MemoryPack lifecycle target")
        content = read_stable_bytes(
            path,
            size_limit=MAX_LIFECYCLE_MEMORY_PACK_BYTES,
        )
        try:
            decoded = decode_memory_pack_json(content.decode("utf-8"))
        except UnsupportedFormatError:
            raise
        except (UnicodeDecodeError, ValueError) as exc:
            raise StorageIntegrityError(
                "MemoryPack is unreadable or malformed"
            ) from exc
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


def assessment_matches_content(
    assessment: LifecycleAssessment,
    content: LifecycleContentIdentity,
) -> bool:
    """Return whether an assessment exactly names a content identity."""
    return (
        assessment.target.kind is content.kind
        and assessment.status is content.status
        and assessment.format_id == content.format_id
        and assessment.detected_version == content.detected_version
        and assessment.current_version == content.current_version
        and assessment.fingerprint == content.fingerprint
        and assessment.file_count == content.file_count
    )


def read_backup_bundle(target: LifecycleTarget) -> BackupBundle:
    """Read and fully verify a Backup-v1 bundle without modifying it."""
    if target.kind is not LifecycleTargetKind.BACKUP:
        raise TypeError("backup inspection requires a backup lifecycle target")
    root = Path(target.path)
    assert_no_link_or_reparse_ancestors(
        root,
        label="backup lifecycle target",
    )
    if not lexists(root):
        return BackupBundle(
            assessment=missing_assessment(target, LIFECYCLE_BACKUP_FORMAT),
            content=None,
            operation_id=None,
            plan_digest=None,
            snapshot=None,
        )
    require_regular_directory(root, label="backup lifecycle target")
    initial_files, initial_directories = scan_directory_entries(
        root,
        label="backup lifecycle target",
    )
    try:
        direct_children = {child.name: child for child in root.iterdir()}
    except OSError as exc:
        raise StorageIntegrityError(
            "backup lifecycle target cannot be read"
        ) from exc
    if not direct_children:
        final_files, final_directories = scan_directory_entries(
            root,
            label="backup lifecycle target",
        )
        if (
            final_files != initial_files
            or final_directories != initial_directories
        ):
            raise StorageIntegrityError(
                "backup lifecycle target changed during inspection"
            )
        return BackupBundle(
            assessment=LifecycleAssessment(
                target=target,
                status=LifecycleStatus.EMPTY,
                format_id=LIFECYCLE_BACKUP_FORMAT.format_id,
                detected_version=None,
                current_version=LIFECYCLE_BACKUP_FORMAT.current_version,
                fingerprint=fingerprint_files({}),
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
    require_regular_file(manifest_path, label="backup manifest")
    require_regular_directory(payload_root, label="backup payload")

    streamed = stream_regular_tree_manifest(root)
    streamed_entries = {entry.relative_path: entry for entry in streamed.files}
    if set(streamed_entries) != set(initial_files):
        raise StorageIntegrityError(
            "backup lifecycle target changed during inspection"
        )
    for directory in initial_directories:
        if directory == "payload":
            continue
        prefix = f"{directory}/"
        if not any(name.startswith(prefix) for name in initial_files):
            raise StorageIntegrityError(
                "backup lifecycle target contains an undeclared empty directory"
            )
    manifest_bytes = read_stable_bytes(
        manifest_path,
        expected_signature=initial_files[LIFECYCLE_BACKUP_MANIFEST],
        size_limit=MAX_LIFECYCLE_BACKUP_MANIFEST_BYTES,
    )
    try:
        manifest = decode_strict_json(
            manifest_bytes.decode("utf-8"),
            label="backup manifest",
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise StorageIntegrityError(
            "backup manifest is unreadable or malformed"
        ) from exc
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
        raise StorageIntegrityError(
            "backup manifest format identity is invalid"
        )
    require_supported_version(LIFECYCLE_BACKUP_FORMAT, manifest["version"])
    operation_id = manifest["operation_id"]
    plan_digest = manifest["plan_digest"]
    if not is_sha256(operation_id) or not is_sha256(plan_digest):
        raise StorageIntegrityError("backup operation identity is invalid")
    try:
        source_content = content_from_backup_manifest(manifest["source"])
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
        raise StorageIntegrityError(
            "backup payload manifest fields are invalid"
        )
    payload_entry = payload_entry_for_kind(source_content.kind)
    if payload["entry"] != payload_entry:
        raise StorageIntegrityError(
            "backup payload entry does not match its storage kind"
        )
    if payload["tree_fingerprint"] != source_content.fingerprint:
        raise StorageIntegrityError(
            "backup payload fingerprint does not match its source"
        )
    listed_files = payload["files"]
    if not isinstance(listed_files, list):
        raise StorageIntegrityError(
            "backup payload file manifest must be an array"
        )
    expected_files: Dict[str, tuple[int, str]] = {}
    casefold_names: set[str] = set()
    for item in listed_files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise StorageIntegrityError("backup payload file entry is invalid")
        relative_name = validate_relative_payload_path(item["path"])
        folded = relative_name.casefold()
        if relative_name in expected_files or folded in casefold_names:
            raise StorageIntegrityError(
                "backup payload file paths are duplicated"
            )
        size = item["size"]
        digest = item["sha256"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise StorageIntegrityError(
                "backup payload file size is invalid"
            )
        if not is_sha256(digest):
            raise StorageIntegrityError(
                "backup payload file digest is invalid"
            )
        expected_files[relative_name] = (size, digest)
        casefold_names.add(folded)
    if [item["path"] for item in listed_files] != sorted(expected_files):
        raise StorageIntegrityError(
            "backup payload file manifest is not canonical"
        )

    actual_entries = {
        relative_name.removeprefix("payload/"): entry
        for relative_name, entry in streamed_entries.items()
        if relative_name.startswith("payload/")
    }
    if set(actual_entries) != set(expected_files):
        raise StorageIntegrityError(
            "backup payload files do not match the manifest"
        )
    for relative_name, entry in actual_entries.items():
        size, digest = expected_files[relative_name]
        if entry.size != size or entry.sha256 != digest:
            raise StorageIntegrityError(
                "backup payload file verification failed"
            )
    if source_content.file_count != len(actual_entries):
        raise StorageIntegrityError(
            "backup source file count does not match its payload"
        )

    payload_target = LifecycleTarget(
        source_content.kind,
        str(root.joinpath(*PurePosixPath(payload_entry).parts)),
    )
    payload_assessment = LifecycleInspector().inspect(payload_target)
    if not assessment_matches_content(payload_assessment, source_content):
        raise StorageIntegrityError(
            "backup payload does not match its source identity"
        )

    final_files, final_directories = scan_directory_entries(
        root,
        label="backup lifecycle target",
    )
    if final_files != initial_files or final_directories != initial_directories:
        raise StorageIntegrityError(
            "backup lifecycle target changed during inspection"
        )

    return BackupBundle(
        assessment=LifecycleAssessment(
            target=target,
            status=LifecycleStatus.CURRENT,
            format_id=LIFECYCLE_BACKUP_FORMAT.format_id,
            detected_version=LIFECYCLE_BACKUP_FORMAT.current_version,
            current_version=LIFECYCLE_BACKUP_FORMAT.current_version,
            fingerprint=streamed.tree_fingerprint,
            file_count=streamed.file_count,
        ),
        content=source_content,
        operation_id=operation_id,
        plan_digest=plan_digest,
        snapshot=PayloadSnapshot(
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


def require_complete_bundle(bundle: BackupBundle) -> BackupBundle:
    """Require every field needed for restore and retry verification."""
    if (
        bundle.assessment.status is not LifecycleStatus.CURRENT
        or bundle.content is None
        or bundle.operation_id is None
        or bundle.plan_digest is None
        or bundle.snapshot is None
    ):
        raise LifecyclePlanError(
            "restore requires a complete verified backup bundle"
        )
    return bundle


# Preserve the historical public path used by introspection and pickle.
LifecycleInspector.__module__ = "erii.data_lifecycle"


__all__ = [
    "BackupBundle",
    "FILE_STORAGE_MANIFEST",
    "LIFECYCLE_BACKUP_MANIFEST",
    "LifecycleInspector",
    "MAX_LIFECYCLE_BACKUP_MANIFEST_BYTES",
    "assessment_matches_content",
    "missing_assessment",
    "read_backup_bundle",
    "require_complete_bundle",
]
