"""Public Turn Recording lifecycle contracts."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import copy
import hashlib
import os
import tempfile
import threading
import unittest

import erii
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


def _unreviewed_delivery_exception(actor_id="tests.turn-host-policy/v1"):
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": actor_id,
        "reason_code": "availability_fallback",
        "decided_at": "2026-08-01T08:00:00+08:00",
        "reply_attempt_number": None,
    }


class _SnapshotOnlyFileStorage(FileStorage):
    """Proves modern Turn opening does not compose legacy point reads."""

    def get_persona_manifest(self, manifest_id):
        raise AssertionError("Turn opening must use capture_turn_context_source")

    def list_persona_compilation_proposals(self, blueprint_id):
        raise AssertionError("Turn opening must use capture_turn_context_source")

    def list_persona_growth_proposals(self, relationship_id):
        raise AssertionError("Turn opening must use capture_turn_context_source")

    def list_relationship_events(self, relationship_id):
        raise AssertionError("Turn opening must use capture_turn_context_source")

    def list_relationship_adjudications(self, relationship_id):
        raise AssertionError("Turn opening must use capture_turn_context_source")


class _GatedSnapshotFileStorage(FileStorage):
    def __init__(self, root_dir, snapshot_started, release_snapshot):
        self._snapshot_started = snapshot_started
        self._release_snapshot = release_snapshot
        super().__init__(root_dir=root_dir)

    def capture_turn_context_source(self, profile):
        with self._turn_context_snapshot_guard():
            self._snapshot_started.set()
            if not self._release_snapshot.wait(timeout=5):
                raise TimeoutError("test did not release the Turn Context snapshot")
            return super().capture_turn_context_source(profile)


class TurnLifecyclePublicTests(unittest.TestCase):
    """Runs the same observable Turn behavior through both bundled adapters."""

    def _storage_factories(self, root_dir):
        return (
            ("file", lambda: FileStorage(os.path.join(root_dir, "files"))),
            ("sqlite", lambda: SQLiteStorage(os.path.join(root_dir, "turns.db"))),
        )

    def test_a8_turn_audit_models_are_publicly_importable(self):
        for name in (
            "ContinuityReviewReceipt",
            "ContinuityReviewRecord",
            "DeliveryExceptionRecord",
            "TurnContextBaseline",
        ):
            with self.subTest(symbol=name):
                self.assertTrue(hasattr(erii, name), name)

    def test_unreviewed_reply_cannot_be_recorded_as_ordinary_shown(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_dir=root_dir) as engine:
                engine.initialize_relationship(
                    "agent_erii",
                    "user_one",
                    "A quiet character who values honest companionship.",
                )
                engine.begin_turn(
                    "agent_erii",
                    "user_one",
                    "Are you still here?",
                    turn_id="turn-unreviewed-shown",
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "shown_unreviewed",
                ):
                    engine.complete_turn(
                        "agent_erii",
                        "user_one",
                        "turn-unreviewed-shown",
                        "I am still here.",
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

    def test_new_turn_freezes_a_portable_context_baseline_at_opening(self):
        persona_source = "A quiet character who values honest companionship."
        for name, make_storage in self._storage_factories(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                with ERIIEngine(storage_driver=make_storage()) as engine:
                    engine.initialize_relationship(
                        "agent_erii",
                        "user_one",
                        persona_source,
                    )

                    opened = engine.begin_turn(
                        "agent_erii",
                        "user_one",
                        "Are you still here?",
                        turn_id="turn-context-baseline",
                    )

                    baseline = opened.context_baseline
                    self.assertIsNotNone(baseline)
                    self.assertEqual(
                        baseline.baseline_version,
                        "turn-context-baseline/v1",
                    )
                    self.assertEqual(baseline.relationship_id, opened.relationship_id)
                    self.assertEqual(baseline.turn_id, opened.turn_id)
                    self.assertEqual(
                        baseline.blueprint.source_sha256,
                        hashlib.sha256(persona_source.encode("utf-8")).hexdigest(),
                    )
                    self.assertIsNone(baseline.manifest)
                    self.assertEqual(baseline.approved_growth_refs, ())
                    self.assertEqual(baseline.premise.premise_id, "fresh")
                    self.assertEqual(baseline.direct_event_count, 0)
                    self.assertEqual(baseline.adjudication_count, 0)
                    self.assertEqual(len(baseline.history_prefix_fingerprint), 64)
                    self.assertEqual(len(baseline.baseline_fingerprint), 64)
                    self.assertEqual(
                        set(baseline.policy_versions),
                        {
                            "relationship_baseline_policy",
                            "relationship_history_projection",
                            "relationship_safety_policy",
                            "interaction_context_policy",
                            "voice_matcher_policy",
                        },
                    )
                    self.assertEqual(
                        opened.to_dict()["context_baseline"],
                        baseline.to_dict(),
                    )

                with ERIIEngine(storage_driver=make_storage()) as reopened_engine:
                    restored = reopened_engine.get_turn(
                        "agent_erii",
                        "user_one",
                        "turn-context-baseline",
                    )

                    self.assertEqual(restored.context_baseline, baseline)

    def test_turn_opening_uses_one_storage_snapshot_instead_of_legacy_getters(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(
                storage_driver=_SnapshotOnlyFileStorage(root_dir)
            ) as engine:
                engine.initialize_relationship(
                    "agent_erii",
                    "user_one",
                    "A quiet character who values honest companionship.",
                )

                opened = engine.begin_turn(
                    "agent_erii",
                    "user_one",
                    "Are you still here?",
                    turn_id="turn-single-snapshot",
                )

                self.assertEqual(opened.context_baseline.direct_event_count, 0)
                self.assertEqual(opened.context_baseline.approved_growth_refs, ())

    def test_file_snapshot_blocks_contributing_writers_until_capture_finishes(self):
        with tempfile.TemporaryDirectory() as root_dir:
            snapshot_started = threading.Event()
            release_snapshot = threading.Event()
            opening_storage = _GatedSnapshotFileStorage(
                root_dir,
                snapshot_started,
                release_snapshot,
            )
            writing_storage = FileStorage(root_dir)
            with ERIIEngine(storage_driver=opening_storage) as opening_engine:
                opening_engine.initialize_relationship(
                    "agent_erii",
                    "user_one",
                    "A quiet character who values honest companionship.",
                )
                with ERIIEngine(storage_driver=writing_storage) as writing_engine:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        opening = pool.submit(
                            opening_engine.begin_turn,
                            "agent_erii",
                            "user_one",
                            "What changed before this moment?",
                            turn_id="turn-gated-file-snapshot",
                        )
                        self.assertTrue(snapshot_started.wait(timeout=5))
                        writing = pool.submit(
                            writing_engine.record_relationship_event,
                            "agent_erii",
                            "user_one",
                            "observation",
                            "This event occurred after the opening snapshot.",
                            event_id="event-after-gated-snapshot",
                        )

                        with self.assertRaises(FutureTimeoutError):
                            writing.result(timeout=0.1)
                        release_snapshot.set()
                        opened = opening.result(timeout=5)
                        writing.result(timeout=5)

                self.assertEqual(opened.context_baseline.direct_event_count, 0)
                self.assertEqual(
                    len(opening_engine.list_relationship_events(
                        "agent_erii",
                        "user_one",
                    )),
                    1,
                )

    def test_modern_turn_wire_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_dir=root_dir) as engine:
                engine.initialize_relationship(
                    "agent_erii",
                    "user_one",
                    "A quiet character who values honest companionship.",
                )
                opened = engine.begin_turn(
                    "agent_erii",
                    "user_one",
                    "Are you still here?",
                    turn_id="turn-strict-wire",
                )
                payload = opened.to_dict()
                payload["unknown_future_authority"] = "must-not-be-ignored"

                with self.assertRaisesRegex(ValueError, "unknown or missing"):
                    type(opened).from_dict(payload)

    def test_modern_turn_cannot_be_downgraded_by_removing_its_version(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_dir=root_dir) as engine:
                engine.initialize_relationship(
                    "agent_erii",
                    "user_one",
                    "A quiet character who values honest companionship.",
                )
                opened = engine.begin_turn(
                    "agent_erii",
                    "user_one",
                    "Are you still here?",
                    turn_id="turn-no-wire-downgrade",
                )
                payload = opened.to_dict()
                del payload["turn_format_version"]

                with self.assertRaisesRegex(ValueError, "modern.*version"):
                    type(opened).from_dict(payload)

    def test_modern_turn_wire_rejects_nested_unknown_fields_and_coercion(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_dir=root_dir) as engine:
                engine.initialize_relationship(
                    "agent_erii",
                    "user_one",
                    "A quiet character who values honest companionship.",
                )
                opened = engine.begin_turn(
                    "agent_erii",
                    "user_one",
                    "Are you still here?",
                    turn_id="turn-strict-nested-wire",
                    interaction_context=(
                        {
                            "signal_id": "observed-location",
                            "source": "host_observed",
                            "signal_type": "location",
                            "value": "home",
                        },
                    ),
                )
                original = opened.to_dict()

                mutations = {}
                transcript_unknown = copy.deepcopy(original)
                transcript_unknown["transcript"]["unknown"] = "forbidden"
                mutations["transcript unknown"] = transcript_unknown
                message_unknown = copy.deepcopy(original)
                message_unknown["transcript"]["user_message"]["unknown"] = "forbidden"
                mutations["message unknown"] = message_unknown
                signal_unknown = copy.deepcopy(original)
                signal_unknown["interaction_context"][0]["unknown"] = "forbidden"
                mutations["signal unknown"] = signal_unknown
                signal_array_tuple = copy.deepcopy(original)
                signal_array_tuple["interaction_context"][0]["evidence_refs"] = ()
                mutations["signal tuple array"] = signal_array_tuple
                message_number = copy.deepcopy(original)
                message_number["transcript"]["user_message"]["content"] = 7
                mutations["message number"] = message_number

                for name, payload in mutations.items():
                    with self.subTest(case=name):
                        with self.assertRaises(ValueError):
                            type(opened).from_dict(payload)

    def test_explicit_legacy_turn_wire_remains_readable(self):
        legacy_payload = {
            "turn_id": "legacy-open-turn",
            "relationship_id": "legacy-relationship",
            "status": "open",
            "transcript": {
                "user_message": {
                    "message_id": "legacy-open-turn:user",
                    "role": "user",
                    "content": "A legacy visible message.",
                    "recorded_at": "2026-07-01T00:00:00+00:00",
                },
                "agent_message": None,
            },
            "interaction_context": [],
            "source_revision": "1",
            "record_version": 1,
            "opened_at": "2026-07-01T00:00:00+00:00",
            "continuity_assessment": None,
            "delivery_disposition": None,
            "processing_plan": None,
            "processing_outcomes": [],
            "completed_at": None,
            "abandoned_at": None,
            "abandonment_reason": None,
        }

        restored = erii.TurnRecord.from_dict(legacy_payload)

        self.assertEqual(restored.turn_format_version, "turn-record/v1")
        self.assertEqual(restored.status, TurnStatus.OPEN)
        self.assertIsNone(restored.context_baseline)

    def test_modern_turn_wire_does_not_coerce_scalar_or_array_types(self):
        with tempfile.TemporaryDirectory() as root_dir:
            with ERIIEngine(storage_dir=root_dir) as engine:
                engine.initialize_relationship(
                    "agent_erii",
                    "user_one",
                    "A quiet character who values honest companionship.",
                )
                opened = engine.begin_turn(
                    "agent_erii",
                    "user_one",
                    "Are you still here?",
                    turn_id="turn-strict-types",
                )
                mutations = {
                    "string record version": ("record_version", "1"),
                    "boolean record version": ("record_version", True),
                    "tuple context array": ("interaction_context", ()),
                }
                for name, (field_name, invalid_value) in mutations.items():
                    with self.subTest(case=name):
                        payload = opened.to_dict()
                        payload[field_name] = invalid_value
                        with self.assertRaises(ValueError):
                            type(opened).from_dict(payload)

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
                        delivery_disposition=DeliveryDisposition.SHOWN_UNREVIEWED,
                        delivery_exception=_unreviewed_delivery_exception(),
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
                        DeliveryDisposition.SHOWN_UNREVIEWED,
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
                        delivery_disposition=DeliveryDisposition.SHOWN_UNREVIEWED,
                        delivery_exception=_unreviewed_delivery_exception(),
                    )
                    retried_receipt = engine.complete_turn(
                        "agent_erii",
                        "user_one",
                        "turn-retry",
                        "I will remember it.",
                        delivery_disposition=DeliveryDisposition.SHOWN_UNREVIEWED,
                        delivery_exception=_unreviewed_delivery_exception(),
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
                                delivery_disposition=(
                                    DeliveryDisposition.SHOWN_UNREVIEWED
                                ),
                                delivery_exception=_unreviewed_delivery_exception(),
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
                        delivery_exception={
                            "exception_record_version": "delivery-exception-record/v1",
                            "disposition": "shown_unreviewed",
                            "actor_kind": "host_policy",
                            "actor_id": "tests.import-visible-exchange/v1",
                            "reason_code": "preexisting_visible_exchange",
                            "decided_at": "2026-08-01T08:00:00+08:00",
                            "reply_attempt_number": None,
                        },
                        processing_channels=(),
                    )
                    turns = engine.list_turns("agent_erii", "user_one")

                    self.assertEqual(receipt.source_turn_id, "turn-one-shot")
                    self.assertEqual(len(turns), 1)
                    self.assertEqual(turns[0].status, TurnStatus.COMPLETED)
                    self.assertEqual(
                        turns[0].delivery_disposition,
                        DeliveryDisposition.SHOWN_UNREVIEWED,
                    )
                    self.assertEqual(
                        turns[0].review_record.reason_code.value,
                        "preexisting_visible_exchange",
                    )
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
                        delivery_exception={
                            **_unreviewed_delivery_exception(),
                            "reason_code": "preexisting_visible_exchange",
                        },
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
                delivery_exception={
                    **_unreviewed_delivery_exception(),
                    "reason_code": "preexisting_visible_exchange",
                },
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
