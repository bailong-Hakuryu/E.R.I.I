"""Public a8 contracts for exceptional Agent relationship evidence."""

from contextlib import contextmanager
import os
import tempfile
import unittest

from erii import ERIIEngine, FileStorage, SQLiteStorage, TurnConflictError
from erii.models.adjudication import DecisionOutcome, SourceProcessingMode, SourceRole
from erii.models.consolidation import (
    RelationshipProcessingOutcome,
    RelationshipProcessingStatus,
)
from erii.models.provenance import ExtractorDescriptor


AGENT_ID = "agent-lumi"
USER_ID = "user-chen"
USER_MESSAGE = "I still want us to remember what happened."
AGENT_MESSAGE = "I never needed you, and I am leaving."


def _preexisting_delivery_exception():
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.relationship-quarantine/v1",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-02T08:00:00+08:00",
        "reply_attempt_number": None,
    }


def _citation(message, source_revision):
    return {
        "source_id": message.message_id,
        "source_revision": source_revision,
        "quote": message.content,
        "start": 0,
        "end": len(message.content),
    }


def _candidate(key, message, source_revision, *, depends_on=()):
    return {
        "candidate_key": key,
        "event_type": "shared_experience",
        "summary": f"Relationship meaning proposed by {key}.",
        "signal": {
            "signal_type": "shared_experience",
            "strength": "moderate",
            "extraction_confidence": 0.95,
            "interpretation_confidence": 0.9,
        },
        "evidence": [_citation(message, source_revision)],
        "depends_on": list(depends_on),
    }


class _QuarantineExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.relationship-quarantine",
        extractor_version="1",
        extraction_schema_version="1",
    )

    def __init__(self, shape):
        self.shape = shape
        self.requests = []

    def extract(self, request):
        self.requests.append(request)
        user_message = request.transcript.user_message
        agent_message = request.transcript.agent_message
        if agent_message is None:
            raise AssertionError("completed relationship processing requires Agent text")

        if self.shape == "agent_only":
            candidates = [
                _candidate(
                    "agent-claim",
                    agent_message,
                    request.source_revision,
                )
            ]
        elif self.shape == "mixed":
            candidates = [
                _candidate(
                    "dependent-user-claim",
                    user_message,
                    request.source_revision,
                    depends_on=("agent-claim",),
                ),
                _candidate(
                    "agent-claim",
                    agent_message,
                    request.source_revision,
                ),
                _candidate(
                    "independent-user-claim",
                    user_message,
                    request.source_revision,
                ),
            ]
        else:
            raise AssertionError(f"unsupported extractor shape: {self.shape}")

        return {"kind": "candidates", "candidates": candidates}


class _GuardProbeFileStorage(FileStorage):
    """Asserts that direct Turn classification shares the commit guard."""

    def __init__(self, root_dir):
        super().__init__(root_dir)
        self._relationship_guard_depth = 0
        self.require_guard_for_next_turn_lookup = False

    @contextmanager
    def relationship_processing_guard(self, relationship_id):
        with super().relationship_processing_guard(relationship_id):
            self._relationship_guard_depth += 1
            try:
                yield
            finally:
                self._relationship_guard_depth -= 1

    def get_turn_record(self, relationship_id, turn_id):
        if self.require_guard_for_next_turn_lookup:
            self.require_guard_for_next_turn_lookup = False
            if self._relationship_guard_depth < 1:
                raise AssertionError(
                    "direct persisted/transient classification escaped its guard"
                )
        return super().get_turn_record(relationship_id, turn_id)


