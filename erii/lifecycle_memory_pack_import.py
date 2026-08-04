"""Execute MemoryPack imports inside caller-provided isolated staging targets."""

import hashlib
import json
from pathlib import Path

from erii.core.memory_pack_import_compatibility import (
    memory_pack_matches_legacy_persona_reason_loss,
)
from erii.engine import ERIIEngine
from erii.lifecycle_memory_pack_import_contracts import (
    MemoryPackStagingAdapter,
    MemoryPackStagingImportReport,
    MemoryPackStagingImportRequest,
    STAGING_IMPORT_REPORT_FORMAT,
)
from erii.models.pack import MemoryPack
from erii.storage.file_storage import FileStorage
from erii.storage.sqlite_storage import SQLiteStorage


class MemoryPackStagingImporter:
    """Run production validation, import, and export only within staging."""

    def import_pack(
        self,
        request: MemoryPackStagingImportRequest,
    ) -> MemoryPackStagingImportReport:
        """Imports a pack and returns its content-free canonical receipt."""
        if not isinstance(request, MemoryPackStagingImportRequest):
            raise TypeError("request must be a MemoryPackStagingImportRequest")

        storage = self._open_storage(request.adapter, request.staging_path)
        engine = ERIIEngine(storage_driver=storage)
        target_agent = request.target_agent_id or request.pack.agent_id
        target_user = request.target_user_id or request.pack.user_id
        try:
            # The production import boundary performs the complete graph
            # preflight before it starts importing payload records.
            engine.import_memory(
                request.pack,
                agent_id=request.target_agent_id,
                user_id=request.target_user_id,
                overwrite=request.overwrite,
            )
            exported = engine.export_memory(target_agent, target_user)
        finally:
            engine.close()

        relationship_id = (
            exported.relationship.relationship_id
            if exported.relationship is not None
            else None
        )
        return _report_from_export(request.adapter, exported, relationship_id)

    def inspect_target(
        self,
        *,
        adapter: MemoryPackStagingAdapter,
        staging_path: str,
        agent_id: str,
        user_id: str,
    ) -> MemoryPackStagingImportReport:
        """Returns the same semantic receipt for an already-published target."""
        if not isinstance(adapter, MemoryPackStagingAdapter):
            raise TypeError("adapter must be a MemoryPackStagingAdapter")
        for label, value in (
            ("staging_path", staging_path),
            ("agent_id", agent_id),
            ("user_id", user_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string")
        exported = self._export_target(
            adapter=adapter,
            path=staging_path,
            agent_id=agent_id,
            user_id=user_id,
        )
        relationship_id = (
            exported.relationship.relationship_id
            if exported.relationship is not None
            else None
        )
        return _report_from_export(adapter, exported, relationship_id)

    def target_matches_import_with_legacy_compatibility(
        self,
        *,
        adapter: MemoryPackStagingAdapter,
        existing_path: str,
        desired_path: str,
        agent_id: str,
        user_id: str,
    ) -> bool:
        """Compares two private targets under the old importer loss rule."""
        existing = self._export_target(
            adapter=adapter,
            path=existing_path,
            agent_id=agent_id,
            user_id=user_id,
        )
        desired = self._export_target(
            adapter=adapter,
            path=desired_path,
            agent_id=agent_id,
            user_id=user_id,
        )
        return memory_pack_matches_legacy_persona_reason_loss(
            existing,
            desired,
        )

    def _export_target(
        self,
        *,
        adapter: MemoryPackStagingAdapter,
        path: str,
        agent_id: str,
        user_id: str,
    ) -> MemoryPack:
        storage = self._open_storage(adapter, path)
        engine = ERIIEngine(storage_driver=storage)
        try:
            return engine.export_memory(agent_id, user_id)
        finally:
            engine.close()

    @staticmethod
    def _open_storage(adapter: MemoryPackStagingAdapter, staging_path: str):
        path = Path(staging_path)
        if adapter == MemoryPackStagingAdapter.FILE_STORAGE:
            if path.exists() and not path.is_dir():
                raise ValueError("FileStorage staging_path must be a directory")
            return FileStorage(root_dir=str(path))
        if path.exists() and not path.is_file():
            raise ValueError("SQLite staging_path must be a file")
        return SQLiteStorage(db_path=str(path))


def _semantic_sha256(pack: MemoryPack) -> str:
    document = pack.to_dict()
    metadata = dict(document["metadata"])
    metadata.pop("exported_at", None)
    document["metadata"] = metadata
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _report_from_export(
    adapter: MemoryPackStagingAdapter,
    exported: MemoryPack,
    relationship_id: str | None,
) -> MemoryPackStagingImportReport:
    return MemoryPackStagingImportReport(
        adapter=adapter,
        agent_id=exported.agent_id,
        user_id=exported.user_id,
        relationship_id=relationship_id,
        semantic_sha256=_semantic_sha256(exported),
        counts=_content_free_counts(exported),
    )


def _content_free_counts(pack: MemoryPack) -> dict[str, int]:
    collection_fields = (
        "nodes",
        "timeline",
        "timeline_entries",
        "archival_ledger",
        "relationship_events",
        "relationship_direct_event_ids",
        "relationship_adjudications",
        "persona_growth_proposals",
        "persona_compilation_proposals",
        "persona_manifests",
        "turn_records",
        "relationship_processing_runs",
        "persona_reflection_decisions",
    )
    counts: dict[str, int] = {
        field_name: len(getattr(pack, field_name))
        for field_name in collection_fields
    }
    counts["relationships"] = int(pack.relationship is not None)
    counts["core_memory_present"] = int(bool(pack.core_memory))
    return counts


__all__ = [
    "MemoryPackStagingAdapter",
    "MemoryPackStagingImportReport",
    "MemoryPackStagingImportRequest",
    "MemoryPackStagingImporter",
    "STAGING_IMPORT_REPORT_FORMAT",
]
