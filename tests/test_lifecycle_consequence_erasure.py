"""Lifecycle closure contracts for relationship consequences and tensions."""

from pathlib import Path
import tempfile
import unittest

from erii.core.consequence import NarrativeTensionProjector
from erii.engine import ERIIEngine
from erii.lifecycle_erasure import (
    ErasureScope,
    ErasureSelector,
    ErasureStorageKind,
    erase_staged_storage,
    rebuild_staged_storage,
)
from erii.models.adjudication import DecisionOutcome
from erii.models.continuity import (
    ContinuityAxis,
    ContinuityEvaluatorDescriptor,
)
from erii.models.continuity_evidence import (
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)
from erii.models.consequence import (
    NarrativeTensionOutcome,
    RelationshipConsequenceKind,
)
from erii.storage.file_storage import FileStorage
from erii.storage.sqlite_storage import SQLiteStorage


class _AlignedEvaluator:
    descriptor = ContinuityEvaluatorDescriptor(
        evaluator_id="tests.lifecycle-consequence-erasure",
        evaluator_version="1",
        evaluation_schema_version="1",
    )

    def evaluate(self, request):
        return {
            "kind": "findings",
            "findings": [
                {
                    "finding_id": f"aligned-{axis.value}",
                    "axis": axis.value,
                    "assessment": "aligned",
                    "severity": "info",
                    "reason_code": "aligned",
                    "reply_start": 0,
                    "reply_end": len(request.proposed_reply),
                    "reply_quote": request.proposed_reply,
                    "supporting_basis_refs": [
                        request.persona_context_refs[0].ref_id
                    ],
                    "conflicting_source_refs": [],
                }
                for axis in ContinuityAxis
            ],
        }


def _persona_candidate():
    return {
        "schema_version": "0.4.0a7",
        "compiler_version": "tests.lifecycle-consequence-erasure/1",
        "source_spans": [
            {
                "span_id": "span-boundary",
                "start": 0,
                "end": 19,
                "quote": "Keeps her boundary.",
            }
        ],
        "claims": [
            {
                "claim_id": "boundary-claim",
                "kind": "value",
                "statement": "She keeps a clearly stated boundary.",
                "activation_tier": "situational",
                "basis": "explicit",
                "scope": "character",
                "source_span_ids": ["span-boundary"],
            }
        ],
    }


def _complete_supported_turn(engine, turn_id):
    turn = engine.begin_turn(
        "agent-consequence",
        "user-consequence",
        f"User source for {turn_id}",
        turn_id=turn_id,
    )
    manifest = engine.get_persona_manifest(
        "agent-consequence",
        "user-consequence",
    )
    persona_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.PERSONA_CLAIM,
        {
            "manifest_id": manifest.manifest_id,
            "content_fingerprint": manifest.content_fingerprint,
            "claim_id": "boundary-claim",
        },
    )
    agent_message = f"Agent source for {turn_id}"
    continuity = engine.evaluate_reply_continuity(
        "agent-consequence",
        "user-consequence",
        turn.turn_id,
        agent_message,
        persona_context_refs=(persona_ref,),
    )
    engine.complete_turn(
        "agent-consequence",
        "user-consequence",
        turn.turn_id,
        agent_message,
        continuity_result=continuity,
    )
    return engine.get_turn(
        "agent-consequence",
        "user-consequence",
        turn.turn_id,
    )


def _adjudicate_event(engine, turn, *, references=()):
    message = turn.transcript.agent_message
    result = engine.adjudicate_turn_candidates(
        "agent-consequence",
        "user-consequence",
        turn.turn_id,
        [
            {
                "candidate_key": turn.turn_id,
                "event_type": "conflict",
                "summary": f"Accepted event for {turn.turn_id}",
                "signal": {
                    "signal_type": "conflict",
                    "strength": "strong",
                    "extraction_confidence": 0.99,
                    "interpretation_confidence": 0.99,
                },
                "evidence": [
                    {
                        "source_id": message.message_id,
                        "source_revision": turn.source_revision,
                        "quote": message.content,
                        "start": 0,
                        "end": len(message.content),
                    }
                ],
                "references": list(references),
            }
        ],
        extractor_version="tests.lifecycle-consequence-erasure/1",
    )
    if result.receipts[0].outcome != DecisionOutcome.ACCEPTED:
        raise AssertionError("lifecycle fixture event was not accepted")
    return result.records[0]


