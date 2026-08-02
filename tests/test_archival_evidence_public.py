"""Public contracts for a8 message-level archival evidence."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest

from erii import (
    ArchivalEvidenceCitation,
    ArchivalOutcomeCode,
    ArchivalPhase,
    ArchivalProcessingError,
    ArchivalStatus,
    ArchivalSubmissionError,
    ArtifactEvidenceReference,
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    FileStorage,
    MemoryPack,
    SQLiteStorage,
    TurnRole,
)
from erii.models.archival import (
    ArchivalReceipt,
    ArchivalRecord,
    PreparedArchivalBatch,
)


AGENT_ID = "agent-erii"
USER_ID = "user-one"


def _visible_exchange_delivery_exception():
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.archival-evidence/v1",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-02T00:00:00+00:00",
        "reply_attempt_number": None,
    }


def _citation(request, *, role: TurnRole, quote: str, start: int):
    message = (
        request.transcript.user_message
        if role == TurnRole.USER
        else request.transcript.agent_message
    )
    assert message is not None
    return {
        "citation_version": "archival-evidence-citation/v1",
        "kind": "message_span",
        "source_id": message.message_id,
        "source_revision": request.source_revision,
        "quote": quote,
        "start": start,
        "end": start + len(quote),
    }


def _reference_with(reference: ArtifactEvidenceReference, **changes):
    values = {
        "relationship_id": reference.relationship_id,
        "source_turn_id": reference.source_turn_id,
        "source_id": reference.source_id,
        "source_revision": reference.source_revision,
        "role": reference.role,
        "message_sha256": reference.message_sha256,
        "start": reference.start,
        "end": reference.end,
    }
    values.update(changes)
    return ArtifactEvidenceReference.create(**values)


class _SchemaOneExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.schema-one-memory-extractor",
        extractor_version="1",
        extraction_schema_version="1",
    )

    def __init__(self):
        self.calls = []

    def extract(self, request):
        self.calls.append(request)
        return {"kind": "no_memory", "reason_code": "nothing_durable"}


class _EvidenceExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.evidence-memory-extractor",
        extractor_version="1",
        extraction_schema_version="2",
    )

    def __init__(
        self,
        *,
        include_agent: bool = False,
        include_timeline: bool = False,
    ):
        self.include_agent = include_agent
        self.include_timeline = include_timeline
        self.calls = []

    def extract(self, request):
        self.calls.append(request)
        user_quote = "snow"
        user_content = request.transcript.user_message.content
        user_start = user_content.rindex(user_quote)
        memories = [
            {
                "node_type": "event",
                "content": "The user mentioned the second snow.",
                "evidence": [
                    _citation(
                        request,
                        role=TurnRole.USER,
                        quote=user_quote,
                        start=user_start,
                    )
                ],
            }
        ]
        if self.include_agent:
            agent_message = request.transcript.agent_message
            assert agent_message is not None
            memories.append(
                {
                    "node_type": "relationship",
                    "content": "The character promised to remember the snow.",
                    "evidence": [
                        _citation(
                            request,
                            role=TurnRole.AGENT,
                            quote=agent_message.content,
                            start=0,
                        )
                    ],
                }
            )
        timeline = []
        if self.include_timeline:
            timeline.append(
                {
                    "content": "The second snow became part of their shared history.",
                    "evidence": [
                        _citation(
                            request,
                            role=TurnRole.USER,
                            quote=user_quote,
                            start=user_start,
                        )
                    ],
                }
            )
        return {"kind": "artifacts", "memories": memories, "timeline": timeline}


class ArchivalEvidencePublicTests(unittest.TestCase):
    @staticmethod
    def _storage_cases(root: str):
        return (
            (
                "file",
                lambda: FileStorage(os.path.join(root, "files")),
            ),
            (
                "sqlite",
                lambda: SQLiteStorage(os.path.join(root, "memory.db")),
            ),
        )

    @staticmethod
    def _record_turn(engine: ERIIEngine, turn_id: str = "turn-snow"):
        engine.initialize_relationship(
            AGENT_ID,
            USER_ID,
            "A careful character who treats shared experiences honestly.",
        )
        return engine.record_turn(
            AGENT_ID,
            USER_ID,
            "snow, then snow",
            "I will remember this snow.",
            turn_id=turn_id,
            delivery_exception=_visible_exchange_delivery_exception(),
        )

    def _assert_empty_import_target(self, engine: ERIIEngine):
        target_export = engine.export_memory(AGENT_ID, USER_ID)
        self.assertEqual(target_export.core_memory, "")
        self.assertEqual(target_export.nodes, [])
        self.assertEqual(target_export.timeline_entries, [])
        self.assertIsNone(target_export.relationship)
        self.assertEqual(target_export.turn_records, [])

    def _export_schema_two_pack(self, root: str, *, include_timeline: bool = False):
        source = ERIIEngine(
            storage_driver=FileStorage(root),
            memory_extractor=_EvidenceExtractor(include_timeline=include_timeline),
            config=ERIIConfig(async_archival=False),
        )
        try:
            self._record_turn(source)
            source.archive_turn(
                AGENT_ID,
                USER_ID,
                "turn-snow",
                idempotency_key="portable-evidence-source",
            )
            return source.export_memory(AGENT_ID, USER_ID)
        finally:
            source.close()

    def test_schema_one_submission_fails_before_identity_creation(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _SchemaOneExtractor()
            engine = ERIIEngine(
                storage_driver=FileStorage(root),
                memory_extractor=extractor,
            )
            try:
                self._record_turn(engine)
                with self.assertRaisesRegex(
                    ArchivalSubmissionError,
                    "extraction schema 2",
                ):
                    engine.archive_turn(
                        AGENT_ID,
                        USER_ID,
                        "turn-snow",
                        idempotency_key="schema-one-is-not-modern",
                    )
                self.assertEqual(extractor.calls, [])
                self.assertEqual(
                    engine.list_archival_receipts(AGENT_ID, USER_ID),
                    [],
                )
            finally:
                engine.close()

    def test_existing_schema_one_extraction_fails_without_resampling(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _EvidenceExtractor()
            engine = ERIIEngine(
                storage_driver=FileStorage(root),
                memory_extractor=extractor,
                config=ERIIConfig(async_archival=True),
            )
            try:
                self._record_turn(engine)
                turn = engine.get_turn(AGENT_ID, USER_ID, "turn-snow")
                legacy_descriptor = ExtractorDescriptor(
                    extractor_id="tests.legacy-memory-extractor",
                    extractor_version="1",
                    extraction_schema_version="1",
                )
                archival_id = "a27878aa-363a-4e98-87ec-7d26f6e316aa"
                engine.storage.atomic_archival_store_v1().create_archival_record(
                    ArchivalRecord(
                        receipt=ArchivalReceipt(
                            archival_id=archival_id,
                            relationship_id=turn.relationship_id,
                            agent_id=AGENT_ID,
                            user_id=USER_ID,
                            source_turn_id=turn.turn_id,
                            source_revision=turn.source_revision,
                            status=ArchivalStatus.PENDING,
                            phase=ArchivalPhase.EXTRACTION,
                            extractor_descriptor=legacy_descriptor,
                            submitted_at="2026-08-02T00:00:00+00:00",
                            updated_at="2026-08-02T00:00:00+00:00",
                        ),
                        idempotency_fingerprint="legacy-idempotency",
                        request_fingerprint="legacy-request",
                    )
                )

                self.assertEqual(engine.process_pending(max_tasks=1), 1)
                receipt = engine.get_archival_receipt(
                    AGENT_ID,
                    USER_ID,
                    archival_id,
                )
                self.assertEqual(receipt.status, ArchivalStatus.FAILED)
                self.assertEqual(
                    receipt.outcome_code,
                    ArchivalOutcomeCode.EXTRACTOR_SCHEMA_UPGRADE_REQUIRED,
                )
                self.assertFalse(receipt.retryable)
                self.assertEqual(extractor.calls, [])
            finally:
                engine.close()

    def test_existing_schema_one_prepared_commit_finishes_without_resampling(self):
        with tempfile.TemporaryDirectory() as root:
            extractor = _EvidenceExtractor()
            engine = ERIIEngine(
                storage_driver=FileStorage(root),
                memory_extractor=extractor,
                config=ERIIConfig(async_archival=True),
            )
            try:
                self._record_turn(engine)
                turn = engine.get_turn(AGENT_ID, USER_ID, "turn-snow")
                legacy_descriptor = ExtractorDescriptor(
                    extractor_id="tests.legacy-memory-extractor",
                    extractor_version="1",
                    extraction_schema_version="1",
                )
                archival_id = "decd4d43-b062-463d-91bc-288e3b321b86"
                batch = PreparedArchivalBatch(
                    archival_id=archival_id,
                    relationship_id=turn.relationship_id,
                    source_turn_id=turn.turn_id,
                    source_revision=turn.source_revision,
                    descriptor=legacy_descriptor,
                )
                engine.storage.atomic_archival_store_v1().create_archival_record(
                    ArchivalRecord(
                        receipt=ArchivalReceipt(
                            archival_id=archival_id,
                            relationship_id=turn.relationship_id,
                            agent_id=AGENT_ID,
                            user_id=USER_ID,
                            source_turn_id=turn.turn_id,
                            source_revision=turn.source_revision,
                            status=ArchivalStatus.PENDING,
                            phase=ArchivalPhase.COMMIT,
                            extractor_descriptor=legacy_descriptor,
                            submitted_at="2026-08-02T00:00:00+00:00",
                            updated_at="2026-08-02T00:00:00+00:00",
                        ),
                        idempotency_fingerprint="legacy-commit-idempotency",
                        request_fingerprint="legacy-commit-request",
                        prepared_batch=batch,
                        prepared_outcome_code=ArchivalOutcomeCode.NO_MEMORY,
                        commit_binding_digest=batch.batch_digest,
                    )
                )

                self.assertEqual(engine.process_pending(max_tasks=1), 1)
                receipt = engine.get_archival_receipt(
                    AGENT_ID,
                    USER_ID,
                    archival_id,
                )
                self.assertEqual(receipt.status, ArchivalStatus.COMPLETED)
                self.assertEqual(
                    receipt.outcome_code,
                    ArchivalOutcomeCode.NO_MEMORY,
                )
                self.assertEqual(extractor.calls, [])
            finally:
                engine.close()

    def test_user_evidence_round_trips_through_bundled_stores_and_memorypack(self):
        for name, make_storage in self._storage_cases(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = _EvidenceExtractor()
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(async_archival=False),
                )
                try:
                    self._record_turn(engine)
                    receipt = engine.archive_turn(
                        AGENT_ID,
                        USER_ID,
                        "turn-snow",
                        idempotency_key=f"verified-user-evidence-{name}",
                    )
                    node = next(
                        item
                        for item in engine.storage.load_nodes(AGENT_ID, USER_ID)
                        if item.source_archival_id == receipt.archival_id
                    )
                    self.assertEqual(len(node.evidence_references), 1)
                    reference = node.evidence_references[0]
                    self.assertEqual(reference.role, TurnRole.USER)
                    self.assertEqual(reference.start, 11)
                    self.assertEqual(reference.end, 15)
                    self.assertTrue(reference.evidence_id.startswith("ae1_"))
                    self.assertFalse(hasattr(reference, "quote"))
                    self.assertEqual(
                        reference.message_sha256,
                        hashlib.sha256("snow, then snow".encode("utf-8")).hexdigest(),
                    )

                    portable = MemoryPack.from_json(
                        engine.export_memory(AGENT_ID, USER_ID).to_json()
                    )
                    portable_node = next(
                        item
                        for item in portable.nodes
                        if item.node_id == node.node_id
                    )
                    self.assertEqual(
                        portable_node.evidence_references,
                        node.evidence_references,
                    )
                finally:
                    engine.close()

    def test_schema_two_import_rejects_missing_source_turn_before_writes(self):
        with tempfile.TemporaryDirectory() as root:
            portable = self._export_schema_two_pack(
                os.path.join(root, "source"),
                include_timeline=True,
            )
            target = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "target")),
            )
            try:
                portable.core_memory = "must not be partially imported"
                portable.nodes = []
                portable.turn_records = []

                with self.assertRaisesRegex(ValueError, "source Turn"):
                    target.import_memory(portable)

                self._assert_empty_import_target(target)
            finally:
                target.close()

    def test_schema_two_import_accepts_closed_memory_and_timeline_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            portable = self._export_schema_two_pack(
                os.path.join(root, "source"),
                include_timeline=True,
            )
            target = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "target")),
            )
            try:
                target.import_memory(MemoryPack.from_json(portable.to_json()))
                restored = target.export_memory(AGENT_ID, USER_ID)

                self.assertEqual(
                    restored.nodes[0].evidence_references,
                    portable.nodes[0].evidence_references,
                )
                self.assertEqual(
                    restored.timeline_entries[0].evidence_references,
                    portable.timeline_entries[0].evidence_references,
                )
                self.assertEqual(restored.turn_records, portable.turn_records)
            finally:
                target.close()

    def test_schema_two_import_rejects_reference_mismatches_before_writes(self):
        with tempfile.TemporaryDirectory() as root:
            pristine = self._export_schema_two_pack(os.path.join(root, "source"))
            mutations = (
                (
                    "turn",
                    {"source_turn_id": "turn-other"},
                    "different source Turn",
                ),
                (
                    "message",
                    {"source_id": "message-other"},
                    "source message was not found",
                ),
                (
                    "revision",
                    {"source_revision": "2"},
                    "source_revision",
                ),
                (
                    "role",
                    {"role": TurnRole.AGENT},
                    "evidence role",
                ),
                (
                    "hash",
                    {"message_sha256": "0" * 64},
                    "evidence hash",
                ),
                (
                    "span",
                    {"end": 1000},
                    "evidence span",
                ),
                (
                    "relationship",
                    {"relationship_id": "relationship-other"},
                    "relationship boundaries",
                ),
            )
            for name, changes, error in mutations:
                with self.subTest(mutation=name):
                    portable = MemoryPack.from_json(pristine.to_json())
                    reference = portable.nodes[0].evidence_references[0]
                    portable.nodes[0].evidence_references = (
                        _reference_with(reference, **changes),
                    )
                    portable.core_memory = "must not be partially imported"
                    target = ERIIEngine(
                        storage_driver=FileStorage(
                            os.path.join(root, f"target-{name}")
                        ),
                    )
                    try:
                        with self.assertRaisesRegex(ValueError, error):
                            target.import_memory(portable)
                        self._assert_empty_import_target(target)
                    finally:
                        target.close()

    def test_schema_two_import_quarantines_exceptional_agent_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            portable = self._export_schema_two_pack(os.path.join(root, "source"))
            turn = portable.turn_records[0]
            agent_message = turn.transcript.agent_message
            self.assertIsNotNone(agent_message)
            portable.nodes[0].evidence_references = (
                ArtifactEvidenceReference.create(
                    relationship_id=turn.relationship_id,
                    source_turn_id=turn.turn_id,
                    source_id=agent_message.message_id,
                    source_revision=turn.source_revision,
                    role=TurnRole.AGENT,
                    message_sha256=hashlib.sha256(
                        agent_message.content.encode("utf-8")
                    ).hexdigest(),
                    start=0,
                    end=len(agent_message.content),
                ),
            )
            portable.core_memory = "must not be partially imported"
            target = ERIIEngine(
                storage_driver=FileStorage(os.path.join(root, "target")),
            )
            try:
                with self.assertRaisesRegex(ValueError, "quarantined"):
                    target.import_memory(portable)
                self._assert_empty_import_target(target)
            finally:
                target.close()

    def test_exceptional_agent_evidence_invalidates_the_complete_decision(self):
        for name, make_storage in self._storage_cases(tempfile.mkdtemp()):
            with self.subTest(storage=name):
                extractor = _EvidenceExtractor(include_agent=True)
                engine = ERIIEngine(
                    storage_driver=make_storage(),
                    memory_extractor=extractor,
                    config=ERIIConfig(async_archival=False),
                )
                try:
                    self._record_turn(engine)
                    with self.assertRaises(ArchivalProcessingError) as raised:
                        engine.archive_turn(
                            AGENT_ID,
                            USER_ID,
                            "turn-snow",
                            idempotency_key=f"mixed-authority-{name}",
                        )
                    self.assertEqual(
                        raised.exception.receipt.outcome_code,
                        ArchivalOutcomeCode.INVALID_EXTRACTOR_OUTPUT,
                    )
                    self.assertEqual(
                        engine.storage.load_nodes(AGENT_ID, USER_ID),
                        [],
                    )
                finally:
                    engine.close()

    def test_strict_citation_and_reference_wire_reject_ambiguous_or_tampered_data(self):
        citation = ArchivalEvidenceCitation.from_dict(
            {
                "citation_version": "archival-evidence-citation/v1",
                "kind": "message_span",
                "source_id": "message-user",
                "source_revision": "1",
                "quote": "雪",
                "start": 2,
                "end": 3,
            }
        )
        self.assertEqual(
            ArchivalEvidenceCitation.from_dict(citation.to_dict()),
            citation,
        )

        for mutation in (
            {"start": True},
            {"end": 2},
            {"citation_version": "archival-evidence-citation/v2"},
            {"extra": "field"},
        ):
            raw = citation.to_dict()
            raw.update(mutation)
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    ArchivalEvidenceCitation.from_dict(raw)

        reference = ArtifactEvidenceReference.create(
            relationship_id="relationship-one",
            source_turn_id="turn-one",
            source_id="message-user",
            source_revision="1",
            role=TurnRole.USER,
            message_sha256="a" * 64,
            start=2,
            end=3,
        )
        tampered = reference.to_dict()
        tampered["start"] = 1
        with self.assertRaisesRegex(ValueError, "evidence_id"):
            ArtifactEvidenceReference.from_dict(tampered)


if __name__ == "__main__":
    unittest.main()
