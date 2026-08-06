import pytest

from erii.core.consequence import RelationshipConsequenceCoordinator
from erii.engine import ERIIEngine
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
from erii.models.recall import RecallAudience, RecallRequest
from erii.storage.file_storage import FileStorage
from erii.storage.sqlite_storage import SQLiteStorage


AGENT_ID = "agent-consequence"
USER_ID = "user-consequence"


class _AlignedEvaluator:
    descriptor = ContinuityEvaluatorDescriptor(
        evaluator_id="tests.consequence-continuity",
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
        "compiler_version": "tests.consequence-persona/1",
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


def _storage(tmp_path, kind):
    if kind == "file":
        return FileStorage(str(tmp_path / "file"))
    return SQLiteStorage(str(tmp_path / "consequence.db"))


def _initialize(engine):
    engine.initialize_relationship(
        AGENT_ID,
        USER_ID,
        "Keeps her boundary.",
    )
    proposal = engine.propose_persona_compilation(
        AGENT_ID,
        USER_ID,
        _persona_candidate(),
    )
    engine.decide_persona_compilation(
        AGENT_ID,
        USER_ID,
        proposal.proposal_id,
        proposal.revision,
        "owner",
        "approve",
    )


def _complete_supported_turn(engine, turn_id, user_message, agent_message):
    turn = engine.begin_turn(
        AGENT_ID,
        USER_ID,
        user_message,
        turn_id=turn_id,
    )
    manifest = engine.get_persona_manifest(AGENT_ID, USER_ID)
    persona_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.PERSONA_CLAIM,
        {
            "manifest_id": manifest.manifest_id,
            "content_fingerprint": manifest.content_fingerprint,
            "claim_id": "boundary-claim",
        },
    )
    result = engine.evaluate_reply_continuity(
        AGENT_ID,
        USER_ID,
        turn.turn_id,
        agent_message,
        persona_context_refs=(persona_ref,),
    )
    engine.complete_turn(
        AGENT_ID,
        USER_ID,
        turn.turn_id,
        agent_message,
        continuity_result=result,
    )
    return engine.get_turn(AGENT_ID, USER_ID, turn.turn_id)


def _adjudicate_event(
    engine,
    turn,
    *,
    candidate_key,
    references=(),
    include_user=False,
    include_agent=True,
):
    evidence = []
    if include_user:
        message = turn.transcript.user_message
        evidence.append(
            {
                "source_id": message.message_id,
                "source_revision": turn.source_revision,
                "quote": message.content,
                "start": 0,
                "end": len(message.content),
            }
        )
    if include_agent:
        message = turn.transcript.agent_message
        evidence.append(
            {
                "source_id": message.message_id,
                "source_revision": turn.source_revision,
                "quote": message.content,
                "start": 0,
                "end": len(message.content),
            }
        )
    result = engine.adjudicate_turn_candidates(
        AGENT_ID,
        USER_ID,
        turn.turn_id,
        [
            {
                "candidate_key": candidate_key,
                "event_type": "conflict",
                "summary": f"Accepted relationship event {candidate_key}.",
                "signal": {
                    "signal_type": "conflict",
                    "strength": "strong",
                    "extraction_confidence": 0.99,
                    "interpretation_confidence": 0.99,
                },
                "evidence": evidence,
                "references": list(references),
            }
        ],
        extractor_version="tests.consequence-extractor/1",
    )
    assert result.receipts[0].outcome == DecisionOutcome.ACCEPTED
    return result.records[0]


@pytest.mark.parametrize("storage_kind", ("file", "sqlite"))
def test_engine_records_and_projects_source_bound_consequence(
    tmp_path,
    storage_kind,
):
    with ERIIEngine(
        storage_driver=_storage(tmp_path, storage_kind),
        continuity_evaluator=_AlignedEvaluator(),
    ) as engine:
        _initialize(engine)
        initiating_turn = _complete_supported_turn(
            engine,
            "turn-initiating",
            "Please change your answer.",
            "No. I am keeping that boundary.",
        )
        initiating = _adjudicate_event(
            engine,
            initiating_turn,
            candidate_key="initiating-boundary",
            include_agent=True,
        )

        consequence = engine.record_relationship_consequence(
            AGENT_ID,
            USER_ID,
            initiating_turn.turn_id,
            initiating.receipt.decision_id,
            initiating.events[0].event_id,
            (
                RelationshipConsequenceKind.HARM,
                RelationshipConsequenceKind.BOUNDARY_EXPRESSION,
            ),
            "The refusal hurt the User while maintaining a boundary.",
            recorded_at="2026-08-06T10:00:00+00:00",
        )
        replay = engine.record_relationship_consequence(
            AGENT_ID,
            USER_ID,
            initiating_turn.turn_id,
            initiating.receipt.decision_id,
            initiating.events[0].event_id,
            (
                RelationshipConsequenceKind.BOUNDARY_EXPRESSION,
                RelationshipConsequenceKind.HARM,
            ),
            "The refusal hurt the User while maintaining a boundary.",
            recorded_at="2030-01-01T00:00:00+00:00",
        )

        assert replay == consequence
        assert consequence.consequence_id == (
            RelationshipConsequenceCoordinator.consequence_id(
                consequence.relationship_id,
                initiating.receipt.decision_id,
                initiating.events[0].event_id,
            )
        )
        assert engine.list_relationship_consequences(AGENT_ID, USER_ID) == [
            consequence
        ]
        projected = engine.list_narrative_tensions(AGENT_ID, USER_ID)
        assert len(projected) == 1
        assert projected[0].outcome == NarrativeTensionOutcome.UNADDRESSED
        assert projected[0].source_message_id == (
            initiating_turn.transcript.agent_message.message_id
        )
        recalled = engine.recall_structured(
            RecallRequest(
                agent_id=AGENT_ID,
                user_id=USER_ID,
                query="What remains unresolved?",
                audience=RecallAudience.AGENT_PRIVATE,
            )
        )
        assert tuple(
            item.tension_id for item in recalled.narrative_tensions
        ) == (consequence.tension_id,)
        assert "unaddressed" in engine.render_recall(recalled)
        public_recall = engine.recall_structured(
            RecallRequest(
                agent_id=AGENT_ID,
                user_id=USER_ID,
                query="What remains unresolved?",
                audience=RecallAudience.PUBLIC,
            )
        )
        assert public_recall.narrative_tensions == ()

        exported = engine.export_memory(AGENT_ID, USER_ID)
        assert exported.relationship_consequences == [consequence]
        assert exported.narrative_tension_links == []

    target_path = tmp_path / f"restored-{storage_kind}.db"
    with ERIIEngine(
        storage_driver=SQLiteStorage(str(target_path)),
        continuity_evaluator=_AlignedEvaluator(),
    ) as restored:
        restored.import_memory(exported)
        assert restored.list_relationship_consequences(
            AGENT_ID,
            USER_ID,
        ) == [consequence]
        assert (
            restored.list_narrative_tensions(AGENT_ID, USER_ID)[0].to_dict()
            == projected[0].to_dict()
        )


