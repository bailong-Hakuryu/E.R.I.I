"""Privacy-safe, read-only diagnostics for one relationship pipeline."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Tuple

from erii.core.persona_context import active_persona_manifest
from erii.models.adjudication import SourceProcessingMode
from erii.models.consolidation import RelationshipProcessingOutcome
from erii.models.provenance import ArtifactProvenanceState
from erii.models.turn import (
    ContinuityAssessmentStatus,
    DeliveryDisposition,
    SourceProcessingState,
    TurnStatus,
)

__all__ = [
    "PipelineInspectionCounts",
    "PipelineInspectionReport",
    "PipelineIssueCode",
    "inspect_relationship_pipeline",
]


class PipelineIssueCode(str, Enum):
    """Stable machine-readable relationship pipeline issue codes."""

    MANIFEST_MISSING = "manifest_missing"
    CONTINUITY_EVALUATOR_UNCONFIGURED = "continuity_evaluator_unconfigured"
    SHOWN_TURN_NOT_EVALUATED = "shown_turn_not_evaluated"
    DECLARED_CHANNEL_WITHOUT_TERMINAL_OUTCOME = (
        "declared_channel_without_terminal_outcome"
    )
    LEGACY_PROVENANCE_PRESENT = "legacy_provenance_present"
    CONSECUTIVE_NO_RELATIONSHIP_EVENT = "consecutive_no_relationship_event"
    LEGACY_CORE_MEMORY_PRESENT = "legacy_core_memory_present"


@dataclass(frozen=True)
class PipelineInspectionCounts:
    """Aggregate-only diagnostic counters."""

    turns: int = 0
    completed_turns: int = 0
    shown_turns: int = 0
    shown_turns_not_evaluated: int = 0
    declared_channels_without_terminal_outcome: int = 0
    legacy_memory_nodes: int = 0
    legacy_timeline_entries: int = 0
    relationship_processing_runs: int = 0
    no_relationship_event_runs: int = 0
    longest_no_relationship_event_streak: int = 0
    legacy_core_memory_records: int = 0

    def to_dict(self) -> Dict[str, int]:
        """Returns aggregate counts only."""
        return {
            "turns": self.turns,
            "completed_turns": self.completed_turns,
            "shown_turns": self.shown_turns,
            "shown_turns_not_evaluated": self.shown_turns_not_evaluated,
            "declared_channels_without_terminal_outcome": (
                self.declared_channels_without_terminal_outcome
            ),
            "legacy_memory_nodes": self.legacy_memory_nodes,
            "legacy_timeline_entries": self.legacy_timeline_entries,
            "relationship_processing_runs": self.relationship_processing_runs,
            "no_relationship_event_runs": self.no_relationship_event_runs,
            "longest_no_relationship_event_streak": (
                self.longest_no_relationship_event_streak
            ),
            "legacy_core_memory_records": self.legacy_core_memory_records,
        }


@dataclass(frozen=True)
class PipelineInspectionReport:
    """Sanitized relationship-pipeline health projection."""

    issue_codes: Tuple[PipelineIssueCode, ...]
    counts: PipelineInspectionCounts = PipelineInspectionCounts()

    def to_dict(self) -> Dict[str, Any]:
        """Returns a JSON-compatible representation without user content or IDs."""
        return {
            "issue_codes": [item.value for item in self.issue_codes],
            "counts": self.counts.to_dict(),
        }


def inspect_relationship_pipeline(
    engine: Any,
    agent_id: str,
    user_id: str,
) -> PipelineInspectionReport:
    """Inspects configured pipeline capabilities without mutating relationship data."""
    issues = []
    profile = engine.storage.get_relationship(agent_id, user_id)
    manifest = (
        active_persona_manifest(engine.storage, profile)
        if profile is not None
        else None
    )
    if manifest is None:
        issues.append(PipelineIssueCode.MANIFEST_MISSING)
    if engine.continuity_evaluator is None:
        issues.append(PipelineIssueCode.CONTINUITY_EVALUATOR_UNCONFIGURED)

    turns = tuple(engine.list_turns(agent_id, user_id))
    completed = tuple(item for item in turns if item.status == TurnStatus.COMPLETED)
    shown = tuple(
        item
        for item in completed
        if item.delivery_disposition
        in (DeliveryDisposition.SHOWN, DeliveryDisposition.OVERRIDDEN)
    )
    not_evaluated = tuple(
        item
        for item in shown
        if item.continuity_assessment is not None
        and item.continuity_assessment.status
        != ContinuityAssessmentStatus.COMPLETED
    )
    if not_evaluated:
        issues.append(PipelineIssueCode.SHOWN_TURN_NOT_EVALUATED)

    pending_channel_count = 0
    for turn in completed:
        pending_channel_count += sum(
            outcome.state == SourceProcessingState.PENDING
            for outcome in engine.get_source_processing_outcomes(
                agent_id,
                user_id,
                turn.turn_id,
            )
        )
    if pending_channel_count:
        issues.append(
            PipelineIssueCode.DECLARED_CHANNEL_WITHOUT_TERMINAL_OUTCOME
        )

    legacy_memory_nodes = sum(
        node.provenance_state == ArtifactProvenanceState.LEGACY_UNAVAILABLE
        for node in engine.storage.load_nodes(agent_id, user_id)
    )
    try:
        timeline_entries = engine.storage.list_timeline_entries(
            agent_id,
            user_id,
        )
    except NotImplementedError:
        timeline_entries = ()
    legacy_timeline_entries = sum(
        entry.provenance_state == ArtifactProvenanceState.LEGACY_UNAVAILABLE
        for entry in timeline_entries
    )
    if legacy_memory_nodes or legacy_timeline_entries:
        issues.append(PipelineIssueCode.LEGACY_PROVENANCE_PRESENT)

    legacy_core_memory_records = int(
        bool(engine.get_core_memory(agent_id, user_id).strip())
    )
    if legacy_core_memory_records:
        issues.append(PipelineIssueCode.LEGACY_CORE_MEMORY_PRESENT)

    relationship_runs = tuple(
        run
        for run in engine.list_relationship_processing_runs(agent_id, user_id)
        if run.processing_mode == SourceProcessingMode.NORMAL
    )
    no_event_runs = sum(
        run.outcome == RelationshipProcessingOutcome.NO_RELATIONSHIP_EVENT
        for run in relationship_runs
    )
    longest_no_event_streak = 0
    current_no_event_streak = 0
    for run in relationship_runs:
        if run.outcome == RelationshipProcessingOutcome.NO_RELATIONSHIP_EVENT:
            current_no_event_streak += 1
            longest_no_event_streak = max(
                longest_no_event_streak,
                current_no_event_streak,
            )
        else:
            current_no_event_streak = 0
    if longest_no_event_streak >= 2:
        issues.append(PipelineIssueCode.CONSECUTIVE_NO_RELATIONSHIP_EVENT)

    counts = PipelineInspectionCounts(
        turns=len(turns),
        completed_turns=len(completed),
        shown_turns=len(shown),
        shown_turns_not_evaluated=len(not_evaluated),
        declared_channels_without_terminal_outcome=pending_channel_count,
        legacy_memory_nodes=legacy_memory_nodes,
        legacy_timeline_entries=legacy_timeline_entries,
        relationship_processing_runs=len(relationship_runs),
        no_relationship_event_runs=no_event_runs,
        longest_no_relationship_event_streak=longest_no_event_streak,
        legacy_core_memory_records=legacy_core_memory_records,
    )
    return PipelineInspectionReport(issue_codes=tuple(issues), counts=counts)
