"""Structured recall preserves the source chain of modern memory artifacts."""

from dataclasses import replace
import os
import tempfile
import unittest

from erii import (
    ArchivalArtifactsDecision,
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    FileStorage,
    MemoryCandidate,
    MemoryNode,
    MemoryType,
    PersonaDelivery,
    RecallAudience,
    RecallArtifactProvenance,
    RecallOptions,
    RecallRequest,
    SQLiteStorage,
    TimelineCandidate,
)
from erii.models.provenance import ArtifactProvenanceState


class _ArtifactExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.provenance-extractor",
        extractor_version="1",
        extraction_schema_version="1",
    )

    def extract(self, request):
        return ArchivalArtifactsDecision(
            timeline=(
                TimelineCandidate(
                    content="We spent an ordinary afternoon at the arcade.",
                ),
            ),
            memories=(
                MemoryCandidate(
                    node_type=MemoryType.EVENT,
                    content="We played one arcade game together.",
                    tags=("arcade",),
                ),
            ),
        )


class _CountingFileStorage(FileStorage):
    def __init__(self, root_dir):
        super().__init__(root_dir)
        self.turn_record_reads = 0
        self.turn_record_list_reads = 0
        self.turn_record_batch_reads = 0
        self.archival_record_list_reads = 0

    def get_turn_record(self, relationship_id, turn_id):
        self.turn_record_reads += 1
        return super().get_turn_record(relationship_id, turn_id)

    def list_turn_records(self, relationship_id):
        self.turn_record_list_reads += 1
        return super().list_turn_records(relationship_id)

    def get_turn_records(self, relationship_id, turn_ids):
        self.turn_record_batch_reads += 1
        return super().get_turn_records(relationship_id, turn_ids)

    def list_archival_records(self, relationship_id):
        self.archival_record_list_reads += 1
        return super().list_archival_records(relationship_id)


class _LegacyManifestFileStorage(FileStorage):
    """Simulates a v1 receipt whose manifest predates payload fingerprints."""

    def list_archival_records(self, relationship_id):
        return [
            replace(
                record,
                receipt=replace(
                    record.receipt,
                    artifact_manifest=tuple(
                        replace(item, artifact_fingerprint=None)
                        for item in record.receipt.artifact_manifest
                    ),
                ),
                prepared_batch=None,
            )
            for record in super().list_archival_records(relationship_id)
        ]


class _TimelineTamperMixin:
    timeline_mutation = None

    def get_recent_timeline_entries(self, agent_id, user_id, limit=5):
        entries = super().get_recent_timeline_entries(agent_id, user_id, limit)
        if not entries or self.timeline_mutation is None:
            return entries
        first = entries[0]
        if self.timeline_mutation == "content":
            first = replace(
                first,
                content="A forged timeline payload reusing a real artifact ID.",
            )
        elif self.timeline_mutation == "descriptor":
            first = replace(
                first,
                extractor_descriptor=replace(
                    first.extractor_descriptor,
                    extractor_version="forged",
                ),
            )
        return [first, *entries[1:]]


class _TimelineTamperingFileStorage(_TimelineTamperMixin, FileStorage):
    pass


class _TimelineTamperingSQLiteStorage(_TimelineTamperMixin, SQLiteStorage):
    pass


