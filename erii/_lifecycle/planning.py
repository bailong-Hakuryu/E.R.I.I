"""Zero-write Request-to-Plan lifecycle orchestration."""

from __future__ import annotations

from pathlib import Path

from erii._lifecycle.contracts import (
    BackupRequest,
    EraseRequest,
    LifecycleAssessment,
    LifecycleContentIdentity,
    LifecycleDirectoryIdentity,
    LifecycleOperation,
    LifecyclePlan,
    LifecyclePlanSelector,
    LifecycleRequest,
    LifecycleStatus,
    LifecycleTarget,
    MemoryPackImportOptions,
    MemoryPackImportRequest,
    RebuildRequest,
    RestoreRequest,
    UpgradeRequest,
)
from erii._lifecycle.erasure_inspection import inspect_erasure_scope
from erii._lifecycle.filesystem import directory_identity
from erii._lifecycle.inspection import (
    LifecycleInspector,
    require_complete_bundle,
)
from erii._lifecycle.memory_pack_validation import (
    validate_memory_pack_semantic_graph,
)
from erii._lifecycle.plan_codec import (
    BACKUP_STRATEGY_ID,
    RESTORE_STRATEGY_ID,
    erasure_storage_kind,
    erasure_strategy_id,
    import_strategy_id,
    sha256_json,
    upgrade_strategy_id,
    validate_assessment,
)
from erii._lifecycle.serializers import (
    assessment_to_dict,
    content_to_dict,
    directory_identity_to_dict,
    selector_to_dict,
)
from erii._lifecycle.snapshots import capture_snapshot, materialize_snapshot
from erii._lifecycle.topology import (
    require_destinations_do_not_overlap,
    require_safe_destination,
)
from erii._lifecycle.upgrade_preview import upgrade_snapshot
from erii.compatibility import LIFECYCLE_PLAN_FORMAT
from erii.errors import (
    LifecycleConflictError,
    LifecyclePlanError,
    StaleLifecyclePlanError,
    StorageIntegrityError,
)
from erii.lifecycle_erasure_contracts import (
    ErasureScope,
    ErasureSelectionError,
)
from erii.models.pack import MemoryPack


LIFECYCLE_PLAN_CONTRACT_VERSION = LIFECYCLE_PLAN_FORMAT.current_version


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
        "source": assessment_to_dict(source),
        "destination": assessment_to_dict(destination),
        "destination_parent": directory_identity_to_dict(destination_parent),
        "content": content_to_dict(content),
        "strategy_id": strategy_id,
        "backup_destination": (
            None
            if backup_destination is None
            else assessment_to_dict(backup_destination)
        ),
        "backup_destination_parent": (
            None
            if backup_destination_parent is None
            else directory_identity_to_dict(backup_destination_parent)
        ),
        "selector": selector_to_dict(selector),
    }
    operation_id = sha256_json(intent)
    plan_digest = sha256_json({**intent, "operation_id": operation_id})
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