class ConsequenceErasureContract(unittest.TestCase):
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
        with ERIIEngine(
            storage_driver=storage,
            continuity_evaluator=_AlignedEvaluator(),
        ) as engine:
            profile = engine.initialize_relationship(
                "agent-consequence",
                "user-consequence",
                "Keeps her boundary.",
            )
            proposal = engine.propose_persona_compilation(
                profile.agent_id,
                profile.user_id,
                _persona_candidate(),
            )
            engine.decide_persona_compilation(
                profile.agent_id,
                profile.user_id,
                proposal.proposal_id,
                proposal.revision,
                "owner",
                "approve",
            )
            root_turn = _complete_supported_turn(engine, "turn-root")
            root_record = _adjudicate_event(engine, root_turn)
            consequence = engine.record_relationship_consequence(
                profile.agent_id,
                profile.user_id,
                root_turn.turn_id,
                root_record.receipt.decision_id,
                root_record.events[0].event_id,
                (RelationshipConsequenceKind.HARM,),
                "The accepted choice caused harm.",
                recorded_at="2026-08-06T08:01:00+08:00",
            )
            link_turn = _complete_supported_turn(engine, "turn-link")
            link_record = _adjudicate_event(
                engine,
                link_turn,
                references=(root_record.events[0].event_id,),
            )
            link = engine.record_narrative_tension_link(
                profile.agent_id,
                profile.user_id,
                consequence.consequence_id,
                link_turn.turn_id,
                link_record.receipt.decision_id,
                link_record.events[0].event_id,
                NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
                "The later accepted event addressed the harm.",
                recorded_at="2026-08-06T08:02:00+08:00",
            )
        return profile, root_record, link_record, consequence, link

    @staticmethod
    def _selector(profile, scope, **identity):
        return ErasureSelector(
            scope=scope,
            agent_id=profile.agent_id,
            user_id=profile.user_id,
            relationship_id=profile.relationship_id,
            **identity,
        )

    def test_rebuild_proof_binds_consequence_and_tension_projection(self):
        for kind in ErasureStorageKind:
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root:
                _, path, storage_factory = next(
                    item for item in self._cases(root) if item[0] is kind
                )
                profile, _, _, _, _ = self._seed(storage_factory, path)

                result = rebuild_staged_storage(
                    path,
                    kind,
                    self._selector(profile, ErasureScope.RELATIONSHIP),
                )

                proof = result.rebuild_proofs[0]
                self.assertEqual(proof.consequence_count, 1)
                self.assertEqual(proof.tension_link_count, 1)
                self.assertEqual(proof.tension_count, 1)
                self.assertEqual(len(proof.tension_digest), 64)
                self.assertEqual(
                    result.inventory.counts["rebuilt"]["narrative_tension"],
                    1,
                )

    def test_later_event_erasure_removes_link_but_preserves_root_consequence(self):
        for kind in ErasureStorageKind:
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root:
                _, path, storage_factory = next(
                    item for item in self._cases(root) if item[0] is kind
                )
                profile, _, link_record, consequence, _ = self._seed(
                    storage_factory,
                    path,
                )

                result = erase_staged_storage(
                    path,
                    kind,
                    self._selector(
                        profile,
                        ErasureScope.RELATIONSHIP_EVENT,
                        relationship_event_id=link_record.events[0].event_id,
                    ),
                )

                reopened = storage_factory(path)
                self.assertEqual(
                    reopened.list_relationship_consequences(profile.relationship_id),
                    [consequence],
                )
                self.assertEqual(
                    reopened.list_narrative_tension_links(profile.relationship_id),
                    [],
                )
                projection = NarrativeTensionProjector.project(
                    reopened.list_relationship_consequences(profile.relationship_id),
                    reopened.list_narrative_tension_links(profile.relationship_id),
                )
                self.assertEqual(
                    projection[0].outcome,
                    NarrativeTensionOutcome.UNADDRESSED,
                )
                self.assertEqual(
                    result.inventory.counts["deleted"]["narrative_tension_link"],
                    1,
                )
                self.assertNotIn(
                    "relationship_consequence",
                    result.inventory.counts["deleted"],
                )
                self.assertEqual(result.rebuild_proofs[0].consequence_count, 1)
                self.assertEqual(result.rebuild_proofs[0].tension_link_count, 0)

    def test_initiating_event_erasure_removes_consequence_and_all_links(self):
        for kind in ErasureStorageKind:
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root:
                _, path, storage_factory = next(
                    item for item in self._cases(root) if item[0] is kind
                )
                profile, root_record, _, _, _ = self._seed(storage_factory, path)

                result = erase_staged_storage(
                    path,
                    kind,
                    self._selector(
                        profile,
                        ErasureScope.RELATIONSHIP_EVENT,
                        relationship_event_id=root_record.events[0].event_id,
                    ),
                )

                reopened = storage_factory(path)
                self.assertEqual(
                    reopened.list_relationship_consequences(profile.relationship_id),
                    [],
                )
                self.assertEqual(
                    reopened.list_narrative_tension_links(profile.relationship_id),
                    [],
                )
                self.assertEqual(
                    result.inventory.counts["deleted"]["relationship_consequence"],
                    1,
                )
                self.assertEqual(
                    result.inventory.counts["deleted"]["narrative_tension_link"],
                    1,
                )
                self.assertEqual(result.rebuild_proofs[0].tension_count, 0)

    def test_initiating_turn_erasure_removes_consequence_and_all_links(self):
        for kind in ErasureStorageKind:
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root:
                _, path, storage_factory = next(
                    item for item in self._cases(root) if item[0] is kind
                )
                profile, _, _, _, _ = self._seed(storage_factory, path)

                result = erase_staged_storage(
                    path,
                    kind,
                    self._selector(
                        profile,
                        ErasureScope.SOURCE_TURN,
                        source_turn_id="turn-root",
                    ),
                )

                reopened = storage_factory(path)
                self.assertEqual(
                    reopened.list_relationship_consequences(profile.relationship_id),
                    [],
                )
                self.assertEqual(
                    reopened.list_narrative_tension_links(profile.relationship_id),
                    [],
                )
                self.assertEqual(
                    result.inventory.counts["deleted"]["relationship_consequence"],
                    1,
                )
                self.assertEqual(
                    result.inventory.counts["deleted"]["narrative_tension_link"],
                    1,
                )
                self.assertEqual(result.rebuild_proofs[0].tension_count, 0)

    def test_relationship_erasure_removes_derived_journals_before_sources(self):
        for kind in ErasureStorageKind:
            with self.subTest(kind=kind.value), tempfile.TemporaryDirectory() as root:
                _, path, storage_factory = next(
                    item for item in self._cases(root) if item[0] is kind
                )
                profile, _, _, _, _ = self._seed(storage_factory, path)

                result = erase_staged_storage(
                    path,
                    kind,
                    self._selector(profile, ErasureScope.RELATIONSHIP),
                )

                reopened = storage_factory(path)
                self.assertIsNone(
                    reopened.get_relationship(profile.agent_id, profile.user_id)
                )
                self.assertEqual(
                    reopened.list_relationship_consequences(profile.relationship_id),
                    [],
                )
                self.assertEqual(
                    reopened.list_narrative_tension_links(profile.relationship_id),
                    [],
                )
                self.assertEqual(
                    result.inventory.counts["deleted"]["relationship_consequence"],
                    1,
                )
                self.assertEqual(
                    result.inventory.counts["deleted"]["narrative_tension_link"],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
