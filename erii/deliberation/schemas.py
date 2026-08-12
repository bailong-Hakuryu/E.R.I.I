"""Provider-neutral Character Deliberation V1 domain schemas.

The models in this module are immutable domain values.  JSON received from a
provider must still enter through :class:`StrictCanonicalCodec`; direct Python
construction is intentionally convenient for trusted host code and tests.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, Literal, Self, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .identifiers import validate_identifier


class _StrictFrozenModel(BaseModel):
    """Deeply immutable Pydantic value with validated copy semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    wire_required_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _freeze_sequence_inputs(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name, field in cls.model_fields.items():
            if name in normalized and get_origin(field.annotation) is tuple:
                item = normalized[name]
                if type(item) is list:
                    normalized[name] = tuple(item)
        return normalized

    @model_validator(mode="after")
    def _validate_identifiers(self) -> Self:
        """Apply one identifier policy to every scalar/list ID field."""
        for name in type(self).model_fields:
            value = getattr(self, name)
            if name.endswith("_id") and value is not None:
                validate_identifier(value, name)
            elif name.endswith("_ids"):
                for item in value:
                    validate_identifier(item, name)
        return self

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """Return a fully revalidated replacement; never trust Pydantic's fast copy."""
        del deep  # revalidation rebuilds the full immutable object either way
        changes = {} if update is None else dict(update)
        unknown = set(changes).difference(type(self).model_fields)
        if unknown:
            raise ValueError("model_copy update contains unknown fields")
        payload = self.model_dump(mode="python", round_trip=True)
        payload.update(changes)
        return type(self).model_validate(payload, strict=True)


def _require_unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label}")


class ResultKind(str, Enum):
    CANDIDATE = "candidate"
    ABSTAIN = "abstain"
    NEEDS_STAGED_DELIBERATION = "needs_staged_deliberation"


class EpistemicStatus(str, Enum):
    SUPPORTED = "supported"
    TENTATIVE = "tentative"
    UNKNOWN = "unknown"


class ImpulseDirection(str, Enum):
    APPROACH = "approach"
    AVOID = "avoid"
    PROTECT_SELF = "protect_self"
    PROTECT_OTHER = "protect_other"
    EXPLORE = "explore"
    WITHHOLD = "withhold"


class TensionKind(str, Enum):
    DISCLOSURE_CONFLICT = "disclosure_conflict"
    ATTACHMENT_AMBIVALENCE = "attachment_ambivalence"
    AUTONOMY_INTIMACY = "autonomy_intimacy"
    CONSISTENCY_FLEXIBILITY = "consistency_flexibility"


class AwarenessLevel(str, Enum):
    UNFORMED = "unformed"
    PARTIALLY_RECOGNIZED = "partially_recognized"
    RECOGNIZED_BUT_UNRESOLVED = "recognized_but_unresolved"
    INTEGRATED = "integrated"


class ExpressionRelation(str, Enum):
    DIRECT = "direct"
    PARTIAL = "partial"
    INDIRECT = "indirect"
    WITHHOLD = "withhold"
    PROTECTIVE_CONCEALMENT = "protective_concealment"
    DEFENSIVE_OPPOSITION = "defensive_opposition"
    STRATEGIC_MISDIRECTION = "strategic_misdirection"
    AMBIVALENT = "ambivalent"


