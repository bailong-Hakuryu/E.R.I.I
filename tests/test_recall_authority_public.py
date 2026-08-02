"""Public a8 contracts for memory recall authority selection."""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Optional

from erii import (
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    FileStorage,
    MemoryNode,
    MemoryRecallProjection,
    MemoryType,
    RecallAudience,
    RecallAuthorityTier,
    RecallBudget,
    RecallOptions,
    RecallRequest,
    TimelineEntry,
)
from erii.core.recall_authority import RecallAuthoritySelector
from erii.models.provenance import ArtifactProvenanceState


AGENT_ID = "agent-erii"
USER_ID = "user-one"
PERSONA_SOURCE = "Erii is careful with shared memories."


def _delivery_exception():
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "tests.recall-authority/v1",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-02T00:00:00+00:00",
        "reply_attempt_number": None,
    }


class _UserEvidenceExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.recall-authority-extractor",
        extractor_version="1",
        extraction_schema_version="2",
    )

    def extract(self, request):
        message = request.transcript.user_message
        return {
            "kind": "artifacts",
            "timeline": [],
            "memories": [
                {
                    "node_type": "event",
                    "content": message.content,
                    "evidence": [
                        {
                            "citation_version": "archival-evidence-citation/v1",
                            "kind": "message_span",
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


class _UserTimelineExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="tests.recall-authority-timeline-extractor",
        extractor_version="1",
        extraction_schema_version="2",
    )

    def extract(self, request):
        message = request.transcript.user_message
        return {
            "kind": "artifacts",
            "timeline": [
                {
                    "content": message.content,
                    "evidence": [
                        {
                            "citation_version": "archival-evidence-citation/v1",
                            "kind": "message_span",
                            "source_id": message.message_id,
                            "source_revision": request.source_revision,
                            "quote": message.content,
                            "start": 0,
                            "end": len(message.content),
                        }
                    ],
                }
            ],
            "memories": [],
        }


def _approve_persona(engine: ERIIEngine) -> None:
    proposal = engine.propose_persona_compilation(
        AGENT_ID,
        USER_ID,
        {
            "compiler_version": "tests.recall-authority/v1",
            "source_spans": [
                {
                    "span_id": "span-identity",
                    "start": 0,
                    "end": len(PERSONA_SOURCE),
                    "quote": PERSONA_SOURCE,
                }
            ],
            "claims": [
                {
                    "claim_id": "claim-identity",
                    "kind": "identity",
                    "statement": PERSONA_SOURCE,
                    "activation_tier": "foundation",
                    "basis": "explicit",
                    "source_span_ids": ["span-identity"],
                }
            ],
        },
    )
    engine.decide_persona_compilation(
        AGENT_ID,
        USER_ID,
        proposal.proposal_id,
        proposal.revision,
        "owner",
        "approve",
    )


class RecallAuthorityPublicTests(unittest.TestCase):
    def test_selector_preserves_upstream_hybrid_retrieval_order(self):
        first = MemoryRecallProjection(
            projection_id="memory:upstream-first",
            source_id="upstream-first",
            source_kind="memory_node",
            visibility=RecallAudience.AGENT_PRIVATE,
            selection_reason="relevance_and_diversity_rank",
            authority_tier=RecallAuthorityTier.ORDINARY,
            memory_type="event",
            content="snow",
        )
        lexically_stronger_second = MemoryRecallProjection(
            projection_id="memory:lexically-stronger-second",
            source_id="lexically-stronger-second",
            source_kind="memory_node",
            visibility=RecallAudience.AGENT_PRIVATE,
            selection_reason="relevance_and_diversity_rank",
            authority_tier=RecallAuthorityTier.ORDINARY,
            memory_type="event",
            content="snow winter",
        )

        selection = RecallAuthoritySelector.select(
            (first, lexically_stronger_second),
            audience=RecallAudience.AGENT_PRIVATE,
            query="snow winter",
            top_k=2,
        )

        self.assertEqual(
            tuple(item.projection_id for item in selection.projections),
            (first.projection_id, lexically_stronger_second.projection_id),
        )

    def _engine(self, root: str) -> ERIIEngine:
        engine = ERIIEngine(
            storage_driver=FileStorage(root),
            memory_extractor=_UserEvidenceExtractor(),
            config=ERIIConfig(async_archival=False),
        )
        engine.initialize_relationship(
            AGENT_ID,
            USER_ID,
            PERSONA_SOURCE,
        )
        _approve_persona(engine)
        return engine

    @staticmethod
    def _archive_ordinary(
        engine: ERIIEngine,
        *,
        turn_id: str,
        content: str,
    ) -> MemoryNode:
        engine.record_turn(
            AGENT_ID,
            USER_ID,
            content,
            "I heard you.",
            turn_id=turn_id,
            delivery_exception=_delivery_exception(),
        )
        receipt = engine.archive_turn(
            AGENT_ID,
            USER_ID,
            turn_id,
            idempotency_key=f"archive-{turn_id}",
        )
        return next(
            node
            for node in engine.storage.load_nodes(AGENT_ID, USER_ID)
            if node.source_archival_id == receipt.archival_id
        )

    @staticmethod
    def _append_legacy(
        engine: ERIIEngine,
        *,
        node_id: str,
        content: str,
        source_turn_id: Optional[str] = None,
        base_importance: float = 0.5,
        is_unresolved: bool = False,
    ) -> None:
        nodes = engine.storage.load_nodes(AGENT_ID, USER_ID)
        relationship = engine.storage.get_relationship(AGENT_ID, USER_ID)
        descriptor = None
        provenance = ArtifactProvenanceState.LEGACY_UNAVAILABLE
        relationship_id = None
        source_archival_id = None
        if source_turn_id is not None:
            descriptor = ExtractorDescriptor(
                extractor_id="tests.legacy-extractor",
                extractor_version="1",
                extraction_schema_version="1",
            )
            provenance = ArtifactProvenanceState.COMPLETE
            relationship_id = relationship.relationship_id
            source_archival_id = f"legacy-archive-{node_id}"
        nodes.append(
            MemoryNode(
                node_id=node_id,
                agent_id=AGENT_ID,
                user_id=USER_ID,
                node_type=MemoryType.EVENT,
                content=content,
                base_importance=base_importance,
                relationship_id=relationship_id,
                source_turn_id=source_turn_id,
                source_archival_id=source_archival_id,
                provenance_state=provenance,
                extractor_descriptor=descriptor,
                is_unresolved=is_unresolved,
            )
        )
        engine.storage.save_nodes(AGENT_ID, USER_ID, nodes)

    @staticmethod
    def _private_request(query: str, *, top_k: int, reinforce: bool = False):
        return RecallRequest(
            agent_id=AGENT_ID,
            user_id=USER_ID,
            query=query,
            audience=RecallAudience.AGENT_PRIVATE,
            options=RecallOptions(
                top_k=top_k,
                max_per_type=100,
                reinforce=reinforce,
                budget=RecallBudget(max_cost=50_000),
            ),
        )

    def test_private_recall_separates_ordinary_legacy_and_quarantine(self):
        with tempfile.TemporaryDirectory() as root:
            engine = self._engine(os.path.join(root, "memory"))
            try:
                ordinary = self._archive_ordinary(
                    engine,
                    turn_id="turn-ordinary",
                    content="snow verified",
                )
                engine.record_turn(
                    AGENT_ID,
                    USER_ID,
                    "secret source",
                    "unreviewed agent reply",
                    turn_id="turn-exceptional",
                    delivery_exception=_delivery_exception(),
                )
                self._append_legacy(
                    engine,
                    node_id="legacy-fireworks",
                    content="fireworks legacy",
                )
                self._append_legacy(
                    engine,
                    node_id="quarantined-secret",
                    content="secret quarantine",
                    source_turn_id="turn-exceptional",
                )

                result = engine.recall_structured(
                    self._private_request(
                        "snow fireworks secret",
                        top_k=3,
                        reinforce=True,
                    )
                )
                self.assertEqual(
                    [item.authority_tier for item in result.memories],
                    [
                        RecallAuthorityTier.ORDINARY,
                        RecallAuthorityTier.LEGACY_CONTEXT,
                    ],
                )
                self.assertNotIn(
                    "secret quarantine",
                    [item.content for item in result.memories],
                )
                rendered = engine.render_recall(result)
                self.assertIn("# Verified Memories", rendered)
                self.assertIn("# Legacy Context - provenance incomplete", rendered)
                self.assertNotIn("secret quarantine", rendered)

                stored = {
                    node.node_id: node
                    for node in engine.storage.load_nodes(AGENT_ID, USER_ID)
                }
                self.assertEqual(stored[ordinary.node_id].access_count, 1)
                self.assertEqual(stored["legacy-fireworks"].access_count, 0)
                self.assertEqual(stored["quarantined-secret"].access_count, 0)
            finally:
                engine.close()

    def test_public_recall_exposes_only_ordinary_memory(self):
        with tempfile.TemporaryDirectory() as root:
            engine = self._engine(root)
            try:
                self._archive_ordinary(
                    engine,
                    turn_id="turn-public",
                    content="public verified snow",
                )
                self._append_legacy(
                    engine,
                    node_id="legacy-public",
                    content="public legacy snow",
                )
                result = engine.recall_structured(
                    RecallRequest(
                        agent_id=AGENT_ID,
                        user_id=USER_ID,
                        query="snow",
                        audience=RecallAudience.PUBLIC,
                        options=RecallOptions(top_k=10, max_per_type=100),
                    )
                )
                self.assertEqual(len(result.memories), 1)
                self.assertEqual(
                    result.memories[0].authority_tier,
                    RecallAuthorityTier.ORDINARY,
                )
            finally:
                engine.close()

    def test_quarantined_memory_cannot_leak_through_derived_signals(self):
        with tempfile.TemporaryDirectory() as root:
            engine = self._engine(root)
            try:
                engine.record_turn(
                    AGENT_ID,
                    USER_ID,
                    "secret source",
                    "unreviewed agent reply",
                    turn_id="turn-quarantined-signal",
                    delivery_exception=_delivery_exception(),
                )
                self._append_legacy(
                    engine,
                    node_id="quarantined-open-loop",
                    content="QUARANTINED SECRET MUST NOT REENTER THE PROMPT",
                    source_turn_id="turn-quarantined-signal",
                    is_unresolved=True,
                )

                result = engine.recall_structured(
                    self._private_request("secret", top_k=5)
                )
                rendered = engine.render_recall(result)

                self.assertEqual(result.memories, ())
                self.assertTrue(
                    all(
                        "QUARANTINED SECRET" not in signal.summary
                        for signal in result.signals
                    )
                )
                self.assertNotIn("QUARANTINED SECRET", rendered)
            finally:
                engine.close()

    def test_diversity_cap_is_applied_after_authority_classification(self):
        with tempfile.TemporaryDirectory() as root:
            engine = self._engine(root)
            try:
                ordinary = self._archive_ordinary(
                    engine,
                    turn_id="turn-low-weight-ordinary",
                    content="snow ordinary",
                )
                self._append_legacy(
                    engine,
                    node_id="legacy-high-weight",
                    content="snow legacy",
                    base_importance=0.95,
                )

                for audience in (
                    RecallAudience.AGENT_PRIVATE,
                    RecallAudience.PUBLIC,
                ):
                    with self.subTest(audience=audience.value):
                        result = engine.recall_structured(
                            RecallRequest(
                                agent_id=AGENT_ID,
                                user_id=USER_ID,
                                query="snow",
                                audience=audience,
                                options=RecallOptions(
                                    top_k=1,
                                    max_per_type=1,
                                    budget=RecallBudget(max_cost=50_000),
                                ),
                            )
                        )
                        self.assertEqual(
                            tuple(item.source_id for item in result.memories),
                            (ordinary.node_id,),
                        )
            finally:
                engine.close()

    def test_timeline_diversity_cap_is_applied_after_authority_classification(self):
        with tempfile.TemporaryDirectory() as root:
            engine = ERIIEngine(
                storage_driver=FileStorage(root),
                memory_extractor=_UserTimelineExtractor(),
                config=ERIIConfig(async_archival=False),
            )
            try:
                profile = engine.initialize_relationship(
                    AGENT_ID,
                    USER_ID,
                    PERSONA_SOURCE,
                )
                _approve_persona(engine)
                engine.record_turn(
                    AGENT_ID,
                    USER_ID,
                    "snow ordinary timeline",
                    "I heard you.",
                    turn_id="turn-ordinary-timeline",
                    delivery_exception=_delivery_exception(),
                )
                engine.archive_turn(
                    AGENT_ID,
                    USER_ID,
                    "turn-ordinary-timeline",
                    idempotency_key="archive-ordinary-timeline",
                )
                engine.storage.import_timeline_entries(
                    AGENT_ID,
                    USER_ID,
                    [
                        TimelineEntry(
                            timeline_entry_id="legacy-newer-timeline",
                            relationship_id=profile.relationship_id,
                            agent_id=AGENT_ID,
                            user_id=USER_ID,
                            content="snow legacy timeline",
                            recorded_at=None,
                            legacy_timestamp="9999-01-01T00:00:00+00:00",
                            provenance_state=(
                                ArtifactProvenanceState.LEGACY_UNAVAILABLE
                            ),
                        )
                    ],
                )

                result = engine.recall_structured(
                    RecallRequest(
                        agent_id=AGENT_ID,
                        user_id=USER_ID,
                        query="snow timeline",
                        audience=RecallAudience.AGENT_PRIVATE,
                        options=RecallOptions(
                            top_k=2,
                            max_per_type=1,
                            budget=RecallBudget(max_cost=50_000),
                        ),
                    )
                )

                timelines = [
                    item
                    for item in result.memories
                    if item.memory_type == "timeline"
                ]
                self.assertEqual(len(timelines), 1)
                self.assertEqual(
                    timelines[0].authority_tier,
                    RecallAuthorityTier.ORDINARY,
                )
                self.assertEqual(
                    timelines[0].content,
                    "snow ordinary timeline",
                )
            finally:
                engine.close()

    def test_full_ordinary_pool_reserves_at_most_one_legacy_slot(self):
        with tempfile.TemporaryDirectory() as root:
            engine = self._engine(root)
            try:
                for index in range(3):
                    self._archive_ordinary(
                        engine,
                        turn_id=f"turn-ordinary-{index}",
                        content=f"snow ordinary {index}",
                    )
                self._append_legacy(
                    engine,
                    node_id="legacy-snow",
                    content="snow legacy",
                )

                top_three = engine.recall_structured(
                    self._private_request("snow", top_k=3)
                )
                self.assertEqual(len(top_three.memories), 3)
                self.assertEqual(
                    sum(
                        item.authority_tier == RecallAuthorityTier.LEGACY_CONTEXT
                        for item in top_three.memories
                    ),
                    1,
                )
                top_one = engine.recall_structured(
                    self._private_request("snow", top_k=1)
                )
                self.assertEqual(
                    top_one.memories[0].authority_tier,
                    RecallAuthorityTier.ORDINARY,
                )
            finally:
                engine.close()

    def test_exact_legacy_duplicate_never_consumes_a_slot(self):
        with tempfile.TemporaryDirectory() as root:
            engine = self._engine(root)
            try:
                self._archive_ordinary(
                    engine,
                    turn_id="turn-duplicate",
                    content="the exact snow memory",
                )
                self._append_legacy(
                    engine,
                    node_id="legacy-duplicate",
                    content="the exact snow memory",
                )
                result = engine.recall_structured(
                    self._private_request("snow", top_k=2)
                )
                self.assertEqual(len(result.memories), 1)
                self.assertEqual(
                    result.memories[0].authority_tier,
                    RecallAuthorityTier.ORDINARY,
                )

                compatible = engine.recall(
                    AGENT_ID,
                    USER_ID,
                    "snow",
                    top_k=2,
                )
                self.assertIn("# Verified Memories", compatible)
                self.assertNotIn("Legacy Context", compatible)
            finally:
                engine.close()

    def test_unrelated_legacy_does_not_displace_an_ordinary_memory(self):
        with tempfile.TemporaryDirectory() as root:
            engine = self._engine(root)
            try:
                for index in range(2):
                    self._archive_ordinary(
                        engine,
                        turn_id=f"turn-relevant-{index}",
                        content=f"snow ordinary {index}",
                    )
                self._append_legacy(
                    engine,
                    node_id="legacy-unrelated",
                    content="summer fireworks legacy",
                )

                result = engine.recall_structured(
                    self._private_request("snow", top_k=2)
                )

                self.assertEqual(len(result.memories), 2)
                self.assertTrue(
                    all(
                        item.authority_tier == RecallAuthorityTier.ORDINARY
                        for item in result.memories
                    )
                )
            finally:
                engine.close()

    def test_oversized_legacy_reservation_falls_back_to_ordinary(self):
        with tempfile.TemporaryDirectory() as root:
            engine = self._engine(root)
            try:
                for index in range(2):
                    self._archive_ordinary(
                        engine,
                        turn_id=f"turn-budget-{index}",
                        content=f"snow ordinary {index}",
                    )
                self._append_legacy(
                    engine,
                    node_id="legacy-oversized",
                    content="snow " + ("legacy " * 600),
                )

                request = self._private_request("snow", top_k=2)
                constrained = request.model_copy(
                    update={
                        "options": request.options.model_copy(
                            update={
                                "budget": RecallBudget(max_cost=3_000),
                            }
                        )
                    }
                )
                result = engine.recall_structured(constrained)

                self.assertEqual(len(result.memories), 2)
                self.assertTrue(
                    all(
                        item.authority_tier == RecallAuthorityTier.ORDINARY
                        for item in result.memories
                    )
                )
                self.assertLessEqual(
                    result.budget_report.selected_cost,
                    result.budget_report.max_cost,
                )
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
