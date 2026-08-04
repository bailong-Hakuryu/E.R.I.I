"""Narrow compatibility rules for results written by older MemoryPack importers."""

from typing import Any, Dict

from erii.models.pack import MemoryPack
from erii.models.persona import (
    PersonaCompilationProposal,
    PersonaCompilationStatus,
)


_LEGACY_REASON_LOSS_STATUSES = frozenset(
    {
        PersonaCompilationStatus.APPROVED,
        PersonaCompilationStatus.REVOKED,
    }
)


def has_legacy_persona_decision_reason_loss(
    existing: PersonaCompilationProposal,
    incoming: PersonaCompilationProposal,
) -> bool:
    """Recognizes the one-way information loss caused by the old importer."""
    return (
        existing.status == incoming.status
        and existing.status in _LEGACY_REASON_LOSS_STATUSES
        and existing.decided_by == incoming.decided_by
        and existing.decided_at == incoming.decided_at
        and existing.decision_reason is None
        and incoming.decision_reason is not None
    )


def memory_pack_matches_legacy_persona_reason_loss(
    existing: MemoryPack,
    desired: MemoryPack,
) -> bool:
    """Allows only old-target ``None`` versus desired non-empty review reasons."""
    existing_document = _portable_document(existing)
    desired_document = _portable_document(desired)
    if existing_document == desired_document:
        return True

    existing_proposals = existing.persona_compilation_proposals
    desired_proposals = desired.persona_compilation_proposals
    if len(existing_proposals) != len(desired_proposals):
        return False

    for index, (existing_proposal, desired_proposal) in enumerate(
        zip(existing_proposals, desired_proposals)
    ):
        if existing_proposal.to_dict() == desired_proposal.to_dict():
            continue
        if not has_legacy_persona_decision_reason_loss(
            existing_proposal,
            desired_proposal,
        ):
            return False
        normalized = dict(
            desired_document["persona_compilation_proposals"][index]
        )
        normalized["decision_reason"] = None
        desired_document["persona_compilation_proposals"][index] = normalized

    return existing_document == desired_document


def _portable_document(pack: MemoryPack) -> Dict[str, Any]:
    document = pack.to_dict()
    metadata = dict(document["metadata"])
    metadata.pop("exported_at", None)
    document["metadata"] = metadata
    return document


__all__ = [
    "has_legacy_persona_decision_reason_loss",
    "memory_pack_matches_legacy_persona_reason_loss",
]
