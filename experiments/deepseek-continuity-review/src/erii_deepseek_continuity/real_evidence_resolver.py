"""Scoped evidence resolution against E.R.I.I.'s public storage contract.

The experiment intentionally supports only two evidence kinds here:
``persona_claim`` and ``memory_node``.  Other kinds fail closed rather than
being represented by text that looks like real evidence.
"""

from typing import Protocol, Sequence

from erii.models.archival import archival_artifact_fingerprint
from erii.models.continuity import ContinuityEvidenceRef, VoicePatternActivation
from erii.models.continuity_evidence import ContinuityEvidenceKind
from erii.storage import BaseStorage, FileStorage, SQLiteStorage

from .evidence_resolver import (
    CrossRelationshipLeakError,
    EvidenceResolutionError,
    ResolvedEvidence,
    ResolvedVoiceActivation,
)


class StorageBackend(Protocol):
    """Small, experiment-owned read boundary over an E.R.I.I. storage adapter."""

    @property
    def relationship_id(self) -> str:
        """Return the single relationship this reader is bound to."""
        ...

    def read_persona_claim(
        self,
        manifest_id: str,
        content_fingerprint: str,
        claim_id: str,
    ) -> str:
        """Read one fingerprint-bound claim from the bound manifest."""
        ...

    def read_memory_content(
        self,
        relationship_id: str,
        node_id: str,
        artifact_fingerprint: str,
    ) -> str:
        """Read one fingerprint-bound memory node from the bound relationship."""
        ...


class ERIIStorageBackend:
    """A relationship-scoped reader implemented with public ``BaseStorage`` APIs.

    ``agent_id`` and ``user_id`` are required because memory nodes are loaded by
    that external pair in the stable storage contract.  Construction verifies
    that the pair maps to exactly ``relationship_id``; every later read remains
    within that immutable scope.
    """

    def __init__(
        self,
        storage: BaseStorage,
        *,
        agent_id: str,
        user_id: str,
        relationship_id: str,
    ) -> None:
        self._storage = storage
        self._agent_id = agent_id
        self._user_id = user_id
        self._relationship_id = relationship_id

        scope_error = False
        try:
            profile = storage.get_relationship(agent_id, user_id)
        except Exception:
            scope_error = True
            profile = None
        if scope_error:
            raise EvidenceResolutionError("relationship_scope_unavailable")
        if profile is None or profile.relationship_id != relationship_id:
            raise CrossRelationshipLeakError("relationship_scope_mismatch") from None
        self._profile = profile

    @property
    def relationship_id(self) -> str:
        return self._relationship_id

    def read_persona_claim(
        self,
        manifest_id: str,
        content_fingerprint: str,
        claim_id: str,
    ) -> str:
        if self._profile.manifest_id != manifest_id:
            raise EvidenceResolutionError("persona_manifest_not_bound") from None
        manifest_error = False
        try:
            manifest = self._storage.get_persona_manifest(manifest_id)
        except Exception:
            manifest_error = True
            manifest = None
        if manifest_error:
            raise EvidenceResolutionError("persona_manifest_unavailable")
        if manifest is None or manifest.content_fingerprint != content_fingerprint:
            raise EvidenceResolutionError("persona_manifest_fingerprint_mismatch") from None

        matches = [claim for claim in manifest.claims if claim.claim_id == claim_id]
        if len(matches) != 1:
            raise EvidenceResolutionError("persona_claim_unavailable") from None
        return matches[0].statement

    def read_memory_content(
        self,
        relationship_id: str,
        node_id: str,
        artifact_fingerprint: str,
    ) -> str:
        if relationship_id != self._relationship_id:
            raise CrossRelationshipLeakError("relationship_scope_mismatch") from None
        collection_error = False
        try:
            nodes = self._storage.load_nodes(self._agent_id, self._user_id)
        except Exception:
            collection_error = True
            nodes = []
        if collection_error:
            raise EvidenceResolutionError("memory_collection_unavailable")

        matches = [node for node in nodes if node.node_id == node_id]
        if len(matches) != 1:
            raise EvidenceResolutionError("memory_node_unavailable") from None
        node = matches[0]
        if node.relationship_id != self._relationship_id:
            raise CrossRelationshipLeakError("relationship_scope_mismatch") from None
        if archival_artifact_fingerprint(node) != artifact_fingerprint:
            raise EvidenceResolutionError("memory_fingerprint_mismatch") from None
        return node.content


