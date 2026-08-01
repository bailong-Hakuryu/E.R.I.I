import hashlib
import json
import unittest

from erii.core.continuity import (
    ContinuityEvaluationCoordinator,
    InteractionContextEvaluationCoordinator,
    VoicePatternMatcher,
)
from erii.models.continuity import (
    ContinuityAxis,
    ContinuityEvaluationRequest,
    ContinuityEvaluatorDescriptor,
    ContinuityFindingAssessment,
    ContinuityFindingSeverity,
    ContinuityReasonCode,
    InteractionContextEvaluationRequest,
    InteractionContextEvaluatorDescriptor,
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
    PersonaManifestCandidate,
    VoicePatternCondition,
    VoicePatternConditionType,
)
from erii.models.relationship import (
    BaselineLevel,
    PremiseExperience,
    RelationshipPremise,
    RelationshipPremiseMode,
)
from erii.models.turn import (
    ContextSignalSource,
    ContinuityVerdict,
    InteractionContextSignal,
)


_BASELINE_FINGERPRINT = "2" * 64
_PERSONA_EVIDENCE_REF = ContinuityEvidenceRef.create(
    ContinuityEvidenceKind.PERSONA_CLAIM,
    {
        "manifest_id": "manifest-1",
        "content_fingerprint": "1" * 64,
        "claim_id": "claim-1",
    },
)
_RELATIONSHIP_EVIDENCE_REF = ContinuityEvidenceRef.create(
    ContinuityEvidenceKind.RELATIONSHIP_EVENT,
    {"relationship_id": "relationship-1", "event_id": "event-other"},
)


def _manifest_candidate():
    baseline = {
        "familiarity": BaselineLevel.MODERATE,
        "trust": BaselineLevel.MODERATE,
        "intimacy": BaselineLevel.LOW,
        "safety": BaselineLevel.HIGH,
        "conflict_tension": BaselineLevel.LOW,
    }
    return PersonaManifestCandidate.model_validate(
        {
            "schema_version": "0.4.0a7",
            "compiler_version": "test-compiler-v1",
            "source_spans": [
                {
                    "span_id": "span-playful",
                    "start": 0,
                    "end": 11,
                    "quote": "Playful line",
                },
                {
                    "span_id": "span-canonical",
                    "start": 12,
                    "end": 26,
                    "quote": "Canonical line",
                },
            ],
            "claims": [
                {
                    "claim_id": "voice-playful",
                    "kind": "voice",
                    "statement": "She can become playfully blunt when excited.",
                    "activation_tier": "situational",
                    "basis": "explicit",
                    "scope": "character",
                    "source_span_ids": ["span-playful"],
                },
                {
                    "claim_id": "voice-canonical",
                    "kind": "voice",
                    "statement": "This register belongs to one canonical relationship.",
                    "activation_tier": "situational",
                    "basis": "explicit",
                    "scope": "canonical_relationship",
                    "source_span_ids": ["span-canonical"],
                },
            ],
            "formative_experiences": [
                {
                    "experience_id": "experience-canonical",
                    "title": "Canonical shared history",
                    "summary": "A relationship-specific formative experience.",
                    "activation_tier": "situational",
                    "scope": "canonical_relationship",
                    "source_span_ids": ["span-canonical"],
                }
            ],
            "premise_templates": [
                {
                    "premise_template_id": "premise-sakura",
                    "counterpart_role": "Sakura",
                    "display_name": "Canonical continuation",
                    "premise_experience_ids": ["experience-canonical"],
                    "qualitative_baseline": baseline,
                    "source_span_ids": ["span-canonical"],
                }
            ],
            "contextual_voice_patterns": [
                {
                    "pattern_id": "pattern-playful",
                    "description": "A concise, playfully blunt register.",
                    "scope": "character",
                    "basis": "explicit",
                    "source_span_ids": ["span-playful"],
                    "conditions": [
                        {
                            "condition_id": "condition-excited",
                            "condition_type": "emotion",
                            "values": ["excited"],
                        }
                    ],
                    "required_claim_ids": ["voice-playful"],
                },
                {
                    "pattern_id": "pattern-canonical",
                    "description": "A register reserved for the selected canonical bond.",
                    "scope": "canonical_relationship",
                    "basis": "explicit",
                    "source_span_ids": ["span-canonical"],
                    "conditions": [
                        {
                            "condition_id": "condition-handwriting",
                            "condition_type": "communication_modality",
                            "values": ["handwriting"],
                        }
                    ],
                    "required_claim_ids": ["voice-canonical"],
                    "required_experience_ids": ["experience-canonical"],
                    "canonical_premise_template_ids": ["premise-sakura"],
                },
            ],
        }
    )