def test_consequence_requires_exact_final_agent_message_evidence(tmp_path):
    with ERIIEngine(
        storage_driver=FileStorage(str(tmp_path / "file")),
        continuity_evaluator=_AlignedEvaluator(),
    ) as engine:
        _initialize(engine)
        turn = _complete_supported_turn(
            engine,
            "turn-user-only",
            "That answer hurt.",
            "I hear that.",
        )
        user_only = _adjudicate_event(
            engine,
            turn,
            candidate_key="user-only",
            include_user=True,
            include_agent=False,
        )

        with pytest.raises(ValueError, match="exact final Agent message evidence"):
            engine.record_relationship_consequence(
                AGENT_ID,
                USER_ID,
                turn.turn_id,
                user_only.receipt.decision_id,
                user_only.events[0].event_id,
                (RelationshipConsequenceKind.HARM,),
                "The User reported harm.",
            )


def test_tension_links_require_direct_reference_and_bilateral_reconciliation(
    tmp_path,
):
    with ERIIEngine(
        storage_driver=FileStorage(str(tmp_path / "file")),
        continuity_evaluator=_AlignedEvaluator(),
    ) as engine:
        _initialize(engine)
        initiating_turn = _complete_supported_turn(
            engine,
            "turn-source",
            "Please reconsider.",
            "No. The boundary remains.",
        )
        initiating = _adjudicate_event(
            engine,
            initiating_turn,
            candidate_key="source",
        )
        consequence = engine.record_relationship_consequence(
            AGENT_ID,
            USER_ID,
            initiating_turn.turn_id,
            initiating.receipt.decision_id,
            initiating.events[0].event_id,
            (RelationshipConsequenceKind.BOUNDARY_EXPRESSION,),
            "The boundary created an unresolved tension.",
        )

        unrelated_turn = _complete_supported_turn(
            engine,
            "turn-unrelated",
            "Let us talk.",
            "We can talk without changing the boundary.",
        )
        unrelated = _adjudicate_event(
            engine,
            unrelated_turn,
            candidate_key="unrelated",
        )
        with pytest.raises(ValueError, match="directly reference"):
            engine.record_narrative_tension_link(
                AGENT_ID,
                USER_ID,
                consequence.consequence_id,
                unrelated_turn.turn_id,
                unrelated.receipt.decision_id,
                unrelated.events[0].event_id,
                NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
                "An unrelated exchange cannot update the tension.",
            )

        agent_only_turn = _complete_supported_turn(
            engine,
            "turn-agent-only",
            "Can this be repaired?",
            "I want to repair the harm while keeping the boundary.",
        )
        agent_only = _adjudicate_event(
            engine,
            agent_only_turn,
            candidate_key="agent-only-repair",
            references=(initiating.events[0].event_id,),
        )
        with pytest.raises(ValueError, match="both User and Agent evidence"):
            engine.record_narrative_tension_link(
                AGENT_ID,
                USER_ID,
                consequence.consequence_id,
                agent_only_turn.turn_id,
                agent_only.receipt.decision_id,
                agent_only.events[0].event_id,
                NarrativeTensionOutcome.MUTUALLY_RECONCILED,
                "One-sided repair is not mutual reconciliation.",
            )

        mutual_turn = _complete_supported_turn(
            engine,
            "turn-mutual",
            "I accept the repair and the boundary.",
            "I accept that understanding too.",
        )
        mutual = _adjudicate_event(
            engine,
            mutual_turn,
            candidate_key="mutual-repair",
            references=(initiating.events[0].event_id,),
            include_user=True,
        )
        link = engine.record_narrative_tension_link(
            AGENT_ID,
            USER_ID,
            consequence.consequence_id,
            mutual_turn.turn_id,
            mutual.receipt.decision_id,
            mutual.events[0].event_id,
            NarrativeTensionOutcome.MUTUALLY_RECONCILED,
            "Both parties explicitly accepted the repair.",
        )

        assert engine.list_narrative_tension_links(AGENT_ID, USER_ID) == [link]
        projection = engine.list_narrative_tensions(AGENT_ID, USER_ID)[0]
        assert projection.outcome == NarrativeTensionOutcome.MUTUALLY_RECONCILED
        assert projection.link_ids == (link.link_id,)
