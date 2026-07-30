import unittest

from erii.core.consolidation import RelationshipConsolidator
from erii.models.adjudication import SourceProcessingMode
from erii.models.consolidation import (
    ApprovedGrowthReference,
    PersonaNoReflectionDecision,
    PersonaReflectionContentDecision,
    PersonaReflectionDecisionRecord,
    PersonaReflectionRecord,
    PersonaReflectionRecordKind,
    ReflectionContextProvenance,
    ReflectionInterpreterDescriptor,
    ReflectionProvenanceState,
    RelationshipEventCandidatesDecision,
    RelationshipNoEventDecision,
    RelationshipProcessingConflictError,
    RelationshipProcessingOutcome,
    RelationshipProcessingRun,
    RelationshipProcessingStatus,
    persona_reflection_decision_from_value,
    relationship_extraction_decision_from_value,
)
from erii.models.provenance import ExtractorDescriptor
from erii.models.relationship import (
    RelationshipEvent,
    RelationshipEventType,
)
from erii.models.temporal import (
    PromiseResolution,
    PromiseResolutionKind,
    PromiseResponsibleParty,
    PromiseSpec,
)


def _candidate(**overrides):
    value = {
        "candidate_key": "snow",
        "event_type": "shared_experience",
        "summary": "We watched the first snow together.",
        "signal": {
            "signal_type": "shared_experience",
            "strength": "moderate",
            "extraction_confidence": 0.95,
            "interpretation_confidence": 0.9,
        },
        "evidence": [
            {
                "source_id": "user-message",
                "source_revision": "1",
                "quote": "The snow is beautiful.",
                "start": 0,
                "end": 22,
            }
        ],
    }
    value.update(overrides)
    return value