class LifecyclePlanner:
    """Deep zero-write Module that freezes one canonical lifecycle plan."""

    def __init__(self, inspector: LifecycleInspector) -> None:
        if not isinstance(inspector, LifecycleInspector):
            raise TypeError("LifecyclePlanner requires a LifecycleInspector")
        self._inspector = inspector

    def _inspect(self, target: LifecycleTarget) -> LifecycleAssessment:
        assessment = self._inspector.inspect(target)
        validate_assessment(assessment)
        return assessment

    def plan(self, request: LifecycleRequest) -> LifecyclePlan:
        """Freeze a zero-write, strictly serializable lifecycle plan."""
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

    def _plan_backup(self, request: BackupRequest) -> LifecyclePlan:
        validate_assessment(request.source)
        current_source = self._inspect(request.source.target)
        if request.source != current_source:
            raise StaleLifecyclePlanError("backup source changed before planning")
        if current_source.status is LifecycleStatus.MISSING:
            raise LifecyclePlanError("missing lifecycle data cannot be backed up")
        destination = self._inspect(request.destination)
        if destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError("backup destination must not already exist")
        destination_parent = require_safe_destination(
            source=current_source.target,
            destination=destination.target,
        )
        return _make_plan(
            operation=LifecycleOperation.BACKUP,
            source=current_source,
            destination=destination,
            destination_parent=destination_parent,
            content=LifecycleContentIdentity.from_assessment(current_source),
            strategy_id=BACKUP_STRATEGY_ID,
        )

    def _plan_restore(self, request: RestoreRequest) -> LifecyclePlan:
        validate_assessment(request.backup)
        current_bundle = require_complete_bundle(
            self._inspector.read_backup_bundle(request.backup.target)
        )
        if request.backup != current_bundle.assessment:
            raise StaleLifecyclePlanError("backup bundle changed before planning")
        destination = self._inspect(request.destination)
        if destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError(
                "this restore slice only publishes to a missing destination"
            )
        assert current_bundle.content is not None
        if destination.target.kind is not current_bundle.content.kind:
            raise LifecyclePlanError(
                "restore destination kind does not match backup content"
            )
        destination_parent = require_safe_destination(
            source=current_bundle.assessment.target,
            destination=destination.target,
        )
        return _make_plan(
            operation=LifecycleOperation.RESTORE,
            source=current_bundle.assessment,
            destination=destination,
            destination_parent=destination_parent,
            content=current_bundle.content,
            strategy_id=RESTORE_STRATEGY_ID,
        )

    def _plan_upgrade(self, request: UpgradeRequest) -> LifecyclePlan:
        validate_assessment(request.source)
        current_source = self._inspect(request.source.target)
        if request.source != current_source:
            raise StaleLifecyclePlanError("upgrade source changed before planning")
        if current_source.status is not LifecycleStatus.MIGRATION_REQUIRED:
            raise LifecyclePlanError("upgrade source does not require migration")
        strategy_id = upgrade_strategy_id(current_source)

        destination = self._inspect(request.destination)
        if destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError("upgrade destination must not already exist")
        backup_destination = self._inspect(request.backup_destination)
        if backup_destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError(
                "upgrade backup destination must not already exist"
            )
        destination_parent = require_safe_destination(
            source=current_source.target,
            destination=destination.target,
        )
        backup_destination_parent = require_safe_destination(
            source=current_source.target,
            destination=backup_destination.target,
        )
        require_destinations_do_not_overlap(
            destination.target,
            backup_destination.target,
        )

        source_snapshot = capture_snapshot(current_source)
        final_source = self._inspect(current_source.target)
        if current_source != final_source:
            raise StaleLifecyclePlanError("upgrade source changed during planning")
        upgraded = upgrade_snapshot(strategy_id, source_snapshot)
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
        validate_assessment(request.source)
        current_source = self._inspect(request.source.target)
        if request.source != current_source:
            raise StaleLifecyclePlanError(
                "lifecycle mutation source changed before planning"
            )
        if current_source.status is not LifecycleStatus.CURRENT:
            raise LifecyclePlanError("erase and rebuild require current live storage")
        storage_kind = erasure_storage_kind(current_source.target.kind)
        if (
            operation is LifecycleOperation.REBUILD
            and request.selector.scope is not ErasureScope.RELATIONSHIP
        ):
            raise LifecyclePlanError(
                "deterministic rebuild currently requires a relationship selector"
            )

        try:
            inspect_erasure_scope(
                current_source.target.path,
                storage_kind,
                request.selector,
            )
        except ErasureSelectionError as exc:
            raise LifecyclePlanError(
                "erasure selector does not match live storage"
            ) from exc
        final_source = self._inspect(current_source.target)
        if current_source != final_source:
            raise StaleLifecyclePlanError(
                "lifecycle mutation source changed during planning"
            )

        backup_destination = self._inspect(request.backup_destination)
        if backup_destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError(
                "erase and rebuild backup destination must not already exist"
            )
        try:
            destination_parent = directory_identity(
                Path(current_source.target.path).parent
            )
        except StorageIntegrityError as exc:
            raise LifecyclePlanError("lifecycle source parent is unsafe") from exc
        backup_destination_parent = require_safe_destination(
            source=current_source.target,
            destination=backup_destination.target,
        )
        return _make_plan(
            operation=operation,
            source=current_source,
            destination=current_source,
            destination_parent=destination_parent,
            content=LifecycleContentIdentity.from_assessment(current_source),
            strategy_id=erasure_strategy_id(operation, current_source.target.kind),
            backup_destination=backup_destination,
            backup_destination_parent=backup_destination_parent,
            selector=request.selector,
        )

    def _plan_memory_pack_import(
        self,
        request: MemoryPackImportRequest,
    ) -> LifecyclePlan:
        validate_assessment(request.source)
        current_source = self._inspect(request.source.target)
        if request.source != current_source:
            raise StaleLifecyclePlanError("MemoryPack source changed before planning")
        if current_source.status not in {
            LifecycleStatus.CURRENT,
            LifecycleStatus.MIGRATION_REQUIRED,
        }:
            raise LifecyclePlanError("MemoryPack import source must be readable")
        destination = self._inspect(request.destination)
        if destination.status is not LifecycleStatus.MISSING:
            raise LifecycleConflictError("MemoryPack import destination must not exist")
        destination_parent = require_safe_destination(
            source=current_source.target,
            destination=destination.target,
        )
        snapshot = materialize_snapshot(capture_snapshot(current_source))
        assert snapshot.files is not None
        try:
            pack = MemoryPack.from_json(
                snapshot.files["memory-pack.erii"].decode("utf-8")
            )
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            raise StorageIntegrityError(
                "MemoryPack import source is malformed"
            ) from exc
        validate_memory_pack_semantic_graph(pack)
        final_source = self._inspect(current_source.target)
        if current_source != final_source:
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
            strategy_id=import_strategy_id(destination.target.kind),
            selector=options,
        )


__all__ = ["LifecyclePlanner"]
