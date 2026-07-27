"""Contracts for alpha.4 temporal signal derivation and recall integration."""

from datetime import datetime, timedelta
import shutil
import tempfile
import unittest

from pydantic import ValidationError

from erii import ERIIEngine, MemoryNode, MemoryType
from erii.core.temporal import RecallSignalDeriver
from erii.models.recall import (
    RecallBudget,
    RecallOptions,
    RecallRequest,
    RecallSignalAuthority,
    RecallSignalProjection,
    RecallSignalReason,
    RecallSignalType,
    RecallTemporalContext,
    WorldTime,
)
from erii.models.relationship import RelationshipEvent, RelationshipEventType
from erii.models.temporal import (
    OpenLoopResolution,
    OpenLoopResolutionKind,
    OpenLoopSpec,
    PromiseCondition,
    PromiseConditionConfirmation,
    PromiseResolution,
    PromiseResolutionKind,
    PromiseSpec,
    WorldMoment,
)


def _event(event_id, event_type, payload, order):
    return RelationshipEvent(
        event_id=event_id,
        relationship_id="relationship-lumi-user",
        event_type=event_type,
        content=f"history {event_id}",
        temporal_payload=payload,
        recorded_at=f"2026-07-28T00:00:{order:02d}+00:00",
    )


def _promise(event_id="promise-tea", due_order=10.0, condition=None, order=1):
    due_at = (
        WorldMoment(
            clock_id="story-v1",
            display_value=f"story day {due_order:g}",
            order_value=due_order,
        )
        if due_order is not None
        else None
    )
    return _event(
        event_id,
        RelationshipEventType.PROMISE,
        PromiseSpec(
            responsible_parties=("agent",),
            action="make jasmine tea",
            due_at=due_at,
            activation_condition=condition,
        ),
        order,
    )


class RecallSignalModelTest(unittest.TestCase):
    def test_world_time_rejects_boolean_order_values(self):
        normalized = WorldTime(
            clock_id=" story-v1 ",
            display_value=" day 10 ",
            order_value=10,
        )
        self.assertEqual(normalized.clock_id, "story-v1")
        self.assertEqual(normalized.display_value, "day 10")
        with self.assertRaisesRegex(ValidationError, "non-empty"):
            WorldTime(
                clock_id="   ",
                display_value="day 10",
                order_value=10,
            )
        with self.assertRaisesRegex(ValidationError, "numeric"):
            WorldTime(
                clock_id="story-v1",
                display_value="not a numeric position",
                order_value=True,
            )

    def test_provenance_is_strict_for_formal_and_legacy_signals(self):
        common = {
            "projection_id": "signal-1",
            "source_id": "source-1",
            "source_kind": "relationship_event",
            "visibility": "agent_private",
            "selection_reason": "fixture",
            "signal_type": "open_loop",
            "summary": "unfinished",
            "subject_id": "source-1",
            "reason": "unresolved_formal_loop",
        }
        with self.assertRaisesRegex(ValidationError, "event provenance"):
            RecallSignalProjection(
                **common,
                authority="formal_relationship_history",
            )
        with self.assertRaisesRegex(ValidationError, "memory provenance"):
            RecallSignalProjection(
                **{**common, "reason": "legacy_unresolved_flag"},
                authority="legacy_unresolved_memory",
            )

    def test_authority_reason_subject_and_condition_deadline_are_consistent(self):
        common = {
            "projection_id": "signal-1",
            "source_id": "loop-1",
            "source_kind": "relationship_event",
            "visibility": "agent_private",
            "selection_reason": "fixture",
            "signal_type": "open_loop",
            "summary": "unfinished",
            "subject_id": "loop-1",
            "authority": "formal_relationship_history",
            "source_event_ids": ("loop-1",),
        }
        with self.assertRaisesRegex(ValidationError, "formal open_loop"):
            RecallSignalProjection(
                **common,
                reason="legacy_unresolved_flag",
            )
        with self.assertRaisesRegex(ValidationError, "identify its subject"):
            RecallSignalProjection(
                **{**common, "source_id": "different"},
                reason="unresolved_formal_loop",
            )
        with self.assertRaisesRegex(ValidationError, "cannot carry a deadline"):
            RecallSignalProjection(
                **{
                    **common,
                    "signal_type": "promise_due",
                    "reason": "condition_confirmed",
                    "condition_id": "snow",
                    "due_world_time": WorldTime(
                        clock_id="story-v1",
                        display_value="day 10",
                        order_value=10,
                    ),
                    "clock_id": "story-v1",
                }
            )
        with self.assertRaisesRegex(ValidationError, "confirmation event"):
            RecallSignalProjection(
                **{
                    **common,
                    "signal_type": "promise_due",
                    "reason": "condition_confirmed",
                    "condition_id": "snow",
                }
            )