class DisclosureLevel(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    MINIMAL = "minimal"
    WITHHELD = "withheld"


class InterpersonalPosture(str, Enum):
    OPEN = "open"
    GUARDED = "guarded"
    DEFENSIVE = "defensive"
    YIELDING = "yielding"
    ASSERTIVE = "assertive"


class VoiceMode(str, Enum):
    CHARACTER_NATIVE = "character_native"


class Perspective(str, Enum):
    FIRST_PERSON = "first_person"
    CLOSE_THIRD_PERSON = "close_third_person"
    FRAGMENTED = "fragmented"
    SENSORY = "sensory"
    MIXED = "mixed"
    MINIMAL = "minimal"


class NarrativeBudget(str, Enum):
    GLIMPSE = "glimpse"
    STANDARD = "standard"
    RICH = "rich"
    SCENE = "scene"


class DeliveryMode(str, Enum):
    SEQUENTIAL = "sequential"
    ATOMIC = "atomic"


class ResidueHorizon(str, Enum):
    NEXT_TURN = "next_turn"
    SHORT_ARC = "short_arc"
    UNTIL_REVIEW = "until_review"
    UNTIL_RESOLVED = "until_resolved"


class RouterSignal(str, Enum):
    NONE = "none"
    NEEDS_STAGED_DELIBERATION = "needs_staged_deliberation"


class SituationAppraisal(_StrictFrozenModel):
    appraisal_id: str = Field(..., max_length=64)
    bounded_summary: str = Field(..., max_length=500)
    epistemic_status: EpistemicStatus
    basis_ref_ids: tuple[str, ...] = ()
    counter_ref_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_refs(self) -> Self:
        _require_unique(self.basis_ref_ids, "basis_ref_id")
        _require_unique(self.counter_ref_ids, "counter_ref_id")
        return self


class PsychologicalCandidate(_StrictFrozenModel):
    candidate_id: str = Field(..., max_length=64)
    kind: str = Field(..., max_length=100)
    bounded_summary: str = Field(..., max_length=500)
    epistemic_status: EpistemicStatus
    basis_ref_ids: tuple[str, ...] = ()
    counter_ref_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_refs(self) -> Self:
        _require_unique(self.basis_ref_ids, "basis_ref_id")
        _require_unique(self.counter_ref_ids, "counter_ref_id")
        return self


class CompetingImpulse(_StrictFrozenModel):
    impulse_id: str = Field(..., max_length=64)
    direction: ImpulseDirection
    bounded_summary: str = Field(..., max_length=500)
    anchored_candidate_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_anchors(self) -> Self:
        _require_unique(self.anchored_candidate_ids, "anchored_candidate_id")
        return self


class Tension(_StrictFrozenModel):
    tension_id: str = Field(..., max_length=64)
    kind: TensionKind
    member_ids: tuple[str, ...] = Field(..., min_length=2)

    @model_validator(mode="after")
    def _unique_members(self) -> Self:
        _require_unique(self.member_ids, "tension member_id")
        return self


class SelfInterpretation(_StrictFrozenModel):
    awareness: AwarenessLevel
    bounded_summary: str = Field(..., max_length=500)


class AffectCandidate(_StrictFrozenModel):
    label: str = Field(..., max_length=50)
    intensity_band: Literal["low", "moderate", "high"]
    epistemic_status: EpistemicStatus
    basis_ref_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_refs(self) -> Self:
        _require_unique(self.basis_ref_ids, "basis_ref_id")
        return self


class BehavioralIntent(_StrictFrozenModel):
    kind: str = Field(..., max_length=100)
    bounded_summary: str = Field(..., max_length=500)


class CommunicationStrategy(_StrictFrozenModel):
    expression_relation: ExpressionRelation
    disclosure: DisclosureLevel
    interpersonal_posture: InterpersonalPosture
    tone_goal: VoiceMode


class Uncertainty(_StrictFrozenModel):
    code: str = Field(..., max_length=100)
    bounded_summary: str = Field(..., max_length=500)


class ResidueProposal(_StrictFrozenModel):
    kind: str = Field(..., max_length=100)
    anchor_ids: tuple[str, ...] = ()
    horizon: ResidueHorizon

    @model_validator(mode="after")
    def _unique_anchors(self) -> Self:
        _require_unique(self.anchor_ids, "residue anchor_id")
        return self


class DeliberationSemanticFrameV1(_StrictFrozenModel):
    wire_required_fields = frozenset(
        {
            "frame_version",
            "result_kind",
            "situation_appraisals",
            "psychological_candidates",
            "competing_impulses",
            "tensions",
            "affect_candidates",
            "self_interpretation",
            "behavioral_intent",
            "communication_strategy",
            "uncertainties",
            "residue_proposals",
        }
    )

    frame_version: Literal["erii-deliberation-frame/v1"] = "erii-deliberation-frame/v1"
    result_kind: ResultKind
    situation_appraisals: tuple[SituationAppraisal, ...] = ()
    psychological_candidates: tuple[PsychologicalCandidate, ...] = ()
    competing_impulses: tuple[CompetingImpulse, ...] = ()
    tensions: tuple[Tension, ...] = ()
    affect_candidates: tuple[AffectCandidate, ...] = ()
    self_interpretation: SelfInterpretation
    behavioral_intent: BehavioralIntent
    communication_strategy: CommunicationStrategy
    uncertainties: tuple[Uncertainty, ...] = ()
    residue_proposals: tuple[ResidueProposal, ...] = ()

    @field_validator(
        "situation_appraisals",
        "psychological_candidates",
        "competing_impulses",
        "tensions",
        "affect_candidates",
        "uncertainties",
        "residue_proposals",
    )
    @classmethod
    def _bounded_collections(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(value) > 50:
            raise ValueError("semantic collection exceeds maximum length")
        return value

    @model_validator(mode="after")
    def _unique_and_closed_ids(self) -> Self:
        _require_unique(tuple(x.appraisal_id for x in self.situation_appraisals), "appraisal_id")
        candidate_ids = tuple(x.candidate_id for x in self.psychological_candidates)
        _require_unique(candidate_ids, "candidate_id")
        _require_unique(tuple(x.impulse_id for x in self.competing_impulses), "impulse_id")
        _require_unique(tuple(x.tension_id for x in self.tensions), "tension_id")
        known = {
            *(x.appraisal_id for x in self.situation_appraisals),
            *candidate_ids,
            *(x.impulse_id for x in self.competing_impulses),
            *(x.tension_id for x in self.tensions),
        }
        for impulse in self.competing_impulses:
            if not set(impulse.anchored_candidate_ids).issubset(candidate_ids):
                raise ValueError("impulse anchor does not resolve to a candidate")
        for tension in self.tensions:
            if not set(tension.member_ids).issubset(known):
                raise ValueError("tension member does not resolve inside the frame")
        for residue in self.residue_proposals:
            if not set(residue.anchor_ids).issubset(known):
                raise ValueError("residue anchor does not resolve inside the frame")
        return self


class CharacterInteriorSceneV1(_StrictFrozenModel):
    wire_required_fields = frozenset(
        {
            "scene_version",
            "voice_mode",
            "perspective",
            "narrative_budget",
            "text",
            "semantic_anchor_ids",
            "factual_echo_refs",
            "projection_eligibility",
        }
    )

    scene_version: Literal["erii-character-interior-scene/v1"] = (
        "erii-character-interior-scene/v1"
    )
    voice_mode: VoiceMode
    perspective: Perspective
    narrative_budget: NarrativeBudget
    text: str = Field(..., max_length=5000, repr=False)
    semantic_anchor_ids: tuple[str, ...] = ()
    factual_echo_refs: tuple[str, ...] = ()
    projection_eligibility: Literal["not_assessed"] = "not_assessed"

    @field_validator("text")
    @classmethod
    def _nonempty_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("interior scene text is empty")
        return value

    @model_validator(mode="after")
    def _unique_refs(self) -> Self:
        _require_unique(self.semantic_anchor_ids, "semantic_anchor_id")
        _require_unique(self.factual_echo_refs, "factual_echo_ref")
        return self


class MessagePart(_StrictFrozenModel):
    wire_required_fields = frozenset({"part_id", "kind", "exact_utf8"})

    part_id: str = Field(..., max_length=64)
    kind: Literal["text"] = "text"
    exact_utf8: str = Field(..., max_length=10000, repr=False)

    @field_validator("exact_utf8")
    @classmethod
    def _nonempty_content(cls, value: str) -> str:
        if not value:
            raise ValueError("message content is empty")
        return value


class VisibleReplyEnvelopeV1(_StrictFrozenModel):
    wire_required_fields = frozenset({"parts", "delivery_mode"})

    parts: tuple[MessagePart, ...] = Field(..., min_length=1)
    delivery_mode: DeliveryMode = DeliveryMode.SEQUENTIAL

    @model_validator(mode="after")
    def _unique_parts(self) -> Self:
        _require_unique(tuple(part.part_id for part in self.parts), "part_id")
        return self


class CompactDecisionV1(_StrictFrozenModel):
    wire_required_fields = frozenset(
        {
            "decision_version",
            "result_kind",
            "frame",
            "interior_scene",
            "reply_candidate",
            "router_signal",
        }
    )

    decision_version: Literal["erii-compact-decision/v1"] = "erii-compact-decision/v1"
    result_kind: ResultKind
    frame: DeliberationSemanticFrameV1
    interior_scene: CharacterInteriorSceneV1
    reply_candidate: VisibleReplyEnvelopeV1
    router_signal: RouterSignal = RouterSignal.NONE

    @model_validator(mode="after")
    def _consistent_result_kind(self) -> Self:
        if self.result_kind is not self.frame.result_kind:
            raise ValueError("decision and frame result kinds differ")
        frame_ids = {
            *(x.appraisal_id for x in self.frame.situation_appraisals),
            *(x.candidate_id for x in self.frame.psychological_candidates),
            *(x.impulse_id for x in self.frame.competing_impulses),
            *(x.tension_id for x in self.frame.tensions),
        }
        if not set(self.interior_scene.semantic_anchor_ids).issubset(frame_ids):
            raise ValueError("interior scene anchor does not resolve inside the frame")
        return self


class EvidenceItem(_StrictFrozenModel):
    wire_required_fields = frozenset(
        {
            "ref_id",
            "authority_kind",
            "visibility",
            "summary_or_exact_content",
            "source_fingerprint",
            "source_turn_id",
            "status",
        }
    )

    ref_id: str = Field(..., max_length=200)
    authority_kind: str = Field(..., max_length=100)
    visibility: str = Field(..., max_length=50)
    summary_or_exact_content: str = Field(..., max_length=5000, repr=False)
    source_fingerprint: str = Field(..., max_length=100)
    source_turn_id: str | None = None
    status: Literal["active"] = "active"


class EvidenceViewV1(_StrictFrozenModel):
    wire_required_fields = frozenset(
        {
            "view_id",
            "relationship_id",
            "turn_id",
            "items",
            "allowed_claim_kinds",
            "view_fingerprint",
        }
    )

    view_id: str = Field(..., max_length=200)
    relationship_id: str = Field(..., max_length=200)
    turn_id: str = Field(..., max_length=200)
    items: tuple[EvidenceItem, ...] = ()
    allowed_claim_kinds: tuple[str, ...] = ()
    view_fingerprint: str = Field(..., max_length=100)

    @model_validator(mode="after")
    def _unique_items(self) -> Self:
        _require_unique(tuple(item.ref_id for item in self.items), "evidence ref_id")
        _require_unique(self.allowed_claim_kinds, "allowed_claim_kind")
        return self


class UserMessageEnvelope(_StrictFrozenModel):
    wire_required_fields = frozenset({"parts", "canonical_fingerprint"})

    parts: tuple[MessagePart, ...] = Field(..., min_length=1)
    canonical_fingerprint: str = Field(..., max_length=100)

    @model_validator(mode="after")
    def _unique_parts(self) -> Self:
        _require_unique(tuple(part.part_id for part in self.parts), "user message part_id")
        return self


class CompactDeliberationRequestV1(_StrictFrozenModel):
    wire_required_fields = frozenset(
        {"schema_version", "user_envelope", "evidence_view", "relationship_id", "turn_id"}
    )

    schema_version: Literal["erii-character-deliberation-request/v1"] = (
        "erii-character-deliberation-request/v1"
    )
    user_envelope: UserMessageEnvelope
    evidence_view: EvidenceViewV1
    relationship_id: str = Field(..., max_length=200)
    turn_id: str = Field(..., max_length=200)

    @model_validator(mode="after")
    def _scope_matches_view(self) -> Self:
        if self.relationship_id != self.evidence_view.relationship_id:
            raise ValueError("request relationship does not match evidence view")
        if self.turn_id != self.evidence_view.turn_id:
            raise ValueError("request turn does not match evidence view")
        return self


class DeliberationPlanV1(_StrictFrozenModel):
    wire_required_fields = frozenset(
        {"plan_version", "frame", "interior_scene", "plan_fingerprint"}
    )

    plan_version: Literal["erii-deliberation-plan/v1"] = "erii-deliberation-plan/v1"
    frame: DeliberationSemanticFrameV1
    interior_scene: CharacterInteriorSceneV1
    plan_fingerprint: str = Field(..., max_length=100)


class ReplyRealizationV1(_StrictFrozenModel):
    wire_required_fields = frozenset(
        {"realization_version", "plan_fingerprint", "reply_candidate"}
    )

    realization_version: Literal["erii-reply-realization/v1"] = (
        "erii-reply-realization/v1"
    )
    plan_fingerprint: str = Field(..., max_length=100)
    reply_candidate: VisibleReplyEnvelopeV1


class StagedPlanRequestV1(_StrictFrozenModel):
    wire_required_fields = frozenset({"schema_version"})
    schema_version: Literal["erii-staged-plan-request/v1"] = "erii-staged-plan-request/v1"


class ReplyRealizationRequestV1(_StrictFrozenModel):
    wire_required_fields = frozenset({"schema_version"})
    schema_version: Literal["erii-reply-realization-request/v1"] = (
        "erii-reply-realization-request/v1"
    )
