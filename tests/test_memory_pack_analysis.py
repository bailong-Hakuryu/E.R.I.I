"""Contracts for the no-write MemoryPack analysis Interface."""

import ast
from dataclasses import FrozenInstanceError, replace
import inspect
from pathlib import Path
import tempfile
import unittest

from erii import ERIIEngine, FileStorage
import erii._engine.memory_pack_analysis as analysis_module
import erii._lifecycle.memory_pack_validation as lifecycle_validation_module
from erii._engine.memory_pack_analysis import (
    analyze_memory_pack,
    analyze_memory_pack_relationship_processing,
    analyze_relationship_processing_reflection_context,
    analyze_relationship_processing_pack_structure,
    validate_relationship_processing_reflections,
    validate_relationship_processing_runs,
    resolve_relationship_processing_profile,
    validate_persisted_turn_adjudication_sources,
    validate_memory_pack_relationship_consequences,
    validate_memory_pack_relationship_processing,
    validate_memory_pack_turn_records,
)
from erii._lifecycle.memory_pack_validation import validate_memory_pack_semantic_graph
from erii.errors import StorageIntegrityError
from erii.models.consequence import (
    RelationshipConsequence,
    RelationshipConsequenceKind,
)
from erii.models.adjudication import (
    AdjudicationRecord,
    DecisionOutcome,
    DecisionReceipt,
    GrowthTriggerKind,
    PersonaGrowthProposal,
)
from erii.models.node import MemoryNode, MemoryType
from erii.models.pack import MemoryPack
from erii.models.relationship import RelationshipEvent, RelationshipEventType
from erii.models.temporal import PromiseResolution, PromiseResolutionKind
from erii.models.consolidation import RelationshipProcessingOutcome
from tests.test_relationship_processing_public import (
    _preexisting_delivery_exception,
    _ReflectionInterpreter,
    _RelationshipExtractor,
)


FIXTURE_SOURCE = (
    Path(__file__).parent
    / "fixtures"
    / "lifecycle"
    / "memory-pack-v0.4.0a7"
    / "source.erii"
)


