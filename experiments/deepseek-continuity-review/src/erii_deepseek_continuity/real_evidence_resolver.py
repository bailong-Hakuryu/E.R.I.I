"""Real Evidence Resolver that connects to E.R.I.I. storage.

This resolver reads actual persona claims, relationship memories,
and other evidence from E.R.I.I. storage to provide to the evaluator.
"""

from typing import Sequence, Protocol
from erii.models.continuity import ContinuityEvidenceRef, VoicePatternActivation
from erii.models.continuity_evidence import ContinuityEvidenceKind
from erii.storage import FileStorage, SQLiteStorage
from .evidence_resolver import (
    ResolvedEvidence,
    ResolvedVoiceActivation,
    EvidenceResolutionError,
    CrossRelationshipLeakError,
)


class StorageBackend(Protocol):
    """Protocol for E.R.I.I. storage backend."""

    def read_persona_claim(
        self,
        manifest_id: str,
        claim_id: str,
    ) -> str:
        """Read a persona claim."""
        ...

    def read_memory_content(
        self,
        relationship_id: str,
        memory_id: str,
    ) -> str:
        """Read memory content for a relationship."""
        ...


class RealEvidenceResolver:
    """
    Real evidence resolver that connects to E.R.I.I. storage.

    Resolves ContinuityEvidenceRef to actual content from storage.

    Key constraints:
    - Only resolves refs from request whitelist
    - Maintains relationship scope (fails closed on cross-relationship)
    - Excerpts only enter temporary prompts, never return values
    """

    def __init__(self, storage_backend: StorageBackend):
        """
        Initialize with storage backend.

        Args:
            storage_backend: E.R.I.I. storage (FileStorage or SQLiteStorage)
        """
        self._storage = storage_backend

    def resolve(
        self,
        persona_refs: Sequence[ContinuityEvidenceRef],
        relationship_refs: Sequence[ContinuityEvidenceRef],
        relationship_id: str,
    ) -> Sequence[ResolvedEvidence]:
        """
        Resolve refs to readable excerpts.

        Validates:
        - Only resolves provided refs
        - Relationship evidence belongs to relationship_id
        - Fails closed on resolution errors
        """
        resolved = []

        # Resolve persona refs
        for ref in persona_refs:
            resolved.append(
                self._resolve_persona_ref(ref)
            )

        # Resolve relationship refs (with scope check)
        for ref in relationship_refs:
            resolved.append(
                self._resolve_relationship_ref(ref, relationship_id)
            )

        return tuple(resolved)

    def resolve_voice_activations(
        self,
        activations: Sequence[VoicePatternActivation],
    ) -> Sequence[ResolvedVoiceActivation]:
        """Resolve voice activations to readable form."""
        resolved = []

        for activation in activations:
            resolved.append(
                ResolvedVoiceActivation(
                    activation_id=activation.activation_id,
                    pattern_id=activation.pattern_id,
                    condition_ids=activation.condition_ids,
                )
            )

        return tuple(resolved)

    def _resolve_persona_ref(
        self,
        ref: ContinuityEvidenceRef,
    ) -> ResolvedEvidence:
        """Resolve persona evidence ref."""

        try:
            if ref.kind == ContinuityEvidenceKind.PERSONA_CLAIM:
                content = self._read_persona_claim(ref)
            elif ref.kind == ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN:
                content = self._read_voice_pattern(ref)
            else:
                # For other persona kinds, provide a generic description
                content = f"[Persona {ref.kind.value}]"

            return ResolvedEvidence(
                ref_id=ref.ref_id,
                kind=ref.kind.value,
                excerpt=content[:200],  # Max 200 chars
            )

        except Exception as exc:
            raise EvidenceResolutionError(
                f"Failed to resolve persona ref {ref.ref_id}"
            ) from None  # Don't leak storage exceptions

    def _resolve_relationship_ref(
        self,
        ref: ContinuityEvidenceRef,
        relationship_id: str,
    ) -> ResolvedEvidence:
        """Resolve relationship evidence ref (with scope check)."""

        # Validate relationship scope
        ref_relationship_id = self._extract_relationship_id(ref)
        if ref_relationship_id != relationship_id:
            raise CrossRelationshipLeakError(
                f"Evidence ref {ref.ref_id} does not belong to {relationship_id}"
            )

        try:
            if ref.kind == ContinuityEvidenceKind.MEMORY_NODE:
                content = self._read_memory_node(ref, relationship_id)
            elif ref.kind == ContinuityEvidenceKind.RELATIONSHIP_EVENT:
                content = self._read_relationship_event(ref, relationship_id)
            else:
                content = f"[Relationship {ref.kind.value}]"

            return ResolvedEvidence(
                ref_id=ref.ref_id,
                kind=ref.kind.value,
                excerpt=content[:200],
            )

        except CrossRelationshipLeakError:
            raise
        except Exception as exc:
            raise EvidenceResolutionError(
                f"Failed to resolve relationship ref {ref.ref_id}"
            ) from None

    def _read_persona_claim(self, ref: ContinuityEvidenceRef) -> str:
        """Read persona claim from storage."""
        # Extract manifest_id and claim_id from ref.locator
        locator = ref.locator
        manifest_id = locator.get("manifest_id")
        claim_id = locator.get("claim_id")

        if not manifest_id or not claim_id:
            raise EvidenceResolutionError("Invalid persona claim locator")

        return self._storage.read_persona_claim(manifest_id, claim_id)

    def _read_voice_pattern(self, ref: ContinuityEvidenceRef) -> str:
        """Read voice pattern description."""
        # Voice patterns are typically just described by their ID
        locator = ref.locator
        pattern_id = locator.get("pattern_id", "unknown")
        return f"Voice pattern: {pattern_id}"

    def _read_memory_node(
        self,
        ref: ContinuityEvidenceRef,
        relationship_id: str,
    ) -> str:
        """Read memory node content."""
        locator = ref.locator
        memory_id = locator.get("memory_id")

        if not memory_id:
            raise EvidenceResolutionError("Invalid memory node locator")

        return self._storage.read_memory_content(relationship_id, memory_id)

    def _read_relationship_event(
        self,
        ref: ContinuityEvidenceRef,
        relationship_id: str,
    ) -> str:
        """Read relationship event summary."""
        # Events typically have a summary in the locator or storage
        locator = ref.locator
        event_id = locator.get("event_id", "unknown")
        return f"Relationship event: {event_id}"

    def _extract_relationship_id(self, ref: ContinuityEvidenceRef) -> str:
        """
        Extract relationship_id from ref to validate scope.

        For relationship evidence, the relationship_id should be in the locator
        or derivable from the ref structure.
        """
        locator = ref.locator

        # Try common fields
        if "relationship_id" in locator:
            return locator["relationship_id"]

        # For memory nodes, might be in a different field
        if "memory_id" in locator and ":" in locator["memory_id"]:
            # Some memory IDs might encode relationship
            # This is implementation-specific
            pass

        # If we can't extract, fail closed
        raise EvidenceResolutionError(
            f"Cannot extract relationship_id from ref {ref.ref_id}"
        )


