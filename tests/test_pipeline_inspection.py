"""Privacy-safe, read-only relationship pipeline diagnostics."""

import os
import tempfile
import unittest

from erii import (
    ERIIEngine,
    ExtractorDescriptor,
    FileStorage,
    MemoryNode,
    PersonaManifestRequiredError,
    RecallOptions,
    RecallRequest,
    PipelineInspectionReport,
    ReplyContinuityAssessment,
    SourceProcessingChannel,
    SQLiteStorage,
    inspect_relationship_pipeline as inspect_public_pipeline,
)
from erii.core.pipeline_inspection import (
    PipelineIssueCode,
    inspect_relationship_pipeline,
)


class _NoRelationshipEventExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.no-event",
        extractor_version="1",
        extraction_schema_version="1",
    )

    def extract(self, request):
        del request
        return {
            "kind": "no_relationship_event",
            "reason_code": "ordinary_exchange",
        }


class _NoStructuredTimelineStorage(FileStorage):
    def list_timeline_entries(self, agent_id, user_id):
        del agent_id, user_id
        raise NotImplementedError("structured timeline is unavailable")


def _delivery_exception(reason_code="preexisting_visible_exchange"):
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.pipeline-inspection/v1",
        "reason_code": reason_code,
        "decided_at": "2026-08-01T00:00:00+00:00",
        "reply_attempt_number": None,
    }


