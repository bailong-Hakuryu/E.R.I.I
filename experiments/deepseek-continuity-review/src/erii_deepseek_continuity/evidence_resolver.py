"""Evidence Resolver (experiment-internal).

Resolves ContinuityEvidenceRef to readable excerpts for prompt construction.

Key constraints:
- Only resolves refs from request whitelist
- Maintains relationship scope
- Fails closed on resolution errors
- Excerpts only enter temporary prompts, never return values
"""

from dataclasses import dataclass
from typing import Protocol, Sequence

from erii.models.continuity import ContinuityEvidenceRef, VoicePatternActivation


@dataclass(frozen=True)
class ResolvedEvidence:
    """Resolved evidence (temporary, for prompt only)."""
    ref_id: str
    kind: str
    excerpt: str  # Max 200 characters


@dataclass(frozen=True)
class ResolvedVoiceActivation:
    """Resolved voice activation (temporary, for prompt only)."""
    activation_id: str
    pattern_id: str
    condition_ids: tuple[str, ...]


class EvidenceResolver(Protocol):
    """Protocol for evidence resolution."""

    def resolve(
        self,
        persona_refs: Sequence[ContinuityEvidenceRef],
        relationship_refs: Sequence[ContinuityEvidenceRef],
        relationship_id: str,
    ) -> Sequence[ResolvedEvidence]:
        """Resolve refs to readable excerpts."""
        ...

    def resolve_voice_activations(
        self,
        activations: Sequence[VoicePatternActivation],
    ) -> Sequence[ResolvedVoiceActivation]:
        """Resolve voice activations to readable form."""
        ...


class FakeEvidenceResolver:
    """Fake evidence resolver for testing (does not access real storage)."""

    def resolve(
        self,
        persona_refs: Sequence[ContinuityEvidenceRef],
        relationship_refs: Sequence[ContinuityEvidenceRef],
        relationship_id: str,
    ) -> Sequence[ResolvedEvidence]:
        """Resolve to fake excerpts for testing."""
        resolved = []

        for ref in persona_refs:
            resolved.append(
                ResolvedEvidence(
                    ref_id=ref.ref_id,
                    kind=ref.kind.value,
                    excerpt=f"[TEST] Persona {ref.kind.value} content",
                )
            )

        for ref in relationship_refs:
            resolved.append(
                ResolvedEvidence(
                    ref_id=ref.ref_id,
                    kind=ref.kind.value,
                    excerpt=f"[TEST] Relationship {ref.kind.value} content",
                )
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


class EvidenceResolutionError(Exception):
    """Evidence resolution failed (contains no sensitive info)."""

    pass


class CrossRelationshipLeakError(Exception):
    """Attempted to resolve evidence from another relationship (security error)."""

    pass
