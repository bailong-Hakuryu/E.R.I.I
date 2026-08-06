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
from erii.models.consequence import (
    NarrativeTensionOutcome,
    RelationshipConsequenceKind,
)
from erii.models.recall import (
    RecallRequest,
    RecallAudience,
    RecallOptions,
    RecallBudget,
)


def main():
    """Run the consequence tracking example."""
    # Create a temporary storage directory
    with tempfile.TemporaryDirectory() as storage_dir:
        engine = ERIIEngine(storage_dir=storage_dir)

        try:
            # Step 1: Initialize a relationship
            print("=== Step 1: Initialize Relationship ===")
            profile = engine.initialize_relationship(
                agent_id="agent-kai",
                user_id="user-mira",
                persona_source="Kai is honest and values trust deeply.",
            )
            print(f"Relationship initialized: {profile.relationship_id}\n")

            # Step 2: Record a completed turn with continuity support
            print("=== Step 2: Record a Source Turn ===")
            turn = engine.begin_turn(
                agent_id="agent-kai",
                user_id="user-mira",
                user_message="Did you tell anyone about my secret?",
                turn_id="turn-boundary-test",
            )

            agent_reply = "I... I had to tell someone. I'm sorry."

            # Complete the turn (in real usage, you'd evaluate continuity first)
            engine.complete_turn(
                agent_id="agent-kai",
                user_id="user-mira",
                turn_id=turn.turn_id,
                agent_message=agent_reply,
            )
            print(f"Turn completed: {turn.turn_id}\n")

            # Step 3: Adjudicate a relationship event from the turn
            print("=== Step 3: Adjudicate Relationship Event ===")
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
                            "quote": agent_reply,
                            "start": 0,
                            "end": len(agent_reply),
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

            # Step 4: Record a relationship consequence
            print("=== Step 4: Record Relationship Consequence ===")
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

            # Step 5: Query consequences
            print("=== Step 5: Query Consequences ===")
            all_consequences = engine.storage.list_relationship_consequences(
                profile.relationship_id
            )
            print(f"Total consequences: {len(all_consequences)}")
            for c in all_consequences:
                print(f"  - {c.consequence_id}: {c.summary}\n")

            # Step 6: Record a later turn where the issue is addressed
            print("=== Step 6: Record Follow-up Turn ===")
            turn2 = engine.begin_turn(
                agent_id="agent-kai",
                user_id="user-mira",
                user_message="I'm really hurt. Why did you do that?",
                turn_id="turn-addressing",
            )

            agent_reply2 = "I know I messed up. I'm truly sorry. Can we talk about it?"

            engine.complete_turn(
                agent_id="agent-kai",
                user_id="user-mira",
                turn_id=turn2.turn_id,
                agent_message=agent_reply2,
            )
            print(f"Follow-up turn completed: {turn2.turn_id}\n")

            # Step 7: Adjudicate a follow-up event
            print("=== Step 7: Adjudicate Follow-up Event ===")
            candidates2 = [
                {
                    "candidate_key": "addressing",
                    "event_type": "reconciliation_attempt",
                    "summary": "Kai acknowledges the mistake and attempts reconciliation.",
                    "signal": {
                        "signal_type": "reconciliation_attempt",
                        "strength": "moderate",
                        "extraction_confidence": 0.85,
                        "interpretation_confidence": 0.80,
                    },
                    "evidence": [
                        {
                            "source_id": turn2.transcript.agent_message.message_id,
                            "source_revision": turn2.source_revision,
                            "quote": agent_reply2,
                            "start": 0,
                            "end": len(agent_reply2),
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

            # Step 8: Record a narrative tension link
            print("=== Step 8: Record Narrative Tension Link ===")
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

            # Step 9: Use Recall with AGENT_PRIVATE audience
            print("=== Step 9: Recall with Consequence Context ===")
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

            # Step 10: Demonstrate public recall doesn't include consequences
            print("\n=== Step 10: Public Recall (No Consequences) ===")
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
