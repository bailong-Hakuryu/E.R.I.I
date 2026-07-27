"""Contracts for a4 Promise and Open Loop durable/candidate models."""

from dataclasses import FrozenInstanceError
import math
import unittest

from pydantic import ValidationError

from erii.models.adjudication import RelationshipEventCandidate
from erii.models.relationship import RelationshipEvent, RelationshipEventType
from erii.models.temporal import (
    OpenLoopResolution,
    OpenLoopResolutionKind,
    OpenLoopSpec,
    PromiseCondition,
    PromiseConditionConfirmation,
    PromiseResolution,
    PromiseResolutionKind,
    PromiseResponsibleParty,
    PromiseSpec,
    WorldMoment,
    temporal_payload_from_dict,
    temporal_payload_to_dict,
)


def evidence_candidate(event_type, temporal_payload=None, signal_type="neutral"):
    return {
        "candidate_key": "temporal-candidate",
        "event_type": event_type,
        "summary": "A temporal relationship event.",
        "signal": {
            "signal_type": signal_type,
            "strength": "moderate",
            "extraction_confidence": 0.95,
            "interpretation_confidence": 0.95,
        },
        "temporal_payload": temporal_payload,
        "evidence": [{"source_id": "message-1", "quote": "direct evidence"}],
    }


class DurableTemporalModelTest(unittest.TestCase):
    def test_world_moment_is_finite_frozen_and_round_trips(self):
        moment = WorldMoment("story-clock", "third winter", 3)

        self.assertEqual(moment.order_value, 3.0)
        self.assertEqual(WorldMoment.from_dict(moment.to_dict()), moment)
        with self.assertRaises(FrozenInstanceError):
            moment.clock_id = "changed"
        for invalid in (math.inf, -math.inf, math.nan, True):
            with self.subTest(order_value=invalid):
                with self.assertRaises(ValueError):
                    WorldMoment("story-clock", "invalid", invalid)

    def test_promise_parties_are_unique_nonempty_and_canonical(self):
        mutual = PromiseSpec(
            responsible_parties=("user", "agent"),
            action="Meet beneath the old tree.",
            due_at=WorldMoment("story-clock", "day 10", 10),
            activation_condition=PromiseCondition(
                "condition-return",
                "The traveller returns.",
            ),
        )
        timeless = PromiseSpec(
            responsible_parties=("agent",),
            action="Remember this conversation.",
        )

        self.assertEqual(
            mutual.responsible_parties,
            (PromiseResponsibleParty.AGENT, PromiseResponsibleParty.USER),
        )
        self.assertIsNotNone(mutual.due_at)
        self.assertIsNotNone(mutual.activation_condition)
        self.assertIsNone(timeless.due_at)
        self.assertIsNone(timeless.activation_condition)
        with self.assertRaises(ValueError):
            PromiseSpec((), "No responsible party.")
        with self.assertRaises(ValueError):
            PromiseSpec(("agent", "agent"), "Duplicate party.")

    def test_every_payload_round_trips_through_the_discriminator(self):
        payloads = (
            PromiseSpec(("agent",), "Return tomorrow."),
            PromiseConditionConfirmation(
                "promise-1",
                "condition-return",
                WorldMoment("story-clock", "day 2", 2),
            ),
            PromiseResolution(
                "promise-1",
                PromiseResolutionKind.SUPERSEDED,
                superseding_promise_event_id="promise-2",
            ),
            OpenLoopSpec(
                subject="An unfinished story",
                expected_continuation="Ask what happened next.",
                origin_memory_node_id="legacy-memory-1",
            ),
            OpenLoopResolution(
                "loop-1",
                OpenLoopResolutionKind.SUPERSEDED,
                superseding_open_loop_event_id="loop-2",
            ),
        )

        for payload in payloads:
            with self.subTest(payload=type(payload).__name__):
                encoded = temporal_payload_to_dict(payload)
                self.assertIn("payload_type", encoded)
                self.assertEqual(temporal_payload_from_dict(encoded), payload)

        with self.assertRaisesRegex(ValueError, "requires payload_type"):
            temporal_payload_from_dict({"action": "missing discriminator"})
        with self.assertRaisesRegex(ValueError, "unsupported"):
            temporal_payload_from_dict({"payload_type": "unknown"})
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            temporal_payload_from_dict(
                {
                    "payload_type": "open_loop",
                    "subject": "valid",
                    "responsible_party": "must not exist",
                }
            )
        malformed_nested_payloads = (
            {
                "payload_type": "promise",
                "responsible_parties": ["agent"],
                "action": "Return.",
                "due_at": {},
            },
            {
                "payload_type": "promise",
                "responsible_parties": ["agent"],
                "action": "Return.",
                "activation_condition": {},
            },
            {
                "payload_type": "promise_condition_confirmed",
                "promise_event_id": "promise-1",
                "condition_id": "condition-1",
                "confirmed_at": {},
            },
            {
                "payload_type": "promise_resolution",
                "promise_event_id": "promise-1",
                "resolution_kind": "fulfilled",
                "resolved_at": {},
            },
        )
        for malformed in malformed_nested_payloads:
            with self.subTest(malformed=malformed["payload_type"]):
                with self.assertRaises(ValueError):
                    temporal_payload_from_dict(malformed)

    def test_supersession_references_are_strict(self):
        with self.assertRaisesRegex(ValueError, "requires superseding"):
            PromiseResolution("promise-1", "superseded")
        with self.assertRaisesRegex(ValueError, "only valid"):
            PromiseResolution(
                "promise-1",
                "fulfilled",
                superseding_promise_event_id="promise-2",
            )
        with self.assertRaisesRegex(ValueError, "cannot supersede itself"):
            OpenLoopResolution(
                "loop-1",
                "superseded",
                superseding_open_loop_event_id="loop-1",
            )


