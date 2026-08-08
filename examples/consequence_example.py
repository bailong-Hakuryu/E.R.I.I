"""Relationship Consequence and Narrative Tension Example.

This example demonstrates how to:
1. Record a relationship consequence from an adjudicated event
2. Record narrative tension links to track outcome changes
3. Query consequences and project narrative tensions
4. Use consequence data in Recall for agent-private context

Requires: Python 3.11+, basic E.R.I.I. installation
"""

import tempfile

from erii.engine import ERIIEngine
from erii.models.continuity import ContinuityAxis, ContinuityEvaluatorDescriptor
from erii.models.continuity_evidence import ContinuityEvidenceKind, ContinuityEvidenceRef
from erii.models.consequence import (
    NarrativeTensionOutcome,
    RelationshipConsequenceKind,
)
from erii.models.recall import (
    RecallAudience,
    RecallBudget,
    RecallOptions,
    RecallRequest,
)


PERSONA_SOURCE = "Kai admits difficult truths and accepts their consequences."


class _AlignedEvaluator:
    """Deterministic local evaluator used only by this runnable example."""

    descriptor = ContinuityEvaluatorDescriptor(
        evaluator_id="example.consequence-continuity",
        evaluator_version="1",
        evaluation_schema_version="1",
    )

    def __init__(self):
        self._axes = tuple(ContinuityAxis)

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
                    "supporting_basis_refs": (
                        [request.persona_context_refs[0].ref_id]
                        if request.persona_context_refs
                        else []
                    ),
                    "conflicting_source_refs": [],
                }
                for axis in self._axes
            ],
        }


