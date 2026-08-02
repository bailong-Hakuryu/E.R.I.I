"""Deep structured-recall module with audience filtering and atomic budgets."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from erii.core.adjudication import list_complete_relationship_events
from erii.core.persona_context import (
    PersonaContextPlanner,
    active_persona_manifest,
)
from erii.core.recall_authority import (
    RecallAuthorityClassifier,
    RecallAuthoritySelector,
)
from erii.core.relationship import RelationshipProjector
from erii.core.retriever import MemoryRetriever
from erii.core.temporal import RecallSignalDeriver
from erii.models.archival import (
    ArchivalArtifactKind,
    ArchivalOutcomeCode,
    ArchivalReceipt,
    ArchivalStatus,
    ArchivalTombstone,
    TimelineEntry,
    archival_artifact_fingerprint,
)
from erii.models.node import MemoryNode, MemoryType, MemoryVisibility
from erii.models.provenance import ArtifactProvenanceState
from erii.models.recall import (
    BudgetOmission,
    BudgetReport,
    EventRecallProjection,
    MemoryRecallProjection,
    PersonaDelivery,
    PersonaRecallContext,
    RecallAudience,
    RecallArtifactProvenance,
    RecallAuthorityTier,
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
from erii.models.turn import TurnRecord, TurnStatus
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
        manifest = None
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
            manifest = active_persona_manifest(self.storage, profile)
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
                approved_experiences = (
                    {
                        item.experience_id: item
                        for item in manifest.candidate.formative_experiences
                    }
                    if manifest is not None
                    else {}
                )
                for experience in profile.premise.experiences:
                    approved = approved_experiences.get(experience.experience_id)
                    if approved is None:
                        continue
                    narratives.append(
                        RelationshipNarrativeProjection(
                            projection_id=f"premise-experience:{experience.experience_id}",
                            source_id=experience.experience_id,
                            source_kind="premise_experience",
                            visibility=RecallAudience.AGENT_PRIVATE,
                            selection_reason="explicit_canonical_continuation",
                            kind="premise_experience",
                            content=f"{approved.title}: {approved.summary}",
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
                    if manifest is None:
                        notices.append(
                            RecallNotice(
                                code="legacy_unreviewed_full_persona",
                                message=(
                                    "No approved Persona Manifest is available, so this "
                                    "compatibility response contains unreviewed Blueprint "
                                    "text and cannot guarantee relationship-scope isolation."
                                ),
                                severity=RecallNoticeSeverity.WARNING,
                            )
                        )
                    else:
                        notices.append(
                            RecallNotice(
                                code="character_source_is_subordinate",
                                message=(
                                    "Full delivery contains every relationship-eligible "
                                    "Manifest item and exact supporting source span; host "
                                    "safety, privacy, authorization, and tool policy remain "
                                    "higher."
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
        ranked_nodes = self.retriever.rank_candidates(
            query=query,
            candidates=candidate_nodes,
            vector_store=self.vector_store,
            embedding_provider=self.embedding_provider,
            update_index=legacy_compat,
        )

        timeline_entries = None
        if request.audience == RecallAudience.AGENT_PRIVATE:
            try:
                timeline_entries = self.storage.get_recent_timeline_entries(
                    clean_agent,
                    clean_user,
                    limit=4,
                )
            except (AttributeError, NotImplementedError):
                timeline_entries = None

        current_relationship_id = (
            profile.relationship_id if profile is not None else None
        )
        source_turn_ids = {
            item.source_turn_id
            for item in ranked_nodes
            if item.relationship_id == current_relationship_id
            and item.source_turn_id
        }
        if timeline_entries is not None:
            source_turn_ids.update(
                item.source_turn_id
                for item in timeline_entries
                if item.relationship_id == current_relationship_id
                and item.source_turn_id
            )
        turn_records_by_id, archival_receipts_by_id = (
            self._load_artifact_provenance_indexes(
                current_relationship_id,
                source_turn_ids,
            )
        )
        memory_projections: List[MemoryRecallProjection] = []
        node_projection_ids: Dict[str, str] = {}
        turn_record_cache: Dict[
            Tuple[str, str], Optional[TurnRecord]
        ] = {}
        legacy_core_projection: Optional[MemoryRecallProjection] = None
        if request.audience == RecallAudience.AGENT_PRIVATE:
            core = self.storage.get_core_memory(clean_agent, clean_user)
            if core:
                legacy_core_projection = MemoryRecallProjection(
                    projection_id=f"legacy-core:{clean_agent}:{clean_user}",
                    source_id=f"legacy-core:{clean_agent}:{clean_user}",
                    source_kind="legacy_core_memory",
                    visibility=RecallAudience.AGENT_PRIVATE,
                    selection_reason="legacy_core_compatibility",
                    memory_type="core",
                    content=core,
                    source_visibility=MemoryVisibility.INTERNAL_MONOLOGUE.value,
                )
        for node in ranked_nodes:
            visibility = (
                RecallAudience.AGENT_PRIVATE
                if node.visibility == MemoryVisibility.INTERNAL_MONOLOGUE.value
                else request.audience
            )
            projection_id = f"memory:{node.node_id}"
            node_projection_ids[node.node_id] = projection_id
            (
                source_references,
                complete_source_chain,
                authority_source_chain,
                source_turn,
            ) = (
                self._artifact_source_references(
                expected_relationship_id=current_relationship_id,
                expected_agent_id=clean_agent,
                expected_user_id=clean_user,
                artifact_relationship_id=node.relationship_id,
                artifact=node,
                artifact_id=node.node_id,
                artifact_kind=ArchivalArtifactKind.MEMORY_NODE,
                source_turn_id=node.source_turn_id,
                source_archival_id=node.source_archival_id,
                turn_records_by_id=turn_records_by_id,
                archival_receipts_by_id=archival_receipts_by_id,
                turn_record_cache=turn_record_cache,
            )
            )
            memory_projections.append(
                MemoryRecallProjection(
                    projection_id=projection_id,
                    source_id=node.node_id,
                    source_kind="memory_node",
                    visibility=visibility,
                    selection_reason="relevance_and_diversity_rank",
                    source_references=source_references,
                    provenance=self._artifact_provenance(
                        node.provenance_state,
                        source_references,
                        complete_source_chain=complete_source_chain,
                        has_declared_source=bool(
                            node.source_turn_id or node.source_archival_id
                        ),
                    ),
                    authority_tier=RecallAuthorityClassifier.classify(
                        node,
                        source_turn=source_turn,
                        authority_source_chain=authority_source_chain,
                    ),
                    memory_type=node.node_type.value,
                    content=node.content,
                    created_at=node.created_at or None,
                    source_visibility=node.visibility,
                )
            )
        if legacy_core_projection is not None and not legacy_compat:
            memory_projections.append(legacy_core_projection)
        if request.audience == RecallAudience.AGENT_PRIVATE:
            if timeline_entries is not None:
                timeline_projections = []
                for entry in timeline_entries:
                    (
                        source_references,
                        complete_source_chain,
                        authority_source_chain,
                        source_turn,
                    ) = (
                        self._artifact_source_references(
                        expected_relationship_id=current_relationship_id,
                        expected_agent_id=clean_agent,
                        expected_user_id=clean_user,
                        artifact_relationship_id=entry.relationship_id,
                        artifact=entry,
                        artifact_id=entry.timeline_entry_id,
                        artifact_kind=ArchivalArtifactKind.TIMELINE_ENTRY,
                        source_turn_id=entry.source_turn_id,
                        source_archival_id=entry.source_archival_id,
                        turn_records_by_id=turn_records_by_id,
                        archival_receipts_by_id=archival_receipts_by_id,
                        turn_record_cache=turn_record_cache,
                    )
                    )
                    timeline_projections.append(
                        MemoryRecallProjection(
                        projection_id=f"timeline:{entry.timeline_entry_id}",
                        source_id=entry.timeline_entry_id,
                        source_kind="experiential_timeline",
                        visibility=RecallAudience.AGENT_PRIVATE,
                        selection_reason="recent_experiential_timeline",
                        source_references=source_references,
                        provenance=self._artifact_provenance(
                            entry.provenance_state,
                            source_references,
                            complete_source_chain=complete_source_chain,
                            has_declared_source=bool(
                                entry.source_turn_id
                                or entry.source_archival_id
                            ),
                        ),
                        authority_tier=RecallAuthorityClassifier.classify(
                            entry,
                            source_turn=source_turn,
                            authority_source_chain=authority_source_chain,
                        ),
                        memory_type="timeline",
                        content=entry.content,
                        created_at=entry.recorded_at or entry.legacy_timestamp,
                        source_visibility=(
                            MemoryVisibility.INTERNAL_MONOLOGUE.value
                        ),
                        )
                    )
            else:
                timeline_projections = self._legacy_timeline_projections(
                    clean_agent,
                    clean_user,
                )
            memory_projections.extend(timeline_projections)

        signal_eligible_node_ids = {
            projection.source_id
            for projection in memory_projections
            if projection.source_kind == "memory_node"
            and projection.authority_tier
            != RecallAuthorityTier.QUARANTINED_HISTORY
        }
        authority_selection = RecallAuthoritySelector.select(
            memory_projections,
            audience=request.audience,
            query=query,
            top_k=request.options.top_k,
            max_per_type=request.options.max_per_type,
        )
        memory_projections = list(authority_selection.projections)
        if (
            legacy_compat
            and legacy_core_projection is not None
            and all(
                item.content != legacy_core_projection.content
                for item in memory_projections
            )
        ):
            # ``recall()`` historically treated Core Memory as always-on persona
            # compatibility context, outside its dynamic ``top_k`` limit.
            memory_projections.insert(0, legacy_core_projection)

        signal_projections: List[RecallSignalProjection] = []
        if request.audience == RecallAudience.AGENT_PRIVATE and not legacy_compat:
            signal_projections = list(
                RecallSignalDeriver.derive(
                    relationship_events,
                    request.temporal_context.world_time,
                    tuple(
                        node
                        for node in candidate_nodes
                        if node.node_id in signal_eligible_node_ids
                    ),
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
            authority_selection.legacy_fallbacks,
            event_projections,
            signal_projections,
            request.options.budget.max_cost,
        )

        reinforced_ids: List[str] = []
        if request.options.reinforce:
            selected_projection_ids = {
                projection.projection_id
                for projection in selected_memories
                if projection.authority_tier == RecallAuthorityTier.ORDINARY
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

    def _project_relationship_narratives(
        self,
        events: Sequence[RelationshipEvent],
        snapshot: RelationshipSnapshot,
        selected_event_ids: Set[str],
    ) -> List[RelationshipNarrativeProjection]:
        """Projects stored interpretation without inventing new relationship meaning."""
        projections: List[RelationshipNarrativeProjection] = []

        try:
            reflection_records = self.storage.list_persona_reflection_records(
                snapshot.profile.relationship_id
            )
        except (AttributeError, NotImplementedError):
            reflection_records = ()
        reflected_event_ids: Set[str] = set()
        for record in reflection_records:
            if record.event_id not in selected_event_ids:
                continue
            reflected_event_ids.add(record.event_id)
            references = [
                RecallSourceReference(
                    source_id=record.event_id,
                    source_kind="relationship_event",
                )
            ]
            if record.target_reflection_id is not None:
                references.append(
                    RecallSourceReference(
                        source_id=record.target_reflection_id,
                        source_kind="persona_reflection_record",
                    )
                )
            projections.append(
                RelationshipNarrativeProjection(
                    projection_id=f"persona-reflection:{record.reflection_id}",
                    source_id=record.reflection_id,
                    source_kind="persona_reflection_record",
                    visibility=RecallAudience.AGENT_PRIVATE,
                    selection_reason="formal_interpretation_for_relevant_event",
                    source_references=tuple(references),
                    kind=record.record_kind.value,
                    content=record.content,
                )
            )

        # Pre-a7 events remain readable without silently rewriting them into the
        # formal reflection store. A formal record always wins for its event.
        for event in events:
            if (
                event.event_id not in selected_event_ids
                or event.event_id in reflected_event_ids
            ):
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
        memory_fallbacks: Mapping[str, MemoryRecallProjection],
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
        selected_fallbacks: Dict[str, MemoryRecallProjection] = {}
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
                fallback = memory_fallbacks.get(projection.projection_id)
                if fallback is None:
                    continue
                fallback_cost = self._cost(fallback)
                if selected_cost + fallback_cost <= max_cost:
                    selected_ids.add(fallback.projection_id)
                    selected_fallbacks[projection.projection_id] = fallback
                    selected_cost += fallback_cost
                else:
                    omissions.append(
                        BudgetOmission(
                            source_id=fallback.source_id,
                            source_kind=fallback.source_kind,
                            estimated_cost=fallback_cost,
                            reason=(
                                "legacy_reservation_fallback_exceeds_remaining_budget"
                            ),
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
        selected_memories = []
        for item in memories:
            if item.projection_id in selected_ids:
                selected_memories.append(item)
                continue
            fallback = selected_fallbacks.get(item.projection_id)
            if fallback is not None:
                selected_memories.append(fallback)
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

    def _legacy_timeline_projections(
        self,
        agent_id: str,
        user_id: str,
    ) -> Tuple[MemoryRecallProjection, ...]:
        projections = []
        for index, entry in enumerate(
            self.storage.get_recent_timeline(agent_id, user_id, limit=4)
        ):
            timestamp, content = self._parse_timeline(entry)
            digest = hashlib.sha256(entry.encode("utf-8")).hexdigest()[:16]
            projections.append(
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
        return tuple(projections)

    def _artifact_source_references(
        self,
        *,
        expected_relationship_id: Optional[str],
        expected_agent_id: str,
        expected_user_id: str,
        artifact_relationship_id: Optional[str],
        artifact: object,
        artifact_id: str,
        artifact_kind: ArchivalArtifactKind,
        source_turn_id: Optional[str],
        source_archival_id: Optional[str],
        turn_records_by_id: Optional[Dict[str, object]],
        archival_receipts_by_id: Optional[Dict[str, object]],
        turn_record_cache: Optional[
            Dict[Tuple[str, str], Optional[TurnRecord]]
        ] = None,
    ) -> Tuple[
        Tuple[RecallSourceReference, ...],
        bool,
        bool,
        Optional[TurnRecord],
    ]:
        """Projects only source identities verified inside the recalled relationship."""
        if (
            expected_relationship_id is None
            or artifact_relationship_id != expected_relationship_id
        ):
            return (), False, False, None

        references = []
        source_revision = None
        source_turn = None
        if source_turn_id:
            if turn_records_by_id is not None:
                candidate_turn = turn_records_by_id.get(source_turn_id)
                if (
                    isinstance(candidate_turn, TurnRecord)
                    and candidate_turn.relationship_id == expected_relationship_id
                    and candidate_turn.status == TurnStatus.COMPLETED
                ):
                    source_turn = candidate_turn
                    source_revision = source_turn.source_revision
            else:
                cache_key = (expected_relationship_id, source_turn_id)
                cache = (
                    turn_record_cache
                    if turn_record_cache is not None
                    else {}
                )
                if cache_key in cache:
                    source_turn = cache[cache_key]
                else:
                    try:
                        candidate_turn = self.storage.get_turn_record(
                            expected_relationship_id,
                            source_turn_id,
                        )
                        source_turn = (
                            candidate_turn
                            if candidate_turn.relationship_id
                            == expected_relationship_id
                            and candidate_turn.status == TurnStatus.COMPLETED
                            else None
                        )
                    except (
                        AttributeError,
                        KeyError,
                        LookupError,
                        NotImplementedError,
                    ):
                        source_turn = None
                    cache[cache_key] = source_turn
                if source_turn is not None:
                    source_revision = source_turn.source_revision
            if source_revision is not None:
                references.append(
                    RecallSourceReference(
                        source_id=source_turn_id,
                        source_kind="source_turn",
                        source_revision=source_revision,
                    )
                )

        archival_receipt = (
            archival_receipts_by_id.get(source_archival_id)
            if archival_receipts_by_id is not None and source_archival_id
            else None
        )
        archival_is_valid = self._archival_certifies_artifact(
            archival_receipt,
            relationship_id=expected_relationship_id,
            agent_id=expected_agent_id,
            user_id=expected_user_id,
            source_turn_id=source_turn_id,
            source_revision=source_revision,
            artifact=artifact,
            artifact_id=artifact_id,
            artifact_kind=artifact_kind,
        )
        authority_source_chain = archival_is_valid or (
            self._archival_tombstone_supports_artifact(
                archival_receipt,
                relationship_id=expected_relationship_id,
                agent_id=expected_agent_id,
                user_id=expected_user_id,
                source_turn_id=source_turn_id,
                source_revision=source_revision,
                artifact=artifact,
                artifact_id=artifact_id,
                artifact_kind=artifact_kind,
            )
        )
        if source_archival_id:
            if archival_is_valid:
                references.append(
                    RecallSourceReference(
                        source_id=source_archival_id,
                        source_kind="archival_batch",
                    )
                )
        complete_source_chain = bool(
            source_revision is not None
            and archival_is_valid
            and archival_receipt.source_revision == source_revision
        )
        return (
            tuple(references),
            complete_source_chain,
            authority_source_chain,
            source_turn,
        )

    def _load_artifact_provenance_indexes(
        self,
        relationship_id: Optional[str],
        source_turn_ids: Set[str],
    ) -> Tuple[
        Optional[Dict[str, object]],
        Optional[Dict[str, object]],
    ]:
        """Reads each relationship provenance ledger at most once per recall."""
        if relationship_id is None:
            return {}, {}

        try:
            turn_records_by_id: Optional[Dict[str, object]] = {
                item.turn_id: item
                for item in self.storage.get_turn_records(
                    relationship_id,
                    sorted(source_turn_ids),
                )
            }
        except (AttributeError, NotImplementedError):
            turn_records_by_id = None

        archival_receipts_by_id: Dict[str, object] = {}
        archival_capability_available = False
        try:
            archival_store = self.storage.atomic_archival_store_v1()
            if archival_store is not None:
                archival_capability_available = True
                archival_receipts_by_id.update(
                    {
                        item.receipt.archival_id: item.receipt
                        for item in archival_store.list_archival_records(
                            relationship_id
                        )
                    }
                )
        except (AttributeError, NotImplementedError):
            pass
        try:
            tombstones = self.storage.list_archival_tombstones(relationship_id)
            archival_capability_available = True
            for item in tombstones:
                archival_receipts_by_id.setdefault(item.archival_id, item)
        except (AttributeError, NotImplementedError):
            pass
        return (
            turn_records_by_id,
            (
                archival_receipts_by_id
                if archival_capability_available
                else None
            ),
        )

    @staticmethod
    def _archival_certifies_artifact(
        receipt: object,
        *,
        relationship_id: str,
        agent_id: str,
        user_id: str,
        source_turn_id: Optional[str],
        source_revision: Optional[str],
        artifact: object,
        artifact_id: str,
        artifact_kind: ArchivalArtifactKind,
    ) -> bool:
        """Requires one successful archival binding for this exact artifact."""
        # This path handles full operational receipts. Compact tombstones use
        # the separate content-free commitment check below.
        if not isinstance(receipt, ArchivalReceipt):
            return False
        if (
            receipt.relationship_id != relationship_id
            or receipt.agent_id != agent_id
            or receipt.user_id != user_id
            or source_turn_id is None
            or source_revision is None
            or receipt.source_turn_id != source_turn_id
            or receipt.source_revision != source_revision
            or receipt.status != ArchivalStatus.COMPLETED
            or receipt.outcome_code != ArchivalOutcomeCode.ARTIFACTS_COMMITTED
        ):
            return False
        if not isinstance(artifact, (MemoryNode, TimelineEntry)):
            return False
        current_fingerprint = archival_artifact_fingerprint(artifact)
        return any(
            item.kind == artifact_kind
            and item.artifact_id == artifact_id
            and item.artifact_fingerprint is not None
            and item.artifact_fingerprint == current_fingerprint
            for item in receipt.artifact_manifest
        )

    @staticmethod
    def _archival_tombstone_supports_artifact(
        receipt: object,
        *,
        relationship_id: str,
        agent_id: str,
        user_id: str,
        source_turn_id: Optional[str],
        source_revision: Optional[str],
        artifact: object,
        artifact_id: str,
        artifact_kind: ArchivalArtifactKind,
    ) -> bool:
        """Keeps evidence authority after detail-retention compaction.

        The compact commitment contains no payload bytes, but binds the
        artifact kind, identity and exact canonical payload fingerprint.
        Projection provenance remains partial because the full receipt is no
        longer present; generation authority can nevertheless survive safely.
        """
        if not isinstance(receipt, ArchivalTombstone):
            return False
        if (
            receipt.relationship_id != relationship_id
            or receipt.agent_id != agent_id
            or receipt.user_id != user_id
            or source_turn_id is None
            or source_revision is None
            or receipt.source_turn_id != source_turn_id
            or receipt.source_revision != source_revision
            or receipt.status != ArchivalStatus.COMPLETED
            or receipt.outcome_code != ArchivalOutcomeCode.ARTIFACTS_COMMITTED
            or not isinstance(artifact, (MemoryNode, TimelineEntry))
            or receipt.artifact_commitments is None
        ):
            return False
        current_fingerprint = archival_artifact_fingerprint(artifact)
        return any(
            item.kind == artifact_kind
            and item.artifact_id == artifact_id
            and item.artifact_fingerprint == current_fingerprint
            for item in receipt.artifact_commitments
        )

    @staticmethod
    def _artifact_provenance(
        declared_state: ArtifactProvenanceState,
        references: Sequence[RecallSourceReference],
        *,
        complete_source_chain: bool = False,
        has_declared_source: bool = False,
    ) -> RecallArtifactProvenance:
        """Classifies source trust without promoting partial legacy metadata."""
        state = ArtifactProvenanceState(declared_state)
        if (
            state == ArtifactProvenanceState.COMPLETE
            and complete_source_chain
        ):
            return RecallArtifactProvenance.SOURCE_LINKED
        if references or has_declared_source:
            return RecallArtifactProvenance.PARTIAL_SOURCE
        return RecallArtifactProvenance.LEGACY_UNRESOLVED


__all__ = [
    "RecallAssembler",
    "RecallBudgetUnsatisfiedError",
    "RecallCostEstimator",
    "default_recall_cost",
]
