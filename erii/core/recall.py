"""Deep structured-recall module with audience filtering and atomic budgets."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from erii.core.adjudication import list_complete_relationship_events
from erii.core.persona_context import PersonaContextPlanner
from erii.core.relationship import RelationshipProjector
from erii.core.retriever import MemoryRetriever
from erii.core.temporal import RecallSignalDeriver
from erii.models.node import MemoryNode, MemoryType, MemoryVisibility
from erii.models.persona import PersonaCompilationStatus, PersonaManifest
from erii.models.recall import (
    BudgetOmission,
    BudgetReport,
    EventRecallProjection,
    MemoryRecallProjection,
    PersonaDelivery,
    PersonaRecallContext,
    RecallAudience,
    RecallNotice,
    RecallNoticeSeverity,
    RecallRequest,
    RecallResult,
    RecallSignalAuthority,
    RecallSignalProjection,
    RecallSignalType,
    RecallSourceReference,
    ReinforcementReport,
    RelationshipMetric,
    RelationshipNarrativeProjection,
    RelationshipRecallContext,
    RelationshipRecallStatus,
)
from erii.models.relationship import RelationshipEvent, RelationshipSnapshot
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage
from erii.vector.base import BaseEmbeddingProvider, BaseVectorStore


RecallCostEstimator = Callable[[str], int]


def default_recall_cost(text: str) -> int:
    """Returns a deterministic dependency-free character cost."""
    return len(text)


class RecallBudgetUnsatisfiedError(ValueError):
    """Raised when required persona meaning cannot fit without truncation."""

    def __init__(self, required_cost: int, max_cost: int) -> None:
        self.required_cost = required_cost
        self.max_cost = max_cost
        super().__init__(
            "required recall context exceeds the atomic budget "
            f"(required={required_cost}, max={max_cost})"
        )


@dataclass(frozen=True)
class _Candidate:
    group: str
    value: object
    required: bool
    cost: int
    priority: int = 3


class RecallAssembler:
    """Assembles a complete RecallResult through one narrow Interface."""

    def __init__(
        self,
        storage: BaseStorage,
        retriever: MemoryRetriever,
        *,
        vector_store: Optional[BaseVectorStore] = None,
        embedding_provider: Optional[BaseEmbeddingProvider] = None,
        cost_estimator: Optional[RecallCostEstimator] = None,
    ) -> None:
        self.storage = storage
        self.retriever = retriever
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.cost_estimator = cost_estimator or default_recall_cost

    def assemble(
        self,
        request: RecallRequest,
        *,
        legacy_compat: bool = False,
    ) -> RecallResult:
        """Assembles, budgets, then optionally reinforces only selected memories."""
        clean_agent = SecuritySanitizer.validate_key(request.agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(request.user_id, "user_id")
        query = SecuritySanitizer.sanitize_text(request.query)
        notices: List[RecallNotice] = []

        try:
            profile = self.storage.get_relationship(clean_agent, clean_user)
        except NotImplementedError:
            profile = None

        persona_context: Optional[PersonaRecallContext] = None
        relationship_context: Optional[RelationshipRecallContext] = None
        relationship_events: Sequence[RelationshipEvent] = ()
        event_projections: List[EventRecallProjection] = []
        if profile is None:
            relationship_status = RelationshipRecallStatus.UNINITIALIZED
            notices.append(
                RecallNotice(
                    code="relationship_uninitialized",
                    message=(
                        "Legacy memories are available, but no relationship or persona "
                        "was created implicitly."
                    ),
                )
            )
        else:
            relationship_status = RelationshipRecallStatus.INITIALIZED
            relationship_events = list_complete_relationship_events(
                self.storage,
                profile.relationship_id,
            )
            snapshot = RelationshipProjector.project(
                profile,
                relationship_events,
                observed_at=request.temporal_context.observed_at,
            )
            premise_projection = None
            narratives: List[RelationshipNarrativeProjection] = []
            metrics: List[RelationshipMetric] = []
            if request.audience == RecallAudience.AGENT_PRIVATE and not legacy_compat:
                premise_bits = [f"mode={profile.premise.mode.value}"]
                if profile.premise.address_name:
                    premise_bits.append(f"address={profile.premise.address_name}")
                if profile.premise.canonical_role:
                    premise_bits.append(f"canonical_role={profile.premise.canonical_role}")
                premise_projection = RelationshipNarrativeProjection(
                    projection_id=f"premise:{profile.relationship_id}",
                    source_id=profile.premise.premise_id,
                    source_kind="relationship_premise",
                    visibility=RecallAudience.AGENT_PRIVATE,
                    selection_reason="immutable_relationship_start",
                    kind="relationship_premise",
                    content="; ".join(premise_bits),
                )
                for experience in profile.premise.experiences:
                    narratives.append(
                        RelationshipNarrativeProjection(
                            projection_id=f"premise-experience:{experience.experience_id}",
                            source_id=experience.experience_id,
                            source_kind="premise_experience",
                            visibility=RecallAudience.AGENT_PRIVATE,
                            selection_reason="explicit_canonical_continuation",
                            kind="premise_experience",
                            content=experience.summary,
                        )
                    )
                for dimension, value in snapshot.state.to_dict().items():
                    reason = snapshot.state_reasons.get(dimension)
                    metrics.append(
                        RelationshipMetric(
                            dimension=dimension,
                            value=value,
                            evidence_event_id=(reason.evidence_event_id if reason else None),
                        )
                    )
                event_projections = self._project_events(
                    relationship_events,
                    query,
                    request.options.top_k,
                )
                narratives.extend(
                    self._project_relationship_narratives(
                        relationship_events,
                        snapshot,
                        {item.source_id for item in event_projections},
                    )
                )

            relationship_context = RelationshipRecallContext(
                relationship_id=profile.relationship_id,
                persona_id=profile.persona_id,
                premise=premise_projection,
                narratives=tuple(narratives),
                internal_state=tuple(metrics),
            )

            if request.audience == RecallAudience.AGENT_PRIVATE and not legacy_compat:
                manifest = self._active_manifest(profile)
                growth = self.storage.list_persona_growth_proposals(profile.relationship_id)
                persona_context = PersonaContextPlanner.plan(
                    profile,
                    manifest,
                    growth,
                    query=query,
                    delivery=request.options.persona_delivery,
                    audience=request.audience,
                )
                if request.options.persona_delivery == PersonaDelivery.FULL:
                    notices.append(
                        RecallNotice(
                            code="character_source_is_subordinate",
                            message=(
                                "Full Character Blueprint text is character material only; "
                                "host safety, privacy, authorization, and tool policy remain higher."
                            ),
                            severity=RecallNoticeSeverity.WARNING,
                        )
                    )

        nodes = self.storage.load_nodes(clean_agent, clean_user)
        candidate_nodes = [MemoryNode.from_dict(node.to_dict()) for node in nodes]
        if request.audience == RecallAudience.PUBLIC:
            candidate_nodes = [
                node
                for node in candidate_nodes
                if node.visibility == MemoryVisibility.PUBLIC_LOG.value
                and node.node_type != MemoryType.INSTRUCTION
            ]
        else:
            candidate_nodes = [
                node for node in candidate_nodes if node.node_type != MemoryType.INSTRUCTION
            ]
        ranked_nodes = self.retriever.retrieve_relevant_nodes(
            query=query,
            all_nodes=candidate_nodes,
            top_k=request.options.top_k,
            max_per_type=request.options.max_per_type,
            vector_store=self.vector_store,
            embedding_provider=self.embedding_provider,
            reinforce=False,
            update_index=legacy_compat,
        )

        memory_projections: List[MemoryRecallProjection] = []
        node_projection_ids: Dict[str, str] = {}
        if request.audience == RecallAudience.AGENT_PRIVATE:
            core = self.storage.get_core_memory(clean_agent, clean_user)
            if core:
                memory_projections.append(
                    MemoryRecallProjection(
                        projection_id=f"legacy-core:{clean_agent}:{clean_user}",
                        source_id=f"legacy-core:{clean_agent}:{clean_user}",
                        source_kind="legacy_core_memory",
                        visibility=RecallAudience.AGENT_PRIVATE,
                        selection_reason="legacy_core_compatibility",
                        memory_type="core",
                        content=core,
                        source_visibility=MemoryVisibility.INTERNAL_MONOLOGUE.value,
                    )
                )
        for node in ranked_nodes:
            visibility = (
                RecallAudience.AGENT_PRIVATE
                if node.visibility == MemoryVisibility.INTERNAL_MONOLOGUE.value
                else request.audience
            )
            projection_id = f"memory:{node.node_id}"
            node_projection_ids[node.node_id] = projection_id
            memory_projections.append(
                MemoryRecallProjection(
                    projection_id=projection_id,
                    source_id=node.node_id,
                    source_kind="memory_node",
                    visibility=visibility,
                    selection_reason="relevance_and_diversity_rank",
                    memory_type=node.node_type.value,
                    content=node.content,
                    created_at=node.created_at or None,
                    source_visibility=node.visibility,
                )
            )
        if request.audience == RecallAudience.AGENT_PRIVATE:
            for index, entry in enumerate(
                self.storage.get_recent_timeline(clean_agent, clean_user, limit=4)
            ):
                timestamp, content = self._parse_timeline(entry)
                digest = hashlib.sha256(entry.encode("utf-8")).hexdigest()[:16]
                memory_projections.append(
                    MemoryRecallProjection(
                        projection_id=f"timeline:{index}:{digest}",
                        source_id=f"timeline:{digest}",
                        source_kind="experiential_timeline",
                        visibility=RecallAudience.AGENT_PRIVATE,
                        selection_reason="recent_experiential_timeline",
                        memory_type="timeline",
                        content=content,
                        created_at=timestamp,
                        source_visibility=MemoryVisibility.INTERNAL_MONOLOGUE.value,
                    )
                )

        signal_projections: List[RecallSignalProjection] = []
        if request.audience == RecallAudience.AGENT_PRIVATE and not legacy_compat:
            signal_projections = list(
                RecallSignalDeriver.derive(
                    relationship_events,
                    request.temporal_context.world_time,
                    candidate_nodes,
                )
            )

        (
            persona_context,
            relationship_context,
            selected_memories,
            selected_events,
            selected_signals,
            budget_report,
        ) = self._apply_budget(
            persona_context,
            relationship_context,
            memory_projections,
            event_projections,
            signal_projections,
            request.options.budget.max_cost,
        )

        reinforced_ids: List[str] = []
        if request.options.reinforce:
            selected_projection_ids = {
                projection.projection_id for projection in selected_memories
            }
            for node in nodes:
                projection_id = node_projection_ids.get(node.node_id)
                if projection_id is None or projection_id not in selected_projection_ids:
                    continue
                node.reinforce_recall(boost=0.08)
                reinforced_ids.append(node.node_id)
            if reinforced_ids:
                self.storage.save_nodes(clean_agent, clean_user, nodes)

        return RecallResult(
            agent_id=clean_agent,
            user_id=clean_user,
            audience=request.audience,
            relationship_status=relationship_status,
            persona_context=persona_context,
            relationship_context=relationship_context,
            memories=tuple(selected_memories),
            events=tuple(selected_events),
            signals=tuple(selected_signals),
            temporal_context=request.temporal_context,
            notices=tuple(notices),
            budget_report=budget_report,
            reinforcement=ReinforcementReport(
                requested=request.options.reinforce,
                applied=bool(reinforced_ids),
                reinforced_source_ids=tuple(reinforced_ids),
                reason=(
                    "Only final budget-selected MemoryNode projections were reinforced."
                    if reinforced_ids
                    else "No selected persistent MemoryNode required reinforcement."
                ),
            ),
        )

    def _active_manifest(self, profile) -> Optional[PersonaManifest]:
        if profile.manifest_id is None:
            return None
        manifest = self.storage.get_persona_manifest(profile.manifest_id)
        if manifest is None:
            return None
        proposals = self.storage.list_persona_compilation_proposals(
            profile.blueprint.blueprint_id
        )
        for proposal in proposals:
            if (
                proposal.proposal_id == manifest.approved_proposal_id
                and proposal.revision == manifest.approved_revision
            ):
                return (
                    manifest
                    if proposal.status == PersonaCompilationStatus.APPROVED
                    else None
                )
        return None

    def _project_events(
        self,
        events: Sequence[RelationshipEvent],
        query: str,
        top_k: int,
    ) -> List[EventRecallProjection]:
        query_tokens = MemoryRetriever.tokenize(query)
        ranked = sorted(
            events,
            key=lambda event: (
                len(query_tokens.intersection(MemoryRetriever.tokenize(event.content))),
                event.recorded_at,
            ),
            reverse=True,
        )[:top_k]
        return [
            EventRecallProjection(
                projection_id=f"relationship-event:{event.event_id}",
                source_id=event.event_id,
                source_kind="relationship_event",
                visibility=RecallAudience.AGENT_PRIVATE,
                selection_reason="relationship_event_relevance",
                source_references=(
                    RecallSourceReference(
                        source_id=event.event_id,
                        source_kind="relationship_event",
                    ),
                ),
                event_type=event.event_type.value,
                summary=event.content,
                recorded_at=event.recorded_at,
                occurred_at=event.occurred_at,
            )
            for event in ranked
        ]

    @staticmethod
    def _project_relationship_narratives(
        events: Sequence[RelationshipEvent],
        snapshot: RelationshipSnapshot,
        selected_event_ids: Set[str],
    ) -> List[RelationshipNarrativeProjection]:
        """Projects stored interpretation without inventing new relationship meaning."""
        projections: List[RelationshipNarrativeProjection] = []

        for event in events:
            if event.event_id not in selected_event_ids:
                continue
            adjudication = event.metadata.get("adjudication", {})
            if not isinstance(adjudication, Mapping):
                continue
            reflection = adjudication.get("persona_reflection")
            if not isinstance(reflection, str) or not reflection.strip():
                continue
            projections.append(
                RelationshipNarrativeProjection(
                    projection_id=f"persona-reflection:{event.event_id}",
                    source_id=event.event_id,
                    source_kind="stored_persona_reflection",
                    visibility=RecallAudience.AGENT_PRIVATE,
                    selection_reason="stored_interpretation_for_relevant_event",
                    source_references=(
                        RecallSourceReference(
                            source_id=event.event_id,
                            source_kind="relationship_event",
                        ),
                    ),
                    kind="persona_reflection",
                    content=reflection,
                )
            )

        for dimension, reason in sorted(snapshot.state_reasons.items()):
            if reason.delta > 0:
                direction = "increased"
            elif reason.delta < 0:
                direction = "decreased"
            else:
                direction = "was reaffirmed"
            projections.append(
                RelationshipNarrativeProjection(
                    projection_id=(
                        f"relationship-state-reason:{dimension}:{reason.evidence_event_id}"
                    ),
                    source_id=reason.evidence_event_id,
                    source_kind="relationship_state_reason",
                    visibility=RecallAudience.AGENT_PRIVATE,
                    selection_reason="latest_evidence_for_relationship_direction",
                    source_references=(
                        RecallSourceReference(
                            source_id=reason.evidence_event_id,
                            source_kind="relationship_event",
                        ),
                    ),
                    kind="relationship_state_reason",
                    content=(
                        f"{dimension} {direction}; grounded in: {reason.explanation}"
                    ),
                )
            )

        for key, belief in sorted(snapshot.beliefs.items()):
            value = (
                belief.value
                if isinstance(belief.value, str)
                else json.dumps(
                    belief.value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            projections.append(
                RelationshipNarrativeProjection(
                    projection_id=f"current-belief:{key}:{belief.evidence_event_id}",
                    source_id=belief.evidence_event_id,
                    source_kind="current_belief",
                    visibility=RecallAudience.AGENT_PRIVATE,
                    selection_reason="latest_supported_relationship_belief",
                    source_references=(
                        RecallSourceReference(
                            source_id=belief.evidence_event_id,
                            source_kind="relationship_event",
                        ),
                    ),
                    kind="current_belief",
                    content=f"{key}: {value}",
                )
            )
        return projections

    def _cost(self, value: object) -> int:
        if hasattr(value, "stable_json"):
            serialized = value.stable_json()
        else:
            serialized = str(value)
        cost = self.cost_estimator(serialized)
        if isinstance(cost, bool) or not isinstance(cost, int) or cost < 0:
            raise ValueError("recall cost estimator must return a non-negative integer")
        return cost

    def _apply_budget(
        self,
        persona: Optional[PersonaRecallContext],
        relationship: Optional[RelationshipRecallContext],
        memories: Sequence[MemoryRecallProjection],
        events: Sequence[EventRecallProjection],
        signals: Sequence[RecallSignalProjection],
        max_cost: int,
    ) -> Tuple[
        Optional[PersonaRecallContext],
        Optional[RelationshipRecallContext],
        List[MemoryRecallProjection],
        List[EventRecallProjection],
        List[RecallSignalProjection],
        BudgetReport,
    ]:
        candidates: List[_Candidate] = []
        if persona is not None:
            for item in persona.authority_items:
                required = item.activation_tier == "foundation"
                candidates.append(_Candidate("persona_authority", item, required, self._cost(item)))
            for item in persona.interpretation_items:
                required = item.activation_tier == "foundation"
                candidates.append(
                    _Candidate("persona_interpretation", item, required, self._cost(item))
                )
            for item in persona.approved_growth_items:
                candidates.append(_Candidate("persona_growth", item, False, self._cost(item)))
        if relationship is not None:
            if relationship.premise is not None:
                candidates.append(
                    _Candidate(
                        "relationship_premise",
                        relationship.premise,
                        False,
                        self._cost(relationship.premise),
                    )
                )
            for item in relationship.narratives:
                candidates.append(
                    _Candidate("relationship_narrative", item, False, self._cost(item))
                )
        for item in memories:
            candidates.append(_Candidate("memory", item, False, self._cost(item)))
        for item in events:
            candidates.append(_Candidate("event", item, False, self._cost(item)))
        for item in signals:
            if item.signal_type == RecallSignalType.PROMISE_OVERDUE:
                priority = 0
            elif item.signal_type == RecallSignalType.PROMISE_DUE:
                priority = 1
            elif item.authority == RecallSignalAuthority.FORMAL_RELATIONSHIP_HISTORY:
                priority = 2
            else:
                priority = 4
            candidates.append(
                _Candidate("signal", item, False, self._cost(item), priority)
            )

        required_cost = sum(item.cost for item in candidates if item.required)
        if required_cost > max_cost:
            raise RecallBudgetUnsatisfiedError(required_cost, max_cost)
        selected_ids: Set[str] = {
            item.value.projection_id for item in candidates if item.required
        }
        selected_cost = required_cost
        omissions: List[BudgetOmission] = []
        ordered_candidates = sorted(
            enumerate(candidates),
            key=lambda indexed: (indexed[1].priority, indexed[0]),
        )
        for _index, candidate in ordered_candidates:
            projection = candidate.value
            if candidate.required:
                continue
            if selected_cost + candidate.cost <= max_cost:
                selected_ids.add(projection.projection_id)
                selected_cost += candidate.cost
            else:
                omissions.append(
                    BudgetOmission(
                        source_id=projection.source_id,
                        source_kind=projection.source_kind,
                        estimated_cost=candidate.cost,
                        reason="atomic_projection_exceeds_remaining_budget",
                    )
                )

        if persona is not None:
            persona = persona.model_copy(
                update={
                    "authority_items": tuple(
                        item for item in persona.authority_items if item.projection_id in selected_ids
                    ),
                    "interpretation_items": tuple(
                        item
                        for item in persona.interpretation_items
                        if item.projection_id in selected_ids
                    ),
                    "approved_growth_items": tuple(
                        item
                        for item in persona.approved_growth_items
                        if item.projection_id in selected_ids
                    ),
                }
            )
        if relationship is not None:
            relationship = relationship.model_copy(
                update={
                    "premise": (
                        relationship.premise
                        if relationship.premise is not None
                        and relationship.premise.projection_id in selected_ids
                        else None
                    ),
                    "narratives": tuple(
                        item
                        for item in relationship.narratives
                        if item.projection_id in selected_ids
                    ),
                }
            )
        selected_memories = [
            item for item in memories if item.projection_id in selected_ids
        ]
        selected_events = [item for item in events if item.projection_id in selected_ids]
        selected_signals = [
            item for item in signals if item.projection_id in selected_ids
        ]
        return (
            persona,
            relationship,
            selected_memories,
            selected_events,
            selected_signals,
            BudgetReport(
                estimator_id=getattr(
                    self.cost_estimator,
                    "__name__",
                    self.cost_estimator.__class__.__name__,
                ),
                max_cost=max_cost,
                required_cost=required_cost,
                selected_cost=selected_cost,
                omitted=tuple(omissions),
            ),
        )

    @staticmethod
    def _parse_timeline(entry: str) -> Tuple[Optional[str], str]:
        if entry.startswith("[") and "] " in entry:
            timestamp, content = entry[1:].split("] ", 1)
            return timestamp, content
        return None, entry


__all__ = [
    "RecallAssembler",
    "RecallBudgetUnsatisfiedError",
    "RecallCostEstimator",
    "default_recall_cost",
]
