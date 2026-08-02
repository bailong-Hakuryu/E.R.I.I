"""Shared staging-only erasure contracts for built-in storage adapters."""

import json
from pathlib import Path
import tempfile
import unittest

from erii.engine import ERIIEngine
from erii.core.adjudication import list_complete_relationship_events
from erii.lifecycle_erasure import (
    ErasureScope,
    ErasureSelector,
    ErasureStorageKind,
    erase_staged_storage,
    inspect_erasure_scope,
    rebuild_staged_storage,
)
from erii.models.provenance import ExtractorDescriptor
from erii.models.adjudication import GrowthTriggerKind, PersonaGrowthProposal
from erii.models.consolidation import ReflectionInterpreterDescriptor
from erii.models.node import MemoryNode
from erii.storage.file_storage import FileStorage
from erii.storage.sqlite_storage import SQLiteStorage


def _delivery_exception():
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.lifecycle-erasure/v1",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-02T08:00:00+08:00",
        "reply_attempt_number": None,
    }


class _EventExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.lifecycle-erasure",
        extractor_version="1",
        extraction_schema_version="1",
    )

    def extract(self, request):
        message = request.transcript.user_message
        return {
            "kind": "candidates",
            "candidates": [
                {
                    "candidate_key": request.source_turn_id,
                    "event_type": "shared_experience",
                    "summary": f"Derived event for {request.source_turn_id}",
                    "signal": {
                        "signal_type": "shared_experience",
                        "strength": "moderate",
                        "extraction_confidence": 0.95,
                        "interpretation_confidence": 0.9,
                    },
                    "evidence": [
                        {
                            "source_id": message.message_id,
                            "source_revision": request.source_revision,
                            "quote": message.content,
                            "start": 0,
                            "end": len(message.content),
                        }
                    ],
                }
            ],
        }


class _ReflectionInterpreter:
    descriptor = ReflectionInterpreterDescriptor(
        interpreter_id="tests.lifecycle-erasure-reflection",
        interpreter_version="1",
    )

    def interpret(self, request):
        return {
            "kind": "reflection",
            "content": "REFLECTION BODY MUST BE ERASED",
            "emotional_direction": "warm",
            "emotional_intensity": "moderate",
            "core_meaning": "A private derived interpretation.",
        }


