"""Contracts for snapshot-bound, zero-write MemoryPack transfer planning."""

import ast
import base64
from dataclasses import FrozenInstanceError, replace
import inspect
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import uuid

from erii import ERIIEngine, FileStorage, SQLiteStorage
from erii.core.adjudication import relationship_adjudication_baseline_fingerprint
from erii.core.persona_compilation import PersonaCompiler
from erii.core.relationship_processing import RelationshipProcessingCoordinator
import erii._engine.memory_pack_transfer as transfer_module
from erii._engine.memory_pack_transfer import (
    MemoryPackCoreWriteMode,
    MemoryPackExportSnapshot,
    MemoryPackNodeWriteMode,
    MemoryPackTargetReadRecorder,
    StaleMemoryPackTransferPlanError,
    analyze_memory_pack_source,
    assemble_memory_pack_export,
    bind_memory_pack_transfer_plan,
    execute_memory_pack_relationship_history,
    execute_memory_pack_writes,
    plan_memory_pack_persona_compilation_writes,
    plan_memory_pack_persona_growth_writes,
    plan_memory_pack_writes,
    replay_memory_pack_target_read_set,
    require_memory_pack_transfer_plan_current,
)
from erii.models.archival import (
    ArchivalOutcomeCode,
    ArchivalPhase,
    ArchivalReceipt,
    ArchivalRecord,
    ArchivalStatus,
    ArchivalTombstone,
)
from erii.models.adjudication import (
    AdjudicationRecord,
    DecisionOutcome,
    DecisionReceipt,
    GrowthTriggerKind,
    PersonaGrowthProposal,
    SourceProcessingMode,
)
from erii.models.consolidation import (
    PersonaNoReflectionDecision,
    PersonaReflectionDecisionRecord,
    PersonaReflectionRecordKind,
    ReflectionContextProvenance,
    ReflectionInterpreterDescriptor,
    ReflectionProvenanceState,
    RelationshipNoEventDecision,
    RelationshipProcessingOutcome,
    RelationshipProcessingRun,
    RelationshipProcessingStatus,
)
from erii.models.consequence import (
    NarrativeTensionLink,
    NarrativeTensionOutcome,
    RelationshipConsequence,
    RelationshipConsequenceKind,
)
from erii.models.node import MemoryNode
from erii.models.pack import MemoryPack
from erii.models.provenance import ExtractorDescriptor
from erii.models.relationship import RelationshipEvent, RelationshipEventType
from erii.models.temporal import (
    PromiseResolution,
    PromiseResolutionKind,
    PromiseSpec,
)
from erii.storage.base import BaseStorage


FIXTURE_SOURCE = (
    Path(__file__).parent
    / "fixtures"
    / "lifecycle"
    / "memory-pack-v0.4.0a7"
    / "source.erii"
)


class _StaleTargetFileStorage(FileStorage):
    def __init__(self, root_dir: str) -> None:
        super().__init__(root_dir)
        self.relationship_reads = 0

    def get_relationship(self, agent_id: str, user_id: str):
        profile = super().get_relationship(agent_id, user_id)
        self.relationship_reads += 1
        if profile is not None and self.relationship_reads >= 4:
            return replace(profile, manifest_id="manifest-added-after-preflight")
        return profile


class _StaleReadSetFileStorage(FileStorage):
    def __init__(
        self,
        root_dir: str,
        stale_method: str,
        stale_processing_run=None,
    ) -> None:
        super().__init__(root_dir)
        self.stale_method = stale_method
        self.timeline_reads = 0
        self.turn_reads = 0
        self.save_nodes_calls = 0
        self.processing_reads = 0
        self.stale_processing_run = stale_processing_run

    def list_timeline_entries(self, agent_id: str, user_id: str):
        entries = super().list_timeline_entries(agent_id, user_id)
        self.timeline_reads += 1
        if self.stale_method == "timeline" and self.timeline_reads >= 2:
            return [replace(entries[0], content="changed after preflight")]
        return entries

    def list_turn_records(self, relationship_id: str):
        records = super().list_turn_records(relationship_id)
        self.turn_reads += 1
        if self.stale_method == "turn" and self.turn_reads >= 2:
            return [replace(records[0], source_revision="changed-after-preflight")]
        return records

    def list_relationship_processing_runs(self, relationship_id: str):
        runs = super().list_relationship_processing_runs(relationship_id)
        self.processing_reads += 1
        if self.stale_method == "processing" and self.processing_reads >= 3:
            return [self.stale_processing_run]
        return runs

    def save_nodes(self, agent_id: str, user_id: str, nodes):
        self.save_nodes_calls += 1
        return super().save_nodes(agent_id, user_id, nodes)


class _OpaqueArchivalFileStorage(FileStorage):
    def capture_archival_tombstone_validation_source(
        self,
        relationship_id,
        archival_ids,
    ):
        raise NotImplementedError("opaque archival validation")


