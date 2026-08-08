"""Independent package, runtime, and durable-format compatibility identities."""

from dataclasses import dataclass
import json
from typing import Any, Dict

from erii._version import __version__
from erii.errors import UnsupportedFormatError


@dataclass(frozen=True, slots=True)
class FormatCompatibility:
    """Names one durable format without coupling it to the package version."""

    format_id: str
    current_version: str
    readable_versions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.current_version not in self.readable_versions:
            raise ValueError("current format version must also be readable")


@dataclass(frozen=True, slots=True)
class CompatibilityCatalog:
    """Single source of truth for versions the current development build reads."""

    package_version: str
    python_requires: str
    python_tested_through: str
    sqlite: FormatCompatibility
    file_storage: FormatCompatibility
    memory_pack: FormatCompatibility
    lifecycle_backup: FormatCompatibility
    lifecycle_plan: FormatCompatibility


SQLITE_FORMAT = FormatCompatibility(
    format_id="erii.sqlite",
    current_version="10",
    readable_versions=tuple(str(version) for version in range(11)),
)
FILE_STORAGE_FORMAT = FormatCompatibility(
    format_id="erii.file-storage",
    current_version="2",
    readable_versions=("legacy", "1", "2"),
)
MEMORY_PACK_FORMAT = FormatCompatibility(
    format_id="erii.memory-pack",
    current_version="0.5.0a1",
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
        "0.5.0a1",
    ),
)
LIFECYCLE_BACKUP_FORMAT = FormatCompatibility(
    format_id="erii.lifecycle-backup",
    current_version="1",
    readable_versions=("1",),
)
LIFECYCLE_PLAN_FORMAT = FormatCompatibility(
    format_id="erii.lifecycle-plan",
    current_version="3",
    readable_versions=("1", "2", "3"),
)

COMPATIBILITY_CATALOG = CompatibilityCatalog(
    package_version=__version__,
    python_requires=">=3.11",
    python_tested_through="3.14",
    sqlite=SQLITE_FORMAT,
    file_storage=FILE_STORAGE_FORMAT,
    memory_pack=MEMORY_PACK_FORMAT,
    lifecycle_backup=LIFECYCLE_BACKUP_FORMAT,
    lifecycle_plan=LIFECYCLE_PLAN_FORMAT,
)

MEMORY_PACK_METADATA_FIELDS = frozenset({"version", "agent_id", "user_id", "exported_at"})
MEMORY_PACK_ROOT_FIELDS = frozenset(
    {
        "metadata",
        "core_memory",
        "nodes",
        "timeline",
        "timeline_entries",
        "archival_ledger",
        "relationship",
        "relationship_events",
        "relationship_direct_event_ids",
        "relationship_adjudications",
        "relationship_consequences",
        "narrative_tension_links",
        "persona_growth_proposals",
        "persona_compilation_proposals",
        "persona_manifests",
        "turn_records",
        "relationship_processing_runs",
        "persona_reflection_decisions",
    }
)
_MEMORY_PACK_V050A1_EXTENSION_FIELDS = frozenset(
    {
        "relationship_consequences",
        "narrative_tension_links",
    }
)
_MEMORY_PACK_LEGACY_ROOT_FIELDS = (
    MEMORY_PACK_ROOT_FIELDS - _MEMORY_PACK_V050A1_EXTENSION_FIELDS
)
_MEMORY_PACK_PRE_V050A1_VERSIONS = frozenset(
    MEMORY_PACK_FORMAT.readable_versions[
        : MEMORY_PACK_FORMAT.readable_versions.index("0.5.0a1")
    ]
)
_MEMORY_PACK_OBJECT_COLLECTIONS = (
    "nodes",
    "timeline",
    "timeline_entries",
    "archival_ledger",
    "relationship_events",
    "relationship_adjudications",
    "relationship_consequences",
    "narrative_tension_links",
    "persona_growth_proposals",
    "persona_compilation_proposals",
    "persona_manifests",
    "turn_records",
    "relationship_processing_runs",
    "persona_reflection_decisions",
)


def require_supported_version(
    compatibility: FormatCompatibility,
    detected_version: object,
) -> str:
    """Returns a supported string version or raises a format-specific error."""
    if not isinstance(detected_version, str) or not detected_version:
        raise ValueError(f"{compatibility.format_id} version must be a non-empty string")
    if detected_version not in compatibility.readable_versions:
        raise UnsupportedFormatError(
            f"unsupported {compatibility.format_id} version {detected_version!r}; "
            f"current reader is {compatibility.current_version!r}"
        )
    return detected_version