def _approved_manifest(candidate):
    source_sha256 = "a" * 64
    content = {
        "blueprint_id": "blueprint-1",
        "blueprint_revision": 1,
        "source_sha256": source_sha256,
        "candidate": candidate.model_dump(mode="json"),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return PersonaManifest(
        manifest_id="manifest-1",
        blueprint_id="blueprint-1",
        blueprint_revision=1,
        source_sha256=source_sha256,
        candidate=candidate,
        content_fingerprint=fingerprint,
        approved_proposal_id="proposal-1",
        approved_revision=1,
        approved_by="owner",
        approved_at="2026-07-29T00:00:00+00:00",
    )


def _voice_pattern_evidence_ref(manifest, pattern_id):
    return ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN,
        {
            "manifest_id": manifest.manifest_id,
            "content_fingerprint": manifest.content_fingerprint,
            "pattern_id": pattern_id,
        },
    )


def _canonical_premise():
    return RelationshipPremise(
        premise_id="premise-sakura",
        mode=RelationshipPremiseMode.CANONICAL_CONTINUATION,
        canonical_role="Sakura",
        experiences=(
            PremiseExperience(
                experience_id="experience-canonical",
                summary="A relationship-specific formative experience.",
                source_spans=({"start": 12, "end": 26},),
            ),
        ),
        baseline_levels={
            "familiarity": BaselineLevel.MODERATE,
            "trust": BaselineLevel.MODERATE,
            "intimacy": BaselineLevel.LOW,
            "safety": BaselineLevel.HIGH,
            "conflict_tension": BaselineLevel.LOW,
        },
    )


def _finding(axis, *, assessment="aligned", reason_code="aligned", severity="info"):
    return {
        "finding_id": f"finding-{axis}",
        "axis": axis,
        "assessment": assessment,
        "severity": severity,
        "reason_code": reason_code,
        "reply_start": 0,
        "reply_end": 5,
        "reply_quote": "Hello",
        "supporting_basis_refs": [_PERSONA_EVIDENCE_REF.ref_id],
        "conflicting_source_refs": (
            [_RELATIONSHIP_EVIDENCE_REF.ref_id]
            if assessment in {"review", "unsupported"}
            else []
        ),
        "voice_activation_refs": [],
    }


def _inferred_emotion_signal(relationship_id, turn_id, value="excited"):
    class Evaluator:
        descriptor = InteractionContextEvaluatorDescriptor(
            evaluator_id="tests.context-evaluator",
            evaluator_version="1",
        )

        def evaluate(self, request):
            return {
                "kind": "signals",
                "signals": [
                    {
                        "candidate_key": "current-emotion",
                        "value": value,
                        "evidence_refs": [
                            request.user_message_evidence_ref
                        ],
                    }
                ],
            }

    request = InteractionContextEvaluationRequest(
        turn_id=turn_id,
        relationship_id=relationship_id,
        persona_id=f"persona-{relationship_id}",
        persona_manifest_id="manifest-1",
        user_message_id=f"{turn_id}:user",
        user_message="Let's go outside!",
        emotion_values=(value,),
        relationship_state={
            "familiarity": 0.5,
            "trust": 0.5,
            "intimacy": 0.25,
            "safety": 0.75,
            "conflict_tension": 0.1,
        },
    )
    return InteractionContextEvaluationCoordinator.evaluate(
        request,
        Evaluator(),
    )[0]


