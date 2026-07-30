"""Engine-level pre-delivery continuity contracts."""

import tempfile
import unittest

from erii import ERIIEngine
from erii.models.continuity import (
    ContinuityAxis,
    ContinuityEvaluatorDescriptor,
    InteractionContextEvaluatorDescriptor,
)
from erii.models.turn import ContinuityVerdict, TurnStatus


class _ContinuityEvaluator:
    descriptor = ContinuityEvaluatorDescriptor(
        evaluator_id="tests.continuity-evaluator",
        evaluator_version="1",
        evaluation_schema_version="1",
    )

    def __init__(self):
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        findings = []
        for axis in ContinuityAxis:
            supporting = ["persona:voice-playful"]
            reason_code = "aligned"
            if axis == ContinuityAxis.VOICE_STYLE:
                reason_code = "supported_contextual_voice"
                supporting = [
                    request.voice_pattern_activations[0].activation_id
                ]
            findings.append(
                {
                    "finding_id": f"finding-{axis.value}",
                    "axis": axis.value,
                    "assessment": (
                        "supported"
                        if axis == ContinuityAxis.VOICE_STYLE
                        else "aligned"
                    ),
                    "severity": "info",
                    "reason_code": reason_code,
                    "reply_start": 0,
                    "reply_end": 5,
                    "reply_quote": "Hello",
                    "supporting_basis_refs": supporting,
                    "conflicting_source_refs": [],
                }
            )
        return {"kind": "findings", "findings": findings}


class _InteractionContextEvaluator:
    descriptor = InteractionContextEvaluatorDescriptor(
        evaluator_id="tests.interaction-context-evaluator",
        evaluator_version="1",
    )

    def __init__(self, *, borrowed_evidence=False):
        self.borrowed_evidence = borrowed_evidence
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        evidence_ref = (
            "relationship-event:another-relationships-event"
            if self.borrowed_evidence
            else request.user_message_evidence_ref
        )
        return {
            "kind": "signals",
            "signals": [
                {
                    "candidate_key": "current-excitement",
                    "value": "excited",
                    "evidence_refs": [evidence_ref],
                }
            ],
        }


def _candidate():
    return {
        "schema_version": "0.4.0a7",
        "compiler_version": "tests.persona-compiler/1",
        "source_spans": [
            {
                "span_id": "span-playful",
                "start": 0,
                "end": 12,
                "quote": "Playful line",
            }
        ],
        "claims": [
            {
                "claim_id": "voice-playful",
                "kind": "voice",
                "statement": "She can sound playfully blunt while gaming.",
                "activation_tier": "situational",
                "basis": "explicit",
                "scope": "character",
                "source_span_ids": ["span-playful"],
            }
        ],
        "contextual_voice_patterns": [
            {
                "pattern_id": "playful-gaming",
                "description": "A concise and playfully blunt gaming register.",
                "scope": "character",
                "basis": "explicit",
                "source_span_ids": ["span-playful"],
                "conditions": [
                    {
                        "condition_id": "while-gaming",
                        "condition_type": "activity",
                        "values": ["gaming"],
                    }
                ],
                "required_claim_ids": ["voice-playful"],
            }
        ],
    }


def _derived_context_candidate():
    candidate = _candidate()
    candidate["contextual_voice_patterns"] = [
        {
            "pattern_id": "emotion-playful",
            "description": "A concise and playfully excited register.",
            "scope": "character",
            "basis": "explicit",
            "source_span_ids": ["span-playful"],
            "conditions": [
                {
                    "condition_id": "when-excited",
                    "condition_type": "emotion",
                    "values": ["excited"],
                }
            ],
            "required_claim_ids": ["voice-playful"],
        },
        {
            "pattern_id": "safety-playful",
            "description": "A relaxed register in a moderately safe relationship.",
            "scope": "character",
            "basis": "explicit",
            "source_span_ids": ["span-playful"],
            "conditions": [
                {
                    "condition_id": "when-moderately-safe",
                    "condition_type": "relationship_safety",
                    "values": ["moderate"],
                }
            ],
            "required_claim_ids": ["voice-playful"],
        },
    ]
    return candidate


