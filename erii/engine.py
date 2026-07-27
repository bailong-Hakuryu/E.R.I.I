"""E.R.I.I. Unified Orchestration Engine (ERIIEngine).

Main entry point for AI Agent long-term memory integration.
Follows Google Python Style Guide.
"""

from dataclasses import replace
import hashlib
import os
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union
import uuid

from erii.adapters.base import BaseLLMAdapter
from erii.adapters.custom_adapter import CallableLLMAdapter
from erii.core.archiver import AsyncArchiverWorker
from erii.core.budget import MemoryBudgetManager
from erii.core.decay import MemoryDecayEvaluator
from erii.core.retriever import MemoryRetriever
from erii.core.adjudication import (
    RelationshipAdjudicator,
    list_complete_relationship_events,
    relationship_occurrence_fingerprint,
)
from erii.core.relationship import RelationshipProjector
from erii.core.persona_compilation import PersonaCompiler
from erii.core.recall import RecallAssembler
from erii.core.queue.base import BaseTaskQueue
from erii.core.queue.persistent_queue import PersistentTaskQueue
from erii.models.config import ERIIConfig
from erii.models.adjudication import (
    AdjudicationBatchResult,
    AdjudicationRecord,
    PersonaGrowthDecision,
    PersonaGrowthIntentCandidate,
    PersonaGrowthProposal,
    RelationshipCandidateBatch,
    RelationshipEventCandidate,
    SourceTurn,
)
from erii.models.node import MemoryNode, MemoryType, MemoryVisibility
from erii.models.pack import MemoryPack
from erii.models.persona import (
    PersonaCompilationDecision,
    PersonaCompilationProposal,
    PersonaCompilationStatus,
    PersonaManifest,
    PersonaManifestCandidate,
)
from erii.models.recall import (
    RecallRequest,
    RecallResult,
)
from erii.models.relationship import (
    BeliefUpdate,
    CharacterBlueprint,
    IdentityKind,
    PersonaConflictError,
    RelationshipEvent,
    RelationshipEventType,
    RelationshipNotFoundError,
    RelationshipProfile,
    RelationshipPremise,
    RelationshipSnapshot,
)
from erii.renderers.markdown import MarkdownRecallRenderer
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage
from erii.storage.file_storage import FileStorage
from erii.vector.base import BaseEmbeddingProvider, BaseVectorStore
from erii.vector.in_memory_vector import CallableEmbeddingAdapter, DummyEmbeddingProvider


class DummyMockLLMAdapter(BaseLLMAdapter):
    """Fallback dummy LLM adapter when no LLM is provided."""

    def generate(self, prompt: str) -> str:
        return '{"timeline_entry": "Interaction logged", "impressions": []}'


