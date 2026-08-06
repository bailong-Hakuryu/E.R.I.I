"""DeepSeek Continuity Evaluator (experimental).

Fully implements E.R.I.I.'s ContinuityEvaluatorV1 contract.
Does not generate replies, does not redefine domain models.

Key guarantees:
- Raw reasoning never enters return values
- Returns real ContinuityEvaluationDecision
- Actor/Reviewer separation
- Zero provider brand in core persistence
"""

from erii.models.continuity import (
    ContinuityEvaluatorDescriptor,
    ContinuityEvaluationRequest,
    ContinuityEvaluationDecision,
)

from .client import DeepSeekClient
from .evidence_resolver import EvidenceResolver
from .prompt_builder import build_review_prompt
from .response_parser import parse_to_decision


class DeepSeekContinuityEvaluator:
    """
    DeepSeek-based Continuity Reviewer (experimental).

    Experiment hypothesis:
    - thinking enabled improves drift detection accuracy
    - does not introduce cross-relationship leaks
    - does not reduce source accuracy

    Zero-leakage commitment:
    - raw reasoning never enters return values, logs, exceptions, repr, serialization
    - prompt never enters return values, logs, exceptions
    - API key never enters return values, logs, exceptions
    - provider fields never enter core persistence
    """

    def __init__(
        self,
        *,
        client: DeepSeekClient,
        evidence_resolver: EvidenceResolver,
    ):
        """
        Initialize evaluator.

        Args:
            client: DeepSeekClient instance (required, no default)
            evidence_resolver: EvidenceResolver instance (required, no default)
        """
        self.descriptor = ContinuityEvaluatorDescriptor(
            evaluator_id="deepseek-shadow-reviewer-experimental",
            evaluator_version="0.1.0",
        )

        self._client = client
        self._evidence_resolver = evidence_resolver

    def evaluate(
        self,
        request: ContinuityEvaluationRequest,
    ) -> ContinuityEvaluationDecision:
        """
        Evaluate proposed_reply continuity.

        Returns real ContinuityEvaluationDecision.
        """

        # 1. Resolve evidence refs to readable excerpts (experiment-internal)
        resolved_evidence = self._evidence_resolver.resolve(
            persona_refs=request.persona_context_refs,
            relationship_refs=request.relationship_context_refs,
            relationship_id=request.relationship_id,
        )

        # 2. Resolve voice activations
        resolved_activations = self._evidence_resolver.resolve_voice_activations(
            activations=request.voice_pattern_activations,
        )

        # 3. Build review prompt (with resolved evidence)
        messages = build_review_prompt(
            request=request,
            resolved_evidence=resolved_evidence,
            resolved_activations=resolved_activations,
        )

        # 4. Call DeepSeek API (thinking switch encapsulated in client)
        response = self._client.complete(messages)

        # 5. Parse to real ContinuityEvaluationDecision
        # Internally constructs ContinuityFinding and validates all constraints
        decision = parse_to_decision(
            response=response,
            request=request,
            resolved_evidence=resolved_evidence,
            resolved_activations=resolved_activations,
        )

        # 6. Return real decision (raw reasoning already discarded in client layer)
        return decision
