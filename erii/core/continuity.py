"""Deterministic continuity aggregation and temporary voice activation."""

import hashlib
import json
from typing import Dict, Mapping, Optional, Sequence, Tuple
import uuid

from erii.models.continuity import (
    ContinuityAxis,
    ContinuityEvaluationDecision,
    ContinuityEvaluationRequest,
    ContinuityEvaluationResult,
    ContinuityEvaluatorDescriptor,
    ContinuityEvaluatorV1,
    ContinuityFindingAssessment,
    ContinuityReasonCode,
    InteractionContextEvaluationRequest,
    InteractionContextEvaluatorDescriptor,
    InteractionContextEvaluatorV1,
    InteractionContextNoSignalsDecision,
    VoicePatternActivation,
    continuity_evaluation_decision_from_value,
    interaction_context_evaluation_decision_from_value,
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
from erii.models.turn import (
    ContextSignalSource,
    ContinuityAssessmentStatus,
    ContinuityVerdict,
    InteractionContextSignal,
    ReplyContinuityAssessment,
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
        return _attest_context_signal(
            InteractionContextSignal(
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
        )


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
            signals.append(
                _attest_context_signal(
                    InteractionContextSignal(
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
                )
            )
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
    ) -> Tuple[VoicePatternActivation, ...]:
        if not isinstance(manifest, PersonaManifest):
            raise TypeError("manifest must be one approved PersonaManifest")
        if not isinstance(premise, RelationshipPremise):
            premise = RelationshipPremise.from_dict(premise)
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
            activations.append(
                VoicePatternActivation(
                    activation_id=activation_id,
                    relationship_id=clean_relationship,
                    source_turn_id=clean_turn,
                    persona_id=clean_persona,
                    manifest_id=manifest.manifest_id,
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

    VERSION = "continuity-aggregation-v1"
    _HARD_CONFLICTS = frozenset(
        {
            ContinuityReasonCode.RELATIONSHIP_CROSSOVER,
            ContinuityReasonCode.INHERITED_INTIMACY,
            ContinuityReasonCode.UNAVAILABLE_KNOWLEDGE,
        }
    )

    @classmethod
    def aggregate(cls, decision: ContinuityEvaluationDecision) -> ContinuityVerdict:
        non_voice = tuple(
            finding
            for finding in decision.findings
            if finding.axis != ContinuityAxis.VOICE_STYLE
        )
        if any(
            finding.reason_code in cls._HARD_CONFLICTS
            for finding in non_voice
        ):
            return ContinuityVerdict.UNSUPPORTED_DRIFT
        if any(
            finding.assessment == ContinuityFindingAssessment.UNSUPPORTED
            for finding in non_voice
        ):
            return ContinuityVerdict.UNSUPPORTED_DRIFT
        if any(
            finding.assessment == ContinuityFindingAssessment.REVIEW
            for finding in non_voice
        ):
            return ContinuityVerdict.REVIEW_REQUIRED
        if any(
            finding.assessment == ContinuityFindingAssessment.SUPPORTED
            for finding in non_voice
        ):
            return ContinuityVerdict.SUPPORTED_NEW_CHOICE
        return ContinuityVerdict.ALIGNED


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
        verdict = ContinuityAggregationPolicyV1.aggregate(decision)
        voice_finding = next(
            item
            for item in decision.findings
            if item.axis == ContinuityAxis.VOICE_STYLE
        )
        style_revision_advised = (
            voice_finding.reason_code
            == ContinuityReasonCode.VOICE_STYLE_DEVIATION
            or voice_finding.assessment
            in {
                ContinuityFindingAssessment.REVIEW,
                ContinuityFindingAssessment.UNSUPPORTED,
            }
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
            style_revision_advised=style_revision_advised,
            voice_pattern_activations=request.voice_pattern_activations,
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
            *request.persona_context_refs,
            *request.relationship_context_refs,
            *activation_ids,
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
            if (
                finding.reason_code
                == ContinuityReasonCode.SUPPORTED_CONTEXTUAL_VOICE
                and not activation_ids.intersection(
                    finding.supporting_basis_refs
                )
            ):
                raise ValueError(
                    "supported contextual voice requires a matched activation reference"
                )


__all__ = [
    "ContinuityAggregationPolicyV1",
    "ContinuityEvaluationCapabilityError",
    "ContinuityEvaluationCoordinator",
    "InteractionContextEvaluationCoordinator",
    "RelationshipSafetySignalProjector",
    "VoicePatternMatcher",
]
