"""End-to-end contracts for the a7 relationship processing pipeline."""

import os
import tempfile
import unittest

from erii import ERIIEngine, FileStorage, SQLiteStorage
from erii.models.consolidation import (
    PersonaReflectionRecordKind,
    ReflectionInterpreterDescriptor,
    RelationshipProcessingOutcome,
    RelationshipProcessingStatus,
)
from erii.models.provenance import ExtractorDescriptor
from erii.models.recall import RecallAudience, RecallRequest
from erii.models.turn import SourceProcessingState
from erii.core.relationship_processing import (
    RelationshipProcessingSubmissionError,
)


def _preexisting_delivery_exception():
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.relationship-processing/v1",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-01T08:00:00+08:00",
        "reply_attempt_number": None,
    }


class _RelationshipExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.relationship-extractor",
        extractor_version="1",
        extraction_schema_version="1",
    )

    def __init__(self, outcome="event"):
        self.outcome = outcome
        self.requests = []

    def extract(self, request):
        self.requests.append(request)
        if self.outcome == "none":
            return {
                "kind": "no_relationship_event",
                "reason_code": "ordinary_exchange",
            }
        user_message = request.transcript.user_message
        source_id = (
            "missing-source"
            if self.outcome == "rejected"
            else user_message.message_id
        )
        return {
            "kind": "candidates",
            "candidates": [
                {
                    "candidate_key": "first-snow",
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
                            "source_id": source_id,
                            "source_revision": request.source_revision,
                            "quote": user_message.content,
                            "start": 0,
                            "end": len(user_message.content),
                        }
                    ],
                }
            ],
        }


class _ReflectionInterpreter:
    descriptor = ReflectionInterpreterDescriptor(
        interpreter_id="tests.persona-reflection",
        interpreter_version="1",
    )

    def __init__(self, *, no_reflection=False, fail_first=False):
        self.no_reflection = no_reflection
        self.fail_first = fail_first
        self.requests = []

    def interpret(self, request):
        self.requests.append(request)
        if self.fail_first and len(self.requests) == 1:
            raise RuntimeError("temporary interpreter failure")
        if self.no_reflection:
            return {
                "kind": "no_reflection",
                "reason_code": "no_distinct_inner_response",
            }
        return {
            "kind": "reflection",
            "content": "I wanted to keep this quiet moment.",
            "emotional_direction": "warm",
            "emotional_intensity": "moderate",
            "core_meaning": "Ordinary time together felt safe.",
        }


class _UniqueRelationshipExtractor(_RelationshipExtractor):
    def extract(self, request):
        decision = super().extract(request)
        if decision["kind"] == "candidates":
            candidate = decision["candidates"][0]
            candidate["candidate_key"] = request.source_turn_id
            candidate["summary"] = (
                f"Shared experience from {request.source_turn_id}."
            )
        return decision