class ContinuityEnginePublicTests(unittest.TestCase):
    def test_evaluation_is_pre_delivery_and_does_not_persist_the_draft(self):
        with tempfile.TemporaryDirectory() as root:
            evaluator = _ContinuityEvaluator()
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=evaluator,
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-game",
                    interaction_context=(
                        {
                            "signal_id": "activity-game",
                            "source": "host_observed",
                            "signal_type": "activity",
                            "value": "gaming",
                        },
                    ),
                )

                result = engine.evaluate_reply_continuity(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    "Hello there.",
                    persona_context_refs=("persona:voice-playful",),
                )

                self.assertEqual(
                    result.assessment.verdict,
                    ContinuityVerdict.ALIGNED,
                )
                self.assertEqual(len(result.voice_pattern_activations), 1)
                self.assertEqual(len(evaluator.requests), 1)
                still_open = engine.get_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )
                self.assertEqual(still_open.status, TurnStatus.OPEN)
                self.assertIsNone(still_open.transcript.agent_message)

                engine.complete_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    "Hello there.",
                    continuity_assessment=result.assessment,
                )
                completed = engine.get_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                )
                self.assertEqual(completed.status, TurnStatus.COMPLETED)
                self.assertEqual(
                    completed.continuity_assessment,
                    result.assessment,
                )

    def test_scoped_internal_context_producers_activate_and_cache_per_turn(self):
        with tempfile.TemporaryDirectory() as root:
            evaluator = _InteractionContextEvaluator()
            with ERIIEngine(
                storage_dir=root,
                interaction_context_evaluator=evaluator,
            ) as engine:
                profile = engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _derived_context_candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Let's go outside!",
                    turn_id="turn-excited",
                )

                first = engine.activate_contextual_voice_patterns(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    interaction_context=(
                        {
                            "signal_id": "current-location",
                            "source": "host_observed",
                            "signal_type": "location",
                            "value": "street",
                            "recorded_at": "2026-07-29T00:00:00+00:00",
                        },
                    ),
                )
                repeated = engine.activate_contextual_voice_patterns(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    interaction_context=(
                        {
                            "signal_id": "current-location",
                            "source": "host_observed",
                            "signal_type": "location",
                            "value": "street",
                            "recorded_at": "2026-07-29T00:00:00+00:00",
                        },
                    ),
                )

                self.assertEqual(first, repeated)
                self.assertEqual(
                    {item.pattern_id for item in first},
                    {"emotion-playful", "safety-playful"},
                )
                self.assertTrue(
                    all(
                        item.relationship_id == profile.relationship_id
                        and item.source_turn_id == turn.turn_id
                        for item in first
                    )
                )
                self.assertEqual(len(evaluator.requests), 1)
                self.assertEqual(
                    evaluator.requests[0].relationship_id,
                    profile.relationship_id,
                )
                self.assertEqual(
                    evaluator.requests[0].turn_id,
                    turn.turn_id,
                )
                self.assertEqual(len(engine._interaction_context_cache), 1)
                engine.abandon_turn(
                    "agent-lumi",
                    "user-chen",
                    turn.turn_id,
                    reason="test complete",
                )
                self.assertEqual(len(engine._interaction_context_cache), 0)

    def test_context_evaluator_cannot_borrow_another_relationships_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            evaluator = _InteractionContextEvaluator(borrowed_evidence=True)
            with ERIIEngine(
                storage_dir=root,
                interaction_context_evaluator=evaluator,
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _derived_context_candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Let's go outside!",
                    turn_id="turn-excited",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "current relationship/Turn",
                ):
                    engine.activate_contextual_voice_patterns(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                    )

    def test_host_entrypoints_reject_unscopeable_derived_context_signals(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(storage_dir=root) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Playful line",
                )
                proposal = engine.propose_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    _candidate(),
                )
                engine.decide_persona_compilation(
                    "agent-lumi",
                    "user-chen",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "host_observed",
                ):
                    engine.begin_turn(
                        "agent-lumi",
                        "user-chen",
                        "Want another game?",
                        turn_id="turn-spoofed-core",
                        interaction_context=(
                            {
                                "signal_id": "borrowed-safety",
                                "source": "core_derived",
                                "signal_type": "relationship_safety",
                                "value": "safe",
                                "evidence_refs": ["relationship:someone-else"],
                            },
                        ),
                    )

                with self.assertRaisesRegex(
                    ValueError,
                    "cannot set internal",
                ):
                    engine.begin_turn(
                        "agent-lumi",
                        "user-chen",
                        "Want another game?",
                        turn_id="turn-spoofed-host-scope",
                        interaction_context=(
                            {
                                "signal_id": "scoped-location",
                                "source": "host_observed",
                                "signal_type": "location",
                                "value": "street",
                                "relationship_id": "borrowed-relationship",
                                "source_turn_id": "borrowed-turn",
                                "producer_version": "spoofed/1",
                            },
                        ),
                    )

                turn = engine.begin_turn(
                    "agent-lumi",
                    "user-chen",
                    "Want another game?",
                    turn_id="turn-game",
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "host_observed",
                ):
                    engine.activate_contextual_voice_patterns(
                        "agent-lumi",
                        "user-chen",
                        turn.turn_id,
                        interaction_context=(
                            {
                                "signal_id": "borrowed-emotion",
                                "source": "evaluator_inferred",
                                "signal_type": "emotion",
                                "value": "excited",
                                "evidence_refs": ["turn:another-relationship"],
                            },
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
