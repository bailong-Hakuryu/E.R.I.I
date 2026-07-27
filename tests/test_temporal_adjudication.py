"""Contracts for evidence-backed temporal relationship adjudication."""

import tempfile
import unittest

from erii import DecisionOutcome, ERIIEngine, MemoryNode, MemoryType
from erii.core.adjudication import relationship_occurrence_fingerprint
from erii.models.temporal import (
    OpenLoopResolution,
    PromiseConditionConfirmation,
    PromiseResolution,
    PromiseSpec,
)


def source_turn(turn_id, messages):
    return {
        "turn_id": turn_id,
        "contract_version": "0.4.0a4",
        "extractor_version": "temporal-tests-v1",
        "messages": [
            {
                "source_id": f"{turn_id}-{index}",
                "role": role,
                "content": content,
                "occurred_at": "2026-07-28T10:00:00+00:00",
            }
            for index, (role, content) in enumerate(messages)
        ],
    }


def temporal_candidate(
    key,
    event_type,
    payload,
    turn,
    *,
    evidence_indexes=(0,),
    summary=None,
    signal_type="neutral",
    extraction_confidence=0.95,
    interpretation_confidence=0.95,
    occurrence_key=None,
    persona_reflection=None,
    growth_trigger="none",
    depends_on=(),
):
    return {
        "candidate_key": key,
        "event_type": event_type,
        "summary": summary or f"Temporal event {key}",
        "signal": {
            "signal_type": signal_type,
            "strength": "moderate",
            "extraction_confidence": extraction_confidence,
            "interpretation_confidence": interpretation_confidence,
        },
        "temporal_payload": payload,
        "evidence": [
            {
                "source_id": turn["messages"][index]["source_id"],
                "quote": turn["messages"][index]["content"],
            }
            for index in evidence_indexes
        ],
        "occurrence_key": occurrence_key or f"temporal:{key}",
        "persona_reflection": persona_reflection,
        "growth_trigger": growth_trigger,
        "depends_on": list(depends_on),
    }


class TemporalOccurrenceIdentityTest(unittest.TestCase):
    def test_legacy_occurrence_identity_is_unchanged_when_payload_is_absent(self):
        legacy = relationship_occurrence_fingerprint(
            "relationship-1",
            "promise",
            "I will bring the book.",
            "2026-07-28T10:00:00+00:00",
            "promise:book",
        )
        explicit_none = relationship_occurrence_fingerprint(
            "relationship-1",
            "promise",
            "I will bring the book.",
            "2026-07-28T10:00:00+00:00",
            "promise:book",
            temporal_payload=None,
        )

        self.assertEqual(legacy, explicit_none)

    def test_temporal_payload_distinguishes_same_summary_and_time(self):
        first = relationship_occurrence_fingerprint(
            "relationship-1",
            "promise_resolution",
            "The promise was fulfilled.",
            "2026-07-28T10:00:00+00:00",
            temporal_payload={
                "payload_type": "promise_resolution",
                "promise_event_id": "promise-1",
                "resolution_kind": "fulfilled",
            },
        )
        second = relationship_occurrence_fingerprint(
            "relationship-1",
            "promise_resolution",
            "The promise was fulfilled.",
            "2026-07-28T10:00:00+00:00",
            temporal_payload={
                "payload_type": "promise_resolution",
                "promise_event_id": "promise-2",
                "resolution_kind": "fulfilled",
            },
        )

        self.assertNotEqual(first, second)


class TemporalEvidenceAdjudicationTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.engine = ERIIEngine(storage_dir=self.root.name)
        self.engine.initialize_relationship(
            "agent_lumi",
            "user_chen",
            "Lumi is an original patient character.",
        )

    def tearDown(self):
        self.engine.close()
        self.root.cleanup()

    def submit(self, turn, proposed):
        result = self.engine.adjudicate_relationship_candidates(
            "agent_lumi",
            "user_chen",
            turn,
            [proposed],
        )
        return result.records[0]

    def create_promise(self, key, *, condition=None):
        turn = source_turn(
            f"turn-{key}",
            [("agent", f"I promise to complete {key}.")],
        )
        payload = {
            "payload_type": "promise",
            "responsible_parties": ["agent"],
            "action": f"complete {key}",
        }
        if condition is not None:
            payload["activation_condition"] = {
                "condition_id": condition,
                "description": f"when {condition} occurs",
            }
        record = self.submit(
            turn,
            temporal_candidate(
                key,
                "promise",
                payload,
                turn,
                signal_type="commitment",
            ),
        )
        self.assertEqual(record.receipt.outcome, DecisionOutcome.ACCEPTED)
        return record.events[0]

    def create_open_loop(self, key):
        turn = source_turn(f"turn-{key}", [("user", f"Let us continue {key} later.")])
        record = self.submit(
            turn,
            temporal_candidate(
                key,
                "open_loop",
                {
                    "payload_type": "open_loop",
                    "subject": key,
                    "expected_continuation": f"continue {key}",
                },
                turn,
            ),
        )
        self.assertEqual(record.receipt.outcome, DecisionOutcome.ACCEPTED)
        return record.events[0]

    def test_structured_promise_requires_each_responsible_party_as_evidence(self):
        missing_turn = source_turn(
            "turn-mutual-missing",
            [
                ("agent", "I promise to return the book."),
                ("user", "Thank you."),
            ],
        )
        missing_user = self.submit(
            missing_turn,
            temporal_candidate(
                "mutual-missing",
                "promise",
                {
                    "payload_type": "promise",
                    "responsible_parties": ["agent", "user"],
                    "action": "return the book together",
                },
                missing_turn,
                evidence_indexes=(0,),
                signal_type="commitment",
            ),
        )

        self.assertEqual(missing_user.receipt.outcome, DecisionOutcome.REJECTED)
        self.assertEqual(
            missing_user.receipt.reason_codes,
            ("promise_responsible_party_not_evidenced:user",),
        )

        accepted_turn = source_turn(
            "turn-mutual-accepted",
            [
                ("agent", "I promise to return the book."),
                ("user", "I promise to help return it."),
            ],
        )
        accepted = self.submit(
            accepted_turn,
            temporal_candidate(
                "mutual-accepted",
                "promise",
                {
                    "payload_type": "promise",
                    "responsible_parties": ["agent", "user"],
                    "action": "return the book together",
                },
                accepted_turn,
                evidence_indexes=(0, 1),
                signal_type="commitment",
            ),
        )

        self.assertEqual(accepted.receipt.outcome, DecisionOutcome.ACCEPTED)
        self.assertIsInstance(accepted.events[0].temporal_payload, PromiseSpec)
        self.assertEqual(dict(accepted.events[0].state_delta), {})

    def test_structured_promise_uses_higher_confidence_thresholds(self):
        turn = source_turn(
            "turn-low-promise",
            [("agent", "I promise to return the book.")],
        )
        low_extraction = self.submit(
            turn,
            temporal_candidate(
                "low-promise",
                "promise",
                {
                    "payload_type": "promise",
                    "responsible_parties": ["agent"],
                    "action": "return the book",
                },
                turn,
                signal_type="commitment",
                extraction_confidence=0.79,
            ),
        )

        self.assertEqual(low_extraction.receipt.outcome, DecisionOutcome.IGNORED)
        self.assertEqual(
            low_extraction.receipt.reason_codes,
            ("low_promise_extraction_confidence",),
        )

    def test_condition_confirmation_requires_matching_structured_promise(self):
        promise = self.create_promise("conditional", condition="first-snow")

        wrong_turn = source_turn(
            "turn-wrong-condition",
            [("user", "The rain started.")],
        )
        wrong = self.submit(
            wrong_turn,
            temporal_candidate(
                "wrong-condition",
                "promise_condition_confirmed",
                {
                    "payload_type": "promise_condition_confirmed",
                    "promise_event_id": promise.event_id,
                    "condition_id": "rain",
                },
                wrong_turn,
            ),
        )
        self.assertEqual(wrong.receipt.outcome, DecisionOutcome.REJECTED)
        self.assertEqual(wrong.receipt.reason_codes, ("promise_condition_id_mismatch",))

        confirmed_turn = source_turn(
            "turn-confirm-condition",
            [("user", "The first snow started.")],
        )
        confirmed = self.submit(
            confirmed_turn,
            temporal_candidate(
                "confirm-condition",
                "promise_condition_confirmed",
                {
                    "payload_type": "promise_condition_confirmed",
                    "promise_event_id": promise.event_id,
                    "condition_id": "first-snow",
                },
                confirmed_turn,
            ),
        )

        self.assertEqual(confirmed.receipt.outcome, DecisionOutcome.ACCEPTED)
        self.assertIsInstance(
            confirmed.events[0].temporal_payload,
            PromiseConditionConfirmation,
        )
        self.assertIn(
            promise.event_id,
            confirmed.events[0].metadata["adjudication"]["references"],
        )

        repeated_turn = source_turn(
            "turn-repeat-condition",
            [("user", "The first snow is still falling.")],
        )
        repeated = self.submit(
            repeated_turn,
            temporal_candidate(
                "repeat-condition",
                "promise_condition_confirmed",
                {
                    "payload_type": "promise_condition_confirmed",
                    "promise_event_id": promise.event_id,
                    "condition_id": "first-snow",
                },
                repeated_turn,
            ),
        )
        self.assertEqual(repeated.receipt.outcome, DecisionOutcome.REJECTED)
        self.assertEqual(
            repeated.receipt.reason_codes,
            ("promise_condition_already_confirmed",),
        )

    def test_promise_resolution_is_terminal_but_same_occurrence_corroborates(self):
        promise = self.create_promise("terminal")
        resolved_turn = source_turn(
            "turn-resolve-terminal",
            [("agent", "I completed terminal.")],
        )
        resolution = temporal_candidate(
            "resolve-terminal",
            "promise_resolution",
            {
                "payload_type": "promise_resolution",
                "promise_event_id": promise.event_id,
                "resolution_kind": "fulfilled",
            },
            resolved_turn,
            occurrence_key="resolution:terminal:fulfilled",
        )
        accepted = self.submit(resolved_turn, resolution)
        repeated = self.submit(resolved_turn, resolution)

        corroborating_turn = source_turn(
            "turn-corroborate-terminal",
            [("user", "I saw that terminal was completed.")],
        )
        corroborated = self.submit(
            corroborating_turn,
            temporal_candidate(
                "corroborate-terminal",
                "promise_resolution",
                {
                    "payload_type": "promise_resolution",
                    "promise_event_id": promise.event_id,
                    "resolution_kind": "fulfilled",
                },
                corroborating_turn,
                summary=resolution["summary"],
                occurrence_key="resolution:terminal:fulfilled",
            ),
        )

        conflicting_turn = source_turn(
            "turn-conflict-terminal",
            [("agent", "I cancelled terminal instead.")],
        )
        conflicting = self.submit(
            conflicting_turn,
            temporal_candidate(
                "conflict-terminal",
                "promise_resolution",
                {
                    "payload_type": "promise_resolution",
                    "promise_event_id": promise.event_id,
                    "resolution_kind": "cancelled",
                },
                conflicting_turn,
            ),
        )

        self.assertEqual(accepted.receipt.outcome, DecisionOutcome.ACCEPTED)
        self.assertIsInstance(accepted.events[0].temporal_payload, PromiseResolution)
        self.assertEqual(accepted.receipt.decision_id, repeated.receipt.decision_id)
        self.assertEqual(corroborated.receipt.outcome, DecisionOutcome.CORROBORATED)
        self.assertEqual(corroborated.receipt.related_event_id, accepted.events[0].event_id)
        self.assertEqual(conflicting.receipt.outcome, DecisionOutcome.REJECTED)
        self.assertEqual(conflicting.receipt.reason_codes, ("promise_already_resolved",))

    def test_temporal_lifecycle_requires_high_interpretation_confidence(self):
        promise = self.create_promise("low-resolution")
        turn = source_turn(
            "turn-low-resolution-evidence",
            [("user", "Hello.")],
        )
        ignored = self.submit(
            turn,
            temporal_candidate(
                "low-resolution",
                "promise_resolution",
                {
                    "payload_type": "promise_resolution",
                    "promise_event_id": promise.event_id,
                    "resolution_kind": "fulfilled",
                },
                turn,
                extraction_confidence=0.8,
                interpretation_confidence=0.0,
            ),
        )

        self.assertEqual(ignored.receipt.outcome, DecisionOutcome.IGNORED)
        self.assertEqual(
            ignored.receipt.reason_codes,
            ("low_temporal_interpretation_confidence",),
        )
        self.assertFalse(
            any(
                isinstance(event.temporal_payload, PromiseResolution)
                for event in self.engine.list_relationship_events(
                    "agent_lumi",
                    "user_chen",
                )
            )
        )

    def test_promise_and_open_loop_supersession_cycles_are_rejected(self):
        first_promise = self.create_promise("promise-a")
        second_promise = self.create_promise("promise-b")
        supersede_turn = source_turn(
            "turn-supersede-promise-a",
            [("agent", "Promise B replaces Promise A.")],
        )
        superseded = self.submit(
            supersede_turn,
            temporal_candidate(
                "supersede-promise-a",
                "promise_resolution",
                {
                    "payload_type": "promise_resolution",
                    "promise_event_id": first_promise.event_id,
                    "resolution_kind": "superseded",
                    "superseding_promise_event_id": second_promise.event_id,
                },
                supersede_turn,
            ),
        )
        cycle_turn = source_turn(
            "turn-cycle-promise-b",
            [("agent", "Promise A replaces Promise B.")],
        )
        cycle = self.submit(
            cycle_turn,
            temporal_candidate(
                "cycle-promise-b",
                "promise_resolution",
                {
                    "payload_type": "promise_resolution",
                    "promise_event_id": second_promise.event_id,
                    "resolution_kind": "superseded",
                    "superseding_promise_event_id": first_promise.event_id,
                },
                cycle_turn,
            ),
        )

        first_loop = self.create_open_loop("loop-a")
        second_loop = self.create_open_loop("loop-b")
        close_turn = source_turn(
            "turn-supersede-loop-a",
            [("user", "Loop B replaces Loop A.")],
        )
        closed = self.submit(
            close_turn,
            temporal_candidate(
                "supersede-loop-a",
                "open_loop_resolution",
                {
                    "payload_type": "open_loop_resolution",
                    "open_loop_event_id": first_loop.event_id,
                    "resolution_kind": "superseded",
                    "superseding_open_loop_event_id": second_loop.event_id,
                },
                close_turn,
            ),
        )
        loop_cycle_turn = source_turn(
            "turn-cycle-loop-b",
            [("user", "Loop A replaces Loop B.")],
        )
        loop_cycle = self.submit(
            loop_cycle_turn,
            temporal_candidate(
                "cycle-loop-b",
                "open_loop_resolution",
                {
                    "payload_type": "open_loop_resolution",
                    "open_loop_event_id": second_loop.event_id,
                    "resolution_kind": "superseded",
                    "superseding_open_loop_event_id": first_loop.event_id,
                },
                loop_cycle_turn,
            ),
        )

        self.assertEqual(superseded.receipt.outcome, DecisionOutcome.ACCEPTED)
        self.assertEqual(cycle.receipt.reason_codes, ("promise_supersession_cycle",))
        self.assertEqual(closed.receipt.outcome, DecisionOutcome.ACCEPTED)
        self.assertIsInstance(closed.events[0].temporal_payload, OpenLoopResolution)
        self.assertEqual(loop_cycle.receipt.reason_codes, ("open_loop_supersession_cycle",))

    def test_open_loop_lifecycle_cannot_smuggle_reflection_or_growth(self):
        turn = source_turn(
            "turn-open-loop-growth",
            [("user", "Let us continue this later.")],
        )
        rejected = self.submit(
            turn,
            temporal_candidate(
                "open-loop-growth",
                "open_loop",
                {
                    "payload_type": "open_loop",
                    "subject": "continue this later",
                },
                turn,
                persona_reflection="This permanently changes who I am.",
                growth_trigger="pivotal",
            ),
        )

        self.assertEqual(rejected.receipt.outcome, DecisionOutcome.REJECTED)
        self.assertEqual(
            rejected.receipt.reason_codes,
            (
                "temporal_lifecycle_cannot_include_persona_reflection",
                "temporal_lifecycle_cannot_trigger_growth",
            ),
        )

    def test_structured_promise_cannot_smuggle_reflection_or_growth(self):
        turn = source_turn(
            "turn-promise-growth",
            [("agent", "I promise to return tomorrow.")],
        )
        rejected = self.submit(
            turn,
            temporal_candidate(
                "promise-growth",
                "promise",
                {
                    "payload_type": "promise",
                    "responsible_parties": ["agent"],
                    "action": "return tomorrow",
                },
                turn,
                signal_type="commitment",
                persona_reflection="This permanently changes who I am.",
                growth_trigger="pivotal",
            ),
        )

        self.assertEqual(rejected.receipt.outcome, DecisionOutcome.REJECTED)
        self.assertEqual(
            rejected.receipt.reason_codes,
            (
                "temporal_lifecycle_cannot_include_persona_reflection",
                "temporal_lifecycle_cannot_trigger_growth",
            ),
        )

    def test_open_loop_origin_must_be_active_and_can_only_be_formalized_once(self):
        missing_turn = source_turn(
            "turn-missing-origin",
            [("user", "Let us continue the letter later.")],
        )
        missing = self.submit(
            missing_turn,
            temporal_candidate(
                "missing-origin",
                "open_loop",
                {
                    "payload_type": "open_loop",
                    "subject": "continue the letter",
                    "origin_memory_node_id": "missing-memory",
                },
                missing_turn,
            ),
        )
        self.assertEqual(missing.receipt.outcome, DecisionOutcome.REJECTED)
        self.assertEqual(
            missing.receipt.reason_codes,
            ("open_loop_origin_memory_not_found",),
        )

        self.engine.storage.save_nodes(
            "agent_lumi",
            "user_chen",
            [
                MemoryNode(
                    node_id="legacy-letter",
                    agent_id="agent_lumi",
                    user_id="user_chen",
                    node_type=MemoryType.THOUGHT,
                    content="Ask about the unfinished letter.",
                    is_unresolved=True,
                )
            ],
        )
        accepted_turn = source_turn(
            "turn-formal-origin",
            [("user", "Let us continue the letter later.")],
        )
        accepted = self.submit(
            accepted_turn,
            temporal_candidate(
                "formal-origin",
                "open_loop",
                {
                    "payload_type": "open_loop",
                    "subject": "continue the letter",
                    "origin_memory_node_id": "legacy-letter",
                },
                accepted_turn,
            ),
        )
        self.assertEqual(accepted.receipt.outcome, DecisionOutcome.ACCEPTED)

        duplicate_turn = source_turn(
            "turn-duplicate-origin",
            [("user", "The letter is still unfinished.")],
        )
        duplicate = self.submit(
            duplicate_turn,
            temporal_candidate(
                "duplicate-origin",
                "open_loop",
                {
                    "payload_type": "open_loop",
                    "subject": "the same unfinished letter",
                    "origin_memory_node_id": "legacy-letter",
                },
                duplicate_turn,
            ),
        )
        self.assertEqual(duplicate.receipt.outcome, DecisionOutcome.REJECTED)
        self.assertEqual(
            duplicate.receipt.reason_codes,
            ("open_loop_origin_already_formalized",),
        )

    def test_supersession_requires_a_persisted_successor_event_id(self):
        original = self.create_promise("persisted-target")
        turn = source_turn(
            "turn-same-batch-successor",
            [("agent", "I replace the old promise with a new one.")],
        )
        successor = temporal_candidate(
            "successor-candidate-key",
            "promise",
            {
                "payload_type": "promise",
                "responsible_parties": ["agent"],
                "action": "fulfil the replacement",
            },
            turn,
            signal_type="commitment",
        )
        resolution = temporal_candidate(
            "resolution-using-candidate-key",
            "promise_resolution",
            {
                "payload_type": "promise_resolution",
                "promise_event_id": original.event_id,
                "resolution_kind": "superseded",
                "superseding_promise_event_id": "successor-candidate-key",
            },
            turn,
            depends_on=("successor-candidate-key",),
        )

        result = self.engine.adjudicate_relationship_candidates(
            "agent_lumi",
            "user_chen",
            turn,
            [successor, resolution],
        )

        self.assertEqual(result.records[0].receipt.outcome, DecisionOutcome.ACCEPTED)
        self.assertEqual(result.records[1].receipt.outcome, DecisionOutcome.REJECTED)
        self.assertEqual(
            result.records[1].receipt.reason_codes,
            ("superseding_promise_target_not_found",),
        )


if __name__ == "__main__":
    unittest.main()