class TemporalRelationshipEventTest(unittest.TestCase):
    def event(self, event_type, payload=None, **updates):
        values = {
            "event_id": f"event-{event_type}",
            "relationship_id": "relationship-1",
            "event_type": event_type,
            "content": "Temporal event.",
            "temporal_payload": payload,
            "recorded_at": "2026-07-28T00:00:00+00:00",
        }
        values.update(updates)
        return RelationshipEvent(**values)

    def test_legacy_promise_without_payload_remains_compatible(self):
        old_data = {
            "event_id": "legacy-promise",
            "relationship_id": "relationship-1",
            "event_type": "promise",
            "content": "A legacy generic promise.",
            "state_delta": {"trust": 0.05},
            "belief_updates": [],
            "metadata": {},
            "occurred_at": None,
            "recorded_at": "2026-07-27T00:00:00+00:00",
        }

        restored = RelationshipEvent.from_dict(old_data)

        self.assertIsNone(restored.temporal_payload)
        self.assertEqual(restored.state_delta["trust"], 0.05)
        self.assertEqual(RelationshipEvent.from_dict(restored.to_dict()), restored)

    def test_event_type_and_payload_are_strictly_paired(self):
        valid = (
            ("promise", PromiseSpec(("agent",), "Return.")),
            (
                "promise_condition_confirmed",
                PromiseConditionConfirmation("promise-1", "condition-1"),
            ),
            (
                "promise_resolution",
                PromiseResolution("promise-1", "fulfilled"),
            ),
            ("open_loop", OpenLoopSpec(subject="Finish the story.")),
            (
                "open_loop_resolution",
                OpenLoopResolution("loop-1", "completed"),
            ),
        )
        for event_type, payload in valid:
            with self.subTest(event_type=event_type):
                event = self.event(event_type, payload)
                restored = RelationshipEvent.from_dict(event.to_dict())
                self.assertEqual(restored, event)

        for event_type in (
            "promise_condition_confirmed",
            "promise_resolution",
            "open_loop",
            "open_loop_resolution",
        ):
            with self.subTest(missing_payload=event_type):
                with self.assertRaisesRegex(ValueError, "requires"):
                    self.event(event_type)
        with self.assertRaisesRegex(ValueError, "requires"):
            self.event("open_loop", PromiseSpec(("agent",), "Wrong type."))
        with self.assertRaisesRegex(ValueError, "cannot contain"):
            self.event("observation", OpenLoopSpec(subject="Not allowed."))

    def test_typed_temporal_events_cannot_hide_relationship_mutations(self):
        payload = PromiseSpec(("agent",), "Return.")
        with self.assertRaisesRegex(ValueError, "cannot contain state_delta"):
            self.event("promise", payload, state_delta={"trust": 0.05})
        with self.assertRaisesRegex(ValueError, "cannot contain state_delta"):
            self.event(
                "promise",
                payload,
                belief_updates=[{"key": "promise", "value": True}],
            )

    def test_idempotent_payload_comparison_includes_temporal_structure(self):
        first = self.event("promise", PromiseSpec(("agent",), "Return."))
        repeated = self.event("promise", PromiseSpec(("agent",), "Return."))
        changed = self.event("promise", PromiseSpec(("agent",), "Stay."))

        self.assertTrue(first.same_payload_as(repeated))
        self.assertFalse(first.same_payload_as(changed))