class StrictDecisionContractTests(unittest.TestCase):
    def test_relationship_extraction_is_discriminated_and_persona_free(self):
        decision = relationship_extraction_decision_from_value(
            {"kind": "candidates", "candidates": [_candidate()]}
        )
        self.assertIsInstance(decision, RelationshipEventCandidatesDecision)
        self.assertEqual(decision.candidates[0].candidate_key, "snow")

        no_event = relationship_extraction_decision_from_value(
            {"kind": "no_relationship_event", "reason_code": "ordinary_exchange"}
        )
        self.assertIsInstance(no_event, RelationshipNoEventDecision)

        invalid_values = (
            {
                "kind": "candidates",
                "candidates": [_candidate(persona_reflection="I felt close to them.")],
            },
            {
                "kind": "candidates",
                "candidates": [_candidate(growth_trigger="pivotal")],
            },
            {
                "kind": "candidates",
                "candidates": [_candidate(unknown_field=True)],
            },
            {
                "kind": "candidates",
                "candidates": [_candidate()],
                "unknown_field": True,
            },
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    relationship_extraction_decision_from_value(value)

    def test_reflection_decision_is_strict_and_explicit(self):
        reflection = persona_reflection_decision_from_value(
            {
                "kind": "reflection",
                "content": "I wanted to remember that quiet moment.",
                "emotional_direction": "warm",
                "emotional_intensity": "moderate",
                "core_meaning": "A shared ordinary moment felt safe.",
            }
        )
        self.assertIsInstance(reflection, PersonaReflectionContentDecision)

        no_reflection = persona_reflection_decision_from_value(
            {"kind": "no_reflection", "reason_code": "no_distinct_inner_response"}
        )
        self.assertIsInstance(no_reflection, PersonaNoReflectionDecision)

        with self.assertRaises(ValueError):
            persona_reflection_decision_from_value(
                {
                    "kind": "reflection",
                    "content": "A thought.",
                    "emotional_direction": "neutral",
                    "emotional_intensity": "weak",
                    "core_meaning": "Nothing changed.",
                    "state_delta": {"trust": 1.0},
                }
            )


class ProcessingAndReflectionRecordTests(unittest.TestCase):
    def test_processing_run_freezes_decision_and_round_trips(self):
        decision = relationship_extraction_decision_from_value(
            {"kind": "candidates", "candidates": [_candidate()]}
        )
        run = RelationshipProcessingRun(
            processing_id="processing-1",
            relationship_id="relationship-1",
            source_turn_id="turn-1",
            source_revision="1",
            processing_mode=SourceProcessingMode.NORMAL,
            status=RelationshipProcessingStatus.COMPLETED,
            outcome=RelationshipProcessingOutcome.EVENTS_ACCEPTED,
            extractor_descriptor=ExtractorDescriptor(
                extractor_id="relationship-extractor",
                extractor_version="1.2.0",
                extraction_schema_version="1",
            ),
            frozen_decision=decision,
            adjudication_base_direct_event_count=2,
            adjudication_base_decision_count=3,
            adjudication_base_fingerprint="a" * 64,
            reflection_planned=True,
            decision_ids=("decision-1",),
            event_ids=("event-1",),
            reflection_outcome_ids=("reflection-decision-1",),
            rule_version="relationship-adjudication-v1",
            contract_version="relationship-processing-v1",
            created_at="2026-07-29T00:00:00+00:00",
            updated_at="2026-07-29T00:00:01+00:00",
            completed_at="2026-07-29T00:00:01+00:00",
        )

        restored = RelationshipProcessingRun.from_dict(run.to_dict())
        self.assertEqual(restored, run)
        self.assertIsNotNone(run.batch_fingerprint)

        changed = run.to_dict()
        changed["batch_fingerprint"] = "0" * 64
        with self.assertRaises(ValueError):
            RelationshipProcessingRun.from_dict(changed)
        with self.assertRaises(RelationshipProcessingConflictError):
            run.advance(adjudication_base_direct_event_count=3)
        for frozen_change in (
            {"rule_version": "replacement-rule"},
            {"contract_version": "replacement-contract"},
            {"created_at": "2026-07-30T00:00:00+00:00"},
        ):
            with self.subTest(frozen_change=frozen_change):
                with self.assertRaises(
                    RelationshipProcessingConflictError
                ):
                    run.advance(**frozen_change)

    def test_no_event_run_is_always_a_completed_terminal_outcome(self):
        with self.assertRaisesRegex(
            ValueError,
            "no-event processing must be completed",
        ):
            RelationshipProcessingRun(
                processing_id="processing-no-event",
                relationship_id="relationship-1",
                source_turn_id="turn-1",
                source_revision="1",
                processing_mode=SourceProcessingMode.NORMAL,
                status=RelationshipProcessingStatus.EXTRACTED,
                outcome=RelationshipProcessingOutcome.PENDING,
                extractor_descriptor=ExtractorDescriptor(
                    extractor_id="relationship-extractor",
                    extractor_version="1.2.0",
                    extraction_schema_version="1",
                ),
                frozen_decision=RelationshipNoEventDecision(
                    reason_code="ordinary_exchange"
                ),
            )

    def test_completed_run_outcome_must_match_accepted_event_presence(self):
        decision = relationship_extraction_decision_from_value(
            {"kind": "candidates", "candidates": [_candidate()]}
        )
        with self.assertRaisesRegex(
            ValueError,
            "completed accepted-event processing",
        ):
            RelationshipProcessingRun(
                processing_id="processing-inconsistent",
                relationship_id="relationship-1",
                source_turn_id="turn-1",
                source_revision="1",
                processing_mode=SourceProcessingMode.NORMAL,
                status=RelationshipProcessingStatus.COMPLETED,
                outcome=RelationshipProcessingOutcome.NO_ACCEPTED_EVENTS,
                extractor_descriptor=ExtractorDescriptor(
                    extractor_id="relationship-extractor",
                    extractor_version="1.2.0",
                    extraction_schema_version="1",
                ),
                frozen_decision=decision,
                decision_ids=("decision-1",),
                event_ids=("event-1",),
                completed_at="2026-07-29T00:00:01+00:00",
            )

    def test_reflection_record_and_no_reflection_outcome_are_portable(self):
        provenance = ReflectionContextProvenance(
            source_turn_id="turn-1",
            source_revision="1",
            decision_id="adjudication-1",
            evidence_ids=("evidence-1",),
            relationship_event_id="event-1",
            blueprint_id="blueprint-1",
            blueprint_sha256="a" * 64,
            blueprint_revision=1,
            manifest_id="manifest-1",
            manifest_revision=3,
            manifest_fingerprint="b" * 64,
            baseline_fingerprint="c" * 64,
            approved_growth=(
                ApprovedGrowthReference(
                    proposal_id="growth-1",
                    revision=2,
                    content_fingerprint="d" * 64,
                ),
            ),
        )
        descriptor = ReflectionInterpreterDescriptor(
            interpreter_id="persona-reflection",
            interpreter_version="1.0.0",
        )
        record = PersonaReflectionRecord(
            reflection_id="reflection-1",
            relationship_id="relationship-1",
            event_id="event-1",
            record_kind=PersonaReflectionRecordKind.REFLECTION,
            content="I wanted to keep this small, warm memory.",
            emotional_direction="warm",
            emotional_intensity="moderate",
            core_meaning="Ordinary time together felt safe.",
            interpreter_descriptor=descriptor,
            context_provenance=provenance,
            recorded_at="2026-07-29T00:00:02+00:00",
        )
        self.assertEqual(
            PersonaReflectionRecord.from_dict(record.to_dict()),
            record,
        )

        outcome = PersonaReflectionDecisionRecord(
            decision_id="reflection-decision-1",
            relationship_id="relationship-1",
            event_id="event-1",
            source_turn_id="turn-1",
            source_revision="1",
            interpreter_descriptor=descriptor,
            decision=PersonaNoReflectionDecision(
                reason_code="no_distinct_inner_response"
            ),
            context_provenance=provenance,
            recorded_at="2026-07-29T00:00:03+00:00",
        )
        self.assertEqual(
            PersonaReflectionDecisionRecord.from_dict(outcome.to_dict()),
            outcome,
        )

    def test_formal_reflection_cannot_downgrade_to_legacy_provenance(self):
        with self.assertRaisesRegex(
            ValueError,
            "formal reflection decision requires complete",
        ):
            PersonaReflectionDecisionRecord(
                decision_id="reflection-decision-legacy-bypass",
                relationship_id="relationship-1",
                event_id="event-1",
                source_turn_id="turn-1",
                source_revision="1",
                interpreter_descriptor=ReflectionInterpreterDescriptor(
                    interpreter_id="persona-reflection",
                    interpreter_version="1.0.0",
                ),
                decision=PersonaNoReflectionDecision(
                    reason_code="no_distinct_inner_response"
                ),
                context_provenance=ReflectionContextProvenance(
                    provenance_state=(
                        ReflectionProvenanceState.LEGACY_UNAVAILABLE
                    ),
                    relationship_event_id="event-1",
                ),
            )


class RelationshipConsolidationTests(unittest.TestCase):
    def test_projection_uses_only_explicit_grouping_evidence(self):
        events = [
            RelationshipEvent(
                event_id="event-2",
                relationship_id="relationship-1",
                event_type=RelationshipEventType.OBSERVATION,
                content="We noticed the snow settling on the street.",
                recorded_at="2026-01-01T00:00:02+00:00",
                metadata={
                    "adjudication": {
                        "occurrence_fingerprint": "snow-occurrence",
                        "references": [],
                    }
                },
            ),
            RelationshipEvent(
                event_id="event-1",
                relationship_id="relationship-1",
                event_type=RelationshipEventType.SHARED_EXPERIENCE,
                content="We watched the first snow together.",
                recorded_at="2026-01-01T00:00:01+00:00",
                metadata={
                    "adjudication": {
                        "occurrence_fingerprint": "snow-occurrence",
                        "references": [],
                    }
                },
            ),
            RelationshipEvent(
                event_id="event-4",
                relationship_id="relationship-1",
                event_type=RelationshipEventType.PROMISE_RESOLUTION,
                content="The promise was fulfilled.",
                temporal_payload=PromiseResolution(
                    promise_event_id="event-3",
                    resolution_kind=PromiseResolutionKind.FULFILLED,
                ),
                recorded_at="2026-01-02T00:00:02+00:00",
                metadata={"adjudication": {"references": ["event-2"]}},
            ),
            RelationshipEvent(
                event_id="event-3",
                relationship_id="relationship-1",
                event_type=RelationshipEventType.PROMISE,
                content="I promised to bring hot chocolate next time.",
                temporal_payload=PromiseSpec(
                    responsible_parties=(PromiseResponsibleParty.AGENT,),
                    action="Bring hot chocolate next time.",
                ),
                recorded_at="2026-01-02T00:00:01+00:00",
            ),
            RelationshipEvent(
                event_id="event-5",
                relationship_id="relationship-1",
                event_type=RelationshipEventType.OBSERVATION,
                content="A source-less observation.",
                recorded_at="2026-01-03T00:00:00+00:00",
            ),
        ]

        result = RelationshipConsolidator.project(
            "relationship-1",
            list(reversed(events)),
        )
        repeated = RelationshipConsolidator.project("relationship-1", events)

        self.assertEqual(result, repeated)
        self.assertEqual(len(result.episodes), 2)
        self.assertEqual(
            [episode.event_ids for episode in result.episodes],
            [("event-1", "event-2"), ("event-3", "event-4")],
        )
        self.assertEqual(len(result.chapters), 1)
        self.assertEqual(
            result.chapters[0].episode_ids,
            tuple(episode.episode_id for episode in result.episodes),
        )
        self.assertEqual(result.unconsolidated_event_ids, ("event-5",))
        self.assertEqual(
            set(result.covered_event_ids),
            {"event-1", "event-2", "event-3", "event-4"},
        )

        foreign = RelationshipEvent(
            event_id="foreign",
            relationship_id="relationship-2",
            event_type=RelationshipEventType.OBSERVATION,
            content="Not part of this relationship.",
            recorded_at="2026-01-04T00:00:00+00:00",
        )
        with self.assertRaises(ValueError):
            RelationshipConsolidator.project(
                "relationship-1",
                events + [foreign],
            )

    def test_unrelated_episodes_do_not_become_a_chapter(self):
        events = [
            RelationshipEvent(
                event_id="promise-snow",
                relationship_id="relationship-1",
                event_type=RelationshipEventType.PROMISE,
                content="I promised to bring hot chocolate.",
                temporal_payload=PromiseSpec(
                    responsible_parties=(PromiseResponsibleParty.AGENT,),
                    action="Bring hot chocolate.",
                ),
                recorded_at="2026-01-01T00:00:01+00:00",
            ),
            RelationshipEvent(
                event_id="resolution-snow",
                relationship_id="relationship-1",
                event_type=RelationshipEventType.PROMISE_RESOLUTION,
                content="The hot chocolate promise was fulfilled.",
                temporal_payload=PromiseResolution(
                    promise_event_id="promise-snow",
                    resolution_kind=PromiseResolutionKind.FULFILLED,
                ),
                recorded_at="2026-01-01T00:00:02+00:00",
            ),
            RelationshipEvent(
                event_id="promise-rain",
                relationship_id="relationship-1",
                event_type=RelationshipEventType.PROMISE,
                content="I promised to bring an umbrella.",
                temporal_payload=PromiseSpec(
                    responsible_parties=(PromiseResponsibleParty.AGENT,),
                    action="Bring an umbrella.",
                ),
                recorded_at="2026-01-02T00:00:01+00:00",
            ),
            RelationshipEvent(
                event_id="resolution-rain",
                relationship_id="relationship-1",
                event_type=RelationshipEventType.PROMISE_RESOLUTION,
                content="The umbrella promise was fulfilled.",
                temporal_payload=PromiseResolution(
                    promise_event_id="promise-rain",
                    resolution_kind=PromiseResolutionKind.FULFILLED,
                ),
                recorded_at="2026-01-02T00:00:02+00:00",
            ),
        ]

        result = RelationshipConsolidator.project(
            "relationship-1",
            events,
        )

        self.assertEqual(len(result.episodes), 2)
        self.assertEqual(result.chapters, ())


if __name__ == "__main__":
    unittest.main()