class MemoryPackAnalysisTests(unittest.TestCase):
    @staticmethod
    def _fixture_pack() -> MemoryPack:
        return MemoryPack.from_json(FIXTURE_SOURCE.read_text(encoding="utf-8"))

    @staticmethod
    def _processing_pack() -> MemoryPack:
        with tempfile.TemporaryDirectory() as root:
            with ERIIEngine(
                storage_driver=FileStorage(root),
                relationship_event_extractor=_RelationshipExtractor(),
                persona_reflection_interpreter=_ReflectionInterpreter(),
            ) as engine:
                engine.initialize_relationship(
                    "agent-lumi",
                    "user-chen",
                    "Lumi values grounded shared experiences.",
                )
                engine.record_turn(
                    "agent-lumi",
                    "user-chen",
                    "The snow is beautiful.",
                    "Yes. I want to remember this quiet moment.",
                    turn_id="turn-snow",
                    delivery_exception=_preexisting_delivery_exception(),
                )
                engine.process_relationship_turn(
                    "agent-lumi",
                    "user-chen",
                    "turn-snow",
                )
                return engine.export_memory("agent-lumi", "user-chen")

    def test_analysis_is_deterministic_immutable_and_does_not_mutate_pack(self) -> None:
        pack = self._fixture_pack()
        before = pack.to_dict()

        first = analyze_memory_pack(pack)
        second = analyze_memory_pack(pack)

        self.assertEqual(first, second)
        self.assertEqual(pack.to_dict(), before)
        self.assertTrue(first.has_bound_archival_history)
        self.assertTrue(first.requires_exact_relationship_restore)
        with self.assertRaises(FrozenInstanceError):
            first.has_bound_archival_history = False

    def test_interface_has_no_storage_dependency(self) -> None:
        parameters = tuple(inspect.signature(analyze_memory_pack).parameters)
        self.assertEqual(parameters, ("pack",))

        source = inspect.getsource(analysis_module)
        tree = ast.parse(source)
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertFalse(
            any(module == "erii.storage" or module.startswith("erii.storage.") for module in imported_modules)
        )
        self.assertNotIn("storage", {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)})

    def test_instruction_failure_message_and_input_are_preserved(self) -> None:
        pack = MemoryPack(
            agent_id="agent-lumi",
            user_id="user-chen",
            nodes=[
                MemoryNode(
                    node_id="instruction-1",
                    agent_id="agent-lumi",
                    user_id="user-chen",
                    content="Do not persist this directive.",
                    node_type=MemoryType.INSTRUCTION,
                )
            ],
        )
        before = pack.to_dict()

        with self.assertRaisesRegex(
            ValueError,
            "MemoryPack instruction nodes cannot be imported into long-term memory",
        ):
            analyze_memory_pack(pack)

        self.assertEqual(pack.to_dict(), before)

    def test_temporal_failure_message_and_input_are_preserved(self) -> None:
        pack = self._fixture_pack()
        pack.relationship_events.append(
            RelationshipEvent(
                event_id="missing-promise-resolution",
                relationship_id=pack.relationship.relationship_id,
                event_type=RelationshipEventType.PROMISE_RESOLUTION,
                content="A missing promise was resolved.",
                temporal_payload=PromiseResolution(
                    promise_event_id="missing-promise",
                    resolution_kind=PromiseResolutionKind.FULFILLED,
                ),
            )
        )
        before = pack.to_dict()

        with self.assertRaisesRegex(
            ValueError,
            "MemoryPack temporal event references missing source events: missing-promise",
        ):
            analyze_memory_pack(pack)

        self.assertEqual(pack.to_dict(), before)

    def test_persona_growth_failure_message_and_input_are_preserved(self) -> None:
        pack = self._fixture_pack()
        proposal = PersonaGrowthProposal(
            proposal_id="growth-1",
            relationship_id=pack.relationship.relationship_id,
            revision=1,
            intent_key="remember-bookmark",
            review_id="review-1",
            statement="Retain the shared bookmark memory.",
            rationale="It is grounded in the accepted event.",
            proposed_changes={"voice": "warmer"},
            supporting_event_ids=(pack.relationship_events[0].event_id,),
            trigger_kind=GrowthTriggerKind.PIVOTAL,
        )
        pack.persona_growth_proposals.extend((proposal, proposal))
        before = pack.to_dict()

        with self.assertRaisesRegex(
            ValueError,
            "MemoryPack contains duplicate Persona Growth identities",
        ):
            analyze_memory_pack(pack)

        self.assertEqual(pack.to_dict(), before)

    def test_consequence_failure_message_and_input_are_preserved(self) -> None:
        pack = MemoryPack(
            agent_id="agent-lumi",
            user_id="user-chen",
            relationship_consequences=[
                RelationshipConsequence(
                    consequence_id="consequence-1",
                    relationship_id="relationship-1",
                    tension_id="tension-1",
                    source_turn_id="turn-1",
                    source_revision="revision-1",
                    source_decision_id="decision-1",
                    source_event_id="event-1",
                    source_message_id="message-1",
                    effects=(RelationshipConsequenceKind.HARM,),
                    summary="The exchange caused harm.",
                    recorded_at="2026-08-13T00:00:00+00:00",
                )
            ],
        )
        before = pack.to_dict()

        with self.assertRaisesRegex(
            ValueError,
            "MemoryPack relationship consequences require a relationship profile",
        ):
            validate_memory_pack_relationship_consequences(pack)

        self.assertEqual(pack.to_dict(), before)

    def test_duplicate_turn_failure_message_and_input_are_preserved(self) -> None:
        pack = self._fixture_pack()
        pack.turn_records.append(pack.turn_records[0])
        before = pack.to_dict()

        with self.assertRaisesRegex(
            ValueError,
            f"MemoryPack contains duplicate turn_id {pack.turn_records[0].turn_id!r}",
        ):
            validate_memory_pack_turn_records(pack)

        self.assertEqual(pack.to_dict(), before)

    def test_persisted_turn_evidence_failure_and_input_are_preserved(self) -> None:
        pack = self._fixture_pack()
        turn = pack.turn_records[0]
        relationship_id = pack.relationship.relationship_id
        record = AdjudicationRecord(
            receipt=DecisionReceipt(
                decision_id="decision-without-evidence",
                relationship_id=relationship_id,
                source_turn_id=turn.turn_id,
                source_revision=turn.source_revision,
                candidate_key="candidate-without-evidence",
                candidate_fingerprint="candidate-fingerprint",
                batch_fingerprint="batch-fingerprint",
                occurrence_fingerprint="occurrence-fingerprint",
                outcome=DecisionOutcome.ACCEPTED,
                reason_codes=("accepted_by_policy",),
                extraction_confidence=1.0,
                interpretation_confidence=1.0,
                extractor_version="tests.memory-pack-analysis/1",
                contract_version="relationship-turn-adjudication-v1",
                rule_version="tests-rule/1",
                policy_version="tests-policy/1",
            )
        )
        pack.relationship_adjudications.append(record)
        before = pack.to_dict()

        with self.assertRaisesRegex(
            ValueError,
            "MemoryPack accepted persisted-Turn adjudication requires evidence",
        ):
            validate_persisted_turn_adjudication_sources(
                pack,
                (record,),
                relationship_id,
            )

        self.assertEqual(pack.to_dict(), before)

    def test_relationship_processing_structure_is_deterministic_and_storage_free(self) -> None:
        pack = self._fixture_pack()
        before = pack.to_dict()

        first = analyze_relationship_processing_pack_structure(
            pack,
            pack.relationship.relationship_id,
        )
        second = analyze_relationship_processing_pack_structure(
            pack,
            pack.relationship.relationship_id,
        )

        self.assertEqual(first, second)
        self.assertEqual(pack.to_dict(), before)
        self.assertEqual(
            tuple(first.direct_event_order),
            tuple(pack.relationship_direct_event_ids),
        )
        source_key = (
            pack.turn_records[0].turn_id,
            pack.turn_records[0].source_revision,
        )
        self.assertIs(first.turns[source_key], pack.turn_records[0])
        with self.assertRaises(TypeError):
            first.turns[source_key] = pack.turn_records[0]

        parameters = tuple(
            inspect.signature(analyze_relationship_processing_pack_structure).parameters
        )
        self.assertEqual(parameters, ("pack", "relationship_id"))
        source = inspect.getsource(analysis_module)
        tree = ast.parse(source)
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        self.assertNotIn("storage", names)

    def test_relationship_processing_entrypoint_matches_engine_without_storage(self) -> None:
        pack = self._processing_pack()
        before = pack.to_dict()

        structure = analyze_memory_pack_relationship_processing(
            pack,
            pack.agent_id,
            pack.user_id,
        )
        validate_memory_pack_relationship_processing(
            pack,
            pack.agent_id,
            pack.user_id,
        )
        ERIIEngine._validate_relationship_processing_pack(
            pack,
            pack.agent_id,
            pack.user_id,
            None,
        )

        self.assertIsNotNone(structure)
        self.assertEqual(pack.to_dict(), before)
        self.assertEqual(
            tuple(
                inspect.signature(
                    validate_memory_pack_relationship_processing
                ).parameters
            ),
            ("pack", "target_agent", "target_user", "existing_relationship_id"),
        )

    def test_lifecycle_semantic_validation_is_pure_and_wraps_processing_failures(self) -> None:
        source = inspect.getsource(lifecycle_validation_module)
        tree = ast.parse(source)
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        forbidden_prefixes = (
            "erii.data_lifecycle",
            "erii.engine",
            "erii.lifecycle_memory_pack_import",
            "erii._engine.memory_pack_transfer",
            "erii.storage",
        )
        self.assertFalse(
            any(
                module == prefix or module.startswith(f"{prefix}.")
                for module in imported_modules
                for prefix in forbidden_prefixes
            )
        )
        self.assertEqual(
            tuple(inspect.signature(validate_memory_pack_semantic_graph).parameters),
            ("pack",),
        )

        pack = self._processing_pack()
        valid_before = pack.to_dict()
        validate_memory_pack_semantic_graph(pack)
        self.assertEqual(pack.to_dict(), valid_before)

        pack.relationship_processing_runs = []
        before = pack.to_dict()

        with self.assertRaisesRegex(
            StorageIntegrityError,
            "MemoryPack semantic graph validation failed",
        ) as raised:
            validate_memory_pack_semantic_graph(pack)

        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertIn(
            "relationship-processing-v1 adjudications require their processing runs",
            str(raised.exception.__cause__),
        )
        self.assertEqual(pack.to_dict(), before)

    def test_relationship_processing_structure_preserves_duplicate_turn_failure(self) -> None:
        pack = self._fixture_pack()
        pack.turn_records.append(pack.turn_records[0])
        before = pack.to_dict()

        with self.assertRaisesRegex(
            ValueError,
            "MemoryPack relationship processing contains duplicate Source Turns",
        ):
            analyze_relationship_processing_pack_structure(
                pack,
                pack.relationship.relationship_id,
            )

        self.assertEqual(pack.to_dict(), before)

    def test_relationship_processing_presence_preserves_failure_order_and_input(self) -> None:
        pack = self._processing_pack()
        pack.relationship_processing_runs = []
        before = pack.to_dict()

        with self.assertRaisesRegex(
            ValueError,
            "MemoryPack relationship-processing-v1 adjudications require their processing runs",
        ):
            resolve_relationship_processing_profile(pack)

        self.assertEqual(pack.to_dict(), before)
        self.assertEqual(
            tuple(inspect.signature(resolve_relationship_processing_profile).parameters),
            ("pack",),
        )

    def test_relationship_processing_replay_is_deterministic_and_storage_free(self) -> None:
        pack = self._processing_pack()
        before = pack.to_dict()
        structure = analyze_relationship_processing_pack_structure(
            pack,
            pack.relationship.relationship_id,
        )

        first = validate_relationship_processing_runs(pack, structure)
        second = validate_relationship_processing_runs(pack, structure)

        self.assertEqual(first, second)
        self.assertEqual(pack.to_dict(), before)
        self.assertEqual(
            first.original_reflection_decision_ids,
            frozenset(pack.relationship_processing_runs[0].reflection_outcome_ids),
        )
        with self.assertRaises(FrozenInstanceError):
            first.original_reflection_decision_ids = frozenset()
        self.assertEqual(
            tuple(inspect.signature(validate_relationship_processing_runs).parameters),
            ("pack", "structure"),
        )

    def test_relationship_processing_replay_preserves_tamper_failure_and_input(self) -> None:
        pack = self._processing_pack()
        original = pack.relationship_adjudications[0]
        pack.relationship_adjudications = [
            replace(
                original,
                receipt=replace(
                    original.receipt,
                    outcome=DecisionOutcome.REJECTED,
                    reason_codes=("evidence_source_not_found",),
                    evidence=(),
                    event_ids=(),
                    related_event_id=None,
                    pivotal_eligible=False,
                ),
                events=(),
            )
        ]
        pack.relationship_events = []
        original_run = pack.relationship_processing_runs[0]
        pack.relationship_processing_runs = [
            replace(
                original_run,
                outcome=RelationshipProcessingOutcome.NO_ACCEPTED_EVENTS,
                event_ids=(),
                reflection_planned=False,
                reflection_outcome_ids=(),
            )
        ]
        before = pack.to_dict()
        structure = analyze_relationship_processing_pack_structure(
            pack,
            pack.relationship.relationship_id,
        )

        with self.assertRaisesRegex(
            ValueError,
            "MemoryPack relationship adjudication does not match its frozen candidate and baseline",
        ):
            validate_relationship_processing_runs(pack, structure)

        self.assertEqual(pack.to_dict(), before)

    def test_relationship_processing_reflection_context_and_provenance_are_portable(self) -> None:
        pack = self._processing_pack()
        before = pack.to_dict()
        structure = analyze_relationship_processing_pack_structure(
            pack,
            pack.relationship.relationship_id,
        )
        context = analyze_relationship_processing_reflection_context(
            pack,
            structure,
            structure.adjudications_by_event,
        )
        run_analysis = validate_relationship_processing_runs(pack, structure)

        validate_relationship_processing_reflections(
            pack,
            structure,
            run_analysis,
            context,
        )
        validate_relationship_processing_reflections(
            pack,
            structure,
            run_analysis,
            context,
        )

        self.assertEqual(pack.to_dict(), before)
        with self.assertRaises(TypeError):
            context.manifests_by_id["new-manifest"] = object()
        self.assertEqual(
            tuple(
                inspect.signature(
                    analyze_relationship_processing_reflection_context
                ).parameters
            ),
            ("pack", "structure", "adjudications_by_event"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    validate_relationship_processing_reflections
                ).parameters
            ),
            ("pack", "structure", "run_analysis", "context"),
        )

    def test_relationship_processing_reflection_provenance_failure_preserves_input(self) -> None:
        pack = self._processing_pack()
        original = pack.persona_reflection_decisions[0]
        provenance = replace(
            original.context_provenance,
            baseline_fingerprint="0" * 64,
        )
        pack.persona_reflection_decisions = [
            replace(
                original,
                context_provenance=provenance,
                reflection_record=replace(
                    original.reflection_record,
                    context_provenance=provenance,
                ),
            )
        ]
        before = pack.to_dict()
        structure = analyze_relationship_processing_pack_structure(
            pack,
            pack.relationship.relationship_id,
        )
        context = analyze_relationship_processing_reflection_context(
            pack,
            structure,
            structure.adjudications_by_event,
        )
        run_analysis = validate_relationship_processing_runs(pack, structure)

        with self.assertRaisesRegex(
            ValueError,
            "MemoryPack persona reflection provenance does not match its Relationship Baseline",
        ):
            validate_relationship_processing_reflections(
                pack,
                structure,
                run_analysis,
                context,
            )

        self.assertEqual(pack.to_dict(), before)


if __name__ == "__main__":
    unittest.main()