class RecallSignalDeriverTest(unittest.TestCase):
    def test_same_clock_deadline_comparison_is_exact_and_deterministic(self):
        promise = _promise()
        before = RecallSignalDeriver.derive(
            (promise,),
            WorldTime(clock_id="story-v1", display_value="day 9", order_value=9),
        )
        due = RecallSignalDeriver.derive(
            (promise,),
            WorldTime(clock_id="story-v1", display_value="day 10", order_value=10),
        )
        overdue = RecallSignalDeriver.derive(
            (promise,),
            WorldTime(clock_id="story-v1", display_value="day 11", order_value=11),
        )

        self.assertEqual(before, ())
        self.assertEqual(due[0].signal_type, RecallSignalType.PROMISE_DUE)
        self.assertEqual(due[0].reason, RecallSignalReason.AT_DEADLINE)
        self.assertEqual(overdue[0].signal_type, RecallSignalType.PROMISE_OVERDUE)
        self.assertIn("not a breach finding", overdue[0].summary)
        self.assertEqual(
            overdue[0].stable_json(),
            RecallSignalDeriver.derive(
                (promise,),
                WorldTime(
                    clock_id="story-v1",
                    display_value="day 11",
                    order_value=11,
                ),
            )[0].stable_json(),
        )

    def test_missing_or_different_clock_never_uses_display_or_wall_time(self):
        promise = _promise()

        self.assertEqual(RecallSignalDeriver.derive((promise,), None), ())
        self.assertEqual(
            RecallSignalDeriver.derive(
                (promise,),
                WorldTime(
                    clock_id="other-story",
                    display_value="story day 10",
                    order_value=10,
                ),
            ),
            (),
        )
        self.assertEqual(
            RecallSignalDeriver.derive(
                (promise,),
                WorldTime(clock_id="story-v1", display_value="story day 10"),
            ),
            (),
        )

    def test_matching_condition_derives_due_and_resolution_closes_it(self):
        condition = PromiseCondition(condition_id="snow", description="snow begins")
        promise = _promise(due_order=None, condition=condition)
        wrong_confirmation = _event(
            "confirmation-wrong",
            RelationshipEventType.PROMISE_CONDITION_CONFIRMED,
            PromiseConditionConfirmation(
                promise_event_id=promise.event_id,
                condition_id="rain",
            ),
            2,
        )
        confirmation = _event(
            "confirmation-snow",
            RelationshipEventType.PROMISE_CONDITION_CONFIRMED,
            PromiseConditionConfirmation(
                promise_event_id=promise.event_id,
                condition_id="snow",
            ),
            3,
        )
        resolution = _event(
            "resolution-tea",
            RelationshipEventType.PROMISE_RESOLUTION,
            PromiseResolution(
                promise_event_id=promise.event_id,
                resolution_kind=PromiseResolutionKind.FULFILLED,
            ),
            4,
        )

        self.assertEqual(
            RecallSignalDeriver.derive((promise, wrong_confirmation), None),
            (),
        )
        due = RecallSignalDeriver.derive(
            (promise, wrong_confirmation, confirmation),
            None,
        )
        self.assertEqual(due[0].reason, RecallSignalReason.CONDITION_CONFIRMED)
        self.assertEqual(
            due[0].source_event_ids,
            (promise.event_id, confirmation.event_id),
        )
        self.assertEqual(
            RecallSignalDeriver.derive(
                (promise, wrong_confirmation, confirmation, resolution),
                None,
            ),
            (),
        )

    def test_confirmed_condition_does_not_bypass_an_incomparable_deadline(self):
        condition = PromiseCondition(condition_id="snow", description="snow begins")
        promise = _promise(condition=condition)
        confirmation = _event(
            "confirmation-snow-with-deadline",
            RelationshipEventType.PROMISE_CONDITION_CONFIRMED,
            PromiseConditionConfirmation(
                promise_event_id=promise.event_id,
                condition_id="snow",
            ),
            2,
        )
        history = (promise, confirmation)

        self.assertEqual(RecallSignalDeriver.derive(history, None), ())
        self.assertEqual(
            RecallSignalDeriver.derive(
                history,
                WorldTime(clock_id="other", display_value="day 10", order_value=10),
            ),
            (),
        )
        self.assertEqual(
            RecallSignalDeriver.derive(
                history,
                WorldTime(clock_id="story-v1", display_value="day 9", order_value=9),
            ),
            (),
        )
        self.assertEqual(
            RecallSignalDeriver.derive(
                history,
                WorldTime(clock_id="story-v1", display_value="day 10", order_value=10),
            )[0].signal_type,
            RecallSignalType.PROMISE_DUE,
        )
        self.assertEqual(
            RecallSignalDeriver.derive(
                history,
                WorldTime(clock_id="story-v1", display_value="day 11", order_value=11),
            )[0].signal_type,
            RecallSignalType.PROMISE_OVERDUE,
        )

    def test_formal_open_loop_resolution_and_legacy_deduplication(self):
        formal = _event(
            "loop-snow",
            RelationshipEventType.OPEN_LOOP,
            OpenLoopSpec(
                subject="finish our snow story",
                expected_continuation="return to the ending together",
                origin_memory_node_id="legacy-snow",
            ),
            1,
        )
        legacy_same = MemoryNode(
            node_id="legacy-snow",
            agent_id="lumi",
            user_id="user",
            node_type=MemoryType.THOUGHT,
            content="unfinished snow story",
            is_unresolved=True,
        )
        legacy_other = MemoryNode(
            node_id="legacy-tea",
            agent_id="lumi",
            user_id="user",
            node_type=MemoryType.THOUGHT,
            content="unfinished tea question",
            is_unresolved=True,
        )

        signals = RecallSignalDeriver.derive(
            (formal,),
            None,
            (legacy_same, legacy_other),
        )
        self.assertEqual(len(signals), 2)
        self.assertEqual(
            signals[0].authority,
            RecallSignalAuthority.FORMAL_RELATIONSHIP_HISTORY,
        )
        self.assertEqual(
            signals[1].authority,
            RecallSignalAuthority.LEGACY_UNRESOLVED_MEMORY,
        )
        self.assertEqual(signals[1].source_memory_ids, ("legacy-tea",))

        resolution = _event(
            "loop-snow-resolution",
            RelationshipEventType.OPEN_LOOP_RESOLUTION,
            OpenLoopResolution(
                open_loop_event_id=formal.event_id,
                resolution_kind=OpenLoopResolutionKind.COMPLETED,
            ),
            2,
        )
        resolved = RecallSignalDeriver.derive(
            (formal, resolution),
            None,
            (legacy_same,),
        )
        self.assertEqual(resolved, ())

    def test_derivation_does_not_mutate_events_or_legacy_nodes(self):
        promise = _promise()
        node = MemoryNode(
            node_id="legacy-question",
            agent_id="lumi",
            user_id="user",
            node_type=MemoryType.THOUGHT,
            content="unfinished question",
            is_unresolved=True,
        )
        event_before = promise.to_dict()
        node_before = node.to_dict()

        RecallSignalDeriver.derive(
            (promise,),
            WorldTime(clock_id="story-v1", display_value="day 11", order_value=11),
            (node,),
        )

        self.assertEqual(promise.to_dict(), event_before)
        self.assertEqual(node.to_dict(), node_before)

    def test_resolution_projection_does_not_depend_on_recorded_at_sort_order(self):
        promise = _event(
            "promise-backfilled",
            RelationshipEventType.PROMISE,
            PromiseSpec(
                responsible_parties=("agent",),
                action="bring the paper crane",
                due_at=WorldMoment("story-v1", "story day 10", 10),
            ),
            20,
        )
        resolution = _event(
            "resolution-backfilled",
            RelationshipEventType.PROMISE_RESOLUTION,
            PromiseResolution(
                promise_event_id=promise.event_id,
                resolution_kind=PromiseResolutionKind.FULFILLED,
            ),
            10,
        )

        signals = RecallSignalDeriver.derive(
            (promise, resolution),
            WorldTime(clock_id="story-v1", display_value="day 11", order_value=11),
        )

        self.assertEqual(signals, ())

    def test_legacy_unresolved_flag_no_longer_changes_decay_weight(self):
        last_access = (datetime.now() - timedelta(days=30)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        common = {
            "agent_id": "lumi",
            "user_id": "user",
            "node_type": MemoryType.THOUGHT,
            "content": "same memory",
            "base_importance": 0.5,
            "last_accessed_at": last_access,
        }
        unresolved = MemoryNode(
            node_id="unresolved",
            is_unresolved=True,
            **common,
        )
        ordinary = MemoryNode(
            node_id="ordinary",
            is_unresolved=False,
            **common,
        )

        self.assertEqual(
            unresolved.calculate_effective_weight(),
            ordinary.calculate_effective_weight(),
        )


class StructuredTemporalRecallTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.engine = ERIIEngine(storage_dir=self.root)
        self.profile = self.engine.initialize_relationship(
            "lumi",
            "user",
            "Lumi is patient and keeps promises.",
        )

    def tearDown(self):
        self.engine.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _append(self, event):
        event = RelationshipEvent.from_dict(
            {
                **event.to_dict(),
                "relationship_id": self.profile.relationship_id,
            }
        )
        self.engine.storage.append_relationship_event(event)
        return event

    @staticmethod
    def _request(audience="agent_private", max_cost=100_000, reinforce=False):
        return RecallRequest(
            agent_id="lumi",
            user_id="user",
            query="promise tea unfinished",
            audience=audience,
            options=RecallOptions(
                reinforce=reinforce,
                persona_delivery="full",
                budget=RecallBudget(max_cost=max_cost),
            ),
            temporal_context=RecallTemporalContext(
                world_time=WorldTime(
                    clock_id="story-v1",
                    display_value="story day 11",
                    order_value=11,
                )
            ),
        )

    def test_private_recall_renders_overdue_but_public_and_legacy_do_not(self):
        self._append(_promise())

        private = self.engine.recall_structured(self._request())
        rendered = self.engine.render_recall(private)
        public = self.engine.recall_structured(self._request(audience="public"))
        legacy = self.engine.recall("lumi", "user", "promise tea")

        self.assertEqual(private.signals[0].signal_type, "promise_overdue")
        self.assertIn("deadline [story-v1]: story day 10", rendered)
        self.assertIn("not a breach finding", rendered)
        self.assertEqual(public.signals, ())
        self.assertNotIn("make jasmine tea", public.stable_json())
        self.assertNotIn("# Current Recall Signals", legacy)

    def test_budget_prioritizes_overdue_and_signals_do_not_reinforce_nodes(self):
        self._append(_promise("promise-overdue", due_order=10, order=1))
        self._append(_promise("promise-due", due_order=11, order=2))
        self._append(
            _event(
                "loop-question",
                RelationshipEventType.OPEN_LOOP,
                OpenLoopSpec(subject="answer the unfinished question"),
                3,
            )
        )
        self.engine.storage.save_nodes(
            "lumi",
            "user",
            [
                MemoryNode(
                    node_id="legacy-loop",
                    agent_id="lumi",
                    user_id="user",
                    node_type=MemoryType.THOUGHT,
                    content="legacy unfinished matter",
                    is_unresolved=True,
                ),
                MemoryNode(
                    node_id="large-memory",
                    agent_id="lumi",
                    user_id="user",
                    node_type=MemoryType.FACT,
                    content="large " * 3000,
                ),
            ],
        )
        complete = self.engine.recall_structured(self._request())
        overdue = next(
            item
            for item in complete.signals
            if item.signal_type == RecallSignalType.PROMISE_OVERDUE
        )
        exact_budget = complete.budget_report.required_cost + len(overdue.stable_json())

        selected = self.engine.recall_structured(
            self._request(max_cost=exact_budget, reinforce=True)
        )
        nodes = {
            node.node_id: node
            for node in self.engine.storage.load_nodes("lumi", "user")
        }

        self.assertEqual(
            [item.signal_type for item in selected.signals],
            [RecallSignalType.PROMISE_OVERDUE],
        )
        self.assertEqual(nodes["legacy-loop"].access_count, 0)
        self.assertEqual(nodes["large-memory"].access_count, 0)
        self.assertTrue(
            any(
                item.source_id == "promise-due"
                for item in selected.budget_report.omitted
            )
        )


if __name__ == "__main__":
    unittest.main()