class StagedErasureContract(unittest.TestCase):
    """Runs the same logical erasure assertions against both backends."""

    def _cases(self, root: str):
        yield (
            ErasureStorageKind.FILE_STORAGE,
            str(Path(root, "file-store")),
            FileStorage,
        )
        yield (
            ErasureStorageKind.SQLITE,
            str(Path(root, "memory.sqlite3")),
            SQLiteStorage,
        )

    @staticmethod
    def _seed(storage_factory, path):
        storage = storage_factory(path)
        with ERIIEngine(storage_driver=storage) as engine:
            target = engine.initialize_relationship(
                "agent-target",
                "user-target",
                "TARGET PERSONA BODY MUST NOT ENTER THE INVENTORY",
            )
            kept = engine.initialize_relationship(
                "agent-kept",
                "user-kept",
                "KEPT PERSONA BODY",
            )
            engine.record_relationship_event(
                "agent-target",
                "user-target",
                "shared_experience",
                "TARGET CHAT BODY MUST NOT ENTER THE INVENTORY",
                event_id="event-target",
                state_delta={"trust": 0.04},
                belief_updates=[
                    {
                        "key": "shared.target",
                        "value": "sensitive-value",
                        "confidence": 0.9,
                    }
                ],
            )
            engine.record_relationship_event(
                "agent-kept",
                "user-kept",
                "shared_experience",
                "KEPT CHAT BODY",
                event_id="event-kept",
                state_delta={"trust": 0.02},
            )
            engine.storage.save_nodes(
                target.agent_id,
                target.user_id,
                [
                    MemoryNode(
                        node_id="target-vector-node",
                        agent_id=target.agent_id,
                        user_id=target.user_id,
                        content="TARGET MEMORY BODY",
                        relationship_id=target.relationship_id,
                    )
                ],
            )
        return target, kept

    def test_relationship_scope_erases_only_the_exact_agent_user_relationship(self):
        for kind in ErasureStorageKind:
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root:
                case = next(item for item in self._cases(root) if item[0] is kind)
                _, path, storage_factory = case
                target, kept = self._seed(storage_factory, path)

                result = erase_staged_storage(
                    path,
                    kind,
                    ErasureSelector(
                        scope=ErasureScope.RELATIONSHIP,
                        agent_id=target.agent_id,
                        user_id=target.user_id,
                        relationship_id=target.relationship_id,
                    ),
                )

                reopened = storage_factory(path)
                self.assertIsNone(
                    reopened.get_relationship(target.agent_id, target.user_id)
                )
                self.assertEqual(
                    reopened.get_relationship(kept.agent_id, kept.user_id),
                    kept,
                )
                self.assertEqual(
                    [event.event_id for event in reopened.list_relationship_events(
                        kept.relationship_id
                    )],
                    ["event-kept"],
                )
                self.assertEqual(result.affected_relationship_ids, (target.relationship_id,))
                self.assertEqual(
                    set(result.inventory.counts),
                    {"deleted", "rebuilt", "delegated", "unverified_external"},
                )
                self.assertGreater(
                    result.inventory.counts["deleted"].get("relationship", 0),
                    0,
                )
                self.assertEqual(
                    result.inventory.counts["delegated"]["memory_vector_delete"],
                    1,
                )
                self.assertEqual(
                    result.inventory.counts["unverified_external"]["memory_vector"],
                    1,
                )
                rendered = json.dumps(result.to_dict(), ensure_ascii=False)
                self.assertNotIn("TARGET CHAT BODY", rendered)
                self.assertNotIn("TARGET PERSONA BODY", rendered)
                self.assertNotIn("sensitive-value", rendered)
                self.assertNotIn("TARGET MEMORY BODY", rendered)

    def test_selector_rejects_ambiguous_or_cross_scope_fields(self):
        with self.assertRaises(ValueError):
            ErasureSelector(
                scope=ErasureScope.RELATIONSHIP,
                agent_id="agent",
                user_id="user",
            )
        with self.assertRaises(ValueError):
            ErasureSelector(
                scope=ErasureScope.RELATIONSHIP,
                agent_id="agent",
                user_id="user",
                relationship_id="relationship",
                source_turn_id="turn-not-allowed",
            )
        with self.assertRaises(ValueError):
            ErasureSelector(
                scope=ErasureScope.COMPLETE_USER,
                user_id="user",
            )

    def test_source_turn_scope_cascades_derived_history_and_rebuilds_projection(self):
        for kind in ErasureStorageKind:
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root:
                _, path, storage_factory = next(
                    item for item in self._cases(root) if item[0] is kind
                )
                storage = storage_factory(path)
                with ERIIEngine(
                    storage_driver=storage,
                    relationship_event_extractor=_EventExtractor(),
                ) as engine:
                    profile = engine.initialize_relationship(
                        "agent-turn",
                        "user-turn",
                        "A stable persona body.",
                    )
                    for turn_id in ("turn-delete", "turn-keep"):
                        engine.record_turn(
                            profile.agent_id,
                            profile.user_id,
                            f"USER BODY {turn_id}",
                            f"AGENT BODY {turn_id}",
                            turn_id=turn_id,
                            delivery_exception=_delivery_exception(),
                        )
                        engine.process_relationship_turn(
                            profile.agent_id,
                            profile.user_id,
                            turn_id,
                        )
                removed_event_id = next(
                    record.events[0].event_id
                    for record in storage_factory(path).list_relationship_adjudications(
                        profile.relationship_id
                    )
                    if record.receipt.source_turn_id == "turn-delete"
                )

                result = erase_staged_storage(
                    path,
                    kind,
                    ErasureSelector(
                        scope=ErasureScope.SOURCE_TURN,
                        agent_id=profile.agent_id,
                        user_id=profile.user_id,
                        relationship_id=profile.relationship_id,
                        source_turn_id="turn-delete",
                    ),
                )

                reopened = storage_factory(path)
                self.assertEqual(
                    [item.turn_id for item in reopened.list_turn_records(
                        profile.relationship_id
                    )],
                    ["turn-keep"],
                )
                kept_turn = reopened.get_turn_record(
                    profile.relationship_id,
                    "turn-keep",
                )
                self.assertIsNotNone(kept_turn)
                self.assertIsNone(kept_turn.context_baseline)
                self.assertEqual(
                    reopened.list_reply_attempts(
                        profile.relationship_id,
                        "turn-delete",
                    ),
                    [],
                )
                self.assertFalse(
                    any(
                        item.receipt.source_turn_id == "turn-delete"
                        for item in reopened.list_relationship_adjudications(
                            profile.relationship_id
                        )
                    )
                )
                self.assertFalse(
                    any(
                        item.source_turn_id == "turn-delete"
                        for item in reopened.list_relationship_processing_runs(
                            profile.relationship_id
                        )
                    )
                )
                remaining = list_complete_relationship_events(
                    reopened,
                    profile.relationship_id,
                )
                self.assertEqual(remaining, [])
                self.assertNotIn(removed_event_id, {item.event_id for item in remaining})
                self.assertEqual(
                    reopened.list_relationship_processing_runs(
                        profile.relationship_id
                    ),
                    [],
                )
                self.assertEqual(result.rebuild_proofs[0].event_count, 0)
                self.assertEqual(
                    result.inventory.counts["rebuilt"]["relationship_state"],
                    1,
                )
                self.assertGreaterEqual(
                    result.inventory.counts["deleted"].get(
                        "relationship_adjudication", 0
                    ),
                    1,
                )
                rendered = json.dumps(result.to_dict(), ensure_ascii=False)
                self.assertNotIn("USER BODY", rendered)
                self.assertNotIn("AGENT BODY", rendered)

    def test_relationship_event_scope_removes_dependent_artifacts_but_keeps_turn(self):
        for kind in ErasureStorageKind:
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root:
                _, path, storage_factory = next(
                    item for item in self._cases(root) if item[0] is kind
                )
                storage = storage_factory(path)
                with ERIIEngine(
                    storage_driver=storage,
                    relationship_event_extractor=_EventExtractor(),
                    persona_reflection_interpreter=_ReflectionInterpreter(),
                ) as engine:
                    profile = engine.initialize_relationship(
                        "agent-event",
                        "user-event",
                        "A stable persona body.",
                    )
                    for turn_id in ("turn-event-delete", "turn-event-keep"):
                        engine.record_turn(
                            profile.agent_id,
                            profile.user_id,
                            f"USER EVENT BODY {turn_id}",
                            f"AGENT EVENT BODY {turn_id}",
                            turn_id=turn_id,
                            delivery_exception=_delivery_exception(),
                        )
                        engine.process_relationship_turn(
                            profile.agent_id,
                            profile.user_id,
                            turn_id,
                        )
                persisted = storage_factory(path)
                target_record = next(
                    record
                    for record in persisted.list_relationship_adjudications(
                        profile.relationship_id
                    )
                    if record.receipt.source_turn_id == "turn-event-delete"
                )
                target_event_id = target_record.events[0].event_id
                persisted.save_persona_growth_proposal(
                    PersonaGrowthProposal(
                        proposal_id="growth-delete",
                        relationship_id=profile.relationship_id,
                        revision=1,
                        intent_key="growth-key",
                        review_id="growth-review",
                        statement="GROWTH BODY MUST BE ERASED",
                        rationale="PRIVATE RATIONALE",
                        proposed_changes={"voice": "private"},
                        supporting_event_ids=(target_event_id,),
                        trigger_kind=GrowthTriggerKind.PIVOTAL,
                    )
                )

                result = erase_staged_storage(
                    path,
                    kind,
                    ErasureSelector(
                        scope=ErasureScope.RELATIONSHIP_EVENT,
                        agent_id=profile.agent_id,
                        user_id=profile.user_id,
                        relationship_id=profile.relationship_id,
                        relationship_event_id=target_event_id,
                    ),
                )

                reopened = storage_factory(path)
                self.assertEqual(
                    [item.turn_id for item in reopened.list_turn_records(
                        profile.relationship_id
                    )],
                    ["turn-event-delete", "turn-event-keep"],
                )
                kept_turn = reopened.get_turn_record(
                    profile.relationship_id,
                    "turn-event-keep",
                )
                self.assertIsNotNone(kept_turn)
                self.assertIsNone(kept_turn.context_baseline)
                self.assertNotIn(
                    target_event_id,
                    {
                        event.event_id
                        for event in list_complete_relationship_events(
                            reopened,
                            profile.relationship_id,
                        )
                    },
                )
                self.assertFalse(
                    any(
                        target_event_id in item.supporting_event_ids
                        for item in reopened.list_persona_growth_proposals(
                            profile.relationship_id
                        )
                    )
                )
                self.assertFalse(
                    any(
                        item.event_id == target_event_id
                        for item in reopened.list_persona_reflection_records(
                            profile.relationship_id
                        )
                    )
                )
                self.assertEqual(
                    list_complete_relationship_events(
                        reopened,
                        profile.relationship_id,
                    ),
                    [],
                )
                self.assertEqual(
                    reopened.list_relationship_processing_runs(
                        profile.relationship_id
                    ),
                    [],
                )
                self.assertEqual(result.rebuild_proofs[0].event_count, 0)
                self.assertGreaterEqual(
                    result.inventory.counts["deleted"].get(
                        "persona_reflection", 0
                    ),
                    1,
                )
                self.assertGreaterEqual(
                    result.inventory.counts["deleted"].get("persona_growth", 0),
                    1,
                )
                rendered = json.dumps(result.to_dict(), ensure_ascii=False)
                self.assertNotIn("REFLECTION BODY", rendered)
                self.assertNotIn("GROWTH BODY", rendered)
                self.assertNotIn("PRIVATE RATIONALE", rendered)

    def test_complete_user_scope_erases_every_agent_relationship_for_exact_identity(self):
        for kind in ErasureStorageKind:
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root:
                _, path, storage_factory = next(
                    item for item in self._cases(root) if item[0] is kind
                )
                with ERIIEngine(storage_driver=storage_factory(path)) as engine:
                    first = engine.initialize_relationship(
                        "agent-one", "shared-user", "FIRST PRIVATE PERSONA"
                    )
                    second = engine.initialize_relationship(
                        "agent-two", "shared-user", "SECOND PRIVATE PERSONA"
                    )
                    kept = engine.initialize_relationship(
                        "agent-one", "kept-user", "KEPT PERSONA"
                    )
                    engine.record_relationship_event(
                        first.agent_id,
                        first.user_id,
                        "shared_experience",
                        "FIRST PRIVATE EVENT",
                        event_id="first-private-event",
                    )
                    engine.record_relationship_event(
                        second.agent_id,
                        second.user_id,
                        "shared_experience",
                        "SECOND PRIVATE EVENT",
                        event_id="second-private-event",
                    )

                result = erase_staged_storage(
                    path,
                    kind,
                    ErasureSelector(
                        scope=ErasureScope.COMPLETE_USER,
                        user_id="shared-user",
                        user_identity_id=first.user_identity_id,
                    ),
                )

                reopened = storage_factory(path)
                self.assertIsNone(
                    reopened.get_relationship(first.agent_id, first.user_id)
                )
                self.assertIsNone(
                    reopened.get_relationship(second.agent_id, second.user_id)
                )
                self.assertEqual(
                    reopened.get_relationship(kept.agent_id, kept.user_id),
                    kept,
                )
                self.assertEqual(
                    result.affected_relationship_ids,
                    tuple(sorted((first.relationship_id, second.relationship_id))),
                )
                self.assertEqual(
                    result.inventory.counts["deleted"]["relationship"],
                    2,
                )
                rendered = json.dumps(result.to_dict(), ensure_ascii=False)
                self.assertNotIn("FIRST PRIVATE", rendered)
                self.assertNotIn("SECOND PRIVATE", rendered)

    def test_inspection_is_zero_write_and_rebuild_preserves_authoritative_history(self):
        for kind in ErasureStorageKind:
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root:
                _, path, storage_factory = next(
                    item for item in self._cases(root) if item[0] is kind
                )
                with ERIIEngine(storage_driver=storage_factory(path)) as engine:
                    profile = engine.initialize_relationship(
                        "agent-rebuild", "user-rebuild", "PRIVATE PERSONA BODY"
                    )
                    engine.record_relationship_event(
                        profile.agent_id,
                        profile.user_id,
                        "shared_experience",
                        "PRIVATE EVENT BODY",
                        event_id="event-rebuild",
                        state_delta={"trust": 0.04},
                        belief_updates=[
                            {
                                "key": "private.belief",
                                "value": "PRIVATE BELIEF BODY",
                                "confidence": 0.95,
                            }
                        ],
                    )
                selector = ErasureSelector(
                    scope=ErasureScope.RELATIONSHIP,
                    agent_id=profile.agent_id,
                    user_id=profile.user_id,
                    relationship_id=profile.relationship_id,
                )
                source = Path(path)
                before = (
                    {
                        item.relative_to(source).as_posix(): item.read_bytes()
                        for item in source.rglob("*")
                        if item.is_file()
                    }
                    if source.is_dir()
                    else source.read_bytes()
                )

                inspection = inspect_erasure_scope(path, kind, selector)

                after_inspection = (
                    {
                        item.relative_to(source).as_posix(): item.read_bytes()
                        for item in source.rglob("*")
                        if item.is_file()
                    }
                    if source.is_dir()
                    else source.read_bytes()
                )
                self.assertEqual(after_inspection, before)
                self.assertEqual(
                    inspection.affected_relationship_ids,
                    (profile.relationship_id,),
                )

                first = rebuild_staged_storage(path, kind, selector)
                second = rebuild_staged_storage(path, kind, selector)

                self.assertEqual(first, second)
                self.assertEqual(first.inventory.counts["deleted"], {})
                self.assertEqual(first.rebuild_proofs[0].event_count, 1)
                reopened = storage_factory(path)
                self.assertEqual(
                    [item.event_id for item in reopened.list_relationship_events(
                        profile.relationship_id
                    )],
                    ["event-rebuild"],
                )
                rendered = json.dumps(first.to_dict(), ensure_ascii=False)
                self.assertNotIn("PRIVATE PERSONA BODY", rendered)
                self.assertNotIn("PRIVATE EVENT BODY", rendered)
                self.assertNotIn("PRIVATE BELIEF BODY", rendered)


if __name__ == "__main__":
    unittest.main()
