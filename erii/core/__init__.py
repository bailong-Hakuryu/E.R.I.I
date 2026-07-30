"""Core processing components for E.R.I.I."""

from erii.core.archiver import AsyncArchiverWorker
from erii.core.budget import MemoryBudgetManager
from erii.core.consolidation import RelationshipConsolidator
from erii.core.continuity import (
    ContinuityAggregationPolicyV1,
    ContinuityEvaluationCapabilityError,
    ContinuityEvaluationCoordinator,
    InteractionContextEvaluationCoordinator,
    RelationshipSafetySignalProjector,
    VoicePatternMatcher,
)
from erii.core.decay import MemoryDecayEvaluator
from erii.core.relationship_processing import (
    RelationshipProcessingCapabilityError,
    RelationshipProcessingCoordinator,
    RelationshipProcessingError,
    RelationshipProcessingSubmissionError,
)
from erii.core.retriever import MemoryRetriever
from erii.core.relationship import RelationshipProjector

__all__ = [
    "AsyncArchiverWorker",
    "ContinuityAggregationPolicyV1",
    "ContinuityEvaluationCapabilityError",
    "ContinuityEvaluationCoordinator",
    "InteractionContextEvaluationCoordinator",
    "MemoryBudgetManager",
    "MemoryDecayEvaluator",
    "MemoryRetriever",
    "RelationshipConsolidator",
    "RelationshipProcessingCapabilityError",
    "RelationshipProcessingCoordinator",
    "RelationshipProcessingError",
    "RelationshipProcessingSubmissionError",
    "RelationshipProjector",
    "RelationshipSafetySignalProjector",
    "VoicePatternMatcher",
]