class StructuredRecallMemoryProvenanceTests(unittest.TestCase):
    def test_modern_memory_and_timeline_projections_name_their_source_chain(self):
        with tempfile.TemporaryDirectory() as root:
            storage_factories = (
                ("file", lambda: FileStorage(os.path.join(root, "files"))),
                ("sqlite", lambda: SQLiteStorage(os.path.join(root, "memory.db"))),
            )
            for name, make_storage in storage_factories:
                with self.subTest(storage=name):
                    engine = ERIIEngine(
                        storage_driver=make_storage(),
                        config=ERIIConfig(async_archival=False),
                        memory_extractor=_ArtifactExtractor(),
                    )
                    engine.initialize_relationship(
                        "agent-lumi",
                        "user-chen",
                        "Lumi is patient.",
                    )
                    turn = engine.record_turn(
                        "agent-lumi",
                        "user-chen",
                        "Let us play one arcade game.",
                        "Okay.",
                        turn_id=f"turn-{name}",
                    )
                    receipt = engine.archive_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.source_turn_id,
                        idempotency_key=f"archive-{name}",
                    )

                    result = engine.recall_structured(
                        RecallRequest(
                            agent_id="agent-lumi",
                            user_id="user-chen",
                            query="arcade",
                            audience=RecallAudience.AGENT_PRIVATE,
                            options=RecallOptions(
                                persona_delivery=PersonaDelivery.FULL,
                            ),
                        )
                    )

                    modern = [
                        item
                        for item in result.memories
                        if item.source_kind
                        in {"memory_node", "experiential_timeline"}
                    ]
                    self.assertEqual(len(modern), 2)
                    for projection in modern:
                        self.assertEqual(
                            projection.provenance,
                            RecallArtifactProvenance.SOURCE_LINKED,
                        )
                        references = {
                            (item.source_kind, item.source_id, item.source_revision)
                            for item in projection.source_references
                        }
                        self.assertIn(
                            ("source_turn", turn.source_turn_id, "1"),
                            references,
                        )
                        self.assertIn(
                            ("archival_batch", receipt.archival_id, None),
                            references,
                        )

                    engine.close()

    def test_legacy_artifacts_with_incomplete_references_are_not_source_linked(self):
        with tempfile.TemporaryDirectory() as root:
            engine = ERIIEngine(storage_dir=root)
            profile = engine.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi is patient.",
            )
            engine.storage.save_nodes(
                "agent-lumi",
                "user-chen",
                [
                    MemoryNode(
                        node_id="memory-missing-turn",
                        agent_id="agent-lumi",
                        user_id="user-chen",
                        relationship_id=profile.relationship_id,
                        source_turn_id="turn-does-not-exist",
                        source_archival_id="archive-legacy",
                        node_type=MemoryType.EVENT,
                        content="An imported memory with an unresolved source turn.",
                    ),
                    MemoryNode(
                        node_id="memory-archival-only",
                        agent_id="agent-lumi",
                        user_id="user-chen",
                        relationship_id=profile.relationship_id,
                        source_archival_id="archive-only",
                        node_type=MemoryType.EVENT,
                        content="An imported memory with only an archival reference.",
                    ),
                    MemoryNode(
                        node_id="memory-unresolved",
                        agent_id="agent-lumi",
                        user_id="user-chen",
                        relationship_id=profile.relationship_id,
                        node_type=MemoryType.EVENT,
                        content="An imported memory without source references.",
                    ),
                ],
            )

            result = engine.recall_structured(
                RecallRequest(
                    agent_id="agent-lumi",
                    user_id="user-chen",
                    query="imported memory source archival",
                    audience=RecallAudience.AGENT_PRIVATE,
                    options=RecallOptions(
                        top_k=10,
                        max_per_type=10,
                        persona_delivery=PersonaDelivery.FULL,
                    ),
                )
            )
            by_id = {item.source_id: item for item in result.memories}

            self.assertEqual(
                by_id["memory-missing-turn"].provenance,
                RecallArtifactProvenance.PARTIAL_SOURCE,
            )
            self.assertEqual(
                by_id["memory-archival-only"].provenance,
                RecallArtifactProvenance.PARTIAL_SOURCE,
            )
            self.assertEqual(
                by_id["memory-unresolved"].provenance,
                RecallArtifactProvenance.LEGACY_UNRESOLVED,
            )
            engine.close()

    def test_one_recall_resolves_a_shared_source_turn_only_once(self):
        with tempfile.TemporaryDirectory() as root:
            storage = _CountingFileStorage(root)
            engine = ERIIEngine(
                storage_driver=storage,
                config=ERIIConfig(async_archival=False),
                memory_extractor=_ArtifactExtractor(),
            )
            engine.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi is patient.",
            )
            turn = engine.record_turn(
                "agent-lumi",
                "user-chen",
                "Let us play one arcade game.",
                "Okay.",
                turn_id="turn-shared-source",
            )
            engine.archive_turn(
                "agent-lumi",
                "user-chen",
                turn.source_turn_id,
                idempotency_key="archive-shared-source",
            )
            storage.turn_record_reads = 0
            storage.turn_record_list_reads = 0
            storage.turn_record_batch_reads = 0
            storage.archival_record_list_reads = 0

            engine.recall_structured(
                RecallRequest(
                    agent_id="agent-lumi",
                    user_id="user-chen",
                    query="arcade",
                    audience=RecallAudience.AGENT_PRIVATE,
                    options=RecallOptions(
                        persona_delivery=PersonaDelivery.FULL,
                    ),
                )
            )

            self.assertEqual(storage.turn_record_batch_reads, 1)
            self.assertEqual(storage.turn_record_list_reads, 0)
            self.assertEqual(storage.turn_record_reads, 0)
            self.assertEqual(storage.archival_record_list_reads, 1)
            engine.close()

    def test_one_recall_reads_the_turn_ledger_once_for_distinct_turns(self):
        with tempfile.TemporaryDirectory() as root:
            storage = _CountingFileStorage(root)
            engine = ERIIEngine(storage_driver=storage)
            profile = engine.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi is patient.",
            )
            descriptor = ExtractorDescriptor(
                extractor_id="tests.synthetic-provenance",
                extractor_version="1",
                extraction_schema_version="1",
            )
            nodes = []
            for index in range(6):
                turn = engine.record_turn(
                    "agent-lumi",
                    "user-chen",
                    f"shared query user message {index}",
                    f"shared query reply {index}",
                    turn_id=f"turn-distinct-{index}",
                )
                nodes.append(
                    MemoryNode(
                        node_id=f"memory-distinct-{index}",
                        agent_id="agent-lumi",
                        user_id="user-chen",
                        relationship_id=profile.relationship_id,
                        source_turn_id=turn.source_turn_id,
                        source_archival_id=f"missing-archive-{index}",
                        provenance_state=ArtifactProvenanceState.COMPLETE,
                        extractor_descriptor=descriptor,
                        node_type=MemoryType.EVENT,
                        content=f"shared query memory {index}",
                    )
                )
            storage.save_nodes("agent-lumi", "user-chen", nodes)
            storage.turn_record_reads = 0
            storage.turn_record_list_reads = 0
            storage.turn_record_batch_reads = 0

            engine.recall_structured(
                RecallRequest(
                    agent_id="agent-lumi",
                    user_id="user-chen",
                    query="shared query",
                    audience=RecallAudience.AGENT_PRIVATE,
                    options=RecallOptions(
                        top_k=10,
                        max_per_type=10,
                        persona_delivery=PersonaDelivery.FULL,
                    ),
                )
            )

            self.assertEqual(storage.turn_record_batch_reads, 1)
            self.assertEqual(storage.turn_record_list_reads, 0)
            self.assertEqual(storage.turn_record_reads, 0)
            engine.close()

    def test_fake_archival_id_cannot_certify_complete_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            engine = ERIIEngine(storage_dir=root)
            profile = engine.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi is patient.",
            )
            turn = engine.record_turn(
                "agent-lumi",
                "user-chen",
                "A source-linked question.",
                "A source-linked answer.",
                turn_id="turn-real",
            )
            engine.storage.save_nodes(
                "agent-lumi",
                "user-chen",
                [
                    MemoryNode(
                        node_id="memory-fake-archive",
                        agent_id="agent-lumi",
                        user_id="user-chen",
                        relationship_id=profile.relationship_id,
                        source_turn_id=turn.source_turn_id,
                        source_archival_id="archive-does-not-exist",
                        provenance_state=ArtifactProvenanceState.COMPLETE,
                        extractor_descriptor=ExtractorDescriptor(
                            extractor_id="tests.synthetic-provenance",
                            extractor_version="1",
                            extraction_schema_version="1",
                        ),
                        node_type=MemoryType.EVENT,
                        content="A source-linked imported memory.",
                    )
                ],
            )

            result = engine.recall_structured(
                RecallRequest(
                    agent_id="agent-lumi",
                    user_id="user-chen",
                    query="source linked imported memory",
                    audience=RecallAudience.AGENT_PRIVATE,
                    options=RecallOptions(persona_delivery=PersonaDelivery.FULL),
                )
            )

            projection = next(
                item
                for item in result.memories
                if item.source_id == "memory-fake-archive"
            )
            self.assertEqual(
                projection.provenance,
                RecallArtifactProvenance.PARTIAL_SOURCE,
            )
            self.assertNotIn(
                "archive-does-not-exist",
                {item.source_id for item in projection.source_references},
            )
            engine.close()

    def test_archival_manifest_must_name_the_exact_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            engine = ERIIEngine(
                storage_driver=FileStorage(root),
                config=ERIIConfig(
                    async_archival=False,
                ),
                memory_extractor=_ArtifactExtractor(),
            )
            engine.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi is patient.",
            )
            turn = engine.record_turn(
                "agent-lumi",
                "user-chen",
                "Let us play one arcade game.",
                "Okay.",
                turn_id="turn-real-manifest",
            )
            receipt = engine.archive_turn(
                "agent-lumi",
                "user-chen",
                turn.source_turn_id,
                idempotency_key="archive-real-manifest",
            )
            original = engine.storage.load_nodes(
                "agent-lumi",
                "user-chen",
            )[0]
            forged_data = original.to_dict()
            forged_data.update(
                {
                    "node_id": "memory-not-in-manifest",
                    "content": "An arcade artifact absent from the receipt manifest.",
                    "source_archival_id": receipt.archival_id,
                }
            )
            engine.storage.save_nodes(
                "agent-lumi",
                "user-chen",
                [MemoryNode.from_dict(forged_data)],
            )

            result = engine.recall_structured(
                RecallRequest(
                    agent_id="agent-lumi",
                    user_id="user-chen",
                    query="artifact absent receipt manifest",
                    audience=RecallAudience.AGENT_PRIVATE,
                    options=RecallOptions(
                        top_k=10,
                        persona_delivery=PersonaDelivery.FULL,
                    ),
                )
            )

            projection = next(
                item
                for item in result.memories
                if item.source_id == "memory-not-in-manifest"
            )
            self.assertEqual(
                projection.provenance,
                RecallArtifactProvenance.PARTIAL_SOURCE,
            )
            self.assertNotIn(
                receipt.archival_id,
                {item.source_id for item in projection.source_references},
            )
            engine.close()

    def test_archival_manifest_id_cannot_certify_a_mutated_payload(self):
        with tempfile.TemporaryDirectory() as root:
            storage_factories = (
                ("file", lambda: FileStorage(os.path.join(root, "files"))),
                ("sqlite", lambda: SQLiteStorage(os.path.join(root, "memory.db"))),
            )
            for name, make_storage in storage_factories:
                with self.subTest(storage=name):
                    engine = ERIIEngine(
                        storage_driver=make_storage(),
                        config=ERIIConfig(async_archival=False),
                        memory_extractor=_ArtifactExtractor(),
                    )
                    engine.initialize_relationship(
                        "agent-lumi",
                        "user-chen",
                        "Lumi is patient.",
                    )
                    turn = engine.record_turn(
                        "agent-lumi",
                        "user-chen",
                        "Let us play one arcade game.",
                        "Okay.",
                        turn_id=f"turn-mutated-{name}",
                    )
                    receipt = engine.archive_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.source_turn_id,
                        idempotency_key=f"archive-mutated-{name}",
                    )
                    original = engine.storage.load_nodes(
                        "agent-lumi",
                        "user-chen",
                    )[0]
                    forged_data = original.to_dict()
                    forged_data["content"] = (
                        "A forged payload reusing a real archival artifact ID."
                    )
                    engine.storage.save_nodes(
                        "agent-lumi",
                        "user-chen",
                        [MemoryNode.from_dict(forged_data)],
                    )

                    result = engine.recall_structured(
                        RecallRequest(
                            agent_id="agent-lumi",
                            user_id="user-chen",
                            query="forged payload archival artifact",
                            audience=RecallAudience.AGENT_PRIVATE,
                            options=RecallOptions(
                                persona_delivery=PersonaDelivery.FULL,
                            ),
                        )
                    )

                    projection = next(
                        item
                        for item in result.memories
                        if item.source_id == original.node_id
                    )
                    self.assertEqual(
                        projection.provenance,
                        RecallArtifactProvenance.PARTIAL_SOURCE,
                    )
                    self.assertNotIn(
                        receipt.archival_id,
                        {
                            item.source_id
                            for item in projection.source_references
                        },
                    )
                    engine.close()

    def test_archival_manifest_cannot_certify_a_mutated_extractor_descriptor(self):
        with tempfile.TemporaryDirectory() as root:
            storage_factories = (
                ("file", lambda: FileStorage(os.path.join(root, "files"))),
                ("sqlite", lambda: SQLiteStorage(os.path.join(root, "memory.db"))),
            )
            for name, make_storage in storage_factories:
                with self.subTest(storage=name):
                    engine = ERIIEngine(
                        storage_driver=make_storage(),
                        config=ERIIConfig(async_archival=False),
                        memory_extractor=_ArtifactExtractor(),
                    )
                    engine.initialize_relationship(
                        "agent-lumi",
                        "user-chen",
                        "Lumi is patient.",
                    )
                    turn = engine.record_turn(
                        "agent-lumi",
                        "user-chen",
                        "Let us play one arcade game.",
                        "Okay.",
                        turn_id=f"turn-mutated-descriptor-{name}",
                    )
                    receipt = engine.archive_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.source_turn_id,
                        idempotency_key=f"archive-mutated-descriptor-{name}",
                    )
                    original = engine.storage.load_nodes(
                        "agent-lumi",
                        "user-chen",
                    )[0]
                    forged_data = original.to_dict()
                    forged_data["extractor_descriptor"] = dict(
                        forged_data["extractor_descriptor"]
                    )
                    forged_data["extractor_descriptor"]["extractor_version"] = (
                        "forged"
                    )
                    engine.storage.save_nodes(
                        "agent-lumi",
                        "user-chen",
                        [MemoryNode.from_dict(forged_data)],
                    )

                    result = engine.recall_structured(
                        RecallRequest(
                            agent_id="agent-lumi",
                            user_id="user-chen",
                            query="arcade",
                            audience=RecallAudience.AGENT_PRIVATE,
                            options=RecallOptions(
                                persona_delivery=PersonaDelivery.FULL,
                            ),
                        )
                    )

                    projection = next(
                        item
                        for item in result.memories
                        if item.source_id == original.node_id
                    )
                    self.assertEqual(
                        projection.provenance,
                        RecallArtifactProvenance.PARTIAL_SOURCE,
                    )
                    self.assertNotIn(
                        receipt.archival_id,
                        {
                            item.source_id
                            for item in projection.source_references
                        },
                    )
                    engine.close()

    def test_timeline_manifest_binds_content_and_extractor_descriptor(self):
        with tempfile.TemporaryDirectory() as root:
            storage_factories = (
                (
                    "file",
                    lambda mutation: _TimelineTamperingFileStorage(
                        os.path.join(root, f"files-{mutation}")
                    ),
                ),
                (
                    "sqlite",
                    lambda mutation: _TimelineTamperingSQLiteStorage(
                        os.path.join(root, f"memory-{mutation}.db")
                    ),
                ),
            )
            for name, make_storage in storage_factories:
                for mutation in ("content", "descriptor"):
                    with self.subTest(storage=name, mutation=mutation):
                        storage = make_storage(mutation)
                        engine = ERIIEngine(
                            storage_driver=storage,
                            config=ERIIConfig(async_archival=False),
                            memory_extractor=_ArtifactExtractor(),
                        )
                        engine.initialize_relationship(
                            "agent-lumi",
                            "user-chen",
                            "Lumi is patient.",
                        )
                        turn = engine.record_turn(
                            "agent-lumi",
                            "user-chen",
                            "Let us play one arcade game.",
                            "Okay.",
                            turn_id=f"turn-timeline-{name}-{mutation}",
                        )
                        receipt = engine.archive_turn(
                            "agent-lumi",
                            "user-chen",
                            turn.source_turn_id,
                            idempotency_key=f"archive-timeline-{name}-{mutation}",
                        )
                        original = storage.get_recent_timeline_entries(
                            "agent-lumi",
                            "user-chen",
                        )[0]
                        storage.timeline_mutation = mutation

                        result = engine.recall_structured(
                            RecallRequest(
                                agent_id="agent-lumi",
                                user_id="user-chen",
                                query="arcade",
                                audience=RecallAudience.AGENT_PRIVATE,
                                options=RecallOptions(
                                    persona_delivery=PersonaDelivery.FULL,
                                ),
                            )
                        )

                        projection = next(
                            item
                            for item in result.memories
                            if item.source_id == original.timeline_entry_id
                        )
                        self.assertEqual(
                            projection.provenance,
                            RecallArtifactProvenance.PARTIAL_SOURCE,
                        )
                        self.assertNotIn(
                            receipt.archival_id,
                            {
                                item.source_id
                                for item in projection.source_references
                            },
                        )
                        engine.close()

    def test_legacy_manifest_without_fingerprint_cannot_certify_payload(self):
        with tempfile.TemporaryDirectory() as root:
            storage = _LegacyManifestFileStorage(root)
            engine = ERIIEngine(
                storage_driver=storage,
                config=ERIIConfig(async_archival=False),
                memory_extractor=_ArtifactExtractor(),
            )
            engine.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi is patient.",
            )
            turn = engine.record_turn(
                "agent-lumi",
                "user-chen",
                "Let us play one arcade game.",
                "Okay.",
                turn_id="turn-receipt-only",
            )
            receipt = engine.archive_turn(
                "agent-lumi",
                "user-chen",
                turn.source_turn_id,
                idempotency_key="archive-receipt-only",
            )

            result = engine.recall_structured(
                RecallRequest(
                    agent_id="agent-lumi",
                    user_id="user-chen",
                    query="arcade",
                    audience=RecallAudience.AGENT_PRIVATE,
                    options=RecallOptions(
                        persona_delivery=PersonaDelivery.FULL,
                    ),
                )
            )

            modern = [
                item
                for item in result.memories
                if item.source_kind
                in {"memory_node", "experiential_timeline"}
            ]
            self.assertEqual(len(modern), 2)
            for projection in modern:
                self.assertEqual(
                    projection.provenance,
                    RecallArtifactProvenance.PARTIAL_SOURCE,
                )
                self.assertNotIn(
                    receipt.archival_id,
                    {
                        item.source_id
                        for item in projection.source_references
                    },
                )
            engine.close()

    def test_compacted_tombstone_cannot_certify_real_or_forged_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            storage_factories = (
                ("file", lambda: FileStorage(os.path.join(root, "files"))),
                ("sqlite", lambda: SQLiteStorage(os.path.join(root, "memory.db"))),
            )
            for name, make_storage in storage_factories:
                with self.subTest(storage=name):
                    engine = ERIIEngine(
                        storage_driver=make_storage(),
                        config=ERIIConfig(
                            async_archival=False,
                            archival_receipt_retention_days=0,
                        ),
                        memory_extractor=_ArtifactExtractor(),
                    )
                    engine.initialize_relationship(
                        "agent-lumi",
                        "user-chen",
                        "Lumi is patient.",
                    )
                    turn = engine.record_turn(
                        "agent-lumi",
                        "user-chen",
                        "Let us play one arcade game.",
                        "Okay.",
                        turn_id=f"turn-compacted-{name}",
                    )
                    receipt = engine.archive_turn(
                        "agent-lumi",
                        "user-chen",
                        turn.source_turn_id,
                        idempotency_key=f"archive-compacted-{name}",
                    )
                    original = engine.storage.load_nodes(
                        "agent-lumi",
                        "user-chen",
                    )[0]
                    forged_data = original.to_dict()
                    forged_data.update(
                        {
                            "node_id": f"memory-forged-after-compaction-{name}",
                            "content": (
                                "A forged compacted arcade artifact."
                            ),
                        }
                    )
                    engine.storage.save_nodes(
                        "agent-lumi",
                        "user-chen",
                        [original, MemoryNode.from_dict(forged_data)],
                    )
                    self.assertEqual(engine.compact_archival_receipts(), 1)

                    result = engine.recall_structured(
                        RecallRequest(
                            agent_id="agent-lumi",
                            user_id="user-chen",
                            query="arcade",
                            audience=RecallAudience.AGENT_PRIVATE,
                            options=RecallOptions(
                                top_k=10,
                                max_per_type=10,
                                persona_delivery=PersonaDelivery.FULL,
                            ),
                        )
                    )

                    by_id = {item.source_id: item for item in result.memories}
                    for artifact_id in (
                        original.node_id,
                        forged_data["node_id"],
                    ):
                        projection = by_id[artifact_id]
                        self.assertEqual(
                            projection.provenance,
                            RecallArtifactProvenance.PARTIAL_SOURCE,
                        )
                        self.assertNotIn(
                            receipt.archival_id,
                            {
                                item.source_id
                                for item in projection.source_references
                            },
                        )
                    engine.close()

    def test_open_turn_cannot_supply_a_verified_source_revision(self):
        with tempfile.TemporaryDirectory() as root:
            engine = ERIIEngine(storage_dir=root)
            profile = engine.initialize_relationship(
                "agent-lumi",
                "user-chen",
                "Lumi is patient.",
            )
            turn = engine.begin_turn(
                "agent-lumi",
                "user-chen",
                "This source turn is still open.",
                turn_id="turn-open",
            )
            engine.storage.save_nodes(
                "agent-lumi",
                "user-chen",
                [
                    MemoryNode(
                        node_id="memory-open-turn",
                        agent_id="agent-lumi",
                        user_id="user-chen",
                        relationship_id=profile.relationship_id,
                        source_turn_id=turn.turn_id,
                        source_archival_id="archive-does-not-exist",
                        provenance_state=ArtifactProvenanceState.COMPLETE,
                        extractor_descriptor=ExtractorDescriptor(
                            extractor_id="tests.synthetic-provenance",
                            extractor_version="1",
                            extraction_schema_version="1",
                        ),
                        node_type=MemoryType.EVENT,
                        content="A memory that points to an open source turn.",
                    )
                ],
            )

            result = engine.recall_structured(
                RecallRequest(
                    agent_id="agent-lumi",
                    user_id="user-chen",
                    query="open source turn",
                    audience=RecallAudience.AGENT_PRIVATE,
                    options=RecallOptions(persona_delivery=PersonaDelivery.FULL),
                )
            )

            projection = next(
                item
                for item in result.memories
                if item.source_id == "memory-open-turn"
            )
            self.assertEqual(
                projection.provenance,
                RecallArtifactProvenance.PARTIAL_SOURCE,
            )
            self.assertEqual(projection.source_references, ())
            engine.close()

    def test_cross_relationship_source_chain_is_suppressed(self):
        with tempfile.TemporaryDirectory() as root:
            engine = ERIIEngine(
                storage_driver=FileStorage(root),
                config=ERIIConfig(
                    async_archival=False,
                ),
                memory_extractor=_ArtifactExtractor(),
            )
            engine.initialize_relationship(
                "agent-lumi",
                "user-alice",
                "Lumi is patient.",
            )
            engine.initialize_relationship(
                "agent-lumi",
                "user-bob",
                "Lumi is patient.",
            )
            turn = engine.record_turn(
                "agent-lumi",
                "user-bob",
                "Let us play one arcade game.",
                "Okay.",
                turn_id="turn-bob",
            )
            engine.archive_turn(
                "agent-lumi",
                "user-bob",
                turn.source_turn_id,
                idempotency_key="archive-bob",
            )
            bob_node = engine.storage.load_nodes("agent-lumi", "user-bob")[0]
            cross_scope_data = bob_node.to_dict()
            cross_scope_data.update(
                {
                    "node_id": "memory-cross-relationship",
                    "user_id": "user-alice",
                    "content": "A cross relationship arcade memory.",
                }
            )
            engine.storage.save_nodes(
                "agent-lumi",
                "user-alice",
                [MemoryNode.from_dict(cross_scope_data)],
            )

            result = engine.recall_structured(
                RecallRequest(
                    agent_id="agent-lumi",
                    user_id="user-alice",
                    query="cross relationship arcade memory",
                    audience=RecallAudience.AGENT_PRIVATE,
                    options=RecallOptions(persona_delivery=PersonaDelivery.FULL),
                )
            )

            projection = next(
                item
                for item in result.memories
                if item.source_id == "memory-cross-relationship"
            )
            self.assertEqual(
                projection.provenance,
                RecallArtifactProvenance.PARTIAL_SOURCE,
            )
            self.assertEqual(projection.source_references, ())
            engine.close()


if __name__ == "__main__":
    unittest.main()
