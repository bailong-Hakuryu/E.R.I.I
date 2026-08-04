"""End-to-end acceptance contract for the Golden Continuity Demo."""

import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from erii import (
    DataLifecycleCoordinator,
    ERIIEngine,
    LifecycleOutcome,
    LifecycleTarget,
    LifecycleTargetKind,
    MemoryPack,
    MemoryPackImportRequest,
    RecallArtifactProvenance,
    RecallBudget,
    RecallOptions,
    RecallRequest,
    SQLiteStorage,
)
from erii.demo import (
    GoldenContinuityDemoVerificationError,
    run_golden_continuity_demo,
)


server_module = importlib.import_module("erii.server.app")


ROOT = Path(__file__).resolve().parents[1]


class GoldenContinuityDemoTest(unittest.TestCase):
    def test_demo_proves_restart_isolation_provenance_and_portability(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "golden-demo"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "erii.server.app",
                    "demo",
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("[PASS] restart persistence", result.stdout)
            self.assertIn("[PASS] relationship isolation", result.stdout)
            self.assertIn("[PASS] provenance", result.stdout)
            self.assertIn("[PASS] portable round trip", result.stdout)

            report = json.loads(
                (output_dir / "demo-report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["schema_version"], "erii.golden-continuity-demo.v2")
            self.assertEqual(report["status"], "passed")
            self.assertTrue(all(report["checks"].values()))
            self.assertIn("portable_round_trip", report["checks"])
            self.assertNotIn("portable_export", report["checks"])

            database_path = output_dir / report["artifacts"]["database"]
            engine = ERIIEngine(
                storage_driver=SQLiteStorage(str(database_path))
            )
            try:
                primary = engine.recall_structured(
                    RecallRequest(
                        agent_id=report["agent_id"],
                        user_id=report["primary_user_id"],
                        query="first snow",
                        audience="agent_private",
                        options=RecallOptions(
                            persona_delivery="planned",
                            budget=RecallBudget(max_cost=50_000),
                        ),
                    )
                )
                isolated = engine.recall_structured(
                    RecallRequest(
                        agent_id=report["agent_id"],
                        user_id=report["isolated_user_id"],
                        query="first snow",
                        audience="agent_private",
                        options=RecallOptions(
                            persona_delivery="planned",
                            budget=RecallBudget(max_cost=50_000),
                        ),
                    )
                )
                primary_snapshot = engine.get_relationship_snapshot(
                    report["agent_id"],
                    report["primary_user_id"],
                )
                isolated_snapshot = engine.get_relationship_snapshot(
                    report["agent_id"],
                    report["isolated_user_id"],
                )
            finally:
                engine.close()

            primary_event = next(
                event
                for event in primary.events
                if event.source_id == report["shared_event_id"]
            )
            self.assertEqual(primary_event.source_kind, "relationship_event")
            self.assertTrue(primary_event.source_references)
            self.assertNotIn(
                report["shared_event_id"],
                {event.source_id for event in isolated.events},
            )
            self.assertEqual(isolated.memories, ())
            self.assertGreater(
                primary_snapshot.state.intimacy,
                isolated_snapshot.state.intimacy,
            )
            self.assertEqual(
                primary_snapshot.profile.agent_identity_id,
                isolated_snapshot.profile.agent_identity_id,
            )
            self.assertNotEqual(
                primary_snapshot.profile.relationship_id,
                isolated_snapshot.profile.relationship_id,
            )
            self.assertNotEqual(
                primary_snapshot.profile.persona_id,
                isolated_snapshot.profile.persona_id,
            )
            self.assertIsNotNone(primary.persona_context)
            self.assertIsNotNone(isolated.persona_context)
            self.assertEqual(
                primary.persona_context.manifest_id,
                report["primary_manifest_id"],
            )
            self.assertEqual(
                isolated.persona_context.manifest_id,
                report["isolated_manifest_id"],
            )
            self.assertNotEqual(
                primary.persona_context.manifest_id,
                isolated.persona_context.manifest_id,
            )
            primary_persona_ids = {
                item.source_id
                for item in (
                    primary.persona_context.authority_items
                    + primary.persona_context.interpretation_items
                    + primary.persona_context.approved_growth_items
                )
            }
            isolated_persona_ids = {
                item.source_id
                for item in (
                    isolated.persona_context.authority_items
                    + isolated.persona_context.interpretation_items
                    + isolated.persona_context.approved_growth_items
                )
            }
            primary_persona_text = "\n".join(
                item.content
                for item in (
                    primary.persona_context.authority_items
                    + primary.persona_context.interpretation_items
                    + primary.persona_context.approved_growth_items
                )
            )
            isolated_persona_text = "\n".join(
                item.content
                for item in (
                    isolated.persona_context.authority_items
                    + isolated.persona_context.interpretation_items
                    + isolated.persona_context.approved_growth_items
                )
            )
            private_persona_ids = set(report["primary_private_persona_ids"])
            self.assertLessEqual(private_persona_ids, primary_persona_ids)
            self.assertTrue(private_persona_ids.isdisjoint(isolated_persona_ids))
            self.assertIn(report["common_persona_claim_id"], primary_persona_ids)
            self.assertIn(report["common_persona_claim_id"], isolated_persona_ids)
            self.assertIn(report["primary_private_persona_phrase"], primary_persona_text)
            self.assertNotIn(
                report["primary_private_persona_phrase"],
                isolated_persona_text,
            )
            self.assertEqual(
                isolated_snapshot.state.to_dict(),
                report["evidence"]["isolated_initial_state"],
            )
            self.assertEqual(
                {
                    key: reason.to_dict()
                    for key, reason in isolated_snapshot.state_reasons.items()
                },
                report["evidence"]["isolated_initial_state_reasons"],
            )
            self.assertEqual(
                primary_snapshot.state_reasons["intimacy"].evidence_event_id,
                report["shared_event_id"],
            )

            linked_memories = [
                memory
                for memory in primary.memories
                if memory.source_kind in {"memory_node", "experiential_timeline"}
            ]
            self.assertEqual(len(linked_memories), 2)
            for memory in linked_memories:
                self.assertEqual(
                    memory.provenance,
                    RecallArtifactProvenance.SOURCE_LINKED,
                )
                references = {
                    (
                        reference.source_kind,
                        reference.source_id,
                        reference.source_revision,
                    )
                    for reference in memory.source_references
                }
                self.assertIn(
                    ("source_turn", report["source_turn_id"], "1"),
                    references,
                )
                self.assertIn(
                    ("archival_batch", report["archival_id"], None),
                    references,
                )

            memory_pack_path = output_dir / report["artifacts"]["memory_pack"]
            memory_pack = MemoryPack.from_json(
                memory_pack_path.read_text(encoding="utf-8")
            )
            self.assertEqual(memory_pack.agent_id, report["agent_id"])
            self.assertEqual(memory_pack.user_id, report["primary_user_id"])
            self.assertIn(
                report["shared_event_id"],
                {event.event_id for event in memory_pack.relationship_events},
            )
            self.assertIn(
                report["source_turn_id"],
                {turn.turn_id for turn in memory_pack.turn_records},
            )
            self.assertTrue(memory_pack.nodes)
            self.assertTrue(memory_pack.timeline_entries)
            self.assertTrue(memory_pack.relationship_adjudications)
            self.assertTrue(memory_pack.relationship_processing_runs)
            self.assertIn(
                report["primary_manifest_id"],
                {
                    manifest.manifest_id
                    for manifest in memory_pack.persona_manifests
                },
            )
            self.assertNotIn(
                report["isolated_manifest_id"],
                {
                    manifest.manifest_id
                    for manifest in memory_pack.persona_manifests
                },
            )

            imported_database = output_dir / report["artifacts"]["imported_database"]
            imported_storage = SQLiteStorage(str(imported_database))
            self.assertIsNone(
                imported_storage.get_relationship(
                    report["agent_id"],
                    report["isolated_user_id"],
                )
            )
            with ERIIEngine(storage_driver=imported_storage) as imported_engine:
                imported_recall = imported_engine.recall_structured(
                    RecallRequest(
                        agent_id=report["agent_id"],
                        user_id=report["primary_user_id"],
                        query="first snow",
                        audience="agent_private",
                        options=RecallOptions(
                            persona_delivery="planned",
                            budget=RecallBudget(max_cost=50_000),
                        ),
                    )
                )
                imported_snapshot = imported_engine.get_relationship_snapshot(
                    report["agent_id"],
                    report["primary_user_id"],
                )
                imported_pack = imported_engine.export_memory(
                    report["agent_id"],
                    report["primary_user_id"],
                )

            self.assertEqual(
                imported_snapshot.profile.relationship_id,
                primary_snapshot.profile.relationship_id,
            )
            self.assertEqual(
                imported_snapshot.profile.persona_id,
                primary_snapshot.profile.persona_id,
            )
            self.assertEqual(
                imported_snapshot.state.to_dict(),
                primary_snapshot.state.to_dict(),
            )
            self.assertEqual(
                {event.source_id for event in imported_recall.events},
                {event.source_id for event in primary.events},
            )
            self.assertEqual(
                imported_recall.persona_context.manifest_id,
                primary.persona_context.manifest_id,
            )
            source_memories = {
                memory.source_id: memory
                for memory in linked_memories
            }
            imported_memories = {
                memory.source_id: memory
                for memory in imported_recall.memories
                if memory.source_kind
                in {"memory_node", "experiential_timeline"}
            }
            self.assertEqual(set(imported_memories), set(source_memories))
            for source_id, imported_memory in imported_memories.items():
                source_memory = source_memories[source_id]
                self.assertEqual(
                    imported_memory.provenance,
                    RecallArtifactProvenance.PARTIAL_SOURCE,
                )
                self.assertEqual(
                    imported_memory.authority_tier,
                    source_memory.authority_tier,
                )
                self.assertEqual(imported_memory.content, source_memory.content)
                self.assertEqual(
                    {
                        (
                            reference.source_kind,
                            reference.source_id,
                            reference.source_revision,
                        )
                        for reference in imported_memory.source_references
                    },
                    {("source_turn", report["source_turn_id"], "1")},
                )
            archival_tombstone = {
                item.archival_id: item
                for item in memory_pack.archival_ledger
            }[report["archival_id"]]
            self.assertIsNotNone(archival_tombstone.artifact_commitments)
            self.assertLessEqual(
                set(source_memories),
                {
                    item.artifact_id
                    for item in archival_tombstone.artifact_commitments
                },
            )
            self.assertEqual(
                {node.node_id for node in imported_pack.nodes},
                {node.node_id for node in memory_pack.nodes},
            )
            self.assertEqual(
                {turn.turn_id for turn in imported_pack.turn_records},
                {turn.turn_id for turn in memory_pack.turn_records},
            )
            self.assertEqual(
                {
                    (proposal.proposal_id, proposal.revision): proposal.decision_reason
                    for proposal in imported_pack.persona_compilation_proposals
                },
                {
                    (proposal.proposal_id, proposal.revision): proposal.decision_reason
                    for proposal in memory_pack.persona_compilation_proposals
                },
            )

    def test_legacy_missing_persona_decision_reason_is_retry_compatible(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result = run_golden_continuity_demo(root / "source-demo")
            source_path = result.output_dir / "user-a.erii"
            source_pack = MemoryPack.from_json(
                source_path.read_text(encoding="utf-8")
            )
            legacy_document = source_pack.to_dict()
            for proposal in legacy_document["persona_compilation_proposals"]:
                proposal["decision_reason"] = None
            legacy_pack = MemoryPack.from_dict(legacy_document)

            destination_path = root / "legacy-import.sqlite3"
            lifecycle = DataLifecycleCoordinator()
            source_target = LifecycleTarget(
                LifecycleTargetKind.MEMORY_PACK,
                str(source_path),
            )
            destination_target = LifecycleTarget(
                LifecycleTargetKind.SQLITE,
                str(destination_path),
            )
            plan = lifecycle.plan(
                MemoryPackImportRequest(
                    source=lifecycle.inspect(source_target),
                    destination=destination_target,
                )
            )

            with ERIIEngine(
                storage_driver=SQLiteStorage(str(destination_path))
            ) as engine:
                engine.import_memory(legacy_pack)
                engine.import_memory(source_pack)
                directly_retried = engine.export_memory(
                    source_pack.agent_id,
                    source_pack.user_id,
                )
            self.assertTrue(
                directly_retried.persona_compilation_proposals
            )
            self.assertTrue(
                all(
                    proposal.decision_reason is None
                    for proposal in directly_retried.persona_compilation_proposals
                )
            )

            report = lifecycle.execute(plan)
            self.assertEqual(report.outcome, LifecycleOutcome.ALREADY_COMPLETE)
            with ERIIEngine(
                storage_driver=SQLiteStorage(str(destination_path))
            ) as engine:
                after_lifecycle_retry = engine.export_memory(
                    source_pack.agent_id,
                    source_pack.user_id,
                )
            self.assertTrue(
                all(
                    proposal.decision_reason is None
                    for proposal in after_lifecycle_retry.persona_compilation_proposals
                )
            )

            strict_path = root / "strict-import.sqlite3"
            with ERIIEngine(
                storage_driver=SQLiteStorage(str(strict_path))
            ) as strict_engine:
                strict_engine.import_memory(source_pack)
                with self.assertRaises(ValueError):
                    strict_engine.import_memory(legacy_pack)

                conflicting_document = source_pack.to_dict()
                for proposal in conflicting_document[
                    "persona_compilation_proposals"
                ]:
                    proposal["decision_reason"] = (
                        "A conflicting non-empty historical reason."
                    )
                with self.assertRaises(ValueError):
                    strict_engine.import_memory(
                        MemoryPack.from_dict(conflicting_document)
                    )

    def test_cli_distinguishes_self_verification_failure_from_bad_arguments(self):
        failure = GoldenContinuityDemoVerificationError(
            "provenance proof failed; partial artifacts retained at C:\\demo"
        )
        with mock.patch.object(sys, "argv", ["erii", "demo"]):
            with mock.patch(
                "erii.demo.run_golden_continuity_demo",
                side_effect=failure,
            ):
                with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    with self.assertRaises(SystemExit) as raised:
                        server_module.cli_main()

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("partial artifacts retained at C:\\demo", stderr.getvalue())

    def test_unexpected_demo_failure_is_typed_and_names_partial_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "partial-demo"
            with mock.patch(
                "erii.demo._execute_golden_continuity_demo",
                side_effect=ValueError("invalid exported pack"),
            ):
                with self.assertRaises(GoldenContinuityDemoVerificationError) as raised:
                    run_golden_continuity_demo(output_dir)

        message = str(raised.exception)
        self.assertIn("invalid exported pack", message)
        self.assertIn("partial artifacts retained at", message)
        self.assertIn(str(output_dir.resolve()), message)

    def test_existing_output_and_invalid_arguments_exit_two(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            existing = Path(temporary_directory) / "existing"
            existing.mkdir()
            existing_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "erii.server.app",
                    "demo",
                    "--output-dir",
                    str(existing),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            invalid_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "erii.server.app",
                    "demo",
                    "--not-a-real-option",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(existing_result.returncode, 2)
        self.assertIn("existing", existing_result.stderr)
        self.assertIn("error:", existing_result.stderr.lower())
        self.assertEqual(invalid_result.returncode, 2)
        self.assertIn("unrecognized arguments", invalid_result.stderr)


if __name__ == "__main__":
    unittest.main()
