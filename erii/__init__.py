"""E.R.I.I. — Experiential Recall & Impression Integration Engine

Export public API symbols.
Follows Google Python Style Guide.
"""

from erii.adapters.base import BaseLLMAdapter
from erii.adapters.custom_adapter import CallableLLMAdapter
from erii.adapters.openai_adapter import OpenAIAdapter
from erii.core.queue.base import BaseTaskQueue
from erii.core.queue.persistent_queue import PersistentTaskQueue
from erii.engine import ERIIEngine
from erii.models.config import ERIIConfig
from erii.models.adjudication import (
    AdjudicationBatchResult,
    AdjudicationRecord,
    CandidateConflictError,
    DecisionOutcome,
    DecisionReceipt,
    EvidenceCitation,
    EvidenceReference,
    GrowthTriggerKind,
    PersonaGrowthConflictError,
    PersonaGrowthDecision,
    PersonaGrowthIntentCandidate,
    PersonaGrowthProposal,
    PersonaGrowthStatus,
    RelationshipCandidateBatch,
    RelationshipEventCandidate,
    RelationshipPolicySpec,
    RelationshipSignal,
    RelationshipSignalType,
    SignalStrength,
    SourceMessage,
    SourceProcessingMode,
    SourceRole,
    SourceTurn,
)
from erii.models.node import MemoryNode, MemoryState, MemoryType, MemoryVisibility
from erii.models.pack import MemoryPack
from erii.models.relationship import (
    BeliefOperation,
    BeliefUpdate,
    CharacterBlueprint,
    CurrentBelief,
    EventConflictError,
    IdentityKind,
    PersonaConflictError,
    RelationshipEvent,
    RelationshipEventType,
    RelationshipNotFoundError,
    RelationshipProfile,
    RelationshipSnapshot,
    RelationshipState,
    StateReason,
    TemporalContext,
)
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage
from erii.storage.file_storage import FileStorage
from erii.storage.sqlite_storage import SQLiteStorage
from erii.vector.base import BaseEmbeddingProvider, BaseVectorStore
from erii.vector.in_memory_vector import (
    CallableEmbeddingAdapter,
    DummyEmbeddingProvider,
    InMemoryVectorStore,
)
from erii._version import __version__

__all__ = [
    "__version__",
    "AdjudicationBatchResult",
    "AdjudicationRecord",
    "ERIIEngine",
    "ERIIConfig",
    "MemoryNode",
    "MemoryType",
    "MemoryState",
    "MemoryVisibility",
    "MemoryPack",
    "BeliefOperation",
    "BeliefUpdate",
    "CharacterBlueprint",
    "CandidateConflictError",
    "CurrentBelief",
    "DecisionOutcome",
    "DecisionReceipt",
    "EventConflictError",
    "EvidenceCitation",
    "EvidenceReference",
    "GrowthTriggerKind",
    "IdentityKind",
    "PersonaConflictError",
    "PersonaGrowthConflictError",
    "PersonaGrowthDecision",
    "PersonaGrowthIntentCandidate",
    "PersonaGrowthProposal",
    "PersonaGrowthStatus",
    "RelationshipCandidateBatch",
    "RelationshipEvent",
    "RelationshipEventCandidate",
    "RelationshipEventType",
    "RelationshipNotFoundError",
    "RelationshipProfile",
    "RelationshipPolicySpec",
    "RelationshipSignal",
    "RelationshipSignalType",
    "RelationshipSnapshot",
    "RelationshipState",
    "StateReason",
    "TemporalContext",
    "SignalStrength",
    "SourceMessage",
    "SourceProcessingMode",
    "SourceRole",
    "SourceTurn",
    "BaseLLMAdapter",
    "CallableLLMAdapter",
    "OpenAIAdapter",
    "BaseStorage",
    "FileStorage",
    "SQLiteStorage",
    "SecuritySanitizer",
    "BaseTaskQueue",
    "PersistentTaskQueue",
    "BaseEmbeddingProvider",
    "BaseVectorStore",
    "CallableEmbeddingAdapter",
    "DummyEmbeddingProvider",
    "InMemoryVectorStore",
]
