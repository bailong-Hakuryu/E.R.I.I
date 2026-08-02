"""Capture of the exact authority and relationship prefixes visible to a Turn."""

from __future__ import annotations

from erii.core.adjudication import relationship_events_from_journals
from erii.core.continuity import (
    InteractionContextEvaluationCoordinator,
    RelationshipSafetySignalProjector,
    VoicePatternMatcher,
)
from erii.models.adjudication import PersonaGrowthProposal
from erii.models.persona import PersonaCompilationStatus, PersonaManifest
from erii.models.relationship import RelationshipProfile
from erii.models.turn import TurnConflictError
from erii.models.turn_context import (
    TurnApprovedGrowthReference,
    TurnBlueprintReference,
    TurnContextBaseline,
    TurnManifestReference,
    TurnPremiseReference,
    history_prefix_fingerprint,
    persona_growth_content_fingerprint,
    premise_content_fingerprint,
)
from erii.storage.base import BaseStorage


RELATIONSHIP_HISTORY_PROJECTION_VERSION = "relationship-projector/v2"


def capture_turn_context_baseline(
    storage: BaseStorage,
    profile: RelationshipProfile,
    turn_id: str,
) -> TurnContextBaseline:
    """Captures bounded identities and journal prefixes without copying history."""
    snapshot = storage.capture_turn_context_source(profile)
    source_profile = snapshot.profile
    _require_same_immutable_profile(profile, source_profile)
    ensure_canonical_turn_identity_available(snapshot, profile, turn_id)
    manifest = _active_snapshot_manifest(snapshot)
    approved_growth = snapshot.approved_growth
    for proposal in approved_growth:
        if proposal.relationship_id != source_profile.relationship_id:
            raise ValueError("Persona Growth belongs to a different relationship")

    direct_events = snapshot.direct_events
    adjudications = snapshot.adjudications
    return TurnContextBaseline.create(
        relationship_id=source_profile.relationship_id,
        turn_id=turn_id,
        persona_id=source_profile.persona_id,
        blueprint=TurnBlueprintReference(
            blueprint_id=source_profile.blueprint.blueprint_id,
            revision=source_profile.blueprint.revision,
            source_sha256=source_profile.blueprint.source_sha256,
        ),
        manifest=(
            TurnManifestReference(
                manifest_id=manifest.manifest_id,
                content_fingerprint=manifest.content_fingerprint,
            )
            if manifest is not None
            else None
        ),
        approved_growth_refs=tuple(
            TurnApprovedGrowthReference(
                proposal_id=proposal.proposal_id,
                revision=proposal.revision,
                content_fingerprint=persona_growth_content_fingerprint(
                    proposal.to_dict()
                ),
            )
            for proposal in approved_growth
        ),
        premise=TurnPremiseReference(
            premise_id=source_profile.premise.premise_id,
            content_fingerprint=premise_content_fingerprint(
                source_profile.premise.to_dict()
            ),
        ),
        direct_event_count=len(direct_events),
        adjudication_count=len(adjudications),
        history_prefix_fingerprint=history_prefix_fingerprint(
            tuple(item.to_dict() for item in direct_events),
            tuple(item.to_dict() for item in adjudications),
        ),
        policy_versions={
            "relationship_baseline_policy": source_profile.baseline.policy_version,
            "relationship_history_projection": (
                RELATIONSHIP_HISTORY_PROJECTION_VERSION
            ),
            "relationship_safety_policy": RelationshipSafetySignalProjector.VERSION,
            "interaction_context_policy": (
                InteractionContextEvaluationCoordinator.VERSION
            ),
            "voice_matcher_policy": VoicePatternMatcher.VERSION,
        },
    )


def ensure_canonical_turn_identity_available(
    snapshot,
    profile: RelationshipProfile,
    turn_id: str,
) -> None:
    """Rejects promotion of a transient adjudication into canonical Turn data."""
    _require_same_immutable_profile(profile, snapshot.profile)
    if any(
        record.receipt.source_turn_id == turn_id
        for record in snapshot.adjudications
    ):
        raise TurnConflictError(
            "turn_id was already used by a transient relationship "
            "adjudication and cannot be promoted to a canonical Turn"
        )