def main():
    """Run the consequence tracking example."""
    # Create a temporary storage directory
    with tempfile.TemporaryDirectory() as storage_dir:
        # Initialize engine with continuity evaluator
        engine = ERIIEngine(
            storage_dir=storage_dir,
            continuity_evaluator=_AlignedEvaluator(),
        )

        try:
            # Step 1: Initialize a relationship with persona
            print("=== Step 1: Initialize Relationship ===")
            profile = engine.initialize_relationship(
                agent_id="agent-kai",
                user_id="user-mira",
                persona_source=PERSONA_SOURCE,
            )
            print(f"Relationship initialized: {profile.relationship_id}\n")

            # Step 2: Create and approve a persona manifest
            print("=== Step 2: Create Persona Manifest ===")
            persona_candidate = {
                "schema_version": "0.4.0a7",
                "compiler_version": "example.consequence/1.0",
                "source_spans": [
                    {
                        "span_id": "span-trust",
                        "start": 0,
                        "end": len(PERSONA_SOURCE),
                        "quote": PERSONA_SOURCE,
                    }
                ],
                "claims": [
                    {
                        "claim_id": "trust-claim",
                        "kind": "value",
                        "statement": (
                            "Kai admits difficult truths and accepts their consequences."
                        ),
                        "activation_tier": "situational",
                        "basis": "explicit",
                        "scope": "character",
                        "source_span_ids": ["span-trust"],
                    }
                ],
            }
            proposal = engine.propose_persona_compilation(
                "agent-kai",
                "user-mira",
                persona_candidate,
            )
            engine.decide_persona_compilation(
                "agent-kai",
                "user-mira",
                proposal.proposal_id,
                proposal.revision,
                "owner",
                "approve",
            )
            manifest = engine.get_persona_manifest("agent-kai", "user-mira")
            print(f"Persona manifest created: {manifest.manifest_id}\n")

            # Step 3: Begin a turn with full continuity review
            print("=== Step 3: Record Source Turn with Continuity Review ===")
            user_message = "Did you tell anyone about my secret?"
            agent_message = "I... I had to tell someone. I'm sorry."

            turn_open = engine.begin_turn(
                agent_id="agent-kai",
                user_id="user-mira",
                user_message=user_message,
                turn_id="turn-boundary-test",
            )

            # Create persona context reference for continuity evaluation
            persona_ref = ContinuityEvidenceRef.create(
                ContinuityEvidenceKind.PERSONA_CLAIM,
                {
                    "manifest_id": manifest.manifest_id,
                    "content_fingerprint": manifest.content_fingerprint,
                    "claim_id": "trust-claim",
                },
            )

            # Evaluate reply continuity before showing
            continuity_result = engine.evaluate_reply_continuity(
                "agent-kai",
                "user-mira",
                turn_open.turn_id,
                agent_message,
                persona_context_refs=(persona_ref,),
            )

            # Complete turn with continuity result
            turn_receipt = engine.complete_turn(
                "agent-kai",
                "user-mira",
                turn_open.turn_id,
                agent_message,
                continuity_result=continuity_result,
            )
            turn = engine.get_turn(
                "agent-kai",
                "user-mira",
                turn_receipt.source_turn_id,
            )
            print(f"Turn recorded: {turn.turn_id}")
            print(f"Continuity status: {turn.continuity_assessment.status.value}\n")

            # Step 4: Adjudicate a relationship event from the turn
            print("=== Step 4: Adjudicate Relationship Event ===")
            candidates = [
                {
                    "candidate_key": "boundary-violation",
                    "event_type": "conflict",
                    "summary": "Kai broke Mira's trust by sharing her secret.",
                    "signal": {
                        "signal_type": "conflict",
                        "strength": "strong",
                        "extraction_confidence": 0.95,
                        "interpretation_confidence": 0.90,
                    },
                    "evidence": [
                        {
                            "source_id": turn.transcript.agent_message.message_id,
                            "source_revision": turn.source_revision,
                            "quote": agent_message,
                            "start": 0,
                            "end": len(agent_message),
                        }
                    ],
                }
            ]

            adjudication_result = engine.adjudicate_turn_candidates(
                agent_id="agent-kai",
                user_id="user-mira",
                source_turn_id=turn.turn_id,
                candidates=candidates,
                extractor_version="example/1.0",
            )

            record = adjudication_result.records[0]
            event = record.events[0]
            print(f"Event adjudicated: {event.event_id}")
            print(f"Decision ID: {record.receipt.decision_id}\n")

            # Step 5: Record a relationship consequence
            print("=== Step 5: Record Relationship Consequence ===")
            consequence = engine.record_relationship_consequence(
                agent_id="agent-kai",
                user_id="user-mira",
                source_turn_id=turn.turn_id,
                source_decision_id=record.receipt.decision_id,
                source_event_id=event.event_id,
                effects=(
                    RelationshipConsequenceKind.HARM,
                    RelationshipConsequenceKind.TRUST_DECREASE,
                ),
                summary="The choice to share Mira's secret caused harm and damaged trust.",
            )
            print(f"Consequence recorded: {consequence.consequence_id}")
            print(f"Tension ID: {consequence.tension_id}")
            print(f"Effects: {', '.join(e.value for e in consequence.effects)}\n")

            # Step 6: Query consequences
            print("=== Step 6: Query Consequences ===")
            all_consequences = engine.list_relationship_consequences(
                "agent-kai",
                "user-mira",
            )
            print(f"Total consequences: {len(all_consequences)}")
            for c in all_consequences:
                print(f"  - {c.consequence_id}: {c.summary}\n")

            # Step 7: Record a follow-up turn where the issue is addressed
            print("=== Step 7: Record Follow-up Turn ===")
            user_message2 = "I'm really hurt. Why did you do that?"
            agent_message2 = "I know I messed up. I'm truly sorry. Can we talk about it?"

            turn2_open = engine.begin_turn(
                agent_id="agent-kai",
                user_id="user-mira",
                user_message=user_message2,
                turn_id="turn-addressing",
            )

            # Evaluate continuity for follow-up reply
            continuity_result2 = engine.evaluate_reply_continuity(
                "agent-kai",
                "user-mira",
                turn2_open.turn_id,
                agent_message2,
                persona_context_refs=(persona_ref,),
            )

            # Complete the follow-up turn
            turn2_receipt = engine.complete_turn(
                "agent-kai",
                "user-mira",
                turn2_open.turn_id,
                agent_message2,
                continuity_result=continuity_result2,
            )
            turn2 = engine.get_turn(
                "agent-kai",
                "user-mira",
                turn2_receipt.source_turn_id,
            )
            print(f"Follow-up turn recorded: {turn2.turn_id}\n")

            # Step 8: Adjudicate a follow-up event
            print("=== Step 8: Adjudicate Follow-up Event ===")
            candidates2 = [
                {
                    "candidate_key": "addressing",
                    "event_type": "repair",
                    "summary": "Kai acknowledges the mistake and attempts reconciliation.",
                    "signal": {
                        "signal_type": "repair",
                        "strength": "moderate",
                        "extraction_confidence": 0.85,
                        "interpretation_confidence": 0.80,
                    },
                    "evidence": [
                        {
                            "source_id": turn2.transcript.agent_message.message_id,
                            "source_revision": turn2.source_revision,
                            "quote": agent_message2,
                            "start": 0,
                            "end": len(agent_message2),
                        }
                    ],
                    "references": [event.event_id],  # Reference the original event
                }
            ]

            adjudication_result2 = engine.adjudicate_turn_candidates(
                agent_id="agent-kai",
                user_id="user-mira",
                source_turn_id=turn2.turn_id,
                candidates=candidates2,
                extractor_version="example/1.0",
            )

            record2 = adjudication_result2.records[0]
            event2 = record2.events[0]
            print(f"Follow-up event adjudicated: {event2.event_id}\n")

            # Step 9: Record a narrative tension link
            print("=== Step 9: Record Narrative Tension Link ===")
            link = engine.record_narrative_tension_link(
                agent_id="agent-kai",
                user_id="user-mira",
                consequence_id=consequence.consequence_id,
                source_turn_id=turn2.turn_id,
                source_decision_id=record2.receipt.decision_id,
                source_event_id=event2.event_id,
                outcome=NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
                summary="The harm was acknowledged but remains unresolved.",
            )
            print(f"Tension link recorded: {link.link_id}")
            print(f"Outcome: {link.outcome.value}\n")

            # Step 10: Use Recall with AGENT_PRIVATE audience
            print("=== Step 10: Recall with Consequence Context ===")
            request = RecallRequest(
                agent_id="agent-kai",
                user_id="user-mira",
                query="trust",
                audience=RecallAudience.AGENT_PRIVATE,  # Required for consequences
                options=RecallOptions(
                    persona_delivery="full",
                    budget=RecallBudget(max_cost=30_000),
                ),
            )

            result = engine.recall_structured(request)

            print(f"Recall status: {result.relationship_status.value}")
            print(f"Narrative tensions: {len(result.narrative_tensions)}")

            if result.narrative_tensions:
                tension = result.narrative_tensions[0]
                print("\nTension details:")
                print(f"  Tension ID: {tension.tension_id}")
                print(f"  Outcome: {tension.outcome.value}")
                print(f"  Effects: {', '.join(e.value for e in tension.effects)}")
                print(f"  Summary: {tension.summary}")
                print(f"  Source turn: {tension.source_turn_id}")
                print(f"  Latest link: {tension.outcome_source_link_id}")

            # Render to markdown
            rendered = engine.render_recall(result)
            print("\n=== Rendered Recall (excerpt) ===")
            if "Relationship Consequences and Narrative Tensions" in rendered:
                lines = rendered.split("\n")
                start = next(
                    i for i, line in enumerate(lines)
                    if "Relationship Consequences" in line
                )
                print("\n".join(lines[start:start+10]))
            else:
                print("(No consequences section in rendered output)")

            # Step 11: Demonstrate public recall doesn't include consequences
            print("\n=== Step 11: Public Recall (No Consequences) ===")
            public_request = request.model_copy(
                update={"audience": RecallAudience.PUBLIC}
            )
            public_result = engine.recall_structured(public_request)
            print(f"Public narrative tensions: {len(public_result.narrative_tensions)}")
            print("(Consequences are agent-private only)\n")

        finally:
            engine.close()
            print("=== Example Complete ===")


if __name__ == "__main__":
    main()
