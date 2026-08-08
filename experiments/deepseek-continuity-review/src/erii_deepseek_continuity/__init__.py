"""DeepSeek Continuity Review Experiment for E.R.I.I.

This module implements a ContinuityEvaluatorV1 that uses DeepSeek's
thinking mode to evaluate character continuity.

Key guarantees:
- Raw reasoning never enters return values
- Implements existing E.R.I.I. contracts, does not redefine them
- Can be deleted without affecting E.R.I.I. core
"""

from .client import DeepSeekAPIError, DeepSeekClient
from .evaluator import DeepSeekContinuityEvaluator
from .evidence_resolver import (
    CrossRelationshipLeakError,
    EvidenceResolver,
    EvidenceResolutionError,
    FakeEvidenceResolver,
    ResolvedEvidence,
    ResolvedVoiceActivation,
)
from .real_evidence_resolver import (
    ERIIStorageBackend,
    FileStorageAdapter,
    RealEvidenceResolver,
    SQLiteStorageAdapter,
    StorageBackend,
)
from .prompt_builder import MAX_REVIEW_PROMPT_BYTES, PromptBudgetError
from .response_parser import ParsingError

__all__ = [
    "DeepSeekContinuityEvaluator",
    "DeepSeekClient",
    "DeepSeekAPIError",
    "ERIIStorageBackend",
    "EvidenceResolver",
    "EvidenceResolutionError",
    "FakeEvidenceResolver",
    "FileStorageAdapter",
    "CrossRelationshipLeakError",
    "ParsingError",
    "PromptBudgetError",
    "MAX_REVIEW_PROMPT_BYTES",
    "RealEvidenceResolver",
    "ResolvedEvidence",
    "ResolvedVoiceActivation",
    "SQLiteStorageAdapter",
    "StorageBackend",
]
