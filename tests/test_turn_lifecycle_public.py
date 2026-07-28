"""Public Turn Recording lifecycle contracts."""

from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
import threading
import unittest

from erii import (
    ContinuityAssessmentStatus,
    DeliveryDisposition,
    DecisionOutcome,
    ERIIEngine,
    FileStorage,
    MemoryPack,
    SourceProcessingChannel,
    SourceProcessingState,
    SQLiteStorage,
    TurnStatus,
    TurnConflictError,
    TurnTerminalConflictError,
)


class TurnLifecyclePublicTests(unittest.TestCase):
    """Runs the same observable Turn behavior through both bundled adapters."""

    def _storage_factories(self, root_dir):
        return (
            ("file", lambda: FileStorage(os.path.join(root_dir, "files"))),
            ("sqlite", lambda: SQLiteStorage(os.path.join(root_dir, "turns.db"))),
        )

    def test_open_turn_is_retrievable_after_engine_restart(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                with ERIIEngine(storage_driver=make_storage()) as engine:
                    engine.initialize_relationship(
                        "agent_erii",
                        "user_one",
                        "A quiet character who values honest companionship.",
                    )
                    opened = engine.begin_turn(
                        "agent_erii",
                        "user_one",
                        "我们今天去看雪吗？",
                        turn_id="turn-first-snow",
                    )

                    self.assertEqual(opened.turn_id, "turn-first-snow")
                    self.assertEqual(opened.status, TurnStatus.OPEN)
                    self.assertEqual(opened.transcript.user_message.content, "我们今天去看雪吗？")
                    self.assertIsNone(opened.transcript.agent_message)

                with ERIIEngine(storage_driver=make_storage()) as reopened_engine:
                    restored = reopened_engine.get_turn(
                        "agent_erii",
                        "user_one",
                        "turn-first-snow",
                    )

                    self.assertEqual(restored, opened)

    def test_turn_opening_persists_only_host_observed_context_signals(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                with ERIIEngine(storage_driver=make_storage()) as engine:
                    engine.initialize_relationship(
                        "agent_erii",
                        "user_one",
                        "A quiet character who values honest companionship.",
                    )

                    opened = engine.begin_turn(
                        "agent_erii",
                        "user_one",
                        "Let us play one more round.",
                        turn_id="turn-context",
                        interaction_context=(
                            {
                                "signal_id": "context-arcade",
                                "source": "host_observed",
                                "signal_type": "location",
                                "value": "Tokyo arcade",
                            },
                        ),
                    )

                    self.assertEqual(len(opened.interaction_context), 1)
                    self.assertEqual(
                        opened.interaction_context[0].value,
                        "Tokyo arcade",
                    )
                    with self.assertRaises(ValueError):
                        engine.begin_turn(
                            "agent_erii",
                            "user_one",
                            "A forged relationship state.",
                            turn_id="turn-forged-context",
                            interaction_context=(
                                {
                                    "signal_id": "context-forged",
                                    "source": "core_derived",
                                    "signal_type": "relationship_stage",
                                    "value": "devoted",
                                },
                            ),
                        )

    def test_retryable_reply_failure_is_observable_without_storing_a_draft(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                with ERIIEngine(storage_driver=make_storage()) as engine:
                    engine.initialize_relationship(
                        "agent_erii",
                        "user_one",
                        "A quiet character who values honest companionship.",
                    )
                    engine.begin_turn(
                        "agent_erii",
                        "user_one",
                        "Are you ready?",
                        turn_id="turn-reply-attempt",
                    )

                    recorded = engine.record_reply_attempt_failure(
                        "agent_erii",
                        "user_one",
                        "turn-reply-attempt",
                        attempt_number=1,
                        stage="generation",
                        capability_descriptor="test-provider/model-v1",
                        failure_classification="temporary_provider_error",
                    )
                    attempts = engine.list_reply_attempts(
                        "agent_erii",
                        "user_one",
                        "turn-reply-attempt",
                    )
                    still_open = engine.get_turn(
                        "agent_erii",
                        "user_one",
                        "turn-reply-attempt",
                    )

                    self.assertEqual(attempts, [recorded])
                    self.assertEqual(still_open.status, TurnStatus.OPEN)
                    self.assertIsNone(still_open.transcript.agent_message)
                    self.assertNotIn(
                        "draft",
                        recorded.to_dict(),
                    )

    def test_completed_turn_seals_visible_reply_and_processing_plan(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                with ERIIEngine(storage_driver=make_storage()) as engine:
                    engine.initialize_relationship(
                        "agent_erii",
                        "user_one",
                        "A quiet character who values honest companionship.",
                    )
                    engine.begin_turn(
                        "agent_erii",
                        "user_one",
                        "Can we go see the snow today?",
                        turn_id="turn-completed",
                    )

                    receipt = engine.complete_turn(
                        "agent_erii",
                        "user_one",
                        "turn-completed",
                        "Of course. Let us go together.",
                        processing_channels=(
                            SourceProcessingChannel.MEMORY_ARCHIVAL,
                            SourceProcessingChannel.RELATIONSHIP_ADJUDICATION,
                        ),
                    )
                    completed = engine.get_turn(
                        "agent_erii",
                        "user_one",
                        "turn-completed",
                    )

                    self.assertEqual(receipt.source_turn_id, "turn-completed")
                    self.assertEqual(completed.status, TurnStatus.COMPLETED)
                    self.assertEqual(
                        completed.transcript.agent_message.content,
                        "Of course. Let us go together.",
                    )
                    self.assertEqual(
                        completed.continuity_assessment.status,
                        ContinuityAssessmentStatus.NOT_EVALUATED,
                    )
                    self.assertEqual(
                        completed.delivery_disposition,
                        DeliveryDisposition.SHOWN,
                    )
                    self.assertEqual(
                        completed.processing_plan.channels,
                        (
                            SourceProcessingChannel.MEMORY_ARCHIVAL,
                            SourceProcessingChannel.RELATIONSHIP_ADJUDICATION,
                        ),
                    )
                    self.assertTrue(
                        all(
                            outcome.state == SourceProcessingState.PENDING
                            for outcome in receipt.processing_outcomes
                        )
                    )

    def test_abandoned_turn_keeps_user_message_without_inventing_a_reply(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                with ERIIEngine(storage_driver=make_storage()) as engine:
                    engine.initialize_relationship(
                        "agent_erii",
                        "user_one",
                        "A quiet character who values honest companionship.",
                    )
                    engine.begin_turn(
                        "agent_erii",
                        "user_one",
                        "Are you still there?",
                        turn_id="turn-cancelled",
                    )

                    abandoned = engine.abandon_turn(
                        "agent_erii",
                        "user_one",
                        "turn-cancelled",
                        reason="user_cancelled",
                    )

                    self.assertEqual(abandoned.status, TurnStatus.ABANDONED)
                    self.assertEqual(abandoned.abandonment_reason, "user_cancelled")
                    self.assertIsNone(abandoned.transcript.agent_message)
                    self.assertIsNone(abandoned.processing_plan)
                    with self.assertRaises(TurnTerminalConflictError):
                        engine.complete_turn(
                            "agent_erii",
                            "user_one",
                            "turn-cancelled",
                            "I am here.",
                        )

    def test_begin_and_complete_retries_are_idempotent_by_turn_identity(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                with ERIIEngine(storage_driver=make_storage()) as engine:
                    engine.initialize_relationship(
                        "agent_erii",
                        "user_one",
                        "A quiet character who values honest companionship.",
                    )
                    first_open = engine.begin_turn(
                        "agent_erii",
                        "user_one",
                        "Please remember this moment.",
                        turn_id="turn-retry",
                    )
                    retried_open = engine.begin_turn(
                        "agent_erii",
                        "user_one",
                        "Please remember this moment.",
                        turn_id="turn-retry",
                    )
                    first_receipt = engine.complete_turn(
                        "agent_erii",
                        "user_one",
                        "turn-retry",
                        "I will remember it.",
                    )
                    retried_receipt = engine.complete_turn(
                        "agent_erii",
                        "user_one",
                        "turn-retry",
                        "I will remember it.",
                    )

                    self.assertEqual(retried_open, first_open)
                    self.assertEqual(retried_receipt, first_receipt)

    def test_competing_terminal_writers_have_exactly_one_winner(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                first_engine = ERIIEngine(storage_driver=make_storage())
                second_engine = ERIIEngine(storage_driver=make_storage())
                try:
                    first_engine.initialize_relationship(
                        "agent_erii",
                        "user_one",
                        "A quiet character who values honest companionship.",
                    )
                    first_engine.begin_turn(
                        "agent_erii",
                        "user_one",
                        "Which reply becomes history?",
                        turn_id="turn-race",
                    )
                    barrier = threading.Barrier(2)

                    def complete(engine, reply):
                        barrier.wait()
                        try:
                            engine.complete_turn(
                                "agent_erii",
                                "user_one",
                                "turn-race",
                                reply,
                                processing_channels=(),
                            )
                            return "completed"
                        except TurnTerminalConflictError:
                            return "conflict"

                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = (
                            pool.submit(
                                complete,
                                first_engine,
                                "The first possible reply.",
                            ),
                            pool.submit(
                                complete,
                                second_engine,
                                "The second possible reply.",
                            ),
                        )
                        outcomes = sorted(future.result() for future in futures)

                    self.assertEqual(outcomes, ["completed", "conflict"])
                finally:
                    first_engine.close()
                    second_engine.close()

    def test_record_turn_is_an_atomic_one_shot_entry_in_the_same_ledger(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                with ERIIEngine(storage_driver=make_storage()) as engine:
                    engine.initialize_relationship(
                        "agent_erii",
                        "user_one",
                        "A quiet character who values honest companionship.",
                    )

                    receipt = engine.record_turn(
                        "agent_erii",
                        "user_one",
                        "The snow has started.",
                        "Then this is our first snow together.",
                        turn_id="turn-one-shot",
                        processing_channels=(),
                    )
                    turns = engine.list_turns("agent_erii", "user_one")

                    self.assertEqual(receipt.source_turn_id, "turn-one-shot")
                    self.assertEqual(len(turns), 1)
                    self.assertEqual(turns[0].status, TurnStatus.COMPLETED)
                    self.assertEqual(turns[0].processing_plan.channels, ())

    def test_relationship_adjudication_can_reference_persisted_source_turn(self):
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                with ERIIEngine(storage_driver=make_storage()) as engine:
                    engine.initialize_relationship(
                        "agent_erii",
                        "user_one",
                        "A quiet character who values honest companionship.",
                    )
                    engine.record_turn(
                        "agent_erii",
                        "user_one",
                        "Thank you for staying with me.",
                        "I wanted to stay.",
                        turn_id="turn-provenance",
                        processing_channels=(),
                    )

                    result = engine.adjudicate_turn_candidates(
                        "agent_erii",
                        "user_one",
                        "turn-provenance",
                        [
                            {
                                "candidate_key": "gratitude-for-staying",
                                "event_type": "observation",
                                "summary": "The user thanked the agent for staying.",
                                "signal": {
                                    "signal_type": "gratitude",
                                    "strength": "moderate",
                                    "extraction_confidence": 0.95,
                                    "interpretation_confidence": 0.95,
                                },
                                "evidence": [
                                    {
                                        "source_id": "turn-provenance:user",
                                        "quote": "Thank you for staying with me.",
                                    }
                                ],
                            }
                        ],
                        extractor_version="test-relationship-extractor-v1",
                    )

                    self.assertEqual(
                        result.records[0].receipt.outcome,
                        DecisionOutcome.ACCEPTED,
                    )
                    self.assertEqual(
                        result.records[0].receipt.source_turn_id,
                        "turn-provenance",
                    )

    def test_memory_pack_round_trip_preserves_complete_source_transcript(self):
        root_dir = tempfile.mkdtemp()
        with ERIIEngine(
            storage_driver=FileStorage(os.path.join(root_dir, "source"))
        ) as source:
            source.initialize_relationship(
                "agent_erii",
                "user_one",
                "A quiet character who values honest companionship.",
            )
            source.record_turn(
                "agent_erii",
                "user_one",
                "This is worth carrying with us.",
                "Then we will carry the whole moment.",
                turn_id="turn-portable",
                processing_channels=(),
            )
            serialized_pack = source.export_memory(
                "agent_erii",
                "user_one",
            ).to_json()

        with ERIIEngine(
            storage_driver=SQLiteStorage(os.path.join(root_dir, "destination.db"))
        ) as destination:
            destination.import_memory(MemoryPack.from_json(serialized_pack))
            restored = destination.get_turn(
                "agent_erii",
                "user_one",
                "turn-portable",
            )

            self.assertEqual(restored.status, TurnStatus.COMPLETED)
            self.assertEqual(
                restored.transcript.user_message.content,
                "This is worth carrying with us.",
            )
            self.assertEqual(
                restored.transcript.agent_message.content,
                "Then we will carry the whole moment.",
            )

            conflicting_data = MemoryPack.from_json(serialized_pack).to_dict()
            conflicting_data["core_memory"] = "must not be partially imported"
            conflicting_data["turn_records"][0]["transcript"]["agent_message"][
                "content"
            ] = "A conflicting reply."

            with self.assertRaises(TurnConflictError):
                destination.import_memory(MemoryPack.from_dict(conflicting_data))
            self.assertEqual(
                destination.get_core_memory("agent_erii", "user_one"),
                "",
            )


if __name__ == "__main__":
    unittest.main()