class RelationshipProcessingPublicTests(unittest.TestCase):
    def _engine(self, root, extractor, interpreter=None):
        engine = ERIIEngine(
            storage_driver=FileStorage(root),
            relationship_event_extractor=extractor,
            persona_reflection_interpreter=interpreter,
        )
        engine.initialize_relationship(
            "agent-lumi",
            "user-chen",
            "Lumi values grounded shared experiences.",
        )
        return engine

    @staticmethod
    def _record_turn(engine, turn_id="turn-snow"):
        return engine.record_turn(
            "agent-lumi",
            "user-chen",
            "The snow is beautiful.",
            "Yes. I want to remember this quiet moment.",
            turn_id=turn_id,
            delivery_exception=_preexisting_delivery_exception(),
        )

    def test_event_then_reflection_is_durable_and_retry_does_not_resample(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _RelationshipExtractor()
            interpreter = _ReflectionInterpreter()
            with self._engine(root, extractor, interpreter) as engine:
                self._record_turn(engine)

                first = engine.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                repeated = engine.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )

                self.assertEqual(first, repeated)
                self.assertEqual(first.status, RelationshipProcessingStatus.COMPLETED)
                self.assertEqual(
                    first.outcome,
                    RelationshipProcessingOutcome.EVENTS_ACCEPTED,
                )
                self.assertEqual(len(extractor.requests), 1)
                self.assertEqual(len(interpreter.requests), 1)
                events = engine.list_relationship_events(
                    "agent-lumi",
                    "user-chen",
                )
                self.assertEqual(len(events), 1)
                self.assertIsNone(
                    events[0].metadata.get("adjudication", {}).get(
                        "persona_reflection"
                    ),
                )
                reflections = engine.list_persona_reflections(
                    "agent-lumi",
                    "user-chen",
                )
                self.assertEqual(len(reflections), 1)
                self.assertEqual(reflections[0].event_id, events[0].event_id)
                self.assertEqual(len(first.reflection_outcome_ids), 1)

    def test_no_event_is_terminal_and_survives_restart_without_extraction(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _RelationshipExtractor("none")
            with self._engine(root, extractor) as engine:
                self._record_turn(engine)
                first = engine.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                self.assertEqual(
                    first.outcome,
                    RelationshipProcessingOutcome.NO_RELATIONSHIP_EVENT,
                )
                self.assertEqual(len(extractor.requests), 1)

            replacement = _RelationshipExtractor("event")
            with self._engine(root, replacement) as restarted:
                repeated = restarted.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                self.assertEqual(repeated, first)
                self.assertEqual(replacement.requests, [])

    def test_rejected_candidates_never_reach_reflection(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _RelationshipExtractor("rejected")
            interpreter = _ReflectionInterpreter()
            with self._engine(root, extractor, interpreter) as engine:
                self._record_turn(engine)
                run = engine.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )

                self.assertEqual(
                    run.outcome,
                    RelationshipProcessingOutcome.NO_ACCEPTED_EVENTS,
                )
                self.assertEqual(interpreter.requests, [])
                self.assertEqual(
                    engine.list_persona_reflection_decisions(
                        "agent-lumi",
                        "user-chen",
                    ),
                    [],
                )

    def test_reflection_failure_keeps_event_and_retry_resumes_only_reflection(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _RelationshipExtractor()
            interpreter = _ReflectionInterpreter(fail_first=True)
            with self._engine(root, extractor, interpreter) as engine:
                self._record_turn(engine)
                partial = engine.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                self.assertEqual(
                    partial.status,
                    RelationshipProcessingStatus.PARTIAL_FAILED,
                )
                self.assertEqual(
                    len(
                        engine.list_relationship_events(
                            "agent-lumi",
                            "user-chen",
                        )
                    ),
                    1,
                )

                completed = engine.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                self.assertEqual(
                    completed.status,
                    RelationshipProcessingStatus.COMPLETED,
                )
                self.assertEqual(len(extractor.requests), 1)
                self.assertEqual(len(interpreter.requests), 2)
                self.assertEqual(
                    len(
                        engine.list_relationship_events(
                            "agent-lumi",
                            "user-chen",
                        )
                    ),
                    1,
                )

    def test_no_reflection_is_persisted_without_placeholder_record(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _RelationshipExtractor()
            interpreter = _ReflectionInterpreter(no_reflection=True)
            with self._engine(root, extractor, interpreter) as engine:
                self._record_turn(engine)
                run = engine.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )

                self.assertEqual(
                    run.status,
                    RelationshipProcessingStatus.COMPLETED,
                )
                self.assertEqual(
                    engine.list_persona_reflections(
                        "agent-lumi",
                        "user-chen",
                    ),
                    [],
                )
                decisions = engine.list_persona_reflection_decisions(
                    "agent-lumi",
                    "user-chen",
                )
                self.assertEqual(len(decisions), 1)
                self.assertEqual(decisions[0].decision.kind, "no_reflection")

    def test_reinterpretation_appends_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _RelationshipExtractor()
            interpreter = _ReflectionInterpreter()
            with self._engine(root, extractor, interpreter) as engine:
                self._record_turn(engine)
                engine.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                original = engine.list_persona_reflections(
                    "agent-lumi",
                    "user-chen",
                )[0]

                first = engine.reinterpret_persona_reflection(
                    "agent-lumi",
                    "user-chen",
                    original.reflection_id,
                    interpretation_id="later-understanding-1",
                )
                repeated = engine.reinterpret_persona_reflection(
                    "agent-lumi",
                    "user-chen",
                    original.reflection_id,
                    interpretation_id="later-understanding-1",
                )

                self.assertEqual(first, repeated)
                self.assertEqual(len(interpreter.requests), 2)
                reflections = engine.list_persona_reflections(
                    "agent-lumi",
                    "user-chen",
                )
                self.assertEqual(len(reflections), 2)
                self.assertEqual(reflections[0], original)
                self.assertEqual(
                    reflections[1].record_kind,
                    PersonaReflectionRecordKind.REINTERPRETATION,
                )
                self.assertEqual(
                    reflections[1].target_reflection_id,
                    original.reflection_id,
                )
                recall = engine.recall_structured(
                    RecallRequest(
                        agent_id="agent-lumi",
                        user_id="user-chen",
                        query="snow",
                        audience=RecallAudience.AGENT_PRIVATE,
                        options={"persona_delivery": "full"},
                    )
                )
                reflection_narratives = [
                    item
                    for item in recall.relationship_context.narratives
                    if item.source_kind == "persona_reflection_record"
                ]
                self.assertEqual(len(reflection_narratives), 2)
                self.assertEqual(
                    {item.source_id for item in reflection_narratives},
                    {item.reflection_id for item in reflections},
                )

    def test_correction_and_reinterpretation_append_each_host_version(self):
        with tempfile.TemporaryDirectory() as root:
            storage_factories = (
                (
                    "file",
                    lambda: FileStorage(os.path.join(root, "file")),
                ),
                (
                    "sqlite",
                    lambda: SQLiteStorage(os.path.join(root, "erii.db")),
                ),
            )
            for storage_name, storage_factory in storage_factories:
                with self.subTest(storage=storage_name):
                    extractor = _RelationshipExtractor()
                    interpreter = _ReflectionInterpreter()
                    with ERIIEngine(
                        storage_driver=storage_factory(),
                        relationship_event_extractor=extractor,
                        persona_reflection_interpreter=interpreter,
                    ) as engine:
                        engine.initialize_relationship(
                            "agent-lumi",
                            "user-chen",
                            "Lumi values grounded shared experiences.",
                        )
                        self._record_turn(engine)
                        engine.process_relationship_turn(
                            "agent-lumi",
                            "user-chen",
                            "turn-snow",
                        )
                        original = engine.list_persona_reflections(
                            "agent-lumi",
                            "user-chen",
                        )[0]

                        appended = []
                        for operation in (
                            engine.correct_persona_reflection,
                            engine.reinterpret_persona_reflection,
                        ):
                            first = operation(
                                "agent-lumi",
                                "user-chen",
                                original.reflection_id,
                                interpretation_id="v1",
                            )
                            repeated = operation(
                                "agent-lumi",
                                "user-chen",
                                original.reflection_id,
                                interpretation_id="v1",
                            )
                            second = operation(
                                "agent-lumi",
                                "user-chen",
                                original.reflection_id,
                                interpretation_id="v2",
                            )
                            second_repeated = operation(
                                "agent-lumi",
                                "user-chen",
                                original.reflection_id,
                                interpretation_id="v2",
                            )

                            self.assertEqual(first, repeated)
                            self.assertEqual(second, second_repeated)
                            self.assertNotEqual(first.decision_id, second.decision_id)
                            self.assertNotEqual(
                                first.interpretation_identity,
                                second.interpretation_identity,
                            )
                            self.assertEqual(first.interpretation_id, "v1")
                            self.assertEqual(second.interpretation_id, "v2")
                            appended.extend((first, second))

                        self.assertEqual(len(interpreter.requests), 5)
                        self.assertEqual(
                            [
                                item.record_kind
                                for item in appended
                            ],
                            [
                                PersonaReflectionRecordKind.CORRECTION,
                                PersonaReflectionRecordKind.CORRECTION,
                                PersonaReflectionRecordKind.REINTERPRETATION,
                                PersonaReflectionRecordKind.REINTERPRETATION,
                            ],
                        )

                    with ERIIEngine(
                        storage_driver=storage_factory(),
                    ) as restarted:
                        persisted = (
                            restarted.list_persona_reflection_decisions(
                                "agent-lumi",
                                "user-chen",
                            )
                        )
                        self.assertEqual(len(persisted), 5)
                        self.assertEqual(
                            [
                                item.interpretation_id
                                for item in persisted[1:]
                            ],
                            ["v1", "v2", "v1", "v2"],
                        )
                        self.assertEqual(
                            len(
                                restarted.list_persona_reflections(
                                    "agent-lumi",
                                    "user-chen",
                                )
                            ),
                            5,
                        )

    def test_source_outcome_and_sealed_plan_are_enforced(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _RelationshipExtractor()
            with self._engine(root, extractor) as engine:
                self._record_turn(engine)
                pending = engine.get_source_processing_outcomes(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                self.assertEqual(pending[0].state, SourceProcessingState.PENDING)

                engine.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                completed = engine.get_source_processing_outcomes(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                self.assertEqual(
                    completed[0].state,
                    SourceProcessingState.ARTIFACTS_COMMITTED,
                )

                engine.record_turn(
                    "agent-lumi",
                    "user-chen",
                    "Nothing special.",
                    "All right.",
                    turn_id="turn-unplanned",
                    delivery_exception=_preexisting_delivery_exception(),
                    processing_channels=(),
                )
                with self.assertRaises(RelationshipProcessingSubmissionError):
                    engine.process_relationship_turn(
                        "agent-lumi",
                        "user-chen",
                        "turn-unplanned",
                    )
                self.assertEqual(len(extractor.requests), 1)

    def test_persona_growth_accepts_formal_reflections(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _UniqueRelationshipExtractor()
            interpreter = _ReflectionInterpreter()
            with self._engine(root, extractor, interpreter) as engine:
                self._record_turn(engine, "turn-snow-1")
                self._record_turn(engine, "turn-snow-2")
                runs = [
                    engine.process_relationship_turn(
                        "agent-lumi",
                        "user-chen",
                        turn_id,
                    )
                    for turn_id in ("turn-snow-1", "turn-snow-2")
                ]

                proposal = engine.propose_persona_growth(
                    "agent-lumi",
                    "user-chen",
                    {
                        "intent_key": "ordinary-time-feels-safe",
                        "review_id": "review-formal-reflections",
                        "statement": "I am learning to value ordinary time together.",
                        "rationale": "Two independent shared experiences support it.",
                        "proposed_changes": {"ordinary_time": "valued"},
                        "supporting_event_ids": [
                            event_id
                            for run in runs
                            for event_id in run.event_ids
                        ],
                        "trigger_kind": "accumulation",
                    },
                )
                self.assertEqual(
                    tuple(proposal.supporting_event_ids),
                    tuple(event_id for run in runs for event_id in run.event_ids),
                )


if __name__ == "__main__":
    unittest.main()
