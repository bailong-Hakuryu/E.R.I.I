"""Contract tests for immutable recall projections and deterministic rendering."""

import unittest

from pydantic import ValidationError

from erii.models.recall import (
    BudgetOmission,
    BudgetReport,
    EventRecallProjection,
    MemoryRecallProjection,
    PersonaDelivery,
    PersonaRecallContext,
    PersonaRecallProjection,
    RecallAudience,
    RecallArtifactProvenance,
    RecallBudget,
    RecallNotice,
    RecallOptions,
    RecallRequest,
    RecallResult,
    RecallSignalProjection,
    RecallTemporalContext,
    RecallSourceReference,
    ReinforcementReport,
    RelationshipMetric,
    RelationshipNarrativeProjection,
    RelationshipRecallContext,
    RelationshipRecallStatus,
    WorldTime,
)
from erii.renderers import (
    MarkdownRecallRenderer,
    RecallAudienceMismatchError,
    RecallRenderBudgetError,
)


def _projection_fields(projection_id, source_kind, visibility=RecallAudience.AGENT_PRIVATE):
    return {
        "projection_id": projection_id,
        "source_id": f"source-{projection_id}",
        "source_kind": source_kind,
        "visibility": visibility,
        "selection_reason": "selected by the contract fixture",
    }


def _private_result():
    source_reference = RecallSourceReference(
        source_id="blueprint-lumi",
        source_kind="character_blueprint",
        source_revision="1",
        source_hash="sha256:blueprint",
        start=10,
        end=20,
    )
    authority = PersonaRecallProjection(
        **_projection_fields("persona-authority", "persona_authority"),
        kind="authority_excerpt",
        content="Authority: Lumi values ordinary life.",
        activation_tier="foundation",
        source_references=(source_reference,),
    )
    interpretation = PersonaRecallProjection(
        **_projection_fields("persona-meaning", "meaning_capsule"),
        kind="meaning_capsule",
        content="Interpretation: ordinary life represents freedom and safety.",
        activation_tier="foundation",
    )
    growth = PersonaRecallProjection(
        **_projection_fields("persona-growth", "approved_persona_growth"),
        kind="approved_growth",
        content="Growth: she has learned that patient repair can restore trust.",
    )
    premise = RelationshipNarrativeProjection(
        **_projection_fields("premise", "relationship_premise"),
        kind="canonical_continuation",
        content="Premise: this relationship explicitly continues a shared story.",
    )
    reflection = RelationshipNarrativeProjection(
        **_projection_fields("reflection", "persona_reflection"),
        kind="persona_reflection",
        content="Reflection: I remember that quiet promise.",
    )
    memory = MemoryRecallProjection(
        **_projection_fields("memory-snow", "memory_node"),
        memory_type="event",
        content="Memory: we watched the first snow together.",
        created_at="2026-07-24T10:00:00Z",
        source_visibility="public_log",
    )
    event = EventRecallProjection(
        **_projection_fields("event-tea", "relationship_event"),
        event_type="shared_experience",
        summary="Event: we quietly drank tea after the storm.",
        recorded_at="2026-07-24T11:00:00Z",
        occurred_at="2026-07-24T10:30:00Z",
    )
    signal = RecallSignalProjection(
        **{
            **_projection_fields("signal-open", "derived_signal"),
            "source_id": "event-tea",
        },
        signal_type="open_loop",
        summary="Signal: our unfinished story can be continued.",
        subject_id="event-tea",
        authority="formal_relationship_history",
        reason="unresolved_formal_loop",
        source_event_ids=("event-tea",),
    )
    return RecallResult(
        agent_id="agent_lumi",
        user_id="user_chen",
        audience=RecallAudience.AGENT_PRIVATE,
        relationship_status=RelationshipRecallStatus.INITIALIZED,
        persona_context=PersonaRecallContext(
            delivery=PersonaDelivery.PLANNED,
            blueprint_id="blueprint-lumi",
            blueprint_hash="sha256:blueprint",
            manifest_id="manifest-lumi",
            manifest_revision="2",
            authority_items=(authority,),
            interpretation_items=(interpretation,),
            approved_growth_items=(growth,),
        ),
        relationship_context=RelationshipRecallContext(
            relationship_id="relationship-lumi-chen",
            persona_id="persona-lumi-chen",
            premise=premise,
            narratives=(reflection,),
            internal_state=(RelationshipMetric(dimension="trust", value=0.72),),
        ),
        memories=(memory,),
        events=(event,),
        signals=(signal,),
        temporal_context=RecallTemporalContext(
            observed_at="2026-07-28T00:00:00Z",
            world_time=WorldTime(
                clock_id="dragon_world",
                display_value="2012-12-25 morning",
                order_value=1356415200,
            ),
        ),
        notices=(
            RecallNotice(
                code="fixture_notice",
                message="Notice: this result is a deterministic test fixture.",
            ),
        ),
        budget_report=BudgetReport(
            estimator_id="fixture_characters",
            max_cost=8000,
            required_cost=300,
            selected_cost=1200,
            omitted=(
                BudgetOmission(
                    source_id="unused-lore",
                    source_kind="persona_reference",
                    estimated_cost=500,
                    reason="lower relevance than selected projections",
                ),
            ),
        ),
        reinforcement=ReinforcementReport(
            requested=True,
            applied=True,
            reinforced_source_ids=("source-memory-snow",),
        ),
    )


