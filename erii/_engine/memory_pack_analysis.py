"""No-write analysis of portable MemoryPack structure."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Dict, List, Mapping, Optional, Sequence, Tuple
import uuid

from erii.core.consequence import RelationshipConsequenceCoordinator
from erii.core.adjudication import (
    PERSISTED_TURN_CONTRACT_VERSION,
    RULE_VERSION as RELATIONSHIP_ADJUDICATION_RULE_VERSION,
    RelationshipAdjudicator,
    relationship_adjudication_baseline_fingerprint,
)
from erii.core.evidence_authority import quarantined_agent_source_ids
from erii.core.relationship_processing import RelationshipProcessingCoordinator
from erii.core.temporal_history import TemporalHistoryValidator
from erii.models.adjudication import (
    AdjudicationRecord,
    DecisionOutcome,
    PersonaGrowthProposal,
    PersonaGrowthStatus,
    RelationshipCandidateBatch,
    SourceTurn,
)
from erii.models.consolidation import (
    PersonaReflectionDecisionRecord,
    PersonaReflectionRecord,
    PersonaReflectionRecordKind,
    ReflectionProvenanceState,
    RelationshipEventCandidatesDecision,
    RelationshipProcessingStatus,
)
from erii.models.node import MemoryType
from erii.models.pack import MemoryPack
from erii.models.persona import PersonaManifest
from erii.models.relationship import RelationshipEvent, RelationshipProfile
from erii.models.temporal import (
    OpenLoopResolution,
    OpenLoopSpec,
    PromiseConditionConfirmation,
    PromiseResolution,
)
from erii.models.turn import TurnStatus


@dataclass(frozen=True)
class MemoryPackAnalysis:
    """Immutable facts derived from a validated portable MemoryPack."""

    has_bound_archival_history: bool
    requires_exact_relationship_restore: bool


@dataclass(frozen=True)
class RelationshipProcessingPackStructure:
    """Read-only indexes for portable relationship-processing ledgers."""

    turns: Mapping[Tuple[str, str], SourceTurn]
    events_by_id: Mapping[str, RelationshipEvent]
    adjudications_by_event: Mapping[str, Tuple[AdjudicationRecord, ...]]
    adjudications_by_id: Mapping[str, AdjudicationRecord]
    event_ids: frozenset[str]
    adjudication_ids: frozenset[str]
    reflection_decisions_by_id: Mapping[str, PersonaReflectionDecisionRecord]
    reflection_decision_ids: frozenset[str]
    top_level_events_by_id: Mapping[str, RelationshipEvent]
    direct_event_order: Tuple[str, ...]
    direct_events_by_id: Mapping[str, RelationshipEvent]


@dataclass(frozen=True)
class RelationshipProcessingRunAnalysis:
    """Frozen facts required by later reflection-provenance validation."""

    original_reflection_decision_ids: frozenset[str]


@dataclass(frozen=True)
class RelationshipProcessingReflectionContext:
    """Read-only portable context resolved before reflection validation."""

    manifests_by_id: Mapping[str, PersonaManifest]
    growth_by_identity: Mapping[Tuple[str, int], PersonaGrowthProposal]
    adjudications_by_event: Mapping[str, Tuple[AdjudicationRecord, ...]]


def resolve_relationship_processing_profile(
    pack: MemoryPack,
) -> Optional[RelationshipProfile]:
    """Resolve portable processing presence before target identity checks."""
    runs = pack.relationship_processing_runs
    decisions = pack.persona_reflection_decisions
    processing_receipt_ids = {
        record.receipt.decision_id
        for record in pack.relationship_adjudications
        if record.receipt.contract_version == "relationship-processing-v1"
    }
    if processing_receipt_ids and not runs:
        raise ValueError(
            "MemoryPack relationship-processing-v1 adjudications require "
            "their processing runs"
        )
    if not runs and not decisions:
        return None
    if pack.relationship is None:
        raise ValueError(
            "MemoryPack relationship processing requires a relationship profile"
        )
    return pack.relationship


def analyze_memory_pack_relationship_processing(
    pack: MemoryPack,
    target_agent: str,
    target_user: str,
    existing_relationship_id: Optional[str] = None,
) -> Optional[RelationshipProcessingPackStructure]:
    """Validate portable processing identity and return its frozen structure.

    Target journal reads and conflicts deliberately remain outside this
    Interface.  Callers can therefore reuse the authoritative portable checks
    without constructing an Engine or opening Storage.
    """
    relationship = resolve_relationship_processing_profile(pack)
    if relationship is None:
        return None
    if (
        pack.agent_id != target_agent
        or pack.user_id != target_user
        or relationship.agent_id != target_agent
        or relationship.user_id != target_user
    ):
        raise ValueError(
            "MemoryPack relationship processing cannot be copied to another "
            "Agent x User"
        )
    if (
        existing_relationship_id is not None
        and existing_relationship_id != relationship.relationship_id
    ):
        raise ValueError(
            "MemoryPack relationship processing requires exact relationship restore"
        )
    return analyze_relationship_processing_pack_structure(
        pack,
        relationship.relationship_id,
    )


def analyze_relationship_processing_pack_structure(
    pack: MemoryPack,
    relationship_id: str,
) -> RelationshipProcessingPackStructure:
    """Build and validate the portable relationship-processing index closure."""
    turns = {
        (record.turn_id, record.source_revision): record
        for record in pack.turn_records
    }
    if len(turns) != len(pack.turn_records):
        raise ValueError(
            "MemoryPack relationship processing contains duplicate Source Turns"
        )

    events_by_id: Dict[str, RelationshipEvent] = {}
    adjudications_by_event: Dict[str, List[AdjudicationRecord]] = {}
    adjudications_by_id: Dict[str, AdjudicationRecord] = {}
    for event in [
        *pack.relationship_events,
        *(
            event
            for record in pack.relationship_adjudications
            for event in record.events
        ),
    ]:
        if event.relationship_id != relationship_id:
            raise ValueError(
                "MemoryPack relationship processing event crosses "
                "relationship boundaries"
            )
        existing_event = events_by_id.get(event.event_id)
        if existing_event is not None and not existing_event.same_payload_as(event):
            raise ValueError(
                "MemoryPack relationship processing contains conflicting "
                "event payloads"
            )
        events_by_id[event.event_id] = event

    for record in pack.relationship_adjudications:
        receipt = record.receipt
        if receipt.relationship_id != relationship_id:
            raise ValueError(
                "MemoryPack relationship adjudication crosses "
                "relationship boundaries"
            )
        existing_record = adjudications_by_id.get(receipt.decision_id)
        if existing_record is not None and existing_record != record:
            raise ValueError(
                "MemoryPack contains conflicting relationship "
                "adjudication decisions"
            )
        if existing_record is not None:
            raise ValueError(
                "MemoryPack contains duplicate relationship "
                "adjudication decisions"
            )
        adjudications_by_id[receipt.decision_id] = record
        for event in record.events:
            adjudications_by_event.setdefault(event.event_id, []).append(record)

    reflection_decisions_by_id = {
        decision.decision_id: decision
        for decision in pack.persona_reflection_decisions
    }
    if len(reflection_decisions_by_id) != len(pack.persona_reflection_decisions):
        raise ValueError(
            "MemoryPack contains duplicate persona reflection decision IDs"
        )

    top_level_events_by_id = {
        event.event_id: event for event in pack.relationship_events
    }
    direct_event_order = tuple(pack.relationship_direct_event_ids)
    if pack.relationship_processing_runs and (
        len(direct_event_order) != len(set(direct_event_order))
        or any(event_id not in top_level_events_by_id for event_id in direct_event_order)
        or (
            set(top_level_events_by_id)
            - set(adjudications_by_event)
            - set(direct_event_order)
        )
    ):
        raise ValueError(
            "MemoryPack relationship processing requires the exact "
            "direct-event journal order"
        )

    return RelationshipProcessingPackStructure(
        turns=MappingProxyType(turns),
        events_by_id=MappingProxyType(events_by_id),
        adjudications_by_event=MappingProxyType(
            {key: tuple(value) for key, value in adjudications_by_event.items()}
        ),
        adjudications_by_id=MappingProxyType(adjudications_by_id),
        event_ids=frozenset(events_by_id),
        adjudication_ids=frozenset(adjudications_by_id),
        reflection_decisions_by_id=MappingProxyType(reflection_decisions_by_id),
        reflection_decision_ids=frozenset(reflection_decisions_by_id),
        top_level_events_by_id=MappingProxyType(top_level_events_by_id),
        direct_event_order=direct_event_order,
        direct_events_by_id=MappingProxyType(
            {
                event_id: top_level_events_by_id[event_id]
                for event_id in direct_event_order
            }
        ),
    )


def validate_memory_pack_relationship_processing(
    pack: MemoryPack,
    target_agent: str,
    target_user: str,
    existing_relationship_id: Optional[str] = None,
) -> None:
    """Validate the complete portable relationship-processing graph."""
    structure = analyze_memory_pack_relationship_processing(
        pack,
        target_agent,
        target_user,
        existing_relationship_id,
    )
    if structure is None:
        return
    reflection_context = analyze_relationship_processing_reflection_context(
        pack,
        structure,
        structure.adjudications_by_event,
    )
    run_analysis = validate_relationship_processing_runs(pack, structure)
    validate_relationship_processing_reflections(
        pack,
        structure,
        run_analysis,
        reflection_context,
    )


def validate_relationship_processing_runs(
    pack: MemoryPack,
    structure: RelationshipProcessingPackStructure,
) -> RelationshipProcessingRunAnalysis:
    """Replay and validate portable processing runs without target state."""
    if pack.relationship is None:
        raise ValueError(
            "MemoryPack relationship processing requires a relationship profile"
        )
    relationship = pack.relationship
    relationship_id = relationship.relationship_id
    processing_receipt_ids = {
        record.receipt.decision_id
        for record in pack.relationship_adjudications
        if record.receipt.contract_version == "relationship-processing-v1"
    }
    relationship_adjudicator = object.__new__(RelationshipAdjudicator)
    run_ids = set()
    run_identities = set()
    original_reflection_decision_ids = set()
    attached_processing_receipt_ids = set()
    for run in pack.relationship_processing_runs:
        if run.relationship_id != relationship_id:
            raise ValueError(
                "MemoryPack relationship processing crosses relationship boundaries"
            )
        source_key = (run.source_turn_id, run.source_revision)
        source_turn = structure.turns.get(source_key)
        if source_turn is None or source_turn.status != TurnStatus.COMPLETED:
            raise ValueError(
                "MemoryPack relationship processing requires its exact completed "
                "Source Turn"
            )
        expected_processing_id = RelationshipProcessingCoordinator.processing_id(
            relationship,
            source_turn,
            processing_mode=run.processing_mode,
            reprocessing_id=run.reprocessing_id,
        )
        if run.processing_id != expected_processing_id:
            raise ValueError(
                "MemoryPack relationship processing ID does not match "
                "its relationship, Source Turn, and processing identity"
            )
        if (
            run.rule_version != RELATIONSHIP_ADJUDICATION_RULE_VERSION
            or run.contract_version != "relationship-processing-v1"
        ):
            raise ValueError(
                "MemoryPack relationship processing uses an unsupported "
                "rule or contract version"
            )
        if not set(run.event_ids).issubset(structure.event_ids):
            raise ValueError(
                "MemoryPack processing run references relationship events "
                "outside the pack"
            )
        if not set(run.decision_ids).issubset(structure.adjudication_ids):
            raise ValueError(
                "MemoryPack processing run references adjudications outside the pack"
            )
        if not set(run.reflection_outcome_ids).issubset(
            structure.reflection_decision_ids
        ):
            raise ValueError(
                "MemoryPack processing run references reflection outcomes "
                "outside the pack"
            )
        if (
            run.adjudication_base_direct_event_count
            > len(structure.direct_event_order)
            or run.adjudication_base_decision_count
            > len(pack.relationship_adjudications)
        ):
            raise ValueError(
                "MemoryPack processing run adjudication baseline exceeds "
                "its append-only journals"
            )
        baseline_direct_events = tuple(
            structure.direct_events_by_id[event_id]
            for event_id in structure.direct_event_order[
                : run.adjudication_base_direct_event_count
            ]
        )
        baseline_adjudications = tuple(
            pack.relationship_adjudications[
                : run.adjudication_base_decision_count
            ]
        )
        expected_baseline_fingerprint = (
            relationship_adjudication_baseline_fingerprint(
                baseline_direct_events,
                baseline_adjudications,
            )
        )
        if run.adjudication_base_fingerprint != expected_baseline_fingerprint:
            raise ValueError(
                "MemoryPack processing run adjudication baseline does not "
                "match its frozen journal prefixes"
            )
        if isinstance(run.frozen_decision, RelationshipEventCandidatesDecision):
            source = RelationshipProcessingCoordinator._source_turn(
                source_turn,
                run,
            )
            candidates = RelationshipCandidateBatch(
                candidates=list(run.frozen_decision.candidates),
            )
            expected_decision_ids = [
                RelationshipAdjudicator._decision_id(
                    relationship,
                    source,
                    candidate,
                )
                for candidate in run.frozen_decision.candidates
            ]
            actual_records = {
                decision_id: structure.adjudications_by_id[decision_id]
                for decision_id in expected_decision_ids
                if decision_id in structure.adjudications_by_id
            }
            canonical = None
            if actual_records:
                try:
                    canonical, resolution_order = (
                        relationship_adjudicator._reconstruct_batch_records(
                            relationship,
                            source,
                            candidates,
                            baseline_direct_events=baseline_direct_events,
                            baseline_adjudications=baseline_adjudications,
                            timestamp_hints=actual_records,
                            quarantined_source_ids=(
                                quarantined_agent_source_ids(source_turn)
                            ),
                        )
                    )
                except ValueError as exc:
                    raise ValueError(
                        "MemoryPack processing adjudication cannot be "
                        "replayed from its frozen candidate and baseline"
                    ) from exc
                canonical_by_id = {
                    record.receipt.decision_id: record
                    for record in canonical.records
                }
                present_resolution_order = tuple(
                    decision_id
                    for decision_id in resolution_order
                    if decision_id in actual_records
                )
                if present_resolution_order != resolution_order[: len(actual_records)]:
                    raise ValueError(
                        "MemoryPack partial processing adjudications are "
                        "not a committed decision-journal prefix"
                    )
                for decision_id, actual_record in actual_records.items():
                    expected_record = canonical_by_id[decision_id]
                    if expected_record.to_dict() != actual_record.to_dict():
                        raise ValueError(
                            "MemoryPack relationship adjudication does "
                            "not match its frozen candidate and baseline"
                        )
                attached_processing_receipt_ids.update(
                    set(actual_records) & processing_receipt_ids
                )
            if run.decision_ids:
                if tuple(run.decision_ids) != tuple(expected_decision_ids):
                    raise ValueError(
                        "MemoryPack processing run does not contain exactly "
                        "one adjudication for each frozen candidate"
                    )
                assert canonical is not None
                expected_event_ids = []
                for expected_record in canonical.records:
                    if expected_record.receipt.outcome == DecisionOutcome.ACCEPTED:
                        expected_event_ids.extend(
                            event.event_id for event in expected_record.events
                        )
                if tuple(run.event_ids) != tuple(expected_event_ids):
                    raise ValueError(
                        "MemoryPack processing run event IDs do not match "
                        "its accepted adjudications"
                    )
            elif run.status not in {
                RelationshipProcessingStatus.EXTRACTED,
                RelationshipProcessingStatus.FAILED,
            }:
                raise ValueError(
                    "MemoryPack advanced processing run is missing "
                    "adjudication decisions"
                )
        for reflection_outcome_id in run.reflection_outcome_ids:
            reflection_outcome = structure.reflection_decisions_by_id[
                reflection_outcome_id
            ]
            expected_reflection_outcome_id = (
                RelationshipProcessingCoordinator._reflection_decision_id(
                    run,
                    reflection_outcome.event_id,
                    PersonaReflectionRecordKind.REFLECTION,
                    None,
                )
            )
            if (
                reflection_outcome_id != expected_reflection_outcome_id
                or reflection_outcome.source_turn_id != run.source_turn_id
                or reflection_outcome.source_revision != run.source_revision
                or reflection_outcome.event_id not in run.event_ids
                or reflection_outcome.record_kind
                != PersonaReflectionRecordKind.REFLECTION
                or reflection_outcome.target_reflection_id is not None
            ):
                raise ValueError(
                    "MemoryPack processing run reflection outcome does not "
                    "belong to that run"
                )
            original_reflection_decision_ids.add(reflection_outcome_id)
        if (
            run.status == RelationshipProcessingStatus.COMPLETED
            and run.reflection_planned
            and {
                structure.reflection_decisions_by_id[item].event_id
                for item in run.reflection_outcome_ids
            }
            != set(run.event_ids)
        ):
            raise ValueError(
                "MemoryPack completed processing run is missing a "
                "reflection outcome for an accepted event"
            )
        identity = (
            run.source_turn_id,
            run.source_revision,
            run.processing_identity,
        )
        if run.processing_id in run_ids or identity in run_identities:
            raise ValueError(
                "MemoryPack contains duplicate relationship processing identities"
            )
        run_ids.add(run.processing_id)
        run_identities.add(identity)

    if processing_receipt_ids != attached_processing_receipt_ids:
        raise ValueError(
            "MemoryPack relationship-processing-v1 adjudications are not "
            "attached to their exact processing runs"
        )
    return RelationshipProcessingRunAnalysis(
        original_reflection_decision_ids=frozenset(
            original_reflection_decision_ids
        ),
    )


def analyze_relationship_processing_reflection_context(
    pack: MemoryPack,
    structure: RelationshipProcessingPackStructure,
    adjudications_by_event: Mapping[str, Sequence[AdjudicationRecord]],
) -> RelationshipProcessingReflectionContext:
    """Validate reflection dependencies and freeze an explicit history context."""
    if pack.relationship is None:
        raise ValueError(
            "MemoryPack relationship processing requires a relationship profile"
        )
    relationship_id = pack.relationship.relationship_id
    manifests_by_id: Dict[str, PersonaManifest] = {}
    for manifest in pack.persona_manifests:
        if manifest.manifest_id in manifests_by_id:
            raise ValueError("MemoryPack contains duplicate Persona Manifest IDs")
        manifests_by_id[manifest.manifest_id] = manifest

    growth_by_identity: Dict[Tuple[str, int], PersonaGrowthProposal] = {}
    growth_proposal_ids = set()
    for proposal in pack.persona_growth_proposals:
        identity = (proposal.proposal_id, proposal.revision)
        if identity in growth_by_identity or proposal.proposal_id in growth_proposal_ids:
            raise ValueError("MemoryPack contains duplicate Persona Growth identities")
        if proposal.relationship_id != relationship_id:
            raise ValueError("MemoryPack Persona Growth crosses relationship boundaries")
        if not set(proposal.supporting_event_ids).issubset(structure.event_ids):
            raise ValueError(
                "MemoryPack Persona Growth references events outside the pack"
            )
        growth_by_identity[identity] = proposal
        growth_proposal_ids.add(proposal.proposal_id)

    return RelationshipProcessingReflectionContext(
        manifests_by_id=MappingProxyType(manifests_by_id),
        growth_by_identity=MappingProxyType(growth_by_identity),
        adjudications_by_event=MappingProxyType(
            {
                event_id: tuple(records)
                for event_id, records in adjudications_by_event.items()
            }
        ),
    )


def validate_relationship_processing_reflections(
    pack: MemoryPack,
    structure: RelationshipProcessingPackStructure,
    run_analysis: RelationshipProcessingRunAnalysis,
    context: RelationshipProcessingReflectionContext,
) -> None:
    """Validate portable reflection identity, evidence, and provenance closure."""
    if pack.relationship is None:
        raise ValueError(
            "MemoryPack relationship processing requires a relationship profile"
        )
    relationship = pack.relationship
    relationship_id = relationship.relationship_id
    reflection_identities = set()
    seen_reflection_ids = set()
    seen_reflections_by_id: Dict[str, PersonaReflectionRecord] = {}
    for decision in pack.persona_reflection_decisions:
        if decision.relationship_id != relationship_id:
            raise ValueError(
                "MemoryPack persona reflections cross relationship boundaries"
            )
        source_turn = structure.turns.get(
            (decision.source_turn_id, decision.source_revision)
        )
        if source_turn is None or source_turn.status != TurnStatus.COMPLETED:
            raise ValueError(
                "MemoryPack persona reflection requires its exact completed "
                "Source Turn"
            )
        if decision.event_id not in structure.event_ids:
            raise ValueError(
                "MemoryPack persona reflection references an event outside the pack"
            )
        if (
            decision.record_kind == PersonaReflectionRecordKind.REFLECTION
            and decision.decision_id
            not in run_analysis.original_reflection_decision_ids
        ):
            raise ValueError(
                "MemoryPack original persona reflection is not attached "
                "to its processing run"
            )
        if decision.record_kind in {
            PersonaReflectionRecordKind.CORRECTION,
            PersonaReflectionRecordKind.REINTERPRETATION,
        }:
            expected_decision_id = (
                RelationshipProcessingCoordinator._explicit_interpretation_decision_id(
                    relationship_id,
                    decision.target_reflection_id,
                    decision.interpretation_id,
                    decision.record_kind,
                )
            )
            if decision.decision_id != expected_decision_id:
                raise ValueError(
                    "MemoryPack reflection interpretation ID does not "
                    "match its stable identity"
                )
        if (
            decision.reflection_record is not None
            and decision.record_kind != PersonaReflectionRecordKind.LEGACY
        ):
            expected_reflection_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"erii:{decision.decision_id}:persona-reflection",
                )
            )
            if decision.reflection_record.reflection_id != expected_reflection_id:
                raise ValueError(
                    "MemoryPack persona reflection ID does not match its decision"
                )
        provenance = decision.context_provenance
        if not set(provenance.prior_event_ids).issubset(structure.event_ids):
            raise ValueError(
                "MemoryPack persona reflection provenance references a "
                "missing relationship event"
            )
        if decision.event_id in provenance.prior_event_ids:
            raise ValueError(
                "MemoryPack persona reflection provenance cannot list its "
                "current event as prior context"
            )
        if provenance.provenance_state == ReflectionProvenanceState.COMPLETE:
            if (
                provenance.source_turn_id != decision.source_turn_id
                or provenance.source_revision != decision.source_revision
            ):
                raise ValueError(
                    "MemoryPack persona reflection provenance does not match "
                    "its Source Turn"
                )

            matching_adjudications = context.adjudications_by_event.get(
                decision.event_id,
                (),
            )
            if len(matching_adjudications) != 1:
                raise ValueError(
                    "MemoryPack complete persona reflection requires exactly "
                    "one accepted adjudication"
                )
            adjudication = matching_adjudications[0]
            receipt = adjudication.receipt
            if (
                receipt.outcome != DecisionOutcome.ACCEPTED
                or provenance.decision_id != receipt.decision_id
                or receipt.source_turn_id != decision.source_turn_id
                or receipt.source_revision != decision.source_revision
            ):
                raise ValueError(
                    "MemoryPack persona reflection provenance is not bound "
                    "to its accepted adjudication"
                )

            evidence_by_id = {item.evidence_id: item for item in receipt.evidence}
            if len(evidence_by_id) != len(receipt.evidence):
                raise ValueError(
                    "MemoryPack adjudication contains duplicate evidence IDs"
                )
            if (
                not provenance.evidence_ids
                or not set(provenance.evidence_ids).issubset(evidence_by_id)
            ):
                raise ValueError(
                    "MemoryPack persona reflection provenance is not bound "
                    "to its adjudication evidence"
                )

            transcript_messages = [
                source_turn.transcript.user_message,
                source_turn.transcript.agent_message,
            ]
            source_messages = {
                item.message_id: item
                for item in transcript_messages
                if item is not None
            }
            for evidence_id in provenance.evidence_ids:
                evidence = evidence_by_id[evidence_id]
                source_message = source_messages.get(evidence.source_id)
                expected_message_hash = (
                    hashlib.sha256(source_message.content.encode("utf-8")).hexdigest()
                    if source_message is not None
                    else None
                )
                expected_evidence_id = (
                    str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            (
                                f"erii:{relationship_id}:evidence:"
                                f"{evidence.source_id}:"
                                f"{evidence.source_revision}:"
                                f"{expected_message_hash}:"
                                f"{evidence.start}:{evidence.end}"
                            ),
                        )
                    )
                    if expected_message_hash is not None
                    else None
                )
                if (
                    source_message is None
                    or evidence.source_revision != decision.source_revision
                    or evidence.role.value != source_message.role.value
                    or evidence.message_sha256 != expected_message_hash
                    or evidence.end > len(source_message.content)
                    or source_message.content[evidence.start : evidence.end]
                    != evidence.quote
                    or evidence.occurred_at != source_message.recorded_at
                    or evidence.evidence_id != expected_evidence_id
                ):
                    raise ValueError(
                        "MemoryPack persona reflection cites invalid Source "
                        "Turn evidence"
                    )

            blueprint = relationship.blueprint
            if (
                provenance.blueprint_id != blueprint.blueprint_id
                or provenance.blueprint_sha256 != blueprint.source_sha256
                or provenance.blueprint_revision != blueprint.revision
            ):
                raise ValueError(
                    "MemoryPack persona reflection provenance does not match "
                    "its Character Blueprint"
                )
            if provenance.baseline_fingerprint != _portable_fingerprint(
                relationship.baseline.to_dict()
            ):
                raise ValueError(
                    "MemoryPack persona reflection provenance does not match "
                    "its Relationship Baseline"
                )

            if provenance.manifest_id is not None:
                manifest = context.manifests_by_id.get(provenance.manifest_id)
                if (
                    manifest is None
                    or relationship.manifest_id != provenance.manifest_id
                    or provenance.manifest_revision != manifest.approved_revision
                    or provenance.manifest_fingerprint
                    != manifest.content_fingerprint
                    or manifest.blueprint_id != blueprint.blueprint_id
                    or manifest.blueprint_revision != blueprint.revision
                    or manifest.source_sha256 != blueprint.source_sha256
                ):
                    raise ValueError(
                        "MemoryPack persona reflection provenance does not "
                        "match its Persona Manifest"
                    )

            for reference in provenance.approved_growth:
                proposal = context.growth_by_identity.get(
                    (reference.proposal_id, reference.revision)
                )
                if (
                    proposal is None
                    or proposal.relationship_id != relationship_id
                    or proposal.status != PersonaGrowthStatus.APPROVED
                    or reference.content_fingerprint
                    != _portable_fingerprint(proposal.to_dict())
                    or reference.approved_at != proposal.decided_at
                    or not set(proposal.supporting_event_ids).issubset(
                        structure.event_ids
                    )
                ):
                    raise ValueError(
                        "MemoryPack persona reflection provenance does not "
                        "match its approved Persona Growth"
                    )
        if decision.target_reflection_id is not None:
            target_reflection = seen_reflections_by_id.get(
                decision.target_reflection_id
            )
            if target_reflection is None:
                raise ValueError(
                    "MemoryPack correction or reinterpretation precedes its target"
                )
            target_provenance = target_reflection.context_provenance
            if (
                decision.event_id != target_reflection.event_id
                or decision.source_turn_id != target_provenance.source_turn_id
                or decision.source_revision != target_provenance.source_revision
                or provenance.decision_id != target_provenance.decision_id
                or provenance.evidence_ids != target_provenance.evidence_ids
                or provenance.blueprint_id != target_provenance.blueprint_id
                or provenance.blueprint_sha256
                != target_provenance.blueprint_sha256
                or provenance.blueprint_revision
                != target_provenance.blueprint_revision
                or provenance.manifest_id != target_provenance.manifest_id
                or provenance.manifest_revision
                != target_provenance.manifest_revision
                or provenance.manifest_fingerprint
                != target_provenance.manifest_fingerprint
                or provenance.baseline_fingerprint
                != target_provenance.baseline_fingerprint
                or decision.target_reflection_id
                not in provenance.prior_reflection_ids
            ):
                raise ValueError(
                    "MemoryPack correction or reinterpretation does not "
                    "share its target reflection's event and source binding"
                )
        if not set(provenance.prior_reflection_ids).issubset(seen_reflection_ids):
            raise ValueError(
                "MemoryPack persona reflection provenance references a later "
                "or missing reflection"
            )
        if decision.interpretation_identity in reflection_identities:
            raise ValueError(
                "MemoryPack contains duplicate persona reflection identities"
            )
        reflection_identities.add(decision.interpretation_identity)
        if decision.reflection_record is not None:
            reflection_id = decision.reflection_record.reflection_id
            if reflection_id in seen_reflection_ids:
                raise ValueError(
                    "MemoryPack contains duplicate persona reflection records"
                )
            seen_reflection_ids.add(reflection_id)
            seen_reflections_by_id[reflection_id] = decision.reflection_record


def _portable_fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_memory_pack_node_types(pack: MemoryPack) -> None:
    """Reject non-persistable command directives before target resolution."""
    if any(node.node_type == MemoryType.INSTRUCTION for node in pack.nodes):
        raise ValueError(
            "MemoryPack instruction nodes cannot be imported into long-term memory"
        )


def validate_memory_pack_relationship_consequences(pack: MemoryPack) -> None:
    """Validate the portable consequence causal graph without target state."""
    if not (
        pack.relationship_consequences
        or pack.narrative_tension_links
    ):
        return
    if pack.relationship is None:
        raise ValueError(
            "MemoryPack relationship consequences require a relationship profile"
        )
    relationship_id = pack.relationship.relationship_id
    RelationshipConsequenceCoordinator.validate_journal(
        relationship_id,
        pack.relationship_consequences,
        pack.narrative_tension_links,
        pack.turn_records,
        pack.relationship_adjudications,
    )

    accepted_events: Dict[str, RelationshipEvent] = {}
    for record in pack.relationship_adjudications:
        for event in record.events:
            existing = accepted_events.get(event.event_id)
            if existing is not None and not existing.same_payload_as(event):
                raise ValueError(
                    "MemoryPack consequence sources contain conflicting "
                    "accepted event identities"
                )
            accepted_events[event.event_id] = event
    complete_events: Dict[str, RelationshipEvent] = {}
    for event in pack.relationship_events:
        existing = complete_events.get(event.event_id)
        if existing is not None and not existing.same_payload_as(event):
            raise ValueError(
                "MemoryPack relationship history contains conflicting "
                "event identities"
            )
        complete_events[event.event_id] = event
    source_event_ids = {
        item.source_event_id for item in pack.relationship_consequences
    } | {
        item.source_event_id for item in pack.narrative_tension_links
    }
    for event_id in source_event_ids:
        accepted = accepted_events.get(event_id)
        complete = complete_events.get(event_id)
        if accepted is None or complete is None:
            raise ValueError(
                "MemoryPack relationship consequence source event is missing "
                "from accepted complete history"
            )
        if not accepted.same_payload_as(complete):
            raise ValueError(
                "MemoryPack relationship consequence source event conflicts "
                "with complete history"
            )


def validate_memory_pack_turn_records(pack: MemoryPack) -> None:
    """Validate portable Turn identity and relationship closure."""
    if not pack.turn_records:
        return
    if pack.relationship is None:
        raise ValueError("MemoryPack turn records require a relationship profile")
    if (
        pack.relationship.agent_id != pack.agent_id
        or pack.relationship.user_id != pack.user_id
    ):
        raise ValueError(
            "MemoryPack source transcripts cannot be copied to another Agent x User"
        )
    seen_turn_ids = set()
    for record in pack.turn_records:
        if record.relationship_id != pack.relationship.relationship_id:
            raise ValueError(
                "MemoryPack turn record belongs to a different relationship"
            )
        if record.turn_id in seen_turn_ids:
            raise ValueError(
                f"MemoryPack contains duplicate turn_id {record.turn_id!r}"
            )
        seen_turn_ids.add(record.turn_id)


def validate_persisted_turn_adjudication_sources(
    pack: MemoryPack,
    records: Sequence[AdjudicationRecord],
    relationship_id: str,
) -> None:
    """Validate portable persisted-Turn evidence after target resolution."""
    turns = {
        (turn.turn_id, turn.source_revision): turn
        for turn in pack.turn_records
    }
    quarantine_reason = (
        "continuity_exception_agent_evidence_quarantined",
    )
    for record in records:
        receipt = record.receipt
        if receipt.relationship_id != relationship_id:
            raise ValueError(
                "MemoryPack persisted-Turn adjudication crosses relationship boundaries"
            )
        turn = turns.get((receipt.source_turn_id, receipt.source_revision))
        if turn is None or turn.status != TurnStatus.COMPLETED:
            raise ValueError(
                "MemoryPack persisted-Turn adjudication requires its exact "
                "completed Source Turn"
            )
        messages = [turn.transcript.user_message]
        if turn.transcript.agent_message is not None:
            messages.append(turn.transcript.agent_message)
        messages_by_id = {message.message_id: message for message in messages}
        if len(messages_by_id) != len(messages):
            raise ValueError(
                "MemoryPack persisted-Turn adjudication has ambiguous source messages"
            )

        evidence_ids = set()
        for evidence in receipt.evidence:
            message = messages_by_id.get(evidence.source_id)
            if (
                message is None
                or evidence.source_revision != turn.source_revision
                or evidence.role.value != message.role.value
                or evidence.message_sha256
                != hashlib.sha256(message.content.encode("utf-8")).hexdigest()
                or not 0 <= evidence.start < evidence.end <= len(message.content)
                or message.content[evidence.start : evidence.end] != evidence.quote
                or evidence.occurred_at != message.recorded_at
            ):
                raise ValueError(
                    "MemoryPack persisted-Turn adjudication evidence does not "
                    "match its Source Turn"
                )
            expected_evidence_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"erii:{relationship_id}:evidence:"
                        f"{evidence.source_id}:{evidence.source_revision}:"
                        f"{evidence.message_sha256}:{evidence.start}:{evidence.end}"
                    ),
                )
            )
            if (
                evidence.evidence_id != expected_evidence_id
                or evidence.evidence_id in evidence_ids
            ):
                raise ValueError(
                    "MemoryPack persisted-Turn adjudication evidence identity "
                    "is invalid"
                )
            evidence_ids.add(evidence.evidence_id)

        if (
            receipt.outcome in (DecisionOutcome.ACCEPTED, DecisionOutcome.CORROBORATED)
            and not receipt.evidence
        ):
            raise ValueError(
                "MemoryPack accepted persisted-Turn adjudication requires evidence"
            )
        quarantined_ids = quarantined_agent_source_ids(turn)
        cites_quarantined_agent = any(
            evidence.source_id in quarantined_ids
            for evidence in receipt.evidence
        )
        if cites_quarantined_agent and not (
            receipt.outcome == DecisionOutcome.REJECTED
            and tuple(receipt.reason_codes) == quarantine_reason
            and not receipt.event_ids
            and not record.events
            and not receipt.pivotal_eligible
        ):
            raise ValueError(
                "MemoryPack persisted-Turn adjudication with quarantined Agent "
                "evidence must retain its a8 rejection"
            )
        if (
            tuple(receipt.reason_codes) == quarantine_reason
            and not cites_quarantined_agent
        ):
            raise ValueError(
                "MemoryPack persisted-Turn adjudication quarantine reason lacks "
                "quarantined Agent evidence"
            )


def validate_memory_pack_persisted_turn_adjudications(
    pack: MemoryPack,
    target_agent: str,
    target_user: str,
    existing_relationship_id: Optional[str],
) -> None:
    """Revalidate direct adjudications against their portable Source Turns."""
    turns = {
        (turn.turn_id, turn.source_revision): turn
        for turn in pack.turn_records
    }
    if len(turns) != len(pack.turn_records):
        raise ValueError(
            "MemoryPack persisted-Turn adjudications contain duplicate Source Turns"
        )
    records = tuple(
        record
        for record in pack.relationship_adjudications
        if (
            record.receipt.contract_version
            == PERSISTED_TURN_CONTRACT_VERSION
            or (
                record.receipt.contract_version
                != "relationship-processing-v1"
                and (
                    record.receipt.source_turn_id,
                    record.receipt.source_revision,
                )
                in turns
            )
        )
    )
    if not records:
        return
    relationship = pack.relationship
    if relationship is None:
        raise ValueError(
            "MemoryPack persisted-Turn adjudications require a relationship profile"
        )
    if (
        pack.agent_id != target_agent
        or pack.user_id != target_user
        or relationship.agent_id != target_agent
        or relationship.user_id != target_user
        or (
            existing_relationship_id is not None
            and existing_relationship_id != relationship.relationship_id
        )
    ):
        raise ValueError(
            "MemoryPack persisted-Turn adjudications require exact relationship restore"
        )

    validate_persisted_turn_adjudication_sources(
        pack,
        records,
        relationship.relationship_id,
    )


def analyze_memory_pack(pack: MemoryPack) -> MemoryPackAnalysis:
    """Validate portable structure and return deterministic no-write facts.

    Target identity, target-state conflicts, remapping, locking, and writes are
    deliberately outside this Interface.
    """
    validate_memory_pack_node_types(pack)
    _validate_temporal_pack(pack)
    _validate_persona_growth_pack(pack)
    has_bound_archival_history = bool(
        pack.timeline_entries
        or pack.archival_ledger
        or pack.turn_records
        or pack.relationship_processing_runs
        or pack.persona_reflection_decisions
        or pack.relationship_consequences
        or pack.narrative_tension_links
        or any(
            node.source_turn_id is not None
            or node.source_archival_id is not None
            for node in pack.nodes
        )
    )
    relationship_id = (
        pack.relationship.relationship_id if pack.relationship is not None else None
    )
    return MemoryPackAnalysis(
        has_bound_archival_history=has_bound_archival_history,
        requires_exact_relationship_restore=bool(
            has_bound_archival_history and relationship_id is not None
        ),
    )


def _temporal_reference_ids(event: RelationshipEvent) -> Sequence[str]:
    payload = event.temporal_payload
    if isinstance(payload, PromiseConditionConfirmation):
        return (payload.promise_event_id,)
    if isinstance(payload, PromiseResolution):
        references = [payload.promise_event_id]
        if payload.superseding_promise_event_id is not None:
            references.append(payload.superseding_promise_event_id)
        return tuple(references)
    if isinstance(payload, OpenLoopResolution):
        references = [payload.open_loop_event_id]
        if payload.superseding_open_loop_event_id is not None:
            references.append(payload.superseding_open_loop_event_id)
        return tuple(references)
    return ()


def _validate_temporal_pack(pack: MemoryPack) -> tuple[RelationshipEvent, ...]:
    ordered_events = []
    by_id: Dict[str, RelationshipEvent] = {}
    for event in [
        *pack.relationship_events,
        *(
            accepted
            for record in pack.relationship_adjudications
            for accepted in record.events
        ),
    ]:
        existing = by_id.get(event.event_id)
        if existing is not None:
            if not existing.same_payload_as(event):
                raise ValueError(
                    f"MemoryPack event_id {event.event_id!r} has conflicting payloads"
                )
            continue
        by_id[event.event_id] = event
        ordered_events.append(event)

    temporal_events = [
        event for event in ordered_events if event.temporal_payload is not None
    ]
    if not temporal_events:
        return tuple(ordered_events)
    if pack.relationship is None:
        raise ValueError("MemoryPack temporal history requires a relationship profile")
    relationship_id = pack.relationship.relationship_id
    if any(event.relationship_id != relationship_id for event in ordered_events):
        raise ValueError("MemoryPack relationship history crosses relationship boundaries")
    all_ids = set(by_id)
    memory_node_ids = {node.node_id for node in pack.nodes}
    for event in temporal_events:
        missing = set(_temporal_reference_ids(event)).difference(all_ids)
        if missing:
            raise ValueError(
                "MemoryPack temporal event references missing source events: "
                + ", ".join(sorted(missing))
            )
        payload = event.temporal_payload
        if (
            isinstance(payload, OpenLoopSpec)
            and payload.origin_memory_node_id is not None
            and payload.origin_memory_node_id not in memory_node_ids
        ):
            raise ValueError(
                "MemoryPack Open Loop references a missing origin memory node: "
                + payload.origin_memory_node_id
            )
    TemporalHistoryValidator.validate_complete_history(ordered_events)
    return tuple(ordered_events)


def _validate_persona_growth_pack(
    pack: MemoryPack,
) -> None:
    if not pack.persona_growth_proposals:
        return
    if pack.relationship is None:
        raise ValueError("MemoryPack Persona Growth requires a relationship profile")
    relationship_id = pack.relationship.relationship_id
    event_ids = {event.event_id for event in pack.relationship_events} | {
        event.event_id
        for record in pack.relationship_adjudications
        for event in record.events
    }
    identities = set()
    proposal_ids = set()
    for proposal in pack.persona_growth_proposals:
        identity = (proposal.proposal_id, proposal.revision)
        if identity in identities or proposal.proposal_id in proposal_ids:
            raise ValueError(
                "MemoryPack contains duplicate Persona Growth identities"
            )
        if proposal.relationship_id != relationship_id:
            raise ValueError(
                "MemoryPack Persona Growth crosses relationship boundaries"
            )
        if not set(proposal.supporting_event_ids).issubset(event_ids):
            raise ValueError(
                "MemoryPack Persona Growth references events outside the pack"
            )
        identities.add(identity)
        proposal_ids.add(proposal.proposal_id)


__all__ = [
    "MemoryPackAnalysis",
    "analyze_memory_pack",
    "analyze_memory_pack_relationship_processing",
    "validate_persisted_turn_adjudication_sources",
    "validate_memory_pack_persisted_turn_adjudications",
    "validate_memory_pack_node_types",
    "validate_memory_pack_relationship_consequences",
    "validate_memory_pack_relationship_processing",
    "validate_memory_pack_turn_records",
]
