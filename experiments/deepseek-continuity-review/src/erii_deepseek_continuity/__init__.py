"""DeepSeek Continuity Review Experiment for E.R.I.I.

This module implements a ContinuityEvaluatorV1 that uses DeepSeek's
thinking mode to evaluate character continuity.

Key guarantees:
- Raw reasoning never enters return values
- Implements existing E.R.I.I. contracts, does not redefine them
- Can be deleted without affecting E.R.I.I. core
"""

from .evaluator import DeepSeekContinuityEvaluator
from .client import DeepSeekClient, DeepSeekAPIError
from .evidence_resolver import FakeEvidenceResolver

__all__ = [
    "DeepSeekContinuityEvaluator",
    "DeepSeekClient",
    "DeepSeekAPIError",
    "FakeEvidenceResolver",
]
