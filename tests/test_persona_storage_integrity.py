"""Regression contracts for Persona approval and portable source integrity."""

from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from erii import ERIIEngine, MemoryPack, SQLiteStorage
from erii.core.persona_compilation import PersonaCompiler
from erii.models.persona import (
    PersonaCompilationConflictError,
    PersonaCompilationStatus,
)
from erii.models.relationship import CharacterBlueprint


SOURCE = "Lumi"


def candidate(statement="Lumi is herself."):
    return {
        "compiler_version": "integrity-v1",
        "source_spans": [
            {
                "span_id": "span-lumi",
                "start": 0,
                "end": len(SOURCE),
                "quote": SOURCE,
            }
        ],
        "claims": [
            {
                "claim_id": "claim-identity",
                "kind": "identity",
                "statement": statement,
                "activation_tier": "foundation",
                "basis": "explicit",
                "source_span_ids": ["span-lumi"],
            }
        ],
    }


class PersonaApprovalAtomicityTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _engine(self, backend):
        if backend == "file":
            return ERIIEngine(storage_dir=f"{self.root}/file")
        return ERIIEngine(
            storage_driver=SQLiteStorage(f"{self.root}/sqlite/memory.db")
        )

    def test_retry_is_idempotent_and_different_binding_fails_before_approval(self):
        for backend in ("file", "sqlite"):
            with self.subTest(backend=backend):
                engine = self._engine(backend)
                profile = engine.initialize_relationship("lumi", "chen", SOURCE)
                first = engine.propose_persona_compilation(
                    "lumi", "chen", candidate(), proposal_id="proposal-first"
                )
                first_manifest = engine.decide_persona_compilation(
                    "lumi", "chen", first.proposal_id, 1, "owner", "approve"
                )

                repeated = engine.decide_persona_compilation(
                    "lumi", "chen", first.proposal_id, 1, "owner", "approve"
                )
                self.assertEqual(repeated.manifest_id, first_manifest.manifest_id)

                second = engine.propose_persona_compilation(
                    "lumi",
                    "chen",
                    candidate("Lumi remains herself."),
                    proposal_id="proposal-second",
                )
                with self.assertRaisesRegex(
                    PersonaCompilationConflictError,
                    "already pinned",
                ):
                    engine.decide_persona_compilation(
                        "lumi", "chen", second.proposal_id, 1, "owner", "approve"
                    )

                proposals = {
                    item.proposal_id: item
                    for item in engine.list_persona_compilation_proposals(
                        "lumi", "chen"
                    )
                }
                stored_profile = engine.storage.get_relationship("lumi", "chen")
                manifests = engine.storage.list_persona_manifests(
                    profile.blueprint.blueprint_id
                )
                self.assertEqual(
                    proposals[second.proposal_id].status,
                    PersonaCompilationStatus.PENDING,
                )
                self.assertEqual(len(manifests), 1)
                self.assertEqual(stored_profile.manifest_id, first_manifest.manifest_id)
                engine.close()

    def test_file_approval_rolls_a_transient_second_file_failure_forward(self):
        engine = self._engine("file")
        profile = engine.initialize_relationship("lumi", "chen", SOURCE)
        proposal = engine.propose_persona_compilation(
            "lumi", "chen", candidate(), proposal_id="proposal-recoverable"
        )
        original_write = engine.storage._write_json_atomic
        call_count = 0

        def flaky_write(path, data):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise OSError("simulated profile write interruption")
            return original_write(path, data)

        with patch.object(engine.storage, "_write_json_atomic", side_effect=flaky_write):
            manifest = engine.decide_persona_compilation(
                "lumi", "chen", proposal.proposal_id, 1, "owner", "approve"
            )

        stored = engine.storage.get_relationship("lumi", "chen")
        journals = engine.storage._get_persona_approval_journal_dir()
        self.assertEqual(stored.manifest_id, manifest.manifest_id)
        self.assertEqual(
            engine.list_persona_compilation_proposals("lumi", "chen")[0].status,
            PersonaCompilationStatus.APPROVED,
        )
        self.assertEqual(list(Path(journals).glob("*.json")), [])
        self.assertEqual(stored.relationship_id, profile.relationship_id)
        engine.close()


class PersonaMemoryPackIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _assert_rejected_by_backends(self, pack, message):
        for backend in ("file", "sqlite"):
            with self.subTest(backend=backend):
                storage = (
                    None
                    if backend == "file"
                    else SQLiteStorage(f"{self.root}/{message}-{backend}.db")
                )
                engine = ERIIEngine(
                    storage_dir=f"{self.root}/{message}-{backend}",
                    storage_driver=storage,
                )
                with self.assertRaises(ValueError):
                    engine.import_memory(pack, agent_id="lumi", user_id=backend)
                profile = engine.storage.get_relationship("lumi", backend)
                self.assertIsNotNone(profile)
                self.assertIsNone(profile.manifest_id)
                self.assertEqual(
                    engine.storage.list_persona_compilation_proposals(
                        profile.blueprint.blueprint_id
                    ),
                    [],
                )
                engine.close()

    def test_foreign_blueprint_compilation_cannot_be_laundered_by_remapping(self):
        source_engine = ERIIEngine(storage_dir=f"{self.root}/source-foreign")
        relationship = source_engine.initialize_relationship("lumi", "source", SOURCE)
        foreign_blueprint = CharacterBlueprint(
            blueprint_id="foreign-blueprint",
            source_text="EVIL",
        )
        foreign_candidate = {
            "source_spans": [
                {"span_id": "foreign", "start": 0, "end": 4, "quote": "EVIL"}
            ],
            "claims": [
                {
                    "claim_id": "foreign-claim",
                    "kind": "identity",
                    "statement": "Foreign claim.",
                    "activation_tier": "foundation",
                    "basis": "explicit",
                    "source_span_ids": ["foreign"],
                }
            ],
        }
        pending = PersonaCompiler.propose(
            foreign_blueprint,
            foreign_candidate,
            proposal_id="foreign-proposal",
        )
        approved = PersonaCompiler.decide(
            pending,
            revision=1,
            actor_id="owner",
            decision="approve",
        )
        manifest = PersonaCompiler.manifest_from_approved(approved)
        pack = MemoryPack(
            agent_id="lumi",
            user_id="source",
            relationship=replace(relationship, manifest_id=manifest.manifest_id),
            persona_compilation_proposals=[approved],
            persona_manifests=[manifest],
        )

        self._assert_rejected_by_backends(pack, "foreign")
        source_engine.close()

    def test_matching_blueprint_ids_do_not_bypass_exact_span_revalidation(self):
        source_engine = ERIIEngine(storage_dir=f"{self.root}/source-forged")
        relationship = source_engine.initialize_relationship("lumi", "source", SOURCE)
        foreign_blueprint = CharacterBlueprint(
            blueprint_id="temporary-foreign",
            source_text="EVIL",
        )
        foreign_pending = PersonaCompiler.propose(
            foreign_blueprint,
            {
                "source_spans": [
                    {"span_id": "forged", "start": 0, "end": 4, "quote": "EVIL"}
                ],
                "claims": [
                    {
                        "claim_id": "forged-claim",
                        "kind": "identity",
                        "statement": "Forged claim.",
                        "activation_tier": "foundation",
                        "basis": "explicit",
                        "source_span_ids": ["forged"],
                    }
                ],
            },
            proposal_id="forged-proposal",
        )
        forged_fingerprint = PersonaCompiler.content_fingerprint(
            relationship.blueprint.blueprint_id,
            relationship.blueprint.revision,
            relationship.blueprint.source_sha256,
            foreign_pending.candidate,
        )
        forged_pending = replace(
            foreign_pending,
            blueprint_id=relationship.blueprint.blueprint_id,
            blueprint_revision=relationship.blueprint.revision,
            source_sha256=relationship.blueprint.source_sha256,
            content_fingerprint=forged_fingerprint,
        )
        forged_approved = PersonaCompiler.decide(
            forged_pending,
            revision=1,
            actor_id="owner",
            decision="approve",
        )
        forged_manifest = PersonaCompiler.manifest_from_approved(forged_approved)
        pack = MemoryPack(
            agent_id="lumi",
            user_id="source",
            relationship=replace(
                relationship,
                manifest_id=forged_manifest.manifest_id,
            ),
            persona_compilation_proposals=[forged_approved],
            persona_manifests=[forged_manifest],
        )

        self._assert_rejected_by_backends(pack, "forged")
        source_engine.close()


if __name__ == "__main__":
    unittest.main()
