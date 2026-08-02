"""Portable a7 relationship-processing ledger contracts."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
import os
import tempfile
import threading
import unittest

from erii import ERIIEngine, FileStorage, SQLiteStorage
from erii.models.archival import TimelineEntry
from erii.models.adjudication import DecisionOutcome, SourceProcessingMode
from erii.models.consolidation import (
    ApprovedGrowthReference,
    PersonaNoReflectionDecision,
    PersonaReflectionDecisionRecord,
    PersonaReflectionRecordKind,
    ReflectionContextProvenance,
    ReflectionInterpreterDescriptor,
    RelationshipNoEventDecision,
    RelationshipProcessingOutcome,
    RelationshipProcessingRun,
    RelationshipProcessingStatus,
)
from erii.models.pack import MemoryPack
from erii.models.provenance import ExtractorDescriptor
from erii.models.relationship import (
    RelationshipEvent,
    RelationshipEventType,
)
from erii.models.temporal import (
    PromiseResolution,
    PromiseResolutionKind,
)
from tests.test_relationship_processing_public import (
    _ReflectionInterpreter,
    _RelationshipExtractor,
    _UniqueRelationshipExtractor,
)
from tests.test_temporal_adjudication import source_turn, temporal_candidate
from tests.test_structured_recall import (
    SOURCE as PERSONA_SOURCE,
    _candidate as persona_candidate,
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


class _BlockingReflectionInterpreter(_ReflectionInterpreter):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def interpret(self, request):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("test reflection interpreter timed out")
        return super().interpret(request)


class _ReferencingRelationshipExtractor(_RelationshipExtractor):
    def __init__(self, referenced_event_id):
        super().__init__()
        self.referenced_event_id = referenced_event_id

    def extract(self, request):
        decision = super().extract(request)
        decision["candidates"][0]["references"] = [
            self.referenced_event_id
        ]
        return decision


class _BlockingImportFileStorage(FileStorage):
    def __init__(self, root_dir, blocked_event_id):
        super().__init__(root_dir)
        self.blocked_event_id = blocked_event_id
        self.import_append_entered = threading.Event()
        self.release_import_append = threading.Event()

    def append_relationship_event(self, event):
        if event.event_id == self.blocked_event_id:
            self.import_append_entered.set()
            if not self.release_import_append.wait(timeout=5):
                raise RuntimeError("test import append timed out")
        return super().append_relationship_event(event)


class ConsolidationMemoryPackTests(unittest.TestCase):
    def test_a7_round_trip_carries_terminal_processing_and_reflection_outcomes(self):
        run = RelationshipProcessingRun(
            processing_id="processing-1",
            relationship_id="relationship-1",
            source_turn_id="turn-1",
            source_revision="1",
            processing_mode=SourceProcessingMode.NORMAL,
            status=RelationshipProcessingStatus.COMPLETED,
            outcome=RelationshipProcessingOutcome.NO_RELATIONSHIP_EVENT,
            extractor_descriptor=ExtractorDescriptor(
                extractor_id="tests.relationship-extractor",
                extractor_version="1",
                extraction_schema_version="1",
            ),
            frozen_decision=RelationshipNoEventDecision(
                reason_code="ordinary_exchange"
            ),
            completed_at="2026-07-29T00:00:01+00:00",
        )
        provenance = ReflectionContextProvenance(
            source_turn_id="turn-2",
            source_revision="1",
            decision_id="adjudication-1",
            evidence_ids=("evidence-1",),
            relationship_event_id="event-1",
            blueprint_id="blueprint-1",
            blueprint_sha256="a" * 64,
            blueprint_revision=1,
            baseline_fingerprint="b" * 64,
        )
        reflection = PersonaReflectionDecisionRecord(
            decision_id="reflection-decision-1",
            relationship_id="relationship-1",
            event_id="event-1",
            source_turn_id="turn-2",
            source_revision="1",
            interpreter_descriptor=ReflectionInterpreterDescriptor(
                interpreter_id="tests.persona-reflection",
                interpreter_version="1",
            ),
            decision=PersonaNoReflectionDecision(
                reason_code="no_distinct_inner_response"
            ),
            context_provenance=provenance,
            recorded_at="2026-07-29T00:00:02+00:00",
        )

        pack = MemoryPack(
            agent_id="agent-lumi",
            user_id="user-chen",
            relationship_processing_runs=[run],
            persona_reflection_decisions=[reflection],
        )
        restored = MemoryPack.from_json(pack.to_json())

        self.assertEqual(restored.version, "0.4.0a8")
        self.assertEqual(restored.relationship_processing_runs, [run])
        self.assertEqual(restored.persona_reflection_decisions, [reflection])
        self.assertNotIn("episodes", restored.to_dict())
        self.assertNotIn("relationship_chapters", restored.to_dict())

    def test_older_pack_without_a7_ledgers_remains_readable(self):
        restored = MemoryPack.from_dict(
            {
                "metadata": {
                    "version": "0.4.0a6",
                    "agent_id": "agent-lumi",
                    "user_id": "user-chen",
                    "exported_at": "2026-07-31 00:00:00",
                }
            }
        )

        self.assertEqual(restored.relationship_processing_runs, [])
        self.assertEqual(restored.persona_reflection_decisions, [])

    def test_file_to_sqlite_restore_preserves_idempotency_and_reflections(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
                persona_reflection_interpreter=_ReflectionInterpreter(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            run = source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()

            self.assertEqual(pack.relationship_processing_runs, [run])
            self.assertEqual(len(pack.persona_reflection_decisions), 1)

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.import_memory(pack)
            target.import_memory(pack)

            self.assertEqual(
                target.list_relationship_processing_runs(
                    "agent-lumi",
                    "user-chen",
                ),
                [run],
            )
            reflections = target.list_persona_reflections(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(len(reflections), 1)
            consolidation = target.get_relationship_consolidation(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(
                set(consolidation.covered_event_ids)
                | set(consolidation.unconsolidated_event_ids),
                {event.event_id for event in pack.relationship_events},
            )
            target.close()

    def test_tampered_reflection_provenance_fails_before_any_target_write(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
                persona_reflection_interpreter=_ReflectionInterpreter(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            exported = source.export_memory("agent-lumi", "user-chen")
            exported.core_memory = "must not be partially imported"
            exported.timeline = [
                {
                    "timestamp": "2026-07-29T00:00:00+00:00",
                    "content": "must not be partially imported",
                }
            ]
            source.close()

            mutations = {
                "adjudication decision": {
                    "decision_id": "missing-decision",
                },
                "adjudication evidence": {
                    "evidence_ids": ("missing-evidence",),
                },
                "blueprint identity": {
                    "blueprint_id": "missing-blueprint",
                },
                "blueprint source hash": {
                    "blueprint_sha256": "f" * 64,
                },
                "blueprint revision": {
                    "blueprint_revision": 2,
                },
                "relationship baseline": {
                    "baseline_fingerprint": "f" * 64,
                },
                "persona manifest": {
                    "manifest_id": "missing-manifest",
                    "manifest_revision": 1,
                    "manifest_fingerprint": "f" * 64,
                },
                "approved growth": {
                    "approved_growth": (
                        ApprovedGrowthReference(
                            proposal_id="missing-growth",
                            revision=1,
                            content_fingerprint="f" * 64,
                        ),
                    ),
                },
                "prior event": {
                    "prior_event_ids": ("missing-event",),
                },
                "current event presented as prior": {
                    "prior_event_ids": (
                        exported.persona_reflection_decisions[0].event_id,
                    ),
                },
            }

            for index, (case, changes) in enumerate(mutations.items()):
                with self.subTest(case=case):
                    tampered = MemoryPack.from_json(exported.to_json())
                    original = tampered.persona_reflection_decisions[0]
                    provenance = replace(
                        original.context_provenance,
                        **changes,
                    )
                    reflection_record = replace(
                        original.reflection_record,
                        context_provenance=provenance,
                    )
                    tampered.persona_reflection_decisions = [
                        replace(
                            original,
                            context_provenance=provenance,
                            reflection_record=reflection_record,
                        )
                    ]

                    target = ERIIEngine(
                        storage_driver=SQLiteStorage(
                            os.path.join(
                                root,
                                f"target-{index}",
                                "memory.db",
                            )
                        )
                    )
                    with self.assertRaises(ValueError):
                        target.import_memory(tampered)

                    target_export = target.export_memory(
                        "agent-lumi",
                        "user-chen",
                    )
                    self.assertEqual(target_export.core_memory, "")
                    self.assertEqual(target_export.timeline, [])
                    self.assertEqual(target_export.nodes, [])
                    self.assertIsNone(target_export.relationship)
                    self.assertEqual(target_export.turn_records, [])
                    self.assertEqual(target_export.relationship_events, [])
                    self.assertEqual(
                        target_export.persona_reflection_decisions,
                        [],
                    )
                    target.close()

    def test_reflection_manifest_provenance_must_match_relationship_binding(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
                persona_reflection_interpreter=_ReflectionInterpreter(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                PERSONA_SOURCE,
            )
            proposal = source.propose_persona_compilation(
                "agent-lumi",
                "user-chen",
                persona_candidate(),
            )
            manifest = source.decide_persona_compilation(
                "agent-lumi",
                "user-chen",
                proposal.proposal_id,
                proposal.revision,
                "owner",
                "approve",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            tampered = MemoryPack.from_json(
                source.export_memory("agent-lumi", "user-chen").to_json()
            )
            source.close()

            self.assertEqual(
                tampered.persona_reflection_decisions[
                    0
                ].context_provenance.manifest_id,
                manifest.manifest_id,
            )
            tampered.relationship = replace(
                tampered.relationship,
                manifest_id=None,
            )

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            with self.assertRaisesRegex(ValueError, "Persona Manifest"):
                target.import_memory(tampered)
            self.assertIsNone(
                target.storage.get_relationship(
                    "agent-lumi",
                    "user-chen",
                )
            )
            target.close()

    def test_export_waits_for_relationship_processing_to_reach_a_coherent_state(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            interpreter = _BlockingReflectionInterpreter()
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
                persona_reflection_interpreter=interpreter,
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            export_started = threading.Event()

            def export_pack():
                export_started.set()
                return source.export_memory("agent-lumi", "user-chen")

            with ThreadPoolExecutor(max_workers=2) as pool:
                processing = pool.submit(
                    source.process_relationship_turn,
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                self.assertTrue(interpreter.entered.wait(timeout=2))
                exporting = pool.submit(export_pack)
                self.assertTrue(export_started.wait(timeout=2))
                try:
                    with self.assertRaises(TimeoutError):
                        exporting.result(timeout=0.15)
                finally:
                    interpreter.release.set()
                run = processing.result(timeout=5)
                pack = exporting.result(timeout=5)
            source.close()

            self.assertEqual(pack.relationship_processing_runs, [run])
            self.assertEqual(len(pack.relationship_events), 1)
            self.assertEqual(len(pack.persona_reflection_decisions), 1)

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.import_memory(pack)
            self.assertEqual(
                target.list_relationship_processing_runs(
                    "agent-lumi",
                    "user-chen",
                ),
                [run],
            )
            target.close()

    def test_processing_run_and_adjudication_cannot_borrow_another_turns_lineage(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_UniqueRelationshipExtractor(),
                persona_reflection_interpreter=_ReflectionInterpreter(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            for turn_id, user_message in (
                ("turn-snow", "The snow is beautiful."),
                ("turn-rain", "The rain sounds gentle."),
            ):
                source.record_turn(
                    "agent-lumi",
                    "user-chen",
                    user_message,
                    "I want to remember this moment.",
                    turn_id=turn_id,
                    delivery_exception=(
                        _preexisting_visible_exchange_delivery_exception()
                    ),
                )
                source.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    turn_id,
                )
            exported = source.export_memory("agent-lumi", "user-chen")
            exported.core_memory = "must not be partially imported"
            source.close()

            def borrowed_run(pack):
                first, second = pack.relationship_processing_runs
                pack.relationship_processing_runs[0] = replace(
                    first,
                    decision_ids=second.decision_ids,
                    event_ids=second.event_ids,
                )

            def borrowed_evidence(pack):
                first, second = pack.relationship_adjudications
                pack.relationship_adjudications[0] = replace(
                    first,
                    receipt=replace(
                        first.receipt,
                        evidence=second.receipt.evidence,
                    ),
                )

            for index, mutation in enumerate(
                (borrowed_run, borrowed_evidence)
            ):
                with self.subTest(mutation=mutation.__name__):
                    tampered = MemoryPack.from_json(exported.to_json())
                    mutation(tampered)
                    target = ERIIEngine(
                        storage_driver=SQLiteStorage(
                            os.path.join(
                                root,
                                f"lineage-target-{index}",
                                "memory.db",
                            )
                        )
                    )
                    with self.assertRaises(ValueError):
                        target.import_memory(tampered)
                    target_export = target.export_memory(
                        "agent-lumi",
                        "user-chen",
                    )
                    self.assertEqual(target_export.core_memory, "")
                    self.assertIsNone(target_export.relationship)
                    self.assertEqual(target_export.turn_records, [])
                    self.assertEqual(target_export.relationship_events, [])
                    target.close()

    def test_multiple_runs_replay_their_distinct_journal_prefixes(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_UniqueRelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            runs = []
            for turn_id, user_message in (
                ("turn-snow", "The snow is beautiful."),
                ("turn-rain", "The rain sounds gentle."),
            ):
                source.record_turn(
                    "agent-lumi",
                    "user-chen",
                    user_message,
                    "I want to remember this moment.",
                    turn_id=turn_id,
                    delivery_exception=(
                        _preexisting_visible_exchange_delivery_exception()
                    ),
                )
                runs.append(
                    source.process_relationship_turn(
                        "agent-lumi",
                        "user-chen",
                        turn_id,
                    )
                )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()

            self.assertEqual(
                [
                    run.adjudication_base_decision_count
                    for run in runs
                ],
                [0, 1],
            )

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.import_memory(pack)
            self.assertEqual(
                target.list_relationship_processing_runs(
                    "agent-lumi",
                    "user-chen",
                ),
                runs,
            )
            target.close()

    def test_accepted_event_cannot_diverge_from_its_frozen_candidate(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            tampered = MemoryPack.from_json(
                source.export_memory("agent-lumi", "user-chen").to_json()
            )
            source.close()

            original_event = tampered.relationship_events[0]
            fabricated_event = replace(
                original_event,
                content="FABRICATED EVENT CONTENT",
            )
            tampered.relationship_events = [fabricated_event]
            original_adjudication = tampered.relationship_adjudications[0]
            tampered.relationship_adjudications = [
                replace(
                    original_adjudication,
                    events=(fabricated_event,),
                )
            ]

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            with self.assertRaisesRegex(ValueError, "frozen candidate"):
                target.import_memory(tampered)
            target_export = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertIsNone(target_export.relationship)
            self.assertEqual(target_export.relationship_events, [])
            target.close()

    def test_valid_candidate_cannot_be_rewritten_as_a_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            tampered = MemoryPack.from_json(
                source.export_memory("agent-lumi", "user-chen").to_json()
            )
            source.close()

            original = tampered.relationship_adjudications[0]
            tampered.relationship_adjudications = [
                replace(
                    original,
                    receipt=replace(
                        original.receipt,
                        outcome=DecisionOutcome.REJECTED,
                        reason_codes=("evidence_source_not_found",),
                        evidence=(),
                        event_ids=(),
                        related_event_id=None,
                        pivotal_eligible=False,
                    ),
                    events=(),
                )
            ]
            tampered.relationship_events = []
            original_run = tampered.relationship_processing_runs[0]
            tampered.relationship_processing_runs = [
                replace(
                    original_run,
                    outcome=RelationshipProcessingOutcome.NO_ACCEPTED_EVENTS,
                    event_ids=(),
                )
            ]

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            with self.assertRaisesRegex(
                ValueError,
                "frozen candidate and baseline",
            ):
                target.import_memory(tampered)
            self.assertIsNone(
                target.storage.get_relationship(
                    "agent-lumi",
                    "user-chen",
                )
            )
            target.close()

    def test_future_stamped_prior_direct_event_round_trips_by_journal_boundary(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            referenced_event_id = "future-stamped-prior"
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=(
                    _ReferencingRelationshipExtractor(
                        referenced_event_id
                    )
                ),
            )
            profile = source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.storage.append_relationship_event(
                RelationshipEvent(
                    event_id=referenced_event_id,
                    relationship_id=profile.relationship_id,
                    event_type=RelationshipEventType.OBSERVATION,
                    content="This happened before the processing run.",
                    recorded_at="2099-01-01T00:00:00+00:00",
                )
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()

            self.assertEqual(
                pack.relationship_direct_event_ids,
                [referenced_event_id],
            )
            self.assertEqual(
                pack.relationship_processing_runs[
                    0
                ].adjudication_base_direct_event_count,
                1,
            )

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(
                restored.relationship_direct_event_ids,
                [referenced_event_id],
            )
            target.close()

    def test_later_backdated_direct_duplicate_does_not_change_frozen_outcome(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            profile = source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            run = source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            accepted = source.list_relationship_events(
                "agent-lumi",
                "user-chen",
            )[0]
            source.storage.append_relationship_event(
                RelationshipEvent(
                    event_id="later-backdated-duplicate",
                    relationship_id=profile.relationship_id,
                    event_type=accepted.event_type,
                    content=accepted.content,
                    occurred_at=accepted.occurred_at,
                    recorded_at="2000-01-01T00:00:00+00:00",
                )
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()

            self.assertEqual(
                run.adjudication_base_direct_event_count,
                0,
            )
            self.assertEqual(
                pack.relationship_direct_event_ids,
                ["later-backdated-duplicate"],
            )

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.import_memory(pack)
            target.import_memory(pack)
            self.assertEqual(
                target.list_relationship_processing_runs(
                    "agent-lumi",
                    "user-chen",
                ),
                [run],
            )
            target.close()

    def test_legitimate_rejected_candidate_round_trips_exactly(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(
                    "rejected"
                ),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            run = source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.import_memory(pack)
            self.assertEqual(
                target.list_relationship_processing_runs(
                    "agent-lumi",
                    "user-chen",
                ),
                [run],
            )
            target.close()

    def test_direct_journal_can_repeat_an_adjudicated_event_id(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            run = source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            accepted = source.list_relationship_events(
                "agent-lumi",
                "user-chen",
            )[0]
            source.storage.append_relationship_event(accepted)
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()

            self.assertEqual(
                pack.relationship_direct_event_ids,
                [accepted.event_id],
            )
            self.assertEqual(
                pack.relationship_adjudications[0].receipt.event_ids,
                (accepted.event_id,),
            )

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(
                restored.relationship_direct_event_ids,
                [accepted.event_id],
            )
            self.assertEqual(
                restored.relationship_processing_runs,
                [run],
            )
            target.close()

    def test_processing_receipts_cannot_be_downgraded_by_deleting_runs(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            tampered = MemoryPack.from_json(
                source.export_memory("agent-lumi", "user-chen").to_json()
            )
            source.close()

            fabricated_event = replace(
                tampered.relationship_events[0],
                content="FABRICATED EVENT CONTENT",
            )
            original_adjudication = tampered.relationship_adjudications[0]
            tampered.relationship_events = [fabricated_event]
            tampered.relationship_adjudications = [
                replace(
                    original_adjudication,
                    events=(fabricated_event,),
                )
            ]
            tampered.relationship_processing_runs = []
            tampered.persona_reflection_decisions = []
            tampered.core_memory = "must not be partially imported"

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            with self.assertRaisesRegex(
                ValueError,
                "require their processing runs",
            ):
                target.import_memory(tampered)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(restored.core_memory, "")
            self.assertIsNone(restored.relationship)
            target.close()

    def test_existing_adjudication_conflict_fails_before_import_writes(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()
            pack.core_memory = "must not be partially imported"

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.storage.create_relationship(pack.relationship)
            original = pack.relationship_adjudications[0]
            conflicting = replace(
                original,
                receipt=replace(
                    original.receipt,
                    outcome=DecisionOutcome.REJECTED,
                    reason_codes=("evidence_source_not_found",),
                    evidence=(),
                    event_ids=(),
                    related_event_id=None,
                    pivotal_eligible=False,
                ),
                events=(),
            )
            target.storage.commit_relationship_adjudication(conflicting)

            with self.assertRaisesRegex(
                ValueError,
                "conflicts with the target decision journal",
            ):
                target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(restored.core_memory, "")
            self.assertEqual(restored.turn_records, [])
            self.assertEqual(restored.relationship_processing_runs, [])
            self.assertEqual(
                restored.relationship_adjudications,
                [conflicting],
            )
            target.close()

    def test_cross_journal_event_conflict_fails_before_import_writes(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()
            pack.core_memory = "must not be partially imported"
            accepted = pack.relationship_adjudications[0].events[0]

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.storage.create_relationship(pack.relationship)
            target.storage.append_relationship_event(
                replace(
                    accepted,
                    content="CONFLICTING TARGET EVENT",
                )
            )

            with self.assertRaisesRegex(
                ValueError,
                "conflicts with the target relationship history",
            ):
                target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(restored.core_memory, "")
            self.assertEqual(restored.turn_records, [])
            self.assertEqual(restored.relationship_processing_runs, [])
            self.assertEqual(restored.relationship_adjudications, [])
            target.close()

    def test_persona_growth_conflict_fails_before_import_writes(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_UniqueRelationshipExtractor(),
                persona_reflection_interpreter=_ReflectionInterpreter(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            runs = []
            for turn_id, user_message in (
                ("turn-snow", "The snow is beautiful."),
                ("turn-rain", "The rain sounds gentle."),
            ):
                source.record_turn(
                    "agent-lumi",
                    "user-chen",
                    user_message,
                    "I want to remember this moment.",
                    turn_id=turn_id,
                    delivery_exception=(
                        _preexisting_visible_exchange_delivery_exception()
                    ),
                )
                runs.append(
                    source.process_relationship_turn(
                        "agent-lumi",
                        "user-chen",
                        turn_id,
                    )
                )
            proposal = source.propose_persona_growth(
                "agent-lumi",
                "user-chen",
                {
                    "intent_key": "value-ordinary-time",
                    "review_id": "review-growth-import",
                    "statement": "I am learning to value ordinary time.",
                    "rationale": "Two distinct shared experiences support it.",
                    "proposed_changes": {"ordinary_time": "valued"},
                    "supporting_event_ids": [
                        event_id
                        for run in runs
                        for event_id in run.event_ids
                    ],
                    "trigger_kind": "accumulation",
                },
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()
            # Simulate a4-a6/growth-only portability without any a7 ledger.
            pack.relationship_processing_runs = []
            pack.persona_reflection_decisions = []
            pack.relationship_adjudications = []
            pack.turn_records = []
            pack.relationship_direct_event_ids = []
            pack.core_memory = "must not be partially imported"

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.storage.create_relationship(pack.relationship)
            conflicting = replace(
                proposal,
                statement="A conflicting target proposal.",
            )
            target.storage.save_persona_growth_proposal(conflicting)

            with self.assertRaisesRegex(
                ValueError,
                "persona growth conflicts with the target proposal history",
            ):
                target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(restored.core_memory, "")
            self.assertEqual(restored.turn_records, [])
            self.assertEqual(restored.relationship_adjudications, [])
            self.assertEqual(restored.relationship_events, [])
            self.assertEqual(
                restored.persona_growth_proposals,
                [conflicting],
            )
            target.close()

    def test_processing_import_rejects_divergent_target_journal_order(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_UniqueRelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            direct_events = [
                source.record_relationship_event(
                    "agent-lumi",
                    "user-chen",
                    RelationshipEventType.OBSERVATION,
                    content,
                    event_id=event_id,
                )
                for event_id, content in (
                    ("direct-first", "The first direct event."),
                    ("direct-second", "The second direct event."),
                )
            ]
            for turn_id, user_message in (
                ("turn-snow", "The snow is beautiful."),
                ("turn-rain", "The rain sounds gentle."),
            ):
                source.record_turn(
                    "agent-lumi",
                    "user-chen",
                    user_message,
                    "I want to remember this moment.",
                    turn_id=turn_id,
                    delivery_exception=(
                        _preexisting_visible_exchange_delivery_exception()
                    ),
                )
                source.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    turn_id,
                )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()
            pack.core_memory = "must not be partially imported"

            target_direct = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target-direct", "memory.db")
                )
            )
            target_direct.storage.create_relationship(pack.relationship)
            for event in reversed(direct_events):
                target_direct.storage.append_relationship_event(event)
            with self.assertRaisesRegex(
                ValueError,
                "direct-event journal is not prefix-compatible",
            ):
                target_direct.import_memory(pack)
            self.assertEqual(
                target_direct.export_memory(
                    "agent-lumi",
                    "user-chen",
                ).core_memory,
                "",
            )
            target_direct.close()

            target_adjudication = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(
                        root,
                        "target-adjudication",
                        "memory.db",
                    )
                )
            )
            target_adjudication.storage.create_relationship(
                pack.relationship
            )
            for record in reversed(pack.relationship_adjudications):
                target_adjudication.storage.commit_relationship_adjudication(
                    record
                )
            with self.assertRaisesRegex(
                ValueError,
                "adjudication journal is not prefix-compatible",
            ):
                target_adjudication.import_memory(pack)
            restored = target_adjudication.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(restored.core_memory, "")
            self.assertEqual(restored.turn_records, [])
            self.assertEqual(restored.relationship_processing_runs, [])
            target_adjudication.close()

    def test_target_temporal_lifecycle_conflict_fails_before_import_writes(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            promise = source.record_promise(
                "agent-lumi",
                "user-chen",
                "Bring the paper crane.",
                ["agent"],
                event_id="promise-paper-crane",
            )
            resolution_turn = source_turn(
                "turn-resolve-paper-crane",
                [("agent", "I brought the paper crane.")],
            )
            resolution_result = source.adjudicate_relationship_candidates(
                "agent-lumi",
                "user-chen",
                resolution_turn,
                [
                    temporal_candidate(
                        "resolve-paper-crane",
                        "promise_resolution",
                        {
                            "payload_type": "promise_resolution",
                            "promise_event_id": promise.event_id,
                            "resolution_kind": "fulfilled",
                        },
                        resolution_turn,
                        summary="The paper-crane promise was fulfilled.",
                    )
                ],
            )
            self.assertIsInstance(
                resolution_result.records[0].events[0].temporal_payload,
                PromiseResolution,
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()
            pack.core_memory = "must not be partially imported"

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.storage.create_relationship(pack.relationship)
            target.storage.append_relationship_event(promise)
            cancelled = target.resolve_promise(
                "agent-lumi",
                "user-chen",
                promise.event_id,
                PromiseResolutionKind.CANCELLED,
                event_id="target-cancelled-paper-crane",
            )

            with self.assertRaisesRegex(
                ValueError,
                "target temporal lifecycle",
            ):
                target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(restored.core_memory, "")
            self.assertEqual(restored.turn_records, [])
            self.assertEqual(restored.relationship_adjudications, [])
            self.assertEqual(restored.relationship_processing_runs, [])
            self.assertEqual(
                restored.relationship_direct_event_ids,
                [promise.event_id, cancelled.event_id],
            )
            target.close()

    def test_timeline_identity_conflict_fails_before_import_writes(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()
            entry = TimelineEntry(
                timeline_entry_id="timeline-conflict",
                relationship_id=pack.relationship.relationship_id,
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                content="The portable Timeline entry.",
                recorded_at="2026-07-30T00:00:00+00:00",
                source_turn_id="turn-snow",
                source_archival_id="archive-timeline-conflict",
                extractor_descriptor=ExtractorDescriptor(
                    extractor_id="tests.timeline",
                    extractor_version="1",
                    extraction_schema_version="1",
                ),
            )
            pack.timeline_entries = [entry]
            pack.core_memory = "must not be partially imported"

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.storage.create_relationship(pack.relationship)
            conflicting = replace(
                entry,
                content="A conflicting target Timeline entry.",
            )
            target.storage.import_timeline_entries(
                pack.agent_id,
                pack.user_id,
                [conflicting],
            )

            with self.assertRaisesRegex(
                ValueError,
                "Timeline entry conflicts with target history",
            ):
                target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(restored.core_memory, "")
            self.assertEqual(restored.turn_records, [])
            self.assertEqual(restored.relationship_adjudications, [])
            self.assertEqual(restored.relationship_processing_runs, [])
            self.assertEqual(restored.timeline_entries, [conflicting])
            target.close()

    def test_bound_pack_requires_the_complete_immutable_relationship_identity(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
                persona_reflection_interpreter=_ReflectionInterpreter(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()
            pack.core_memory = "must not be partially imported"

            incompatible_profile = replace(
                pack.relationship,
                persona_id="different-persona",
                agent_identity_id="different-agent-identity",
                user_identity_id="different-user-identity",
                blueprint=replace(
                    pack.relationship.blueprint,
                    blueprint_id="different-blueprint",
                ),
            )
            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.storage.create_relationship(incompatible_profile)

            with self.assertRaisesRegex(
                ValueError,
                "immutable relationship or Character Blueprint identity",
            ):
                target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(restored.relationship, incompatible_profile)
            self.assertEqual(restored.core_memory, "")
            self.assertEqual(restored.turn_records, [])
            self.assertEqual(restored.relationship_adjudications, [])
            self.assertEqual(restored.relationship_processing_runs, [])
            target.close()

    def test_globally_occupied_relationship_id_fails_before_payload_writes(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()
            pack.core_memory = "must not be partially imported"

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            occupied_profile = replace(
                pack.relationship,
                persona_id="occupied-persona",
                agent_identity_id="occupied-agent-identity",
                user_identity_id="occupied-user-identity",
                agent_id="agent-other",
                user_id="user-other",
                blueprint=replace(
                    pack.relationship.blueprint,
                    blueprint_id="occupied-blueprint",
                ),
            )
            target.storage.create_relationship(occupied_profile)

            with self.assertRaisesRegex(
                ValueError,
                "exact relationship identity conflicts",
            ):
                target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertIsNone(restored.relationship)
            self.assertEqual(restored.core_memory, "")
            self.assertEqual(restored.turn_records, [])
            self.assertEqual(restored.relationship_events, [])
            self.assertEqual(
                target.storage.get_relationship(
                    "agent-other",
                    "user-other",
                ),
                occupied_profile,
            )
            target.close()

    def test_target_adjudication_superset_cannot_ambiguate_reflection_binding(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
                persona_reflection_interpreter=_ReflectionInterpreter(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()
            pack.core_memory = "must not be partially imported"

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.storage.create_relationship(pack.relationship)
            original = pack.relationship_adjudications[0]
            target.storage.commit_relationship_adjudication(original)
            ambiguous = replace(
                original,
                receipt=replace(
                    original.receipt,
                    decision_id="legacy-duplicate-event-decision",
                    candidate_key="legacy-duplicate-event-candidate",
                    contract_version="0.4.0a4",
                ),
            )
            target.storage.commit_relationship_adjudication(ambiguous)

            with self.assertRaisesRegex(
                ValueError,
                "requires exactly one accepted adjudication",
            ):
                target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(restored.core_memory, "")
            self.assertEqual(restored.turn_records, [])
            self.assertEqual(
                restored.relationship_adjudications,
                [original, ambiguous],
            )
            self.assertEqual(restored.persona_reflection_decisions, [])
            self.assertEqual(restored.relationship_processing_runs, [])
            target.close()

    def test_processing_turn_timestamp_conflict_fails_before_import_writes(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()
            pack.core_memory = "must not be partially imported"

            original_turn = pack.turn_records[0]
            shifted_transcript = replace(
                original_turn.transcript,
                user_message=replace(
                    original_turn.transcript.user_message,
                    recorded_at="2020-01-01T00:00:00+00:00",
                ),
                agent_message=replace(
                    original_turn.transcript.agent_message,
                    recorded_at="2020-01-01T00:00:01+00:00",
                ),
            )
            shifted_turn = replace(
                original_turn,
                transcript=shifted_transcript,
                opened_at="2020-01-01T00:00:00+00:00",
                completed_at="2020-01-01T00:00:01+00:00",
            )
            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            target.storage.create_relationship(pack.relationship)
            target.storage.create_turn_record(shifted_turn)

            with self.assertRaisesRegex(
                ValueError,
                "exact relationship-processing provenance",
            ):
                target.import_memory(pack)
            restored = target.export_memory(
                "agent-lumi",
                "user-chen",
            )
            self.assertEqual(restored.core_memory, "")
            self.assertEqual(restored.turn_records, [shifted_turn])
            self.assertEqual(restored.relationship_adjudications, [])
            self.assertEqual(restored.relationship_processing_runs, [])
            target.close()

    def test_processing_run_identity_and_versions_are_canonical(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "The snow is beautiful.",
                "Yes. I want to remember this quiet moment.",
                turn_id="turn-snow",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-snow",
            )
            exported = source.export_memory("agent-lumi", "user-chen")
            source.close()

            mutations = (
                (
                    "processing-id",
                    {"processing_id": "fake-processing-id"},
                    "processing ID does not match",
                ),
                (
                    "rule-version",
                    {"rule_version": "fake-rule-version"},
                    "unsupported rule or contract version",
                ),
                (
                    "contract-version",
                    {"contract_version": "fake-contract-version"},
                    "unsupported rule or contract version",
                ),
            )
            for index, (case, changes, message) in enumerate(mutations):
                with self.subTest(case=case):
                    pack = MemoryPack.from_json(exported.to_json())
                    pack.relationship_processing_runs = [
                        replace(
                            pack.relationship_processing_runs[0],
                            **changes,
                        )
                    ]
                    pack.core_memory = "must not be partially imported"
                    target = ERIIEngine(
                        storage_driver=SQLiteStorage(
                            os.path.join(
                                root,
                                f"canonical-target-{index}",
                                "memory.db",
                            )
                        )
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        target.import_memory(pack)
                    restored = target.export_memory(
                        "agent-lumi",
                        "user-chen",
                    )
                    self.assertIsNone(restored.relationship)
                    self.assertEqual(restored.core_memory, "")
                    self.assertEqual(restored.turn_records, [])
                    target.close()

    def test_import_holds_the_online_relationship_processing_guard(self):
        with tempfile.TemporaryDirectory() as root:
            imported_event_id = "imported-direct-event"
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source"))
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            source.record_relationship_event(
                "agent-lumi",
                "user-chen",
                RelationshipEventType.OBSERVATION,
                "A portable direct event.",
                event_id=imported_event_id,
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()

            storage = _BlockingImportFileStorage(
                os.path.join(root, "target"),
                imported_event_id,
            )
            target = ERIIEngine(storage_driver=storage)
            with ThreadPoolExecutor(max_workers=2) as pool:
                import_future = pool.submit(target.import_memory, pack)
                self.assertTrue(
                    storage.import_append_entered.wait(timeout=2)
                )
                online_future = pool.submit(
                    target.record_relationship_event,
                    "agent-lumi",
                    "user-chen",
                    RelationshipEventType.OBSERVATION,
                    "An online event that must wait.",
                    event_id="online-direct-event",
                )
                try:
                    with self.assertRaises(TimeoutError):
                        online_future.result(timeout=0.1)
                finally:
                    storage.release_import_append.set()
                import_future.result(timeout=5)
                online_event = online_future.result(timeout=5)

            self.assertEqual(online_event.event_id, "online-direct-event")
            self.assertEqual(
                target.export_memory(
                    "agent-lumi",
                    "user-chen",
                ).relationship_direct_event_ids,
                [imported_event_id, "online-direct-event"],
            )
            target.close()

    def test_correction_cannot_borrow_another_reflections_source_binding(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_UniqueRelationshipExtractor(),
                persona_reflection_interpreter=_ReflectionInterpreter(),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi values grounded shared experiences.",
            )
            for turn_id, user_message in (
                ("turn-snow", "The snow is beautiful."),
                ("turn-rain", "The rain sounds gentle."),
            ):
                source.record_turn(
                    "agent-lumi",
                    "user-chen",
                    user_message,
                    "I want to remember this moment.",
                    turn_id=turn_id,
                    delivery_exception=(
                        _preexisting_visible_exchange_delivery_exception()
                    ),
                )
                source.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    turn_id,
                )
            first_reflection, second_reflection = (
                source.list_persona_reflections(
                    "agent-lumi",
                    "user-chen",
                )
            )
            correction = source.correct_persona_reflection(
                "agent-lumi",
                "user-chen",
                first_reflection.reflection_id,
                interpretation_id="correct-first-v1",
            )
            tampered = MemoryPack.from_json(
                source.export_memory("agent-lumi", "user-chen").to_json()
            )
            source.close()

            second_original = next(
                item
                for item in tampered.persona_reflection_decisions
                if item.record_kind
                == PersonaReflectionRecordKind.REFLECTION
                and item.event_id == second_reflection.event_id
            )
            forged_record = replace(
                correction.reflection_record,
                event_id=second_original.event_id,
                context_provenance=second_original.context_provenance,
            )
            forged_correction = replace(
                correction,
                event_id=second_original.event_id,
                source_turn_id=second_original.source_turn_id,
                source_revision=second_original.source_revision,
                context_provenance=second_original.context_provenance,
                reflection_record=forged_record,
            )
            tampered.persona_reflection_decisions = [
                forged_correction if item.decision_id == correction.decision_id else item
                for item in tampered.persona_reflection_decisions
            ]

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            with self.assertRaisesRegex(ValueError, "target reflection"):
                target.import_memory(tampered)
            self.assertIsNone(
                target.storage.get_relationship(
                    "agent-lumi",
                    "user-chen",
                )
            )
            target.close()

    def test_a7_ledgers_cannot_be_remapped_to_another_user(self):
        with tempfile.TemporaryDirectory() as root:
            source = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "source")),
                relationship_event_extractor=_RelationshipExtractor("none"),
            )
            source.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi keeps each relationship isolated.",
            )
            source.record_turn(
                "agent-lumi",
                "user-chen",
                "Hello.",
                "Hello.",
                turn_id="turn-hello",
                delivery_exception=(
                    _preexisting_visible_exchange_delivery_exception()
                ),
            )
            source.process_relationship_turn(
                "agent-lumi",
                "user-chen",
                "turn-hello",
            )
            pack = source.export_memory("agent-lumi", "user-chen")
            source.close()

            target = ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(root, "target", "memory.db")
                )
            )
            with self.assertRaises(ValueError):
                target.import_memory(
                    pack,
                    agent_id="agent-lumi",
                    user_id="another-user",
                )
            self.assertIsNone(
                target.storage.get_relationship(
                    "agent-lumi",
                    "another-user",
                )
            )
            target.close()


if __name__ == "__main__":
    unittest.main()