def _require_same_immutable_profile(
    expected: RelationshipProfile,
    actual: RelationshipProfile,
) -> None:
    expected_payload = expected.to_dict()
    actual_payload = actual.to_dict()
    expected_payload.pop("manifest_id", None)
    actual_payload.pop("manifest_id", None)
    if expected_payload != actual_payload:
        raise ValueError("Turn Context profile changed before snapshot capture")


def _active_snapshot_manifest(snapshot) -> PersonaManifest | None:
    manifest = snapshot.pinned_manifest
    proposal = snapshot.backing_compilation_proposal
    if manifest is None or proposal is None:
        return None
    if proposal.status != PersonaCompilationStatus.APPROVED:
        return None
    return manifest


def resolve_turn_context_authorities(
    storage: BaseStorage,
    profile: RelationshipProfile,
    baseline: TurnContextBaseline,
) -> tuple[PersonaManifest | None, tuple[PersonaGrowthProposal, ...]]:
    """Resolves all revocable opening authority from one coherent snapshot."""
    snapshot = storage.capture_turn_context_source(profile)
    source_profile = snapshot.profile
    _require_same_immutable_profile(profile, source_profile)
    manifest_ref = baseline.manifest
    manifest = _active_snapshot_manifest(snapshot)
    if manifest_ref is None:
        resolved_manifest = None
    elif (
        manifest is None
        or manifest.manifest_id != manifest_ref.manifest_id
        or manifest.content_fingerprint != manifest_ref.content_fingerprint
    ):
        raise ValueError("the Turn Context Manifest authority is no longer approved")
    else:
        resolved_manifest = manifest

    growth_by_id = {
        (proposal.proposal_id, proposal.revision): proposal
        for proposal in snapshot.approved_growth
    }
    resolved_growth = []
    for reference in baseline.approved_growth_refs:
        proposal = growth_by_id.get((reference.proposal_id, reference.revision))
        if (
            proposal is None
            or proposal.relationship_id != source_profile.relationship_id
            or persona_growth_content_fingerprint(proposal.to_dict())
            != reference.content_fingerprint
        ):
            raise ValueError(
                "Turn Context Persona Growth authority is no longer approved"
            )
        resolved_growth.append(proposal)
    return resolved_manifest, tuple(resolved_growth)


def resolve_turn_context_manifest(
    storage: BaseStorage,
    profile: RelationshipProfile,
    baseline: TurnContextBaseline,
):
    """Resolves the exact frozen Manifest while honoring later revocation."""
    manifest, _ = resolve_turn_context_authorities(storage, profile, baseline)
    return manifest


def resolve_turn_context_approved_growth(
    storage: BaseStorage,
    profile: RelationshipProfile,
    baseline: TurnContextBaseline,
) -> tuple[PersonaGrowthProposal, ...]:
    """Resolves only the opening-time growth prefix and honors later revocation."""
    _, growth = resolve_turn_context_authorities(storage, profile, baseline)
    return growth


def resolve_turn_context_history(
    storage: BaseStorage,
    profile: RelationshipProfile,
    baseline: TurnContextBaseline,
):
    """Rebuilds exactly the two relationship journal prefixes frozen at opening."""
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
    direct_events = tuple(storage.list_relationship_events(profile.relationship_id))
    adjudications = tuple(
        storage.list_relationship_adjudications(profile.relationship_id)
    )
    if (
        len(direct_events) < baseline.direct_event_count
        or len(adjudications) < baseline.adjudication_count
    ):
        raise ValueError("Turn Context history prefix is no longer available")
    direct_prefix = direct_events[: baseline.direct_event_count]
    adjudication_prefix = adjudications[: baseline.adjudication_count]
    fingerprint = history_prefix_fingerprint(
        tuple(item.to_dict() for item in direct_prefix),
        tuple(item.to_dict() for item in adjudication_prefix),
    )
    if fingerprint != baseline.history_prefix_fingerprint:
        raise ValueError("Turn Context history prefix fingerprint changed")
    return tuple(
        relationship_events_from_journals(
            direct_prefix,
            adjudication_prefix,
        )
    )


__all__ = [
    "RELATIONSHIP_HISTORY_PROJECTION_VERSION",
    "capture_turn_context_baseline",
    "ensure_canonical_turn_identity_available",
    "resolve_turn_context_approved_growth",
    "resolve_turn_context_authorities",
    "resolve_turn_context_history",
    "resolve_turn_context_manifest",
]
