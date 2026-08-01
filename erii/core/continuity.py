"""Deterministic continuity aggregation and temporary voice activation."""

import hashlib
import json
from typing import Dict, Mapping, Optional, Sequence, Tuple
import uuid

from erii.models.continuity import (
    CONTINUITY_AGGREGATION_POLICY_V1_VERSION,
    ContinuityEvaluationDecision,
    ContinuityEvaluationRequest,
    ContinuityEvaluationResult,
    ContinuityEvaluatorDescriptor,
    ContinuityEvaluatorV1,
    ContinuityReviewBinding,
    InteractionContextEvaluationRequest,
    InteractionContextEvaluatorDescriptor,
    InteractionContextEvaluatorV1,
    InteractionContextNoSignalsDecision,
    VoicePatternActivation,
    _attest_voice_pattern_activation,
    _aggregate_continuity_decision_v1,
    _continuity_style_revision_advised_v1,
    continuity_evaluation_decision_from_value,
    interaction_context_evaluation_decision_from_value,
)
from erii.models.continuity_evidence import (
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)
from erii.models.persona import (
    ContextualVoicePatternCandidate,
    PersonaManifest,
    PersonaScope,
    VoicePatternCondition,
)
from erii.models.relationship import (
    RelationshipPremise,
    RelationshipPremiseMode,
    RelationshipSnapshot,
)
from erii.core.persona_context import validate_persona_premise_binding
from erii.models.turn import (
    ContextSignalSource,
    ContinuityAssessmentStatus,
    ContinuityVerdict,
    InteractionContextSignal,
    ReplyContinuityAssessment,
)
from erii.models.voice_trace import (
    VoiceActivationTrace,
    VoiceConditionMatchTrace,
)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


_TRUSTED_CONTEXT_PRODUCER = object()


def _attest_context_signal(
    signal: InteractionContextSignal,
) -> InteractionContextSignal:
    """Marks one non-serialized signal as produced by this runtime kernel."""
    object.__setattr__(
        signal,
        "_runtime_attestation",
        _TRUSTED_CONTEXT_PRODUCER,
    )
    return signal


class ContinuityEvaluationCapabilityError(RuntimeError):
    """Raised when pre-delivery continuity evaluation is not configured."""


class RelationshipSafetySignalProjector:
    """Deterministically derives one current-relationship safety band."""

    VERSION = "relationship-safety-policy-v1"
    LOW_THRESHOLD = 1.0 / 3.0
    HIGH_THRESHOLD = 2.0 / 3.0

    @classmethod
    def project(
        cls,
        snapshot: RelationshipSnapshot,
        *,
        source_turn_id: str,
        history_prefix_fingerprint: str,
    ) -> InteractionContextSignal:
        if not isinstance(snapshot, RelationshipSnapshot):
            raise TypeError("snapshot must be a RelationshipSnapshot")
        clean_turn = source_turn_id.strip()
        if not clean_turn:
            raise ValueError("source_turn_id must be non-empty")
        state = snapshot.state
        if (
            state.safety < cls.LOW_THRESHOLD
            or state.conflict_tension > cls.HIGH_THRESHOLD
        ):
            value = "low"
        elif (
            state.safety >= cls.HIGH_THRESHOLD
            and state.conflict_tension <= cls.LOW_THRESHOLD
        ):
            value = "high"
        else:
            value = "moderate"

        relationship_id = snapshot.profile.relationship_id
        evidence_refs = [
            (
                f"relationship-baseline:{relationship_id}:"
                f"{snapshot.profile.baseline.policy_version}"
            )
        ]
        for dimension in ("safety", "conflict_tension"):
            reason = snapshot.state_reasons.get(dimension)
            if reason is not None:
                evidence_refs.append(
                    f"relationship-event:{reason.evidence_event_id}"
                )
        evidence_refs = list(dict.fromkeys(evidence_refs))
        payload = {
            "relationship_id": relationship_id,
            "source_turn_id": clean_turn,
            "policy_version": cls.VERSION,
            "state": snapshot.state.to_dict(),
            "event_count": snapshot.event_count,
            "last_event_id": snapshot.last_event_id,
            "value": value,
            "evidence_refs": evidence_refs,
        }
        fingerprint = _canonical_hash(payload)
        signal = InteractionContextSignal(
                signal_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"erii:relationship-safety:{fingerprint}",
                    )
                ),
                source=ContextSignalSource.CORE_DERIVED,
                signal_type="relationship_safety",
                value=value,
                evidence_refs=tuple(evidence_refs),
                relationship_id=relationship_id,
                source_turn_id=clean_turn,
                producer_version=cls.VERSION,
            )
        object.__setattr__(
            signal,
            "_trace_context",
            {
                "kind": ContextSignalSource.CORE_DERIVED.value,
                "producer_input_fingerprint": fingerprint,
                "history_prefix_fingerprint": history_prefix_fingerprint,
                "relationship_projection_version": "relationship-projector/v2",
            },
        )
        return _attest_context_signal(signal)