class RealEvidenceResolver:
    """Resolve an explicit request whitelist through a scoped storage reader."""

    def __init__(self, storage_backend: StorageBackend) -> None:
        self._storage = storage_backend

    def resolve(
        self,
        persona_refs: Sequence[ContinuityEvidenceRef],
        relationship_refs: Sequence[ContinuityEvidenceRef],
        relationship_id: str,
    ) -> Sequence[ResolvedEvidence]:
        if relationship_id != self._storage.relationship_id:
            raise CrossRelationshipLeakError("relationship_scope_mismatch") from None
        return tuple(
            [self._resolve_persona_ref(ref) for ref in persona_refs]
            + [self._resolve_relationship_ref(ref, relationship_id) for ref in relationship_refs]
        )

    def resolve_voice_activations(
        self,
        activations: Sequence[VoicePatternActivation],
    ) -> Sequence[ResolvedVoiceActivation]:
        return tuple(
            ResolvedVoiceActivation(
                activation_id=activation.activation_id,
                pattern_id=activation.pattern_id,
                condition_ids=activation.condition_ids,
            )
            for activation in activations
        )

    def _resolve_persona_ref(self, ref: ContinuityEvidenceRef) -> ResolvedEvidence:
        if ref.kind != ContinuityEvidenceKind.PERSONA_CLAIM:
            raise EvidenceResolutionError("unsupported_persona_evidence_kind") from None
        resolution_error = False
        try:
            content = self._storage.read_persona_claim(
                ref.locator["manifest_id"],
                ref.locator["content_fingerprint"],
                ref.locator["claim_id"],
            )
        except (CrossRelationshipLeakError, EvidenceResolutionError):
            raise
        except Exception:
            resolution_error = True
            content = ""
        if resolution_error:
            raise EvidenceResolutionError("persona_evidence_resolution_failed")
        return ResolvedEvidence(ref.ref_id, ref.kind.value, content[:200])

    def _resolve_relationship_ref(
        self,
        ref: ContinuityEvidenceRef,
        relationship_id: str,
    ) -> ResolvedEvidence:
        if ref.locator.get("relationship_id") != relationship_id:
            raise CrossRelationshipLeakError("relationship_scope_mismatch") from None
        if ref.kind != ContinuityEvidenceKind.MEMORY_NODE:
            raise EvidenceResolutionError("unsupported_relationship_evidence_kind") from None
        resolution_error = False
        try:
            content = self._storage.read_memory_content(
                relationship_id,
                ref.locator["node_id"],
                ref.locator["artifact_fingerprint"],
            )
        except (CrossRelationshipLeakError, EvidenceResolutionError):
            raise
        except Exception:
            resolution_error = True
            content = ""
        if resolution_error:
            raise EvidenceResolutionError("relationship_evidence_resolution_failed")
        return ResolvedEvidence(ref.ref_id, ref.kind.value, content[:200])


class FileStorageAdapter(ERIIStorageBackend):
    """Typed convenience wrapper for ``FileStorage``; all reads are real."""

    def __init__(
        self,
        file_storage: FileStorage,
        *,
        agent_id: str,
        user_id: str,
        relationship_id: str,
    ) -> None:
        super().__init__(
            file_storage,
            agent_id=agent_id,
            user_id=user_id,
            relationship_id=relationship_id,
        )


class SQLiteStorageAdapter(ERIIStorageBackend):
    """Typed convenience wrapper for ``SQLiteStorage``; all reads are real."""

    def __init__(
        self,
        sqlite_storage: SQLiteStorage,
        *,
        agent_id: str,
        user_id: str,
        relationship_id: str,
    ) -> None:
        super().__init__(
            sqlite_storage,
            agent_id=agent_id,
            user_id=user_id,
            relationship_id=relationship_id,
        )
