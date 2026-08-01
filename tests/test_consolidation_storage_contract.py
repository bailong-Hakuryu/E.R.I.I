"""Storage contracts for a7 relationship processing and consolidation."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
import tempfile
import unittest

from erii import (
    ERIIEngine,
    ExtractorDescriptor,
    FileStorage,
    RelationshipEvent,
    RelationshipEventType,
    SQLiteStorage,
)
from erii.models.adjudication import SourceProcessingMode
from erii.models.consolidation import (
    PersonaNoReflectionDecision,
    PersonaReflectionContentDecision,
    PersonaReflectionDecisionRecord,
    PersonaReflectionRecord,
    PersonaReflectionRecordKind,
    ReflectionContextProvenance,
    ReflectionInterpreterDescriptor,
    RelationshipProcessingConflictError,
    RelationshipProcessingOutcome,
    RelationshipProcessingRun,
    RelationshipProcessingStatus,
    relationship_extraction_decision_from_value,
)


def _preexisting_visible_exchange_delivery_exception():
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-07-29T00:00:00+00:00",
        "reply_attempt_number": None,
    }


class FileRelationshipHistoryConcurrencyTest(unittest.TestCase):
    def test_two_file_storage_instances_do_not_lose_relationship_events(self):
        with tempfile.TemporaryDirectory() as root_dir:
            first = FileStorage(root_dir)
            with ERIIEngine(storage_driver=first) as engine:
                profile = engine.initialize_relationship(
                    "agent_lumi",
                    "user_chen",
                    "Lumi keeps relationship histories isolated.",
                )
            second = FileStorage(root_dir)
            events = [
                RelationshipEvent(
                    event_id=f"concurrent-event-{index}",
                    relationship_id=profile.relationship_id,
                    event_type=RelationshipEventType.OBSERVATION,
                    content=f"Concurrent observation {index}.",
                    recorded_at=f"2026-07-29T00:00:{index:02d}+00:00",
                )
                for index in range(40)
            ]

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [
                    pool.submit(
                        (first if index % 2 == 0 else second).append_relationship_event,
                        event,
                    )
                    for index, event in enumerate(events)
                ]
                for future in futures:
                    future.result()

            stored = first.list_relationship_events(profile.relationship_id)
            self.assertEqual(
                {event.event_id for event in stored},
                {event.event_id for event in events},
            )


class ConsolidationStorageContract(unittest.TestCase):
    def _storage_factories(self, root_dir):
        return (
            ("file", lambda: FileStorage(os.path.join(root_dir, "files"))),
            ("sqlite", lambda: SQLiteStorage(os.path.join(root_dir, "memory.db"))),
        )

    def _accepted_context(self, storage):
        engine = ERIIEngine(storage_driver=storage)
        profile = engine.initialize_relationship(
            "agent_lumi",
            "user_chen",
            "Lumi values grounded, ordinary shared experiences.",
        )
        engine.record_turn(
            "agent_lumi",
            "user_chen",
            "The snow is beautiful.",
            "Yes. I want to remember this quiet moment.",
            turn_id="turn-snow",
            delivery_exception=(
                _preexisting_visible_exchange_delivery_exception()
            ),
        )
        turn = engine.get_turn("agent_lumi", "user_chen", "turn-snow")
        source_id = turn.transcript.user_message.message_id
        result = engine.adjudicate_turn_candidates(
            "agent_lumi",
            "user_chen",
            "turn-snow",
            [
                {
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
                            "source_id": source_id,
                            "source_revision": "1",
                            "quote": "The snow is beautiful.",
                            "start": 0,
                            "end": 22,
                        }
                    ],
                }
            ],
            extractor_version="tests.relationship-extractor/1",
        )
        adjudication = result.records[0]
        event = result.events[0]
        decision = relationship_extraction_decision_from_value(
            {
                "kind": "candidates",
                "candidates": [
                    {
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
                                "source_id": source_id,
                                "source_revision": "1",
                                "quote": "The snow is beautiful.",
                                "start": 0,
                                "end": 22,
                            }
                        ],
                    }
                ],
            }
        )
        run = RelationshipProcessingRun(
            processing_id="processing-snow",
            relationship_id=profile.relationship_id,
            source_turn_id=turn.turn_id,
            source_revision=turn.source_revision,
            processing_mode=SourceProcessingMode.NORMAL,
            status=RelationshipProcessingStatus.REFLECTION_PENDING,
            outcome=RelationshipProcessingOutcome.PENDING,
            extractor_descriptor=ExtractorDescriptor(
                extractor_id="tests.relationship-extractor",
                extractor_version="1",
                extraction_schema_version="1",
            ),
            frozen_decision=decision,
            reflection_planned=True,
            decision_ids=(adjudication.receipt.decision_id,),
            event_ids=(event.event_id,),
            created_at="2026-07-29T00:00:00+00:00",
            updated_at="2026-07-29T00:00:01+00:00",
        )
        baseline_fingerprint = hashlib.sha256(
            json.dumps(
                profile.baseline.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        provenance = ReflectionContextProvenance(
            relationship_event_id=event.event_id,
            source_turn_id=turn.turn_id,
            source_revision=turn.source_revision,
            decision_id=adjudication.receipt.decision_id,
            evidence_ids=tuple(
                item.evidence_id for item in adjudication.receipt.evidence
            ),
            blueprint_id=profile.blueprint.blueprint_id,
            blueprint_sha256=profile.blueprint.source_sha256,
            blueprint_revision=profile.blueprint.revision,
            baseline_fingerprint=baseline_fingerprint,
        )
        return engine, profile, run, event, provenance

    def test_run_identity_and_cas_are_shared_across_builtin_adapters(self):
        with tempfile.TemporaryDirectory() as root_dir:
            for name, make_storage in self._storage_factories(root_dir):
                with self.subTest(storage=name):
                    storage = make_storage()
                    engine, _profile, run, _event, _provenance = (
                        self._accepted_context(storage)
                    )
                    try:
                        first = storage.create_relationship_processing_run(run)
                        repeated = storage.create_relationship_processing_run(run)
                        self.assertEqual(repeated, first)

                        advanced = run.advance(
                            status=RelationshipProcessingStatus.ADJUDICATED,
                        )
                        stored = storage.update_relationship_processing_run(
                            advanced,
                            expected_record_version=run.record_version,
                        )
                        self.assertEqual(stored, advanced)
                        self.assertEqual(
                            storage.get_relationship_processing_run(
                                run.relationship_id,
                                run.processing_id,
                            ),
                            advanced,
                        )

                        stale = run.advance(
                            status=RelationshipProcessingStatus.PARTIAL_FAILED,
                            outcome=(
                                RelationshipProcessingOutcome.PARTIAL_FAILED
                            ),
                            reflection_failure_event_ids=run.event_ids,
                            safe_failure_code="persona_reflection_failed",
                            completed_at="2026-07-29T00:00:02+00:00",
                        )
                        with self.assertRaises(
                            RelationshipProcessingConflictError
                        ):
                            storage.update_relationship_processing_run(
                                stale,
                                expected_record_version=run.record_version,
                            )

                        conflicting_identity = replace(
                            run,
                            processing_id="processing-snow-conflict",
                        )
                        with self.assertRaises(
                            RelationshipProcessingConflictError
                        ):
                            storage.create_relationship_processing_run(
                                conflicting_identity
                            )
                    finally:
                        engine.close()

    def test_reflection_and_no_reflection_are_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as root_dir:
            for name, make_storage in self._storage_factories(root_dir):
                with self.subTest(storage=name):
                    storage = make_storage()
                    engine, _profile, run, event, provenance = (
                        self._accepted_context(storage)
                    )
                    try:
                        storage.create_relationship_processing_run(run)
                        descriptor = ReflectionInterpreterDescriptor(
                            interpreter_id="tests.persona-reflection",
                            interpreter_version="1",
                        )
                        content = PersonaReflectionContentDecision(
                            content="I wanted to keep this quiet memory.",
                            emotional_direction="warm",
                            emotional_intensity="moderate",
                            core_meaning="Ordinary time together felt safe.",
                        )
                        reflection = PersonaReflectionRecord(
                            reflection_id="reflection-snow",
                            relationship_id=run.relationship_id,
                            event_id=event.event_id,
                            record_kind=PersonaReflectionRecordKind.REFLECTION,
                            content=content.content,
                            emotional_direction=content.emotional_direction,
                            emotional_intensity=content.emotional_intensity,
                            core_meaning=content.core_meaning,
                            interpreter_descriptor=descriptor,
                            context_provenance=provenance,
                            recorded_at="2026-07-29T00:00:02+00:00",
                        )
                        decision = PersonaReflectionDecisionRecord(
                            decision_id="reflection-decision-snow",
                            relationship_id=run.relationship_id,
                            event_id=event.event_id,
                            source_turn_id=run.source_turn_id,
                            source_revision=run.source_revision,
                            interpreter_descriptor=descriptor,
                            decision=content,
                            context_provenance=provenance,
                            reflection_record=reflection,
                            recorded_at="2026-07-29T00:00:02+00:00",
                        )
                        completed = run.advance(
                            status=RelationshipProcessingStatus.COMPLETED,
                            outcome=RelationshipProcessingOutcome.EVENTS_ACCEPTED,
                            reflection_outcome_ids=(decision.decision_id,),
                            completed_at="2026-07-29T00:00:03+00:00",
                        )
                        stored = storage.commit_persona_reflection_decision(decision)
                        repeated = storage.commit_persona_reflection_decision(decision)
                        self.assertEqual(repeated, stored)
                        storage.update_relationship_processing_run(
                            completed,
                            expected_record_version=run.record_version,
                        )
                        self.assertEqual(
                            storage.list_persona_reflection_decisions(
                                run.relationship_id
                            ),
                            [decision],
                        )
                        self.assertEqual(
                            storage.list_persona_reflection_records(
                                run.relationship_id
                            ),
                            [reflection],
                        )

                        no_reflection = PersonaReflectionDecisionRecord(
                            decision_id="reflection-decision-none",
                            relationship_id=run.relationship_id,
                            event_id=event.event_id,
                            source_turn_id=run.source_turn_id,
                            source_revision=run.source_revision,
                            interpreter_descriptor=descriptor,
                            decision=PersonaNoReflectionDecision(
                                reason_code="reflection_not_needed"
                            ),
                            context_provenance=provenance,
                            recorded_at="2026-07-29T00:00:04+00:00",
                        )
                        with self.assertRaises(
                            RelationshipProcessingConflictError
                        ):
                            storage.commit_persona_reflection_decision(no_reflection)
                        self.assertEqual(
                            storage.list_persona_reflection_decisions(
                                run.relationship_id
                            ),
                            [decision],
                        )
                    finally:
                        engine.close()


if __name__ == "__main__":
    unittest.main()
