"""Public REST and MemoryPack round-trip contracts for the a8 evidence slice."""

from __future__ import annotations

from dataclasses import replace
import importlib
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from erii import (
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    FileStorage,
    SQLiteStorage,
)
from erii.models.adjudication import DecisionOutcome


server_module = importlib.import_module("erii.server.app")

AGENT_ID = "agent-a8-portable"
USER_ID = "user-a8-portable"
TURN_ID = "turn-a8-portable"
TEST_API_KEY = "test-a8-portable-key-1234567890-abcd"


def _visible_exchange_delivery_exception():
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.a8-portable/v1",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-02T00:00:00+00:00",
        "reply_attempt_number": None,
    }


class _SchemaTwoArchivalExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.a8-portable-archival",
        extractor_version="1",
        extraction_schema_version="2",
    )

    def extract(self, request):
        message = request.transcript.user_message
        quote = "second snow"
        start = message.content.index(quote)
        citation = {
            "citation_version": "archival-evidence-citation/v1",
            "kind": "message_span",
            "source_id": message.message_id,
            "source_revision": request.source_revision,
            "quote": quote,
            "start": start,
            "end": start + len(quote),
        }
        return {
            "kind": "artifacts",
            "memories": [
                {
                    "node_type": "event",
                    "content": "The user remembered the second snow.",
                    "evidence": [citation],
                }
            ],
            "timeline": [
                {
                    "content": "The second snow entered their shared history.",
                    "evidence": [citation],
                }
            ],
        }


class _AgentEvidenceRelationshipExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.a8-portable-relationship",
        extractor_version="1",
        extraction_schema_version="1",
    )

    def extract(self, request):
        message = request.transcript.agent_message
        return {
            "kind": "candidates",
            "candidates": [
                {
                    "candidate_key": "agent-only-memory-claim",
                    "event_type": "shared_experience",
                    "summary": "The character claimed this as shared history.",
                    "signal": {
                        "signal_type": "shared_experience",
                        "strength": "moderate",
                        "extraction_confidence": 0.99,
                        "interpretation_confidence": 0.99,
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


class _MissingTurnExportFileStorage(FileStorage):
    """Simulates a broken modern export graph without its Source Turn."""

    def list_turn_records(self, relationship_id):
        return []


class A8RestMemoryPackRoundTripPublicTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = self._temporary_directory.name
        self._engines = []
        server_module.configure_server_access(TEST_API_KEY)
        self.client = TestClient(
            server_module.app,
            headers={"X-API-Key": TEST_API_KEY},
        )

    def tearDown(self):
        self.client.close()
        server_module._engine = None
        server_module.configure_server_access(None)
        for engine in reversed(self._engines):
            engine.close()
        self._temporary_directory.cleanup()

    def _install_engine(self, engine: ERIIEngine) -> ERIIEngine:
        self._engines.append(engine)
        server_module._engine = engine
        return engine

    @staticmethod
    def _initialize_and_record(engine: ERIIEngine) -> None:
        engine.initialize_relationship(
            AGENT_ID,
            USER_ID,
            "A careful character who treats shared history honestly.",
        )
        engine.record_turn(
            AGENT_ID,
            USER_ID,
            "We watched the second snow together.",
            "I will remember the second snow.",
            turn_id=TURN_ID,
            delivery_exception=_visible_exchange_delivery_exception(),
        )

    def _rest_export(self):
        response = self.client.post(
            "/api/v1/memory/export",
            json={"agent_id": AGENT_ID, "user_id": USER_ID},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["pack"]

    def _rest_import(self, pack):
        response = self.client.post(
            "/api/v1/memory/import",
            json={"pack_data": pack},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["pack"]

    def test_rest_round_trip_preserves_schema_two_archival_evidence_closure(self):
        source = self._install_engine(
            ERIIEngine(
                storage_driver=FileStorage(os.path.join(self.root, "source")),
                memory_extractor=_SchemaTwoArchivalExtractor(),
                config=ERIIConfig(async_archival=False),
            )
        )
        self._initialize_and_record(source)
        source.archive_turn(
            AGENT_ID,
            USER_ID,
            TURN_ID,
            idempotency_key="a8-rest-schema-two-evidence",
        )

        exported = self._rest_export()
        self.assertEqual(len(exported["nodes"]), 1)
        self.assertEqual(len(exported["timeline_entries"]), 1)
        self.assertEqual(len(exported["turn_records"]), 1)
        source_reference = exported["nodes"][0]["evidence_references"][0]
        timeline_reference = exported["timeline_entries"][0][
            "evidence_references"
        ][0]
        self.assertEqual(source_reference, timeline_reference)
        self.assertEqual(source_reference["source_turn_id"], TURN_ID)
        self.assertEqual(source_reference["role"], "user")
        self.assertTrue(source_reference["evidence_id"].startswith("ae1_"))
        self.assertNotIn("quote", source_reference)

        target = self._install_engine(
            ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(self.root, "archival-target.db")
                )
            )
        )
        imported = self._rest_import(exported)
        self.assertEqual(
            imported["nodes"][0]["evidence_references"],
            exported["nodes"][0]["evidence_references"],
        )

        restored = self._rest_export()
        self.assertEqual(
            restored["nodes"][0]["evidence_references"],
            exported["nodes"][0]["evidence_references"],
        )
        self.assertEqual(
            restored["timeline_entries"][0]["evidence_references"],
            exported["timeline_entries"][0]["evidence_references"],
        )
        self.assertEqual(restored["turn_records"], exported["turn_records"])
        self.assertEqual(
            target.get_turn(AGENT_ID, USER_ID, TURN_ID).turn_id,
            TURN_ID,
        )

    def test_import_rejects_schema_two_artifact_content_tampering_atomically(self):
        source = self._install_engine(
            ERIIEngine(
                storage_driver=FileStorage(
                    os.path.join(self.root, "artifact-tamper-source")
                ),
                memory_extractor=_SchemaTwoArchivalExtractor(),
                config=ERIIConfig(async_archival=False),
            )
        )
        self._initialize_and_record(source)
        source.archive_turn(
            AGENT_ID,
            USER_ID,
            TURN_ID,
            idempotency_key="a8-artifact-content-tamper",
        )
        tampered = source.export_memory(AGENT_ID, USER_ID)
        tampered.nodes = [
            replace(
                tampered.nodes[0],
                content="Tampered content with the original artifact identity.",
            )
        ]
        tampered.core_memory = "must not be partially imported"

        target = self._install_engine(
            ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(self.root, "artifact-tamper-target.db")
                )
            )
        )
        with self.assertRaisesRegex(ValueError, "artifact commitment"):
            target.import_memory(tampered)
        self.assertIsNone(target.storage.get_relationship(AGENT_ID, USER_ID))
        self.assertEqual(target.get_core_memory(AGENT_ID, USER_ID), "")

    def test_rest_round_trip_preserves_quarantined_relationship_receipt(self):
        source = self._install_engine(
            ERIIEngine(
                storage_driver=SQLiteStorage(
                    os.path.join(self.root, "relationship-source.db")
                ),
                relationship_event_extractor=(
                    _AgentEvidenceRelationshipExtractor()
                ),
            )
        )
        self._initialize_and_record(source)
        run = source.process_relationship_turn(
            AGENT_ID,
            USER_ID,
            TURN_ID,
        )
        self.assertEqual(run.status.value, "completed")
        self.assertEqual(run.outcome.value, "no_accepted_events")
        self.assertEqual(run.event_ids, ())

        exported = self._rest_export()
        self.assertEqual(len(exported["relationship_adjudications"]), 1)
        source_record = exported["relationship_adjudications"][0]
        source_receipt = source_record["receipt"]
        self.assertEqual(source_receipt["outcome"], "rejected")
        self.assertEqual(
            source_receipt["reason_codes"],
            ["continuity_exception_agent_evidence_quarantined"],
        )
        self.assertEqual(source_receipt["event_ids"], [])
        self.assertEqual(source_record["events"], [])
        self.assertEqual(len(source_receipt["evidence"]), 1)
        self.assertEqual(source_receipt["evidence"][0]["role"], "agent")

        target = self._install_engine(
            ERIIEngine(
                storage_driver=FileStorage(
                    os.path.join(self.root, "relationship-target")
                )
            )
        )
        imported = self._rest_import(exported)
        self.assertEqual(
            imported["relationship_adjudications"],
            exported["relationship_adjudications"],
        )

        restored = self._rest_export()
        self.assertEqual(
            restored["relationship_adjudications"],
            exported["relationship_adjudications"],
        )
        self.assertEqual(
            restored["relationship_processing_runs"],
            exported["relationship_processing_runs"],
        )
        self.assertEqual(restored["relationship_events"], [])
        self.assertEqual(
            target.list_relationship_adjudications(AGENT_ID, USER_ID)[0].to_dict(),
            source_record,
        )

    def test_import_rejects_tampered_direct_exception_adjudication(self):
        source = self._install_engine(
            ERIIEngine(
                storage_driver=FileStorage(
                    os.path.join(self.root, "direct-source")
                )
            )
        )
        self._initialize_and_record(source)
        turn = source.get_turn(AGENT_ID, USER_ID, TURN_ID)
        agent_message = turn.transcript.agent_message
        rejected = source.adjudicate_turn_candidates(
            AGENT_ID,
            USER_ID,
            TURN_ID,
            [
                {
                    "candidate_key": "tampered-direct-agent-claim",
                    "event_type": "shared_experience",
                    "summary": "The exceptional Agent reply changed the relationship.",
                    "signal": {
                        "signal_type": "shared_experience",
                        "strength": "moderate",
                        "extraction_confidence": 0.99,
                        "interpretation_confidence": 0.99,
                    },
                    "evidence": [
                        {
                            "source_id": agent_message.message_id,
                            "source_revision": turn.source_revision,
                            "quote": agent_message.content,
                            "start": 0,
                            "end": len(agent_message.content),
                        }
                    ],
                }
            ],
            extractor_version="tests.direct-pack/v1",
        )
        self.assertEqual(
            rejected.receipts[0].outcome,
            DecisionOutcome.REJECTED,
        )

        original_pack = source.export_memory(AGENT_ID, USER_ID)
        self.assertEqual(original_pack.relationship_processing_runs, [])
        original = original_pack.relationship_adjudications[0]
        for index, contract_version in enumerate(
            (
                original.receipt.contract_version,
                "0.4.0a4",
            )
        ):
            with self.subTest(contract_version=contract_version):
                tampered = source.export_memory(AGENT_ID, USER_ID)
                tampered.relationship_adjudications = [
                    replace(
                        original,
                        receipt=replace(
                            original.receipt,
                            outcome=DecisionOutcome.ACCEPTED,
                            reason_codes=("accepted_by_policy",),
                            contract_version=contract_version,
                        ),
                    )
                ]
                tampered.core_memory = "must not be partially imported"

                target = self._install_engine(
                    ERIIEngine(
                        storage_driver=SQLiteStorage(
                            os.path.join(
                                self.root,
                                f"direct-target-{index}.db",
                            )
                        )
                    )
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "quarantined Agent evidence",
                ):
                    target.import_memory(tampered)
                self.assertIsNone(
                    target.storage.get_relationship(AGENT_ID, USER_ID)
                )
                self.assertEqual(
                    target.get_core_memory(AGENT_ID, USER_ID),
                    "",
                )

    def test_export_rejects_modern_direct_adjudication_without_source_turn(self):
        source = self._install_engine(
            ERIIEngine(
                storage_driver=_MissingTurnExportFileStorage(
                    os.path.join(self.root, "broken-direct-export")
                )
            )
        )
        self._initialize_and_record(source)
        turn = source.get_turn(AGENT_ID, USER_ID, TURN_ID)
        agent_message = turn.transcript.agent_message
        source.adjudicate_turn_candidates(
            AGENT_ID,
            USER_ID,
            TURN_ID,
            [
                {
                    "candidate_key": "missing-export-turn",
                    "event_type": "shared_experience",
                    "summary": "The exceptional Agent reply changed the relationship.",
                    "signal": {
                        "signal_type": "shared_experience",
                        "strength": "moderate",
                        "extraction_confidence": 0.99,
                        "interpretation_confidence": 0.99,
                    },
                    "evidence": [
                        {
                            "source_id": agent_message.message_id,
                            "source_revision": turn.source_revision,
                            "quote": agent_message.content,
                            "start": 0,
                            "end": len(agent_message.content),
                        }
                    ],
                }
            ],
            extractor_version="tests.missing-export-turn/v1",
        )
        export_path = os.path.join(self.root, "must-not-exist.erii")

        with self.assertRaisesRegex(ValueError, "exact completed Source Turn"):
            source.export_memory(
                AGENT_ID,
                USER_ID,
                export_path=export_path,
            )
        self.assertFalse(os.path.exists(export_path))


if __name__ == "__main__":
    unittest.main()