class ContextualVoicePatternTests(unittest.TestCase):
    def test_manifest_patterns_are_source_backed_and_dependency_checked(self):
        candidate = _manifest_candidate()
        self.assertEqual(len(candidate.contextual_voice_patterns), 2)
        self.assertIsInstance(
            candidate.contextual_voice_patterns[0],
            ContextualVoicePatternCandidate,
        )
        self.assertIsInstance(
            candidate.contextual_voice_patterns[0].conditions[0],
            VoicePatternCondition,
        )

        invalid = candidate.model_dump(mode="json")
        invalid["contextual_voice_patterns"][0]["required_claim_ids"] = ["missing"]
        with self.assertRaises(ValueError):
            PersonaManifestCandidate.model_validate(invalid)

        invalid_source = candidate.model_dump(mode="json")
        invalid_source["contextual_voice_patterns"][0]["conditions"][0][
            "condition_type"
        ] = VoicePatternConditionType.ACTIVITY.value
        invalid_source["contextual_voice_patterns"][0]["conditions"][0][
            "signal_source"
        ] = ContextSignalSource.EVALUATOR_INFERRED.value
        with self.assertRaises(ValueError):
            PersonaManifestCandidate.model_validate(invalid_source)

        invalid_safety = candidate.model_dump(mode="json")
        invalid_safety["contextual_voice_patterns"][0]["conditions"][0] = {
            "condition_id": "condition-safety",
            "condition_type": VoicePatternConditionType.RELATIONSHIP_SAFETY.value,
            "values": ["safe"],
        }
        with self.assertRaisesRegex(ValueError, "low, moderate, or high"):
            PersonaManifestCandidate.model_validate(invalid_safety)

    def test_matcher_uses_signal_authority_and_keeps_canonical_scope_local(self):
        manifest = _approved_manifest(_manifest_candidate())
        fresh_signals = (
            _inferred_emotion_signal(
                "relationship-fresh",
                "turn-fresh",
            ),
            InteractionContextSignal(
                signal_id="signal-modality",
                source=ContextSignalSource.HOST_OBSERVED,
                signal_type="communication_modality",
                value="handwriting",
            ),
        )

        fresh = VoicePatternMatcher.match(
            manifest=manifest,
            relationship_id="relationship-fresh",
            source_turn_id="turn-fresh",
            persona_id="persona-fresh",
            premise=RelationshipPremise(),
            signals=fresh_signals,
            context_baseline_fingerprint=_BASELINE_FINGERPRINT,
        )
        self.assertEqual(
            [activation.pattern_id for activation in fresh],
            ["pattern-playful"],
        )

        canonical = _canonical_premise()
        canonical_signals = (
            _inferred_emotion_signal(
                "relationship-canonical",
                "turn-canonical",
            ),
            fresh_signals[1],
        )
        active = VoicePatternMatcher.match(
            manifest=manifest,
            relationship_id="relationship-canonical",
            source_turn_id="turn-canonical",
            persona_id="persona-canonical",
            premise=canonical,
            signals=canonical_signals,
            context_baseline_fingerprint=_BASELINE_FINGERPRINT,
        )
        repeated = VoicePatternMatcher.match(
            manifest=manifest,
            relationship_id="relationship-canonical",
            source_turn_id="turn-canonical",
            persona_id="persona-canonical",
            premise=canonical,
            signals=tuple(reversed(canonical_signals)),
            context_baseline_fingerprint=_BASELINE_FINGERPRINT,
        )
        self.assertEqual(active, repeated)
        self.assertEqual(
            [activation.pattern_id for activation in active],
            ["pattern-canonical", "pattern-playful"],
        )
        self.assertTrue(
            all(item.relationship_id == "relationship-canonical" for item in active)
        )

        self_reported = (
            InteractionContextSignal(
                signal_id="bad-emotion-source",
                source=ContextSignalSource.HOST_OBSERVED,
                signal_type="emotion",
                value="excited",
            ),
        )
        self.assertEqual(
            VoicePatternMatcher.match(
                manifest=manifest,
                relationship_id="relationship-fresh",
                source_turn_id="turn-fresh",
                persona_id="persona-fresh",
                premise=RelationshipPremise(),
                signals=self_reported,
                context_baseline_fingerprint=_BASELINE_FINGERPRINT,
            ),
            (),
        )

        unsupported_inference = (
            InteractionContextSignal(
                signal_id="emotion-without-evidence",
                source=ContextSignalSource.EVALUATOR_INFERRED,
                signal_type="emotion",
                value="excited",
            ),
        )
        self.assertEqual(
            VoicePatternMatcher.match(
                manifest=manifest,
                relationship_id="relationship-fresh",
                source_turn_id="turn-fresh",
                persona_id="persona-fresh",
                premise=RelationshipPremise(),
                signals=unsupported_inference,
                context_baseline_fingerprint=_BASELINE_FINGERPRINT,
            ),
            (),
        )

        forged_scoped_inference = (
            InteractionContextSignal(
                signal_id="forged-scoped-emotion",
                source=ContextSignalSource.EVALUATOR_INFERRED,
                signal_type="emotion",
                value="excited",
                evidence_refs=("turn-message:turn-fresh:user",),
                relationship_id="relationship-fresh",
                source_turn_id="turn-fresh",
                producer_version="forged/1",
            ),
        )
        self.assertEqual(
            VoicePatternMatcher.match(
                manifest=manifest,
                relationship_id="relationship-fresh",
                source_turn_id="turn-fresh",
                persona_id="persona-fresh",
                premise=RelationshipPremise(),
                signals=forged_scoped_inference,
                context_baseline_fingerprint=_BASELINE_FINGERPRINT,
            ),
            (),
        )

        with self.assertRaisesRegex(ValueError, "another relationship or Turn"):
            VoicePatternMatcher.match(
                manifest=manifest,
                relationship_id="relationship-other",
                source_turn_id="turn-other",
                persona_id="persona-other",
                premise=RelationshipPremise(),
                signals=fresh_signals,
                context_baseline_fingerprint=_BASELINE_FINGERPRINT,
            )

    def test_interaction_context_evaluator_is_strict_and_current_turn_bounded(self):
        class Evaluator:
            descriptor = InteractionContextEvaluatorDescriptor(
                evaluator_id="tests.context-evaluator",
                evaluator_version="1",
            )

            def __init__(self, decision):
                self.decision = decision

            def evaluate(self, request):
                return self.decision

        request = InteractionContextEvaluationRequest(
            turn_id="turn-1",
            relationship_id="relationship-1",
            persona_id="persona-1",
            persona_manifest_id="manifest-1",
            user_message_id="turn-1:user",
            user_message="Let's go outside!",
            emotion_values=("excited", "calm"),
            relationship_state={
                "familiarity": 0.5,
                "trust": 0.5,
                "intimacy": 0.25,
                "safety": 0.75,
                "conflict_tension": 0.1,
            },
        )
        evaluator = Evaluator(
            {
                "kind": "signals",
                "signals": [
                    {
                        "candidate_key": "current-excitement",
                        "value": "excited",
                        "evidence_refs": [request.user_message_evidence_ref],
                    }
                ],
            }
        )
        signals = InteractionContextEvaluationCoordinator.evaluate(
            request,
            evaluator,
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].relationship_id, "relationship-1")
        self.assertEqual(signals[0].source_turn_id, "turn-1")
        self.assertEqual(
            signals[0].producer_version,
            evaluator.descriptor.public_version,
        )

        evaluator.decision = {
            "kind": "signals",
            "signals": [
                {
                    "candidate_key": "borrowed",
                    "value": "excited",
                    "evidence_refs": ["relationship-event:someone-elses-event"],
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "current relationship/Turn"):
            InteractionContextEvaluationCoordinator.evaluate(request, evaluator)

        no_signals = interaction_context_evaluation_decision_from_value(
            {
                "kind": "no_signals",
                "reason_code": "insufficient_evidence",
            }
        )
        self.assertEqual(no_signals.kind, "no_signals")
        with self.assertRaises(ValueError):
            interaction_context_evaluation_decision_from_value(
                {
                    "kind": "no_signals",
                    "reason_code": "insufficient_evidence",
                    "explanation": "free-form output is not part of the contract",
                }
            )


class ContinuityEvaluationTests(unittest.TestCase):
    def _decision(self, replacement=None):
        values = {
            axis.value: _finding(axis.value)
            for axis in ContinuityAxis
        }
        if replacement is not None:
            values[replacement["axis"]] = replacement
        return continuity_evaluation_decision_from_value(
            {"kind": "findings", "findings": list(values.values())}
        )

    def _evaluate(
        self,
        decision,
        *,
        voice_pattern_activations=(),
        persona_context_refs=(_PERSONA_EVIDENCE_REF,),
    ):
        class Evaluator:
            descriptor = ContinuityEvaluatorDescriptor(
                evaluator_id="continuity-evaluator",
                evaluator_version="1.0.0",
                evaluation_schema_version="1",
            )

            def evaluate(self, request):
                return decision

        return ContinuityEvaluationCoordinator.evaluate(
            ContinuityEvaluationRequest(
                turn_id="turn-1",
                relationship_id="relationship-1",
                persona_id="persona-1",
                user_message="Hi",
                proposed_reply="Hello",
                persona_manifest_id="manifest-1",
                context_baseline_fingerprint=_BASELINE_FINGERPRINT,
                persona_context_refs=persona_context_refs,
                relationship_context_refs=(_RELATIONSHIP_EVIDENCE_REF,),
                voice_pattern_activations=voice_pattern_activations,
            ),
            Evaluator(),
        )

    def test_voice_only_deviation_is_an_advisory_not_persona_drift(self):
        result = self._evaluate(
            self._decision(
                _finding(
                    ContinuityAxis.VOICE_STYLE.value,
                    assessment=ContinuityFindingAssessment.UNSUPPORTED.value,
                    reason_code=ContinuityReasonCode.VOICE_STYLE_DEVIATION.value,
                    severity=ContinuityFindingSeverity.ADVISORY.value,
                )
            )
        )
        self.assertEqual(result.assessment.verdict, ContinuityVerdict.ALIGNED)
        self.assertTrue(result.style_revision_advised)

    def test_hard_scope_conflict_wins_deterministically(self):
        result = self._evaluate(
            self._decision(
                _finding(
                    ContinuityAxis.RELATIONSHIP_SCOPE.value,
                    assessment=ContinuityFindingAssessment.UNSUPPORTED.value,
                    reason_code=ContinuityReasonCode.RELATIONSHIP_CROSSOVER.value,
                    severity=ContinuityFindingSeverity.CRITICAL.value,
                )
            )
        )
        self.assertEqual(
            result.assessment.verdict,
            ContinuityVerdict.UNSUPPORTED_DRIFT,
        )

    def test_supported_choice_and_review_are_distinct(self):
        supported = self._evaluate(
            self._decision(
                _finding(
                    ContinuityAxis.PSYCHOLOGICAL_CAUSALITY.value,
                    assessment=ContinuityFindingAssessment.SUPPORTED.value,
                    reason_code=ContinuityReasonCode.SUPPORTED_NEW_CHOICE.value,
                    severity=ContinuityFindingSeverity.INFO.value,
                )
            )
        )
        self.assertEqual(
            supported.assessment.verdict,
            ContinuityVerdict.SUPPORTED_NEW_CHOICE,
        )

        review = self._evaluate(
            self._decision(
                _finding(
                    ContinuityAxis.IDENTITY_VALUES.value,
                    assessment=ContinuityFindingAssessment.REVIEW.value,
                    reason_code=ContinuityReasonCode.VALUE_TENSION.value,
                    severity=ContinuityFindingSeverity.WARNING.value,
                )
            )
        )
        self.assertEqual(
            review.assessment.verdict,
            ContinuityVerdict.REVIEW_REQUIRED,
        )

    def test_contextual_voice_support_requires_this_turns_activation(self):
        manifest = _approved_manifest(_manifest_candidate())
        activations = VoicePatternMatcher.match(
            manifest=manifest,
            relationship_id="relationship-1",
            source_turn_id="turn-1",
            persona_id="persona-1",
            premise=RelationshipPremise(),
            signals=(
                _inferred_emotion_signal("relationship-1", "turn-1"),
            ),
            context_baseline_fingerprint=_BASELINE_FINGERPRINT,
        )
        self.assertEqual(len(activations), 1)
        pattern_ref = _voice_pattern_evidence_ref(
            manifest,
            activations[0].pattern_id,
        )
        voice_finding = _finding(
            ContinuityAxis.VOICE_STYLE.value,
            assessment=ContinuityFindingAssessment.SUPPORTED.value,
            reason_code=ContinuityReasonCode.SUPPORTED_CONTEXTUAL_VOICE.value,
            severity=ContinuityFindingSeverity.INFO.value,
        )
        voice_finding["supporting_basis_refs"] = [pattern_ref.ref_id]
        voice_finding["voice_activation_refs"] = [activations[0].activation_id]
        result = self._evaluate(
            self._decision(voice_finding),
            voice_pattern_activations=activations,
            persona_context_refs=(_PERSONA_EVIDENCE_REF, pattern_ref),
        )
        self.assertEqual(result.assessment.verdict, ContinuityVerdict.ALIGNED)
        self.assertFalse(result.style_revision_advised)
        self.assertEqual(len(result.voice_activation_traces), 1)
        trace = result.voice_activation_traces[0]
        self.assertEqual(trace.activation_id, activations[0].activation_id)
        self.assertEqual(trace.pattern_ref_id, pattern_ref.ref_id)
        self.assertEqual(
            trace.context_baseline_fingerprint,
            _BASELINE_FINGERPRINT,
        )
        self.assertEqual(trace.condition_matches[0].matched_value, "excited")

        with self.assertRaisesRegex(ValueError, "unavailable voice activation"):
            self._evaluate(
                self._decision(voice_finding),
                persona_context_refs=(_PERSONA_EVIDENCE_REF, pattern_ref),
            )

    def test_unreferenced_voice_activation_is_not_persisted_as_a_trace(self):
        manifest = _approved_manifest(_manifest_candidate())
        activations = VoicePatternMatcher.match(
            manifest=manifest,
            relationship_id="relationship-1",
            source_turn_id="turn-1",
            persona_id="persona-1",
            premise=_canonical_premise(),
            signals=(
                _inferred_emotion_signal("relationship-1", "turn-1"),
                InteractionContextSignal(
                    signal_id="signal-modality",
                    source=ContextSignalSource.HOST_OBSERVED,
                    signal_type="communication_modality",
                    value="handwriting",
                ),
            ),
            context_baseline_fingerprint=_BASELINE_FINGERPRINT,
        )
        self.assertEqual(len(activations), 2)
        cited = next(
            item for item in activations if item.pattern_id == "pattern-playful"
        )
        pattern_ref = _voice_pattern_evidence_ref(manifest, cited.pattern_id)
        voice_finding = _finding(
            ContinuityAxis.VOICE_STYLE.value,
            assessment=ContinuityFindingAssessment.SUPPORTED.value,
            reason_code=ContinuityReasonCode.SUPPORTED_CONTEXTUAL_VOICE.value,
            severity=ContinuityFindingSeverity.INFO.value,
        )
        voice_finding["supporting_basis_refs"] = [pattern_ref.ref_id]
        voice_finding["voice_activation_refs"] = [cited.activation_id]

        result = self._evaluate(
            self._decision(voice_finding),
            voice_pattern_activations=activations,
            persona_context_refs=(_PERSONA_EVIDENCE_REF, pattern_ref),
        )

        self.assertEqual(
            tuple(item.activation_id for item in result.voice_activation_traces),
            (cited.activation_id,),
        )
        self.assertEqual(
            result.review_binding.voice_pattern_activation_ids,
            (cited.activation_id,),
        )

    def test_voice_activation_reference_requires_voice_axis_and_reason(self):
        cases = (
            (
                "wrong axis",
                "reason_code is incompatible with the finding axis",
                _finding(
                    ContinuityAxis.IDENTITY_VALUES.value,
                    assessment=ContinuityFindingAssessment.SUPPORTED.value,
                    reason_code=ContinuityReasonCode.SUPPORTED_CONTEXTUAL_VOICE.value,
                ),
            ),
            (
                "wrong reason",
                "voice activation references are valid only",
                _finding(ContinuityAxis.VOICE_STYLE.value),
            ),
        )
        for name, expected_error, finding in cases:
            with self.subTest(case=name):
                finding["voice_activation_refs"] = ["activation-1"]
                with self.assertRaisesRegex(ValueError, expected_error):
                    continuity_evaluation_decision_from_value(
                        {"kind": "findings", "findings": [finding]}
                    )

    def test_cited_activation_requires_matching_pattern_evidence(self):
        manifest = _approved_manifest(_manifest_candidate())
        activations = VoicePatternMatcher.match(
            manifest=manifest,
            relationship_id="relationship-1",
            source_turn_id="turn-1",
            persona_id="persona-1",
            premise=RelationshipPremise(),
            signals=(
                _inferred_emotion_signal("relationship-1", "turn-1"),
            ),
            context_baseline_fingerprint=_BASELINE_FINGERPRINT,
        )
        voice_finding = _finding(
            ContinuityAxis.VOICE_STYLE.value,
            assessment=ContinuityFindingAssessment.SUPPORTED.value,
            reason_code=ContinuityReasonCode.SUPPORTED_CONTEXTUAL_VOICE.value,
            severity=ContinuityFindingSeverity.INFO.value,
        )
        voice_finding["voice_activation_refs"] = [activations[0].activation_id]

        with self.assertRaisesRegex(
            ValueError,
            "exactly one matching contextual voice pattern reference",
        ):
            self._evaluate(
                self._decision(voice_finding),
                voice_pattern_activations=activations,
            )

    def test_evaluator_cannot_supply_an_aggregate_verdict(self):
        value = {
            "kind": "findings",
            "findings": [
                _finding(axis.value)
                for axis in ContinuityAxis
            ],
            "verdict": "aligned",
        }
        with self.assertRaises(ValueError):
            continuity_evaluation_decision_from_value(value)


if __name__ == "__main__":
    unittest.main()