class InteractionContextEvaluationCoordinator:
    """Validates a scoped emotion evaluator and stamps trusted signals."""

    VERSION = "interaction-context-evaluator-v1"

    @classmethod
    def input_fingerprint(
        cls,
        request: InteractionContextEvaluationRequest,
        evaluator: InteractionContextEvaluatorV1,
        *,
        descriptor: Optional[InteractionContextEvaluatorDescriptor] = None,
    ) -> str:
        descriptor = descriptor or cls._descriptor(evaluator)
        return _canonical_hash(
            {
                "contract_version": cls.VERSION,
                "descriptor": descriptor.to_dict(),
                "request": request.to_dict(),
            }
        )

    @classmethod
    def evaluate(
        cls,
        request: InteractionContextEvaluationRequest,
        evaluator: InteractionContextEvaluatorV1,
        *,
        descriptor: Optional[InteractionContextEvaluatorDescriptor] = None,
        input_fingerprint: Optional[str] = None,
    ) -> Tuple[InteractionContextSignal, ...]:
        if not isinstance(request, InteractionContextEvaluationRequest):
            raise TypeError(
                "request must be an InteractionContextEvaluationRequest"
            )
        descriptor = descriptor or cls._descriptor(evaluator)
        decision = interaction_context_evaluation_decision_from_value(
            evaluator.evaluate(request)
        )
        if isinstance(decision, InteractionContextNoSignalsDecision):
            return ()

        requested_values = {
            value.casefold(): value
            for value in request.emotion_values
        }
        allowed_refs = set(request.allowed_evidence_refs)
        request_fingerprint = input_fingerprint or cls.input_fingerprint(
            request,
            evaluator,
            descriptor=descriptor,
        )
        signals = []
        for candidate in decision.signals:
            canonical_value = requested_values.get(candidate.value.casefold())
            if canonical_value is None:
                raise ValueError(
                    "interaction context evaluator returned an emotion "
                    "outside the approved pattern vocabulary"
                )
            unknown_refs = set(candidate.evidence_refs).difference(allowed_refs)
            if unknown_refs:
                raise ValueError(
                    "interaction context evaluator cited evidence outside "
                    "the current relationship/Turn: "
                    + ", ".join(sorted(unknown_refs))
                )
            payload = {
                "relationship_id": request.relationship_id,
                "source_turn_id": request.turn_id,
                "candidate_key": candidate.candidate_key,
                "value": canonical_value,
                "evidence_refs": list(candidate.evidence_refs),
                "descriptor": descriptor.to_dict(),
                "request_fingerprint": request_fingerprint,
            }
            fingerprint = _canonical_hash(payload)
            signal = InteractionContextSignal(
                    signal_id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"erii:inferred-emotion:{fingerprint}",
                        )
                    ),
                    source=ContextSignalSource.EVALUATOR_INFERRED,
                    signal_type="emotion",
                    value=canonical_value,
                    evidence_refs=tuple(candidate.evidence_refs),
                    relationship_id=request.relationship_id,
                    source_turn_id=request.turn_id,
                    producer_version=descriptor.public_version,
                    )
            object.__setattr__(
                signal,
                "_trace_context",
                {
                    "kind": ContextSignalSource.EVALUATOR_INFERRED.value,
                    "candidate_key": candidate.candidate_key,
                    "producer_input_fingerprint": request_fingerprint,
                    "evaluator_descriptor": descriptor.to_dict(),
                },
            )
            signals.append(_attest_context_signal(signal))
        return tuple(signals)

    @staticmethod
    def _descriptor(
        evaluator: InteractionContextEvaluatorV1,
    ) -> InteractionContextEvaluatorDescriptor:
        descriptor = getattr(evaluator, "descriptor", None)
        if isinstance(descriptor, InteractionContextEvaluatorDescriptor):
            return descriptor
        if isinstance(descriptor, Mapping):
            return InteractionContextEvaluatorDescriptor.from_dict(descriptor)
        raise TypeError(
            "InteractionContextEvaluatorV1 requires a versioned descriptor"
        )


