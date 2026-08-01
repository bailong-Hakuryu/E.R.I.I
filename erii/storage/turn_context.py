"""Ephemeral, coherent storage source for one Turn Context Baseline."""

from dataclasses import dataclass
from typing import Optional, Tuple

from erii.models.adjudication import (
    AdjudicationRecord,
    PersonaGrowthProposal,
    PersonaGrowthStatus,
)
from erii.models.persona import PersonaCompilationProposal, PersonaManifest
from erii.models.persona import PersonaCompilationStatus
from erii.models.relationship import (
    RelationshipEvent,
    RelationshipProfile,
)
from erii.models.turn_context import (
    TurnContextBaseline,
    history_prefix_fingerprint,
    persona_growth_content_fingerprint,
    premise_content_fingerprint,
)


@dataclass(frozen=True)
class TurnContextSourceSnapshot:
    """One storage-consistent source view used to construct a Turn baseline.

    This value is deliberately ephemeral.  It carries complete domain records
    so Core can validate authority and construct bounded portable references;
    the snapshot itself is not a wire or MemoryPack format.
    """

    profile: RelationshipProfile
    pinned_manifest: Optional[PersonaManifest]
    backing_compilation_proposal: Optional[PersonaCompilationProposal]
    approved_growth: Tuple[PersonaGrowthProposal, ...]
    direct_events: Tuple[RelationshipEvent, ...]
    adjudications: Tuple[AdjudicationRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.profile, RelationshipProfile):
            raise TypeError("snapshot profile must be a RelationshipProfile")
        if self.pinned_manifest is not None and not isinstance(
            self.pinned_manifest,
            PersonaManifest,
        ):
            raise TypeError("pinned_manifest must be a PersonaManifest or None")
        if self.backing_compilation_proposal is not None and not isinstance(
            self.backing_compilation_proposal,
            PersonaCompilationProposal,
        ):
            raise TypeError(
                "backing_compilation_proposal must be a "
                "PersonaCompilationProposal or None"
            )

        growth = tuple(self.approved_growth)
        direct_events = tuple(self.direct_events)
        adjudications = tuple(self.adjudications)
        if any(not isinstance(item, PersonaGrowthProposal) for item in growth):
            raise TypeError("approved_growth must contain PersonaGrowthProposal values")
        if any(not isinstance(item, RelationshipEvent) for item in direct_events):
            raise TypeError("direct_events must contain RelationshipEvent values")
        if any(not isinstance(item, AdjudicationRecord) for item in adjudications):
            raise TypeError("adjudications must contain AdjudicationRecord values")

        relationship_id = self.profile.relationship_id
        if any(item.relationship_id != relationship_id for item in growth):
            raise ValueError("Persona Growth snapshot crosses relationship authority")
        if any(item.status != PersonaGrowthStatus.APPROVED for item in growth):
            raise ValueError("approved_growth contains a proposal that is not approved")
        if any(item.relationship_id != relationship_id for item in direct_events):
            raise ValueError("direct-event snapshot crosses relationship authority")
        if any(
            item.receipt.relationship_id != relationship_id
            for item in adjudications
        ):
            raise ValueError("adjudication snapshot crosses relationship authority")

        manifest = self.pinned_manifest
        proposal = self.backing_compilation_proposal
        if manifest is None and proposal is not None:
            raise ValueError(
                "a backing compilation proposal requires its pinned Manifest"
            )
        if manifest is not None:
            if self.profile.manifest_id != manifest.manifest_id:
                raise ValueError("snapshot Manifest differs from relationship binding")
            if (
                manifest.blueprint_id != self.profile.blueprint.blueprint_id
                or manifest.blueprint_revision != self.profile.blueprint.revision
                or manifest.source_sha256 != self.profile.blueprint.source_sha256
            ):
                raise ValueError("snapshot Manifest differs from relationship Blueprint")
        if proposal is not None and manifest is not None:
            if (
                proposal.proposal_id != manifest.approved_proposal_id
                or proposal.revision != manifest.approved_revision
                or proposal.blueprint_id != manifest.blueprint_id
                or proposal.blueprint_revision != manifest.blueprint_revision
                or proposal.source_sha256 != manifest.source_sha256
                or proposal.content_fingerprint != manifest.content_fingerprint
            ):
                raise ValueError(
                    "snapshot compilation proposal does not back the pinned Manifest"
                )

        object.__setattr__(
            self,
            "approved_growth",
            tuple(
                sorted(
                    growth,
                    key=lambda item: (
                        item.created_at,
                        item.proposal_id,
                        item.revision,
                    ),
                )
            ),
        )
        object.__setattr__(self, "direct_events", direct_events)
        object.__setattr__(self, "adjudications", adjudications)


def validate_turn_context_baseline_authority(
    snapshot: TurnContextSourceSnapshot,
    baseline: TurnContextBaseline,
) -> None:
    """Fails unless one coherent source snapshot still authorizes a baseline.

    Later append-only history and newly approved growth remain deferred to the
    next Turn. Revocation or mutation of anything that already belonged to the
    opening baseline invalidates reviewed delivery.
    """
    profile = snapshot.profile
    if (
        baseline.relationship_id != profile.relationship_id
        or baseline.persona_id != profile.persona_id
        or baseline.blueprint.blueprint_id != profile.blueprint.blueprint_id
        or baseline.blueprint.revision != profile.blueprint.revision
        or baseline.blueprint.source_sha256 != profile.blueprint.source_sha256
        or baseline.premise.premise_id != profile.premise.premise_id
        or baseline.premise.content_fingerprint
        != premise_content_fingerprint(profile.premise.to_dict())
    ):
        raise ValueError("Turn Context Baseline authority no longer resolves exactly")

    manifest_ref = baseline.manifest
    manifest = snapshot.pinned_manifest
    proposal = snapshot.backing_compilation_proposal
    if manifest_ref is not None and (
        manifest is None
        or proposal is None
        or proposal.status != PersonaCompilationStatus.APPROVED
        or manifest.manifest_id != manifest_ref.manifest_id
        or manifest.content_fingerprint != manifest_ref.content_fingerprint
    ):
        raise ValueError("the Turn Context Manifest authority is no longer approved")

    growth_by_id = {
        (item.proposal_id, item.revision): item
        for item in snapshot.approved_growth
    }
    for reference in baseline.approved_growth_refs:
        growth = growth_by_id.get((reference.proposal_id, reference.revision))
        if (
            growth is None
            or growth.relationship_id != profile.relationship_id
            or persona_growth_content_fingerprint(growth.to_dict())
            != reference.content_fingerprint
        ):
            raise ValueError(
                "Turn Context Persona Growth authority is no longer approved"
            )

    if (
        len(snapshot.direct_events) < baseline.direct_event_count
        or len(snapshot.adjudications) < baseline.adjudication_count
    ):
        raise ValueError("Turn Context history prefix is no longer available")
    direct_prefix = snapshot.direct_events[: baseline.direct_event_count]
    adjudication_prefix = snapshot.adjudications[: baseline.adjudication_count]
    if history_prefix_fingerprint(
        tuple(item.to_dict() for item in direct_prefix),
        tuple(item.to_dict() for item in adjudication_prefix),
    ) != baseline.history_prefix_fingerprint:
        raise ValueError("Turn Context history prefix fingerprint changed")


__all__ = [
    "TurnContextSourceSnapshot",
    "validate_turn_context_baseline_authority",
]