def _memory_pack_supports_v050a1_fields(version: str) -> bool:
    """Returns whether a supported wire version includes the v0.5.0a1 fields."""
    return version not in _MEMORY_PACK_PRE_V050A1_VERSIONS


def validate_memory_pack_envelope(data: object) -> Dict[str, Any]:
    """Validates the MemoryPack envelope before any nested model is constructed."""
    if not isinstance(data, dict):
        raise ValueError("MemoryPack root must be a JSON object")
    unknown_root = set(data) - MEMORY_PACK_ROOT_FIELDS
    if unknown_root:
        raise ValueError(
            "MemoryPack contains unknown root fields: "
            + ", ".join(sorted(str(item) for item in unknown_root))
        )
    if "metadata" not in data:
        raise ValueError("MemoryPack metadata is required")
    metadata = data["metadata"]
    if not isinstance(metadata, dict):
        raise ValueError("MemoryPack metadata must be a JSON object")
    metadata_fields = set(metadata)
    if metadata_fields != MEMORY_PACK_METADATA_FIELDS:
        missing = MEMORY_PACK_METADATA_FIELDS - metadata_fields
        unknown = metadata_fields - MEMORY_PACK_METADATA_FIELDS
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unknown " + ", ".join(sorted(str(item) for item in unknown)))
        raise ValueError("MemoryPack metadata fields are invalid: " + "; ".join(details))

    version = require_supported_version(MEMORY_PACK_FORMAT, metadata["version"])
    allowed_root_fields = (
        MEMORY_PACK_ROOT_FIELDS
        if _memory_pack_supports_v050a1_fields(version)
        else _MEMORY_PACK_LEGACY_ROOT_FIELDS
    )
    incompatible_root = set(data) - allowed_root_fields
    if incompatible_root:
        raise ValueError(
            f"MemoryPack version {version!r} contains fields introduced in "
            f"{MEMORY_PACK_FORMAT.current_version!r}: "
            + ", ".join(sorted(str(item) for item in incompatible_root))
        )
    for field_name in ("agent_id", "user_id", "exported_at"):
        value = metadata[field_name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"MemoryPack metadata {field_name} must be a non-empty string")

    core_memory = data.get("core_memory", "")
    if not isinstance(core_memory, str):
        raise ValueError("MemoryPack core_memory must be a string")
    relationship = data.get("relationship")
    if relationship is not None and not isinstance(relationship, dict):
        raise ValueError("MemoryPack relationship must be an object or null")
    for field_name in _MEMORY_PACK_OBJECT_COLLECTIONS:
        value = data.get(field_name, [])
        if not isinstance(value, list):
            raise ValueError(f"MemoryPack {field_name} must be an array")
        if any(not isinstance(item, dict) for item in value):
            raise ValueError(f"MemoryPack {field_name} must contain only objects")
    direct_event_ids = data.get("relationship_direct_event_ids", [])
    if not isinstance(direct_event_ids, list) or any(
        not isinstance(item, str) for item in direct_event_ids
    ):
        raise ValueError("MemoryPack relationship_direct_event_ids must contain only strings")
    return metadata


def decode_memory_pack_json(json_text: str) -> Dict[str, Any]:
    """Decodes JSON while rejecting duplicate object fields at every level."""
    if not isinstance(json_text, str):
        raise ValueError("MemoryPack JSON input must be a string")

    def reject_duplicate_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"MemoryPack JSON contains duplicate field {key!r}")
            result[key] = value
        return result

    decoded = json.loads(json_text, object_pairs_hook=reject_duplicate_fields)
    validate_memory_pack_envelope(decoded)
    return decoded


__all__ = [
    "COMPATIBILITY_CATALOG",
    "CompatibilityCatalog",
    "FILE_STORAGE_FORMAT",
    "FormatCompatibility",
    "LIFECYCLE_BACKUP_FORMAT",
    "LIFECYCLE_PLAN_FORMAT",
    "MEMORY_PACK_FORMAT",
    "MEMORY_PACK_METADATA_FIELDS",
    "MEMORY_PACK_ROOT_FIELDS",
    "SQLITE_FORMAT",
    "decode_memory_pack_json",
    "require_supported_version",
    "validate_memory_pack_envelope",
]