class MemoryPackTransferPlanTests(unittest.TestCase):
    @staticmethod
    def _fixture_pack() -> MemoryPack:
        return MemoryPack.from_json(FIXTURE_SOURCE.read_text(encoding="utf-8"))

    @staticmethod
    def _export_snapshot(pack: MemoryPack) -> MemoryPackExportSnapshot:
        return MemoryPackExportSnapshot(
            agent_id=pack.agent_id,
            user_id=pack.user_id,
            core_memory=pack.core_memory,
            nodes=tuple(pack.nodes),
            legacy_timeline=(
                "[2026-08-14 09:30:00] structured legacy entry",
                "legacy entry without timestamp",
            ),
            timeline_entries=tuple(pack.timeline_entries),
            archival_tombstones=tuple(pack.archival_ledger),
            relationship=pack.relationship,
            relationship_events=tuple(pack.relationship_events),
            relationship_direct_event_ids=tuple(
                pack.relationship_direct_event_ids
            ),
            relationship_adjudications=tuple(
                pack.relationship_adjudications
            ),
            relationship_consequences=tuple(
                pack.relationship_consequences
            ),
            narrative_tension_links=tuple(pack.narrative_tension_links),
            persona_growth_proposals=tuple(pack.persona_growth_proposals),
            persona_compilation_proposals=tuple(
                pack.persona_compilation_proposals
            ),
            persona_manifests=tuple(pack.persona_manifests),
            turn_records=tuple(pack.turn_records),
            relationship_processing_runs=tuple(
                pack.relationship_processing_runs
            ),
            persona_reflection_decisions=tuple(
                pack.persona_reflection_decisions
            ),
            exported_at="2026-08-14 09:31:00",
        )

    @classmethod
    def _fixture_pack_with_processing_run(cls) -> MemoryPack:
        pack = cls._fixture_pack()
        turn = pack.turn_records[0]
        pack.relationship_processing_runs = [
            RelationshipProcessingRun(
                processing_id=RelationshipProcessingCoordinator.processing_id(
                    pack.relationship,
                    turn,
                    processing_mode=SourceProcessingMode.NORMAL,
                    reprocessing_id=None,
                ),
                relationship_id=pack.relationship.relationship_id,
                source_turn_id=turn.turn_id,
                source_revision=turn.source_revision,
                processing_mode=SourceProcessingMode.NORMAL,
                status=RelationshipProcessingStatus.COMPLETED,
                outcome=RelationshipProcessingOutcome.NO_RELATIONSHIP_EVENT,
                extractor_descriptor=ExtractorDescriptor(
                    extractor_id="tests.memory-pack-transfer",
                    extractor_version="1",
                    extraction_schema_version="1",
                ),
                frozen_decision=RelationshipNoEventDecision(
                    reason_code="ordinary_exchange"
                ),
                adjudication_base_fingerprint=(
                    relationship_adjudication_baseline_fingerprint((), ())
                ),
                completed_at="2026-08-14T00:00:00+00:00",
            )
        ]
        pack.relationship_direct_event_ids = [
            event.event_id for event in pack.relationship_events
        ]
        return pack

    @classmethod
    def _remappable_write_plan_pack(cls) -> MemoryPack:
        pack = cls._fixture_pack()
        relationship_id = pack.relationship.relationship_id
        promise_old = RelationshipEvent(
            event_id="promise-old",
            relationship_id=relationship_id,
            event_type=RelationshipEventType.PROMISE,
            content="Meet at the bridge.",
            temporal_payload=PromiseSpec(
                responsible_parties=("agent",),
                action="Meet at the bridge.",
            ),
            recorded_at="2026-08-14T00:00:00+00:00",
        )
        promise_new = RelationshipEvent(
            event_id="promise-new",
            relationship_id=relationship_id,
            event_type=RelationshipEventType.PROMISE,
            content="Meet at the station.",
            temporal_payload=PromiseSpec(
                responsible_parties=("agent",),
                action="Meet at the station.",
            ),
            recorded_at="2026-08-14T00:01:00+00:00",
        )
        resolution = RelationshipEvent(
            event_id="promise-resolution",
            relationship_id=relationship_id,
            event_type=RelationshipEventType.PROMISE_RESOLUTION,
            content="The station promise supersedes the bridge promise.",
            temporal_payload=PromiseResolution(
                promise_event_id=promise_old.event_id,
                resolution_kind=PromiseResolutionKind.SUPERSEDED,
                superseding_promise_event_id=promise_new.event_id,
            ),
            recorded_at="2026-08-14T00:02:00+00:00",
        )
        decision_id = "decision-accepted"
        adjudicated_event = RelationshipEvent(
            event_id="adjudicated-event",
            relationship_id=relationship_id,
            event_type=RelationshipEventType.SHARED_EXPERIENCE,
            content="They kept the station promise.",
            recorded_at="2026-08-14T00:03:00+00:00",
            metadata={
                "adjudication": {
                    "decision_id": decision_id,
                    "occurrence_fingerprint": "source-occurrence",
                    "occurrence_key": "station-kept",
                    "references": [promise_new.event_id],
                }
            },
        )
        receipt = DecisionReceipt(
            decision_id=decision_id,
            relationship_id=relationship_id,
            source_turn_id="portable-turn",
            source_revision="1",
            candidate_key="station-kept",
            candidate_fingerprint="candidate-fingerprint",
            batch_fingerprint="batch-fingerprint",
            occurrence_fingerprint="source-occurrence",
            outcome=DecisionOutcome.ACCEPTED,
            reason_codes=("accepted_by_policy",),
            extraction_confidence=1.0,
            interpretation_confidence=1.0,
            extractor_version="tests.memory-pack-transfer/1",
            contract_version="relationship-turn-adjudication-v1",
            rule_version="tests-rule/1",
            policy_version="tests-policy/1",
            event_ids=(adjudicated_event.event_id,),
            related_event_id=promise_new.event_id,
        )
        pack.turn_records = []
        pack.timeline_entries = []
        pack.relationship_events = [
            promise_old,
            promise_new,
            resolution,
            adjudicated_event,
        ]
        pack.relationship_direct_event_ids = [
            promise_old.event_id,
            promise_new.event_id,
            resolution.event_id,
        ]
        pack.relationship_adjudications = [
            AdjudicationRecord(receipt=receipt, events=(adjudicated_event,))
        ]
        pack.persona_growth_proposals = [
            PersonaGrowthProposal(
                proposal_id="growth-source",
                relationship_id=relationship_id,
                revision=1,
                intent_key="remember-station-promise",
                review_id="review-station-promise",
                statement="Retain the fulfilled station promise.",
                rationale="It is grounded in accepted relationship history.",
                proposed_changes={"voice": "steadier"},
                supporting_event_ids=(adjudicated_event.event_id,),
                trigger_kind=GrowthTriggerKind.PIVOTAL,
            )
        ]
        return pack

    def test_export_assembly_is_deterministic_and_storage_free(self) -> None:
        source = self._fixture_pack()
        before = source.to_dict()
        snapshot = self._export_snapshot(source)

        first = assemble_memory_pack_export(snapshot)
        second = assemble_memory_pack_export(snapshot)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(source.to_dict(), before)
        self.assertEqual(
            first.timeline,
            [
                {
                    "timestamp": "2026-08-14 09:30:00",
                    "content": "structured legacy entry",
                },
                {
                    "timestamp": "",
                    "content": "legacy entry without timestamp",
                },
            ],
        )
        self.assertEqual(first.exported_at, "2026-08-14 09:31:00")
        self.assertEqual(
            tuple(inspect.signature(assemble_memory_pack_export).parameters),
            ("snapshot",),
        )
        function_names = {
            node.id
            for node in ast.walk(
                ast.parse(inspect.getsource(assemble_memory_pack_export))
            )
            if isinstance(node, ast.Name)
        }
        self.assertNotIn("storage", function_names)
        with self.assertRaises(FrozenInstanceError):
            snapshot.core_memory = "changed"

    def test_export_assembly_preserves_portable_validation_failure(self) -> None:
        source = self._fixture_pack()
        snapshot = replace(
            self._export_snapshot(source),
            turn_records=(source.turn_records[0], source.turn_records[0]),
        )

        with self.assertRaisesRegex(
            ValueError,
            "persisted-Turn adjudications contain duplicate Source Turns",
        ):
            assemble_memory_pack_export(snapshot)

    @classmethod
    def _persona_compilation_pack(cls) -> MemoryPack:
        pack = cls._fixture_pack()
        source = pack.relationship.blueprint.source_text
        pending = PersonaCompiler.propose(
            pack.relationship.blueprint,
            {
                "compiler_version": "tests.memory-pack-transfer/1",
                "source_spans": [
                    {
                        "span_id": "span-all",
                        "start": 0,
                        "end": len(source),
                        "quote": source,
                    }
                ],
                "claims": [
                    {
                        "claim_id": "claim-source",
                        "kind": "identity",
                        "statement": "The imported source remains authoritative.",
                        "activation_tier": "foundation",
                        "basis": "explicit",
                        "source_span_ids": ["span-all"],
                    }
                ],
            },
            proposal_id="proposal-source",
            created_by="tests",
        )
        approved = PersonaCompiler.decide(
            pending,
            revision=1,
            actor_id="owner",
            decision="approve",
            decided_at="2026-08-14T00:00:00+00:00",
        )
        manifest = PersonaCompiler.manifest_from_approved(approved)
        pack.persona_compilation_proposals = [approved]
        pack.persona_manifests = [manifest]
        pack.relationship = replace(
            pack.relationship,
            manifest_id=manifest.manifest_id,
        )
        return pack

    @staticmethod
    def _tombstone(
        pack: MemoryPack,
        archival_id: str,
        *,
        relationship_id: str | None = None,
    ) -> ArchivalTombstone:
        return ArchivalTombstone(
            archival_id=archival_id,
            relationship_id=(
                relationship_id or pack.relationship.relationship_id
            ),
            agent_id=pack.agent_id,
            user_id=pack.user_id,
            source_turn_id=f"turn-{archival_id}",
            source_revision="1",
            status=ArchivalStatus.COMPLETED,
            outcome_code=ArchivalOutcomeCode.NO_MEMORY,
            terminal_at="2026-08-14T00:00:00+00:00",
            request_fingerprint=f"request-{archival_id}",
            idempotency_fingerprint=f"idempotency-{archival_id}",
        )

    @staticmethod
    def _pending_archival_record(
        pack: MemoryPack,
        archival_id: str,
    ) -> ArchivalRecord:
        return ArchivalRecord(
            receipt=ArchivalReceipt(
                archival_id=archival_id,
                relationship_id=pack.relationship.relationship_id,
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                source_turn_id=f"turn-{archival_id}",
                source_revision="1",
                status=ArchivalStatus.PENDING,
                phase=ArchivalPhase.EXTRACTION,
                extractor_descriptor=ExtractorDescriptor(
                    extractor_id="tests.memory-pack-transfer",
                    extractor_version="1",
                    extraction_schema_version="1",
                ),
                submitted_at="2026-08-14T00:00:00+00:00",
                updated_at="2026-08-14T00:00:00+00:00",
            ),
            request_fingerprint=f"request-{archival_id}",
            idempotency_fingerprint=f"idempotency-{archival_id}",
        )

    @staticmethod
    def _storage_factories(root: str):
        return (
            ("file", lambda: FileStorage(os.path.join(root, "file"))),
            (
                "sqlite",
                lambda: SQLiteStorage(os.path.join(root, "sqlite", "memory.db")),
            ),
        )

    @classmethod
    def _all_non_compilation_write_pack(cls) -> MemoryPack:
        """Builds one valid exact-restore pack exercising every executor batch."""
        pack = cls._remappable_write_plan_pack()
        fixture = cls._fixture_pack()
        turn = fixture.turn_records[0]
        event = pack.relationship_adjudications[0].events[0]
        decision_id = pack.relationship_adjudications[0].receipt.decision_id
        relationship_id = pack.relationship.relationship_id

        pack.version = "0.5.0a3"
        pack.turn_records = [turn]
        pack.timeline = []
        pack.timeline_entries = list(fixture.timeline_entries)
        pack.archival_ledger = [
            replace(
                cls._tombstone(pack, "executor-archival"),
                source_turn_id=turn.turn_id,
                source_revision=turn.source_revision,
            )
        ]
        consequence = RelationshipConsequence(
            consequence_id="executor-consequence",
            relationship_id=relationship_id,
            tension_id="executor-tension",
            source_turn_id=turn.turn_id,
            source_revision=turn.source_revision,
            source_decision_id=decision_id,
            source_event_id=event.event_id,
            source_message_id="executor-message",
            effects=(RelationshipConsequenceKind.TEMPORARY_DISTANCE,),
            summary="The accepted event had a durable consequence.",
            recorded_at="2026-08-14T00:06:00+00:00",
        )
        pack.relationship_consequences = [consequence]
        pack.narrative_tension_links = [
            NarrativeTensionLink(
                link_id="executor-tension-link",
                relationship_id=relationship_id,
                tension_id=consequence.tension_id,
                consequence_id=consequence.consequence_id,
                source_turn_id=turn.turn_id,
                source_revision=turn.source_revision,
                source_decision_id=decision_id,
                source_event_id=event.event_id,
                outcome=NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
                summary="The consequence remains explicitly unresolved.",
                recorded_at="2026-08-14T00:07:00+00:00",
            )
        ]
        pack.persona_reflection_decisions = [
            PersonaReflectionDecisionRecord(
                decision_id="executor-reflection-decision",
                relationship_id=relationship_id,
                event_id=event.event_id,
                source_turn_id=turn.turn_id,
                source_revision=turn.source_revision,
                interpreter_descriptor=ReflectionInterpreterDescriptor(
                    interpreter_id="tests.memory-pack-transfer",
                    interpreter_version="1",
                ),
                decision=PersonaNoReflectionDecision(
                    reason_code="reflection_not_needed"
                ),
                context_provenance=ReflectionContextProvenance(
                    relationship_event_id=event.event_id,
                    provenance_state=(
                        ReflectionProvenanceState.LEGACY_UNAVAILABLE
                    ),
                ),
                record_kind=PersonaReflectionRecordKind.LEGACY,
                recorded_at="2026-08-14T00:08:00+00:00",
            )
        ]
        pack.relationship_processing_runs = list(
            cls._fixture_pack_with_processing_run().relationship_processing_runs
        )
        return pack

    @staticmethod
    def _semantic_write_snapshot(
        storage,
        agent_id: str,
        user_id: str,
        relationship_id: str,
    ) -> dict:
        profile = storage.get_relationship(agent_id, user_id)

        def documents(values):
            return tuple(item.to_dict() for item in values)

        return {
            "relationship": profile.to_dict() if profile is not None else None,
            "nodes": documents(storage.load_nodes(agent_id, user_id)),
            "core_memory": storage.get_core_memory(agent_id, user_id),
            "legacy_timeline": tuple(
                storage.get_recent_timeline(
                    agent_id,
                    user_id,
                    limit=100,
                )
            ),
            "timeline": documents(
                storage.list_timeline_entries(agent_id, user_id)
            ),
            "turns": documents(storage.list_turn_records(relationship_id)),
            "tombstones": documents(
                storage.list_archival_tombstones(relationship_id)
            ),
            "events": documents(storage.list_relationship_events(relationship_id)),
            "adjudications": documents(
                storage.list_relationship_adjudications(relationship_id)
            ),
            "consequences": documents(
                storage.list_relationship_consequences(relationship_id)
            ),
            "tension_links": documents(
                storage.list_narrative_tension_links(relationship_id)
            ),
            "growth": documents(
                storage.list_persona_growth_proposals(relationship_id)
            ),
            "reflections": documents(
                storage.list_persona_reflection_decisions(relationship_id)
            ),
            "processing_runs": documents(
                storage.list_relationship_processing_runs(relationship_id)
            ),
        }

    @staticmethod
    def _create_target_relationship(storage, pack: MemoryPack) -> None:
        storage.create_relationship(pack.relationship)

    @staticmethod
    def _create_other_relationship(storage, pack: MemoryPack) -> None:
        storage.create_relationship(
            replace(
                pack.relationship,
                relationship_id="other-relationship",
                persona_id="other-persona",
                agent_identity_id="other-agent-identity",
                user_identity_id="other-user-identity",
                agent_id="other-agent",
                user_id="other-user",
                blueprint=replace(
                    pack.relationship.blueprint,
                    blueprint_id="other-blueprint",
                ),
                manifest_id=None,
            )
        )

    def test_plan_is_deterministic_frozen_and_does_not_mutate_inputs(self) -> None:
        pack = self._fixture_pack()
        before = pack.to_dict()
        source = analyze_memory_pack_source(pack)

        first = bind_memory_pack_transfer_plan(
            source,
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=False,
        )
        second = bind_memory_pack_transfer_plan(
            source,
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=False,
        )

        self.assertEqual(first, second)
        self.assertEqual(pack.to_dict(), before)
        self.assertEqual(first.target.relationship_id, pack.relationship.relationship_id)
        self.assertEqual(first.target_reads.observations, ())
        self.assertEqual(len(first.target_reads.fingerprint), 64)
        self.assertEqual(first.source.analysis, source.analysis)
        self.assertEqual(len(first.source.analysis_fingerprint), 64)
        self.assertEqual(len(first.fingerprint), 64)
        with self.assertRaises(FrozenInstanceError):
            first.overwrite = True

    def test_write_plan_freezes_modes_payloads_and_legacy_batch_order(self) -> None:
        pack = self._fixture_pack()
        pack.nodes = [
            MemoryNode(
                node_id="write-plan-node",
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                content="Frozen at planning time.",
            )
        ]
        before = pack.to_dict()

        first = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=False,
        )
        second = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=False,
        )
        overwrite = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=True,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(pack.to_dict(), before)
        self.assertEqual(first.node_write_mode, MemoryPackNodeWriteMode.MERGE)
        self.assertEqual(first.core_write_mode, MemoryPackCoreWriteMode.IF_EMPTY)
        self.assertEqual(overwrite.node_write_mode, MemoryPackNodeWriteMode.REPLACE)
        self.assertEqual(overwrite.core_write_mode, MemoryPackCoreWriteMode.ALWAYS)
        self.assertEqual(
            first.batch_order,
            (
                "nodes",
                "core_memory",
                "turn_records",
                "timeline_entries",
                "relationship_history",
            ),
        )
        materialized_nodes = first.memory_nodes()
        self.assertEqual(materialized_nodes[0].content, "Frozen at planning time.")
        materialized_nodes[0].content = "changed by caller"
        self.assertEqual(
            first.memory_nodes()[0].content,
            "Frozen at planning time.",
        )
        with self.assertRaises(FrozenInstanceError):
            first.target_agent = "changed"

    def test_write_plan_preserves_exact_relationship_ids(self) -> None:
        pack = self._remappable_write_plan_pack()
        before = pack.to_dict()

        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=False,
        )

        self.assertEqual(pack.to_dict(), before)
        self.assertIsNotNone(plan.relationship)
        relationship = plan.relationship
        self.assertEqual(
            [item.event_id for item in relationship.direct_events],
            pack.relationship_direct_event_ids,
        )
        self.assertEqual(
            relationship.adjudications[0].receipt.decision_id,
            "decision-accepted",
        )
        self.assertEqual(
            relationship.adjudications[0].events[0].event_id,
            "adjudicated-event",
        )
        self.assertEqual(
            relationship.persona_growth_proposals[0].proposal_id,
            "growth-source",
        )

    def test_write_plan_stably_remaps_ids_and_temporal_references(self) -> None:
        pack = self._remappable_write_plan_pack()
        target_relationship_id = "target-relationship"
        target_profile = replace(
            pack.relationship,
            relationship_id=target_relationship_id,
            agent_id="target-agent",
            user_id="target-user",
        )
        before = pack.to_dict()

        first = plan_memory_pack_writes(
            pack,
            target_profile.agent_id,
            target_profile.user_id,
            target_profile,
            overwrite=False,
        )
        second = plan_memory_pack_writes(
            pack,
            target_profile.agent_id,
            target_profile.user_id,
            target_profile,
            overwrite=False,
        )

        self.assertEqual(first, second)
        self.assertEqual(pack.to_dict(), before)
        relationship = first.relationship
        mapped_direct = {item.content: item for item in relationship.direct_events}
        mapped_old = mapped_direct["Meet at the bridge."]
        mapped_new = mapped_direct["Meet at the station."]
        mapped_resolution = mapped_direct[
            "The station promise supersedes the bridge promise."
        ]
        self.assertEqual(
            mapped_old.event_id,
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"erii:{target_relationship_id}:promise-old",
                )
            ),
        )
        self.assertEqual(
            mapped_resolution.temporal_payload.promise_event_id,
            mapped_old.event_id,
        )
        self.assertEqual(
            mapped_resolution.temporal_payload.superseding_promise_event_id,
            mapped_new.event_id,
        )

        mapped_record = relationship.adjudications[0]
        expected_decision_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"erii:{target_relationship_id}:decision:portable-turn:1:"
                    "normal::station-kept"
                ),
            )
        )
        expected_adjudicated_event_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{expected_decision_id}:event",
            )
        )
        self.assertEqual(mapped_record.receipt.decision_id, expected_decision_id)
        self.assertEqual(
            mapped_record.events[0].event_id,
            expected_adjudicated_event_id,
        )
        self.assertEqual(mapped_record.receipt.related_event_id, mapped_new.event_id)
        self.assertEqual(
            mapped_record.events[0].metadata["adjudication"]["references"],
            (mapped_new.event_id,),
        )

        mapped_growth = relationship.persona_growth_proposals[0]
        self.assertEqual(
            mapped_growth.proposal_id,
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"erii:{target_relationship_id}:growth:"
                        "review-station-promise:remember-station-promise"
                    ),
                )
            ),
        )
        self.assertEqual(
            mapped_growth.supporting_event_ids,
            (expected_adjudicated_event_id,),
        )

    def test_persona_compilation_plan_preserves_and_remaps_stable_ids(self) -> None:
        pack = self._persona_compilation_pack()
        before = pack.to_dict()

        exact = plan_memory_pack_persona_compilation_writes(
            pack,
            pack.relationship,
        )
        target_blueprint_id = "target-blueprint"
        remapped_target = replace(
            pack.relationship,
            relationship_id="target-relationship",
            agent_id="target-agent",
            user_id="target-user",
            blueprint=replace(
                pack.relationship.blueprint,
                blueprint_id=target_blueprint_id,
            ),
            manifest_id=None,
        )
        remapped = plan_memory_pack_persona_compilation_writes(
            pack,
            remapped_target,
        )
        repeated = plan_memory_pack_persona_compilation_writes(
            pack,
            remapped_target,
        )

        self.assertEqual(pack.to_dict(), before)
        self.assertEqual(remapped, repeated)
        self.assertEqual(exact.proposals[0].proposal_id, "proposal-source")
        expected_proposal_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"erii:{target_blueprint_id}:persona-compilation:"
                    "proposal-source"
                ),
            )
        )
        self.assertEqual(remapped.proposals[0].proposal_id, expected_proposal_id)
        self.assertEqual(
            remapped.manifests[0].approved_proposal_id,
            expected_proposal_id,
        )
        self.assertEqual(
            remapped.selected_manifest.manifest_id,
            remapped.manifests[0].manifest_id,
        )
        self.assertNotEqual(
            remapped.manifests[0].manifest_id,
            pack.persona_manifests[0].manifest_id,
        )

    def test_write_planner_interfaces_have_no_storage_dependency(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(plan_memory_pack_writes).parameters),
            (
                "pack",
                "target_agent",
                "target_user",
                "target_profile",
                "overwrite",
            ),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    plan_memory_pack_persona_compilation_writes
                ).parameters
            ),
            ("pack", "target_profile"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    plan_memory_pack_persona_growth_writes
                ).parameters
            ),
            ("pack", "target_relationship_id"),
        )

    def test_history_execution_seam_preserves_causal_order_across_storage_adapters(
        self,
    ) -> None:
        pack = self._remappable_write_plan_pack()
        snapshots = []
        with tempfile.TemporaryDirectory() as root:
            for name, make_storage in self._storage_factories(root):
                with self.subTest(storage=name):
                    storage = make_storage()
                    storage.create_relationship(pack.relationship)
                    plan = plan_memory_pack_writes(
                        pack,
                        pack.agent_id,
                        pack.user_id,
                        pack.relationship,
                        overwrite=False,
                    )
                    result = execute_memory_pack_relationship_history(
                        storage,
                        plan.relationship,
                    )

                    self.assertEqual(
                        result.unit_order,
                        ("event", "event", "event", "adjudication"),
                    )
                    self.assertEqual(result.direct_event_count, 3)
                    self.assertEqual(result.adjudication_count, 1)
                    snapshots.append(
                        (
                            result,
                            tuple(
                                item.to_dict()
                                for item in storage.list_relationship_events(
                                    pack.relationship.relationship_id
                                )
                            ),
                            tuple(
                                item.to_dict()
                                for item in storage.list_relationship_adjudications(
                                    pack.relationship.relationship_id
                                )
                            ),
                        )
                    )

        self.assertEqual(snapshots[0], snapshots[1])
        self.assertFalse(
            hasattr(ERIIEngine, "_commit_relationship_import_history")
        )

    def test_history_execution_preflights_unresolved_order_before_writing(
        self,
    ) -> None:
        pack = self._remappable_write_plan_pack()
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=False,
        )
        unresolved = RelationshipEvent(
            event_id="unresolved-resolution",
            relationship_id=pack.relationship.relationship_id,
            event_type=RelationshipEventType.PROMISE_RESOLUTION,
            content="This references history that is not present.",
            temporal_payload=PromiseResolution(
                promise_event_id="missing-promise",
                resolution_kind=PromiseResolutionKind.SUPERSEDED,
                superseding_promise_event_id="missing-superseding-promise",
            ),
            recorded_at="2026-08-14T00:04:00+00:00",
        )
        invalid_history = replace(
            plan.relationship,
            direct_events=(unresolved,),
            adjudications=(),
        )

        with tempfile.TemporaryDirectory() as root:
            for name, make_storage in self._storage_factories(root):
                with self.subTest(storage=name):
                    storage = make_storage()
                    storage.create_relationship(pack.relationship)
                    with self.assertRaisesRegex(
                        ValueError,
                        "unresolved causal ordering",
                    ):
                        execute_memory_pack_relationship_history(
                            storage,
                            invalid_history,
                        )
                    self.assertEqual(
                        storage.list_relationship_events(
                            pack.relationship.relationship_id
                        ),
                        [],
                    )
                    self.assertEqual(
                        storage.list_relationship_adjudications(
                            pack.relationship.relationship_id
                        ),
                        [],
                    )

    def test_write_executor_commits_frozen_batches_across_storage_adapters(
        self,
    ) -> None:
        pack = self._all_non_compilation_write_pack()
        pack.nodes = [
            MemoryNode(
                node_id="executor-node",
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                content="Executed through the frozen payload seam.",
            )
        ]
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=False,
        )
        expected_batches = (
            "nodes",
            "core_memory",
            "turn_records",
            "timeline_entries",
            "archival_tombstones",
            "relationship_history",
            "relationship_consequences",
            "narrative_tension_links",
            "persona_growth_proposals",
            "persona_reflection_decisions",
            "relationship_processing_runs",
        )
        self.assertEqual(plan.batch_order, expected_batches)
        snapshots = []

        with tempfile.TemporaryDirectory() as root:
            for name, make_storage in self._storage_factories(root):
                with self.subTest(storage=name):
                    storage = make_storage()
                    storage.create_relationship(pack.relationship)
                    result = execute_memory_pack_writes(storage, plan)

                    self.assertEqual(result.executed_batches, expected_batches)
                    self.assertEqual(result.saved_node_count, 1)
                    self.assertTrue(result.core_memory_written)
                    self.assertIsNotNone(result.history)
                    self.assertEqual(result.history.direct_event_count, 3)
                    self.assertEqual(result.history.adjudication_count, 1)
                    with self.assertRaises(FrozenInstanceError):
                        result.saved_node_count = 0
                    snapshot = self._semantic_write_snapshot(
                        storage,
                        pack.agent_id,
                        pack.user_id,
                        pack.relationship.relationship_id,
                    )
                    self.assertEqual(len(snapshot["nodes"]), 1)
                    self.assertEqual(snapshot["core_memory"], pack.core_memory)
                    self.assertEqual(len(snapshot["timeline"]), 1)
                    self.assertEqual(len(snapshot["turns"]), 1)
                    self.assertEqual(len(snapshot["tombstones"]), 1)
                    self.assertEqual(len(snapshot["events"]), 3)
                    self.assertEqual(len(snapshot["adjudications"]), 1)
                    self.assertEqual(len(snapshot["consequences"]), 1)
                    self.assertEqual(len(snapshot["tension_links"]), 1)
                    self.assertEqual(len(snapshot["growth"]), 1)
                    self.assertEqual(len(snapshot["reflections"]), 1)
                    self.assertEqual(len(snapshot["processing_runs"]), 1)
                    snapshots.append((result, snapshot))

        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(
            tuple(inspect.signature(execute_memory_pack_writes).parameters),
            ("storage", "plan"),
        )

    def test_write_executor_preserves_node_core_and_legacy_modes(self) -> None:
        pack = self._remappable_write_plan_pack()
        pack.nodes = [
            MemoryNode(
                node_id="shared-node",
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                content="imported replacement",
            ),
            MemoryNode(
                node_id="new-node",
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                content="imported new node",
            ),
        ]
        existing_nodes = [
            MemoryNode(
                node_id="shared-node",
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                content="stored old value",
            ),
            MemoryNode(
                node_id="retained-node",
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                content="stored retained value",
            ),
        ]

        with tempfile.TemporaryDirectory() as root:
            for overwrite in (False, True):
                snapshots = []
                for name, make_storage in self._storage_factories(
                    os.path.join(root, str(overwrite))
                ):
                    with self.subTest(storage=name, overwrite=overwrite):
                        storage = make_storage()
                        storage.create_relationship(pack.relationship)
                        storage.save_nodes(
                            pack.agent_id,
                            pack.user_id,
                            existing_nodes,
                        )
                        storage.save_core_memory(
                            pack.agent_id,
                            pack.user_id,
                            "stored core",
                        )
                        plan = plan_memory_pack_writes(
                            pack,
                            pack.agent_id,
                            pack.user_id,
                            pack.relationship,
                            overwrite=overwrite,
                        )
                        result = execute_memory_pack_writes(storage, plan)
                        nodes = {
                            item.node_id: item.content
                            for item in storage.load_nodes(
                                pack.agent_id,
                                pack.user_id,
                            )
                        }
                        expected_nodes = {
                            "shared-node": "imported replacement",
                            "new-node": "imported new node",
                        }
                        if not overwrite:
                            expected_nodes["retained-node"] = (
                                "stored retained value"
                            )

                        self.assertEqual(nodes, expected_nodes)
                        self.assertEqual(
                            result.saved_node_count,
                            len(expected_nodes),
                        )
                        self.assertEqual(
                            storage.get_core_memory(
                                pack.agent_id,
                                pack.user_id,
                            ),
                            pack.core_memory if overwrite else "stored core",
                        )
                        self.assertEqual(
                            result.core_memory_written,
                            overwrite,
                        )
                        recent_timeline = storage.get_recent_timeline(
                            pack.agent_id,
                            pack.user_id,
                            limit=10,
                        )
                        self.assertEqual(
                            recent_timeline,
                            [
                                "[2026-07-29 22:10:00+08:00] "
                                "我们在雨声里读完了同一篇故事。 ☔"
                            ],
                        )
                        snapshots.append(
                            (
                                result,
                                nodes,
                                recent_timeline,
                            )
                        )
                self.assertEqual(snapshots[0], snapshots[1])

    def test_write_executor_preflights_all_history_before_payload_writes(
        self,
    ) -> None:
        pack = self._all_non_compilation_write_pack()
        pack.core_memory = "must not be written"
        pack.nodes = [
            MemoryNode(
                node_id="must-not-be-written",
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                content="must not be written",
            )
        ]
        unresolved = RelationshipEvent(
            event_id="executor-unresolved-resolution",
            relationship_id=pack.relationship.relationship_id,
            event_type=RelationshipEventType.PROMISE_RESOLUTION,
            content="This execution plan has an unresolved dependency.",
            temporal_payload=PromiseResolution(
                promise_event_id="executor-missing-promise",
                resolution_kind=PromiseResolutionKind.SUPERSEDED,
                superseding_promise_event_id="executor-missing-successor",
            ),
            recorded_at="2026-08-14T00:05:00+00:00",
        )
        pack.relationship_events = [unresolved]
        pack.relationship_direct_event_ids = [unresolved.event_id]
        pack.relationship_adjudications = []
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=False,
        )

        with tempfile.TemporaryDirectory() as root:
            for name, make_storage in self._storage_factories(root):
                with self.subTest(storage=name):
                    storage = make_storage()
                    storage.create_relationship(pack.relationship)
                    baseline = self._semantic_write_snapshot(
                        storage,
                        pack.agent_id,
                        pack.user_id,
                        pack.relationship.relationship_id,
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "unresolved causal ordering",
                    ):
                        execute_memory_pack_writes(storage, plan)
                    self.assertEqual(
                        self._semantic_write_snapshot(
                            storage,
                            pack.agent_id,
                            pack.user_id,
                            pack.relationship.relationship_id,
                        ),
                        baseline,
                    )

    def test_write_executor_rejects_a_changed_frozen_plan(self) -> None:
        pack = self._fixture_pack()
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=False,
        )
        changed = replace(plan, core_memory="changed after planning")

        with tempfile.TemporaryDirectory() as root:
            for name, make_storage in self._storage_factories(root):
                with self.subTest(storage=name):
                    storage = make_storage()
                    storage.create_relationship(pack.relationship)
                    baseline = self._semantic_write_snapshot(
                        storage,
                        pack.agent_id,
                        pack.user_id,
                        pack.relationship.relationship_id,
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "write plan changed after planning",
                    ):
                        execute_memory_pack_writes(storage, changed)
                    self.assertEqual(
                        self._semantic_write_snapshot(
                            storage,
                            pack.agent_id,
                            pack.user_id,
                            pack.relationship.relationship_id,
                        ),
                        baseline,
                    )

    def test_write_executor_rolls_back_execution_faults_and_retries(
        self,
    ) -> None:
        structured_pack = self._all_non_compilation_write_pack()
        structured_pack.nodes = [
            MemoryNode(
                node_id="atomic-import-node",
                agent_id=structured_pack.agent_id,
                user_id=structured_pack.user_id,
                content="This node belongs to the atomic import.",
            )
        ]
        structured_overwrite = plan_memory_pack_writes(
            structured_pack,
            structured_pack.agent_id,
            structured_pack.user_id,
            structured_pack.relationship,
            overwrite=True,
        )
        structured_merge = plan_memory_pack_writes(
            structured_pack,
            structured_pack.agent_id,
            structured_pack.user_id,
            structured_pack.relationship,
            overwrite=False,
        )

        legacy_pack = self._all_non_compilation_write_pack()
        legacy_pack.nodes = list(structured_pack.nodes)
        legacy_pack.timeline_entries = []
        legacy_pack.timeline = [
            {
                "timestamp": "2026-08-14T00:11:00+00:00",
                "content": "atomic legacy entry one",
            },
            {
                "timestamp": "2026-08-14T00:12:00+00:00",
                "content": "atomic legacy entry two",
            },
        ]
        legacy_overwrite = plan_memory_pack_writes(
            legacy_pack,
            legacy_pack.agent_id,
            legacy_pack.user_id,
            legacy_pack.relationship,
            overwrite=True,
        )

        cases = (
            (
                "last_batch_before",
                structured_overwrite,
                "create_relationship_processing_run",
                1,
                "before",
                None,
                RuntimeError,
                "injected:create_relationship_processing_run:1",
            ),
            (
                "last_batch_after",
                structured_overwrite,
                "create_relationship_processing_run",
                1,
                "after",
                None,
                RuntimeError,
                "injected-after:create_relationship_processing_run:1",
            ),
            (
                "history_second_before",
                structured_overwrite,
                "append_relationship_event",
                2,
                "before",
                None,
                RuntimeError,
                "injected:append_relationship_event:2",
            ),
            (
                "legacy_second_before",
                legacy_overwrite,
                "add_timeline_entry",
                2,
                "before",
                None,
                RuntimeError,
                "injected:add_timeline_entry:2",
            ),
            (
                "core_read_after_nodes",
                structured_merge,
                "get_core_memory",
                1,
                "before",
                None,
                RuntimeError,
                "injected:get_core_memory:1",
            ),
            (
                "tension_stored_return_mismatch",
                structured_overwrite,
                "append_narrative_tension_link",
                1,
                "mismatch",
                lambda stored: replace(
                    stored,
                    summary="injected stored-return mismatch",
                ),
                ValueError,
                "persisted Narrative Tension link differs",
            ),
            (
                "turn_stored_return_mismatch",
                structured_overwrite,
                "create_turn_record",
                1,
                "mismatch",
                lambda stored: replace(
                    stored,
                    source_revision="injected-stored-return-mismatch",
                ),
                ValueError,
                "persisted Turn differs",
            ),
            (
                "growth_stored_return_mismatch",
                structured_overwrite,
                "save_persona_growth_proposal",
                1,
                "mismatch",
                lambda stored: replace(
                    stored,
                    statement="injected stored-return mismatch",
                ),
                ValueError,
                "persisted Persona Growth proposal differs",
            ),
            (
                "reflection_stored_return_mismatch",
                structured_overwrite,
                "commit_persona_reflection_decision",
                1,
                "mismatch",
                lambda stored: replace(
                    stored,
                    recorded_at="2026-08-14T00:09:00+00:00",
                ),
                ValueError,
                "persisted Persona Reflection decision differs",
            ),
            (
                "processing_stored_return_mismatch",
                structured_overwrite,
                "create_relationship_processing_run",
                1,
                "mismatch",
                lambda stored: replace(
                    stored,
                    rule_version="injected-stored-return-mismatch",
                ),
                ValueError,
                "persisted relationship processing run differs",
            ),
        )

        with tempfile.TemporaryDirectory() as root:
            for (
                case_name,
                plan,
                method_name,
                nth,
                mode,
                mutate_stored_return,
                error_type,
                error_pattern,
            ) in cases:
                fault_factories = self._storage_factories(
                    os.path.join(root, case_name, "fault")
                )
                control_factories = self._storage_factories(
                    os.path.join(root, case_name, "control")
                )
                for (
                    (storage_name, make_fault_storage),
                    (control_name, make_control_storage),
                ) in zip(fault_factories, control_factories):
                    self.assertEqual(storage_name, control_name)
                    with self.subTest(case=case_name, storage=storage_name):
                        pack = (
                            legacy_pack
                            if plan is legacy_overwrite
                            else structured_pack
                        )

                        def seed(make_storage):
                            storage = make_storage()
                            storage.create_relationship(pack.relationship)
                            storage.save_nodes(
                                pack.agent_id,
                                pack.user_id,
                                [
                                    MemoryNode(
                                        node_id="atomic-sentinel-node",
                                        agent_id=pack.agent_id,
                                        user_id=pack.user_id,
                                        content="preserve this baseline node",
                                        created_at="2026-08-14 00:00:00",
                                        last_accessed_at="2026-08-14 00:00:00",
                                    )
                                ],
                            )
                            storage.save_core_memory(
                                pack.agent_id,
                                pack.user_id,
                                "atomic baseline core",
                            )
                            return storage

                        control_storage = seed(make_control_storage)
                        control_result = execute_memory_pack_writes(
                            control_storage,
                            plan,
                        )
                        control_snapshot = self._semantic_write_snapshot(
                            make_control_storage(),
                            pack.agent_id,
                            pack.user_id,
                            pack.relationship.relationship_id,
                        )

                        fault_storage = seed(make_fault_storage)
                        baseline = self._semantic_write_snapshot(
                            make_fault_storage(),
                            pack.agent_id,
                            pack.user_id,
                            pack.relationship.relationship_id,
                        )
                        original = getattr(fault_storage, method_name)
                        call_count = 0

                        def injected(*args, **kwargs):
                            nonlocal call_count
                            call_count += 1
                            if call_count != nth:
                                return original(*args, **kwargs)
                            if mode == "before":
                                raise RuntimeError(
                                    f"injected:{method_name}:{nth}"
                                )
                            stored = original(*args, **kwargs)
                            if mode == "mismatch":
                                assert mutate_stored_return is not None
                                return mutate_stored_return(stored)
                            if mode == "after":
                                raise RuntimeError(
                                    f"injected-after:{method_name}:{nth}"
                                )
                            raise AssertionError(f"unsupported mode: {mode}")

                        with mock.patch.object(
                            fault_storage,
                            method_name,
                            side_effect=injected,
                        ):
                            with self.assertRaisesRegex(
                                error_type,
                                error_pattern,
                            ):
                                execute_memory_pack_writes(
                                    fault_storage,
                                    plan,
                                )
                        self.assertGreaterEqual(call_count, nth)

                        reopened = make_fault_storage()
                        self.assertEqual(
                            self._semantic_write_snapshot(
                                reopened,
                                pack.agent_id,
                                pack.user_id,
                                pack.relationship.relationship_id,
                            ),
                            baseline,
                        )
                        retry_result = execute_memory_pack_writes(
                            reopened,
                            plan,
                        )
                        self.assertEqual(retry_result, control_result)
                        self.assertEqual(
                            self._semantic_write_snapshot(
                                make_fault_storage(),
                                pack.agent_id,
                                pack.user_id,
                                pack.relationship.relationship_id,
                            ),
                            control_snapshot,
                        )

    def test_builtin_atomic_capabilities_and_custom_direct_fallback(
        self,
    ) -> None:
        self.assertIsNone(
            BaseStorage.atomic_memory_pack_write_store_v1(object())
        )

        class DirectFallbackFileStorage(FileStorage):
            def __init__(self, root_dir: str) -> None:
                super().__init__(root_dir)
                self.capability_reads = 0
                self.save_nodes_calls = 0

            def atomic_memory_pack_write_store_v1(self):
                self.capability_reads += 1
                return None

            def save_nodes(self, agent_id: str, user_id: str, nodes):
                self.save_nodes_calls += 1
                return super().save_nodes(agent_id, user_id, nodes)

        with tempfile.TemporaryDirectory() as root:
            builtins = (
                FileStorage(os.path.join(root, "file")),
                SQLiteStorage(os.path.join(root, "sqlite", "memory.db")),
            )
            for storage in builtins:
                with self.subTest(storage=type(storage).__name__):
                    self.assertIs(
                        storage.atomic_memory_pack_write_store_v1(),
                        storage,
                    )

            pack = MemoryPack(
                agent_id="direct-fallback-agent",
                user_id="direct-fallback-user",
                core_memory="direct fallback core",
                nodes=[
                    MemoryNode(
                        node_id="direct-fallback-node",
                        agent_id="direct-fallback-agent",
                        user_id="direct-fallback-user",
                        content="Executed through the compatibility path.",
                    )
                ],
            )
            plan = plan_memory_pack_writes(
                pack,
                pack.agent_id,
                pack.user_id,
                None,
                overwrite=False,
            )
            fallback = DirectFallbackFileStorage(
                os.path.join(root, "direct-fallback")
            )

            result = execute_memory_pack_writes(fallback, plan)

            self.assertEqual(fallback.capability_reads, 1)
            self.assertEqual(fallback.save_nodes_calls, 1)
            self.assertEqual(result.executed_batches, plan.batch_order)
            self.assertEqual(
                fallback.get_core_memory(pack.agent_id, pack.user_id),
                pack.core_memory,
            )
            self.assertEqual(
                [
                    node.to_dict()
                    for node in fallback.load_nodes(pack.agent_id, pack.user_id)
                ],
                [pack.nodes[0].to_dict()],
            )

    def test_file_storage_recovers_active_and_committed_write_journals(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            for journal_state in ("active", "committed"):
                with self.subTest(journal_state=journal_state):
                    storage_root = os.path.join(root, journal_state)
                    agent_id = f"journal-{journal_state}-agent"
                    user_id = f"journal-{journal_state}-user"
                    storage = FileStorage(storage_root)
                    storage.save_core_memory(
                        agent_id,
                        user_id,
                        "exact baseline core",
                    )
                    core_path = storage._get_core_path(agent_id, user_id)
                    baseline_bytes = Path(core_path).read_bytes()

                    storage.save_core_memory(
                        agent_id,
                        user_id,
                        "durable after-image core",
                    )
                    after_image_bytes = Path(core_path).read_bytes()
                    self.assertNotEqual(after_image_bytes, baseline_bytes)

                    journal_path = Path(
                        storage._get_memory_pack_write_journal_path()
                    )
                    storage._write_memory_pack_journal_raw(
                        {
                            "version": 1,
                            "state": journal_state,
                            "transaction_id": f"tests-{journal_state}",
                            "target_agent": agent_id,
                            "target_user": user_id,
                            "relationship_id": None,
                            "entries": [
                                {
                                    "path": storage._root_relative_memory_pack_path(
                                        core_path
                                    ),
                                    "existed": True,
                                    "before_image": base64.b64encode(
                                        baseline_bytes
                                    ).decode("ascii"),
                                }
                            ],
                        }
                    )
                    self.assertTrue(journal_path.is_file())

                    reopened = FileStorage(storage_root)
                    expected_bytes = (
                        baseline_bytes
                        if journal_state == "active"
                        else after_image_bytes
                    )
                    expected_core = (
                        "exact baseline core"
                        if journal_state == "active"
                        else "durable after-image core"
                    )
                    self.assertEqual(Path(core_path).read_bytes(), expected_bytes)
                    self.assertEqual(
                        reopened.get_core_memory(agent_id, user_id),
                        expected_core,
                    )
                    self.assertFalse(journal_path.exists())

    def test_file_storage_commit_marker_replace_is_the_commit_point(
        self,
    ) -> None:
        pack = MemoryPack(
            agent_id="commit-point-agent",
            user_id="commit-point-user",
            core_memory="committed despite parent fsync report",
            nodes=[
                MemoryNode(
                    node_id="commit-point-node",
                    agent_id="commit-point-agent",
                    user_id="commit-point-user",
                    content="The committed marker was already replaced.",
                )
            ],
        )
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
            overwrite=True,
        )

        with tempfile.TemporaryDirectory() as root:
            storage = FileStorage(root)
            journal_path = Path(
                storage._get_memory_pack_write_journal_path()
            )
            original_fsync_parent = FileStorage._fsync_parent_directory
            committed_fsync_failures = 0

            def fail_after_committed_replace(file_path: str) -> None:
                nonlocal committed_fsync_failures
                path = Path(file_path)
                if path == journal_path and path.exists():
                    journal = json.loads(path.read_text(encoding="utf-8"))
                    if journal.get("state") == "committed":
                        committed_fsync_failures += 1
                        raise OSError("injected committed parent fsync failure")
                original_fsync_parent(file_path)

            with mock.patch.object(
                FileStorage,
                "_fsync_parent_directory",
                side_effect=fail_after_committed_replace,
            ):
                result = execute_memory_pack_writes(storage, plan)

            self.assertEqual(committed_fsync_failures, 1)
            self.assertEqual(result.executed_batches, plan.batch_order)
            reopened = FileStorage(root)
            self.assertEqual(
                reopened.get_core_memory(pack.agent_id, pack.user_id),
                pack.core_memory,
            )
            self.assertEqual(
                [node.to_dict() for node in reopened.load_nodes(
                    pack.agent_id,
                    pack.user_id,
                )],
                [pack.nodes[0].to_dict()],
            )
            self.assertFalse(journal_path.exists())

    def test_file_storage_requires_durable_cleanup_before_success(
        self,
    ) -> None:
        pack = MemoryPack(
            agent_id="cleanup-fsync-agent",
            user_id="cleanup-fsync-user",
            core_memory="after-image awaiting durable cleanup",
        )
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
            overwrite=True,
        )

        with tempfile.TemporaryDirectory() as root:
            storage = FileStorage(root)
            journal_path = Path(
                storage._get_memory_pack_write_journal_path()
            )
            original_fsync_parent = FileStorage._fsync_parent_directory
            commit_publish_started = False
            journal_fsync_failures = 0

            def fail_commit_and_cleanup_fsync(file_path: str) -> None:
                nonlocal commit_publish_started, journal_fsync_failures
                path = Path(file_path)
                if path == journal_path:
                    if path.exists():
                        journal = json.loads(path.read_text(encoding="utf-8"))
                        if journal.get("state") == "committed":
                            commit_publish_started = True
                    if commit_publish_started:
                        journal_fsync_failures += 1
                        raise OSError("injected persistent journal fsync failure")
                original_fsync_parent(file_path)

            with mock.patch.object(
                FileStorage,
                "_fsync_parent_directory",
                side_effect=fail_commit_and_cleanup_fsync,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected persistent journal fsync failure",
                ):
                    execute_memory_pack_writes(storage, plan)

            self.assertEqual(journal_fsync_failures, 2)
            reopened = FileStorage(root)
            self.assertEqual(
                reopened.get_core_memory(pack.agent_id, pack.user_id),
                pack.core_memory,
            )
            self.assertFalse(journal_path.exists())

    def test_file_storage_committed_marker_survives_helper_boundary_error(
        self,
    ) -> None:
        pack = MemoryPack(
            agent_id="commit-boundary-agent",
            user_id="commit-boundary-user",
            core_memory="committed before helper boundary error",
            nodes=[
                MemoryNode(
                    node_id="commit-boundary-node",
                    agent_id="commit-boundary-agent",
                    user_id="commit-boundary-user",
                    content="The durable committed marker wins.",
                )
            ],
        )
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
            overwrite=True,
        )

        with tempfile.TemporaryDirectory() as root:
            storage = FileStorage(root)
            journal_path = Path(
                storage._get_memory_pack_write_journal_path()
            )
            original_write_journal = storage._write_memory_pack_journal_raw
            committed_boundary_failures = 0

            def fail_after_durable_committed_marker(
                journal: dict[str, object],
            ) -> None:
                nonlocal committed_boundary_failures
                original_write_journal(journal)
                if journal.get("state") == "committed":
                    committed_boundary_failures += 1
                    raise OSError("injected error after durable committed marker")

            with mock.patch.object(
                storage,
                "_write_memory_pack_journal_raw",
                side_effect=fail_after_durable_committed_marker,
            ):
                result = execute_memory_pack_writes(storage, plan)

            self.assertEqual(committed_boundary_failures, 1)
            self.assertEqual(result.executed_batches, plan.batch_order)
            reopened = FileStorage(root)
            self.assertEqual(
                reopened.get_core_memory(pack.agent_id, pack.user_id),
                pack.core_memory,
            )
            self.assertEqual(
                [node.to_dict() for node in reopened.load_nodes(
                    pack.agent_id,
                    pack.user_id,
                )],
                [pack.nodes[0].to_dict()],
            )
            self.assertFalse(journal_path.exists())

    def test_file_storage_commit_publish_failure_rolls_back_active_journal(
        self,
    ) -> None:
        pack = MemoryPack(
            agent_id="commit-active-agent",
            user_id="commit-active-user",
            core_memory="after-image rejected before commit publish",
            nodes=[
                MemoryNode(
                    node_id="commit-active-node",
                    agent_id="commit-active-agent",
                    user_id="commit-active-user",
                    content="The active journal restores this write.",
                )
            ],
        )
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
            overwrite=True,
        )

        with tempfile.TemporaryDirectory() as root:
            storage = FileStorage(root)
            storage.save_core_memory(
                pack.agent_id,
                pack.user_id,
                "exact file baseline",
            )
            journal_path = Path(
                storage._get_memory_pack_write_journal_path()
            )
            original_write_journal = storage._write_memory_pack_journal_raw
            commit_publish_failures = 0

            def fail_before_committed_marker(
                journal: dict[str, object],
            ) -> None:
                nonlocal commit_publish_failures
                if journal.get("state") == "committed":
                    commit_publish_failures += 1
                    raise OSError("injected failure before committed marker")
                original_write_journal(journal)

            with mock.patch.object(
                storage,
                "_write_memory_pack_journal_raw",
                side_effect=fail_before_committed_marker,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected failure before committed marker",
                ):
                    execute_memory_pack_writes(storage, plan)

            self.assertEqual(commit_publish_failures, 1)
            reopened = FileStorage(root)
            self.assertEqual(
                reopened.get_core_memory(pack.agent_id, pack.user_id),
                "exact file baseline",
            )
            self.assertEqual(
                reopened.load_nodes(pack.agent_id, pack.user_id),
                [],
            )
            self.assertFalse(journal_path.exists())

    def test_sqlite_storage_commit_boundary_error_preserves_committed_state(
        self,
    ) -> None:
        pack = MemoryPack(
            agent_id="sqlite-commit-agent",
            user_id="sqlite-commit-user",
            core_memory="committed before sqlite wrapper error",
            nodes=[
                MemoryNode(
                    node_id="sqlite-commit-node",
                    agent_id="sqlite-commit-agent",
                    user_id="sqlite-commit-user",
                    content="The SQLite transaction already committed.",
                )
            ],
        )
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
            overwrite=True,
        )

        class CommitBoundaryConnection:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self._connection = connection
                self.commit_failures = 0

            @property
            def in_transaction(self) -> bool:
                return self._connection.in_transaction

            def execute(self, *args, **kwargs):
                return self._connection.execute(*args, **kwargs)

            def commit(self) -> None:
                self._connection.commit()
                self.commit_failures += 1
                raise RuntimeError("injected error after sqlite commit")

            def rollback(self) -> None:
                self._connection.rollback()

            def close(self) -> None:
                self._connection.close()

            def __getattr__(self, name: str):
                return getattr(self._connection, name)

        with tempfile.TemporaryDirectory() as root:
            database_path = os.path.join(root, "memory.db")
            storage = SQLiteStorage(database_path)
            original_open = storage._open_connection
            opened_connections: list[CommitBoundaryConnection] = []

            def open_commit_boundary_connection() -> CommitBoundaryConnection:
                connection = CommitBoundaryConnection(original_open())
                opened_connections.append(connection)
                return connection

            with mock.patch.object(
                storage,
                "_open_connection",
                side_effect=open_commit_boundary_connection,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected error after sqlite commit",
                ):
                    execute_memory_pack_writes(storage, plan)

            self.assertEqual(len(opened_connections), 1)
            self.assertEqual(opened_connections[0].commit_failures, 1)
            reopened = SQLiteStorage(database_path)
            self.assertEqual(
                reopened.get_core_memory(pack.agent_id, pack.user_id),
                pack.core_memory,
            )
            self.assertEqual(
                [node.to_dict() for node in reopened.load_nodes(
                    pack.agent_id,
                    pack.user_id,
                )],
                [pack.nodes[0].to_dict()],
            )

    def test_sqlite_storage_commit_error_detects_automatic_rollback(
        self,
    ) -> None:
        pack = MemoryPack(
            agent_id="sqlite-rollback-agent",
            user_id="sqlite-rollback-user",
            core_memory="this after-image must be rolled back",
            nodes=[
                MemoryNode(
                    node_id="sqlite-rollback-node",
                    agent_id="sqlite-rollback-agent",
                    user_id="sqlite-rollback-user",
                    content="The simulated commit rolled back automatically.",
                )
            ],
        )
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
            overwrite=True,
        )

        class AutoRollbackCommitConnection:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self._connection = connection
                self.commit_failures = 0

            @property
            def in_transaction(self) -> bool:
                return self._connection.in_transaction

            def execute(self, *args, **kwargs):
                return self._connection.execute(*args, **kwargs)

            def commit(self) -> None:
                self._connection.rollback()
                self.commit_failures += 1
                raise RuntimeError("injected sqlite automatic rollback")

            def rollback(self) -> None:
                self._connection.rollback()

            def close(self) -> None:
                self._connection.close()

            def __getattr__(self, name: str):
                return getattr(self._connection, name)

        with tempfile.TemporaryDirectory() as root:
            database_path = os.path.join(root, "memory.db")
            storage = SQLiteStorage(database_path)
            storage.save_core_memory(
                pack.agent_id,
                pack.user_id,
                "exact sqlite baseline",
            )
            original_open = storage._open_connection
            opened_connections: list[AutoRollbackCommitConnection] = []

            def open_auto_rollback_connection() -> AutoRollbackCommitConnection:
                connection = AutoRollbackCommitConnection(original_open())
                opened_connections.append(connection)
                return connection

            with mock.patch.object(
                storage,
                "_open_connection",
                side_effect=open_auto_rollback_connection,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected sqlite automatic rollback",
                ):
                    execute_memory_pack_writes(storage, plan)

            self.assertEqual(len(opened_connections), 1)
            self.assertEqual(opened_connections[0].commit_failures, 1)
            reopened = SQLiteStorage(database_path)
            self.assertEqual(
                reopened.get_core_memory(pack.agent_id, pack.user_id),
                "exact sqlite baseline",
            )
            self.assertEqual(
                reopened.load_nodes(pack.agent_id, pack.user_id),
                [],
            )

    def test_sqlite_storage_commit_failure_while_active_rolls_back(
        self,
    ) -> None:
        pack = MemoryPack(
            agent_id="sqlite-active-agent",
            user_id="sqlite-active-user",
            core_memory="after-image rejected while transaction is active",
            nodes=[
                MemoryNode(
                    node_id="sqlite-active-node",
                    agent_id="sqlite-active-agent",
                    user_id="sqlite-active-user",
                    content="The active SQLite transaction rolls back.",
                )
            ],
        )
        plan = plan_memory_pack_writes(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
            overwrite=True,
        )

        class ActiveCommitFailureConnection:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self._connection = connection
                self.rollback_calls = 0

            @property
            def in_transaction(self) -> bool:
                return self._connection.in_transaction

            def execute(self, *args, **kwargs):
                return self._connection.execute(*args, **kwargs)

            def commit(self) -> None:
                raise OSError("injected failure before sqlite commit")

            def rollback(self) -> None:
                self.rollback_calls += 1
                self._connection.rollback()

            def close(self) -> None:
                self._connection.close()

            def __getattr__(self, name: str):
                return getattr(self._connection, name)

        with tempfile.TemporaryDirectory() as root:
            database_path = os.path.join(root, "memory.db")
            storage = SQLiteStorage(database_path)
            storage.save_core_memory(
                pack.agent_id,
                pack.user_id,
                "exact active sqlite baseline",
            )
            original_open = storage._open_connection
            opened_connections: list[ActiveCommitFailureConnection] = []

            def open_active_failure_connection() -> ActiveCommitFailureConnection:
                connection = ActiveCommitFailureConnection(original_open())
                opened_connections.append(connection)
                return connection

            with mock.patch.object(
                storage,
                "_open_connection",
                side_effect=open_active_failure_connection,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected failure before sqlite commit",
                ):
                    execute_memory_pack_writes(storage, plan)

            self.assertEqual(len(opened_connections), 1)
            self.assertEqual(opened_connections[0].rollback_calls, 1)
            reopened = SQLiteStorage(database_path)
            self.assertEqual(
                reopened.get_core_memory(pack.agent_id, pack.user_id),
                "exact active sqlite baseline",
            )
            self.assertEqual(
                reopened.load_nodes(pack.agent_id, pack.user_id),
                [],
            )

    def test_engine_delegates_non_compilation_payload_once(self) -> None:
        pack = self._fixture_pack()
        with tempfile.TemporaryDirectory() as root:
            storage = FileStorage(root)
            storage.create_relationship(pack.relationship)
            with (
                mock.patch(
                    "erii.engine.execute_memory_pack_writes",
                    return_value=None,
                ) as delegated,
                mock.patch.object(storage, "save_nodes") as save_nodes,
                mock.patch.object(storage, "save_core_memory") as save_core_memory,
                mock.patch.object(
                    storage,
                    "import_timeline_entries",
                ) as import_timeline_entries,
            ):
                with ERIIEngine(storage_driver=storage) as engine:
                    returned = engine.import_memory(pack)

            self.assertIs(returned, pack)
            delegated.assert_called_once()
            delegated_storage, delegated_plan = delegated.call_args.args
            self.assertIs(delegated_storage, storage)
            self.assertEqual(delegated_plan.target_agent, pack.agent_id)
            self.assertEqual(delegated_plan.target_user, pack.user_id)
            self.assertEqual(
                delegated_plan.target_relationship_id,
                pack.relationship.relationship_id,
            )
            save_nodes.assert_not_called()
            save_core_memory.assert_not_called()
            import_timeline_entries.assert_not_called()

    def test_write_executor_binds_exact_restore_facts_in_the_plan(self) -> None:
        transcript_pack = self._fixture_pack()

        archival_pack = self._fixture_pack()
        archival_pack.turn_records = []
        archival_pack.archival_ledger = [
            self._tombstone(archival_pack, "exact-restore-archival")
        ]

        consequence_pack = self._fixture_pack()
        consequence_pack.version = "0.5.0a3"
        consequence_pack.turn_records = []
        consequence_pack.relationship_consequences = [
            RelationshipConsequence(
                consequence_id="exact-restore-consequence",
                relationship_id=(
                    consequence_pack.relationship.relationship_id
                ),
                tension_id="exact-restore-tension",
                source_turn_id="exact-restore-turn",
                source_revision="1",
                source_decision_id="exact-restore-decision",
                source_event_id=(
                    consequence_pack.relationship_events[0].event_id
                ),
                source_message_id="exact-restore-message",
                effects=(RelationshipConsequenceKind.CONFLICT,),
                summary="This consequence remains source-bound.",
                recorded_at="2026-08-14T00:10:00+00:00",
            )
        ]

        processing_pack = self._fixture_pack_with_processing_run()
        processing_pack.turn_records = []

        cases = (
            (
                "turn_transcripts",
                transcript_pack,
                "source transcripts require exact relationship restore",
            ),
            (
                "archival_provenance",
                archival_pack,
                "archival provenance requires exact relationship restore",
            ),
            (
                "relationship_consequences",
                consequence_pack,
                "relationship consequences require exact relationship restore",
            ),
            (
                "relationship_processing",
                processing_pack,
                "relationship processing requires exact relationship restore",
            ),
        )

        with tempfile.TemporaryDirectory() as root:
            for case_name, pack, error in cases:
                pack.core_memory = "must remain frozen behind exact restore"
                pack.nodes = [
                    MemoryNode(
                        node_id=f"exact-restore-{case_name}",
                        agent_id="target-agent",
                        user_id="target-user",
                        content="must not be written",
                    )
                ]
                target_profile = replace(
                    pack.relationship,
                    relationship_id=f"target-{case_name}",
                    agent_id="target-agent",
                    user_id="target-user",
                )
                plan = plan_memory_pack_writes(
                    pack,
                    target_profile.agent_id,
                    target_profile.user_id,
                    target_profile,
                    overwrite=False,
                )
                self.assertEqual(
                    plan.relationship.source_relationship_id,
                    pack.relationship.relationship_id,
                )
                for name, make_storage in self._storage_factories(
                    os.path.join(root, case_name)
                ):
                    with self.subTest(case=case_name, storage=name):
                        storage = make_storage()
                        storage.create_relationship(target_profile)
                        baseline = self._semantic_write_snapshot(
                            storage,
                            target_profile.agent_id,
                            target_profile.user_id,
                            target_profile.relationship_id,
                        )
                        with self.assertRaisesRegex(ValueError, error):
                            execute_memory_pack_writes(storage, plan)
                        self.assertEqual(
                            self._semantic_write_snapshot(
                                storage,
                                target_profile.agent_id,
                                target_profile.user_id,
                                target_profile.relationship_id,
                            ),
                            baseline,
                        )

    def test_plan_interface_depends_only_on_an_explicit_read_only_storage_seam(
        self,
    ) -> None:
        source = inspect.getsource(transfer_module)
        tree = ast.parse(source)
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        self.assertFalse(
            any(
                module in {
                    "erii.storage.file_storage",
                    "erii.storage.sqlite_storage",
                }
                for module in imported_modules
            )
        )
        self.assertTrue(MemoryPackTargetReadRecorder.READ_METHODS)
        self.assertFalse(
            MemoryPackTargetReadRecorder.READ_METHODS
            & {
                "save_nodes",
                "save_core_memory",
                "create_relationship",
                "import_timeline_entries",
                "import_archival_tombstones",
                "create_turn_record",
                "append_relationship_event",
                "commit_relationship_adjudication",
            }
        )

    def test_plan_rejects_stale_source_and_target_snapshots(self) -> None:
        pack = self._fixture_pack()
        source = analyze_memory_pack_source(pack)
        plan = bind_memory_pack_transfer_plan(
            source,
            pack,
            pack.agent_id,
            pack.user_id,
            pack.relationship,
            overwrite=False,
        )

        pack.core_memory = "changed after planning"
        plan_target = pack.relationship
        with self.assertRaisesRegex(
            StaleMemoryPackTransferPlanError,
            "source changed after planning",
        ):
            require_memory_pack_transfer_plan_current(
                plan,
                pack,
                plan_target,
            )

        pack = self._fixture_pack()
        with self.assertRaisesRegex(
            StaleMemoryPackTransferPlanError,
            "target changed after preflight",
        ):
            require_memory_pack_transfer_plan_current(
                plan,
                pack,
                replace(plan_target, manifest_id="new-manifest"),
            )

    def test_plan_identity_binds_overwrite_intent_and_empty_target(self) -> None:
        pack = self._fixture_pack()
        source = analyze_memory_pack_source(pack)
        merge_plan = bind_memory_pack_transfer_plan(
            source,
            pack,
            "target-agent",
            "target-user",
            None,
            overwrite=False,
        )
        overwrite_plan = bind_memory_pack_transfer_plan(
            source,
            pack,
            "target-agent",
            "target-user",
            None,
            overwrite=True,
        )

        self.assertIsNone(merge_plan.target.relationship_id)
        self.assertEqual(merge_plan.target, overwrite_plan.target)
        self.assertNotEqual(merge_plan.fingerprint, overwrite_plan.fingerprint)

    def test_file_and_sqlite_profiles_produce_the_same_target_snapshot(self) -> None:
        pack = self._fixture_pack()
        source = analyze_memory_pack_source(pack)
        with tempfile.TemporaryDirectory() as root:
            storages = (
                FileStorage(os.path.join(root, "file")),
                SQLiteStorage(os.path.join(root, "sqlite", "memory.db")),
            )
            plans = []
            for storage in storages:
                storage.create_relationship(pack.relationship)
                profile = storage.get_relationship(pack.agent_id, pack.user_id)
                plans.append(
                    bind_memory_pack_transfer_plan(
                        source,
                        pack,
                        pack.agent_id,
                        pack.user_id,
                        profile,
                        overwrite=True,
                    )
                )

        self.assertEqual(plans[0], plans[1])

    def test_file_and_sqlite_produce_the_same_complete_target_read_set(self) -> None:
        pack = self._fixture_pack()
        with tempfile.TemporaryDirectory() as root:
            storages = (
                FileStorage(os.path.join(root, "file")),
                SQLiteStorage(os.path.join(root, "sqlite", "memory.db")),
            )
            read_sets = []
            for storage in storages:
                storage.create_relationship(pack.relationship)
                archival_id = "equivalent-archival-source"
                storage.import_archival_tombstones(
                    pack.relationship.relationship_id,
                    [self._tombstone(pack, archival_id)],
                )
                storage.import_timeline_entries(
                    pack.agent_id,
                    pack.user_id,
                    pack.timeline_entries,
                )
                storage.create_turn_record(pack.turn_records[0])
                reads = MemoryPackTargetReadRecorder(storage)
                relationship_id = pack.relationship.relationship_id
                blueprint_id = pack.relationship.blueprint.blueprint_id
                reads.get_relationship(pack.agent_id, pack.user_id)
                reads.list_timeline_entries(pack.agent_id, pack.user_id)
                reads.list_relationship_adjudications(relationship_id)
                reads.list_relationship_consequences(relationship_id)
                reads.list_narrative_tension_links(relationship_id)
                reads.list_relationship_events(relationship_id)
                reads.list_relationship_processing_runs(relationship_id)
                reads.list_persona_reflection_decisions(relationship_id)
                reads.list_persona_reflection_records(relationship_id)
                reads.list_persona_growth_proposals(relationship_id)
                reads.list_persona_compilation_proposals(blueprint_id)
                reads.list_persona_manifests(blueprint_id)
                reads.capture_archival_tombstone_validation_source(
                    relationship_id,
                    [archival_id],
                )
                reads.list_turn_records(relationship_id)
                read_sets.append(reads.freeze())

            self.assertEqual(read_sets[0], read_sets[1])
            self.assertEqual(len(read_sets[0].fingerprint), 64)
            replay_memory_pack_target_read_set(storages[0], read_sets[0])

    def test_read_set_replay_rejects_target_relationship_archival_changes(
        self,
    ) -> None:
        pack = self._fixture_pack()
        with tempfile.TemporaryDirectory() as root:
            for name, make_storage in self._storage_factories(root):
                with self.subTest(storage=name):
                    storage = make_storage()
                    self._create_target_relationship(storage, pack)
                    reads = MemoryPackTargetReadRecorder(storage)
                    reads.capture_archival_tombstone_validation_source(
                        pack.relationship.relationship_id,
                        ["incoming-archival"],
                    )
                    frozen = reads.freeze()
                    storage.import_archival_tombstones(
                        pack.relationship.relationship_id,
                        [self._tombstone(pack, "archival-after-preflight")],
                    )

                    with self.assertRaisesRegex(
                        StaleMemoryPackTransferPlanError,
                        "target conflict reads changed after preflight",
                    ):
                        replay_memory_pack_target_read_set(storage, frozen)

    def test_read_set_replay_rejects_same_archival_id_in_other_relationship(
        self,
    ) -> None:
        pack = self._fixture_pack()
        incoming_id = "incoming-cross-relationship"
        with tempfile.TemporaryDirectory() as root:
            for name, make_storage in self._storage_factories(root):
                with self.subTest(storage=name):
                    storage = make_storage()
                    self._create_target_relationship(storage, pack)
                    self._create_other_relationship(storage, pack)
                    reads = MemoryPackTargetReadRecorder(storage)
                    reads.capture_archival_tombstone_validation_source(
                        pack.relationship.relationship_id,
                        [incoming_id],
                    )
                    frozen = reads.freeze()
                    storage.import_archival_tombstones(
                        "other-relationship",
                        [
                            self._tombstone(
                                pack,
                                incoming_id,
                                relationship_id="other-relationship",
                            )
                        ],
                    )

                    with self.assertRaisesRegex(
                        StaleMemoryPackTransferPlanError,
                        "target conflict reads changed after preflight",
                    ):
                        replay_memory_pack_target_read_set(storage, frozen)

    def test_read_set_replay_ignores_unrelated_relationship_tombstones(
        self,
    ) -> None:
        pack = self._fixture_pack()
        with tempfile.TemporaryDirectory() as root:
            for name, make_storage in self._storage_factories(root):
                with self.subTest(storage=name):
                    storage = make_storage()
                    self._create_target_relationship(storage, pack)
                    self._create_other_relationship(storage, pack)
                    reads = MemoryPackTargetReadRecorder(storage)
                    reads.capture_archival_tombstone_validation_source(
                        pack.relationship.relationship_id,
                        ["incoming-archival"],
                    )
                    frozen = reads.freeze()
                    storage.import_archival_tombstones(
                        "other-relationship",
                        [
                            self._tombstone(
                                pack,
                                "unrelated-archival",
                                relationship_id="other-relationship",
                            )
                        ],
                    )

                    replay_memory_pack_target_read_set(storage, frozen)

    def test_read_set_replay_ignores_worker_attempt_state_changes(self) -> None:
        pack = self._fixture_pack()
        archival_id = "active-worker-archival"
        with tempfile.TemporaryDirectory() as root:
            for name, make_storage in self._storage_factories(root):
                with self.subTest(storage=name):
                    storage = make_storage()
                    self._create_target_relationship(storage, pack)
                    store = storage.atomic_archival_store_v1()
                    store.create_archival_record(
                        self._pending_archival_record(pack, archival_id)
                    )
                    reads = MemoryPackTargetReadRecorder(storage)
                    reads.capture_archival_tombstone_validation_source(
                        pack.relationship.relationship_id,
                        [archival_id],
                    )
                    frozen = reads.freeze()

                    claimed = store.claim_next_archival_record(
                        now=0.0,
                        lease_seconds=60.0,
                        permit_seconds=60.0,
                        archival_id=archival_id,
                    )
                    self.assertIsNotNone(claimed)
                    self.assertIsNotNone(claimed.attempt_id)

                    replay_memory_pack_target_read_set(storage, frozen)

    def test_engine_fails_closed_when_archival_validation_reads_are_opaque(
        self,
    ) -> None:
        pack = self._fixture_pack()
        pack.archival_ledger = [
            ArchivalTombstone(
                archival_id="opaque-archival",
                relationship_id=pack.relationship.relationship_id,
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                source_turn_id=pack.turn_records[0].turn_id,
                source_revision=pack.turn_records[0].source_revision,
                status=ArchivalStatus.COMPLETED,
                outcome_code=ArchivalOutcomeCode.NO_MEMORY,
                terminal_at="2026-08-14T00:00:00+00:00",
                request_fingerprint="opaque-request",
                idempotency_fingerprint="opaque-idempotency",
            )
        ]
        with tempfile.TemporaryDirectory() as root:
            storage = _OpaqueArchivalFileStorage(root)
            storage.create_relationship(pack.relationship)
            with ERIIEngine(storage_driver=storage) as engine:
                with self.assertRaisesRegex(
                    ValueError,
                    "cannot bind archival validation source reads",
                ):
                    engine.import_memory(pack)

            self.assertEqual(storage.load_nodes(pack.agent_id, pack.user_id), [])

    def test_engine_rejects_stale_target_before_any_payload_write(self) -> None:
        pack = self._fixture_pack()
        pack.core_memory = "must not be imported"
        with tempfile.TemporaryDirectory() as root:
            storage = _StaleTargetFileStorage(root)
            storage.create_relationship(pack.relationship)
            target_before = FileStorage.get_relationship(
                storage,
                pack.agent_id,
                pack.user_id,
            )
            with ERIIEngine(storage_driver=storage) as engine:
                with self.assertRaisesRegex(
                    StaleMemoryPackTransferPlanError,
                    "target changed after preflight",
                ):
                    engine.import_memory(pack)

                self.assertEqual(
                    storage.get_core_memory(pack.agent_id, pack.user_id),
                    "",
                )
                self.assertEqual(
                    storage.load_nodes(pack.agent_id, pack.user_id),
                    [],
                )
                self.assertEqual(
                    FileStorage.get_relationship(
                        storage,
                        pack.agent_id,
                        pack.user_id,
                    ),
                    target_before,
                )

    def test_engine_rejects_stale_conflict_reads_before_any_payload_write(self) -> None:
        pack = self._fixture_pack()
        for stale_method in ("timeline", "turn"):
            with self.subTest(stale_method=stale_method):
                with tempfile.TemporaryDirectory() as root:
                    storage = _StaleReadSetFileStorage(root, stale_method)
                    storage.create_relationship(pack.relationship)
                    storage.import_timeline_entries(
                        pack.agent_id,
                        pack.user_id,
                        pack.timeline_entries,
                    )
                    storage.create_turn_record(pack.turn_records[0])
                    with ERIIEngine(storage_driver=storage) as engine:
                        with self.assertRaisesRegex(
                            StaleMemoryPackTransferPlanError,
                            "target conflict reads changed after preflight",
                        ):
                            engine.import_memory(pack)

                    self.assertEqual(storage.save_nodes_calls, 0)

    def test_engine_rejects_stale_processing_reads_before_any_payload_write(
        self,
    ) -> None:
        pack = self._fixture_pack_with_processing_run()
        with tempfile.TemporaryDirectory() as root:
            storage = _StaleReadSetFileStorage(
                root,
                "processing",
                stale_processing_run=pack.relationship_processing_runs[0],
            )
            storage.create_relationship(pack.relationship)
            storage.import_timeline_entries(
                pack.agent_id,
                pack.user_id,
                pack.timeline_entries,
            )
            storage.create_turn_record(pack.turn_records[0])
            with ERIIEngine(storage_driver=storage) as engine:
                with self.assertRaisesRegex(
                    StaleMemoryPackTransferPlanError,
                    "target conflict reads changed after preflight",
                ):
                    engine.import_memory(pack)

            self.assertEqual(storage.save_nodes_calls, 0)

    def test_new_target_import_failure_leaves_no_partial_state(self) -> None:
        """Verifies that when importing to a new target fails, no partial state is left.

        This is a core R1B exit gate requirement: a failed import to a new
        (non-existent) relationship must not leave behind partial nodes, core memory,
        or timeline entries. The target storage state must remain as if the import
        was never attempted.
        """
        pack = self._fixture_pack()
        pack.core_memory = "new target core memory"
        pack.nodes = [
            MemoryNode(
                node_id="new-target-node-1",
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                content="This should not persist on failure",
            )
        ]

        # Test with a pack that will fail validation after preflight
        # but before writes, simulating various failure scenarios
        for storage_type in ("file", "sqlite"):
            with self.subTest(storage_type=storage_type):
                if storage_type == "file":
                    with tempfile.TemporaryDirectory() as root:
                        storage = FileStorage(root)
                        # Don't create the relationship - this is a new target
                        self._verify_new_target_atomicity(storage, pack)
                else:
                    with tempfile.TemporaryDirectory() as root:
                        db_path = os.path.join(root, "test.db")
                        storage = SQLiteStorage(db_path)
                        # Don't create the relationship - this is a new target
                        self._verify_new_target_atomicity(storage, pack)

    def _verify_new_target_atomicity(
        self,
        storage: BaseStorage,
        pack: MemoryPack,
    ) -> None:
        """Helper to verify atomicity for new target imports."""
        # Verify target doesn't exist before import
        initial_profile = storage.get_relationship(pack.agent_id, pack.user_id)
        self.assertIsNone(initial_profile)
        initial_nodes = storage.load_nodes(pack.agent_id, pack.user_id)
        self.assertEqual(initial_nodes, [])
        initial_core = storage.get_core_memory(pack.agent_id, pack.user_id)
        self.assertEqual(initial_core, "")

        with ERIIEngine(storage_driver=storage) as engine:
            # Import should succeed for new target with valid pack
            engine.import_memory(pack)

            # Verify the import succeeded
            final_profile = storage.get_relationship(pack.agent_id, pack.user_id)
            self.assertIsNotNone(final_profile)
            final_nodes = storage.load_nodes(pack.agent_id, pack.user_id)
            self.assertEqual(len(final_nodes), 1)
            self.assertEqual(final_nodes[0].node_id, "new-target-node-1")
            final_core = storage.get_core_memory(pack.agent_id, pack.user_id)
            self.assertEqual(final_core, "new target core memory")

        # Now test an execution failure after target creation.  The failure
        # must roll back the relationship and every payload batch as one unit.
        failing_pack = self._fixture_pack()
        failing_pack.core_memory = "must roll back"
        failing_pack.nodes = [
            MemoryNode(
                node_id="rollback-node",
                agent_id=failing_pack.agent_id,
                user_id=failing_pack.user_id,
                content="must not persist",
            )
        ]

        with tempfile.TemporaryDirectory() as root2:
            if isinstance(storage, FileStorage):
                storage2 = FileStorage(root2)
            else:
                db_path2 = os.path.join(root2, "test2.db")
                storage2 = SQLiteStorage(db_path2)

            def fail_payload(*args, **kwargs):
                raise RuntimeError("injected MemoryPack payload failure")

            with mock.patch.object(storage2, "save_nodes", side_effect=fail_payload):
                with ERIIEngine(storage_driver=storage2) as engine:
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "injected MemoryPack payload failure",
                    ):
                        engine.import_memory(failing_pack)

            self.assertIsNone(
                storage2.get_relationship(
                    failing_pack.agent_id,
                    failing_pack.user_id,
                )
            )
            self.assertEqual(
                storage2.load_nodes(
                    failing_pack.agent_id,
                    failing_pack.user_id,
                ),
                [],
            )
            self.assertEqual(
                storage2.get_core_memory(
                    failing_pack.agent_id,
                    failing_pack.user_id,
                ),
                "",
            )

    def test_new_target_persona_compilation_failure_rolls_back(self) -> None:
        """Compilation history and target creation share the import transaction."""
        pack = self._persona_compilation_pack()
        pack.core_memory = "compilation rollback core"
        pack.nodes = [
            MemoryNode(
                node_id="compilation-rollback-node",
                agent_id=pack.agent_id,
                user_id=pack.user_id,
                content="must not persist",
            )
        ]

        for storage_type in ("file", "sqlite"):
            with self.subTest(storage_type=storage_type):
                with tempfile.TemporaryDirectory() as root:
                    if storage_type == "file":
                        storage = FileStorage(root)
                    else:
                        storage = SQLiteStorage(os.path.join(root, "memory.db"))

                    def fail_payload(*args, **kwargs):
                        raise RuntimeError("injected compilation payload failure")

                    with mock.patch.object(
                        storage,
                        "save_nodes",
                        side_effect=fail_payload,
                    ):
                        with ERIIEngine(storage_driver=storage) as engine:
                            with self.assertRaisesRegex(
                                RuntimeError,
                                "injected compilation payload failure",
                            ):
                                engine.import_memory(pack)

                    self.assertIsNone(
                        storage.get_relationship(pack.agent_id, pack.user_id)
                    )
                    self.assertEqual(
                        storage.list_persona_compilation_proposals(
                            pack.relationship.blueprint.blueprint_id
                        ),
                        [],
                    )
                    self.assertEqual(
                        storage.list_persona_manifests(
                            pack.relationship.blueprint.blueprint_id
                        ),
                        [],
                    )


if __name__ == "__main__":
    unittest.main()