class ERIIEngine:
    """Experiential Recall & Impression Integration Engine (E.R.I.I.)."""

    def __init__(
        self,
        storage_dir: str = "./erii_memory",
        llm: Optional[Union[BaseLLMAdapter, Callable[[str], str]]] = None,
        storage_driver: Optional[BaseStorage] = None,
        config: Optional[ERIIConfig] = None,
        task_queue: Optional[BaseTaskQueue] = None,
        vector_store: Optional[BaseVectorStore] = None,
        embedding_provider: Optional[Union[BaseEmbeddingProvider, Callable[[str], List[float]]]] = None,
    ) -> None:
        """Initializes ERIIEngine.

        Args:
            storage_dir: Directory path for default file storage.
            llm: LLM provider adapter or Python callable function.
            storage_driver: Custom storage driver instance (optional).
            config: ERIIConfig instance (optional).
            task_queue: Custom BaseTaskQueue implementation (optional).
            vector_store: BaseVectorStore instance for hybrid vector search (optional).
            embedding_provider: BaseEmbeddingProvider or callable function (optional).
        """
        self.config = config or ERIIConfig(storage_dir=storage_dir)

        # 1. Resolve LLM Adapter
        if callable(llm) and not isinstance(llm, BaseLLMAdapter):
            self.llm_adapter: BaseLLMAdapter = CallableLLMAdapter(llm)
        elif isinstance(llm, BaseLLMAdapter):
            self.llm_adapter = llm
        else:
            self.llm_adapter = DummyMockLLMAdapter()

        # 2. Resolve Storage Driver
        if storage_driver is not None:
            self.storage = storage_driver
        else:
            self.storage = FileStorage(root_dir=self.config.storage_dir)

        # 3. Resolve Vector & Embedding Components
        self.vector_store = vector_store
        if callable(embedding_provider) and not isinstance(embedding_provider, BaseEmbeddingProvider):
            self.embedding_provider: Optional[BaseEmbeddingProvider] = CallableEmbeddingAdapter(embedding_provider)
        elif isinstance(embedding_provider, BaseEmbeddingProvider):
            self.embedding_provider = embedding_provider
        elif self.vector_store is not None:
            self.embedding_provider = DummyEmbeddingProvider()
        else:
            self.embedding_provider = None

        # 4. Instantiate Sub-engines
        self.decay_evaluator = MemoryDecayEvaluator(
            decay_rate=self.config.decay_rate,
            max_weight_cap=self.config.max_weight_cap,
        )
        self.retriever = MemoryRetriever(decay_rate=self.config.decay_rate)
        self.budget_manager = MemoryBudgetManager(
            core_budget=self.config.core_budget,
            timeline_budget=self.config.timeline_budget,
            dynamic_budget=self.config.dynamic_budget,
        )
        self.relationship_adjudicator = RelationshipAdjudicator(self.storage)
        self.recall_assembler = RecallAssembler(
            storage=self.storage,
            retriever=self.retriever,
            vector_store=self.vector_store,
            embedding_provider=self.embedding_provider,
        )

        # 5. Keep the default persistent queue alongside the selected storage.
        if task_queue is None:
            if hasattr(self.storage, "db_path"):
                queue_db_path = self.storage.db_path
            else:
                configured_root = os.path.abspath(self.config.storage_dir)
                legacy_root = os.path.abspath("./erii_memory")
                legacy_queue = os.path.abspath("./erii_memory.db")
                if configured_root == legacy_root and os.path.exists(legacy_queue):
                    queue_db_path = legacy_queue
                else:
                    queue_db_path = os.path.join(configured_root, "erii_tasks.db")
            task_queue = PersistentTaskQueue(db_path=queue_db_path)

        # 6. Assemble the background archiver without starting hidden work.
        self.archiver_worker = AsyncArchiverWorker(
            storage=self.storage,
            llm_adapter=self.llm_adapter,
            enable_sanitizer=self.config.enable_security_sanitizer,
            enable_pii_scrubbing=self.config.enable_pii_scrubbing,
            task_queue=task_queue,
        )

    def remember(
        self,
        agent_id: str,
        user_id: str,
        user_message: str = "",
        bot_reply: str = "",
        user_msg: str = "",
    ) -> None:
        """Records a conversation turn into memory and enqueues archival.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            user_message: User message text string.
            bot_reply: Assistant response text string.
            user_msg: Deprecated alias for user_message.
        """
        user_message = user_message or user_msg
        if not user_message or not bot_reply:
            return

        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        if self.config.enable_security_sanitizer:
            user_message = SecuritySanitizer.sanitize_text(user_message)
            bot_reply = SecuritySanitizer.sanitize_text(bot_reply)

        if self.config.enable_pii_scrubbing:
            user_message = SecuritySanitizer.scrub_pii(user_message)
            bot_reply = SecuritySanitizer.scrub_pii(bot_reply)

        # Queue for host-controlled background processing, or process inline.
        if self.config.async_archival:
            self.archiver_worker.push_task(
                agent_id=clean_agent,
                user_id=clean_user,
                user_msg=user_message,
                bot_reply=bot_reply,
            )
        else:
            self.archiver_worker.process_now(
                agent_id=clean_agent,
                user_id=clean_user,
                user_msg=user_message,
                bot_reply=bot_reply,
            )

    def recall(
        self,
        agent_id: str,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> str:
        """Recalls relevant memory context formatted for LLM system prompt injection.

        Applies Security Sanitization, Term Matching, Category Diversity Cap,
        Recall Reinforcement, and Token Budgeting.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            query: Query message string for term matching.
            top_k: Maximum dynamic nodes to retrieve.

        Returns:
            Formatted Markdown context string ready for prompt injection.
        """
        # This facade intentionally keeps the pre-a3 lifecycle and Markdown
        # contract. Structured recall is exposed separately by
        # ``recall_structured`` and must not silently change existing callers.
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        if self.config.enable_security_sanitizer:
            query = SecuritySanitizer.sanitize_text(query)

        nodes = self.storage.load_nodes(clean_agent, clean_user)
        nodes = self.decay_evaluator.sweep_nodes(nodes)
        selected_nodes = self.retriever.retrieve_relevant_nodes(
            query=query,
            all_nodes=nodes,
            top_k=top_k,
            vector_store=self.vector_store,
            embedding_provider=self.embedding_provider,
        )
        if selected_nodes:
            self.storage.save_nodes(clean_agent, clean_user, nodes)

        core_memory = self.storage.get_core_memory(clean_agent, clean_user)
        timeline_entries = self.storage.get_recent_timeline(
            clean_agent,
            clean_user,
            limit=4,
        )
        dynamic_lines = []
        for idx, node in enumerate(selected_nodes, 1):
            weight = self.decay_evaluator.evaluate_node(node)
            type_tag = f"[{node.node_type.value.upper()}]"
            time_prefix = f"[{node.created_at}] " if node.created_at else ""
            dynamic_lines.append(
                f"{idx}. {time_prefix}{type_tag} {node.content} "
                f"(weight: {weight:.2f})"
            )

        budgeted = self.budget_manager.allocate_memory_context(
            core_memory=core_memory,
            timeline_entries=timeline_entries,
            dynamic_nodes_formatted="\n".join(dynamic_lines),
        )
        sections = []
        if budgeted["core_memory"]:
            sections.append(f"# Core Persona Memory\n{budgeted['core_memory']}")
        if budgeted["dynamic_memory"]:
            sections.append(f"# Relevant Memories\n{budgeted['dynamic_memory']}")
        if budgeted["timeline_context"]:
            sections.append(f"# Experiential Timeline\n{budgeted['timeline_context']}")
        return "\n\n".join(sections)

    def recall_structured(
        self,
        request: Union[RecallRequest, Mapping[str, Any]],
    ) -> RecallResult:
        """Returns an audience-filtered, renderer-neutral structured recall.

        Structured recall is read-only unless ``request.options.reinforce`` is
        explicitly true. It never initializes a missing relationship.
        """
        validated = RecallRequest.model_validate(request)
        if self.config.enable_security_sanitizer:
            validated = validated.model_copy(
                update={"query": SecuritySanitizer.sanitize_text(validated.query)}
            )
        return self.recall_assembler.assemble(validated)

    def render_recall(
        self,
        result: RecallResult,
        *,
        max_output_cost: Optional[int] = None,
    ) -> str:
        """Deterministically renders an already assembled result without writes."""
        renderer = MarkdownRecallRenderer(
            audience=result.audience,
            max_output_cost=max_output_cost,
        )
        return renderer.render(result)

    def set_core_memory(self, agent_id: str, user_id: str, content: str) -> None:
        """Sets Core Persona memory string for given agent and user."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        self.storage.save_core_memory(clean_agent, clean_user, content)

    def get_core_memory(self, agent_id: str, user_id: str) -> str:
        """Gets Core Persona memory string for given agent and user."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        return self.storage.get_core_memory(clean_agent, clean_user)

    def initialize_relationship(
        self,
        agent_id: str,
        user_id: str,
        persona_source: str,
        compiled_persona: Optional[Mapping[str, Any]] = None,
        *,
        relationship_premise: Optional[
            Union[RelationshipPremise, Mapping[str, Any]]
        ] = None,
        source_format: str = "text/plain",
        source_name: Optional[str] = None,
    ) -> RelationshipProfile:
        """Initializes one isolated Agent x User relationship idempotently.

        The imported persona source is an immutable authority snapshot. Calling
        this method again for the same pair is safe only when the source and any
        explicitly supplied compilation are unchanged.
        """
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        source_text = persona_source if isinstance(persona_source, str) else ""
        if not source_text.strip():
            raise ValueError("persona_source must be a non-empty string")
        premise = (
            RelationshipPremise()
            if relationship_premise is None
            else (
                relationship_premise
                if isinstance(relationship_premise, RelationshipPremise)
                else RelationshipPremise.from_dict(relationship_premise)
            )
        )

        existing = self.storage.get_relationship(clean_agent, clean_user)
        if existing is not None:
            self._ensure_persona_matches(
                existing,
                source_text,
                compiled_persona,
                premise,
            )
            return existing

        agent_identity_id = self.storage.get_or_create_identity(
            IdentityKind.AGENT,
            clean_agent,
        )
        user_identity_id = self.storage.get_or_create_identity(
            IdentityKind.USER,
            clean_user,
        )
        profile = RelationshipProfile(
            relationship_id=str(uuid.uuid4()),
            persona_id=str(uuid.uuid4()),
            agent_identity_id=agent_identity_id,
            user_identity_id=user_identity_id,
            agent_id=clean_agent,
            user_id=clean_user,
            blueprint=CharacterBlueprint(
                blueprint_id=str(uuid.uuid4()),
                source_text=source_text,
                compiled=compiled_persona or {},
                source_format=source_format,
                source_name=source_name,
            ),
            premise=premise,
        )
        stored = self.storage.create_relationship(profile)
        self._ensure_persona_matches(stored, source_text, compiled_persona, premise)
        return stored

    @staticmethod
    def _ensure_persona_matches(
        profile: RelationshipProfile,
        persona_source: str,
        compiled_persona: Optional[Mapping[str, Any]],
        premise: Optional[RelationshipPremise] = None,
    ) -> None:
        """Rejects attempts to mutate the immutable authority snapshot."""
        if profile.blueprint.source_text != persona_source:
            raise PersonaConflictError(
                "this relationship already has a different persona authority snapshot"
            )
        if compiled_persona is not None:
            candidate = CharacterBlueprint(
                blueprint_id=profile.blueprint.blueprint_id,
                source_text=persona_source,
                compiled=compiled_persona,
                created_at=profile.blueprint.created_at,
            )
            if candidate.to_dict()["compiled"] != profile.blueprint.to_dict()["compiled"]:
                raise PersonaConflictError(
                    "this relationship already has a different compiled persona snapshot"
                )
        if premise is not None and profile.premise.to_dict() != premise.to_dict():
            raise PersonaConflictError(
                "this relationship already has a different immutable relationship premise"
            )

    def propose_persona_compilation(
        self,
        agent_id: str,
        user_id: str,
        compiler_or_candidate: Any,
        *,
        created_by: Optional[str] = None,
        proposal_id: Optional[str] = None,
    ) -> PersonaCompilationProposal:
        """Explicitly compiles or validates one reviewable Persona proposal.

        A mapping or ``PersonaManifestCandidate`` is treated as advanced host
        input. A callable or compiler adapter is invoked exactly here, never by
        relationship initialization or a background worker.
        """
        profile = self._require_relationship(agent_id, user_id, "compiling a persona")
        if isinstance(compiler_or_candidate, (Mapping, PersonaManifestCandidate)):
            proposal = PersonaCompiler.propose(
                profile.blueprint,
                compiler_or_candidate,
                proposal_id=proposal_id,
                created_by=created_by,
            )
        else:
            proposal = PersonaCompiler.compile(
                profile.blueprint,
                compiler_or_candidate,
                proposal_id=proposal_id,
                created_by=created_by,
            )
        return self.storage.save_persona_compilation_proposal(proposal)

    def revise_persona_compilation(
        self,
        agent_id: str,
        user_id: str,
        proposal_id: str,
        expected_revision: int,
        candidate: Union[PersonaManifestCandidate, Mapping[str, Any]],
        actor_id: str,
    ) -> PersonaCompilationProposal:
        """Creates a complete immutable revision; approval never edits content."""
        profile = self._require_relationship(agent_id, user_id, "revising a persona")
        proposals = self.storage.list_persona_compilation_proposals(
            profile.blueprint.blueprint_id
        )
        matching = [item for item in proposals if item.proposal_id == proposal_id]
        if not matching:
            raise LookupError("persona compilation proposal does not exist")
        current = max(matching, key=lambda item: item.revision)
        revised = PersonaCompiler.revise(
            profile.blueprint,
            current,
            candidate,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )
        return self.storage.save_persona_compilation_proposal(revised)

    def decide_persona_compilation(
        self,
        agent_id: str,
        user_id: str,
        proposal_id: str,
        revision: int,
        actor_id: str,
        decision: Union[PersonaCompilationDecision, str],
        *,
        reason: Optional[str] = None,
    ) -> Union[PersonaCompilationProposal, PersonaManifest]:
        """Decides an exact latest revision and atomically materializes approval."""
        profile = self._require_relationship(agent_id, user_id, "deciding a persona")
        proposals = self.storage.list_persona_compilation_proposals(
            profile.blueprint.blueprint_id
        )
        matching = [item for item in proposals if item.proposal_id == proposal_id]
        if not matching:
            raise LookupError("persona compilation proposal does not exist")
        current = max(matching, key=lambda item: item.revision)
        if current.revision != revision:
            raise ValueError("persona compilation proposal revision changed")
        parsed_decision = PersonaCompilationDecision(decision)
        if (
            parsed_decision == PersonaCompilationDecision.APPROVE
            and current.status == PersonaCompilationStatus.APPROVED
        ):
            matching_manifests = [
                item
                for item in self.storage.list_persona_manifests(
                    profile.blueprint.blueprint_id
                )
                if item.approved_proposal_id == current.proposal_id
                and item.approved_revision == current.revision
                and item.content_fingerprint == current.content_fingerprint
            ]
            if len(matching_manifests) != 1:
                raise ValueError(
                    "approved persona compilation does not have exactly one Manifest"
                )
            return self.storage.approve_and_bind_persona_manifest(
                profile,
                current,
                matching_manifests[0],
                PersonaCompilationStatus.APPROVED,
            )
        decided = PersonaCompiler.decide(
            current,
            revision=revision,
            actor_id=actor_id,
            decision=parsed_decision,
            reason=reason,
        )
        if parsed_decision == PersonaCompilationDecision.APPROVE:
            manifest = PersonaCompiler.manifest_from_approved(decided)
            return self.storage.approve_and_bind_persona_manifest(
                profile,
                decided,
                manifest,
                PersonaCompilationStatus.PENDING,
            )
        expected = (
            PersonaCompilationStatus.APPROVED
            if parsed_decision == PersonaCompilationDecision.REVOKE
            else PersonaCompilationStatus.PENDING
        )
        return self.storage.save_persona_compilation_proposal(decided, expected)

    def list_persona_compilation_proposals(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[PersonaCompilationProposal]:
        """Returns compilation revisions for this relationship's Blueprint."""
        profile = self._require_relationship(agent_id, user_id, "reading persona proposals")
        return self.storage.list_persona_compilation_proposals(
            profile.blueprint.blueprint_id
        )

    def get_persona_manifest(
        self,
        agent_id: str,
        user_id: str,
    ) -> Optional[PersonaManifest]:
        """Returns the exact Manifest pinned to the relationship, if any."""
        profile = self._require_relationship(agent_id, user_id, "reading a persona Manifest")
        if profile.manifest_id is None:
            return None
        return self.storage.get_persona_manifest(profile.manifest_id)

    def _require_relationship(
        self,
        agent_id: str,
        user_id: str,
        operation: str,
    ) -> RelationshipProfile:
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        profile = self.storage.get_relationship(clean_agent, clean_user)
        if profile is None:
            raise RelationshipNotFoundError(
                f"initialize_relationship() must be called before {operation}"
            )
        return profile

    def record_relationship_event(
        self,
        agent_id: str,
        user_id: str,
        event_type: Union[RelationshipEventType, str],
        content: str,
        *,
        state_delta: Optional[Mapping[str, float]] = None,
        belief_updates: Optional[Sequence[Union[BeliefUpdate, Mapping[str, Any]]]] = None,
        occurred_at: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> RelationshipEvent:
        """Appends one validated relationship event and returns the stored event."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        profile = self.storage.get_relationship(clean_agent, clean_user)
        if profile is None:
            raise RelationshipNotFoundError(
                "initialize_relationship() must be called before recording events"
            )

        event = RelationshipEvent(
            event_id=event_id or str(uuid.uuid4()),
            relationship_id=profile.relationship_id,
            event_type=RelationshipEventType(event_type),
            content=content,
            state_delta=state_delta or {},
            belief_updates=belief_updates or (),
            occurred_at=occurred_at,
        )
        return self.storage.append_relationship_event(event)

    def adjudicate_relationship_candidates(
        self,
        agent_id: str,
        user_id: str,
        source_turn: Union[SourceTurn, Mapping[str, Any]],
        candidates: Sequence[
            Union[RelationshipEventCandidate, Mapping[str, Any]]
        ],
    ) -> AdjudicationBatchResult:
        """Validates and durably adjudicates untrusted relationship candidates.

        The caller supplies the full transient source turn so evidence quotes can
        be verified. Only minimal verified spans are retained by accepted or
        corroborated decisions; raw source messages are not persisted.
        """
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        profile = self.storage.get_relationship(clean_agent, clean_user)
        if profile is None:
            raise RelationshipNotFoundError(
                "initialize_relationship() must be called before adjudicating candidates"
            )
        validated_turn = SourceTurn.model_validate(source_turn)
        validated_batch = RelationshipCandidateBatch.model_validate(
            {"candidates": list(candidates)}
        )
        return self.relationship_adjudicator.adjudicate(
            profile,
            validated_turn,
            validated_batch,
        )

    def list_relationship_adjudications(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[AdjudicationRecord]:
        """Returns durable candidate decisions for an initialized relationship."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        profile = self.storage.get_relationship(clean_agent, clean_user)
        if profile is None:
            raise RelationshipNotFoundError(
                "initialize_relationship() must be called before reading adjudications"
            )
        return self.storage.list_relationship_adjudications(profile.relationship_id)

    def propose_persona_growth(
        self,
        agent_id: str,
        user_id: str,
        intent: Union[PersonaGrowthIntentCandidate, Mapping[str, Any]],
    ) -> PersonaGrowthProposal:
        """Creates a pending growth proposal from a separate history-based review."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        profile = self.storage.get_relationship(clean_agent, clean_user)
        if profile is None:
            raise RelationshipNotFoundError(
                "initialize_relationship() must be called before proposing persona growth"
            )
        validated_intent = PersonaGrowthIntentCandidate.model_validate(intent)
        return self.relationship_adjudicator.propose_persona_growth(
            profile,
            validated_intent,
        )

    def decide_persona_growth_proposal(
        self,
        agent_id: str,
        user_id: str,
        proposal_id: str,
        revision: int,
        actor_id: str,
        decision: Union[PersonaGrowthDecision, str],
        *,
        reason: Optional[str] = None,
    ) -> PersonaGrowthProposal:
        """Records an out-of-band host decision for an exact proposal revision."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        profile = self.storage.get_relationship(clean_agent, clean_user)
        if profile is None:
            raise RelationshipNotFoundError(
                "initialize_relationship() must be called before deciding persona growth"
            )
        return self.relationship_adjudicator.decide_persona_growth(
            profile,
            proposal_id,
            revision,
            actor_id,
            PersonaGrowthDecision(decision),
            reason,
        )

    def list_persona_growth_proposals(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[PersonaGrowthProposal]:
        """Returns pending and decided persona-growth proposals."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        profile = self.storage.get_relationship(clean_agent, clean_user)
        if profile is None:
            raise RelationshipNotFoundError(
                "initialize_relationship() must be called before reading persona growth"
            )
        return self.storage.list_persona_growth_proposals(profile.relationship_id)

    def get_relationship_snapshot(
        self,
        agent_id: str,
        user_id: str,
        *,
        observed_at: Optional[str] = None,
    ) -> RelationshipSnapshot:
        """Rebuilds current beliefs and relationship state from accepted history."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        profile = self.storage.get_relationship(clean_agent, clean_user)
        if profile is None:
            raise RelationshipNotFoundError(
                "initialize_relationship() must be called before reading a snapshot"
            )
        events = list_complete_relationship_events(self.storage, profile.relationship_id)
        return RelationshipProjector.project(profile, events, observed_at=observed_at)

    def list_relationship_events(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[RelationshipEvent]:
        """Returns the append-only relationship history in storage order."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        profile = self.storage.get_relationship(clean_agent, clean_user)
        if profile is None:
            raise RelationshipNotFoundError(
                "initialize_relationship() must be called before reading events"
            )
        return list_complete_relationship_events(self.storage, profile.relationship_id)

    def remember_thought(
        self,
        agent_id: str,
        user_id: str,
        content: str,
        visibility: str = MemoryVisibility.PUBLIC_LOG.value,
        is_unresolved: bool = False,
        emotional_score: float = 0.0,
        foreshadowing_tags: Optional[List[str]] = None,
        created_at: Optional[str] = None,
    ) -> MemoryNode:
        """Explicitly records a first-person inner monologue or diary entry.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            content: Inner monologue or diary text.
            visibility: MemoryVisibility string ("public_log" or "internal_monologue").
            is_unresolved: True if this represents an active narrative suspense/unresolved thought.
            emotional_score: Score between -1.0 and 1.0.
            foreshadowing_tags: List of narrative tags.
            created_at: Custom ISO/world timestamp.

        Returns:
            The created MemoryNode instance.
        """
        import uuid
        from datetime import datetime

        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        if self.config.enable_security_sanitizer:
            content = SecuritySanitizer.sanitize_text(content)
        if self.config.enable_pii_scrubbing:
            content = SecuritySanitizer.scrub_pii(content)

        ts = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        valid_visibility = (
            visibility
            if visibility in (MemoryVisibility.PUBLIC_LOG.value, MemoryVisibility.INTERNAL_MONOLOGUE.value)
            else MemoryVisibility.PUBLIC_LOG.value
        )

        node = MemoryNode(
            node_id=str(uuid.uuid4()),
            user_id=clean_user,
            agent_id=clean_agent,
            node_type=MemoryType.THOUGHT,
            content=content,
            tags=foreshadowing_tags or [],
            base_importance=0.8,
            emotional_score=emotional_score,
            visibility=valid_visibility,
            is_unresolved=is_unresolved,
            foreshadowing_tags=foreshadowing_tags or [],
            created_at=ts,
            last_accessed_at=ts,
        )

        nodes = self.storage.load_nodes(clean_agent, clean_user)
        nodes.append(node)
        self.storage.save_nodes(clean_agent, clean_user, nodes)
        return node

    def get_inner_monologue(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 10,
        unresolved_only: bool = False,
        visibility: Optional[str] = MemoryVisibility.PUBLIC_LOG.value,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves psychological monologue and diary entries filtered by visibility and narrative suspense."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        nodes = self.storage.load_nodes(clean_agent, clean_user)
        filtered = []

        for node in nodes:
            if node.node_type not in (MemoryType.THOUGHT, MemoryType.DIARY):
                continue

            if visibility and node.visibility != visibility:
                continue

            if unresolved_only and not node.is_unresolved:
                continue

            if start_time and node.created_at < start_time:
                continue

            if end_time and node.created_at > end_time:
                continue

            filtered.append(node)

        # Sort: unresolved suspense nodes first (1 > 0), then by created_at descending
        filtered.sort(key=lambda n: (1 if n.is_unresolved else 0, n.created_at), reverse=True)

        result = []
        for node in filtered[:limit]:
            d = node.to_dict()
            d["effective_weight"] = node.calculate_effective_weight(
                decay_rate=self.config.decay_rate,
                max_weight_cap=self.config.max_weight_cap,
            )
            result.append(d)

        return result

    def get_diary_timeline(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 10,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves formatted first-person public diary timeline entries for UI rendering."""
        return self.get_inner_monologue(
            agent_id=agent_id,
            user_id=user_id,
            limit=limit,
            unresolved_only=False,
            visibility=MemoryVisibility.PUBLIC_LOG.value,
            start_time=start_time,
            end_time=end_time,
        )

    def resolve_thought(
        self,
        agent_id: str,
        user_id: str,
        node_id: str,
    ) -> bool:
        """Marks a suspenseful/unresolved thought node as resolved."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        nodes = self.storage.load_nodes(clean_agent, clean_user)
        found = False
        for node in nodes:
            if node.node_id == node_id:
                node.is_unresolved = False
                found = True
                break

        if found:
            self.storage.save_nodes(clean_agent, clean_user, nodes)

        return found

    def export_memory(
        self, agent_id: str, user_id: str, export_path: Optional[str] = None
    ) -> MemoryPack:
        """Exports memory for specified agent and user into a MemoryPack object."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        nodes = self.storage.load_nodes(clean_agent, clean_user)
        core_mem = self.storage.get_core_memory(clean_agent, clean_user)
        timeline = self.storage.get_recent_timeline(clean_agent, clean_user, limit=1000)
        try:
            relationship = self.storage.get_relationship(clean_agent, clean_user)
        except NotImplementedError:
            relationship = None

        relationship_events = []
        relationship_adjudications = []
        persona_growth_proposals = []
        persona_compilation_proposals = []
        persona_manifests = []
        if relationship is not None:
            try:
                relationship_events = list_complete_relationship_events(
                    self.storage,
                    relationship.relationship_id,
                )
            except NotImplementedError:
                pass
            try:
                relationship_adjudications = (
                    self.storage.list_relationship_adjudications(
                        relationship.relationship_id
                    )
                )
            except NotImplementedError:
                pass
            try:
                persona_growth_proposals = self.storage.list_persona_growth_proposals(
                    relationship.relationship_id
                )
            except NotImplementedError:
                pass
            try:
                persona_compilation_proposals = (
                    self.storage.list_persona_compilation_proposals(
                        relationship.blueprint.blueprint_id
                    )
                )
            except NotImplementedError:
                pass
            try:
                persona_manifests = self.storage.list_persona_manifests(
                    relationship.blueprint.blueprint_id
                )
            except NotImplementedError:
                pass

        raw_timeline = []
        for line in timeline:
            if line.startswith("[") and "]" in line:
                idx = line.index("]")
                ts = line[1:idx]
                content = line[idx + 2 :]
                raw_timeline.append({"timestamp": ts, "content": content})
            else:
                raw_timeline.append({"timestamp": "", "content": line})

        pack = MemoryPack(
            agent_id=clean_agent,
            user_id=clean_user,
            core_memory=core_mem,
            nodes=nodes,
            timeline=raw_timeline,
            relationship=relationship,
            relationship_events=relationship_events,
            relationship_adjudications=relationship_adjudications,
            persona_growth_proposals=persona_growth_proposals,
            persona_compilation_proposals=persona_compilation_proposals,
            persona_manifests=persona_manifests,
        )

        if export_path:
            with open(export_path, "w", encoding="utf-8") as f:
                f.write(pack.to_json())

        return pack

    def import_memory(
        self,
        pack_or_path: Union[MemoryPack, str, Dict[str, Any]],
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None,
        overwrite: bool = False,
    ) -> MemoryPack:
        """Imports a MemoryPack object or JSON file into storage."""
        if isinstance(pack_or_path, str):
            with open(pack_or_path, "r", encoding="utf-8") as f:
                pack = MemoryPack.from_json(f.read())
        elif isinstance(pack_or_path, dict):
            pack = MemoryPack.from_dict(pack_or_path)
        else:
            pack = pack_or_path

        target_agent = agent_id or pack.agent_id
        target_user = user_id or pack.user_id

        clean_agent = SecuritySanitizer.validate_key(target_agent, "agent_id")
        clean_user = SecuritySanitizer.validate_key(target_user, "user_id")

        if overwrite:
            existing_nodes = []
        else:
            existing_nodes = self.storage.load_nodes(clean_agent, clean_user)

        node_map = {n.node_id: n for n in existing_nodes}
        for n in pack.nodes:
            node_map[n.node_id] = n

        self.storage.save_nodes(clean_agent, clean_user, list(node_map.values()))

        if pack.core_memory and (overwrite or not self.storage.get_core_memory(clean_agent, clean_user)):
            self.storage.save_core_memory(clean_agent, clean_user, pack.core_memory)

        for entry in pack.timeline:
            self.storage.add_timeline_entry(
                clean_agent, clean_user, entry.get("content", ""), entry.get("timestamp")
            )

        if pack.relationship is not None:
            existing_profile = self.storage.get_relationship(clean_agent, clean_user)
            if existing_profile is not None:
                self._ensure_persona_matches(
                    existing_profile,
                    pack.relationship.blueprint.source_text,
                    pack.relationship.blueprint.compiled,
                    pack.relationship.premise,
                )
                target_profile = existing_profile
            elif (
                clean_agent == pack.relationship.agent_id
                and clean_user == pack.relationship.user_id
            ):
                try:
                    target_profile = self.storage.create_relationship(pack.relationship)
                except (RuntimeError, ValueError):
                    target_profile = self.initialize_relationship(
                        clean_agent,
                        clean_user,
                        pack.relationship.blueprint.source_text,
                        pack.relationship.blueprint.compiled,
                        relationship_premise=pack.relationship.premise,
                        source_format=pack.relationship.blueprint.source_format,
                        source_name=pack.relationship.blueprint.source_name,
                    )
            else:
                target_profile = self.initialize_relationship(
                    clean_agent,
                    clean_user,
                    pack.relationship.blueprint.source_text,
                    pack.relationship.blueprint.compiled,
                    relationship_premise=pack.relationship.premise,
                    source_format=pack.relationship.blueprint.source_format,
                    source_name=pack.relationship.blueprint.source_name,
                )

            has_persona_compilation_payload = bool(
                pack.persona_compilation_proposals
                or pack.persona_manifests
                or pack.relationship.manifest_id
            )
            if has_persona_compilation_payload:
                target_profile = self._import_persona_compilation(pack, target_profile)

            source_relationship_id = pack.relationship.relationship_id
            target_relationship_id = target_profile.relationship_id

            decision_id_map = {}
            for record in pack.relationship_adjudications:
                receipt = record.receipt
                if source_relationship_id == target_relationship_id:
                    mapped_decision_id = receipt.decision_id
                else:
                    processing_identity = (
                        f"{receipt.processing_mode.value}:{receipt.reprocessing_id or ''}"
                    )
                    mapped_decision_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            (
                                f"erii:{target_relationship_id}:decision:"
                                f"{receipt.source_turn_id}:{receipt.source_revision}:"
                                f"{processing_identity}:{receipt.candidate_key}"
                            ),
                        )
                    )
                decision_id_map[receipt.decision_id] = mapped_decision_id

            source_event_ids = {
                event.event_id for event in pack.relationship_events
            } | {
                event.event_id
                for record in pack.relationship_adjudications
                for event in record.events
            }
            event_id_map = {
                event_id: (
                    event_id
                    if source_relationship_id == target_relationship_id
                    else str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"erii:{target_relationship_id}:{event_id}",
                        )
                    )
                )
                for event_id in source_event_ids
            }
            if source_relationship_id != target_relationship_id:
                for record in pack.relationship_adjudications:
                    mapped_decision_id = decision_id_map[record.receipt.decision_id]
                    for index, event in enumerate(record.events):
                        event_suffix = "event" if index == 0 else f"event:{index}"
                        event_id_map[event.event_id] = str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"{mapped_decision_id}:{event_suffix}",
                            )
                        )

            def remap_event(source_event: RelationshipEvent) -> RelationshipEvent:
                if source_relationship_id == target_relationship_id:
                    return source_event
                metadata = source_event.to_dict().get("metadata", {})
                adjudication = metadata.get("adjudication")
                if isinstance(adjudication, dict):
                    if adjudication.get("decision_id"):
                        adjudication["decision_id"] = decision_id_map.get(
                            adjudication["decision_id"],
                            adjudication["decision_id"],
                        )
                    adjudication["references"] = [
                        event_id_map.get(item, item)
                        for item in adjudication.get("references", [])
                    ]
                    adjudication["occurrence_fingerprint"] = (
                        relationship_occurrence_fingerprint(
                            relationship_id=target_relationship_id,
                            event_type=source_event.event_type.value,
                            summary=source_event.content,
                            occurred_at=source_event.occurred_at,
                            occurrence_key=adjudication.get("occurrence_key"),
                        )
                    )
                return replace(
                    source_event,
                    event_id=event_id_map[source_event.event_id],
                    relationship_id=target_relationship_id,
                    metadata=metadata,
                )

            adjudicated_event_ids = {
                event.event_id
                for record in pack.relationship_adjudications
                for event in record.events
            }
            for source_event in pack.relationship_events:
                if source_event.event_id in adjudicated_event_ids:
                    continue
                self.storage.append_relationship_event(remap_event(source_event))

            for source_record in pack.relationship_adjudications:
                if source_relationship_id == target_relationship_id:
                    imported_record = source_record
                else:
                    imported_events = tuple(remap_event(event) for event in source_record.events)
                    old_receipt = source_record.receipt
                    mapped_decision_id = decision_id_map[old_receipt.decision_id]
                    mapped_occurrence = (
                        imported_events[0].metadata["adjudication"][
                            "occurrence_fingerprint"
                        ]
                        if imported_events
                        else hashlib.sha256(
                            (
                                f"{target_relationship_id}:"
                                f"{old_receipt.occurrence_fingerprint}"
                            ).encode("utf-8")
                        ).hexdigest()
                    )
                    imported_receipt = replace(
                        old_receipt,
                        decision_id=mapped_decision_id,
                        relationship_id=target_relationship_id,
                        occurrence_fingerprint=mapped_occurrence,
                        event_ids=tuple(event.event_id for event in imported_events),
                        related_event_id=(
                            event_id_map.get(old_receipt.related_event_id)
                            if old_receipt.related_event_id
                            else None
                        ),
                    )
                    imported_record = replace(
                        source_record,
                        receipt=imported_receipt,
                        events=imported_events,
                    )
                self.storage.commit_relationship_adjudication(imported_record)

            for source_proposal in pack.persona_growth_proposals:
                if source_relationship_id == target_relationship_id:
                    imported_proposal = source_proposal
                else:
                    imported_proposal = replace(
                        source_proposal,
                        proposal_id=str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                (
                                    f"erii:{target_relationship_id}:growth:"
                                    f"{source_proposal.review_id}:"
                                    f"{source_proposal.intent_key}"
                                ),
                            )
                        ),
                        relationship_id=target_relationship_id,
                        supporting_event_ids=tuple(
                            event_id_map.get(event_id, event_id)
                            for event_id in source_proposal.supporting_event_ids
                        ),
                    )
                self.storage.save_persona_growth_proposal(imported_proposal)

        return pack

    def _import_persona_compilation(
        self,
        pack: MemoryPack,
        target_profile: RelationshipProfile,
    ) -> RelationshipProfile:
        """Validates, remaps, then imports one Blueprint's compilation history."""
        if pack.relationship is None:
            return target_profile
        source_blueprint = pack.relationship.blueprint
        target_blueprint = target_profile.blueprint
        source_blueprint_id = source_blueprint.blueprint_id
        target_blueprint_id = target_blueprint.blueprint_id
        remapped = source_blueprint_id != target_blueprint_id

        if source_blueprint.source_text != target_blueprint.source_text:
            raise ValueError(
                "MemoryPack Persona Compilation cannot be remapped to different source text"
            )

        def immutable_proposal_content(
            proposal: PersonaCompilationProposal,
        ) -> Dict[str, Any]:
            data = proposal.to_dict()
            for key in (
                "status",
                "created_at",
                "created_by",
                "decided_by",
                "decided_at",
                "decision_reason",
            ):
                data.pop(key, None)
            return data

        def proposal_lifecycle(proposal: PersonaCompilationProposal):
            return (
                proposal.status,
                proposal.decided_by,
                proposal.decided_at,
                proposal.decision_reason,
            )

        source_proposals: Dict[
            tuple[str, int], PersonaCompilationProposal
        ] = {}
        validated_source_candidates = {}
        for source_proposal in pack.persona_compilation_proposals:
            source_key = (source_proposal.proposal_id, source_proposal.revision)
            if source_key in source_proposals:
                raise ValueError("MemoryPack contains a duplicate Persona proposal revision")
            if (
                source_proposal.blueprint_id != source_blueprint_id
                or source_proposal.blueprint_revision != source_blueprint.revision
                or source_proposal.source_sha256 != source_blueprint.source_sha256
            ):
                raise ValueError(
                    "MemoryPack Persona proposal belongs to a different Blueprint revision"
                )
            validated_source = PersonaCompiler._validate_against_source(
                source_proposal.candidate,
                source_blueprint.source_text,
            )
            if validated_source.model_dump(mode="json") != source_proposal.candidate.model_dump(
                mode="json"
            ):
                raise ValueError(
                    "MemoryPack Persona proposal lacks canonical source-span hashes"
                )
            expected_fingerprint = PersonaCompiler.content_fingerprint(
                source_blueprint_id,
                source_blueprint.revision,
                source_blueprint.source_sha256,
                validated_source,
            )
            if source_proposal.content_fingerprint != expected_fingerprint:
                raise ValueError("MemoryPack Persona proposal fingerprint is invalid")
            if source_proposal.status == PersonaCompilationStatus.PENDING:
                if any(
                    value is not None
                    for value in (
                        source_proposal.decided_by,
                        source_proposal.decided_at,
                        source_proposal.decision_reason,
                    )
                ):
                    raise ValueError("pending MemoryPack Persona proposal has decision state")
            elif source_proposal.decided_by is None or source_proposal.decided_at is None:
                raise ValueError("decided MemoryPack Persona proposal lacks provenance")
            source_proposals[source_key] = source_proposal
            validated_source_candidates[source_key] = validated_source

        for source_proposal in source_proposals.values():
            if source_proposal.revision > 1 and (
                source_proposal.proposal_id,
                source_proposal.parent_revision,
            ) not in source_proposals:
                raise ValueError("MemoryPack Persona proposal parent revision is missing")

        source_manifests_by_id: Dict[str, PersonaManifest] = {}
        source_manifests_by_revision: Dict[tuple[str, int], PersonaManifest] = {}
        for source_manifest in pack.persona_manifests:
            manifest_key = (
                source_manifest.approved_proposal_id,
                source_manifest.approved_revision,
            )
            if (
                source_manifest.manifest_id in source_manifests_by_id
                or manifest_key in source_manifests_by_revision
            ):
                raise ValueError("MemoryPack contains a duplicate Persona Manifest")
            if (
                source_manifest.blueprint_id != source_blueprint_id
                or source_manifest.blueprint_revision != source_blueprint.revision
                or source_manifest.source_sha256 != source_blueprint.source_sha256
            ):
                raise ValueError(
                    "MemoryPack Persona Manifest belongs to a different Blueprint revision"
                )
            source_proposal = source_proposals.get(manifest_key)
            if source_proposal is None:
                raise ValueError("MemoryPack Manifest references a missing proposal revision")
            if source_proposal.status not in (
                PersonaCompilationStatus.APPROVED,
                PersonaCompilationStatus.REVOKED,
            ):
                raise ValueError("MemoryPack Manifest references an unapproved proposal")
            validated_manifest_candidate = PersonaCompiler._validate_against_source(
                source_manifest.candidate,
                source_blueprint.source_text,
            )
            if validated_manifest_candidate.model_dump(
                mode="json"
            ) != source_manifest.candidate.model_dump(mode="json"):
                raise ValueError(
                    "MemoryPack Persona Manifest lacks canonical source-span hashes"
                )
            source_approval = replace(
                source_proposal,
                status=PersonaCompilationStatus.APPROVED,
                decided_by=source_manifest.approved_by,
                decided_at=source_manifest.approved_at,
                decision_reason=None,
            )
            expected_source_manifest = PersonaCompiler.manifest_from_approved(
                source_approval
            )
            if expected_source_manifest.to_dict() != source_manifest.to_dict():
                raise ValueError(
                    "MemoryPack Persona Manifest does not match its approved proposal"
                )
            if source_proposal.status == PersonaCompilationStatus.APPROVED and (
                source_proposal.decided_by != source_manifest.approved_by
                or source_proposal.decided_at != source_manifest.approved_at
            ):
                raise ValueError(
                    "approved MemoryPack Persona proposal has different Manifest provenance"
                )
            source_manifests_by_id[source_manifest.manifest_id] = source_manifest
            source_manifests_by_revision[manifest_key] = source_manifest

        for source_proposal in source_proposals.values():
            if source_proposal.status in (
                PersonaCompilationStatus.APPROVED,
                PersonaCompilationStatus.REVOKED,
            ) and (
                source_proposal.proposal_id,
                source_proposal.revision,
            ) not in source_manifests_by_revision:
                raise ValueError("approved MemoryPack proposal is missing its Manifest")

        proposal_id_map: Dict[str, str] = {}
        mapped_proposals: List[PersonaCompilationProposal] = []
        for source_proposal in sorted(
            source_proposals.values(),
            key=lambda item: (item.proposal_id, item.revision),
        ):
            proposal_id_map.setdefault(
                source_proposal.proposal_id,
                (
                    str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            (
                                f"erii:{target_blueprint_id}:persona-compilation:"
                                f"{source_proposal.proposal_id}"
                            ),
                        )
                    )
                    if remapped
                    else source_proposal.proposal_id
                ),
            )
            source_key = (source_proposal.proposal_id, source_proposal.revision)
            validated_target = PersonaCompiler._validate_against_source(
                validated_source_candidates[source_key],
                target_blueprint.source_text,
            )
            fingerprint = PersonaCompiler.content_fingerprint(
                target_blueprint_id,
                target_blueprint.revision,
                target_blueprint.source_sha256,
                validated_target,
            )
            mapped = replace(
                source_proposal,
                proposal_id=proposal_id_map[source_proposal.proposal_id],
                blueprint_id=target_blueprint_id,
                blueprint_revision=target_blueprint.revision,
                source_sha256=target_blueprint.source_sha256,
                candidate=validated_target,
                content_fingerprint=fingerprint,
            )
            mapped_proposals.append(mapped)

        mapped_manifest_by_source_id: Dict[str, PersonaManifest] = {}
        proposal_by_key = {
            (item.proposal_id, item.revision): item for item in mapped_proposals
        }
        for source_manifest in source_manifests_by_id.values():
            mapped_proposal_id = proposal_id_map.get(source_manifest.approved_proposal_id)
            if mapped_proposal_id is None:
                raise ValueError("MemoryPack manifest references a missing proposal revision")
            mapped_proposal = proposal_by_key.get(
                (mapped_proposal_id, source_manifest.approved_revision)
            )
            if mapped_proposal is None:
                raise ValueError("MemoryPack manifest references a missing proposal revision")
            mapped_approval = replace(
                mapped_proposal,
                status=PersonaCompilationStatus.APPROVED,
                decided_by=source_manifest.approved_by,
                decided_at=source_manifest.approved_at,
                decision_reason=None,
            )
            mapped_manifest_by_source_id[
                source_manifest.manifest_id
            ] = PersonaCompiler.manifest_from_approved(mapped_approval)

        selected_source_manifest_id = pack.relationship.manifest_id
        selected_manifest = None
        selected_proposal_key = None
        if selected_source_manifest_id is not None:
            selected_manifest = mapped_manifest_by_source_id.get(
                selected_source_manifest_id
            )
            if selected_manifest is None:
                raise ValueError("relationship references a Manifest missing from MemoryPack")
            selected_proposal_key = (
                selected_manifest.approved_proposal_id,
                selected_manifest.approved_revision,
            )
            if target_profile.manifest_id not in (None, selected_manifest.manifest_id):
                raise ValueError("target relationship is pinned to a different Manifest")

        existing_compilations = {
            (item.proposal_id, item.revision): item
            for item in self.storage.list_persona_compilation_proposals(target_blueprint_id)
        }
        existing_manifests = self.storage.list_persona_manifests(target_blueprint_id)
        existing_manifest_by_id = {item.manifest_id: item for item in existing_manifests}
        existing_manifest_by_revision = {
            (item.approved_proposal_id, item.approved_revision): item
            for item in existing_manifests
        }
        for mapped in mapped_proposals:
            key = (mapped.proposal_id, mapped.revision)
            existing = existing_compilations.get(key)
            if existing is None:
                continue
            if immutable_proposal_content(existing) != immutable_proposal_content(mapped):
                raise ValueError("MemoryPack proposal identity conflicts with stored content")
            if existing.status == mapped.status:
                if proposal_lifecycle(existing) != proposal_lifecycle(mapped):
                    raise ValueError("MemoryPack proposal lifecycle conflicts with storage")
            elif not (
                existing.status == PersonaCompilationStatus.PENDING
                or (
                    existing.status == PersonaCompilationStatus.APPROVED
                    and mapped.status == PersonaCompilationStatus.REVOKED
                )
            ):
                raise ValueError("MemoryPack proposal status conflicts with storage")

        for mapped_manifest in mapped_manifest_by_source_id.values():
            existing = existing_manifest_by_id.get(mapped_manifest.manifest_id)
            by_revision = existing_manifest_by_revision.get(
                (
                    mapped_manifest.approved_proposal_id,
                    mapped_manifest.approved_revision,
                )
            )
            for candidate in (existing, by_revision):
                if candidate is not None and candidate.to_dict() != mapped_manifest.to_dict():
                    raise ValueError("MemoryPack Manifest identity conflicts with storage")

        # No writes occur until the complete source graph and every target
        # conflict have been validated.
        for mapped in mapped_proposals:
            key = (mapped.proposal_id, mapped.revision)
            if key in existing_compilations:
                continue
            pending = replace(
                mapped,
                status=PersonaCompilationStatus.PENDING,
                decided_by=None,
                decided_at=None,
                decision_reason=None,
            )
            self.storage.save_persona_compilation_proposal(pending)
            existing_compilations[key] = pending

        for mapped in mapped_proposals:
            key = (mapped.proposal_id, mapped.revision)
            current = existing_compilations[key]
            if mapped.status == PersonaCompilationStatus.PENDING:
                continue
            matching_manifest = next(
                (
                    item
                    for item in mapped_manifest_by_source_id.values()
                    if (
                        item.approved_proposal_id,
                        item.approved_revision,
                    )
                    == key
                ),
                None,
            )
            if mapped.status in (
                PersonaCompilationStatus.APPROVED,
                PersonaCompilationStatus.REVOKED,
            ):
                if matching_manifest is None:
                    raise ValueError("approved MemoryPack proposal is missing its Manifest")
                approved = replace(
                    mapped,
                    status=PersonaCompilationStatus.APPROVED,
                    decided_by=matching_manifest.approved_by,
                    decided_at=matching_manifest.approved_at,
                    decision_reason=None,
                )
                manifest_already_exists = (
                    matching_manifest.manifest_id in existing_manifest_by_id
                )
                if key == selected_proposal_key and mapped.status == PersonaCompilationStatus.APPROVED:
                    expected = current.status
                    self.storage.approve_and_bind_persona_manifest(
                        target_profile,
                        approved,
                        matching_manifest,
                        expected,
                    )
                    target_profile = self.storage.get_relationship(
                        target_profile.agent_id,
                        target_profile.user_id,
                    ) or target_profile
                elif current.status == PersonaCompilationStatus.PENDING:
                    self.storage.approve_persona_manifest(
                        approved,
                        matching_manifest,
                        PersonaCompilationStatus.PENDING,
                    )
                elif (
                    current.status == PersonaCompilationStatus.APPROVED
                    and not manifest_already_exists
                ):
                    self.storage.approve_persona_manifest(
                        approved,
                        matching_manifest,
                        PersonaCompilationStatus.APPROVED,
                    )
                elif current.status == PersonaCompilationStatus.REVOKED and not manifest_already_exists:
                    raise ValueError("revoked stored proposal is missing its Persona Manifest")

                if (
                    mapped.status == PersonaCompilationStatus.REVOKED
                    and current.status != PersonaCompilationStatus.REVOKED
                ):
                    self.storage.save_persona_compilation_proposal(
                        mapped,
                        PersonaCompilationStatus.APPROVED,
                    )
                existing_compilations[key] = mapped
                existing_manifest_by_id[matching_manifest.manifest_id] = matching_manifest
            elif (
                mapped.status == PersonaCompilationStatus.REJECTED
                and current.status == PersonaCompilationStatus.PENDING
            ):
                self.storage.save_persona_compilation_proposal(
                    mapped,
                    PersonaCompilationStatus.PENDING,
                )
                existing_compilations[key] = mapped

        if selected_manifest is not None and target_profile.manifest_id is None:
            target_profile = self.storage.bind_relationship_manifest(
                target_profile,
                selected_manifest.manifest_id,
            )
        return target_profile

    def start(self) -> "ERIIEngine":
        """Explicitly starts background archival and returns this engine."""
        self.archiver_worker.start()
        return self

    def process_pending(self, max_tasks: Optional[int] = None) -> int:
        """Synchronously processes ready archival tasks under host control."""
        if max_tasks is not None and max_tasks < 0:
            raise ValueError("max_tasks cannot be negative")
        processed = 0
        while max_tasks is None or processed < max_tasks:
            if not self.archiver_worker.process_next():
                break
            processed += 1
        return processed

    def close(self) -> None:
        """Gracefully shuts down background archiver thread."""
        if hasattr(self, "archiver_worker"):
            self.archiver_worker.shutdown()

    def shutdown(self) -> None:
        """Alias for close()."""
        self.close()

    def __enter__(self) -> "ERIIEngine":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit, ensures close() is called."""
        self.close()

