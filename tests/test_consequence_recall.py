"""Structured Recall contracts for relationship consequences and tensions."""

import shutil
import tempfile
import unittest

from pydantic import ValidationError

from erii.core.recall import RecallAssembler
from erii.engine import ERIIEngine
from erii.models.consequence import (
    NarrativeTensionLink,
    NarrativeTensionOutcome,
    RelationshipConsequence,
)
from erii.models.recall import (
    BudgetReport,
    NarrativeTensionRecallProjection,
    RecallAudience,
    RecallBudget,
    RecallOptions,
    RecallRequest,
    RecallResult,
    RelationshipRecallContext,
    RelationshipRecallStatus,
)


def _consequence(relationship_id: str, suffix: str) -> RelationshipConsequence:
    return RelationshipConsequence(
        consequence_id=f"consequence-{suffix}",
        relationship_id=relationship_id,
        tension_id=f"tension-{suffix}",
        source_turn_id=f"turn-{suffix}",
        source_revision="1",
        source_decision_id=f"decision-{suffix}",
        source_event_id=f"event-{suffix}",
        source_message_id=f"message-{suffix}",
        effects=("harm", "trust_decrease"),
        summary=f"The choice damaged trust for {suffix}.",
        recorded_at="2026-08-06T10:00:00Z",
    )


def _link(relationship_id: str, suffix: str) -> NarrativeTensionLink:
    return NarrativeTensionLink(
        link_id=f"link-{suffix}",
        relationship_id=relationship_id,
        tension_id=f"tension-{suffix}",
        consequence_id=f"consequence-{suffix}",
        source_turn_id=f"turn-{suffix}-follow-up",
        source_revision="2",
        source_decision_id=f"decision-{suffix}-follow-up",
        source_event_id=f"event-{suffix}-follow-up",
        outcome=NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
        summary=f"The harm was addressed but remains unresolved for {suffix}.",
        recorded_at="2026-08-06T11:00:00Z",
    )


def _recall_projection(
    suffix: str,
    outcome: NarrativeTensionOutcome,
) -> NarrativeTensionRecallProjection:
    linked = outcome != NarrativeTensionOutcome.UNADDRESSED
    return NarrativeTensionRecallProjection(
        projection_id=f"narrative-tension:tension-{suffix}",
        source_id=f"consequence-{suffix}",
        source_kind="relationship_consequence",
        visibility=RecallAudience.AGENT_PRIVATE,
        selection_reason="budget fixture",
        relationship_id="relationship-budget",
        tension_id=f"tension-{suffix}",
        consequence_id=f"consequence-{suffix}",
        source_turn_id=f"turn-{suffix}",
        source_revision="1",
        source_decision_id=f"decision-{suffix}",
        source_event_id=f"event-{suffix}",
        source_message_id=f"message-{suffix}",
        effects=("harm",),
        outcome=outcome,
        summary=f"Tension {suffix}",
        link_ids=((f"link-{suffix}",) if linked else ()),
        outcome_source_link_id=(f"link-{suffix}" if linked else None),
        outcome_source_turn_id=(
            f"turn-{suffix}-follow-up" if linked else f"turn-{suffix}"
        ),
        outcome_source_revision="2" if linked else "1",
        outcome_source_decision_id=(
            f"decision-{suffix}-follow-up" if linked else f"decision-{suffix}"
        ),
        outcome_source_event_id=(
            f"event-{suffix}-follow-up" if linked else f"event-{suffix}"
        ),
    )


class ConsequenceRecallTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_recall_is_relationship_scoped_and_retains_both_source_layers(self):
        engine = ERIIEngine(storage_dir=self.root)
        try:
            engine.initialize_relationship("mira", "alice", "Mira values honesty.")
            engine.initialize_relationship("mira", "bob", "Mira values honesty.")
            alice = engine.storage.get_relationship("mira", "alice")
            bob = engine.storage.get_relationship("mira", "bob")
            self.assertIsNotNone(alice)
            self.assertIsNotNone(bob)

            alice_consequence = _consequence(alice.relationship_id, "alice")
            engine.storage.append_relationship_consequence(alice_consequence)
            engine.storage.append_narrative_tension_link(
                _link(alice.relationship_id, "alice")
            )
            engine.storage.append_relationship_consequence(
                _consequence(bob.relationship_id, "bob")
            )

            request = RecallRequest(
                agent_id="mira",
                user_id="alice",
                query="trust",
                audience=RecallAudience.AGENT_PRIVATE,
                options=RecallOptions(
                    persona_delivery="full",
                    budget=RecallBudget(max_cost=30_000),
                ),
            )
            result = engine.recall_structured(request)

            self.assertEqual(len(result.narrative_tensions), 1)
            tension = result.narrative_tensions[0]
            self.assertEqual(tension.relationship_id, alice.relationship_id)
            self.assertEqual(tension.consequence_id, "consequence-alice")
            self.assertEqual(
                tension.outcome,
                NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
            )
            self.assertEqual(tension.source_message_id, "message-alice")
            self.assertEqual(tension.outcome_source_link_id, "link-alice")
            self.assertEqual(
                tension.outcome_source_event_id,
                "event-alice-follow-up",
            )
            reference_pairs = {
                (reference.source_kind, reference.source_id)
                for reference in tension.source_references
            }
            self.assertIn(
                ("relationship_decision_receipt", "decision-alice"),
                reference_pairs,
            )
            self.assertIn(
                ("relationship_event", "event-alice-follow-up"),
                reference_pairs,
            )

            rendered_once = engine.render_recall(result)
            rendered_twice = engine.render_recall(result)
            self.assertEqual(rendered_once, rendered_twice)
            self.assertIn(
                "# Relationship Consequences and Narrative Tensions",
                rendered_once,
            )
            self.assertIn("addressed_unresolved", rendered_once)
            self.assertIn("consequence=consequence-alice", rendered_once)
            self.assertIn("link=link-alice", rendered_once)
            self.assertNotIn("consequence-bob", result.stable_json())
            self.assertNotIn("for bob", rendered_once)

            legacy_rendered = engine.recall("mira", "alice", "trust")
            self.assertIn("consequence=consequence-alice", legacy_rendered)
            self.assertNotIn("consequence-bob", legacy_rendered)

            public_result = engine.recall_structured(
                request.model_copy(update={"audience": RecallAudience.PUBLIC})
            )
            self.assertEqual(public_result.narrative_tensions, ())
        finally:
            engine.close()

    def test_open_tension_wins_a_constrained_optional_budget(self):
        closed = _recall_projection(
            "closed",
            NarrativeTensionOutcome.RELATIONSHIP_ENDED,
        )
        assembler = RecallAssembler(
            storage=object(),
            retriever=object(),
            cost_estimator=lambda _serialized: 10,
        )

        for outcome in (
            NarrativeTensionOutcome.UNADDRESSED,
            NarrativeTensionOutcome.ADDRESSED_UNRESOLVED,
        ):
            with self.subTest(outcome=outcome):
                open_tension = _recall_projection("open", outcome)
                budgeted = assembler._apply_budget(
                    None,
                    None,
                    (),
                    {},
                    (),
                    (),
                    (closed, open_tension),
                    10,
                )

                self.assertEqual(budgeted[5], [open_tension])
                self.assertEqual(
                    tuple(item.source_id for item in budgeted[6].omitted),
                    ("consequence-closed",),
                )

    def test_old_result_payload_remains_valid_without_tension_field(self):
        old_result = RecallResult(
            agent_id="mira",
            user_id="alice",
            audience=RecallAudience.AGENT_PRIVATE,
            relationship_status=RelationshipRecallStatus.UNINITIALIZED,
            budget_report=BudgetReport(
                estimator_id="fixture",
                max_cost=100,
                required_cost=0,
                selected_cost=0,
            ),
        )
        old_payload = old_result.model_dump(mode="json")
        old_payload.pop("narrative_tensions")

        restored = RecallResult.model_validate(old_payload)

        self.assertEqual(restored.narrative_tensions, ())

    def test_result_rejects_a_tension_from_another_relationship(self):
        with self.assertRaisesRegex(ValidationError, "crossed relationship scope"):
            RecallResult(
                agent_id="mira",
                user_id="alice",
                audience=RecallAudience.AGENT_PRIVATE,
                relationship_status=RelationshipRecallStatus.INITIALIZED,
                relationship_context=RelationshipRecallContext(
                    relationship_id="relationship-alice",
                    persona_id="persona-alice",
                ),
                narrative_tensions=(
                    _recall_projection(
                        "open",
                        NarrativeTensionOutcome.UNADDRESSED,
                    ),
                ),
                budget_report=BudgetReport(
                    estimator_id="fixture",
                    max_cost=100,
                    required_cost=0,
                    selected_cost=0,
                ),
            )


if __name__ == "__main__":
    unittest.main()