class PipelineInspectionTests(unittest.TestCase):
    def test_missing_manifest_and_evaluator_are_reported_without_ids(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(storage_dir=root) as engine:
                engine.initialize_relationship(
                    "agent-secret",
                    "user-secret",
                    "private persona source",
                )

                report = inspect_relationship_pipeline(
                    engine,
                    "agent-secret",
                    "user-secret",
                )

        self.assertIs(inspect_public_pipeline, inspect_relationship_pipeline)
        self.assertIsInstance(report, PipelineInspectionReport)
        self.assertEqual(
            report.issue_codes,
            (
                PipelineIssueCode.MANIFEST_MISSING,
                PipelineIssueCode.CONTINUITY_EVALUATOR_UNCONFIGURED,
            ),
        )
        serialized = str(report.to_dict())
        self.assertNotIn("agent-secret", serialized)
        self.assertNotIn("user-secret", serialized)
        self.assertNotIn("private persona source", serialized)

    def test_unreviewed_shown_turn_and_pending_channels_are_counted(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=object(),
            ) as engine:
                engine.initialize_relationship(
                    "agent",
                    "user",
                    "source",
                )
                engine.record_turn(
                    "agent",
                    "user",
                    "sensitive user message",
                    "sensitive agent reply",
                    turn_id="sensitive-turn-id",
                    delivery_exception=_delivery_exception(),
                    processing_channels=(
                        SourceProcessingChannel.MEMORY_ARCHIVAL,
                        SourceProcessingChannel.RELATIONSHIP_ADJUDICATION,
                    ),
                )

                report = inspect_relationship_pipeline(engine, "agent", "user")

        self.assertIn(
            PipelineIssueCode.SHOWN_TURN_NOT_EVALUATED,
            report.issue_codes,
        )
        self.assertIn(
            PipelineIssueCode.DECLARED_CHANNEL_WITHOUT_TERMINAL_OUTCOME,
            report.issue_codes,
        )
        self.assertEqual(report.counts.shown_turns, 1)
        self.assertEqual(report.counts.shown_turns_not_evaluated, 1)
        self.assertEqual(
            report.counts.declared_channels_without_terminal_outcome,
            2,
        )
        serialized = str(report.to_dict())
        self.assertNotIn("sensitive user message", serialized)
        self.assertNotIn("sensitive agent reply", serialized)
        self.assertNotIn("sensitive-turn-id", serialized)

    def test_failed_continuity_evaluation_is_not_counted_as_completed(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=object(),
            ) as engine:
                engine.initialize_relationship("agent", "user", "source")
                turn = engine.begin_turn(
                    "agent",
                    "user",
                    "private user message",
                    turn_id="failed-continuity-turn",
                )
                engine.complete_turn(
                    "agent",
                    "user",
                    turn.turn_id,
                    "private shown reply",
                    continuity_assessment=ReplyContinuityAssessment(
                        status="failed",
                        evaluator_version="tests.evaluator/1",
                    ),
                    delivery_disposition="shown_unreviewed",
                    delivery_exception=_delivery_exception("availability_fallback"),
                    processing_channels=(),
                )

                report = inspect_relationship_pipeline(engine, "agent", "user")

        self.assertIn(
            PipelineIssueCode.SHOWN_TURN_NOT_EVALUATED,
            report.issue_codes,
        )
        self.assertEqual(report.counts.shown_turns_not_evaluated, 1)

    def test_legacy_artifacts_and_core_memory_are_aggregate_only(self):
        with tempfile.TemporaryDirectory() as root:
            factories = (
                ("file", lambda: FileStorage(os.path.join(root, "files"))),
                ("sqlite", lambda: SQLiteStorage(os.path.join(root, "memory.db"))),
            )
            for name, make_storage in factories:
                with self.subTest(storage=name):
                    with ERIIEngine(storage_driver=make_storage()) as engine:
                        engine.initialize_relationship("agent", "user", "source")
                        engine.storage.save_nodes(
                            "agent",
                            "user",
                            [
                                MemoryNode(
                                    node_id="private-node-id",
                                    agent_id="agent",
                                    user_id="user",
                                    content="private memory body",
                                )
                            ],
                        )
                        engine.storage.add_timeline_entry(
                            "agent",
                            "user",
                            "private timeline body",
                        )
                        engine.set_core_memory(
                            "agent",
                            "user",
                            "private legacy core body",
                        )

                        report = inspect_relationship_pipeline(
                            engine,
                            "agent",
                            "user",
                        )

                    self.assertIn(
                        PipelineIssueCode.LEGACY_PROVENANCE_PRESENT,
                        report.issue_codes,
                    )
                    self.assertIn(
                        PipelineIssueCode.LEGACY_CORE_MEMORY_PRESENT,
                        report.issue_codes,
                    )
                    self.assertEqual(report.counts.legacy_memory_nodes, 1)
                    self.assertEqual(report.counts.legacy_timeline_entries, 1)
                    self.assertEqual(report.counts.legacy_core_memory_records, 1)
                    serialized = str(report.to_dict())
                    self.assertNotIn("private memory body", serialized)
                    self.assertNotIn("private timeline body", serialized)
                    self.assertNotIn("private legacy core body", serialized)
                    self.assertNotIn("private-node-id", serialized)

    def test_consecutive_no_relationship_event_runs_are_visible_as_a_streak(self):
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_dir=root,
                relationship_event_extractor=_NoRelationshipEventExtractor(),
            ) as engine:
                engine.initialize_relationship("agent", "user", "source")
                for index in range(2):
                    turn_id = f"private-turn-{index}"
                    engine.record_turn(
                        "agent",
                        "user",
                        "private ordinary exchange",
                        "private ordinary reply",
                        turn_id=turn_id,
                        delivery_exception=_delivery_exception(),
                    )
                    engine.process_relationship_turn(
                        "agent",
                        "user",
                        turn_id,
                    )

                report = inspect_relationship_pipeline(engine, "agent", "user")

        self.assertIn(
            PipelineIssueCode.CONSECUTIVE_NO_RELATIONSHIP_EVENT,
            report.issue_codes,
        )
        self.assertEqual(report.counts.relationship_processing_runs, 2)
        self.assertEqual(report.counts.no_relationship_event_runs, 2)
        self.assertEqual(report.counts.longest_no_relationship_event_streak, 2)
        serialized = str(report.to_dict())
        self.assertNotIn("private ordinary exchange", serialized)
        self.assertNotIn("private ordinary reply", serialized)
        self.assertNotIn("private-turn", serialized)

    def test_missing_structured_timeline_capability_does_not_break_inspection(self):
        with tempfile.TemporaryDirectory() as root:
            storage = _NoStructuredTimelineStorage(root)
            with ERIIEngine(storage_driver=storage) as engine:
                engine.initialize_relationship("agent", "user", "source")

                report = inspect_relationship_pipeline(engine, "agent", "user")

        self.assertEqual(report.counts.legacy_timeline_entries, 0)

    def test_revoked_pinned_manifest_is_reported_as_missing(self):
        source = "Lumi is patient."
        candidate = {
            "compiler_version": "pipeline-inspection-v1",
            "source_spans": [
                {
                    "span_id": "span-identity",
                    "start": 0,
                    "end": len(source),
                    "quote": source,
                }
            ],
            "claims": [
                {
                    "claim_id": "claim-identity",
                    "kind": "identity",
                    "statement": source,
                    "activation_tier": "foundation",
                    "basis": "explicit",
                    "source_span_ids": ["span-identity"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_dir=root,
                continuity_evaluator=object(),
            ) as engine:
                engine.initialize_relationship("agent", "user", source)
                proposal = engine.propose_persona_compilation(
                    "agent",
                    "user",
                    candidate,
                )
                engine.decide_persona_compilation(
                    "agent",
                    "user",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
                engine.decide_persona_compilation(
                    "agent",
                    "user",
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "revoke",
                )

                report = inspect_relationship_pipeline(engine, "agent", "user")
                self.assertIn(
                    PipelineIssueCode.MANIFEST_MISSING,
                    report.issue_codes,
                )
                self.assertIsNotNone(
                    engine.get_persona_manifest("agent", "user")
                )
                with self.assertRaises(PersonaManifestRequiredError):
                    engine.recall_structured(
                        RecallRequest(
                            agent_id="agent",
                            user_id="user",
                            query="",
                            audience="agent_private",
                            options=RecallOptions(persona_delivery="planned"),
                        )
                    )
                turn = engine.begin_turn(
                    "agent",
                    "user",
                    "private message",
                    turn_id="turn-after-revoke",
                )
                with self.assertRaises(PersonaManifestRequiredError):
                    engine.activate_contextual_voice_patterns(
                        "agent",
                        "user",
                        turn.turn_id,
                    )
                with self.assertRaises(PersonaManifestRequiredError):
                    engine.evaluate_reply_continuity(
                        "agent",
                        "user",
                        turn.turn_id,
                        "private proposed reply",
                        persona_context_refs=(),
                    )


if __name__ == "__main__":
    unittest.main()