class TemporalCandidateBoundaryTest(unittest.TestCase):
    def test_structured_promise_requires_commitment_and_canonicalizes_parties(self):
        raw = evidence_candidate(
            "promise",
            {
                "payload_type": "promise",
                "responsible_parties": ["user", "agent"],
                "action": "Return before winter ends.",
            },
            signal_type="gratitude",
        )
        with self.assertRaisesRegex(ValidationError, "commitment"):
            RelationshipEventCandidate.model_validate(raw)

        raw["signal"]["signal_type"] = "commitment"
        parsed = RelationshipEventCandidate.model_validate(raw)
        durable = parsed.temporal_payload.to_durable()

        self.assertEqual(
            parsed.temporal_payload.responsible_parties,
            [PromiseResponsibleParty.AGENT, PromiseResponsibleParty.USER],
        )
        self.assertEqual(
            durable.responsible_parties,
            (PromiseResponsibleParty.AGENT, PromiseResponsibleParty.USER),
        )

        raw["temporal_payload"]["due_at"] = {
            "clock_id": "story-clock",
            "display_value": "not comparable",
            "order_value": True,
        }
        with self.assertRaisesRegex(ValidationError, "numeric"):
            RelationshipEventCandidate.model_validate(raw)

    def test_candidate_payload_union_and_event_type_pairing_are_strict(self):
        parsed = RelationshipEventCandidate.model_validate(
            evidence_candidate(
                "open_loop",
                {
                    "payload_type": "open_loop",
                    "subject": "An unfinished question.",
                    "expected_continuation": "Answer it later.",
                    "origin_memory_node_id": "memory-1",
                },
            )
        )
        self.assertEqual(parsed.temporal_payload.to_durable().subject, "An unfinished question.")

        with self.assertRaises(ValidationError):
            RelationshipEventCandidate.model_validate(
                evidence_candidate("open_loop", None)
            )
        with self.assertRaises(ValidationError):
            RelationshipEventCandidate.model_validate(
                evidence_candidate(
                    "open_loop",
                    {
                        "payload_type": "promise_resolution",
                        "promise_event_id": "promise-1",
                        "resolution_kind": "fulfilled",
                    },
                )
            )
        with self.assertRaises(ValidationError):
            RelationshipEventCandidate.model_validate(
                evidence_candidate(
                    "observation",
                    {
                        "payload_type": "open_loop",
                        "subject": "Not valid on an observation.",
                    },
                )
            )

    def test_legacy_promise_candidate_without_payload_remains_parseable(self):
        parsed = RelationshipEventCandidate.model_validate(
            evidence_candidate("promise", None, signal_type="gratitude")
        )
        self.assertEqual(parsed.event_type, RelationshipEventType.PROMISE)
        self.assertIsNone(parsed.temporal_payload)


if __name__ == "__main__":
    unittest.main()