class RecallModelContractTest(unittest.TestCase):
    def test_request_requires_an_explicit_audience(self):
        with self.assertRaises(ValidationError):
            RecallRequest(
                agent_id="agent_lumi",
                user_id="user_chen",
                query="snow",
                options=RecallOptions(budget=RecallBudget(max_cost=4000)),
            )

    def test_result_is_frozen_and_round_trips_through_stable_json(self):
        result = _private_result()

        encoded_once = result.stable_json()
        encoded_twice = result.stable_json()
        restored = RecallResult.model_validate_json(encoded_once)

        self.assertEqual(encoded_once, encoded_twice)
        self.assertEqual(restored, result)
        with self.assertRaises(ValidationError):
            result.user_id = "other_user"

    def test_public_result_rejects_private_projection_before_rendering(self):
        private_memory = MemoryRecallProjection(
            **_projection_fields("secret", "memory_node"),
            memory_type="thought",
            content="private thought",
        )

        with self.assertRaisesRegex(ValidationError, "agent-private projections"):
            RecallResult(
                agent_id="agent_lumi",
                user_id="user_chen",
                audience=RecallAudience.PUBLIC,
                relationship_status=RelationshipRecallStatus.UNINITIALIZED,
                memories=(private_memory,),
                budget_report=BudgetReport(
                    estimator_id="characters",
                    max_cost=100,
                    required_cost=0,
                    selected_cost=20,
                ),
            )

    def test_uninitialized_result_cannot_smuggle_relationship_context(self):
        relationship = RelationshipRecallContext(
            relationship_id="relationship-1",
            persona_id="persona-1",
        )
        with self.assertRaisesRegex(ValidationError, "uninitialized recall"):
            RecallResult(
                agent_id="agent_lumi",
                user_id="user_chen",
                audience=RecallAudience.AGENT_PRIVATE,
                relationship_status=RelationshipRecallStatus.UNINITIALIZED,
                relationship_context=relationship,
                budget_report=BudgetReport(
                    estimator_id="characters",
                    max_cost=100,
                    required_cost=0,
                    selected_cost=0,
                ),
            )


class MarkdownRecallRendererContractTest(unittest.TestCase):
    def test_renderer_is_deterministic_and_keeps_all_semantic_items(self):
        result = _private_result()
        renderer = MarkdownRecallRenderer(audience=RecallAudience.AGENT_PRIVATE)

        first = renderer.render(result)
        second = renderer.render(result)

        self.assertEqual(first, second)
        expected_semantics = (
            "Authority: Lumi values ordinary life.",
            "Interpretation: ordinary life represents freedom and safety.",
            "Growth: she has learned that patient repair can restore trust.",
            "Premise: this relationship explicitly continues a shared story.",
            "Reflection: I remember that quiet promise.",
            "Memory: we watched the first snow together.",
            "Event: we quietly drank tea after the storm.",
            "Signal: our unfinished story can be continued.",
            "2026-07-28T00:00:00Z",
            "2012-12-25 morning",
            "Notice: this result is a deterministic test fixture.",
        )
        for semantic_text in expected_semantics:
            self.assertIn(semantic_text, first)

        # Numeric relationship state and budget/reinforcement receipts are
        # diagnostics, not instructions for the dialogue model.
        self.assertNotIn("0.72", first)
        self.assertNotIn("unused-lore", first)
        self.assertNotIn("source-memory-snow", first)

    def test_memory_lines_explain_that_impressions_are_not_relationship_authority(self):
        result = _private_result()
        source_linked = result.memories[0].model_copy(
            update={
                "projection_id": "memory-source-linked",
                "source_id": "memory-source-linked",
                "provenance": RecallArtifactProvenance.SOURCE_LINKED,
                "source_references": (
                    RecallSourceReference(
                        source_id="turn-1",
                        source_kind="source_turn",
                        source_revision="1",
                    ),
                    RecallSourceReference(
                        source_id="archive-1",
                        source_kind="archival_batch",
                    ),
                ),
            }
        )
        partial_source = result.memories[0].model_copy(
            update={
                "projection_id": "memory-partial-source",
                "source_id": "memory-partial-source",
                "provenance": RecallArtifactProvenance.PARTIAL_SOURCE,
                "source_references": (
                    RecallSourceReference(
                        source_id="turn-missing",
                        source_kind="source_turn",
                    ),
                ),
            }
        )
        legacy_core = result.memories[0].model_copy(
            update={
                "projection_id": "legacy-core",
                "source_id": "legacy-core",
                "source_kind": "legacy_core_memory",
                "memory_type": "core",
                "content": "A mutable legacy summary.",
            }
        )
        rendered = MarkdownRecallRenderer(
            audience=RecallAudience.AGENT_PRIVATE
        ).render(
            result.model_copy(
                update={
                    "memories": (
                        result.memories[0],
                        source_linked,
                        partial_source,
                        legacy_core,
                    )
                }
            )
        )

        self.assertIn("[EVENT; LEGACY UNRESOLVED IMPRESSION]", rendered)
        self.assertIn("[EVENT; SOURCE-LINKED IMPRESSION]", rendered)
        self.assertIn("[EVENT; PARTIAL SOURCE IMPRESSION]", rendered)
        self.assertIn("[CORE; LEGACY MUTABLE SUMMARY]", rendered)

    def test_renderer_rejects_audience_mismatch_instead_of_filtering(self):
        result = _private_result()
        renderer = MarkdownRecallRenderer(audience=RecallAudience.PUBLIC)

        with self.assertRaises(RecallAudienceMismatchError):
            renderer.render(result)

    def test_renderer_raises_budget_error_without_truncating(self):
        result = _private_result()
        renderer = MarkdownRecallRenderer(
            audience=RecallAudience.AGENT_PRIVATE,
            max_output_cost=30,
            cost_estimator=len,
        )

        with self.assertRaises(RecallRenderBudgetError) as raised:
            renderer.render(result)

        self.assertGreater(raised.exception.required_cost, 30)
        self.assertEqual(raised.exception.max_output_cost, 30)


if __name__ == "__main__":
    unittest.main()