class VoicePatternMatcher:
    """Pure matcher from approved patterns and source-typed turn signals."""

    VERSION = "voice-pattern-matcher-v1"

    @classmethod
    def match(
        cls,
        *,
        manifest: PersonaManifest,
        relationship_id: str,
        source_turn_id: str,
        persona_id: str,
        premise: RelationshipPremise,
        signals: Sequence[InteractionContextSignal],
        context_baseline_fingerprint: str,
    ) -> Tuple[VoicePatternActivation, ...]:
        if not isinstance(manifest, PersonaManifest):
            raise TypeError("manifest must be one approved PersonaManifest")
        if not isinstance(premise, RelationshipPremise):
            premise = RelationshipPremise.from_dict(premise)
        validate_persona_premise_binding(premise, manifest.candidate)
        clean_relationship = relationship_id.strip()
        clean_turn = source_turn_id.strip()
        clean_persona = persona_id.strip()
        if not clean_relationship or not clean_turn or not clean_persona:
            raise ValueError(
                "relationship_id, source_turn_id, and persona_id "
                "must be non-empty"
            )

        normalized_signals = tuple(
            item
            if isinstance(item, InteractionContextSignal)
            else InteractionContextSignal.from_dict(item)
            for item in signals
        )
        by_id: Dict[str, InteractionContextSignal] = {}
        for signal in normalized_signals:
            existing = by_id.get(signal.signal_id)
            if existing is not None and not existing.same_claim_as(signal):
                raise ValueError("one signal_id cannot carry different context claims")
            by_id[signal.signal_id] = signal
        ordered_signals = tuple(
            by_id[key]
            for key in sorted(by_id)
            if cls._signal_scope_is_available(
                by_id[key],
                relationship_id=clean_relationship,
                source_turn_id=clean_turn,
            )
        )

        activations = []
        for pattern in sorted(
            manifest.contextual_voice_patterns,
            key=lambda item: item.pattern_id,
        ):
            if not cls._scope_is_available(pattern, premise):
                continue
            supporting = []
            for condition in pattern.conditions:
                signal = next(
                    (
                        item
                        for item in ordered_signals
                        if cls._condition_matches(condition, item)
                        and item.signal_id not in {
                            selected.signal_id for selected in supporting
                        }
                    ),
                    None,
                )
                if signal is None:
                    supporting = []
                    break
                supporting.append(signal)
            if not supporting:
                continue

            payload = {
                "relationship_id": clean_relationship,
                "source_turn_id": clean_turn,
                "persona_id": clean_persona,
                "manifest_id": manifest.manifest_id,
                "manifest_fingerprint": manifest.content_fingerprint,
                "context_baseline_fingerprint": context_baseline_fingerprint,
                "pattern": pattern.model_dump(mode="json"),
                "matcher_version": cls.VERSION,
                "signals": [
                    {
                        "signal_id": item.signal_id,
                        "source": item.source.value,
                        "signal_type": item.signal_type,
                        "value": item.value,
                        "evidence_refs": list(item.evidence_refs),
                        "relationship_id": item.relationship_id,
                        "source_turn_id": item.source_turn_id,
                        "producer_version": item.producer_version,
                    }
                    for item in supporting
                ],
            }
            fingerprint = _canonical_hash(payload)
            activation_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"erii:voice-activation:{fingerprint}",
                )
            )
            matched_values = tuple(
                next(
                    value
                    for value in condition.values
                    if value.casefold() == signal.value.casefold()
                )
                for condition, signal in zip(pattern.conditions, supporting)
            )
            activation = VoicePatternActivation(
                    activation_id=activation_id,
                    relationship_id=clean_relationship,
                    source_turn_id=clean_turn,
                    persona_id=clean_persona,
                    manifest_id=manifest.manifest_id,
                    manifest_fingerprint=manifest.content_fingerprint,
                    context_baseline_fingerprint=context_baseline_fingerprint,
                    pattern_id=pattern.pattern_id,
                    pattern_scope=pattern.scope,
                    matcher_version=cls.VERSION,
                    supporting_signal_ids=tuple(
                        item.signal_id for item in supporting
                    ),
                    condition_ids=tuple(
                        item.condition_id for item in pattern.conditions
                    ),
                    input_fingerprint=fingerprint,
                )
            activations.append(
                _attest_voice_pattern_activation(
                    activation,
                    supporting,
                    condition_types=tuple(
                        item.condition_type.value for item in pattern.conditions
                    ),
                    matched_values=matched_values,
                )
            )
        return tuple(activations)

    @staticmethod
    def _signal_scope_is_available(
        signal: InteractionContextSignal,
        *,
        relationship_id: str,
        source_turn_id: str,
    ) -> bool:
        if signal.source == ContextSignalSource.HOST_OBSERVED:
            if signal.relationship_id is None:
                return True
        elif (
            getattr(signal, "_runtime_attestation", None)
            is not _TRUSTED_CONTEXT_PRODUCER
        ):
            # Source labels and scope strings are data, not authority. Only a
            # runtime producer can authorize a derived voice condition.
            return False
        elif signal.relationship_id is None:
            # Pre-a7 records may contain derived labels without scope. They
            # remain readable but cannot authorize a runtime voice pattern.
            return False
        if (
            signal.relationship_id != relationship_id
            or signal.source_turn_id != source_turn_id
        ):
            raise ValueError(
                "interaction context signal belongs to another "
                "relationship or Turn"
            )
        return True

    @staticmethod
    def _scope_is_available(
        pattern: ContextualVoicePatternCandidate,
        premise: RelationshipPremise,
    ) -> bool:
        if pattern.scope != PersonaScope.CANONICAL_RELATIONSHIP:
            return True
        return (
            premise.mode == RelationshipPremiseMode.CANONICAL_CONTINUATION
            and premise.premise_id in pattern.canonical_premise_template_ids
        )

    @staticmethod
    def _condition_matches(
        condition: VoicePatternCondition,
        signal: InteractionContextSignal,
    ) -> bool:
        if signal.source != condition.signal_source:
            return False
        if signal.signal_type.casefold() != condition.condition_type.value.casefold():
            return False
        if signal.value.casefold() not in {
            value.casefold() for value in condition.values
        }:
            return False
        if signal.source in {
            ContextSignalSource.CORE_DERIVED,
            ContextSignalSource.EVALUATOR_INFERRED,
        } and not signal.evidence_refs:
            return False
        return True


