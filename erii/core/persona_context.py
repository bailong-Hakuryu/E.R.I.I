"""Deterministic planning of source-anchored persona recall context."""

from typing import Dict, Iterable, List, Optional, Sequence, Set

from erii.core.retriever import MemoryRetriever
from erii.models.adjudication import PersonaGrowthProposal, PersonaGrowthStatus
from erii.models.persona import (
    PersonaActivationTier,
    PersonaApplicability,
    PersonaManifest,
)
from erii.models.recall import (
    PersonaDelivery,
    PersonaRecallContext,
    PersonaRecallProjection,
    RecallAudience,
    RecallSourceReference,
)
from erii.models.relationship import RelationshipProfile


class PersonaManifestRequiredError(ValueError):
    """Raised when planned delivery has no approved, relationship-pinned Manifest."""


class PersonaContextPlanner:
    """Plans a compact persona context while keeping authority layers separate."""

    @classmethod
    def plan(
        cls,
        profile: RelationshipProfile,
        manifest: Optional[PersonaManifest],
        growth_proposals: Sequence[PersonaGrowthProposal],
        *,
        query: str,
        delivery: PersonaDelivery,
        audience: RecallAudience,
    ) -> Optional[PersonaRecallContext]:
        """Returns deterministic persona projections without reading or writing storage."""
        if audience == RecallAudience.PUBLIC:
            return None
        if delivery == PersonaDelivery.PLANNED and manifest is None:
            raise PersonaManifestRequiredError(
                "planned persona recall requires an approved Manifest pinned to the relationship"
            )

        authority: List[PersonaRecallProjection] = []
        interpretations: List[PersonaRecallProjection] = []
        if delivery == PersonaDelivery.FULL:
            authority.append(
                PersonaRecallProjection(
                    projection_id=f"blueprint:{profile.blueprint.blueprint_id}:full",
                    source_id=profile.blueprint.blueprint_id,
                    source_kind="character_blueprint",
                    visibility=RecallAudience.AGENT_PRIVATE,
                    selection_reason="explicit_full_persona_delivery",
                    source_references=(
                        RecallSourceReference(
                            source_id=profile.blueprint.blueprint_id,
                            source_kind="character_blueprint",
                            source_revision=str(profile.blueprint.revision),
                            source_hash=profile.blueprint.source_sha256,
                            start=0,
                            end=len(profile.blueprint.source_text),
                        ),
                    ),
                    kind="full_blueprint_subordinate_to_host_policy",
                    content=profile.blueprint.source_text,
                    activation_tier=PersonaActivationTier.FOUNDATION.value,
                )
            )

        if manifest is not None:
            candidate = manifest.candidate
            span_by_id = {span.span_id: span for span in candidate.source_spans}
            claim_by_id = {claim.claim_id: claim for claim in candidate.claims}
            experience_by_id = {
                experience.experience_id: experience
                for experience in candidate.formative_experiences
            }
            capsule_by_id = {
                capsule.capsule_id: capsule for capsule in candidate.meaning_capsules
            }
            capsules_by_claim: Dict[str, List[object]] = {}
            for capsule in candidate.meaning_capsules:
                capsules_by_claim.setdefault(capsule.claim_id, []).append(capsule)
            link_by_id = {link.link_id: link for link in candidate.formative_links}
            template_by_id = {
                template.premise_template_id: template
                for template in candidate.premise_templates
            }
            query_tokens = MemoryRetriever.tokenize(query)

            selected_ids: Set[str] = set()
            required_ids: Set[str] = set()
            for claim in candidate.claims:
                if claim.applicability != PersonaApplicability.APPLICABLE:
                    continue
                if claim.activation_tier == PersonaActivationTier.FOUNDATION:
                    selected_ids.add(claim.claim_id)
                    required_ids.add(claim.claim_id)
                elif cls._is_relevant(query_tokens, claim.statement, claim.tags):
                    selected_ids.add(claim.claim_id)
            if delivery == PersonaDelivery.FULL:
                selected_ids.update(
                    claim.claim_id
                    for claim in candidate.claims
                    if claim.applicability == PersonaApplicability.APPLICABLE
                )
                selected_ids.update(experience_by_id)
                selected_ids.update(
                    capsule.capsule_id
                    for capsule in candidate.meaning_capsules
                    if claim_by_id[capsule.claim_id].applicability
                    == PersonaApplicability.APPLICABLE
                )

            for experience in candidate.formative_experiences:
                if experience.activation_tier == PersonaActivationTier.FOUNDATION:
                    selected_ids.add(experience.experience_id)
                    required_ids.add(experience.experience_id)
                elif cls._is_relevant(
                    query_tokens,
                    f"{experience.title} {experience.summary}",
                    experience.tags,
                ):
                    selected_ids.add(experience.experience_id)

            cls._expand_dependency_closure(
                selected_ids,
                claim_by_id,
                experience_by_id,
                capsule_by_id,
                capsules_by_claim,
                link_by_id,
                template_by_id,
            )
            cls._expand_dependency_closure(
                required_ids,
                claim_by_id,
                experience_by_id,
                capsule_by_id,
                capsules_by_claim,
                link_by_id,
                template_by_id,
            )
            selected_ids.update(required_ids)

            source_span_ids: Set[str] = set()
            for claim in candidate.claims:
                if claim.claim_id not in selected_ids:
                    continue
                tier = (
                    PersonaActivationTier.FOUNDATION.value
                    if claim.claim_id in required_ids
                    else claim.activation_tier.value
                )
                interpretations.append(
                    cls._projection(
                        manifest,
                        claim.claim_id,
                        "persona_claim",
                        claim.statement,
                        tier,
                        claim.source_span_ids,
                        span_by_id,
                    )
                )
                source_span_ids.update(claim.source_span_ids)
            for experience in candidate.formative_experiences:
                if experience.experience_id not in selected_ids:
                    continue
                interpretations.append(
                    cls._projection(
                        manifest,
                        experience.experience_id,
                        "formative_experience",
                        f"{experience.title}: {experience.summary}",
                        (
                            PersonaActivationTier.FOUNDATION.value
                            if experience.experience_id in required_ids
                            else experience.activation_tier.value
                        ),
                        experience.source_span_ids,
                        span_by_id,
                    )
                )
                source_span_ids.update(experience.source_span_ids)
            for link in candidate.formative_links:
                if link.link_id not in selected_ids:
                    continue
                interpretations.append(
                    cls._projection(
                        manifest,
                        link.link_id,
                        "formative_link",
                        (
                            f"{link.from_id} {link.relation.value} {link.to_id}; "
                            f"basis={link.basis.value}; scope={link.scope.value}"
                        ),
                        (
                            PersonaActivationTier.FOUNDATION.value
                            if link.link_id in required_ids
                            else PersonaActivationTier.SITUATIONAL.value
                        ),
                        link.source_span_ids,
                        span_by_id,
                    )
                )
                source_span_ids.update(link.source_span_ids)
            for capsule in candidate.meaning_capsules:
                if capsule.capsule_id not in selected_ids:
                    continue
                claim = claim_by_id[capsule.claim_id]
                tier = (
                    PersonaActivationTier.FOUNDATION.value
                    if capsule.capsule_id in required_ids
                    else claim.activation_tier.value
                )
                interpretations.append(
                    cls._projection(
                        manifest,
                        capsule.capsule_id,
                        "meaning_capsule",
                        capsule.meaning,
                        tier,
                        capsule.source_span_ids,
                        span_by_id,
                    )
                )
                source_span_ids.update(capsule.source_span_ids)
            for template in candidate.premise_templates:
                if template.premise_template_id not in selected_ids:
                    continue
                interpretations.append(
                    cls._projection(
                        manifest,
                        template.premise_template_id,
                        "canonical_premise_template",
                        (
                            f"{template.display_name}: counterpart_role="
                            f"{template.counterpart_role}; explicit binding required"
                        ),
                        (
                            PersonaActivationTier.FOUNDATION.value
                            if template.premise_template_id in required_ids
                            else PersonaActivationTier.REFERENCE.value
                        ),
                        template.source_span_ids,
                        span_by_id,
                    )
                )
                source_span_ids.update(template.source_span_ids)

            if delivery == PersonaDelivery.PLANNED:
                foundation_spans: Set[str] = set()
                for item in interpretations:
                    if item.activation_tier == PersonaActivationTier.FOUNDATION.value:
                        foundation_spans.update(
                            reference.source_id for reference in item.source_references
                        )
                for span_id in sorted(source_span_ids):
                    span = span_by_id[span_id]
                    tier = (
                        PersonaActivationTier.FOUNDATION.value
                        if span_id in foundation_spans
                        else PersonaActivationTier.SITUATIONAL.value
                    )
                    authority.append(
                        PersonaRecallProjection(
                            projection_id=f"persona-span:{span_id}",
                            source_id=span_id,
                            source_kind="character_blueprint_span",
                            visibility=RecallAudience.AGENT_PRIVATE,
                            selection_reason="source_anchor_for_selected_interpretation",
                            source_references=(
                                RecallSourceReference(
                                    source_id=span_id,
                                    source_kind="character_blueprint_span",
                                    source_revision=str(profile.blueprint.revision),
                                    source_hash=span.quote_sha256,
                                    start=span.start,
                                    end=span.end,
                                ),
                            ),
                            kind="source_span",
                            content=span.quote,
                            activation_tier=tier,
                        )
                    )

        growth = []
        for proposal in growth_proposals:
            if proposal.status != PersonaGrowthStatus.APPROVED:
                continue
            growth.append(
                PersonaRecallProjection(
                    projection_id=f"growth:{proposal.proposal_id}:{proposal.revision}",
                    source_id=proposal.proposal_id,
                    source_kind="approved_persona_growth",
                    visibility=RecallAudience.AGENT_PRIVATE,
                    selection_reason="approved_relationship_specific_growth",
                    kind="relationship_growth",
                    content=f"{proposal.statement}\n{proposal.rationale}",
                    activation_tier=PersonaActivationTier.SITUATIONAL.value,
                )
            )

        return PersonaRecallContext(
            delivery=delivery,
            blueprint_id=profile.blueprint.blueprint_id,
            blueprint_hash=profile.blueprint.source_sha256,
            manifest_id=manifest.manifest_id if manifest else None,
            manifest_revision=str(manifest.approved_revision) if manifest else None,
            authority_items=tuple(authority),
            interpretation_items=tuple(interpretations),
            approved_growth_items=tuple(growth),
        )

    @staticmethod
    def _is_relevant(query_tokens: Set[str], text: str, tags: Iterable[str]) -> bool:
        if not query_tokens:
            return False
        return bool(query_tokens.intersection(MemoryRetriever.tokenize(f"{text} {' '.join(tags)}")))

    @staticmethod
    def _expand_dependency_closure(
        entity_ids: Set[str],
        claim_by_id: Dict[str, object],
        experience_by_id: Dict[str, object],
        capsule_by_id: Dict[str, object],
        capsules_by_claim: Dict[str, List[object]],
        link_by_id: Dict[str, object],
        template_by_id: Dict[str, object],
    ) -> None:
        """Expands all graph entities needed to understand selected persona meaning."""
        pending = list(entity_ids)
        while pending:
            entity_id = pending.pop()
            additions: Set[str] = set()
            claim = claim_by_id.get(entity_id)
            if claim is not None:
                additions.update(claim.required_dependency_ids)
                additions.update(
                    capsule.capsule_id
                    for capsule in capsules_by_claim.get(entity_id, ())
                )
            capsule = capsule_by_id.get(entity_id)
            if capsule is not None:
                additions.add(capsule.claim_id)
                additions.update(capsule.experience_ids)
                additions.update(capsule.link_ids)
            link = link_by_id.get(entity_id)
            if link is not None:
                additions.add(link.from_id)
                additions.add(link.to_id)
            template = template_by_id.get(entity_id)
            if template is not None:
                additions.update(template.premise_experience_ids)

            # Referencing an experience is already closed: its approved summary
            # and source spans form one projection.
            additions.intersection_update(
                set(claim_by_id)
                | set(experience_by_id)
                | set(capsule_by_id)
                | set(link_by_id)
                | set(template_by_id)
            )
            new_ids = additions.difference(entity_ids)
            if new_ids:
                entity_ids.update(new_ids)
                pending.extend(new_ids)

    @staticmethod
    def _projection(
        manifest: PersonaManifest,
        item_id: str,
        kind: str,
        content: str,
        activation_tier: str,
        source_span_ids: Iterable[str],
        span_by_id: Dict[str, object],
    ) -> PersonaRecallProjection:
        references = []
        for span_id in source_span_ids:
            span = span_by_id[span_id]
            references.append(
                RecallSourceReference(
                    source_id=span_id,
                    source_kind="character_blueprint_span",
                    source_revision=str(manifest.blueprint_revision),
                    source_hash=span.quote_sha256,
                    start=span.start,
                    end=span.end,
                )
            )
        return PersonaRecallProjection(
            projection_id=f"persona:{item_id}",
            source_id=item_id,
            source_kind=kind,
            visibility=RecallAudience.AGENT_PRIVATE,
            selection_reason=f"persona_{activation_tier}",
            source_references=tuple(references),
            kind=kind,
            content=content,
            activation_tier=activation_tier,
        )


__all__ = ["PersonaContextPlanner", "PersonaManifestRequiredError"]
