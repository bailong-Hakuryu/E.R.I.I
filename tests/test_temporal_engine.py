"""Trusted host, storage-integrity, and portability contracts for v0.4.0a4."""

import shutil
import tempfile
import unittest

from erii import ERIIEngine, MemoryNode, MemoryType, SQLiteStorage
from erii.core.temporal_history import TemporalHistoryConflictError
from erii.models.pack import MemoryPack
from erii.models.relationship import RelationshipEvent, RelationshipEventType
from erii.models.temporal import (
    OpenLoopResolution,
    OpenLoopResolutionKind,
    PromiseCondition,
    PromiseConditionConfirmation,
    PromiseResolution,
    PromiseResolutionKind,
    PromiseSpec,
    WorldMoment,
)


class TemporalEngineTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _engines(self):
        return (
            ERIIEngine(storage_dir=f"{self.root}/file"),
            ERIIEngine(
                storage_driver=SQLiteStorage(f"{self.root}/sqlite/memory.db")
            ),
        )

    def test_trusted_promise_is_immutable_idempotent_and_resolves_once(self):
        for engine in self._engines():
            with self.subTest(storage=engine.storage.__class__.__name__):
                profile = engine.initialize_relationship("lumi", "chen", "Lumi is patient.")
                promise = engine.record_promise(
                    "lumi",
                    "chen",
                    "Watch the next snow together.",
                    ["user", "agent"],
                    due_at=WorldMoment("story", "winter day 3", 3),
                    event_id="promise-snow",
                )
                repeated = engine.record_promise(
                    "lumi",
                    "chen",
                    "Watch the next snow together.",
                    ["agent", "user"],
                    due_at={
                        "clock_id": "story",
                        "display_value": "winter day 3",
                        "order_value": 3,
                    },
                    event_id="promise-snow",
                )
                self.assertEqual(repeated.event_id, promise.event_id)
                self.assertEqual(
                    [item.value for item in promise.temporal_payload.responsible_parties],
                    ["agent", "user"],
                )

                resolution = engine.resolve_promise(
                    "lumi",
                    "chen",
                    promise.event_id,
                    "fulfilled",
                    event_id="promise-snow-resolution",
                )
                repeated_resolution = engine.resolve_promise(
                    "lumi",
                    "chen",
                    promise.event_id,
                    "fulfilled",
                    event_id="promise-snow-resolution",
                )
                self.assertEqual(repeated_resolution.event_id, resolution.event_id)
                with self.assertRaises(TemporalHistoryConflictError):
                    engine.resolve_promise(
                        "lumi",
                        "chen",
                        promise.event_id,
                        "cancelled",
                        event_id="competing-resolution",
                    )

                history = engine.list_relationship_events("lumi", "chen")
                original = next(item for item in history if item.event_id == promise.event_id)
                self.assertIsInstance(original.temporal_payload, PromiseSpec)
                self.assertEqual(original.temporal_payload.action, "Watch the next snow together.")
                snapshot = engine.get_relationship_snapshot("lumi", "chen")
                self.assertEqual(snapshot.profile.relationship_id, profile.relationship_id)
                self.assertEqual(snapshot.state.trust, profile.baseline.state["trust"])
                engine.close()

    def test_conditions_open_loops_and_storage_bypass_share_integrity_rules(self):
        for engine in self._engines():
            with self.subTest(storage=engine.storage.__class__.__name__):
                profile = engine.initialize_relationship("lumi", "chen", "Lumi is patient.")
                promise = engine.record_promise(
                    "lumi",
                    "chen",
                    "Return when the bell rings.",
                    ["agent"],
                    activation_condition=PromiseCondition(
                        "bell-rings",
                        "The old bell rings.",
                    ),
                    event_id="conditional-promise",
                )
                confirmation = engine.confirm_promise_condition(
                    "lumi",
                    "chen",
                    promise.event_id,
                    "bell-rings",
                    event_id="bell-confirmed",
                )
                self.assertIsInstance(
                    confirmation.temporal_payload,
                    PromiseConditionConfirmation,
                )
                with self.assertRaises(TemporalHistoryConflictError):
                    engine.confirm_promise_condition(
                        "lumi",
                        "chen",
                        promise.event_id,
                        "bell-rings",
                        event_id="bell-confirmed-again",
                    )

                engine.storage.save_nodes(
                    "lumi",
                    "chen",
                    [
                        MemoryNode(
                            node_id="legacy-loop",
                            agent_id="lumi",
                            user_id="chen",
                            node_type=MemoryType.THOUGHT,
                            content="I still need to ask about the letter.",
                            is_unresolved=True,
                        )
                    ],
                )
                loop = engine.record_open_loop(
                    "lumi",
                    "chen",
                    "Ask about the letter",
                    expected_continuation="Return to the question in a later conversation.",
                    origin_memory_node_id="legacy-loop",
                    event_id="formal-loop",
                )
                engine.resolve_open_loop(
                    "lumi",
                    "chen",
                    loop.event_id,
                    OpenLoopResolutionKind.COMPLETED,
                    event_id="formal-loop-resolution",
                )
                with self.assertRaises(TemporalHistoryConflictError):
                    engine.record_open_loop(
                        "lumi",
                        "chen",
                        "Duplicate promotion",
                        origin_memory_node_id="legacy-loop",
                    )

                invalid = RelationshipEvent(
                    event_id="invalid-storage-bypass",
                    relationship_id=profile.relationship_id,
                    event_type=RelationshipEventType.PROMISE_RESOLUTION,
                    content="Invalid cross-target resolution",
                    temporal_payload=PromiseResolution(
                        promise_event_id="missing-promise",
                        resolution_kind=PromiseResolutionKind.CANCELLED,
                    ),
                )
                with self.assertRaises(TemporalHistoryConflictError):
                    engine.storage.append_relationship_event(invalid)
                engine.close()

    def test_temporal_targets_never_cross_agent_user_relationships(self):
        engine = ERIIEngine(storage_dir=self.root)
        engine.initialize_relationship("lumi", "chen", "Lumi is patient.")
        promise = engine.record_promise(
            "lumi",
            "chen",
            "Remember the red umbrella.",
            ["agent"],
        )
        engine.initialize_relationship("lumi", "another", "Lumi is patient.")

        with self.assertRaises(TemporalHistoryConflictError):
            engine.resolve_promise(
                "lumi",
                "another",
                promise.event_id,
                PromiseResolutionKind.FULFILLED,
            )
        engine.close()

    def test_memory_pack_remaps_nested_temporal_references_and_rejects_gaps(self):
        source = ERIIEngine(storage_dir=f"{self.root}/source")
        source.initialize_relationship("lumi", "chen", "Lumi is patient.")
        old_promise = source.record_promise(
            "lumi",
            "chen",
            "Meet at the bridge.",
            ["agent"],
            event_id="promise-old",
        )
        new_promise = source.record_promise(
            "lumi",
            "chen",
            "Meet at the station.",
            ["agent"],
            event_id="promise-new",
        )
        source.resolve_promise(
            "lumi",
            "chen",
            old_promise.event_id,
            PromiseResolutionKind.SUPERSEDED,
            superseding_promise_event_id=new_promise.event_id,
            event_id="promise-resolution",
        )
        old_loop = source.record_open_loop(
            "lumi",
            "chen",
            "Choose a meeting place",
            event_id="loop-old",
        )
        new_loop = source.record_open_loop(
            "lumi",
            "chen",
            "Confirm the station exit",
            event_id="loop-new",
        )
        source.resolve_open_loop(
            "lumi",
            "chen",
            old_loop.event_id,
            OpenLoopResolutionKind.SUPERSEDED,
            superseding_open_loop_event_id=new_loop.event_id,
            event_id="loop-resolution",
        )
        pack = source.export_memory("lumi", "chen")

        target = ERIIEngine(
            storage_driver=SQLiteStorage(f"{self.root}/target/memory.db")
        )
        target.import_memory(pack, agent_id="lumi", user_id="another")
        imported = target.list_relationship_events("lumi", "another")
        imported_ids = {item.event_id for item in imported}
        self.assertTrue(imported_ids.isdisjoint({"promise-old", "promise-new", "loop-old", "loop-new"}))
        for event in imported:
            payload = event.temporal_payload
            if isinstance(payload, PromiseResolution):
                self.assertIn(payload.promise_event_id, imported_ids)
                self.assertIn(payload.superseding_promise_event_id, imported_ids)
            if isinstance(payload, OpenLoopResolution):
                self.assertIn(payload.open_loop_event_id, imported_ids)
                self.assertIn(payload.superseding_open_loop_event_id, imported_ids)
        target.import_memory(pack, agent_id="lumi", user_id="another")
        self.assertEqual(
            len(target.list_relationship_events("lumi", "another")),
            len(imported),
        )

        broken = pack.to_dict()
        broken["relationship_events"] = [
            item
            for item in broken["relationship_events"]
            if item["event_id"] != "promise-old"
        ]
        broken_pack = MemoryPack.from_dict(broken)
        empty_target = ERIIEngine(storage_dir=f"{self.root}/empty-target")
        with self.assertRaises(ValueError):
            empty_target.import_memory(
                broken_pack,
                agent_id="lumi",
                user_id="broken",
            )
        self.assertIsNone(empty_target.storage.get_relationship("lumi", "broken"))
        source.close()
        target.close()
        empty_target.close()

    def test_memory_pack_import_orders_direct_resolution_after_adjudicated_promise(self):
        source = ERIIEngine(storage_dir=f"{self.root}/mixed-source")
        source.initialize_relationship("lumi", "chen", "Lumi is patient.")
        turn = {
            "turn_id": "turn-adjudicated-promise",
            "messages": [
                {
                    "source_id": "message-adjudicated-promise",
                    "role": "agent",
                    "content": "I promise to bring the paper crane.",
                }
            ],
        }
        result = source.adjudicate_relationship_candidates(
            "lumi",
            "chen",
            turn,
            [
                {
                    "candidate_key": "paper-crane-promise",
                    "event_type": "promise",
                    "summary": "Lumi promised to bring the paper crane.",
                    "signal": {
                        "signal_type": "commitment",
                        "strength": "moderate",
                        "extraction_confidence": 0.95,
                        "interpretation_confidence": 0.95,
                    },
                    "temporal_payload": {
                        "payload_type": "promise",
                        "responsible_parties": ["agent"],
                        "action": "bring the paper crane",
                    },
                    "evidence": [
                        {
                            "source_id": "message-adjudicated-promise",
                            "quote": "I promise to bring the paper crane.",
                        }
                    ],
                }
            ],
        )
        promise = result.records[0].events[0]
        source.resolve_promise(
            "lumi",
            "chen",
            promise.event_id,
            PromiseResolutionKind.FULFILLED,
            event_id="direct-resolution",
        )

        pack = source.export_memory("lumi", "chen")
        adjudicated_ids = {
            event.event_id
            for record in pack.relationship_adjudications
            for event in record.events
        }
        # A portable pack may keep the two journals separate instead of
        # duplicating adjudicated events in relationship_events.
        pack.relationship_events = [
            event
            for event in pack.relationship_events
            if event.event_id not in adjudicated_ids
        ]
        target = ERIIEngine(
            storage_driver=SQLiteStorage(f"{self.root}/mixed-target/memory.db")
        )
        target.import_memory(pack, agent_id="lumi", user_id="another")
        imported = target.list_relationship_events("lumi", "another")
        imported_promise = next(
            event for event in imported if isinstance(event.temporal_payload, PromiseSpec)
        )
        imported_resolution = next(
            event
            for event in imported
            if isinstance(event.temporal_payload, PromiseResolution)
        )

        self.assertEqual(
            imported_resolution.temporal_payload.promise_event_id,
            imported_promise.event_id,
        )
        self.assertEqual(
            len(target.list_relationship_adjudications("lumi", "another")),
            1,
        )
        source.close()
        target.close()

    def test_memory_pack_replays_confirmation_before_direct_resolution(self):
        source = ERIIEngine(storage_dir=f"{self.root}/conditional-source")
        source.initialize_relationship("lumi", "chen", "Lumi is patient.")
        promise = source.record_promise(
            "lumi",
            "chen",
            "Return when the bell rings.",
            ["agent"],
            activation_condition=PromiseCondition(
                "bell-rings",
                "The old bell rings.",
            ),
            event_id="conditional-promise",
        )
        turn = {
            "turn_id": "turn-adjudicated-confirmation",
            "messages": [
                {
                    "source_id": "message-bell-rings",
                    "role": "user",
                    "content": "The old bell is ringing now.",
                }
            ],
        }
        confirmation_result = source.adjudicate_relationship_candidates(
            "lumi",
            "chen",
            turn,
            [
                {
                    "candidate_key": "bell-confirmation",
                    "event_type": "promise_condition_confirmed",
                    "summary": "The old bell rang.",
                    "signal": {
                        "signal_type": "neutral",
                        "strength": "moderate",
                        "extraction_confidence": 0.95,
                        "interpretation_confidence": 0.95,
                    },
                    "temporal_payload": {
                        "payload_type": "promise_condition_confirmed",
                        "promise_event_id": promise.event_id,
                        "condition_id": "bell-rings",
                    },
                    "evidence": [
                        {
                            "source_id": "message-bell-rings",
                            "quote": "The old bell is ringing now.",
                        }
                    ],
                }
            ],
        )
        self.assertEqual(len(confirmation_result.records[0].events), 1)
        source.resolve_promise(
            "lumi",
            "chen",
            promise.event_id,
            PromiseResolutionKind.FULFILLED,
            event_id="direct-conditional-resolution",
        )

        pack = source.export_memory("lumi", "chen")
        adjudicated_ids = {
            event.event_id
            for record in pack.relationship_adjudications
            for event in record.events
        }
        pack.relationship_events = [
            event
            for event in pack.relationship_events
            if event.event_id not in adjudicated_ids
        ]
        target = ERIIEngine(storage_dir=f"{self.root}/conditional-target")
        target.import_memory(pack, agent_id="lumi", user_id="another")

        imported = target.list_relationship_events("lumi", "another")
        self.assertEqual(
            sum(
                isinstance(event.temporal_payload, PromiseConditionConfirmation)
                for event in imported
            ),
            1,
        )
        self.assertEqual(
            sum(
                isinstance(event.temporal_payload, PromiseResolution)
                for event in imported
            ),
            1,
        )
        source.close()
        target.close()

    def test_memory_pack_rejects_a_missing_open_loop_origin_memory(self):
        source = ERIIEngine(storage_dir=f"{self.root}/origin-source")
        source.initialize_relationship("lumi", "chen", "Lumi is patient.")
        source.storage.save_nodes(
            "lumi",
            "chen",
            [
                MemoryNode(
                    node_id="legacy-origin",
                    agent_id="lumi",
                    user_id="chen",
                    node_type=MemoryType.THOUGHT,
                    content="An unfinished question.",
                    is_unresolved=True,
                )
            ],
        )
        source.record_open_loop(
            "lumi",
            "chen",
            "Return to the unfinished question",
            origin_memory_node_id="legacy-origin",
        )
        broken = source.export_memory("lumi", "chen")
        broken.nodes = []

        target = ERIIEngine(storage_dir=f"{self.root}/origin-target")
        with self.assertRaisesRegex(ValueError, "missing origin memory node"):
            target.import_memory(broken, agent_id="lumi", user_id="another")
        self.assertIsNone(target.storage.get_relationship("lumi", "another"))
        source.close()
        target.close()


if __name__ == "__main__":
    unittest.main()