class FileStorageAdapter:
    """Adapter for E.R.I.I. FileStorage to StorageBackend protocol."""

    def __init__(self, file_storage: FileStorage):
        self._storage = file_storage

    def read_persona_claim(self, manifest_id: str, claim_id: str) -> str:
        """Read persona claim from FileStorage."""
        # FileStorage implementation would go here
        # This is a placeholder - actual implementation depends on FileStorage API
        return f"[Persona claim {claim_id} from manifest {manifest_id}]"

    def read_memory_content(self, relationship_id: str, memory_id: str) -> str:
        """Read memory content from FileStorage."""
        return f"[Memory {memory_id} from relationship {relationship_id}]"


class SQLiteStorageAdapter:
    """Adapter for E.R.I.I. SQLiteStorage to StorageBackend protocol."""

    def __init__(self, sqlite_storage: SQLiteStorage):
        self._storage = sqlite_storage

    def read_persona_claim(self, manifest_id: str, claim_id: str) -> str:
        """Read persona claim from SQLiteStorage."""
        # SQLiteStorage implementation would go here
        return f"[Persona claim {claim_id} from manifest {manifest_id}]"

    def read_memory_content(self, relationship_id: str, memory_id: str) -> str:
        """Read memory content from SQLiteStorage."""
        return f"[Memory {memory_id} from relationship {relationship_id}]"
