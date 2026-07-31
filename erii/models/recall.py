"""Immutable request and projection models for structured recall.

The models in this module form the host-facing recall seam.  They deliberately
do not expose mutable storage or relationship-domain objects.  A
``RecallResult`` is a complete, audience-filtered value that a renderer may
format without reading storage or deciding what to omit.
"""

from __future__ import annotations

from enum import Enum
import json
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RecallModel(BaseModel):
    """Strict, immutable base model with canonical JSON serialization."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        # Projection content may be an exact Character Blueprint source span.
        str_strip_whitespace=False,
        allow_inf_nan=False,
    )

    def stable_json(self) -> str:
        """Returns deterministic JSON suitable for snapshots and fingerprints."""

        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class RecallAudience(str, Enum):
    """The only supported consumers of a structured recall result."""

    AGENT_PRIVATE = "agent_private"
    PUBLIC = "public"


class PersonaDelivery(str, Enum):
    """How approved character authority is delivered for this recall."""

    PLANNED = "planned"
    FULL = "full"


class RelationshipRecallStatus(str, Enum):
    """Whether relationship-aware projections were available."""

    UNINITIALIZED = "uninitialized"
    INITIALIZED = "initialized"


class RecallNoticeSeverity(str, Enum):
    """Severity of a safe, audience-filtered recall notice."""

    INFO = "info"
    WARNING = "warning"


class RecallSignalType(str, Enum):
    """Kinds of current, side-effect-free temporal recall signals."""

    PROMISE_DUE = "promise_due"
    PROMISE_OVERDUE = "promise_overdue"
    OPEN_LOOP = "open_loop"


class RecallSignalAuthority(str, Enum):
    """Authority of the history source from which a signal was derived."""

    FORMAL_RELATIONSHIP_HISTORY = "formal_relationship_history"
    LEGACY_UNRESOLVED_MEMORY = "legacy_unresolved_memory"


class RecallSignalReason(str, Enum):
    """Exact deterministic condition that caused a signal to exist."""

    AT_DEADLINE = "at_deadline"
    PAST_DEADLINE = "past_deadline"
    CONDITION_CONFIRMED = "condition_confirmed"
    UNRESOLVED_FORMAL_LOOP = "unresolved_formal_loop"
    LEGACY_UNRESOLVED_FLAG = "legacy_unresolved_flag"


class RecallArtifactProvenance(str, Enum):
    """Trust level of the source chain carried by one memory projection."""

    SOURCE_LINKED = "source_linked"
    PARTIAL_SOURCE = "partial_source"
    LEGACY_UNRESOLVED = "legacy_unresolved"


class WorldTime(RecallModel):
    """An explicit time value in one host-owned fictional or real clock."""

    clock_id: str = Field(min_length=1, max_length=256)
    display_value: str = Field(min_length=1, max_length=4000)
    order_value: Optional[float] = None

    @field_validator("clock_id", "display_value", mode="before")
    @classmethod
    def text_fields_are_normalized(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("WorldTime text fields must be non-empty strings")
        return value.strip()

    @field_validator("order_value", mode="before")
    @classmethod
    def order_value_is_explicitly_numeric(cls, value):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("order_value must be numeric when supplied")
        return value


class RecallTemporalContext(RecallModel):
    """Observation context carried by recall without mutating history."""

    observed_at: Optional[str] = Field(default=None, min_length=1, max_length=256)
    world_time: Optional[WorldTime] = None


class RecallBudget(RecallModel):
    """Maximum cost accepted by the host for the assembled projections."""

    max_cost: int = Field(default=8192, ge=1)


class RecallOptions(RecallModel):
    """Explicit policy choices for one structured recall operation."""

    top_k: int = Field(default=5, ge=1, le=100)
    max_per_type: int = Field(default=2, ge=1, le=100)
    reinforce: bool = False
    persona_delivery: PersonaDelivery = PersonaDelivery.PLANNED
    budget: RecallBudget = Field(default_factory=RecallBudget)


class RecallRequest(RecallModel):
    """Complete input to the structured recall module."""

    agent_id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=256)
    query: str = Field(default="", max_length=200_000)
    audience: RecallAudience
    options: RecallOptions = Field(default_factory=RecallOptions)
    temporal_context: RecallTemporalContext = Field(default_factory=RecallTemporalContext)


class RecallSourceReference(RecallModel):
    """Traceable reference to authority without embedding a domain record."""

    source_id: str = Field(min_length=1, max_length=256)
    source_kind: str = Field(min_length=1, max_length=128)
    source_revision: Optional[str] = Field(default=None, min_length=1, max_length=128)
    source_hash: Optional[str] = Field(default=None, min_length=1, max_length=256)
    start: Optional[int] = Field(default=None, ge=0)
    end: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def offsets_are_complete(self) -> "RecallSourceReference":
        if (self.start is None) != (self.end is None):
            raise ValueError("source start and end must be supplied together")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("source end must be greater than start")
        return self


class RecallProjection(RecallModel):
    """Common metadata for one selected, audience-safe semantic projection."""

    projection_id: str = Field(min_length=1, max_length=256)
    source_id: str = Field(min_length=1, max_length=256)
    source_kind: str = Field(min_length=1, max_length=128)
    visibility: RecallAudience
    selection_reason: str = Field(min_length=1, max_length=2000)
    source_references: Tuple[RecallSourceReference, ...] = Field(default_factory=tuple)


class PersonaRecallProjection(RecallProjection):
    """One selected character-authority, interpretation, or growth item."""

    kind: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=200_000)
    activation_tier: Optional[str] = Field(default=None, min_length=1, max_length=64)


class PersonaRecallContext(RecallModel):
    """Three explicitly separated layers of persona context."""

    delivery: PersonaDelivery
    blueprint_id: str = Field(min_length=1, max_length=256)
    blueprint_hash: str = Field(min_length=1, max_length=256)
    manifest_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    manifest_revision: Optional[str] = Field(default=None, min_length=1, max_length=128)
    authority_items: Tuple[PersonaRecallProjection, ...] = Field(default_factory=tuple)
    interpretation_items: Tuple[PersonaRecallProjection, ...] = Field(default_factory=tuple)
    approved_growth_items: Tuple[PersonaRecallProjection, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def manifest_identity_is_complete(self) -> "PersonaRecallContext":
        if (self.manifest_id is None) != (self.manifest_revision is None):
            raise ValueError("manifest_id and manifest_revision must be supplied together")
        return self


class MemoryRecallProjection(RecallProjection):
    """One immutable projection of selected legacy or structured memory."""

    provenance: RecallArtifactProvenance = (
        RecallArtifactProvenance.LEGACY_UNRESOLVED
    )
    memory_type: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=200_000)
    created_at: Optional[str] = Field(default=None, min_length=1, max_length=256)
    source_visibility: Optional[str] = Field(default=None, min_length=1, max_length=128)


class EventRecallProjection(RecallProjection):
    """One selected relationship event without mutable state or audit receipts."""

    event_type: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=200_000)
    recorded_at: str = Field(min_length=1, max_length=256)
    occurred_at: Optional[str] = Field(default=None, min_length=1, max_length=256)


class RelationshipNarrativeProjection(RecallProjection):
    """Narrative relationship meaning safe for the selected audience."""

    kind: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=200_000)


class RelationshipMetric(RecallModel):
    """Private diagnostic relationship state; never rendered by default."""

    dimension: str = Field(min_length=1, max_length=128)
    value: float = Field(ge=0.0, le=1.0)
    evidence_event_id: Optional[str] = Field(default=None, min_length=1, max_length=256)


class RelationshipRecallContext(RecallModel):
    """Purpose-built relationship projection separated from its numeric diagnostics."""

    relationship_id: str = Field(min_length=1, max_length=256)
    persona_id: str = Field(min_length=1, max_length=256)
    premise: Optional[RelationshipNarrativeProjection] = None
    narratives: Tuple[RelationshipNarrativeProjection, ...] = Field(default_factory=tuple)
    internal_state: Tuple[RelationshipMetric, ...] = Field(default_factory=tuple)


class RecallSignalProjection(RecallProjection):
    """One provenance-complete, side-effect-free current signal."""

    signal_type: RecallSignalType
    summary: str = Field(min_length=1, max_length=200_000)
    subject_id: str = Field(min_length=1, max_length=256)
    authority: RecallSignalAuthority
    reason: RecallSignalReason
    source_event_ids: Tuple[str, ...] = Field(default_factory=tuple)
    source_memory_ids: Tuple[str, ...] = Field(default_factory=tuple)
    due_world_time: Optional[WorldTime] = None
    condition_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    # Retained as a compact compatibility/indexing field. When a deadline is
    # present it must name the exact same clock.
    clock_id: Optional[str] = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def provenance_and_signal_shape_are_consistent(self) -> "RecallSignalProjection":
        if len(self.source_event_ids) != len(set(self.source_event_ids)):
            raise ValueError("source_event_ids must not contain duplicates")
        if len(self.source_memory_ids) != len(set(self.source_memory_ids)):
            raise ValueError("source_memory_ids must not contain duplicates")
        if self.visibility != RecallAudience.AGENT_PRIVATE:
            raise ValueError("temporal recall signals are agent-private in schema version 1")
        if self.source_id != self.subject_id:
            raise ValueError("signal source_id must identify its subject")

        if self.authority == RecallSignalAuthority.FORMAL_RELATIONSHIP_HISTORY:
            if not self.source_event_ids or self.source_memory_ids:
                raise ValueError(
                    "formal signals require event provenance and cannot name legacy memories"
                )
            if self.source_event_ids[0] != self.subject_id:
                raise ValueError("formal signal subject must be its first source event")
        else:
            if not self.source_memory_ids or self.source_event_ids:
                raise ValueError(
                    "legacy signals require memory provenance and cannot name formal events"
                )
            if self.source_memory_ids != (self.subject_id,):
                raise ValueError("legacy signal subject must be its only source memory")
            if (
                self.signal_type != RecallSignalType.OPEN_LOOP
                or self.reason != RecallSignalReason.LEGACY_UNRESOLVED_FLAG
            ):
                raise ValueError("legacy unresolved memories can only derive legacy open loops")

        if self.due_world_time is not None:
            if self.clock_id != self.due_world_time.clock_id:
                raise ValueError("clock_id must match due_world_time.clock_id")
        elif self.clock_id is not None:
            raise ValueError("clock_id requires due_world_time")

        if self.signal_type == RecallSignalType.PROMISE_OVERDUE:
            if (
                self.reason != RecallSignalReason.PAST_DEADLINE
                or self.due_world_time is None
                or self.due_world_time.order_value is None
            ):
                raise ValueError("promise_overdue requires a comparable past deadline")
        elif self.signal_type == RecallSignalType.PROMISE_DUE:
            if self.reason == RecallSignalReason.AT_DEADLINE:
                if (
                    self.due_world_time is None
                    or self.due_world_time.order_value is None
                ):
                    raise ValueError("deadline-based promise_due requires a comparable deadline")
            elif self.reason == RecallSignalReason.CONDITION_CONFIRMED:
                if self.condition_id is None:
                    raise ValueError("condition-based promise_due requires condition_id")
                if self.due_world_time is not None:
                    raise ValueError(
                        "condition-based promise_due cannot carry a deadline"
                    )
            else:
                raise ValueError("promise_due has an incompatible derivation reason")
        elif self.reason not in {
            RecallSignalReason.UNRESOLVED_FORMAL_LOOP,
            RecallSignalReason.LEGACY_UNRESOLVED_FLAG,
        }:
            raise ValueError("open_loop has an incompatible derivation reason")

        if self.condition_id is not None and len(self.source_event_ids) < 2:
            raise ValueError(
                "condition-backed promise signal requires its confirmation event"
            )
        if self.signal_type == RecallSignalType.OPEN_LOOP:
            if self.due_world_time is not None or self.condition_id is not None:
                raise ValueError("open_loop cannot carry promise timing or condition fields")
            if (
                self.authority
                == RecallSignalAuthority.FORMAL_RELATIONSHIP_HISTORY
                and self.reason != RecallSignalReason.UNRESOLVED_FORMAL_LOOP
            ):
                raise ValueError("formal open_loop requires unresolved_formal_loop reason")
        return self


class RecallNotice(RecallModel):
    """An audience-safe condition the renderer must not silently discard."""

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    severity: RecallNoticeSeverity = RecallNoticeSeverity.INFO


class BudgetOmission(RecallModel):
    """One complete projection omitted before rendering."""

    source_id: str = Field(min_length=1, max_length=256)
    source_kind: str = Field(min_length=1, max_length=128)
    estimated_cost: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)


class BudgetReport(RecallModel):
    """Diagnostic account of projection-level budget decisions."""

    estimator_id: str = Field(min_length=1, max_length=256)
    max_cost: int = Field(ge=1)
    required_cost: int = Field(ge=0)
    selected_cost: int = Field(ge=0)
    omitted: Tuple[BudgetOmission, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def costs_are_consistent(self) -> "BudgetReport":
        if self.selected_cost > self.max_cost:
            raise ValueError("selected_cost must not exceed max_cost")
        if self.required_cost > self.selected_cost:
            raise ValueError("required_cost must not exceed selected_cost")
        return self


class ReinforcementReport(RecallModel):
    """Diagnostic receipt for explicit post-budget memory reinforcement."""

    requested: bool = False
    applied: bool = False
    reinforced_source_ids: Tuple[str, ...] = Field(default_factory=tuple)
    reason: Optional[str] = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def state_is_consistent(self) -> "ReinforcementReport":
        if self.applied and not self.requested:
            raise ValueError("reinforcement cannot be applied when it was not requested")
        if not self.applied and self.reinforced_source_ids:
            raise ValueError("non-applied reinforcement cannot list source ids")
        if len(self.reinforced_source_ids) != len(set(self.reinforced_source_ids)):
            raise ValueError("reinforced_source_ids must not contain duplicates")
        return self


class RecallResult(RecallModel):
    """Complete, immutable, audience-filtered structured recall output."""

    schema_version: int = Field(default=1, ge=1)
    agent_id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=256)
    audience: RecallAudience
    relationship_status: RelationshipRecallStatus
    persona_context: Optional[PersonaRecallContext] = None
    relationship_context: Optional[RelationshipRecallContext] = None
    memories: Tuple[MemoryRecallProjection, ...] = Field(default_factory=tuple)
    events: Tuple[EventRecallProjection, ...] = Field(default_factory=tuple)
    signals: Tuple[RecallSignalProjection, ...] = Field(default_factory=tuple)
    temporal_context: RecallTemporalContext = Field(default_factory=RecallTemporalContext)
    notices: Tuple[RecallNotice, ...] = Field(default_factory=tuple)
    budget_report: BudgetReport
    reinforcement: ReinforcementReport = Field(default_factory=ReinforcementReport)

    @model_validator(mode="after")
    def result_is_audience_safe_and_consistent(self) -> "RecallResult":
        projections = []
        if self.persona_context is not None:
            projections.extend(self.persona_context.authority_items)
            projections.extend(self.persona_context.interpretation_items)
            projections.extend(self.persona_context.approved_growth_items)
        if self.relationship_context is not None:
            if self.relationship_context.premise is not None:
                projections.append(self.relationship_context.premise)
            projections.extend(self.relationship_context.narratives)
        projections.extend(self.memories)
        projections.extend(self.events)
        projections.extend(self.signals)

        if self.audience == RecallAudience.PUBLIC:
            private_ids = [
                projection.projection_id
                for projection in projections
                if projection.visibility == RecallAudience.AGENT_PRIVATE
            ]
            if private_ids:
                raise ValueError(
                    "public recall cannot contain agent-private projections: "
                    + ", ".join(private_ids)
                )
            if self.persona_context is not None and self.persona_context.authority_items:
                raise ValueError("public recall cannot contain character authority source text")
            if self.relationship_context is not None and self.relationship_context.internal_state:
                raise ValueError("public recall cannot contain internal relationship state")

        if self.relationship_status == RelationshipRecallStatus.UNINITIALIZED:
            if self.persona_context is not None or self.relationship_context is not None:
                raise ValueError(
                    "uninitialized recall cannot contain persona or relationship context"
                )
        elif self.relationship_context is None:
            raise ValueError("initialized recall requires relationship_context")
        return self