class ContinuityAggregationPolicyV1:
    """Versioned deterministic aggregation of five independent findings."""

    VERSION = CONTINUITY_AGGREGATION_POLICY_V1_VERSION

    @classmethod
    def aggregate(cls, decision: ContinuityEvaluationDecision) -> ContinuityVerdict:
        return _aggregate_continuity_decision_v1(decision)

    @staticmethod
    def style_revision_advised(
        decision: ContinuityEvaluationDecision,
    ) -> bool:
        """Returns the product-facing voice advisory without changing verdict."""
        return _continuity_style_revision_advised_v1(decision)


class ContinuityEvaluationCoordinator:
    """Deep public seam that validates evaluator evidence and aggregates it."""

    @classmethod
    def evaluate(
        cls,
        request: ContinuityEvaluationRequest,
        evaluator: ContinuityEvaluatorV1,
    ) -> ContinuityEvaluationResult:
        if not isinstance(request, ContinuityEvaluationRequest):
            raise TypeError("request must be a ContinuityEvaluationRequest")
        descriptor = getattr(evaluator, "descriptor", None)
        if not isinstance(descriptor, ContinuityEvaluatorDescriptor):
            if not isinstance(descriptor, Mapping):
                raise TypeError(
                    "ContinuityEvaluatorV1 requires a versioned descriptor"
                )
            descriptor = ContinuityEvaluatorDescriptor.from_dict(descriptor)
        decision = continuity_evaluation_decision_from_value(
            evaluator.evaluate(request)
        )
        cls._validate_sources_and_reply_spans(request, decision)
        traces = cls._project_voice_activation_traces(request, decision)
        cited_activation_ids = tuple(
            sorted(trace.activation_id for trace in traces)
        )
        verdict = ContinuityAggregationPolicyV1.aggregate(decision)
        style_revision_advised = (
            ContinuityAggregationPolicyV1.style_revision_advised(decision)
        )
        return ContinuityEvaluationResult(
            assessment=ReplyContinuityAssessment(
                status=ContinuityAssessmentStatus.COMPLETED,
                evaluator_version=(
                    f"{descriptor.public_version}+"
                    f"{ContinuityAggregationPolicyV1.VERSION}"
                ),
                verdict=verdict,
            ),
            findings=decision.findings,
            evaluator_descriptor=descriptor,
            aggregation_policy_version=ContinuityAggregationPolicyV1.VERSION,
            review_binding=ContinuityReviewBinding.from_request(
                request,
                voice_pattern_activation_ids=cited_activation_ids,
            ),
            style_revision_advised=style_revision_advised,
            voice_activation_traces=traces,
        )

    @staticmethod
    def _validate_sources_and_reply_spans(
        request: ContinuityEvaluationRequest,
        decision: ContinuityEvaluationDecision,
    ) -> None:
        activation_ids = {
            activation.activation_id
            for activation in request.voice_pattern_activations
        }
        allowed_refs = {
            *(item.ref_id for item in request.persona_context_refs),
            *(item.ref_id for item in request.relationship_context_refs),
        }
        for finding in decision.findings:
            if (
                finding.reply_end > len(request.proposed_reply)
                or request.proposed_reply[
                    finding.reply_start : finding.reply_end
                ]
                != finding.reply_quote
            ):
                raise ValueError(
                    "continuity finding reply span does not match proposed_reply"
                )
            unknown_refs = {
                *finding.supporting_basis_refs,
                *finding.conflicting_source_refs,
            }.difference(allowed_refs)
            if unknown_refs:
                raise ValueError(
                    "continuity finding cites context not supplied by the kernel: "
                    + ", ".join(sorted(unknown_refs))
                )
            unknown_activation_refs = set(
                finding.voice_activation_refs
            ).difference(activation_ids)
            if unknown_activation_refs:
                raise ValueError(
                    "continuity finding cites an unavailable voice activation: "
                    + ", ".join(sorted(unknown_activation_refs))
                )

    @classmethod
    def _project_voice_activation_traces(
        cls,
        request: ContinuityEvaluationRequest,
        decision: ContinuityEvaluationDecision,
    ) -> Tuple[VoiceActivationTrace, ...]:
        cited_ids = {
            reference
            for finding in decision.findings
            for reference in finding.voice_activation_refs
        }
        if not cited_ids:
            return ()
        activations = {
            item.activation_id: item for item in request.voice_pattern_activations
        }
        evidence_refs = {
            item.ref_id: item
            for item in (
                *request.persona_context_refs,
                *request.relationship_context_refs,
            )
        }
        allowed_evidence_ids = set(evidence_refs)
        traces = []
        for activation_id in sorted(cited_ids):
            activation = activations[activation_id]
            pattern_refs = tuple(
                item
                for item in evidence_refs.values()
                if cls._is_activation_pattern_ref(item, activation)
            )
            if len(pattern_refs) != 1:
                raise ValueError(
                    "a cited voice activation requires exactly one matching "
                    "contextual voice pattern reference"
                )
            pattern_ref = pattern_refs[0]
            matches = []
            for condition_id, condition_type, matched_value, signal in zip(
                activation.condition_ids,
                activation._condition_types,
                activation._matched_values,
                activation._supporting_signals,
            ):
                matches.append(
                    VoiceConditionMatchTrace(
                        condition_id=condition_id,
                        signal_source=signal.source,
                        signal_id=signal.signal_id,
                        signal_type=condition_type,
                        matched_value=matched_value,
                        producer_version=(
                            signal.producer_version
                            or "host-observation-admission/v1"
                        ),
                        evidence_ref_ids=tuple(
                            sorted(
                                set(signal.evidence_refs).intersection(
                                    allowed_evidence_ids
                                )
                            )
                        ),
                        source_context=cls._voice_signal_trace_context(signal),
                    )
                )
            traces.append(
                VoiceActivationTrace.create(
                    activation_id=activation.activation_id,
                    relationship_id=activation.relationship_id,
                    turn_id=activation.source_turn_id,
                    persona_id=activation.persona_id,
                    manifest_id=activation.manifest_id,
                    context_baseline_fingerprint=(
                        activation.context_baseline_fingerprint
                    ),
                    pattern_ref_id=pattern_ref.ref_id,
                    pattern_scope=activation.pattern_scope,
                    matcher_version=activation.matcher_version,
                    matcher_input_fingerprint=activation.input_fingerprint,
                    condition_matches=tuple(matches),
                )
            )
        return tuple(traces)

    @staticmethod
    def _is_activation_pattern_ref(
        reference: ContinuityEvidenceRef,
        activation: VoicePatternActivation,
    ) -> bool:
        return (
            reference.kind
            == ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN
            and reference.locator.get("manifest_id") == activation.manifest_id
            and reference.locator.get("content_fingerprint")
            == activation.manifest_fingerprint
            and reference.locator.get("pattern_id") == activation.pattern_id
        )

    @staticmethod
    def _voice_signal_trace_context(
        signal: InteractionContextSignal,
    ) -> Mapping[str, object]:
        if signal.source == ContextSignalSource.HOST_OBSERVED:
            return {
                "kind": ContextSignalSource.HOST_OBSERVED.value,
                "observation_fingerprint": _canonical_hash(signal.to_dict()),
            }
        context = getattr(signal, "_trace_context", None)
        if not isinstance(context, Mapping):
            raise ValueError(
                "derived voice signal lacks a non-portable producer trace context"
            )
        return context


__all__ = [
    "ContinuityAggregationPolicyV1",
    "ContinuityEvaluationCapabilityError",
    "ContinuityEvaluationCoordinator",
    "InteractionContextEvaluationCoordinator",
    "RelationshipSafetySignalProjector",
    "VoicePatternMatcher",
]
