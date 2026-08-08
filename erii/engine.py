"""E.R.I.I. Unified Orchestration Engine (ERIIEngine).

Main entry point for AI Agent long-term memory integration.
Follows Google Python Style Guide.
"""

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import replace
import hashlib
import json
import os
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union
import uuid
import warnings

from erii.adapters.base import BaseLLMAdapter
from erii.adapters.custom_adapter import CallableLLMAdapter
from erii.core.archiver import AsyncArchiverWorker
from erii.core.archival import ArchivalCoordinator
from erii.core.budget import MemoryBudgetManager
from erii.core.decay import MemoryDecayEvaluator
from erii.core.retriever import MemoryRetriever
from erii.core.adjudication import (
    PERSISTED_TURN_CONTRACT_VERSION,
    RULE_VERSION as RELATIONSHIP_ADJUDICATION_RULE_VERSION,
    RelationshipAdjudicator,
    list_complete_relationship_events,
    relationship_adjudication_baseline_fingerprint,
    relationship_events_from_journals,
    relationship_occurrence_fingerprint,
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
from erii.core.memory_pack_import_compatibility import (
    has_legacy_persona_decision_reason_loss,
)
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
    DecisionOutcome,
    PersonaGrowthDecision,
    PersonaGrowthIntentCandidate,
    PersonaGrowthProposal,
    PersonaGrowthStatus,
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
    ReflectionProvenanceState,
    RelationshipConsolidation,
    RelationshipEventCandidatesDecision,
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
            timeline_entries=timeline_entries,
            archival_ledger=archival_ledger,
            relationship=relationship,
            relationship_events=relationship_events,
            relationship_direct_event_ids=relationship_direct_event_ids,
            relationship_adjudications=relationship_adjudications,
            relationship_consequences=relationship_consequences,
            narrative_tension_links=narrative_tension_links,
            persona_growth_proposals=persona_growth_proposals,
            persona_compilation_proposals=persona_compilation_proposals,
            persona_manifests=persona_manifests,
            turn_records=turn_records,
            relationship_processing_runs=relationship_processing_runs,
            persona_reflection_decisions=persona_reflection_decisions,
        )

        validate_memory_pack_archival_evidence(pack)
        self._validate_persisted_turn_adjudication_pack(
            pack,
            clean_agent,
            clean_user,
            relationship,
        )
        self._validate_relationship_consequence_pack(pack)

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

        self._validate_memory_pack_node_types(pack)

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

        provisional_guard_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"erii:relationship-import:{clean_agent}:{clean_user}",
            )
        )
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

        self._validate_memory_pack_node_types(pack)
        self._validate_temporal_pack(pack)
        self._validate_persona_growth_pack(pack)

        target_agent = agent_id or pack.agent_id
        target_user = user_id or pack.user_id

        clean_agent = SecuritySanitizer.validate_key(target_agent, "agent_id")
        clean_user = SecuritySanitizer.validate_key(target_user, "user_id")
        has_bound_archival_history = bool(
            pack.timeline_entries
            or pack.archival_ledger
            or pack.turn_records
            or pack.relationship_processing_runs
            or pack.persona_reflection_decisions
            or pack.relationship_consequences
            or pack.narrative_tension_links
            or any(
                node.source_turn_id is not None
                or node.source_archival_id is not None
                for node in pack.nodes
            )
        )
        requires_exact_relationship_restore = bool(
            has_bound_archival_history and pack.relationship is not None
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
        self._validate_relationship_consequence_pack(pack)
        existing_target_profile = self.storage.get_relationship(
            clean_agent,
            clean_user,
        )
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
        )
        self._validate_relationship_adjudication_import_conflicts(
            pack,
            existing_target_profile,
        )
        self._validate_relationship_consequence_import_conflicts(
            pack,
            existing_target_profile,
        )
        self._validate_persisted_turn_adjudication_pack(
            pack,
            clean_agent,
            clean_user,
            existing_target_profile,
        )
        self._validate_relationship_processing_pack(
            pack,
            clean_agent,
            clean_user,
            existing_target_profile,
            storage=self.storage,
            relationship_adjudicator=self.relationship_adjudicator,
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
            self._import_persona_compilation(
                pack,
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
            self.storage.validate_archival_tombstones(
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
            )

        target_profile: Optional[RelationshipProfile] = None
        if pack.relationship is not None:
            existing_profile = self.storage.get_relationship(
                clean_agent,
                clean_user,
            )
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
                    target_profile = self.storage.create_relationship(
                        pack.relationship
                    )
                except (RuntimeError, ValueError) as exc:
                    raced_profile = self.storage.get_relationship(
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
                        target_profile = self.initialize_relationship(
                            clean_agent,
                            clean_user,
                            pack.relationship.blueprint.source_text,
                            pack.relationship.blueprint.compiled,
                            relationship_premise=pack.relationship.premise,
                            source_format=(
                                pack.relationship.blueprint.source_format
                            ),
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

            if (
                held_relationship_id is not None
                and target_profile.relationship_id
                != held_relationship_id
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

            self._validate_persona_growth_import_conflicts(
                pack,
                target_profile,
            )
            if has_persona_compilation_payload:
                target_profile = self._import_persona_compilation(
                    pack,
                    target_profile,
                )

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

        if not pack.timeline_entries:
            for entry in pack.timeline:
                self.storage.add_timeline_entry(
                    clean_agent,
                    clean_user,
                    entry.get("content", ""),
                    entry.get("timestamp"),
                )

        if pack.relationship is not None:
            assert target_profile is not None
            source_relationship_id = pack.relationship.relationship_id
            target_relationship_id = target_profile.relationship_id
            if pack.turn_records:
                if source_relationship_id != target_relationship_id:
                    raise ValueError(
                        "MemoryPack source transcripts require exact relationship restore"
                    )
                for turn_record in pack.turn_records:
                    self.storage.create_turn_record(turn_record)

            if pack.timeline_entries:
                self.storage.import_timeline_entries(
                    clean_agent,
                    clean_user,
                    pack.timeline_entries,
                )
            if pack.archival_ledger:
                self.storage.import_archival_tombstones(
                    target_relationship_id,
                    pack.archival_ledger,
                )

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
                temporal_payload = self._remap_temporal_payload(
                    source_event.temporal_payload,
                    event_id_map,
                )
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
                            temporal_payload=(
                                temporal_payload.to_dict()
                                if temporal_payload is not None
                                else None
                            ),
                        )
                    )
                return replace(
                    source_event,
                    event_id=event_id_map[source_event.event_id],
                    relationship_id=target_relationship_id,
                    metadata=metadata,
                    temporal_payload=temporal_payload,
                )

            if source_relationship_id != target_relationship_id:
                remapped_history = []
                seen_remapped_source_ids = set()
                for source_event in [
                    *pack.relationship_events,
                    *(
                        event
                        for record in pack.relationship_adjudications
                        for event in record.events
                    ),
                ]:
                    if source_event.event_id in seen_remapped_source_ids:
                        continue
                    seen_remapped_source_ids.add(source_event.event_id)
                    remapped_history.append(remap_event(source_event))
                TemporalHistoryValidator.validate_complete_history(remapped_history)

            top_level_source_by_id = {
                event.event_id: event
                for event in pack.relationship_events
            }
            if pack.relationship_direct_event_ids:
                ordered_ids = tuple(pack.relationship_direct_event_ids)
                if (
                    len(ordered_ids) != len(set(ordered_ids))
                    or any(
                        event_id not in top_level_source_by_id
                        for event_id in ordered_ids
                    )
                ):
                    raise ValueError(
                        "MemoryPack direct-event journal order does not match "
                        "its direct events"
                    )
                direct_source_events = [
                    top_level_source_by_id[event_id]
                    for event_id in ordered_ids
                ]
            else:
                adjudicated_event_ids = {
                    event.event_id
                    for record in pack.relationship_adjudications
                    for event in record.events
                }
                direct_source_events = [
                    source_event
                    for source_event in pack.relationship_events
                    if source_event.event_id not in adjudicated_event_ids
                ]
            imported_direct_events = [
                remap_event(source_event)
                for source_event in direct_source_events
            ]
            imported_records: List[AdjudicationRecord] = []
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
                imported_records.append(imported_record)

            self._commit_relationship_import_history(
                target_relationship_id,
                imported_direct_events,
                imported_records,
            )

            if (
                pack.relationship_consequences
                or pack.narrative_tension_links
            ) and source_relationship_id != target_relationship_id:
                raise ValueError(
                    "MemoryPack relationship consequences require exact "
                    "relationship restore"
                )
            for consequence in pack.relationship_consequences:
                stored_consequence = (
                    self.storage.append_relationship_consequence(consequence)
                )
                if not stored_consequence.same_payload_as(consequence):
                    raise ValueError(
                        "persisted relationship consequence differs from "
                        "the imported journal entry"
                    )
            for link in pack.narrative_tension_links:
                stored_link = self.storage.append_narrative_tension_link(link)
                if not stored_link.same_payload_as(link):
                    raise ValueError(
                        "persisted Narrative Tension link differs from "
                        "the imported journal entry"
                    )

            for source_proposal in pack.persona_growth_proposals:
                imported_proposal = (
                    self._remap_persona_growth_proposal_for_import(
                        pack,
                        source_proposal,
                        target_relationship_id,
                    )
                )
                self.storage.save_persona_growth_proposal(imported_proposal)

            if (
                pack.relationship_processing_runs
                or pack.persona_reflection_decisions
            ) and source_relationship_id != target_relationship_id:
                raise ValueError(
                    "MemoryPack relationship processing requires exact "
                    "relationship restore"
                )
            for reflection_decision in pack.persona_reflection_decisions:
                self.storage.commit_persona_reflection_decision(
                    reflection_decision
                )
            for processing_run in pack.relationship_processing_runs:
                self.storage.create_relationship_processing_run(processing_run)

        elif pack.timeline_entries:
            self.storage.import_timeline_entries(
                clean_agent,
                clean_user,
                pack.timeline_entries,
            )

        return pack

    def _commit_relationship_import_history(
        self,
        relationship_id: str,
        direct_events: Sequence[RelationshipEvent],
        adjudications: Sequence[AdjudicationRecord],
    ) -> None:
        """Imports both journals in a stable order that satisfies temporal references.

        Direct events and adjudicated events are stored in separate append-only
        journals. A trusted resolution may therefore depend on an adjudicated
        Promise (or the reverse). Importing one whole journal before the other
        would reject a valid pack even though its causal target is present.
        """
        try:
            existing = list_complete_relationship_events(self.storage, relationship_id)
        except NotImplementedError:
            existing = []
        available_ids = {event.event_id for event in existing}
        direct_queue = list(direct_events)
        adjudication_queue = list(adjudications)
        imported_events = [
            event
            for unit in [*direct_queue, *adjudication_queue]
            for event in (
                (unit,)
                if isinstance(unit, RelationshipEvent)
                else unit.events
            )
        ]
        prerequisites = TemporalHistoryValidator.causal_prerequisites(imported_events)
        direct_index = 0
        adjudication_index = 0

        while (
            direct_index < len(direct_queue)
            or adjudication_index < len(adjudication_queue)
        ):
            journal_heads = []
            if direct_index < len(direct_queue):
                journal_heads.append(("event", direct_queue[direct_index]))
            if adjudication_index < len(adjudication_queue):
                journal_heads.append(
                    (
                        "adjudication",
                        adjudication_queue[adjudication_index],
                    )
                )
            for unit_kind, unit in journal_heads:
                unit_events = (
                    (unit,)
                    if isinstance(unit, RelationshipEvent)
                    else unit.events
                )
                causal_ids = set(available_ids)
                ready = True
                for event in unit_events:
                    references = prerequisites[event.event_id]
                    if not references.issubset(causal_ids):
                        ready = False
                        break
                    causal_ids.add(event.event_id)
                if not ready:
                    continue

                if unit_kind == "event":
                    stored_event = self.storage.append_relationship_event(unit)
                    if not stored_event.same_payload_as(unit):
                        raise ValueError(
                            "persisted relationship event differs from "
                            "the imported journal entry"
                        )
                    direct_index += 1
                else:
                    stored_record = (
                        self.storage.commit_relationship_adjudication(unit)
                    )
                    if stored_record.to_dict() != unit.to_dict():
                        raise ValueError(
                            "persisted relationship adjudication differs from "
                            "the imported journal entry"
                        )
                    adjudication_index += 1
                available_ids.update(event.event_id for event in unit_events)
                break
            else:
                remaining_units = [
                    *direct_queue[direct_index:],
                    *adjudication_queue[adjudication_index:],
                ]
                unresolved = sorted(
                    {
                        reference
                        for unit in remaining_units
                        for event in (
                            (unit,)
                            if isinstance(unit, RelationshipEvent)
                            else unit.events
                        )
                        for reference in prerequisites[event.event_id]
                        if reference not in available_ids
                    }
                )
                raise ValueError(
                    "MemoryPack relationship history has unresolved causal ordering"
                    + (f": {', '.join(unresolved)}" if unresolved else "")
                )

    @staticmethod
    def _temporal_reference_ids(event: RelationshipEvent) -> Sequence[str]:
        payload = event.temporal_payload
        if isinstance(payload, PromiseConditionConfirmation):
            return (payload.promise_event_id,)
        if isinstance(payload, PromiseResolution):
            references = [payload.promise_event_id]
            if payload.superseding_promise_event_id is not None:
                references.append(payload.superseding_promise_event_id)
            return tuple(references)
        if isinstance(payload, OpenLoopResolution):
            references = [payload.open_loop_event_id]
            if payload.superseding_open_loop_event_id is not None:
                references.append(payload.superseding_open_loop_event_id)
            return tuple(references)
        return ()

    @staticmethod
    def _validate_memory_pack_node_types(pack: MemoryPack) -> None:
        """Rejects non-persistable command directives before import writes."""
        if any(node.node_type == MemoryType.INSTRUCTION for node in pack.nodes):
            raise ValueError(
                "MemoryPack instruction nodes cannot be imported into long-term memory"
            )

    @classmethod
    def _validate_temporal_pack(cls, pack: MemoryPack) -> None:
        """Rejects incomplete or cross-relationship temporal graphs before import writes."""
        ordered_events = []
        by_id: Dict[str, RelationshipEvent] = {}
        for event in [
            *pack.relationship_events,
            *(
                accepted
                for record in pack.relationship_adjudications
                for accepted in record.events
            ),
        ]:
            existing = by_id.get(event.event_id)
            if existing is not None:
                if not existing.same_payload_as(event):
                    raise ValueError(
                        f"MemoryPack event_id {event.event_id!r} has conflicting payloads"
                    )
                continue
            by_id[event.event_id] = event
            ordered_events.append(event)

        temporal_events = [
            event for event in ordered_events if event.temporal_payload is not None
        ]
        if not temporal_events:
            return
        if pack.relationship is None:
            raise ValueError("MemoryPack temporal history requires a relationship profile")
        relationship_id = pack.relationship.relationship_id
        if any(event.relationship_id != relationship_id for event in ordered_events):
            raise ValueError("MemoryPack relationship history crosses relationship boundaries")
        all_ids = set(by_id)
        memory_node_ids = {node.node_id for node in pack.nodes}
        for event in temporal_events:
            missing = set(cls._temporal_reference_ids(event)).difference(all_ids)
            if missing:
                raise ValueError(
                    "MemoryPack temporal event references missing source events: "
                    + ", ".join(sorted(missing))
                )
            payload = event.temporal_payload
            if (
                isinstance(payload, OpenLoopSpec)
                and payload.origin_memory_node_id is not None
                and payload.origin_memory_node_id not in memory_node_ids
            ):
                # Existence is the portability invariant. The legacy node may
                # legitimately have been resolved or superseded after the formal
                # Open Loop captured its historical provenance.
                raise ValueError(
                    "MemoryPack Open Loop references a missing origin memory node: "
                    + payload.origin_memory_node_id
                )
        TemporalHistoryValidator.validate_complete_history(ordered_events)

    @staticmethod
    def _validate_turn_pack(
        pack: MemoryPack,
        target_agent: str,
        target_user: str,
    ) -> None:
        """Rejects transcript remapping before import performs any writes."""
        if not pack.turn_records:
            return
        if pack.relationship is None:
            raise ValueError("MemoryPack turn records require a relationship profile")
        if (
            pack.agent_id != target_agent
            or pack.user_id != target_user
            or pack.relationship.agent_id != target_agent
            or pack.relationship.user_id != target_user
        ):
            raise ValueError(
                "MemoryPack source transcripts cannot be copied to another Agent x User"
            )
        seen_turn_ids = set()
        for record in pack.turn_records:
            if record.relationship_id != pack.relationship.relationship_id:
                raise ValueError(
                    "MemoryPack turn record belongs to a different relationship"
                )
            if record.turn_id in seen_turn_ids:
                raise ValueError(
                    f"MemoryPack contains duplicate turn_id {record.turn_id!r}"
                )
            seen_turn_ids.add(record.turn_id)

    def _validate_turn_import_conflicts(
        self,
        pack: MemoryPack,
        existing_profile: Optional[RelationshipProfile],
    ) -> None:
        """Preflights turn identities before legacy memory fields are written."""
        if not pack.turn_records or existing_profile is None:
            return
        existing_by_id = {
            record.turn_id: record
            for record in self.storage.list_turn_records(
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

    @staticmethod
    def _validate_relationship_consequence_pack(pack: MemoryPack) -> None:
        """Preflights the complete consequence causal graph without writes."""
        if not (
            pack.relationship_consequences
            or pack.narrative_tension_links
        ):
            return
        if pack.relationship is None:
            raise ValueError(
                "MemoryPack relationship consequences require a relationship profile"
            )
        relationship_id = pack.relationship.relationship_id
        RelationshipConsequenceCoordinator.validate_journal(
            relationship_id,
            pack.relationship_consequences,
            pack.narrative_tension_links,
            pack.turn_records,
            pack.relationship_adjudications,
        )

        accepted_events: Dict[str, RelationshipEvent] = {}
        for record in pack.relationship_adjudications:
            for event in record.events:
                existing = accepted_events.get(event.event_id)
                if existing is not None and not existing.same_payload_as(event):
                    raise ValueError(
                        "MemoryPack consequence sources contain conflicting "
                        "accepted event identities"
                    )
                accepted_events[event.event_id] = event
        complete_events: Dict[str, RelationshipEvent] = {}
        for event in pack.relationship_events:
            existing = complete_events.get(event.event_id)
            if existing is not None and not existing.same_payload_as(event):
                raise ValueError(
                    "MemoryPack relationship history contains conflicting "
                    "event identities"
                )
            complete_events[event.event_id] = event
        source_event_ids = {
            item.source_event_id for item in pack.relationship_consequences
        } | {
            item.source_event_id for item in pack.narrative_tension_links
        }
        for event_id in source_event_ids:
            accepted = accepted_events.get(event_id)
            complete = complete_events.get(event_id)
            if accepted is None or complete is None:
                raise ValueError(
                    "MemoryPack relationship consequence source event is missing "
                    "from accepted complete history"
                )
            if not accepted.same_payload_as(complete):
                raise ValueError(
                    "MemoryPack relationship consequence source event conflicts "
                    "with complete history"
                )

    def _validate_relationship_consequence_import_conflicts(
        self,
        pack: MemoryPack,
        existing_profile: Optional[RelationshipProfile],
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
        try:
            existing_consequences = (
                self.storage.list_relationship_consequences(relationship_id)
            )
            existing_links = self.storage.list_narrative_tension_links(
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

        try:
            target_entries = self.storage.list_timeline_entries(
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

    @staticmethod
    def _validate_persona_growth_pack(pack: MemoryPack) -> None:
        """Validates portable growth history independently of a7 run ledgers."""
        if not pack.persona_growth_proposals:
            return
        if pack.relationship is None:
            raise ValueError(
                "MemoryPack Persona Growth requires a relationship profile"
            )
        relationship_id = pack.relationship.relationship_id
        event_ids = {
            event.event_id for event in pack.relationship_events
        } | {
            event.event_id
            for record in pack.relationship_adjudications
            for event in record.events
        }
        identities = set()
        proposal_ids = set()
        for proposal in pack.persona_growth_proposals:
            identity = (proposal.proposal_id, proposal.revision)
            if (
                identity in identities
                or proposal.proposal_id in proposal_ids
            ):
                raise ValueError(
                    "MemoryPack contains duplicate Persona Growth identities"
                )
            if proposal.relationship_id != relationship_id:
                raise ValueError(
                    "MemoryPack Persona Growth crosses relationship boundaries"
                )
            if not set(proposal.supporting_event_ids).issubset(event_ids):
                raise ValueError(
                    "MemoryPack Persona Growth references events outside the pack"
                )
            identities.add(identity)
            proposal_ids.add(proposal.proposal_id)

    @staticmethod
    def _remap_persona_growth_proposal_for_import(
        pack: MemoryPack,
        source_proposal: PersonaGrowthProposal,
        target_relationship_id: str,
    ) -> PersonaGrowthProposal:
        """Applies the same stable legacy-remap identities used by import."""
        if pack.relationship is None:
            raise ValueError(
                "MemoryPack Persona Growth requires a relationship profile"
            )
        source_relationship_id = pack.relationship.relationship_id
        if source_relationship_id == target_relationship_id:
            return source_proposal

        decision_id_map = {}
        for record in pack.relationship_adjudications:
            receipt = record.receipt
            processing_identity = (
                f"{receipt.processing_mode.value}:"
                f"{receipt.reprocessing_id or ''}"
            )
            decision_id_map[receipt.decision_id] = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"erii:{target_relationship_id}:decision:"
                        f"{receipt.source_turn_id}:{receipt.source_revision}:"
                        f"{processing_identity}:{receipt.candidate_key}"
                    ),
                )
            )
        source_event_ids = {
            event.event_id for event in pack.relationship_events
        } | {
            event.event_id
            for record in pack.relationship_adjudications
            for event in record.events
        }
        event_id_map = {
            event_id: str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"erii:{target_relationship_id}:{event_id}",
                )
            )
            for event_id in source_event_ids
        }
        for record in pack.relationship_adjudications:
            mapped_decision_id = decision_id_map[
                record.receipt.decision_id
            ]
            for index, event in enumerate(record.events):
                event_suffix = "event" if index == 0 else f"event:{index}"
                event_id_map[event.event_id] = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{mapped_decision_id}:{event_suffix}",
                    )
                )
        return replace(
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
                event_id_map[event_id]
                for event_id in source_proposal.supporting_event_ids
            ),
        )

    def _validate_persona_growth_import_conflicts(
        self,
        pack: MemoryPack,
        target_profile: RelationshipProfile,
    ) -> None:
        """Preflights exact or remapped proposal identities before payload writes."""
        if not pack.persona_growth_proposals:
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

        for source_proposal in pack.persona_growth_proposals:
            incoming = self._remap_persona_growth_proposal_for_import(
                pack,
                source_proposal,
                target_profile.relationship_id,
            )
            try:
                existing = self.storage.get_persona_growth_proposal(
                    incoming.proposal_id
                )
            except NotImplementedError:
                existing = next(
                    (
                        proposal
                        for proposal in (
                            self.storage.list_persona_growth_proposals(
                                target_profile.relationship_id
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
        try:
            existing_records = self.storage.list_relationship_adjudications(
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
    def _validate_persisted_turn_adjudication_pack(
        pack: MemoryPack,
        target_agent: str,
        target_user: str,
        existing_profile: Optional[RelationshipProfile],
    ) -> None:
        """Revalidates a8 direct adjudications against their portable Source Turns."""
        turns = {
            (turn.turn_id, turn.source_revision): turn
            for turn in pack.turn_records
        }
        if len(turns) != len(pack.turn_records):
            raise ValueError(
                "MemoryPack persisted-Turn adjudications contain duplicate Source Turns"
            )
        records = tuple(
            record
            for record in pack.relationship_adjudications
            if (
                record.receipt.contract_version
                == PERSISTED_TURN_CONTRACT_VERSION
                or (
                    record.receipt.contract_version
                    != "relationship-processing-v1"
                    and (
                        record.receipt.source_turn_id,
                        record.receipt.source_revision,
                    )
                    in turns
                )
            )
        )
        if not records:
            return
        relationship = pack.relationship
        if relationship is None:
            raise ValueError(
                "MemoryPack persisted-Turn adjudications require a relationship profile"
            )
        if (
            pack.agent_id != target_agent
            or pack.user_id != target_user
            or relationship.agent_id != target_agent
            or relationship.user_id != target_user
            or (
                existing_profile is not None
                and existing_profile.relationship_id
                != relationship.relationship_id
            )
        ):
            raise ValueError(
                "MemoryPack persisted-Turn adjudications require exact relationship restore"
            )

        quarantine_reason = (
            "continuity_exception_agent_evidence_quarantined",
        )
        for record in records:
            receipt = record.receipt
            if receipt.relationship_id != relationship.relationship_id:
                raise ValueError(
                    "MemoryPack persisted-Turn adjudication crosses relationship boundaries"
                )
            turn = turns.get((receipt.source_turn_id, receipt.source_revision))
            if turn is None or turn.status != TurnStatus.COMPLETED:
                raise ValueError(
                    "MemoryPack persisted-Turn adjudication requires its exact "
                    "completed Source Turn"
                )
            messages = [turn.transcript.user_message]
            if turn.transcript.agent_message is not None:
                messages.append(turn.transcript.agent_message)
            messages_by_id = {message.message_id: message for message in messages}
            if len(messages_by_id) != len(messages):
                raise ValueError(
                    "MemoryPack persisted-Turn adjudication has ambiguous source messages"
                )

            evidence_ids = set()
            for evidence in receipt.evidence:
                message = messages_by_id.get(evidence.source_id)
                if (
                    message is None
                    or evidence.source_revision != turn.source_revision
                    or evidence.role.value != message.role.value
                    or evidence.message_sha256
                    != hashlib.sha256(message.content.encode("utf-8")).hexdigest()
                    or not 0 <= evidence.start < evidence.end <= len(message.content)
                    or message.content[evidence.start : evidence.end]
                    != evidence.quote
                    or evidence.occurred_at != message.recorded_at
                ):
                    raise ValueError(
                        "MemoryPack persisted-Turn adjudication evidence does not "
                        "match its Source Turn"
                    )
                expected_evidence_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        (
                            f"erii:{relationship.relationship_id}:evidence:"
                            f"{evidence.source_id}:{evidence.source_revision}:"
                            f"{evidence.message_sha256}:{evidence.start}:{evidence.end}"
                        ),
                    )
                )
                if (
                    evidence.evidence_id != expected_evidence_id
                    or evidence.evidence_id in evidence_ids
                ):
                    raise ValueError(
                        "MemoryPack persisted-Turn adjudication evidence identity "
                        "is invalid"
                    )
                evidence_ids.add(evidence.evidence_id)

            if (
                receipt.outcome
                in (DecisionOutcome.ACCEPTED, DecisionOutcome.CORROBORATED)
                and not receipt.evidence
            ):
                raise ValueError(
                    "MemoryPack accepted persisted-Turn adjudication requires evidence"
                )
            quarantined_ids = quarantined_agent_source_ids(turn)
            cites_quarantined_agent = any(
                evidence.source_id in quarantined_ids
                for evidence in receipt.evidence
            )
            if cites_quarantined_agent and not (
                receipt.outcome == DecisionOutcome.REJECTED
                and tuple(receipt.reason_codes) == quarantine_reason
                and not receipt.event_ids
                and not record.events
                and not receipt.pivotal_eligible
            ):
                raise ValueError(
                    "MemoryPack persisted-Turn adjudication with quarantined Agent "
                    "evidence must retain its a8 rejection"
                )
            if (
                tuple(receipt.reason_codes) == quarantine_reason
                and not cites_quarantined_agent
            ):
                raise ValueError(
                    "MemoryPack persisted-Turn adjudication quarantine reason lacks "
                    "quarantined Agent evidence"
                )

    @staticmethod
    def _validate_relationship_processing_pack(
        pack: MemoryPack,
        target_agent: str,
        target_user: str,
        existing_profile: Optional[RelationshipProfile],
        *,
        storage: Optional[BaseStorage] = None,
        relationship_adjudicator: Optional[RelationshipAdjudicator] = None,
    ) -> None:
        """Preflights a7 ledgers before any legacy memory field is written."""
        if relationship_adjudicator is None:
            # `_reconstruct_batch_records` is a pure replay routine; bypassing
            # its storage-binding constructor keeps standalone pack validation
            # zero-write while using the exact production adjudication rules.
            relationship_adjudicator = object.__new__(RelationshipAdjudicator)
        runs = pack.relationship_processing_runs
        decisions = pack.persona_reflection_decisions
        processing_receipt_ids = {
            record.receipt.decision_id
            for record in pack.relationship_adjudications
            if record.receipt.contract_version
            == "relationship-processing-v1"
        }
        if processing_receipt_ids and not runs:
            raise ValueError(
                "MemoryPack relationship-processing-v1 adjudications require "
                "their processing runs"
            )
        if not runs and not decisions:
            return
        if pack.relationship is None:
            raise ValueError(
                "MemoryPack relationship processing requires a relationship profile"
            )
        relationship = pack.relationship
        if (
            pack.agent_id != target_agent
            or pack.user_id != target_user
            or relationship.agent_id != target_agent
            or relationship.user_id != target_user
        ):
            raise ValueError(
                "MemoryPack relationship processing cannot be copied to another "
                "Agent x User"
            )
        relationship_id = relationship.relationship_id
        if (
            existing_profile is not None
            and existing_profile.relationship_id != relationship_id
        ):
            raise ValueError(
                "MemoryPack relationship processing requires exact relationship restore"
            )

        turns = {
            (record.turn_id, record.source_revision): record
            for record in pack.turn_records
        }
        if len(turns) != len(pack.turn_records):
            raise ValueError(
                "MemoryPack relationship processing contains duplicate Source Turns"
            )
        events_by_id: Dict[str, RelationshipEvent] = {}
        adjudications_by_event: Dict[str, List[AdjudicationRecord]] = {}
        adjudications_by_id: Dict[str, AdjudicationRecord] = {}
        for event in [
            *pack.relationship_events,
            *(
                event
                for record in pack.relationship_adjudications
                for event in record.events
            ),
        ]:
            if event.relationship_id != relationship_id:
                raise ValueError(
                    "MemoryPack relationship processing event crosses "
                    "relationship boundaries"
                )
            existing_event = events_by_id.get(event.event_id)
            if (
                existing_event is not None
                and not existing_event.same_payload_as(event)
            ):
                raise ValueError(
                    "MemoryPack relationship processing contains conflicting "
                    "event payloads"
                )
            events_by_id[event.event_id] = event
        for record in pack.relationship_adjudications:
            receipt = record.receipt
            if receipt.relationship_id != relationship_id:
                raise ValueError(
                    "MemoryPack relationship adjudication crosses "
                    "relationship boundaries"
                )
            existing_record = adjudications_by_id.get(receipt.decision_id)
            if existing_record is not None and existing_record != record:
                raise ValueError(
                    "MemoryPack contains conflicting relationship "
                    "adjudication decisions"
                )
            if existing_record is not None:
                raise ValueError(
                    "MemoryPack contains duplicate relationship "
                    "adjudication decisions"
                )
            adjudications_by_id[receipt.decision_id] = record
            for event in record.events:
                adjudications_by_event.setdefault(event.event_id, []).append(
                    record
                )
        adjudications_for_reflection_by_event = adjudications_by_event
        event_ids = set(events_by_id)
        adjudication_ids = set(adjudications_by_id)
        reflection_decisions_by_id = {
            decision.decision_id: decision for decision in decisions
        }
        reflection_decision_ids = set(reflection_decisions_by_id)
        if len(reflection_decisions_by_id) != len(decisions):
            raise ValueError(
                "MemoryPack contains duplicate persona reflection decision IDs"
            )

        adjudicated_event_ids = set(adjudications_by_event)
        top_level_events_by_id = {
            event.event_id: event
            for event in pack.relationship_events
        }
        direct_event_order = tuple(pack.relationship_direct_event_ids)
        if runs:
            if (
                len(direct_event_order) != len(set(direct_event_order))
                or any(
                    event_id not in top_level_events_by_id
                    for event_id in direct_event_order
                )
                or (
                    set(top_level_events_by_id)
                    - adjudicated_event_ids
                    - set(direct_event_order)
                )
            ):
                raise ValueError(
                    "MemoryPack relationship processing requires the exact "
                    "direct-event journal order"
                )
        direct_events_by_id = {
            event_id: top_level_events_by_id[event_id]
            for event_id in direct_event_order
        }
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

        manifests_by_id: Dict[str, PersonaManifest] = {}
        for manifest in pack.persona_manifests:
            if manifest.manifest_id in manifests_by_id:
                raise ValueError(
                    "MemoryPack contains duplicate Persona Manifest IDs"
                )
            manifests_by_id[manifest.manifest_id] = manifest
        growth_by_identity = {}
        growth_proposal_ids = set()
        for proposal in pack.persona_growth_proposals:
            identity = (proposal.proposal_id, proposal.revision)
            if (
                identity in growth_by_identity
                or proposal.proposal_id in growth_proposal_ids
            ):
                raise ValueError(
                    "MemoryPack contains duplicate Persona Growth identities"
                )
            if proposal.relationship_id != relationship_id:
                raise ValueError(
                    "MemoryPack Persona Growth crosses relationship boundaries"
                )
            if not set(proposal.supporting_event_ids).issubset(event_ids):
                raise ValueError(
                    "MemoryPack Persona Growth references events outside the pack"
                )
            growth_by_identity[identity] = proposal
            growth_proposal_ids.add(proposal.proposal_id)

        def portable_fingerprint(value: object) -> str:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return hashlib.sha256(encoded).hexdigest()

        run_ids = set()
        run_identities = set()
        original_reflection_decision_ids = set()
        attached_processing_receipt_ids = set()
        for run in runs:
            if run.relationship_id != relationship_id:
                raise ValueError(
                    "MemoryPack relationship processing crosses relationship boundaries"
                )
            source_key = (run.source_turn_id, run.source_revision)
            source_turn = turns.get(source_key)
            if source_turn is None or source_turn.status != TurnStatus.COMPLETED:
                raise ValueError(
                    "MemoryPack relationship processing requires its exact completed "
                    "Source Turn"
                )
            expected_processing_id = (
                RelationshipProcessingCoordinator.processing_id(
                    relationship,
                    source_turn,
                    processing_mode=run.processing_mode,
                    reprocessing_id=run.reprocessing_id,
                )
            )
            if run.processing_id != expected_processing_id:
                raise ValueError(
                    "MemoryPack relationship processing ID does not match "
                    "its relationship, Source Turn, and processing identity"
                )
            if (
                run.rule_version
                != RELATIONSHIP_ADJUDICATION_RULE_VERSION
                or run.contract_version
                != "relationship-processing-v1"
            ):
                raise ValueError(
                    "MemoryPack relationship processing uses an unsupported "
                    "rule or contract version"
                )
            if not set(run.event_ids).issubset(event_ids):
                raise ValueError(
                    "MemoryPack processing run references relationship events "
                    "outside the pack"
                )
            if not set(run.decision_ids).issubset(adjudication_ids):
                raise ValueError(
                    "MemoryPack processing run references adjudications outside the pack"
                )
            if not set(run.reflection_outcome_ids).issubset(
                reflection_decision_ids
            ):
                raise ValueError(
                    "MemoryPack processing run references reflection outcomes "
                    "outside the pack"
                )
            if (
                run.adjudication_base_direct_event_count
                > len(direct_event_order)
                or run.adjudication_base_decision_count
                > len(pack.relationship_adjudications)
            ):
                raise ValueError(
                    "MemoryPack processing run adjudication baseline exceeds "
                    "its append-only journals"
                )
            baseline_direct_events = tuple(
                direct_events_by_id[event_id]
                for event_id in direct_event_order[
                    : run.adjudication_base_direct_event_count
                ]
            )
            baseline_adjudications = tuple(
                pack.relationship_adjudications[
                    : run.adjudication_base_decision_count
                ]
            )
            expected_baseline_fingerprint = (
                relationship_adjudication_baseline_fingerprint(
                    baseline_direct_events,
                    baseline_adjudications,
                )
            )
            if (
                run.adjudication_base_fingerprint
                != expected_baseline_fingerprint
            ):
                raise ValueError(
                    "MemoryPack processing run adjudication baseline does not "
                    "match its frozen journal prefixes"
                )
            if isinstance(
                run.frozen_decision,
                RelationshipEventCandidatesDecision,
            ):
                source = RelationshipProcessingCoordinator._source_turn(
                    source_turn,
                    run,
                )
                candidates = RelationshipCandidateBatch(
                    candidates=list(run.frozen_decision.candidates),
                )
                expected_decision_by_candidate = {
                    candidate.candidate_key: (
                        RelationshipAdjudicator._decision_id(
                            relationship,
                            source,
                            candidate,
                        )
                    )
                    for candidate in run.frozen_decision.candidates
                }
                expected_decision_ids = list(
                    expected_decision_by_candidate.values()
                )
                actual_records = {
                    decision_id: adjudications_by_id[decision_id]
                    for decision_id in expected_decision_ids
                    if decision_id in adjudications_by_id
                }
                if actual_records:
                    try:
                        canonical, resolution_order = (
                            relationship_adjudicator
                            ._reconstruct_batch_records(
                                relationship,
                                source,
                                candidates,
                                baseline_direct_events=(
                                    baseline_direct_events
                                ),
                                baseline_adjudications=(
                                    baseline_adjudications
                                ),
                                timestamp_hints=actual_records,
                                quarantined_source_ids=(
                                    quarantined_agent_source_ids(source_turn)
                                ),
                            )
                        )
                    except ValueError as exc:
                        raise ValueError(
                            "MemoryPack processing adjudication cannot be "
                            "replayed from its frozen candidate and baseline"
                        ) from exc
                    canonical_by_id = {
                        record.receipt.decision_id: record
                        for record in canonical.records
                    }
                    present_resolution_order = tuple(
                        decision_id
                        for decision_id in resolution_order
                        if decision_id in actual_records
                    )
                    if (
                        present_resolution_order
                        != resolution_order[: len(actual_records)]
                    ):
                        raise ValueError(
                            "MemoryPack partial processing adjudications are "
                            "not a committed decision-journal prefix"
                        )
                    for decision_id, actual_record in actual_records.items():
                        expected_record = canonical_by_id[decision_id]
                        if (
                            expected_record.to_dict()
                            != actual_record.to_dict()
                        ):
                            raise ValueError(
                                "MemoryPack relationship adjudication does "
                                "not match its frozen candidate and baseline"
                            )
                    attached_processing_receipt_ids.update(
                        set(actual_records) & processing_receipt_ids
                    )
                if run.decision_ids:
                    if tuple(run.decision_ids) != tuple(expected_decision_ids):
                        raise ValueError(
                            "MemoryPack processing run does not contain exactly "
                            "one adjudication for each frozen candidate"
                        )
                    expected_event_ids = []
                    for expected_record in canonical.records:
                        if (
                            expected_record.receipt.outcome
                            == DecisionOutcome.ACCEPTED
                        ):
                            expected_event_ids.extend(
                                event.event_id
                                for event in expected_record.events
                            )
                    if tuple(run.event_ids) != tuple(expected_event_ids):
                        raise ValueError(
                            "MemoryPack processing run event IDs do not match "
                            "its accepted adjudications"
                        )
                elif run.status not in {
                    RelationshipProcessingStatus.EXTRACTED,
                    RelationshipProcessingStatus.FAILED,
                }:
                    raise ValueError(
                        "MemoryPack advanced processing run is missing "
                        "adjudication decisions"
                    )
            for reflection_outcome_id in run.reflection_outcome_ids:
                reflection_outcome = reflection_decisions_by_id[
                    reflection_outcome_id
                ]
                expected_reflection_outcome_id = (
                    RelationshipProcessingCoordinator._reflection_decision_id(
                        run,
                        reflection_outcome.event_id,
                        PersonaReflectionRecordKind.REFLECTION,
                        None,
                    )
                )
                if (
                    reflection_outcome_id
                    != expected_reflection_outcome_id
                    or
                    reflection_outcome.source_turn_id != run.source_turn_id
                    or reflection_outcome.source_revision != run.source_revision
                    or reflection_outcome.event_id not in run.event_ids
                    or reflection_outcome.record_kind
                    != PersonaReflectionRecordKind.REFLECTION
                    or reflection_outcome.target_reflection_id is not None
                ):
                    raise ValueError(
                        "MemoryPack processing run reflection outcome does not "
                        "belong to that run"
                    )
                original_reflection_decision_ids.add(reflection_outcome_id)
            if (
                run.status == RelationshipProcessingStatus.COMPLETED
                and run.reflection_planned
                and {
                    reflection_decisions_by_id[item].event_id
                    for item in run.reflection_outcome_ids
                }
                != set(run.event_ids)
            ):
                raise ValueError(
                    "MemoryPack completed processing run is missing a "
                    "reflection outcome for an accepted event"
                )
            identity = (
                run.source_turn_id,
                run.source_revision,
                run.processing_identity,
            )
            if run.processing_id in run_ids or identity in run_identities:
                raise ValueError(
                    "MemoryPack contains duplicate relationship processing identities"
                )
            run_ids.add(run.processing_id)
            run_identities.add(identity)

        if processing_receipt_ids != attached_processing_receipt_ids:
            raise ValueError(
                "MemoryPack relationship-processing-v1 adjudications are not "
                "attached to their exact processing runs"
            )

        reflection_identities = set()
        seen_reflection_ids = set()
        seen_reflections_by_id: Dict[str, PersonaReflectionRecord] = {}
        for decision in decisions:
            if decision.relationship_id != relationship_id:
                raise ValueError(
                    "MemoryPack persona reflections cross relationship boundaries"
                )
            source_turn = turns.get(
                (decision.source_turn_id, decision.source_revision)
            )
            if source_turn is None or source_turn.status != TurnStatus.COMPLETED:
                raise ValueError(
                    "MemoryPack persona reflection requires its exact completed "
                    "Source Turn"
                )
            if decision.event_id not in event_ids:
                raise ValueError(
                    "MemoryPack persona reflection references an event outside the pack"
                )
            if (
                decision.record_kind
                == PersonaReflectionRecordKind.REFLECTION
                and decision.decision_id
                not in original_reflection_decision_ids
            ):
                raise ValueError(
                    "MemoryPack original persona reflection is not attached "
                    "to its processing run"
                )
            if decision.record_kind in {
                PersonaReflectionRecordKind.CORRECTION,
                PersonaReflectionRecordKind.REINTERPRETATION,
            }:
                expected_decision_id = (
                    RelationshipProcessingCoordinator
                    ._explicit_interpretation_decision_id(
                        relationship_id,
                        decision.target_reflection_id,
                        decision.interpretation_id,
                        decision.record_kind,
                    )
                )
                if decision.decision_id != expected_decision_id:
                    raise ValueError(
                        "MemoryPack reflection interpretation ID does not "
                        "match its stable identity"
                    )
            if (
                decision.reflection_record is not None
                and decision.record_kind
                != PersonaReflectionRecordKind.LEGACY
            ):
                expected_reflection_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"erii:{decision.decision_id}:persona-reflection",
                    )
                )
                if (
                    decision.reflection_record.reflection_id
                    != expected_reflection_id
                ):
                    raise ValueError(
                        "MemoryPack persona reflection ID does not match "
                        "its decision"
                    )
            provenance = decision.context_provenance
            if not set(provenance.prior_event_ids).issubset(event_ids):
                raise ValueError(
                    "MemoryPack persona reflection provenance references a "
                    "missing relationship event"
                )
            if decision.event_id in provenance.prior_event_ids:
                raise ValueError(
                    "MemoryPack persona reflection provenance cannot list its "
                    "current event as prior context"
                )
            if provenance.provenance_state == ReflectionProvenanceState.COMPLETE:
                if (
                    provenance.source_turn_id != decision.source_turn_id
                    or provenance.source_revision != decision.source_revision
                ):
                    raise ValueError(
                        "MemoryPack persona reflection provenance does not match "
                        "its Source Turn"
                    )

                matching_adjudications = adjudications_for_reflection_by_event.get(
                    decision.event_id,
                    [],
                )
                if len(matching_adjudications) != 1:
                    raise ValueError(
                        "MemoryPack complete persona reflection requires exactly "
                        "one accepted adjudication"
                    )
                adjudication = matching_adjudications[0]
                receipt = adjudication.receipt
                if (
                    receipt.outcome != DecisionOutcome.ACCEPTED
                    or provenance.decision_id != receipt.decision_id
                    or receipt.source_turn_id != decision.source_turn_id
                    or receipt.source_revision != decision.source_revision
                ):
                    raise ValueError(
                        "MemoryPack persona reflection provenance is not bound "
                        "to its accepted adjudication"
                    )

                evidence_by_id = {
                    item.evidence_id: item for item in receipt.evidence
                }
                if len(evidence_by_id) != len(receipt.evidence):
                    raise ValueError(
                        "MemoryPack adjudication contains duplicate evidence IDs"
                    )
                if (
                    not provenance.evidence_ids
                    or not set(provenance.evidence_ids).issubset(
                        evidence_by_id
                    )
                ):
                    raise ValueError(
                        "MemoryPack persona reflection provenance is not bound "
                        "to its adjudication evidence"
                    )

                transcript_messages = [
                    source_turn.transcript.user_message,
                    source_turn.transcript.agent_message,
                ]
                source_messages = {
                    item.message_id: item
                    for item in transcript_messages
                    if item is not None
                }
                for evidence_id in provenance.evidence_ids:
                    evidence = evidence_by_id[evidence_id]
                    source_message = source_messages.get(evidence.source_id)
                    expected_message_hash = (
                        hashlib.sha256(
                            source_message.content.encode("utf-8")
                        ).hexdigest()
                        if source_message is not None
                        else None
                    )
                    expected_evidence_id = (
                        str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                (
                                    f"erii:{relationship_id}:evidence:"
                                    f"{evidence.source_id}:"
                                    f"{evidence.source_revision}:"
                                    f"{expected_message_hash}:"
                                    f"{evidence.start}:{evidence.end}"
                                ),
                            )
                        )
                        if expected_message_hash is not None
                        else None
                    )
                    if (
                        source_message is None
                        or evidence.source_revision
                        != decision.source_revision
                        or evidence.role.value != source_message.role.value
                        or evidence.message_sha256 != expected_message_hash
                        or evidence.end > len(source_message.content)
                        or source_message.content[
                            evidence.start : evidence.end
                        ]
                        != evidence.quote
                        or evidence.occurred_at
                        != source_message.recorded_at
                        or evidence.evidence_id != expected_evidence_id
                    ):
                        raise ValueError(
                            "MemoryPack persona reflection cites invalid Source "
                            "Turn evidence"
                        )

                blueprint = relationship.blueprint
                if (
                    provenance.blueprint_id != blueprint.blueprint_id
                    or provenance.blueprint_sha256 != blueprint.source_sha256
                    or provenance.blueprint_revision != blueprint.revision
                ):
                    raise ValueError(
                        "MemoryPack persona reflection provenance does not match "
                        "its Character Blueprint"
                    )
                if provenance.baseline_fingerprint != portable_fingerprint(
                    relationship.baseline.to_dict()
                ):
                    raise ValueError(
                        "MemoryPack persona reflection provenance does not match "
                        "its Relationship Baseline"
                    )

                if provenance.manifest_id is not None:
                    manifest = manifests_by_id.get(provenance.manifest_id)
                    if (
                        manifest is None
                        or relationship.manifest_id != provenance.manifest_id
                        or provenance.manifest_revision
                        != manifest.approved_revision
                        or provenance.manifest_fingerprint
                        != manifest.content_fingerprint
                        or manifest.blueprint_id != blueprint.blueprint_id
                        or manifest.blueprint_revision != blueprint.revision
                        or manifest.source_sha256 != blueprint.source_sha256
                    ):
                        raise ValueError(
                            "MemoryPack persona reflection provenance does not "
                            "match its Persona Manifest"
                        )

                for reference in provenance.approved_growth:
                    proposal = growth_by_identity.get(
                        (reference.proposal_id, reference.revision)
                    )
                    if (
                        proposal is None
                        or proposal.relationship_id != relationship_id
                        or proposal.status != PersonaGrowthStatus.APPROVED
                        or reference.content_fingerprint
                        != portable_fingerprint(proposal.to_dict())
                        or reference.approved_at != proposal.decided_at
                        or not set(proposal.supporting_event_ids).issubset(
                            event_ids
                        )
                    ):
                        raise ValueError(
                            "MemoryPack persona reflection provenance does not "
                            "match its approved Persona Growth"
                        )
            if decision.target_reflection_id is not None:
                target_reflection = seen_reflections_by_id.get(
                    decision.target_reflection_id
                )
                if target_reflection is None:
                    raise ValueError(
                        "MemoryPack correction or reinterpretation precedes its target"
                    )
                target_provenance = target_reflection.context_provenance
                if (
                    decision.event_id != target_reflection.event_id
                    or decision.source_turn_id
                    != target_provenance.source_turn_id
                    or decision.source_revision
                    != target_provenance.source_revision
                    or provenance.decision_id
                    != target_provenance.decision_id
                    or provenance.evidence_ids
                    != target_provenance.evidence_ids
                    or provenance.blueprint_id
                    != target_provenance.blueprint_id
                    or provenance.blueprint_sha256
                    != target_provenance.blueprint_sha256
                    or provenance.blueprint_revision
                    != target_provenance.blueprint_revision
                    or provenance.manifest_id
                    != target_provenance.manifest_id
                    or provenance.manifest_revision
                    != target_provenance.manifest_revision
                    or provenance.manifest_fingerprint
                    != target_provenance.manifest_fingerprint
                    or provenance.baseline_fingerprint
                    != target_provenance.baseline_fingerprint
                    or decision.target_reflection_id
                    not in provenance.prior_reflection_ids
                ):
                    raise ValueError(
                        "MemoryPack correction or reinterpretation does not "
                        "share its target reflection's event and source binding"
                    )
            if not set(
                decision.context_provenance.prior_reflection_ids
            ).issubset(seen_reflection_ids):
                raise ValueError(
                    "MemoryPack persona reflection provenance references a later "
                    "or missing reflection"
                )
            if decision.interpretation_identity in reflection_identities:
                raise ValueError(
                    "MemoryPack contains duplicate persona reflection identities"
                )
            reflection_identities.add(decision.interpretation_identity)
            if decision.reflection_record is not None:
                reflection_id = decision.reflection_record.reflection_id
                if reflection_id in seen_reflection_ids:
                    raise ValueError(
                        "MemoryPack contains duplicate persona reflection records"
                    )
                seen_reflection_ids.add(reflection_id)
                seen_reflections_by_id[reflection_id] = (
                    decision.reflection_record
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

    @staticmethod
    def _remap_temporal_payload(payload, event_id_map: Mapping[str, str]):
        """Remaps every relationship-event reference in one temporal payload."""
        if payload is None or isinstance(payload, (PromiseSpec, OpenLoopSpec)):
            return payload

        def mapped(source_id: str) -> str:
            try:
                return event_id_map[source_id]
            except KeyError as exc:
                raise ValueError(
                    "MemoryPack temporal payload references an event outside the pack"
                ) from exc

        if isinstance(payload, PromiseConditionConfirmation):
            return replace(payload, promise_event_id=mapped(payload.promise_event_id))
        if isinstance(payload, PromiseResolution):
            return replace(
                payload,
                promise_event_id=mapped(payload.promise_event_id),
                superseding_promise_event_id=(
                    mapped(payload.superseding_promise_event_id)
                    if payload.superseding_promise_event_id is not None
                    else None
                ),
            )
        if isinstance(payload, OpenLoopResolution):
            return replace(
                payload,
                open_loop_event_id=mapped(payload.open_loop_event_id),
                superseding_open_loop_event_id=(
                    mapped(payload.superseding_open_loop_event_id)
                    if payload.superseding_open_loop_event_id is not None
                    else None
                ),
            )
        raise ValueError("unsupported temporal payload in MemoryPack")

    def _import_persona_compilation(
        self,
        pack: MemoryPack,
        target_profile: RelationshipProfile,
        *,
        validate_only: bool = False,
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
                decision_reason=source_proposal.decision_reason,
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
                decision_reason=mapped_proposal.decision_reason,
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
            validate_persona_premise_binding(
                target_profile.premise,
                selected_manifest.candidate,
            )

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
        legacy_reason_loss_keys = set()
        for mapped in mapped_proposals:
            key = (mapped.proposal_id, mapped.revision)
            existing = existing_compilations.get(key)
            if existing is None:
                continue
            if immutable_proposal_content(existing) != immutable_proposal_content(mapped):
                raise ValueError("MemoryPack proposal identity conflicts with stored content")
            if existing.status == mapped.status:
                if proposal_lifecycle(existing) != proposal_lifecycle(mapped):
                    if not has_legacy_persona_decision_reason_loss(
                        existing,
                        mapped,
                    ):
                        raise ValueError(
                            "MemoryPack proposal lifecycle conflicts with storage"
                        )
                    legacy_reason_loss_keys.add(key)
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
        if validate_only:
            return target_profile

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
            applied_mapped = (
                current
                if key in legacy_reason_loss_keys
                else mapped
            )
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
                    applied_mapped,
                    status=PersonaCompilationStatus.APPROVED,
                    decided_by=matching_manifest.approved_by,
                    decided_at=matching_manifest.approved_at,
                    decision_reason=applied_mapped.decision_reason,
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
                existing_compilations[key] = applied_mapped
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

