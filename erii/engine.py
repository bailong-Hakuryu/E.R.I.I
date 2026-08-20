"""E.R.I.I. Unified Orchestration Engine (ERIIEngine).

Main entry point for AI Agent long-term memory integration.
Follows Google Python Style Guide.
"""

from collections import OrderedDict
from concurrent.futures import Future
import os
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union
import uuid
import warnings

from erii.adapters.base import BaseLLMAdapter
from erii.adapters.custom_adapter import CallableLLMAdapter
from erii._engine.memory_pack_analysis import (
    analyze_memory_pack_relationship_processing,
    analyze_relationship_processing_reflection_context,
    validate_relationship_processing_reflections,
    validate_relationship_processing_runs,
    validate_memory_pack_persisted_turn_adjudications,
    validate_memory_pack_node_types,
    validate_memory_pack_relationship_consequences,
    validate_memory_pack_turn_records,
)
from erii._engine.memory_pack_transfer import (
    MemoryPackExportSnapshot,
    MemoryPackTargetReadRecorder,
    analyze_memory_pack_source,
    assemble_memory_pack_export,
    bind_memory_pack_transfer_plan,
    execute_memory_pack_persona_compilation,
    execute_memory_pack_writes,
    memory_pack_import_operation_id,
    memory_pack_import_result_from_json,
    memory_pack_import_result_json,
    plan_memory_pack_persona_compilation_writes,
    plan_memory_pack_persona_growth_writes,
    plan_memory_pack_writes,
    replay_memory_pack_target_read_set,
    require_memory_pack_transfer_plan_current,
)
from erii.core.archiver import AsyncArchiverWorker
from erii.core.archival import ArchivalCoordinator
from erii.core.budget import MemoryBudgetManager
from erii.core.decay import MemoryDecayEvaluator
from erii.core.retriever import MemoryRetriever
from erii.core.adjudication import (
    PERSISTED_TURN_CONTRACT_VERSION,
    RelationshipAdjudicator,
    list_complete_relationship_events,
    relationship_events_from_journals,
)
from erii.core.consolidation import RelationshipConsolidator
from erii.core.consequence import (
    NarrativeTensionProjector,
    RelationshipConsequenceCoordinator,
)
from erii.core.continuity import (
    ContinuityEvaluationCapabilityError,
    ContinuityEvaluationCoordinator,
    InteractionContextEvaluationCoordinator,
    RelationshipSafetySignalProjector,
    VoicePatternMatcher,
)
from erii.core.continuity_evidence import (
    ContinuityEvidenceRefValue,
    ContinuityEvidenceResolver,
)
from erii.core.evidence_authority import quarantined_agent_source_ids
from erii.core.memory_pack_evidence import validate_memory_pack_archival_evidence
from erii.core.persona_context import (
    PersonaManifestRequiredError,
    validate_persona_premise_binding,
)
from erii.core.relationship_processing import RelationshipProcessingCoordinator
from erii.core.relationship import RelationshipProjector
from erii.core.persona_compilation import PersonaCompiler
from erii.core.recall import RecallAssembler
from erii.core.temporal_history import TemporalHistoryValidator
from erii.core.turn_ledger import TurnLedger
from erii.core.turn_context import (
    resolve_turn_context_authorities,
    resolve_turn_context_history,
)
from erii.core.queue.base import BaseTaskQueue
from erii.core.queue.persistent_queue import PersistentTaskQueue
from erii.models.config import ERIIConfig
from erii.models.archival import (
    ArchivalDrainReport,
    ArchivalOutcomeCode,
    ArchivalReceipt,
    ArchivalStatus,
    ArchivalSubmissionError,
    ArchivalTombstone,
    MemoryExtractorV1,
    ShutdownReport,
)
from erii.models.adjudication import (
    AdjudicationBatchResult,
    AdjudicationRecord,
    PersonaGrowthDecision,
    PersonaGrowthIntentCandidate,
    PersonaGrowthProposal,
    RelationshipCandidateBatch,
    RelationshipEventCandidate,
    SourceProcessingMode,
    SourceMessage,
    SourceRole,
    SourceTurn,
)
from erii.models.consolidation import (
    PersonaReflectionDecisionRecord,
    PersonaReflectionInterpreterV1,
    PersonaReflectionRecord,
    PersonaReflectionRecordKind,
    RelationshipConsolidation,
    RelationshipEventExtractorV1,
    RelationshipProcessingOutcome,
    RelationshipProcessingRun,
    RelationshipProcessingStatus,
)
from erii.models.consequence import (
    NarrativeTensionLink,
    NarrativeTensionOutcome,
    NarrativeTensionProjection,
    RelationshipConsequence,
    RelationshipConsequenceKind,
)
from erii.models.continuity import (
    ContinuityEvaluationRequest,
    ContinuityEvaluationResult,
    ContinuityEvaluatorV1,
    InteractionContextEvaluationRequest,
    InteractionContextEvaluatorV1,
    VoicePatternActivation,
)
from erii.models.continuity_review import DeliveryExceptionRecord
from erii.models.node import MemoryNode, MemoryType, MemoryVisibility
from erii.models.pack import MemoryPack
from erii.models.persona import (
    PersonaCompilationDecision,
    PersonaCompilationProposal,
    PersonaCompilationStatus,
    PersonaManifest,
    PersonaManifestCandidate,
    VoicePatternConditionType,
)
from erii.models.recall import (
    RecallAudience,
    RecallOptions,
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
from erii.models.temporal import (
    OpenLoopResolution,
    OpenLoopResolutionKind,
    OpenLoopSpec,
    PromiseCondition,
    PromiseConditionConfirmation,
    PromiseResolution,
    PromiseResolutionKind,
    PromiseResponsibleParty,
    PromiseSpec,
    WorldMoment,
)
from erii.models.turn import (
    ContextSignalSource,
    InteractionContextSignal,
    DeliveryDisposition,
    ReplyAttemptRecord,
    ReplyAttemptStage,
    ReplyContinuityAssessment,
    SourceProcessingChannel,
    SourceProcessingOutcome,
    SourceProcessingState,
    SourceTurnReceipt,
    TurnConflictError,
    TurnNotFoundError,
    TurnRecord,
    TurnStatus,
)
from erii.renderers.markdown import MarkdownRecallRenderer
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage
from erii.storage.file_storage import FileStorage
from erii.storage.memory_pack import memory_pack_remap_scope_id
from erii.vector.base import BaseEmbeddingProvider, BaseVectorStore
from erii.vector.in_memory_vector import CallableEmbeddingAdapter, DummyEmbeddingProvider


class _RelationshipImportGuardChanged(RuntimeError):
    """Signals that import must reacquire the target relationship guard."""


class DummyMockLLMAdapter(BaseLLMAdapter):
    """Fallback dummy LLM adapter when no LLM is provided."""

    def generate(self, prompt: str) -> str:
        return '{"timeline_entry": "Interaction logged", "impressions": []}'


class ERIIEngine:
    """Experiential Recall & Impression Integration Engine (E.R.I.I.)."""

    _INTERACTION_CONTEXT_CACHE_LIMIT = 256

    def __init__(
        self,
        storage_dir: str = "./erii_memory",
        llm: Optional[Union[BaseLLMAdapter, Callable[[str], str]]] = None,
        storage_driver: Optional[BaseStorage] = None,
        config: Optional[ERIIConfig] = None,
        task_queue: Optional[BaseTaskQueue] = None,
        vector_store: Optional[BaseVectorStore] = None,
        embedding_provider: Optional[Union[BaseEmbeddingProvider, Callable[[str], List[float]]]] = None,
        memory_extractor: Optional[MemoryExtractorV1] = None,
        relationship_event_extractor: Optional[RelationshipEventExtractorV1] = None,
        persona_reflection_interpreter: Optional[
            PersonaReflectionInterpreterV1
        ] = None,
        continuity_evaluator: Optional[ContinuityEvaluatorV1] = None,
        interaction_context_evaluator: Optional[
            InteractionContextEvaluatorV1
        ] = None,
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
            memory_extractor: Versioned host capability for reliable Source Turn archival.
            relationship_event_extractor: Strict host relationship-event extractor.
            persona_reflection_interpreter: Optional post-acceptance persona interpreter.
            continuity_evaluator: Optional host pre-delivery continuity evaluator.
            interaction_context_evaluator: Optional independent current-emotion
                evaluator used before contextual voice matching.
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
        self.relationship_consequence_coordinator = (
            RelationshipConsequenceCoordinator(self.storage)
        )
        self.continuity_evidence_resolver = ContinuityEvidenceResolver(self.storage)
        self.turn_ledger = TurnLedger(
            self.storage,
            evidence_resolver=self.continuity_evidence_resolver,
        )
        self.memory_extractor = memory_extractor
        self.relationship_event_extractor = relationship_event_extractor
        self.persona_reflection_interpreter = persona_reflection_interpreter
        self.continuity_evaluator = continuity_evaluator
        self.interaction_context_evaluator = interaction_context_evaluator
        self._interaction_context_cache: OrderedDict[
            Tuple[str, str, str, str],
            Tuple[InteractionContextSignal, ...],
        ] = OrderedDict()
        self._interaction_context_inflight: Dict[
            Tuple[str, str, str, str],
            Future,
        ] = {}
        self._interaction_context_cache_lock = threading.RLock()
        self.relationship_processing = RelationshipProcessingCoordinator(
            storage=self.storage,
            relationship_event_extractor=relationship_event_extractor,
            persona_reflection_interpreter=persona_reflection_interpreter,
        )
        self.archival_coordinator = ArchivalCoordinator(
            storage=self.storage,
            memory_extractor=memory_extractor,
            enable_sanitizer=self.config.enable_security_sanitizer,
            enable_pii_scrubbing=self.config.enable_pii_scrubbing,
            max_attempts=self.config.archival_max_attempts,
            base_delay_seconds=self.config.archival_base_delay_seconds,
            lease_seconds=self.config.archival_lease_seconds,
            commit_permit_seconds=self.config.archival_commit_permit_seconds,
            consumer_lease_seconds=self.config.archival_consumer_lease_seconds,
            max_memory_candidates=self.config.archival_max_memory_candidates,
            receipt_retention_days=self.config.archival_receipt_retention_days,
        )
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
                configured_root = os.path.abspath(
                    getattr(
                        self.storage,
                        "root_dir",
                        self.config.storage_dir,
                    )
                )
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
        warnings.warn(
            "ERIIEngine.remember() is deprecated in 0.4.0b1; removal is deferred "
            "to a later incompatible milestone; record a canonical Turn and call "
            "archive_turn() instead",
            DeprecationWarning,
            stacklevel=2,
        )
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
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        stored_nodes = self.storage.load_nodes(clean_agent, clean_user)
        if stored_nodes:
            self.storage.save_nodes(
                clean_agent,
                clean_user,
                self.decay_evaluator.sweep_nodes(stored_nodes),
            )
        request = RecallRequest(
            agent_id=clean_agent,
            user_id=clean_user,
            query=query,
            audience=RecallAudience.AGENT_PRIVATE,
            options=RecallOptions(
                top_k=top_k,
                reinforce=True,
            ),
        )
        result = self.recall_assembler.assemble(
            request,
            legacy_compat=True,
        )
        return MarkdownRecallRenderer(
            audience=RecallAudience.AGENT_PRIVATE,
        ).render(result)

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

        return self._initialize_relationship_on_storage(
            self.storage,
            clean_agent,
            clean_user,
            source_text,
            compiled_persona,
            premise=premise,
            source_format=source_format,
            source_name=source_name,
        )

    def _initialize_relationship_on_storage(
        self,
        storage: BaseStorage,
        agent_id: str,
        user_id: str,
        source_text: str,
        compiled_persona: Optional[Mapping[str, Any]],
        *,
        premise: RelationshipPremise,
        source_format: str,
        source_name: Optional[str],
    ) -> RelationshipProfile:
        """Initializes a relationship through an optional transaction view."""
        existing = storage.get_relationship(agent_id, user_id)
        if existing is not None:
            self._ensure_persona_matches(
                existing,
                source_text,
                compiled_persona,
                premise,
            )
            return existing

        agent_identity_id = storage.get_or_create_identity(
            IdentityKind.AGENT,
            agent_id,
        )
        user_identity_id = storage.get_or_create_identity(
            IdentityKind.USER,
            user_id,
        )
        profile = RelationshipProfile(
            relationship_id=str(uuid.uuid4()),
            persona_id=str(uuid.uuid4()),
            agent_identity_id=agent_identity_id,
            user_identity_id=user_identity_id,
            agent_id=agent_id,
            user_id=user_id,
            blueprint=CharacterBlueprint(
                blueprint_id=str(uuid.uuid4()),
                source_text=source_text,
                compiled=compiled_persona or {},
                source_format=source_format,
                source_name=source_name,
            ),
            premise=premise,
        )
        stored = storage.create_relationship(profile)
        self._ensure_persona_matches(stored, source_text, compiled_persona, premise)
        return stored

    @staticmethod
    def _host_observed_context(
        values: Sequence[
            Union[InteractionContextSignal, Mapping[str, object]]
        ],
    ) -> tuple[InteractionContextSignal, ...]:
        """Validates context supplied through a public host-controlled entrypoint."""
        signals = tuple(
            item
            if isinstance(item, InteractionContextSignal)
            else InteractionContextSignal.from_dict(item)
            for item in values
        )
        if any(
            item.source != ContextSignalSource.HOST_OBSERVED
            for item in signals
        ):
            raise ValueError(
                "public interaction_context accepts only host_observed signals; "
                "core_derived and evaluator_inferred signals require a "
                "relationship-scoped internal producer"
            )
        if any(item.relationship_id is not None for item in signals):
            raise ValueError(
                "public host_observed signals cannot set internal relationship, "
                "Turn, or producer scope metadata"
            )
        return signals

    def begin_turn(
        self,
        agent_id: str,
        user_id: str,
        user_message: str,
        *,
        turn_id: Optional[str] = None,
        interaction_context: Sequence[
            Union[InteractionContextSignal, Mapping[str, object]]
        ] = (),
    ) -> TurnRecord:
        """Persists the exact visible user message and opens a source turn."""
        profile = self._require_relationship(agent_id, user_id, "beginning a turn")
        host_context = self._host_observed_context(interaction_context)
        stable_turn_id = turn_id or str(uuid.uuid4())
        with self.storage.relationship_processing_guard(
            profile.relationship_id
        ):
            return self.turn_ledger.open(
                profile,
                user_message,
                turn_id=stable_turn_id,
                interaction_context=host_context,
            )

    def get_turn(
        self,
        agent_id: str,
        user_id: str,
        turn_id: str,
    ) -> TurnRecord:
        """Returns one durable turn scoped to the requested relationship."""
        profile = self._require_relationship(agent_id, user_id, "reading a turn")
        return self.turn_ledger.get(profile, turn_id)

    def list_turns(
        self,
        agent_id: str,
        user_id: str,
        *,
        status: Optional[Union[TurnStatus, str]] = None,
    ) -> List[TurnRecord]:
        """Lists durable turns for exactly one isolated relationship."""
        profile = self._require_relationship(agent_id, user_id, "listing turns")
        return self.turn_ledger.list(profile, status=status)

    def record_reply_attempt_failure(
        self,
        agent_id: str,
        user_id: str,
        turn_id: str,
        *,
        attempt_number: int,
        stage: Union[ReplyAttemptStage, str],
        capability_descriptor: str,
        failure_classification: str,
    ) -> ReplyAttemptRecord:
        """Records safe failure metadata without persisting an unseen draft."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "recording a reply attempt",
        )
        return self.turn_ledger.record_reply_attempt_failure(
            profile,
            turn_id,
            attempt_number=attempt_number,
            stage=stage,
            capability_descriptor=capability_descriptor,
            failure_classification=failure_classification,
        )

    def list_reply_attempts(
        self,
        agent_id: str,
        user_id: str,
        turn_id: str,
    ) -> List[ReplyAttemptRecord]:
        """Lists sanitized failed attempts for one relationship-scoped turn."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "listing reply attempts",
        )
        return self.turn_ledger.list_reply_attempts(profile, turn_id)

    def complete_turn(
        self,
        agent_id: str,
        user_id: str,
        turn_id: str,
        agent_message: str,
        *,
        continuity_assessment: Optional[
            Union[ReplyContinuityAssessment, Mapping[str, object]]
        ] = None,
        continuity_result: Optional[ContinuityEvaluationResult] = None,
        delivery_exception: Optional[
            Union[DeliveryExceptionRecord, Mapping[str, object]]
        ] = None,
        delivery_disposition: Union[
            DeliveryDisposition,
            str,
        ] = DeliveryDisposition.SHOWN,
        processing_channels: Optional[
            Sequence[Union[SourceProcessingChannel, str]]
        ] = None,
    ) -> SourceTurnReceipt:
        """Seals an open turn with the reply actually displayed by the host."""
        profile = self._require_relationship(agent_id, user_id, "completing a turn")
        receipt = self.turn_ledger.complete(
            profile,
            turn_id,
            agent_message,
            continuity_assessment=continuity_assessment,
            continuity_result=continuity_result,
            delivery_exception=delivery_exception,
            delivery_disposition=delivery_disposition,
            processing_channels=(
                processing_channels
                if processing_channels is not None
                else self._default_source_processing_channels()
            ),
        )
        self._evict_interaction_context_cache(
            profile.relationship_id,
            turn_id,
        )
        return receipt

    def abandon_turn(
        self,
        agent_id: str,
        user_id: str,
        turn_id: str,
        *,
        reason: str,
    ) -> TurnRecord:
        """Explicitly terminates an unanswered turn without inventing a reply."""
        profile = self._require_relationship(agent_id, user_id, "abandoning a turn")
        record = self.turn_ledger.abandon(profile, turn_id, reason=reason)
        self._evict_interaction_context_cache(
            profile.relationship_id,
            turn_id,
        )
        return record

    def record_turn(
        self,
        agent_id: str,
        user_id: str,
        user_message: str,
        agent_message: str,
        *,
        turn_id: Optional[str] = None,
        continuity_assessment: Optional[
            Union[ReplyContinuityAssessment, Mapping[str, object]]
        ] = None,
        delivery_exception: Optional[
            Union[DeliveryExceptionRecord, Mapping[str, object]]
        ] = None,
        delivery_disposition: Union[
            DeliveryDisposition,
            str,
        ] = DeliveryDisposition.SHOWN_UNREVIEWED,
        processing_channels: Optional[
            Sequence[Union[SourceProcessingChannel, str]]
        ] = None,
    ) -> SourceTurnReceipt:
        """Atomically records an exchange whose visible messages already exist."""
        profile = self._require_relationship(agent_id, user_id, "recording a turn")
        stable_turn_id = turn_id or str(uuid.uuid4())
        with self.storage.relationship_processing_guard(
            profile.relationship_id
        ):
            return self.turn_ledger.record(
                profile,
                user_message,
                agent_message,
                turn_id=stable_turn_id,
                continuity_assessment=continuity_assessment,
                delivery_exception=delivery_exception,
                delivery_disposition=delivery_disposition,
                processing_channels=(
                    processing_channels
                    if processing_channels is not None
                    else self._default_source_processing_channels()
                ),
            )

    def archive_turn(
        self,
        agent_id: str,
        user_id: str,
        source_turn_id: str,
        *,
        idempotency_key: str,
    ) -> Union[ArchivalReceipt, ArchivalTombstone]:
        """Submits one completed Source Turn to reliable memory archival.

        The configured ``async_archival`` flag selects deferred acceptance or
        inline processing. Both modes share the same durable archival identity
        and receipt model.
        """
        self.archival_coordinator.ensure_available()
        profile = self._require_relationship(
            agent_id,
            user_id,
            "archiving a Source Turn",
        )
        try:
            source_turn = self.turn_ledger.get(profile, source_turn_id)
        except TurnNotFoundError as exc:
            raise ArchivalSubmissionError(
                "invalid_source_turn: Source Turn was not found"
            ) from exc
        return self.archival_coordinator.submit(
            profile,
            source_turn,
            idempotency_key=idempotency_key,
            process_inline=not self.config.async_archival,
        )

    def get_archival_receipt(
        self,
        agent_id: str,
        user_id: str,
        archival_id: str,
    ) -> Union[ArchivalReceipt, ArchivalTombstone]:
        """Returns one receipt only inside the exact Agent x User scope."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "reading an archival receipt",
        )
        return self.archival_coordinator.get(
            profile.relationship_id,
            archival_id,
        )

    def list_archival_receipts(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[Union[ArchivalReceipt, ArchivalTombstone]]:
        """Lists operational archival receipts for one isolated relationship."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "listing archival receipts",
        )
        return self.archival_coordinator.list(profile.relationship_id)

    def compact_archival_receipts(self) -> int:
        """Compacts expired terminal receipts without deleting their artifacts."""
        return self.archival_coordinator.compact_expired()

    def process_relationship_turn(
        self,
        agent_id: str,
        user_id: str,
        source_turn_id: str,
        *,
        processing_mode: Union[
            SourceProcessingMode,
            str,
        ] = SourceProcessingMode.NORMAL,
        reprocessing_id: Optional[str] = None,
    ) -> RelationshipProcessingRun:
        """Synchronously extracts, adjudicates, and reflects on one sealed turn."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "processing a Source Turn relationship channel",
        )
        turn = self.turn_ledger.get(profile, source_turn_id)
        mode = (
            processing_mode
            if isinstance(processing_mode, SourceProcessingMode)
            else SourceProcessingMode(processing_mode)
        )
        return self.relationship_processing.process(
            profile,
            turn,
            processing_mode=mode,
            reprocessing_id=reprocessing_id,
        )

    def get_relationship_processing_run(
        self,
        agent_id: str,
        user_id: str,
        processing_id: str,
    ) -> RelationshipProcessingRun:
        """Returns one durable processing run inside the requested relationship."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "reading a relationship processing run",
        )
        return self.relationship_processing.get(
            profile.relationship_id,
            processing_id,
        )

    def list_relationship_processing_runs(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[RelationshipProcessingRun]:
        """Lists frozen relationship processing runs in durable order."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "listing relationship processing runs",
        )
        return self.relationship_processing.list(profile.relationship_id)

    def get_relationship_processing_receipt(
        self,
        agent_id: str,
        user_id: str,
        processing_id: str,
    ) -> RelationshipProcessingRun:
        """Compatibility name for a durable relationship processing run."""
        return self.get_relationship_processing_run(
            agent_id,
            user_id,
            processing_id,
        )

    def list_relationship_processing_receipts(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[RelationshipProcessingRun]:
        """Compatibility name for durable relationship processing runs."""
        return self.list_relationship_processing_runs(agent_id, user_id)

    def get_persona_reflection(
        self,
        agent_id: str,
        user_id: str,
        reflection_id: str,
    ) -> PersonaReflectionRecord:
        """Returns one formal, append-only persona reflection."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "reading a persona reflection",
        )
        return self.relationship_processing.get_reflection(
            profile.relationship_id,
            reflection_id,
        )

    def list_persona_reflections(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[PersonaReflectionRecord]:
        """Lists formal reflections without manufacturing no-op placeholders."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "listing persona reflections",
        )
        return self.relationship_processing.list_reflections(
            profile.relationship_id
        )

    def list_persona_reflection_decisions(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[PersonaReflectionDecisionRecord]:
        """Lists both reflection and explicit no-reflection decisions."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "listing persona reflection decisions",
        )
        return self.relationship_processing.list_reflection_decisions(
            profile.relationship_id
        )

    def correct_persona_reflection(
        self,
        agent_id: str,
        user_id: str,
        target_reflection_id: str,
        *,
        interpretation_id: str,
    ) -> PersonaReflectionDecisionRecord:
        """Appends a correction; the target reflection remains immutable."""
        return self._append_persona_reflection_interpretation(
            agent_id,
            user_id,
            target_reflection_id,
            interpretation_id=interpretation_id,
            record_kind=PersonaReflectionRecordKind.CORRECTION,
        )

    def reinterpret_persona_reflection(
        self,
        agent_id: str,
        user_id: str,
        target_reflection_id: str,
        *,
        interpretation_id: str,
    ) -> PersonaReflectionDecisionRecord:
        """Appends a later interpretation; prior understanding is preserved."""
        return self._append_persona_reflection_interpretation(
            agent_id,
            user_id,
            target_reflection_id,
            interpretation_id=interpretation_id,
            record_kind=PersonaReflectionRecordKind.REINTERPRETATION,
        )

    def _append_persona_reflection_interpretation(
        self,
        agent_id: str,
        user_id: str,
        target_reflection_id: str,
        *,
        interpretation_id: str,
        record_kind: PersonaReflectionRecordKind,
    ) -> PersonaReflectionDecisionRecord:
        profile = self._require_relationship(
            agent_id,
            user_id,
            "appending a persona reflection interpretation",
        )
        target = self.relationship_processing.get_reflection(
            profile.relationship_id,
            target_reflection_id,
        )
        source_turn_id = target.context_provenance.source_turn_id
        if source_turn_id is None:
            raise ValueError(
                "legacy reflection lacks Source Turn provenance for reinterpretation"
            )
        turn = self.turn_ledger.get(profile, source_turn_id)
        return self.relationship_processing.append_reflection_interpretation(
            profile,
            turn,
            target_reflection_id=target_reflection_id,
            interpretation_id=interpretation_id,
            record_kind=record_kind,
        )

    def get_relationship_consolidation(
        self,
        agent_id: str,
        user_id: str,
    ) -> RelationshipConsolidation:
        """Rebuilds deterministic episodes and chapters from authoritative events."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "projecting relationship consolidation",
        )
        return RelationshipConsolidator.project(
            profile.relationship_id,
            list_complete_relationship_events(
                self.storage,
                profile.relationship_id,
            ),
        )

    @staticmethod
    def _voice_condition_values(
        manifest: PersonaManifest,
        condition_type: VoicePatternConditionType,
    ) -> Tuple[str, ...]:
        values = []
        seen = set()
        for pattern in manifest.contextual_voice_patterns:
            for condition in pattern.conditions:
                if condition.condition_type != condition_type:
                    continue
                for value in condition.values:
                    folded = value.casefold()
                    if folded in seen:
                        continue
                    seen.add(folded)
                    values.append(value)
        return tuple(values)

    @staticmethod
    def _deduplicate_context_signals(
        signals: Sequence[InteractionContextSignal],
    ) -> Tuple[InteractionContextSignal, ...]:
        by_id: Dict[str, InteractionContextSignal] = {}
        for signal in signals:
            existing = by_id.get(signal.signal_id)
            if existing is not None and not existing.same_claim_as(signal):
                raise ValueError(
                    "one interaction context signal ID cannot carry "
                    "different claims"
                )
            by_id[signal.signal_id] = signal
        return tuple(by_id[key] for key in sorted(by_id))

    def _evict_interaction_context_cache(
        self,
        relationship_id: str,
        turn_id: str,
    ) -> None:
        """Drops temporary context projections when their Turn terminates."""
        with self._interaction_context_cache_lock:
            for key in tuple(self._interaction_context_cache):
                if key[0] == relationship_id and key[1] == turn_id:
                    self._interaction_context_cache.pop(key, None)

    def _evaluate_interaction_context_cached(
        self,
        request: InteractionContextEvaluationRequest,
        evaluator: InteractionContextEvaluatorV1,
    ) -> Tuple[InteractionContextSignal, ...]:
        """Single-flights one exact evaluator input without a global callback lock."""
        descriptor = InteractionContextEvaluationCoordinator._descriptor(
            evaluator
        )
        input_fingerprint = (
            InteractionContextEvaluationCoordinator.input_fingerprint(
                request,
                evaluator,
                descriptor=descriptor,
            )
        )
        cache_key = (
            request.relationship_id,
            request.turn_id,
            request.persona_manifest_id,
            input_fingerprint,
        )
        with self._interaction_context_cache_lock:
            cached = self._interaction_context_cache.get(cache_key)
            if cached is not None:
                self._interaction_context_cache.move_to_end(cache_key)
                return cached
            pending = self._interaction_context_inflight.get(cache_key)
            if pending is None:
                pending = Future()
                self._interaction_context_inflight[cache_key] = pending
                leader = True
            else:
                leader = False

        if not leader:
            return pending.result()

        try:
            inferred = InteractionContextEvaluationCoordinator.evaluate(
                request,
                evaluator,
                descriptor=descriptor,
                input_fingerprint=input_fingerprint,
            )
        except BaseException as exc:
            pending.set_exception(exc)
            with self._interaction_context_cache_lock:
                self._interaction_context_inflight.pop(cache_key, None)
            raise

        with self._interaction_context_cache_lock:
            self._interaction_context_cache[cache_key] = inferred
            self._interaction_context_cache.move_to_end(cache_key)
            while (
                len(self._interaction_context_cache)
                > self._INTERACTION_CONTEXT_CACHE_LIMIT
            ):
                self._interaction_context_cache.popitem(last=False)
            self._interaction_context_inflight.pop(cache_key, None)
        pending.set_result(inferred)
        return inferred

    def _derive_voice_context_signals(
        self,
        profile: RelationshipProfile,
        turn: TurnRecord,
        manifest: PersonaManifest,
        host_signals: Sequence[InteractionContextSignal],
    ) -> Tuple[InteractionContextSignal, ...]:
        if turn.context_baseline is None:
            raise ValueError("voice context requires a Turn Context Baseline")
        events = resolve_turn_context_history(
            self.storage,
            profile,
            turn.context_baseline,
        )
        snapshot = RelationshipProjector.project(profile, events)
        derived = []
        safety_values = self._voice_condition_values(
            manifest,
            VoicePatternConditionType.RELATIONSHIP_SAFETY,
        )
        if safety_values:
            derived.append(
                RelationshipSafetySignalProjector.project(
                    snapshot,
                    source_turn_id=turn.turn_id,
                    history_prefix_fingerprint=(
                        turn.context_baseline.history_prefix_fingerprint
                    ),
                )
            )

        emotion_values = self._voice_condition_values(
            manifest,
            VoicePatternConditionType.EMOTION,
        )
        evaluator = self.interaction_context_evaluator
        if emotion_values and evaluator is not None:
            request = InteractionContextEvaluationRequest(
                turn_id=turn.turn_id,
                relationship_id=profile.relationship_id,
                persona_id=profile.persona_id,
                persona_manifest_id=manifest.manifest_id,
                user_message_id=turn.transcript.user_message.message_id,
                user_message=turn.transcript.user_message.content,
                emotion_values=emotion_values,
                relationship_state=snapshot.state.to_dict(),
                recent_events=events[-16:],
                host_observed_signals=tuple(host_signals),
            )
            inferred = self._evaluate_interaction_context_cached(
                request,
                evaluator,
            )
            derived.extend(inferred)
        return tuple(derived)

    def activate_contextual_voice_patterns(
        self,
        agent_id: str,
        user_id: str,
        source_turn_id: str,
        *,
        interaction_context: Sequence[
            Union[InteractionContextSignal, Mapping[str, Any]]
        ] = (),
    ) -> Sequence[VoicePatternActivation]:
        """Matches temporary, source-backed voice patterns for one open turn."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "activating contextual voice patterns",
        )
        turn = self.turn_ledger.get(profile, source_turn_id)
        if turn.status != TurnStatus.OPEN:
            raise TurnConflictError(
                "contextual voice activation is only valid before turn delivery"
            )
        baseline = turn.context_baseline
        if baseline is None or baseline.manifest is None:
            raise PersonaManifestRequiredError(
                "contextual voice activation requires a Manifest frozen at "
                "Turn Opening"
            )
        try:
            manifest, _ = resolve_turn_context_authorities(
                self.storage,
                profile,
                baseline,
            )
        except ValueError as exc:
            raise PersonaManifestRequiredError(str(exc)) from exc
        turn_signals = tuple(
            signal
            for signal in turn.interaction_context
            if signal.source == ContextSignalSource.HOST_OBSERVED
        )
        extra_signals = self._host_observed_context(interaction_context)
        if extra_signals:
            raise ValueError(
                "host interaction context must be persisted by begin_turn()"
            )
        host_signals = self._deduplicate_context_signals(turn_signals)
        derived_signals = self._derive_voice_context_signals(
            profile,
            turn,
            manifest,
            host_signals,
        )
        return VoicePatternMatcher.match(
            manifest=manifest,
            relationship_id=profile.relationship_id,
            source_turn_id=turn.turn_id,
            persona_id=profile.persona_id,
            premise=profile.premise,
            signals=(*host_signals, *derived_signals),
            context_baseline_fingerprint=baseline.baseline_fingerprint,
        )

    def evaluate_reply_continuity(
        self,
        agent_id: str,
        user_id: str,
        source_turn_id: str,
        proposed_reply: str,
        *,
        persona_context_refs: Sequence[ContinuityEvidenceRefValue],
        relationship_context_refs: Sequence[ContinuityEvidenceRefValue] = (),
        interaction_context: Sequence[
            Union[InteractionContextSignal, Mapping[str, Any]]
        ] = (),
    ) -> ContinuityEvaluationResult:
        """Evaluates an unpersisted proposed reply before the host displays it."""
        evaluator = self.continuity_evaluator
        if evaluator is None:
            raise ContinuityEvaluationCapabilityError(
                "pre-delivery continuity evaluation is not configured"
            )
        profile = self._require_relationship(
            agent_id,
            user_id,
            "evaluating reply continuity",
        )
        turn = self.turn_ledger.get(profile, source_turn_id)
        if turn.status != TurnStatus.OPEN:
            raise TurnConflictError(
                "reply continuity evaluation is only valid before turn delivery"
            )
        baseline = turn.context_baseline
        if baseline is None or baseline.manifest is None:
            raise PersonaManifestRequiredError(
                "reply continuity evaluation requires a Manifest frozen at "
                "Turn Opening"
            )
        try:
            manifest, _ = resolve_turn_context_authorities(
                self.storage,
                profile,
                baseline,
            )
        except ValueError as exc:
            raise PersonaManifestRequiredError(str(exc)) from exc
        resolved_persona_refs = (
            self.continuity_evidence_resolver.resolve_persona_refs(
                profile,
                baseline,
                persona_context_refs,
            )
        )
        resolved_relationship_refs = (
            self.continuity_evidence_resolver.resolve_relationship_refs(
                profile,
                baseline,
                relationship_context_refs,
            )
        )
        activations = self.activate_contextual_voice_patterns(
            agent_id,
            user_id,
            source_turn_id,
            interaction_context=interaction_context,
        )
        request = ContinuityEvaluationRequest(
            turn_id=turn.turn_id,
            relationship_id=profile.relationship_id,
            persona_id=profile.persona_id,
            user_message=turn.transcript.user_message.content,
            proposed_reply=proposed_reply,
            persona_manifest_id=manifest.manifest_id,
            context_baseline_fingerprint=baseline.baseline_fingerprint,
            persona_context_refs=resolved_persona_refs,
            relationship_context_refs=resolved_relationship_refs,
            voice_pattern_activations=tuple(activations),
        )
        return ContinuityEvaluationCoordinator.evaluate(request, evaluator)

    def get_source_processing_outcomes(
        self,
        agent_id: str,
        user_id: str,
        source_turn_id: str,
    ) -> Sequence[SourceProcessingOutcome]:
        """Projects live channel truth without mutating the sealed TurnRecord."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "reading Source Turn processing outcomes",
        )
        turn = self.turn_ledger.get(profile, source_turn_id)
        outcomes = {
            item.channel: item for item in turn.processing_outcomes
        }
        if (
            SourceProcessingChannel.MEMORY_ARCHIVAL in outcomes
            and self.archival_coordinator.query_available
        ):
            matching = [
                receipt
                for receipt in self.archival_coordinator.list(
                    profile.relationship_id
                )
                if receipt.source_turn_id == source_turn_id
                and receipt.source_revision == turn.source_revision
            ]
            if matching:
                receipt = matching[-1]
                state = SourceProcessingState.PENDING
                if receipt.status == ArchivalStatus.COMPLETED:
                    state = (
                        SourceProcessingState.NO_OUTPUT
                        if receipt.outcome_code == ArchivalOutcomeCode.NO_MEMORY
                        else SourceProcessingState.ARTIFACTS_COMMITTED
                    )
                elif receipt.status == ArchivalStatus.FAILED:
                    state = SourceProcessingState.FAILED
                outcomes[SourceProcessingChannel.MEMORY_ARCHIVAL] = (
                    SourceProcessingOutcome(
                        channel=SourceProcessingChannel.MEMORY_ARCHIVAL,
                        state=state,
                        updated_at=(
                            receipt.updated_at
                            if isinstance(receipt, ArchivalReceipt)
                            else receipt.terminal_at
                        ),
                    )
                )
        if (
            SourceProcessingChannel.RELATIONSHIP_ADJUDICATION in outcomes
            and self.relationship_processing.query_available
        ):
            matching_runs = [
                run
                for run in self.relationship_processing.list(
                    profile.relationship_id
                )
                if run.source_turn_id == source_turn_id
                and run.source_revision == turn.source_revision
                and run.processing_mode == SourceProcessingMode.NORMAL
            ]
            if matching_runs:
                run = matching_runs[-1]
                state = SourceProcessingState.PENDING
                if run.status == RelationshipProcessingStatus.COMPLETED:
                    state = (
                        SourceProcessingState.NO_OUTPUT
                        if run.outcome
                        in (
                            RelationshipProcessingOutcome.NO_RELATIONSHIP_EVENT,
                            RelationshipProcessingOutcome.NO_ACCEPTED_EVENTS,
                        )
                        else SourceProcessingState.ARTIFACTS_COMMITTED
                    )
                elif run.status in (
                    RelationshipProcessingStatus.PARTIAL_FAILED,
                    RelationshipProcessingStatus.FAILED,
                ):
                    state = SourceProcessingState.FAILED
                outcomes[
                    SourceProcessingChannel.RELATIONSHIP_ADJUDICATION
                ] = SourceProcessingOutcome(
                    channel=SourceProcessingChannel.RELATIONSHIP_ADJUDICATION,
                    state=state,
                    updated_at=run.updated_at,
                )
        return tuple(
            outcomes[channel] for channel in turn.processing_plan.channels
        )

    def drain(self, timeout: float = 30.0) -> ArchivalDrainReport:
        """Explicitly drains the archival submission snapshot within a deadline."""
        return self.archival_coordinator.drain(timeout)

    def drain_archival(self, timeout: float = 30.0) -> ArchivalDrainReport:
        """Compatibility alias for :meth:`drain`."""
        return self.drain(timeout)

    def _default_source_processing_channels(
        self,
    ) -> Sequence[SourceProcessingChannel]:
        """Returns only processors actually configured on this Engine."""
        channels = []
        if getattr(self, "memory_extractor", None) is not None:
            channels.append(SourceProcessingChannel.MEMORY_ARCHIVAL)
        if getattr(self, "relationship_event_extractor", None) is not None:
            channels.append(SourceProcessingChannel.RELATIONSHIP_ADJUDICATION)
        return tuple(channels)

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

    @staticmethod
    def _ensure_bound_relationship_matches(
        existing: RelationshipProfile,
        incoming: RelationshipProfile,
    ) -> None:
        """Requires every immutable identity behind portable provenance to match."""
        existing_data = existing.to_dict()
        incoming_data = incoming.to_dict()
        # Manifest approval is an append-only persona-compilation state. Its
        # exact merge/conflict rules are preflighted by _import_persona_compilation.
        existing_data.pop("manifest_id", None)
        incoming_data.pop("manifest_id", None)
        if existing_data != incoming_data:
            raise PersonaConflictError(
                "MemoryPack bound provenance conflicts with the target's "
                "immutable relationship or Character Blueprint identity"
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
        if parsed_decision == PersonaCompilationDecision.APPROVE:
            validate_persona_premise_binding(
                profile.premise,
                current.candidate,
            )
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
        with self.storage.relationship_processing_guard(
            profile.relationship_id
        ):
            return self.storage.append_relationship_event(event)

    def record_promise(
        self,
        agent_id: str,
        user_id: str,
        action: str,
        responsible_parties: Sequence[Union[PromiseResponsibleParty, str]],
        *,
        due_at: Optional[Union[WorldMoment, Mapping[str, Any]]] = None,
        activation_condition: Optional[
            Union[PromiseCondition, Mapping[str, Any]]
        ] = None,
        content: Optional[str] = None,
        occurred_at: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> RelationshipEvent:
        """Appends one trusted, explicitly structured Promise.

        This is a host-authority API. Untrusted model output must cross
        ``adjudicate_relationship_candidates`` instead.
        """
        profile = self._require_relationship(agent_id, user_id, "recording a Promise")
        payload = PromiseSpec(
            responsible_parties=responsible_parties,
            action=action,
            due_at=due_at,
            activation_condition=activation_condition,
        )
        return self._append_temporal_event(
            profile,
            RelationshipEvent(
                event_id=event_id or str(uuid.uuid4()),
                relationship_id=profile.relationship_id,
                event_type=RelationshipEventType.PROMISE,
                content=content or payload.action,
                occurred_at=occurred_at,
                temporal_payload=payload,
            ),
        )

    def confirm_promise_condition(
        self,
        agent_id: str,
        user_id: str,
        promise_event_id: str,
        condition_id: str,
        *,
        confirmed_at: Optional[Union[WorldMoment, Mapping[str, Any]]] = None,
        content: Optional[str] = None,
        occurred_at: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> RelationshipEvent:
        """Appends evidence that a condition attached to one Promise occurred."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "confirming a Promise condition",
        )
        payload = PromiseConditionConfirmation(
            promise_event_id=promise_event_id,
            condition_id=condition_id,
            confirmed_at=confirmed_at,
        )
        return self._append_temporal_event(
            profile,
            RelationshipEvent(
                event_id=event_id or str(uuid.uuid4()),
                relationship_id=profile.relationship_id,
                event_type=RelationshipEventType.PROMISE_CONDITION_CONFIRMED,
                content=content or f"Promise condition confirmed: {condition_id}",
                occurred_at=occurred_at,
                temporal_payload=payload,
            ),
        )

    def resolve_promise(
        self,
        agent_id: str,
        user_id: str,
        promise_event_id: str,
        resolution_kind: Union[PromiseResolutionKind, str],
        *,
        superseding_promise_event_id: Optional[str] = None,
        resolved_at: Optional[Union[WorldMoment, Mapping[str, Any]]] = None,
        note: Optional[str] = None,
        content: Optional[str] = None,
        occurred_at: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> RelationshipEvent:
        """Resolves a Promise by appending history; the original remains unchanged."""
        profile = self._require_relationship(agent_id, user_id, "resolving a Promise")
        payload = PromiseResolution(
            promise_event_id=promise_event_id,
            resolution_kind=resolution_kind,
            superseding_promise_event_id=superseding_promise_event_id,
            resolved_at=resolved_at,
            note=note,
        )
        return self._append_temporal_event(
            profile,
            RelationshipEvent(
                event_id=event_id or str(uuid.uuid4()),
                relationship_id=profile.relationship_id,
                event_type=RelationshipEventType.PROMISE_RESOLUTION,
                content=content or f"Promise resolved: {payload.resolution_kind.value}",
                occurred_at=occurred_at,
                temporal_payload=payload,
            ),
        )

    def record_open_loop(
        self,
        agent_id: str,
        user_id: str,
        subject: str,
        *,
        expected_continuation: Optional[str] = None,
        origin_memory_node_id: Optional[str] = None,
        content: Optional[str] = None,
        occurred_at: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> RelationshipEvent:
        """Appends an unfinished matter without inventing a responsible party."""
        profile = self._require_relationship(agent_id, user_id, "recording an Open Loop")
        if origin_memory_node_id is not None:
            nodes = self.storage.load_nodes(profile.agent_id, profile.user_id)
            matching = [item for item in nodes if item.node_id == origin_memory_node_id]
            if (
                not matching
                or not matching[0].is_unresolved
                or not matching[0].is_latest
            ):
                raise ValueError(
                    "origin_memory_node_id must identify an active unresolved memory "
                    "for this pair"
                )
        payload = OpenLoopSpec(
            subject=subject,
            expected_continuation=expected_continuation,
            origin_memory_node_id=origin_memory_node_id,
        )
        return self._append_temporal_event(
            profile,
            RelationshipEvent(
                event_id=event_id or str(uuid.uuid4()),
                relationship_id=profile.relationship_id,
                event_type=RelationshipEventType.OPEN_LOOP,
                content=content or payload.subject,
                occurred_at=occurred_at,
                temporal_payload=payload,
            ),
        )

    def resolve_open_loop(
        self,
        agent_id: str,
        user_id: str,
        open_loop_event_id: str,
        resolution_kind: Union[OpenLoopResolutionKind, str],
        *,
        superseding_open_loop_event_id: Optional[str] = None,
        note: Optional[str] = None,
        content: Optional[str] = None,
        occurred_at: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> RelationshipEvent:
        """Closes an Open Loop through a later immutable event."""
        profile = self._require_relationship(agent_id, user_id, "resolving an Open Loop")
        payload = OpenLoopResolution(
            open_loop_event_id=open_loop_event_id,
            resolution_kind=resolution_kind,
            superseding_open_loop_event_id=superseding_open_loop_event_id,
            note=note,
        )
        return self._append_temporal_event(
            profile,
            RelationshipEvent(
                event_id=event_id or str(uuid.uuid4()),
                relationship_id=profile.relationship_id,
                event_type=RelationshipEventType.OPEN_LOOP_RESOLUTION,
                content=content or f"Open Loop resolved: {payload.resolution_kind.value}",
                occurred_at=occurred_at,
                temporal_payload=payload,
            ),
        )

    def _append_temporal_event(
        self,
        profile: RelationshipProfile,
        event: RelationshipEvent,
    ) -> RelationshipEvent:
        """Validates temporal history before the storage-level atomic recheck."""
        with self.storage.relationship_processing_guard(
            profile.relationship_id
        ):
            history = list_complete_relationship_events(
                self.storage,
                profile.relationship_id,
            )
            TemporalHistoryValidator.validate_append(history, event)
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
        warnings.warn(
            "ERIIEngine.adjudicate_relationship_candidates() is deprecated in "
            "0.4.0b1; removal is deferred to a later incompatible milestone. Use "
            "adjudicate_turn_candidates() for a persisted Turn or "
            "process_relationship_turn() for automatic processing",
            DeprecationWarning,
            stacklevel=2,
        )
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
        with self.storage.relationship_processing_guard(
            profile.relationship_id
        ):
            # The persisted/transient classification and the resulting journal
            # commit share one relationship guard.  Otherwise a canonical Turn
            # could be created in between these operations and silently inherit
            # a transient decision contract.
            persisted_turn = self._persisted_turn_for_direct_adjudication(
                profile,
                validated_turn,
            )
            if persisted_turn is not None:
                validated_turn = validated_turn.model_copy(
                    update={"contract_version": PERSISTED_TURN_CONTRACT_VERSION}
                )
            elif validated_turn.contract_version in {
                PERSISTED_TURN_CONTRACT_VERSION,
                "relationship-processing-v1",
            }:
                raise ValueError(
                    "transient direct adjudication cannot claim a reserved "
                    "persisted contract_version"
                )
            return self.relationship_adjudicator.adjudicate(
                profile,
                validated_turn,
                validated_batch,
                quarantined_source_ids=(
                    quarantined_agent_source_ids(persisted_turn)
                    if persisted_turn is not None
                    else ()
                ),
            )

    def _persisted_turn_for_direct_adjudication(
        self,
        profile: RelationshipProfile,
        source_turn: SourceTurn,
    ) -> Optional[TurnRecord]:
        """Binds the legacy direct API to persisted delivery authority when present."""
        try:
            record = self.storage.get_turn_record(
                profile.relationship_id,
                source_turn.turn_id,
            )
        except (AttributeError, LookupError, NotImplementedError):
            return None
        if record.status != TurnStatus.COMPLETED:
            raise ValueError(
                "direct adjudication cannot reuse an incomplete persisted Turn"
            )
        if record.source_revision != source_turn.revision:
            raise ValueError(
                "direct adjudication Source Turn revision does not match persisted data"
            )

        persisted_messages = [record.transcript.user_message]
        if record.transcript.agent_message is not None:
            persisted_messages.append(record.transcript.agent_message)
        supplied_by_id = {message.source_id: message for message in source_turn.messages}
        if (
            len(supplied_by_id) != len(source_turn.messages)
            or len(supplied_by_id) != len(persisted_messages)
        ):
            raise ValueError(
                "direct adjudication Source Turn does not match persisted messages"
            )
        for message in persisted_messages:
            supplied = supplied_by_id.get(message.message_id)
            if (
                supplied is None
                or supplied.revision != record.source_revision
                or supplied.role.value != message.role.value
                or supplied.content != message.content
                or supplied.occurred_at != message.recorded_at
            ):
                raise ValueError(
                    "direct adjudication Source Turn does not match persisted messages"
                )
        return record

    def adjudicate_turn_candidates(
        self,
        agent_id: str,
        user_id: str,
        source_turn_id: str,
        candidates: Sequence[
            Union[RelationshipEventCandidate, Mapping[str, Any]]
        ],
        *,
        extractor_version: str,
    ) -> AdjudicationBatchResult:
        """Adjudicates candidates against one persisted completed source turn."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "adjudicating a source turn",
        )
        record = self.turn_ledger.get(profile, source_turn_id)
        if record.status != TurnStatus.COMPLETED:
            raise ValueError("relationship adjudication requires a completed source turn")
        visible_messages = (
            record.transcript.user_message,
            record.transcript.agent_message,
        )
        source_turn = SourceTurn(
            turn_id=record.turn_id,
            revision=record.source_revision,
            messages=[
                SourceMessage(
                    source_id=message.message_id,
                    revision=record.source_revision,
                    role=(
                        SourceRole.USER
                        if message.role.value == SourceRole.USER.value
                        else SourceRole.AGENT
                    ),
                    content=message.content,
                    occurred_at=message.recorded_at,
                )
                for message in visible_messages
                if message is not None
            ],
            extractor_version=extractor_version,
            contract_version=PERSISTED_TURN_CONTRACT_VERSION,
        )
        validated_batch = RelationshipCandidateBatch.model_validate(
            {"candidates": list(candidates)}
        )
        return self.relationship_adjudicator.adjudicate(
            profile,
            source_turn,
            validated_batch,
            quarantined_source_ids=quarantined_agent_source_ids(record),
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

    def record_relationship_consequence(
        self,
        agent_id: str,
        user_id: str,
        source_turn_id: str,
        source_decision_id: str,
        source_event_id: str,
        effects: Sequence[Union[RelationshipConsequenceKind, str]],
        summary: str,
        *,
        consequence_id: Optional[str] = None,
        tension_id: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> RelationshipConsequence:
        """Records one consequence of an exact supported, shown Agent choice."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "recording a relationship consequence",
        )
        return self.relationship_consequence_coordinator.record_consequence(
            profile.relationship_id,
            source_turn_id=source_turn_id,
            source_decision_id=source_decision_id,
            source_event_id=source_event_id,
            effects=effects,
            summary=summary,
            consequence_id=consequence_id,
            tension_id=tension_id,
            recorded_at=recorded_at,
        )

    def append_relationship_consequence(
        self,
        agent_id: str,
        user_id: str,
        consequence: Union[RelationshipConsequence, Mapping[str, Any]],
    ) -> RelationshipConsequence:
        """Validates and appends a pre-built immutable consequence record."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "appending a relationship consequence",
        )
        validated = (
            consequence
            if isinstance(consequence, RelationshipConsequence)
            else RelationshipConsequence.from_dict(consequence)
        )
        return self.relationship_consequence_coordinator.append_consequence(
            profile.relationship_id,
            validated,
        )

    def list_relationship_consequences(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[RelationshipConsequence]:
        """Returns source-bound consequences for one isolated relationship."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "reading relationship consequences",
        )
        return list(
            self.relationship_consequence_coordinator.list_consequences(
                profile.relationship_id
            )
        )

    def record_narrative_tension_link(
        self,
        agent_id: str,
        user_id: str,
        consequence_id: str,
        source_turn_id: str,
        source_decision_id: str,
        source_event_id: str,
        outcome: Union[NarrativeTensionOutcome, str],
        summary: str,
        *,
        link_id: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> NarrativeTensionLink:
        """Links one later accepted event to a consequence's tension."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "recording a Narrative Tension link",
        )
        return self.relationship_consequence_coordinator.record_tension_link(
            profile.relationship_id,
            consequence_id=consequence_id,
            source_turn_id=source_turn_id,
            source_decision_id=source_decision_id,
            source_event_id=source_event_id,
            outcome=outcome,
            summary=summary,
            link_id=link_id,
            recorded_at=recorded_at,
        )

    def append_narrative_tension_link(
        self,
        agent_id: str,
        user_id: str,
        link: Union[NarrativeTensionLink, Mapping[str, Any]],
    ) -> NarrativeTensionLink:
        """Validates and appends a pre-built immutable tension link."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "appending a Narrative Tension link",
        )
        validated = (
            link
            if isinstance(link, NarrativeTensionLink)
            else NarrativeTensionLink.from_dict(link)
        )
        return self.relationship_consequence_coordinator.append_tension_link(
            profile.relationship_id,
            validated,
        )

    def list_narrative_tension_links(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[NarrativeTensionLink]:
        """Returns append-only tension links for one isolated relationship."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "reading Narrative Tension links",
        )
        return list(
            self.relationship_consequence_coordinator.list_links(
                profile.relationship_id
            )
        )

    def list_narrative_tensions(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[NarrativeTensionProjection]:
        """Deterministically projects current tension state from both journals."""
        profile = self._require_relationship(
            agent_id,
            user_id,
            "projecting Narrative Tensions",
        )
        return list(
            self.relationship_consequence_coordinator.project(
                profile.relationship_id
            )
        )

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
            timeline_entries = self.storage.list_timeline_entries(
                clean_agent,
                clean_user,
            )
        except NotImplementedError:
            timeline_entries = []
        try:
            relationship = self.storage.get_relationship(clean_agent, clean_user)
        except NotImplementedError:
            relationship = None

        relationship_events = []
        relationship_direct_event_ids = []
        relationship_adjudications = []
        relationship_consequences = []
        narrative_tension_links = []
        persona_growth_proposals = []
        persona_compilation_proposals = []
        persona_manifests = []
        turn_records = []
        relationship_processing_runs = []
        persona_reflection_decisions = []
        archival_ledger = []
        if relationship is not None:
            # Processing writes its event, adjudication, run, and reflection
            # ledgers in several durable phases. Hold the same relationship
            # guard used by the coordinator so the exported graph cannot
            # observe a half-finished phase transition.
            with self.storage.relationship_processing_guard(
                relationship.relationship_id
            ):
                try:
                    relationship_direct_event_ids = [
                        event.event_id
                        for event in self.storage.list_relationship_events(
                            relationship.relationship_id
                        )
                    ]
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
                    relationship_consequences = (
                        self.storage.list_relationship_consequences(
                            relationship.relationship_id
                        )
                    )
                    narrative_tension_links = (
                        self.storage.list_narrative_tension_links(
                            relationship.relationship_id
                        )
                    )
                except NotImplementedError:
                    pass
                try:
                    persona_growth_proposals = (
                        self.storage.list_persona_growth_proposals(
                            relationship.relationship_id
                        )
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
                try:
                    turn_records = self.storage.list_turn_records(
                        relationship.relationship_id
                    )
                except NotImplementedError:
                    pass
                try:
                    archival_ledger = self.storage.list_archival_tombstones(
                        relationship.relationship_id
                    )
                except NotImplementedError:
                    pass
                try:
                    relationship_processing_runs = (
                        self.storage.list_relationship_processing_runs(
                            relationship.relationship_id
                        )
                    )
                    persona_reflection_decisions = (
                        self.storage.list_persona_reflection_decisions(
                            relationship.relationship_id
                        )
                    )
                except NotImplementedError:
                    pass

        pack = assemble_memory_pack_export(
            MemoryPackExportSnapshot(
            agent_id=clean_agent,
            user_id=clean_user,
            core_memory=core_mem,
                nodes=tuple(nodes),
                legacy_timeline=tuple(timeline),
                timeline_entries=tuple(timeline_entries),
                archival_tombstones=tuple(archival_ledger),
            relationship=relationship,
                relationship_events=tuple(relationship_events),
                relationship_direct_event_ids=tuple(
                    relationship_direct_event_ids
                ),
                relationship_adjudications=tuple(
                    relationship_adjudications
                ),
                relationship_consequences=tuple(
                    relationship_consequences
                ),
                narrative_tension_links=tuple(narrative_tension_links),
                persona_growth_proposals=tuple(persona_growth_proposals),
                persona_compilation_proposals=tuple(
                    persona_compilation_proposals
                ),
                persona_manifests=tuple(persona_manifests),
                turn_records=tuple(turn_records),
                relationship_processing_runs=tuple(
                    relationship_processing_runs
                ),
                persona_reflection_decisions=tuple(
                    persona_reflection_decisions
                ),
            )
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
        """Imports a MemoryPack while serializing exact-identity relationship writes."""
        if isinstance(pack_or_path, str):
            with open(pack_or_path, "r", encoding="utf-8") as f:
                pack = MemoryPack.from_json(f.read())
        elif isinstance(pack_or_path, dict):
            pack = MemoryPack.from_dict(pack_or_path)
        else:
            pack = pack_or_path

        validate_memory_pack_node_types(pack)

        target_agent = agent_id or pack.agent_id
        target_user = user_id or pack.user_id
        clean_agent = SecuritySanitizer.validate_key(target_agent, "agent_id")
        clean_user = SecuritySanitizer.validate_key(target_user, "user_id")
        relationship = pack.relationship
        if relationship is None:
            return self._import_memory_unlocked(
                pack,
                agent_id=clean_agent,
                user_id=clean_user,
                overwrite=overwrite,
            )

        provisional_guard_id = memory_pack_remap_scope_id(clean_agent, clean_user)
        for _ in range(4):
            existing_profile = self.storage.get_relationship(
                clean_agent,
                clean_user,
            )
            if existing_profile is not None:
                guard_relationship_id = existing_profile.relationship_id
            elif (
                clean_agent == relationship.agent_id
                and clean_user == relationship.user_id
            ):
                guard_relationship_id = relationship.relationship_id
            else:
                # Legacy remapping does not know its fresh relationship ID yet.
                # The first pass creates only that profile, then retries under
                # its real guard before importing any memory or history.
                guard_relationship_id = provisional_guard_id

            with self.storage.relationship_processing_guard(
                guard_relationship_id
            ):
                locked_profile = self.storage.get_relationship(
                    clean_agent,
                    clean_user,
                )
                if (
                    locked_profile is not None
                    and locked_profile.relationship_id
                    != guard_relationship_id
                ):
                    continue
                try:
                    return self._import_memory_unlocked(
                        pack,
                        agent_id=clean_agent,
                        user_id=clean_user,
                        overwrite=overwrite,
                        held_relationship_id=guard_relationship_id,
                    )
                except _RelationshipImportGuardChanged:
                    continue
        raise RuntimeError(
            "target relationship changed repeatedly while MemoryPack import "
            "was acquiring its processing guard"
        )

    def _import_memory_unlocked(
        self,
        pack_or_path: Union[MemoryPack, str, Dict[str, Any]],
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None,
        overwrite: bool = False,
        held_relationship_id: Optional[str] = None,
    ) -> MemoryPack:
        """Implements import after any discoverable relationship guard is held."""
        if isinstance(pack_or_path, str):
            with open(pack_or_path, "r", encoding="utf-8") as f:
                pack = MemoryPack.from_json(f.read())
        elif isinstance(pack_or_path, dict):
            pack = MemoryPack.from_dict(pack_or_path)
        else:
            pack = pack_or_path

        transfer_source = analyze_memory_pack_source(pack)
        pack_analysis = transfer_source.analysis

        target_agent = agent_id or pack.agent_id
        target_user = user_id or pack.user_id

        clean_agent = SecuritySanitizer.validate_key(target_agent, "agent_id")
        clean_user = SecuritySanitizer.validate_key(target_user, "user_id")
        has_bound_archival_history = pack_analysis.has_bound_archival_history
        requires_exact_relationship_restore = (
            pack_analysis.requires_exact_relationship_restore
        )
        if has_bound_archival_history and (
            clean_agent != pack.agent_id or clean_user != pack.user_id
        ):
            raise ValueError(
                "MemoryPack archival provenance cannot be remapped to another "
                "Agent x User scope"
            )
        self._validate_turn_pack(pack, clean_agent, clean_user)
        validate_memory_pack_archival_evidence(pack)
        validate_memory_pack_relationship_consequences(pack)
        import_operation_id = memory_pack_import_operation_id(
            transfer_source,
            clean_agent,
            clean_user,
            overwrite=overwrite,
        )
        receipt_relationship_id = None
        if pack.relationship is not None:
            if (
                clean_agent == pack.relationship.agent_id
                and clean_user == pack.relationship.user_id
            ):
                receipt_relationship_id = pack.relationship.relationship_id
            else:
                receipt_relationship_id = memory_pack_remap_scope_id(
                    clean_agent,
                    clean_user,
                )
        receipt_capability_provider = getattr(
            self.storage,
            "atomic_memory_pack_write_store_v2",
            None,
        )
        receipt_capability = (
            receipt_capability_provider()
            if callable(receipt_capability_provider)
            else None
        )
        if receipt_capability is not None:
            committed_result = receipt_capability.load_memory_pack_write_result(
                import_operation_id,
                clean_agent,
                clean_user,
                receipt_relationship_id,
                lambda result_json: memory_pack_import_result_from_json(
                    result_json,
                    pack,
                ),
            )
            if committed_result is not None:
                return committed_result
        existing_target_profile = self.storage.get_relationship(
            clean_agent,
            clean_user,
        )
        target_reads = MemoryPackTargetReadRecorder(self.storage)
        if (
            pack.relationship is not None
            and existing_target_profile is not None
        ):
            if requires_exact_relationship_restore:
                self._ensure_bound_relationship_matches(
                    existing_target_profile,
                    pack.relationship,
                )
            else:
                self._ensure_persona_matches(
                    existing_target_profile,
                    pack.relationship.blueprint.source_text,
                    pack.relationship.blueprint.compiled,
                    pack.relationship.premise,
                )
        self._validate_timeline_import_conflicts(
            pack,
            clean_agent,
            clean_user,
            existing_target_profile,
            storage=target_reads,
        )
        self._validate_relationship_adjudication_import_conflicts(
            pack,
            existing_target_profile,
            storage=target_reads,
        )
        self._validate_relationship_consequence_import_conflicts(
            pack,
            existing_target_profile,
            storage=target_reads,
        )
        validate_memory_pack_persisted_turn_adjudications(
            pack,
            clean_agent,
            clean_user,
            (
                existing_target_profile.relationship_id
                if existing_target_profile is not None
                else None
            ),
        )
        self._validate_relationship_processing_pack(
            pack,
            clean_agent,
            clean_user,
            existing_target_profile,
            storage=target_reads,
        )
        has_persona_compilation_payload = bool(
            pack.relationship is not None
            and (
                pack.persona_compilation_proposals
                or pack.persona_manifests
                or pack.relationship.manifest_id
            )
        )
        preflight_profile = existing_target_profile
        if (
            preflight_profile is None
            and pack.relationship is not None
            and clean_agent == pack.relationship.agent_id
            and clean_user == pack.relationship.user_id
        ):
            preflight_profile = pack.relationship
        if has_persona_compilation_payload and preflight_profile is not None:
            compilation_plan = plan_memory_pack_persona_compilation_writes(
                pack,
                preflight_profile,
            )
            assert compilation_plan is not None
            execute_memory_pack_persona_compilation(
                target_reads,
                compilation_plan,
                preflight_profile,
                validate_only=True,
            )
        if pack.archival_ledger:
            if pack.relationship is None:
                raise ValueError(
                    "MemoryPack archival ledger requires a relationship profile"
                )
            if (
                existing_target_profile is not None
                and existing_target_profile.relationship_id
                != pack.relationship.relationship_id
            ):
                raise ValueError(
                    "MemoryPack archival provenance requires exact relationship restore"
                )
            target_reads.validate_archival_tombstones(
                pack.relationship.relationship_id,
                pack.archival_ledger,
            )
        if pack.turn_records:
            existing_turn_profile = existing_target_profile
            if (
                existing_turn_profile is not None
                and existing_turn_profile.relationship_id
                != pack.relationship.relationship_id
            ):
                raise ValueError(
                    "MemoryPack source transcripts cannot be remapped to a "
                    "different relationship"
                )
            self._validate_turn_import_conflicts(
                pack,
                existing_turn_profile,
                storage=target_reads,
            )
        persona_growth_preflighted = bool(
            pack.persona_growth_proposals and preflight_profile is not None
        )
        if persona_growth_preflighted:
            preflight_growth_proposals = plan_memory_pack_persona_growth_writes(
                pack,
                preflight_profile.relationship_id,
            )
            self._validate_persona_growth_import_conflicts(
                preflight_growth_proposals,
                preflight_profile.relationship_id,
                storage=target_reads,
            )

        frozen_target_reads = target_reads.freeze()
        transfer_plan = bind_memory_pack_transfer_plan(
            transfer_source,
            pack,
            clean_agent,
            clean_user,
            existing_target_profile,
            overwrite=overwrite,
            target_reads=frozen_target_reads,
        )
        atomic_relationship_id = transfer_plan.target.relationship_id
        if (
            atomic_relationship_id is None
            and pack.relationship is not None
            and clean_agent == pack.relationship.agent_id
            and clean_user == pack.relationship.user_id
        ):
            atomic_relationship_id = pack.relationship.relationship_id

        preserve_remapped_persona_rejection = False

        def execute_import(transactional_storage: BaseStorage) -> MemoryPack:
            """Revalidate and execute the complete import atomically."""
            nonlocal preserve_remapped_persona_rejection
            current_target_profile = transactional_storage.get_relationship(
                clean_agent,
                clean_user,
            )
            require_memory_pack_transfer_plan_current(
                transfer_plan,
                pack,
                current_target_profile,
            )
            replay_memory_pack_target_read_set(
                transactional_storage,
                frozen_target_reads,
            )
            current_target_profile = transactional_storage.get_relationship(
                clean_agent,
                clean_user,
            )
            require_memory_pack_transfer_plan_current(
                transfer_plan,
                pack,
                current_target_profile,
            )

            target_profile: Optional[RelationshipProfile] = None
            created_target_profile = False
            if pack.relationship is not None:
                existing_profile = current_target_profile
                if existing_profile is not None:
                    if requires_exact_relationship_restore:
                        self._ensure_bound_relationship_matches(
                            existing_profile,
                            pack.relationship,
                        )
                    else:
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
                        target_profile = transactional_storage.create_relationship(
                            pack.relationship
                        )
                        created_target_profile = True
                    except (RuntimeError, ValueError) as exc:
                        raced_profile = transactional_storage.get_relationship(
                            clean_agent,
                            clean_user,
                        )
                        if raced_profile is not None:
                            target_profile = raced_profile
                        elif requires_exact_relationship_restore:
                            raise ValueError(
                                "MemoryPack exact relationship identity conflicts "
                                "with the target storage"
                            ) from exc
                        else:
                            target_profile = self._initialize_relationship_on_storage(
                                transactional_storage,
                                clean_agent,
                                clean_user,
                                pack.relationship.blueprint.source_text,
                                pack.relationship.blueprint.compiled,
                                premise=pack.relationship.premise,
                                source_format=pack.relationship.blueprint.source_format,
                                source_name=pack.relationship.blueprint.source_name,
                            )
                            created_target_profile = True
                else:
                    target_profile = self._initialize_relationship_on_storage(
                        transactional_storage,
                        clean_agent,
                        clean_user,
                        pack.relationship.blueprint.source_text,
                        pack.relationship.blueprint.compiled,
                        premise=pack.relationship.premise,
                        source_format=pack.relationship.blueprint.source_format,
                        source_name=pack.relationship.blueprint.source_name,
                    )
                    created_target_profile = True

                if (
                    held_relationship_id is not None
                    and target_profile.relationship_id
                    != held_relationship_id
                    and not created_target_profile
                ):
                    raise _RelationshipImportGuardChanged
                if (
                    requires_exact_relationship_restore
                    and target_profile.relationship_id
                    != pack.relationship.relationship_id
                ):
                    raise ValueError(
                        "MemoryPack bound provenance requires exact relationship "
                        "restore"
                    )
                if requires_exact_relationship_restore:
                    self._ensure_bound_relationship_matches(
                        target_profile,
                        pack.relationship,
                    )

                if not persona_growth_preflighted:
                    growth_proposals = plan_memory_pack_persona_growth_writes(
                        pack,
                        target_profile.relationship_id,
                    )
                    self._validate_persona_growth_import_conflicts(
                        growth_proposals,
                        target_profile.relationship_id,
                        storage=transactional_storage,
                    )
                try:
                    write_plan = plan_memory_pack_writes(
                        pack,
                        clean_agent,
                        clean_user,
                        target_profile,
                        overwrite=overwrite,
                    )
                except ValueError:
                    if (
                        has_persona_compilation_payload
                        and (
                            clean_agent != pack.relationship.agent_id
                            or clean_user != pack.relationship.user_id
                        )
                    ):
                        preserve_remapped_persona_rejection = True
                    raise
                assert write_plan.relationship is not None
                if write_plan.persona_compilation is not None:
                    try:
                        target_profile = execute_memory_pack_persona_compilation(
                            transactional_storage,
                            write_plan.persona_compilation,
                            target_profile,
                        )
                    except ValueError:
                        if (
                            has_persona_compilation_payload
                            and (
                                clean_agent != pack.relationship.agent_id
                                or clean_user != pack.relationship.user_id
                            )
                        ):
                            preserve_remapped_persona_rejection = True
                        raise
            else:
                write_plan = plan_memory_pack_writes(
                    pack,
                    clean_agent,
                    clean_user,
                    target_profile,
                    overwrite=overwrite,
                )

            execute_memory_pack_writes(transactional_storage, write_plan)
            return pack

        capability_provider = getattr(
            self.storage,
            "atomic_memory_pack_write_store_v1",
            None,
        )
        capability = (
            capability_provider()
            if callable(capability_provider)
            else None
        )
        try:
            if receipt_capability is not None:
                return receipt_capability.execute_memory_pack_write_v2(
                    import_operation_id,
                    clean_agent,
                    clean_user,
                    receipt_relationship_id,
                    execute_import,
                    memory_pack_import_result_json,
                    lambda result_json: memory_pack_import_result_from_json(
                        result_json,
                        pack,
                    ),
                    lock_relationship_id=atomic_relationship_id,
                )
            if capability is None:
                return execute_import(self.storage)
            return capability.execute_memory_pack_write(
                clean_agent,
                clean_user,
                atomic_relationship_id,
                execute_import,
            )
        except ValueError:
            # Preserve the historical remapping contract for a rejected Persona
            # Compilation payload: the empty target relationship is useful for
            # a later explicit retry, while compilation and memory payloads are
            # still rolled back by the atomic capability.
            if (
                existing_target_profile is None
                and pack.relationship is not None
                and has_persona_compilation_payload
                and (clean_agent != pack.relationship.agent_id
                     or clean_user != pack.relationship.user_id)
                and preserve_remapped_persona_rejection
            ):
                self._initialize_relationship_on_storage(
                    self.storage,
                    clean_agent,
                    clean_user,
                    pack.relationship.blueprint.source_text,
                    pack.relationship.blueprint.compiled,
                    premise=pack.relationship.premise,
                    source_format=pack.relationship.blueprint.source_format,
                    source_name=pack.relationship.blueprint.source_name,
                )
            raise

    @staticmethod
    def _validate_turn_pack(
        pack: MemoryPack,
        target_agent: str,
        target_user: str,
    ) -> None:
        """Rejects transcript remapping before import performs any writes."""
        if not pack.turn_records:
            return
        if (
            pack.relationship is not None
            and (
                pack.agent_id != target_agent
                or pack.user_id != target_user
                or pack.relationship.agent_id != target_agent
                or pack.relationship.user_id != target_user
            )
        ):
            raise ValueError(
                "MemoryPack source transcripts cannot be copied to another Agent x User"
            )
        validate_memory_pack_turn_records(pack)

    def _validate_turn_import_conflicts(
        self,
        pack: MemoryPack,
        existing_profile: Optional[RelationshipProfile],
        *,
        storage: Optional[BaseStorage] = None,
    ) -> None:
        """Preflights turn identities before legacy memory fields are written."""
        if not pack.turn_records or existing_profile is None:
            return
        target_storage = storage or self.storage
        existing_by_id = {
            record.turn_id: record
            for record in target_storage.list_turn_records(
                existing_profile.relationship_id
            )
        }
        requires_exact_source_turn = bool(
            pack.relationship_processing_runs
            or pack.persona_reflection_decisions
        )
        for incoming in pack.turn_records:
            existing = existing_by_id.get(incoming.turn_id)
            if existing is None:
                continue
            if requires_exact_source_turn:
                if existing.to_dict() == incoming.to_dict():
                    continue
                raise TurnConflictError(
                    f"turn_id {incoming.turn_id!r} conflicts with exact "
                    "relationship-processing provenance"
                )
            if (
                incoming.status == TurnStatus.OPEN
                and existing.same_opening_as(incoming)
            ):
                continue
            if (
                incoming.status != TurnStatus.OPEN
                and existing.same_terminal_payload_as(incoming)
            ):
                continue
            raise TurnConflictError(
                f"turn_id {incoming.turn_id!r} already has different content"
            )

    def _validate_relationship_consequence_import_conflicts(
        self,
        pack: MemoryPack,
        existing_profile: Optional[RelationshipProfile],
        *,
        storage: Optional[BaseStorage] = None,
    ) -> None:
        """Preflights target consequence identities before any import writes."""
        if not (
            pack.relationship_consequences
            or pack.narrative_tension_links
        ) or existing_profile is None:
            return
        if (
            pack.relationship is None
            or pack.relationship.relationship_id
            != existing_profile.relationship_id
        ):
            raise ValueError(
                "MemoryPack relationship consequences require exact "
                "relationship restore"
            )
        relationship_id = existing_profile.relationship_id
        target_storage = storage or self.storage
        try:
            existing_consequences = (
                target_storage.list_relationship_consequences(relationship_id)
            )
            existing_links = target_storage.list_narrative_tension_links(
                relationship_id
            )
        except NotImplementedError as exc:
            raise ValueError(
                "target storage cannot preflight relationship consequences"
            ) from exc

        consequence_by_id: Dict[str, RelationshipConsequence] = {}
        consequence_by_source: Dict[
            Tuple[str, str], RelationshipConsequence
        ] = {}
        consequence_by_tension: Dict[str, RelationshipConsequence] = {}
        for consequence in [
            *existing_consequences,
            *pack.relationship_consequences,
        ]:
            identities = (
                (consequence_by_id, consequence.consequence_id),
                (
                    consequence_by_source,
                    (
                        consequence.source_decision_id,
                        consequence.source_event_id,
                    ),
                ),
                (consequence_by_tension, consequence.tension_id),
            )
            for registry, identity in identities:
                existing = registry.get(identity)
                if existing is not None and not existing.same_payload_as(
                    consequence
                ):
                    raise ValueError(
                        "MemoryPack relationship consequence conflicts with "
                        "the target journal"
                    )
                registry[identity] = consequence

        link_by_id: Dict[str, NarrativeTensionLink] = {}
        link_by_source: Dict[Tuple[str, str], NarrativeTensionLink] = {}
        for link in [*existing_links, *pack.narrative_tension_links]:
            identities = (
                (link_by_id, link.link_id),
                (link_by_source, (link.tension_id, link.source_event_id)),
            )
            for registry, identity in identities:
                existing = registry.get(identity)
                if existing is not None and not existing.same_payload_as(link):
                    raise ValueError(
                        "MemoryPack Narrative Tension link conflicts with "
                        "the target journal"
                    )
                registry[identity] = link

        NarrativeTensionProjector.project(
            (*existing_consequences, *pack.relationship_consequences),
            (*existing_links, *pack.narrative_tension_links),
        )

    def _validate_timeline_import_conflicts(
        self,
        pack: MemoryPack,
        target_agent: str,
        target_user: str,
        existing_profile: Optional[RelationshipProfile],
        *,
        storage: Optional[BaseStorage] = None,
    ) -> None:
        """Preflights stable Timeline identities and relationship provenance."""
        if not pack.timeline_entries:
            return
        expected_relationship_id = (
            pack.relationship.relationship_id
            if pack.relationship is not None
            else "legacy_unavailable"
        )
        if (
            existing_profile is not None
            and pack.relationship is not None
            and existing_profile.relationship_id
            != expected_relationship_id
        ):
            raise ValueError(
                "MemoryPack Timeline provenance requires exact relationship "
                "restore"
            )

        incoming_by_id = {}
        for entry in pack.timeline_entries:
            if (
                entry.agent_id != target_agent
                or entry.user_id != target_user
            ):
                raise ValueError(
                    "MemoryPack Timeline entry crosses Agent x User boundaries"
                )
            if entry.relationship_id != expected_relationship_id:
                raise ValueError(
                    "MemoryPack Timeline entry crosses relationship boundaries"
                )
            existing_in_pack = incoming_by_id.get(entry.timeline_entry_id)
            if (
                existing_in_pack is not None
                and existing_in_pack.to_dict() != entry.to_dict()
            ):
                raise ValueError(
                    "MemoryPack contains conflicting Timeline entry identities"
                )
            incoming_by_id[entry.timeline_entry_id] = entry

        target_storage = storage or self.storage
        try:
            target_entries = target_storage.list_timeline_entries(
                target_agent,
                target_user,
            )
        except NotImplementedError as exc:
            raise ValueError(
                "target storage cannot preflight structured Timeline entries"
            ) from exc
        target_by_id = {
            entry.timeline_entry_id: entry for entry in target_entries
        }
        for entry_id, incoming in incoming_by_id.items():
            existing = target_by_id.get(entry_id)
            if (
                existing is not None
                and existing.to_dict() != incoming.to_dict()
            ):
                raise ValueError(
                    "MemoryPack Timeline entry conflicts with target history"
                )

    def _validate_persona_growth_import_conflicts(
        self,
        incoming_proposals: Sequence[PersonaGrowthProposal],
        target_relationship_id: str,
        *,
        storage: Optional[BaseStorage] = None,
    ) -> None:
        """Preflights planned proposal identities before payload writes."""
        if not incoming_proposals:
            return

        def immutable_content(
            proposal: PersonaGrowthProposal,
        ) -> Dict[str, Any]:
            data = proposal.to_dict()
            for key in (
                "status",
                "created_at",
                "decided_by",
                "decided_at",
                "decision_reason",
            ):
                data.pop(key, None)
            return data

        def lifecycle(proposal: PersonaGrowthProposal):
            return (
                proposal.status,
                proposal.decided_by,
                proposal.decided_at,
                proposal.decision_reason,
            )

        target_storage = storage or self.storage
        for incoming in incoming_proposals:
            try:
                existing = target_storage.get_persona_growth_proposal(
                    incoming.proposal_id
                )
            except NotImplementedError:
                existing = next(
                    (
                        proposal
                        for proposal in (
                            target_storage.list_persona_growth_proposals(
                                target_relationship_id
                            )
                        )
                        if proposal.proposal_id == incoming.proposal_id
                    ),
                    None,
                )
            if existing is None:
                continue
            if (
                immutable_content(existing) != immutable_content(incoming)
                or lifecycle(existing) != lifecycle(incoming)
            ):
                raise ValueError(
                    "MemoryPack persona growth conflicts with the target "
                    "proposal history"
                )

    def _validate_relationship_adjudication_import_conflicts(
        self,
        pack: MemoryPack,
        existing_profile: Optional[RelationshipProfile],
        *,
        storage: Optional[BaseStorage] = None,
    ) -> None:
        """Rejects exact-identity adjudication conflicts before import writes."""
        if (
            pack.relationship is None
            or existing_profile is None
            or not pack.relationship_adjudications
            or existing_profile.relationship_id
            != pack.relationship.relationship_id
        ):
            return
        target_storage = storage or self.storage
        try:
            existing_records = target_storage.list_relationship_adjudications(
                existing_profile.relationship_id
            )
        except NotImplementedError:
            return
        existing_by_id = {
            record.receipt.decision_id: record
            for record in existing_records
        }
        for incoming in pack.relationship_adjudications:
            existing = existing_by_id.get(incoming.receipt.decision_id)
            if (
                existing is not None
                and existing.to_dict() != incoming.to_dict()
            ):
                raise ValueError(
                    "MemoryPack relationship adjudication conflicts with "
                    "the target decision journal"
                )

    @staticmethod
    def _validate_relationship_processing_pack(
        pack: MemoryPack,
        target_agent: str,
        target_user: str,
        existing_profile: Optional[RelationshipProfile],
        *,
        storage: Optional[BaseStorage] = None,
    ) -> None:
        """Preflights a7 ledgers before any legacy memory field is written."""
        runs = pack.relationship_processing_runs
        decisions = pack.persona_reflection_decisions
        structure = analyze_memory_pack_relationship_processing(
            pack,
            target_agent,
            target_user,
            (
                existing_profile.relationship_id
                if existing_profile is not None
                else None
            ),
        )
        if structure is None:
            return
        assert pack.relationship is not None
        relationship_id = pack.relationship.relationship_id
        events_by_id = structure.events_by_id
        adjudications_by_event = structure.adjudications_by_event
        adjudications_for_reflection_by_event = adjudications_by_event
        direct_event_order = structure.direct_event_order
        direct_events_by_id = structure.direct_events_by_id
        if existing_profile is not None:
            if storage is None:
                raise ValueError(
                    "target storage is required to validate existing "
                    "relationship-processing history"
                )
            try:
                existing_direct_events = (
                    storage.list_relationship_events(relationship_id)
                )
                existing_adjudications = (
                    storage.list_relationship_adjudications(
                        relationship_id
                    )
                )
                existing_runs = (
                    storage.list_relationship_processing_runs(
                        relationship_id
                    )
                )
                existing_reflection_decisions = (
                    storage.list_persona_reflection_decisions(
                        relationship_id
                    )
                )
                existing_reflections = (
                    storage.list_persona_reflection_records(
                        relationship_id
                    )
                )
                existing_growth_proposals = (
                    storage.list_persona_growth_proposals(
                        relationship_id
                    )
                )
            except NotImplementedError as exc:
                raise ValueError(
                    "target storage cannot validate relationship-processing "
                    "journal prefixes"
                ) from exc
            for existing_event in [
                *existing_direct_events,
                *(
                    event
                    for record in existing_adjudications
                    for event in record.events
                ),
            ]:
                incoming_event = events_by_id.get(existing_event.event_id)
                if (
                    incoming_event is not None
                    and not existing_event.same_payload_as(incoming_event)
                ):
                    raise ValueError(
                        "MemoryPack relationship event conflicts with "
                        "the target relationship history"
                    )
            incoming_direct_events = [
                direct_events_by_id[event_id]
                for event_id in direct_event_order
            ]
            shared_direct_count = min(
                len(existing_direct_events),
                len(incoming_direct_events),
            )
            if any(
                existing_direct_events[index].to_dict()
                != incoming_direct_events[index].to_dict()
                for index in range(shared_direct_count)
            ):
                raise ValueError(
                    "MemoryPack direct-event journal is not prefix-compatible "
                    "with the target relationship"
                )
            shared_adjudication_count = min(
                len(existing_adjudications),
                len(pack.relationship_adjudications),
            )
            if any(
                existing_adjudications[index].to_dict()
                != pack.relationship_adjudications[index].to_dict()
                for index in range(shared_adjudication_count)
            ):
                raise ValueError(
                    "MemoryPack adjudication journal is not prefix-compatible "
                    "with the target relationship"
                )
            merged_direct_events = (
                existing_direct_events
                if len(existing_direct_events) >= len(incoming_direct_events)
                else incoming_direct_events
            )
            merged_adjudications = (
                existing_adjudications
                if len(existing_adjudications)
                >= len(pack.relationship_adjudications)
                else pack.relationship_adjudications
            )
            try:
                TemporalHistoryValidator.validate_complete_history(
                    relationship_events_from_journals(
                        merged_direct_events,
                        merged_adjudications,
                    )
                )
            except ValueError as exc:
                raise ValueError(
                    "MemoryPack relationship history conflicts with the "
                    "target temporal lifecycle"
                ) from exc
            adjudications_for_reflection_by_event = {}
            for record in merged_adjudications:
                for event in record.events:
                    adjudications_for_reflection_by_event.setdefault(
                        event.event_id,
                        [],
                    ).append(record)
            existing_runs_by_id = {
                run.processing_id: run
                for run in existing_runs
            }
            existing_runs_by_identity = {
                (
                    run.source_turn_id,
                    run.source_revision,
                    run.processing_identity,
                ): run
                for run in existing_runs
            }
            for incoming_run in runs:
                existing_run = existing_runs_by_id.get(
                    incoming_run.processing_id
                )
                if existing_run is None:
                    existing_run = existing_runs_by_identity.get(
                        (
                            incoming_run.source_turn_id,
                            incoming_run.source_revision,
                            incoming_run.processing_identity,
                        )
                    )
                if (
                    existing_run is not None
                    and not existing_run.same_frozen_input_as(incoming_run)
                ):
                    raise ValueError(
                        "MemoryPack relationship processing conflicts with "
                        "the target frozen run"
                    )
            existing_reflection_decisions_by_id = {
                decision.decision_id: decision
                for decision in existing_reflection_decisions
            }
            existing_reflection_decisions_by_identity = {
                decision.interpretation_identity: decision
                for decision in existing_reflection_decisions
            }
            existing_reflections_by_id = {
                reflection.reflection_id: reflection
                for reflection in existing_reflections
            }
            for incoming_decision in decisions:
                existing_decision = (
                    existing_reflection_decisions_by_id.get(
                        incoming_decision.decision_id
                    )
                    or existing_reflection_decisions_by_identity.get(
                        incoming_decision.interpretation_identity
                    )
                )
                if (
                    existing_decision is not None
                    and not existing_decision.same_payload_as(
                        incoming_decision
                    )
                ):
                    raise ValueError(
                        "MemoryPack persona reflection conflicts with "
                        "the target decision history"
                    )
                incoming_reflection = incoming_decision.reflection_record
                if incoming_reflection is None:
                    continue
                existing_reflection = existing_reflections_by_id.get(
                    incoming_reflection.reflection_id
                )
                if (
                    existing_reflection is not None
                    and not existing_reflection.same_payload_as(
                        incoming_reflection
                    )
                ):
                    raise ValueError(
                        "MemoryPack persona reflection conflicts with "
                        "the target reflection history"
                    )
            existing_growth_by_id = {
                proposal.proposal_id: proposal
                for proposal in existing_growth_proposals
            }
            for incoming_proposal in pack.persona_growth_proposals:
                existing_proposal = existing_growth_by_id.get(
                    incoming_proposal.proposal_id
                )
                if existing_proposal is None:
                    continue
                existing_data = existing_proposal.to_dict()
                incoming_data = incoming_proposal.to_dict()
                existing_data.pop("created_at", None)
                incoming_data.pop("created_at", None)
                if existing_data != incoming_data:
                    raise ValueError(
                        "MemoryPack persona growth conflicts with "
                        "the target proposal history"
                    )

        reflection_context = analyze_relationship_processing_reflection_context(
            pack,
            structure,
            adjudications_for_reflection_by_event,
        )
        run_analysis = validate_relationship_processing_runs(pack, structure)
        validate_relationship_processing_reflections(
            pack,
            structure,
            run_analysis,
            reflection_context,
        )

        if storage is None:
            return
        try:
            existing_runs = storage.list_relationship_processing_runs(
                relationship_id
            )
            existing_decisions = storage.list_persona_reflection_decisions(
                relationship_id
            )
        except NotImplementedError as exc:
            raise ValueError(
                "storage adapter cannot import a7 relationship processing ledgers"
            ) from exc

        existing_runs_by_id = {
            item.processing_id: item for item in existing_runs
        }
        existing_runs_by_identity = {
            (
                item.source_turn_id,
                item.source_revision,
                item.processing_identity,
            ): item
            for item in existing_runs
        }
        for incoming in runs:
            existing = existing_runs_by_id.get(incoming.processing_id)
            if existing is None:
                existing = existing_runs_by_identity.get(
                    (
                        incoming.source_turn_id,
                        incoming.source_revision,
                        incoming.processing_identity,
                    )
                )
            if existing is not None and existing != incoming:
                raise ValueError(
                    "relationship processing identity already has different state"
                )

        existing_decisions_by_id = {
            item.decision_id: item for item in existing_decisions
        }
        existing_decisions_by_identity = {
            item.interpretation_identity: item for item in existing_decisions
        }
        for incoming in decisions:
            existing = existing_decisions_by_id.get(incoming.decision_id)
            if existing is None:
                existing = existing_decisions_by_identity.get(
                    incoming.interpretation_identity
                )
            if (
                existing is not None
                and not existing.same_payload_as(incoming)
            ):
                raise ValueError(
                    "persona reflection identity already has different content"
                )


    def start(self) -> "ERIIEngine":
        """Starts only the legacy ``remember()`` worker and returns this engine."""
        self.archiver_worker.start()
        return self

    def process_pending(self, max_tasks: Optional[int] = None) -> int:
        """Synchronously processes reliable and legacy archival work."""
        if max_tasks is not None and max_tasks < 0:
            raise ValueError("max_tasks cannot be negative")
        processed = 0
        if self.archival_coordinator.available:
            processed += self.archival_coordinator.process_pending(
                max_tasks=max_tasks,
            )
        while max_tasks is None or processed < max_tasks:
            if not self.archiver_worker.process_next():
                break
            processed += 1
        return processed

    def close(self, timeout: float = 1.0) -> ShutdownReport:
        """Stops acceptance and cooperatively shuts down explicit workers."""
        report = self.archival_coordinator.close(timeout=timeout)
        with self._interaction_context_cache_lock:
            self._interaction_context_cache.clear()
        if hasattr(self, "archiver_worker"):
            self.archiver_worker.shutdown()
        legacy_stopped = (
            self.archiver_worker.worker_thread is None
            or not self.archiver_worker.worker_thread.is_alive()
        )
        return ShutdownReport(
            worker_stopped=report.worker_stopped and legacy_stopped,
            unfinished_archival_ids=report.unfinished_archival_ids,
        )

    def shutdown(self, timeout: float = 1.0) -> ShutdownReport:
        """Alias for close()."""
        return self.close(timeout=timeout)

    def __enter__(self) -> "ERIIEngine":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit, ensures close() is called."""
        self.close()