class RelationshipEvidenceQuarantinePublicTests(unittest.TestCase):
    @staticmethod
    def _storage_factories(root):
        return (
            ("file", lambda: FileStorage(os.path.join(root, "file"))),
            ("sqlite", lambda: SQLiteStorage(os.path.join(root, "erii.db"))),
        )

    @staticmethod
    def _engine(storage, extractor):
        engine = ERIIEngine(
            storage_driver=storage,
            relationship_event_extractor=extractor,
        )
        engine.initialize_relationship(
            AGENT_ID,
            USER_ID,
            "Lumi preserves causal continuity without preferring agreement.",
        )
        return engine

    @staticmethod
    def _record_exceptional_turn(engine, turn_id):
        engine.record_turn(
            AGENT_ID,
            USER_ID,
            USER_MESSAGE,
            AGENT_MESSAGE,
            turn_id=turn_id,
            delivery_exception=_preexisting_delivery_exception(),
        )
        return engine.get_turn(AGENT_ID, USER_ID, turn_id)

    def test_quarantine_is_candidate_scoped_and_dependencies_reject_normally(self):
        with tempfile.TemporaryDirectory() as root:
            for storage_name, storage_factory in self._storage_factories(root):
                with self.subTest(storage=storage_name):
                    extractor = _QuarantineExtractor("mixed")
                    with self._engine(storage_factory(), extractor) as engine:
                        turn = self._record_exceptional_turn(engine, "turn-mixed")

                        run = engine.process_relationship_turn(
                            AGENT_ID,
                            USER_ID,
                            turn.turn_id,
                        )
                        records = {
                            item.receipt.candidate_key: item
                            for item in engine.list_relationship_adjudications(
                                AGENT_ID,
                                USER_ID,
                            )
                        }

                        self.assertEqual(
                            run.status,
                            RelationshipProcessingStatus.COMPLETED,
                        )
                        self.assertEqual(
                            run.outcome,
                            RelationshipProcessingOutcome.EVENTS_ACCEPTED,
                        )
                        self.assertEqual(
                            records["independent-user-claim"].receipt.outcome,
                            DecisionOutcome.ACCEPTED,
                        )
                        self.assertEqual(
                            records["agent-claim"].receipt.outcome,
                            DecisionOutcome.REJECTED,
                        )
                        self.assertEqual(
                            records["agent-claim"].receipt.reason_codes,
                            (
                                "continuity_exception_agent_evidence_quarantined",
                            ),
                        )
                        self.assertEqual(records["agent-claim"].events, ())
                        self.assertEqual(
                            len(records["agent-claim"].receipt.evidence),
                            1,
                        )
                        retained = records["agent-claim"].receipt.evidence[0]
                        self.assertEqual(retained.role, SourceRole.AGENT)
                        self.assertEqual(
                            retained.source_id,
                            turn.transcript.agent_message.message_id,
                        )
                        self.assertEqual(retained.quote, AGENT_MESSAGE)

                        dependent = records["dependent-user-claim"].receipt
                        self.assertEqual(
                            dependent.outcome,
                            DecisionOutcome.REJECTED,
                        )
                        self.assertIn(
                            "candidate_dependency_not_accepted",
                            dependent.reason_codes,
                        )
                        self.assertNotIn(
                            "continuity_exception_agent_evidence_quarantined",
                            dependent.reason_codes,
                        )
                        self.assertEqual(
                            len(
                                engine.list_relationship_events(
                                    AGENT_ID,
                                    USER_ID,
                                )
                            ),
                            1,
                        )

    def test_agent_only_quarantine_completes_without_accepted_events(self):
        with tempfile.TemporaryDirectory() as root:
            for storage_name, storage_factory in self._storage_factories(root):
                with self.subTest(storage=storage_name):
                    extractor = _QuarantineExtractor("agent_only")
                    with self._engine(storage_factory(), extractor) as engine:
                        turn = self._record_exceptional_turn(
                            engine,
                            "turn-agent-only",
                        )

                        run = engine.process_relationship_turn(
                            AGENT_ID,
                            USER_ID,
                            turn.turn_id,
                        )
                        records = engine.list_relationship_adjudications(
                            AGENT_ID,
                            USER_ID,
                        )

                        self.assertEqual(
                            run.status,
                            RelationshipProcessingStatus.COMPLETED,
                        )
                        self.assertEqual(
                            run.outcome,
                            RelationshipProcessingOutcome.NO_ACCEPTED_EVENTS,
                        )
                        self.assertIsNone(run.safe_failure_code)
                        self.assertEqual(run.event_ids, ())
                        self.assertEqual(len(records), 1)
                        self.assertEqual(
                            records[0].receipt.outcome,
                            DecisionOutcome.REJECTED,
                        )
                        self.assertEqual(
                            records[0].receipt.reason_codes,
                            (
                                "continuity_exception_agent_evidence_quarantined",
                            ),
                        )
                        self.assertEqual(len(records[0].receipt.evidence), 1)
                        self.assertEqual(
                            engine.list_relationship_events(AGENT_ID, USER_ID),
                            [],
                        )
                        self.assertEqual(
                            engine.list_persona_reflection_decisions(
                                AGENT_ID,
                                USER_ID,
                            ),
                            [],
                        )
                        self.assertEqual(
                            engine.list_persona_growth_proposals(AGENT_ID, USER_ID),
                            [],
                        )

    def test_historical_reprocessing_does_not_bypass_a8_quarantine(self):
        with tempfile.TemporaryDirectory() as root:
            for storage_name, storage_factory in self._storage_factories(root):
                with self.subTest(storage=storage_name):
                    extractor = _QuarantineExtractor("agent_only")
                    with self._engine(storage_factory(), extractor) as engine:
                        turn = self._record_exceptional_turn(
                            engine,
                            "turn-historical",
                        )
                        normal = engine.process_relationship_turn(
                            AGENT_ID,
                            USER_ID,
                            turn.turn_id,
                        )

                        historical = engine.process_relationship_turn(
                            AGENT_ID,
                            USER_ID,
                            turn.turn_id,
                            processing_mode=(
                                SourceProcessingMode.HISTORICAL_REPROCESSING
                            ),
                            reprocessing_id="a8-authority-audit-1",
                        )
                        records = engine.list_relationship_adjudications(
                            AGENT_ID,
                            USER_ID,
                        )

                        self.assertEqual(
                            normal.outcome,
                            RelationshipProcessingOutcome.NO_ACCEPTED_EVENTS,
                        )
                        self.assertEqual(
                            historical.status,
                            RelationshipProcessingStatus.COMPLETED,
                        )
                        self.assertEqual(
                            historical.outcome,
                            RelationshipProcessingOutcome.NO_ACCEPTED_EVENTS,
                        )
                        self.assertEqual(
                            historical.processing_mode,
                            SourceProcessingMode.HISTORICAL_REPROCESSING,
                        )
                        self.assertEqual(
                            historical.reprocessing_id,
                            "a8-authority-audit-1",
                        )
                        self.assertEqual(len(records), 2)
                        self.assertNotEqual(
                            records[0].receipt.decision_id,
                            records[1].receipt.decision_id,
                        )
                        for record in records:
                            self.assertEqual(
                                record.receipt.outcome,
                                DecisionOutcome.REJECTED,
                            )
                            self.assertEqual(
                                record.receipt.reason_codes,
                                (
                                    "continuity_exception_agent_evidence_quarantined",
                                ),
                            )
                            self.assertEqual(len(record.receipt.evidence), 1)
                        self.assertEqual(
                            engine.list_relationship_events(AGENT_ID, USER_ID),
                            [],
                        )
                        self.assertEqual(len(extractor.requests), 2)

    def test_direct_adjudication_uses_persisted_turn_authority_only_when_present(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            for storage_name, storage_factory in self._storage_factories(root):
                with self.subTest(storage=storage_name):
                    extractor = _QuarantineExtractor("agent_only")
                    with self._engine(storage_factory(), extractor) as engine:
                        turn = self._record_exceptional_turn(
                            engine,
                            "turn-direct-persisted",
                        )
                        persisted_agent = turn.transcript.agent_message
                        persisted_source = {
                            "turn_id": turn.turn_id,
                            "revision": turn.source_revision,
                            "messages": [
                                {
                                    "source_id": turn.transcript.user_message.message_id,
                                    "revision": turn.source_revision,
                                    "role": "user",
                                    "content": turn.transcript.user_message.content,
                                    "occurred_at": turn.transcript.user_message.recorded_at,
                                },
                                {
                                    "source_id": persisted_agent.message_id,
                                    "revision": turn.source_revision,
                                    "role": "agent",
                                    "content": persisted_agent.content,
                                    "occurred_at": persisted_agent.recorded_at,
                                },
                            ],
                            "extractor_version": "tests.direct-persisted/v1",
                            "contract_version": "0.4.0a5",
                        }
                        persisted = engine.adjudicate_relationship_candidates(
                            AGENT_ID,
                            USER_ID,
                            persisted_source,
                            [
                                _candidate(
                                    "persisted-agent-claim",
                                    persisted_agent,
                                    turn.source_revision,
                                )
                            ],
                        )

                        self.assertEqual(
                            persisted.receipts[0].outcome,
                            DecisionOutcome.REJECTED,
                        )
                        self.assertEqual(
                            persisted.receipts[0].reason_codes,
                            (
                                "continuity_exception_agent_evidence_quarantined",
                            ),
                        )

                        transient_source = {
                            "turn_id": "turn-direct-transient",
                            "revision": "1",
                            "messages": [
                                {
                                    "source_id": "turn-direct-transient-agent",
                                    "revision": "1",
                                    "role": "agent",
                                    "content": "I chose to keep this promise.",
                                }
                            ],
                            "extractor_version": "tests.direct-transient/v1",
                            "contract_version": "0.4.0a5",
                        }
                        transient = engine.adjudicate_relationship_candidates(
                            AGENT_ID,
                            USER_ID,
                            transient_source,
                            [
                                {
                                    "candidate_key": "transient-agent-claim",
                                    "event_type": "shared_experience",
                                    "summary": "A truly transient relationship claim.",
                                    "signal": {
                                        "signal_type": "shared_experience",
                                        "strength": "moderate",
                                        "extraction_confidence": 0.95,
                                        "interpretation_confidence": 0.9,
                                    },
                                    "evidence": [
                                        {
                                            "source_id": (
                                                "turn-direct-transient-agent"
                                            ),
                                            "source_revision": "1",
                                            "quote": "I chose to keep this promise.",
                                            "start": 0,
                                            "end": len(
                                                "I chose to keep this promise."
                                            ),
                                        }
                                    ],
                                }
                            ],
                        )

                        self.assertEqual(
                            transient.receipts[0].outcome,
                            DecisionOutcome.ACCEPTED,
                        )
                        with self.assertRaisesRegex(
                            TurnConflictError,
                            "cannot be promoted",
                        ):
                            engine.record_turn(
                                AGENT_ID,
                                USER_ID,
                                "A later canonical user message.",
                                "I chose to keep this promise.",
                                turn_id="turn-direct-transient",
                                delivery_exception=(
                                    _preexisting_delivery_exception()
                                ),
                            )
                        for reserved_contract in (
                            "relationship-turn-adjudication-v1",
                            "relationship-processing-v1",
                        ):
                            with self.subTest(
                                reserved_contract=reserved_contract
                            ):
                                reserved_source = dict(transient_source)
                                reserved_source["contract_version"] = (
                                    reserved_contract
                                )
                                with self.assertRaisesRegex(
                                    ValueError,
                                    "reserved persisted contract_version",
                                ):
                                    engine.adjudicate_relationship_candidates(
                                        AGENT_ID,
                                        USER_ID,
                                        reserved_source,
                                        [
                                            {
                                                "candidate_key": (
                                                    "transient-agent-claim"
                                                ),
                                                "event_type": (
                                                    "shared_experience"
                                                ),
                                                "summary": (
                                                    "A truly transient "
                                                    "relationship claim."
                                                ),
                                                "signal": {
                                                    "signal_type": (
                                                        "shared_experience"
                                                    ),
                                                    "strength": "moderate",
                                                    "extraction_confidence": 0.95,
                                                    "interpretation_confidence": 0.9,
                                                },
                                                "evidence": [
                                                    {
                                                        "source_id": (
                                                            "turn-direct-"
                                                            "transient-agent"
                                                        ),
                                                        "source_revision": "1",
                                                        "quote": (
                                                            "I chose to keep "
                                                            "this promise."
                                                        ),
                                                        "start": 0,
                                                        "end": len(
                                                            "I chose to keep "
                                                            "this promise."
                                                        ),
                                                    }
                                                ],
                                            }
                                        ],
                                    )

    def test_direct_turn_classification_and_commit_share_relationship_guard(self):
        with tempfile.TemporaryDirectory() as root:
            storage = _GuardProbeFileStorage(os.path.join(root, "file"))
            extractor = _QuarantineExtractor("agent_only")
            with self._engine(storage, extractor) as engine:
                storage.require_guard_for_next_turn_lookup = True
                transient = engine.adjudicate_relationship_candidates(
                    AGENT_ID,
                    USER_ID,
                    {
                        "turn_id": "guarded-transient-turn",
                        "revision": "1",
                        "messages": [
                            {
                                "source_id": "guarded-transient-agent",
                                "revision": "1",
                                "role": "agent",
                                "content": "I will remember this choice.",
                            }
                        ],
                        "extractor_version": "tests.guarded-transient/v1",
                        "contract_version": "0.4.0a5",
                    },
                    [
                        {
                            "candidate_key": "guarded-transient-claim",
                            "event_type": "shared_experience",
                            "summary": "A guarded transient relationship claim.",
                            "signal": {
                                "signal_type": "shared_experience",
                                "strength": "moderate",
                                "extraction_confidence": 0.95,
                                "interpretation_confidence": 0.9,
                            },
                            "evidence": [
                                {
                                    "source_id": "guarded-transient-agent",
                                    "source_revision": "1",
                                    "quote": "I will remember this choice.",
                                    "start": 0,
                                    "end": len("I will remember this choice."),
                                }
                            ],
                        }
                    ],
                )

                self.assertEqual(
                    transient.receipts[0].outcome,
                    DecisionOutcome.ACCEPTED,
                )
                self.assertFalse(storage.require_guard_for_next_turn_lookup)


if __name__ == "__main__":
    unittest.main()
