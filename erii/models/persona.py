"""Persona compilation schemas and immutable durable records.

Pydantic models in this module describe untrusted compiler output. Frozen
dataclasses describe the exact proposal revisions and approved manifests that
may cross the persistence seam.
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from erii.models.relationship import (
    BaselineLevel,
    RELATIONSHIP_DIMENSIONS,
    RelationshipPremiseMode,
    utc_now,
)
from erii.models.turn import ContextSignalSource


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unique(values: Sequence[str], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


class PersonaCompilationConflictError(ValueError):
    """Raised for stale revisions or invalid proposal state transitions."""


class PersonaActivationTier(str, Enum):
    """Runtime activation tiers for approved persona interpretation items."""

    FOUNDATION = "foundation"
    SITUATIONAL = "situational"
    REFERENCE = "reference"


class PersonaInterpretationBasis(str, Enum):
    """How directly source text supports an interpretation."""

    EXPLICIT = "explicit"
    STRONGLY_IMPLIED = "strongly_implied"
    INTERPRETIVE = "interpretive"


class PersonaApplicability(str, Enum):
    """Whether source meaning is valid at the character-authority layer."""

    APPLICABLE = "applicable"
    INAPPLICABLE_HOST_AUTHORITY = "inapplicable_host_authority"


class PersonaScope(str, Enum):
    """The scope within which an interpretation can take effect."""

    CHARACTER = "character"
    CANONICAL_RELATIONSHIP = "canonical_relationship"
    RELATIONSHIP_TENDENCY = "relationship_tendency"


class VoicePatternConditionType(str, Enum):
    """Typed runtime contexts that may activate an expression register."""

    EMOTION = "emotion"
    ACTIVITY = "activity"
    RELATIONSHIP_SAFETY = "relationship_safety"
    COMMUNICATION_MODALITY = "communication_modality"
    ENVIRONMENTAL_CUE = "environmental_cue"


class PersonaClaimKind(str, Enum):
    """Stable kinds of persona claims represented by a manifest."""

    IDENTITY = "identity"
    SELF_CONCEPT = "self_concept"
    VALUE = "value"
    DESIRE = "desire"
    FEAR = "fear"
    NEED = "need"
    TENDENCY = "tendency"
    BOUNDARY = "boundary"
    VOICE = "voice"
    LORE = "lore"
    HOST_DIRECTIVE = "host_directive"


class FormativeLinkType(str, Enum):
    """Typed, interpretive meaning between persona graph items."""

    SUPPORTS = "supports"
    EXPLAINS = "explains"
    EXPRESSES = "expresses"
    SHAPES_ATTACHMENT = "shapes_attachment"
    TENSIONS_WITH = "tensions_with"
    RELATIONSHIP_SPECIFIC = "relationship_specific"


class PersonaCompilationStatus(str, Enum):
    """Out-of-band review status for a compilation proposal revision."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class PersonaCompilationDecision(str, Enum):
    """Review decisions allowed at the compilation seam."""

    APPROVE = "approve"
    REJECT = "reject"
    REVOKE = "revoke"


class PersonaDeliveryMode(str, Enum):
    """Ways Character Blueprint authority may be delivered at recall time."""

    PLANNED = "planned"
    FULL = "full"


class PersonaBoundaryModel(BaseModel):
    """Strict base for data crossing the untrusted persona compiler seam."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class PersonaSourceSpan(PersonaBoundaryModel):
    """An exact source range claimed by a compiler output item."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    span_id: str = Field(min_length=1, max_length=256)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=50_000)
    quote_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)
    section: Optional[str] = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def offsets_are_ordered(self) -> "PersonaSourceSpan":
        if not self.span_id.strip() or self.span_id != self.span_id.strip():
            raise ValueError("span_id must be non-empty and cannot have surrounding whitespace")
        if self.end <= self.start:
            raise ValueError("source span end must be greater than start")
        if self.quote_sha256 is not None:
            try:
                int(self.quote_sha256, 16)
            except ValueError as exc:
                raise ValueError("quote_sha256 must be hexadecimal") from exc
        return self


class PersonaClaimCandidate(PersonaBoundaryModel):
    """One source-backed interpretation claim."""

    claim_id: str = Field(min_length=1, max_length=256)
    kind: PersonaClaimKind
    statement: str = Field(min_length=1, max_length=8000)
    activation_tier: PersonaActivationTier
    basis: PersonaInterpretationBasis
    scope: PersonaScope = PersonaScope.CHARACTER
    applicability: PersonaApplicability = PersonaApplicability.APPLICABLE
    source_span_ids: List[str] = Field(min_length=1, max_length=64)
    required_dependency_ids: List[str] = Field(default_factory=list, max_length=64)
    tags: List[str] = Field(default_factory=list, max_length=64)
    priority: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def claim_lists_and_applicability_are_valid(self) -> "PersonaClaimCandidate":
        _unique(self.source_span_ids, "claim source_span_ids")
        _unique(self.required_dependency_ids, "claim required_dependency_ids")
        _unique(self.tags, "claim tags")
        if self.claim_id in self.required_dependency_ids:
            raise ValueError("a claim cannot depend on itself")
        if self.kind == PersonaClaimKind.HOST_DIRECTIVE:
            if self.applicability != PersonaApplicability.INAPPLICABLE_HOST_AUTHORITY:
                raise ValueError("host directives must be inapplicable at character authority")
        elif self.applicability == PersonaApplicability.INAPPLICABLE_HOST_AUTHORITY:
            raise ValueError("only host directives may be marked as host-authority inapplicable")
        return self


class FormativeExperienceCandidate(PersonaBoundaryModel):
    """A source-backed experience that may shape persona meaning."""

    experience_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=1000)
    summary: str = Field(min_length=1, max_length=12_000)
    participant_roles: List[str] = Field(default_factory=list, max_length=64)
    activation_tier: PersonaActivationTier = PersonaActivationTier.SITUATIONAL
    scope: PersonaScope = PersonaScope.CHARACTER
    source_span_ids: List[str] = Field(min_length=1, max_length=64)
    tags: List[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def experience_lists_are_unique(self) -> "FormativeExperienceCandidate":
        _unique(self.participant_roles, "experience participant_roles")
        _unique(self.source_span_ids, "experience source_span_ids")
        _unique(self.tags, "experience tags")
        return self


class FormativeLinkCandidate(PersonaBoundaryModel):
    """A typed interpretation link, never a claim of certain causality."""

    link_id: str = Field(min_length=1, max_length=256)
    from_id: str = Field(min_length=1, max_length=256)
    relation: FormativeLinkType
    to_id: str = Field(min_length=1, max_length=256)
    basis: PersonaInterpretationBasis
    scope: PersonaScope = PersonaScope.CHARACTER
    source_span_ids: List[str] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def link_is_well_formed(self) -> "FormativeLinkCandidate":
        _unique(self.source_span_ids, "link source_span_ids")
        if self.from_id == self.to_id:
            raise ValueError("formative links cannot target their own source")
        return self


class MeaningCapsuleCandidate(PersonaBoundaryModel):
    """Minimum neutral explanation connecting a claim to formative evidence."""

    capsule_id: str = Field(min_length=1, max_length=256)
    claim_id: str = Field(min_length=1, max_length=256)
    meaning: str = Field(min_length=1, max_length=8000)
    experience_ids: List[str] = Field(min_length=1, max_length=64)
    link_ids: List[str] = Field(min_length=1, max_length=64)
    source_span_ids: List[str] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def capsule_lists_are_unique(self) -> "MeaningCapsuleCandidate":
        _unique(self.experience_ids, "capsule experience_ids")
        _unique(self.link_ids, "capsule link_ids")
        _unique(self.source_span_ids, "capsule source_span_ids")
        return self


class CanonicalPremiseTemplateCandidate(PersonaBoundaryModel):
    """An explicitly selectable canonical relationship starting template."""

    premise_template_id: str = Field(min_length=1, max_length=256)
    counterpart_role: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=512)
    address_name: Optional[str] = Field(default=None, min_length=1, max_length=512)
    premise_experience_ids: List[str] = Field(min_length=1, max_length=64)
    qualitative_baseline: Dict[str, BaselineLevel]
    source_span_ids: List[str] = Field(min_length=1, max_length=64)
    requires_explicit_binding: bool = True

    @model_validator(mode="after")
    def premise_is_complete(self) -> "CanonicalPremiseTemplateCandidate":
        _unique(self.premise_experience_ids, "premise experience IDs")
        _unique(self.source_span_ids, "premise source span IDs")
        dimensions = set(self.qualitative_baseline)
        if dimensions != set(RELATIONSHIP_DIMENSIONS):
            raise ValueError(
                "qualitative_baseline must contain every relationship dimension exactly once"
            )
        if not self.requires_explicit_binding:
            raise ValueError("canonical premise templates must require explicit binding")
        return self


class VoicePatternCondition(PersonaBoundaryModel):
    """One exact, source-authority-aware condition for a voice pattern."""

    condition_id: str = Field(min_length=1, max_length=256)
    condition_type: VoicePatternConditionType
    values: List[str] = Field(min_length=1, max_length=32)
    signal_source: Optional[ContextSignalSource] = None

    @model_validator(mode="after")
    def condition_has_one_authoritative_signal_source(self) -> "VoicePatternCondition":
        normalized = [item.casefold() for item in self.values]
        _unique(normalized, "voice pattern condition values")
        if (
            self.condition_type
            == VoicePatternConditionType.RELATIONSHIP_SAFETY
            and not set(normalized).issubset({"low", "moderate", "high"})
        ):
            raise ValueError(
                "relationship_safety values must be low, moderate, or high"
            )
        expected_sources = {
            VoicePatternConditionType.EMOTION: ContextSignalSource.EVALUATOR_INFERRED,
            VoicePatternConditionType.ACTIVITY: ContextSignalSource.HOST_OBSERVED,
            VoicePatternConditionType.RELATIONSHIP_SAFETY: (
                ContextSignalSource.CORE_DERIVED
            ),
            VoicePatternConditionType.COMMUNICATION_MODALITY: (
                ContextSignalSource.HOST_OBSERVED
            ),
            VoicePatternConditionType.ENVIRONMENTAL_CUE: (
                ContextSignalSource.HOST_OBSERVED
            ),
        }
        expected = expected_sources[self.condition_type]
        if self.signal_source is None:
            object.__setattr__(self, "signal_source", expected)
        elif self.signal_source != expected:
            raise ValueError(
                f"{self.condition_type.value} conditions require "
                f"{expected.value} signals"
            )
        return self


class ContextualVoicePatternCandidate(PersonaBoundaryModel):
    """A source-backed expression register with deterministic activation inputs."""

    pattern_id: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=8000)
    scope: PersonaScope = PersonaScope.CHARACTER
    basis: PersonaInterpretationBasis
    source_span_ids: List[str] = Field(min_length=1, max_length=64)
    conditions: List[VoicePatternCondition] = Field(min_length=1, max_length=32)
    required_claim_ids: List[str] = Field(min_length=1, max_length=64)
    required_experience_ids: List[str] = Field(default_factory=list, max_length=64)
    canonical_premise_template_ids: List[str] = Field(
        default_factory=list,
        max_length=32,
    )

    @model_validator(mode="after")
    def pattern_lists_are_unique_and_scope_is_explicit(
        self,
    ) -> "ContextualVoicePatternCandidate":
        _unique(self.source_span_ids, "voice pattern source_span_ids")
        _unique(
            [item.condition_id for item in self.conditions],
            "voice pattern condition IDs",
        )
        _unique(
            [item.condition_type.value for item in self.conditions],
            "voice pattern condition types",
        )
        _unique(self.required_claim_ids, "voice pattern required_claim_ids")
        _unique(
            self.required_experience_ids,
            "voice pattern required_experience_ids",
        )
        _unique(
            self.canonical_premise_template_ids,
            "voice pattern canonical premise template IDs",
        )
        if self.scope == PersonaScope.CANONICAL_RELATIONSHIP:
            if not self.canonical_premise_template_ids:
                raise ValueError(
                    "canonical relationship voice patterns require an explicit "
                    "premise template"
                )
        elif self.canonical_premise_template_ids:
            raise ValueError(
                "only canonical relationship voice patterns can name premise templates"
            )
        return self


class PersonaManifestCandidate(PersonaBoundaryModel):
    """One complete, untrusted candidate interpretation of a Blueprint revision."""

    schema_version: str = Field(default="0.4.0a3", min_length=1, max_length=64)
    compiler_version: str = Field(default="unspecified", min_length=1, max_length=128)
    source_spans: List[PersonaSourceSpan] = Field(min_length=1, max_length=4096)
    claims: List[PersonaClaimCandidate] = Field(min_length=1, max_length=2048)
    formative_experiences: List[FormativeExperienceCandidate] = Field(
        default_factory=list, max_length=1024
    )
    formative_links: List[FormativeLinkCandidate] = Field(
        default_factory=list, max_length=4096
    )
    meaning_capsules: List[MeaningCapsuleCandidate] = Field(
        default_factory=list, max_length=2048
    )
    premise_templates: List[CanonicalPremiseTemplateCandidate] = Field(
        default_factory=list, max_length=128
    )
    contextual_voice_patterns: List[ContextualVoicePatternCandidate] = Field(
        default_factory=list,
        max_length=1024,
    )

    def model_dump(self, *args, **kwargs) -> Dict[str, Any]:
        """Keeps pre-a7 empty manifests byte-compatible for fingerprinting."""
        data = super().model_dump(*args, **kwargs)
        if not data.get("contextual_voice_patterns"):
            data.pop("contextual_voice_patterns", None)
        return data

    @model_validator(mode="after")
    def graph_is_referentially_complete(self) -> "PersonaManifestCandidate":
        span_ids = [item.span_id for item in self.source_spans]
        _unique(span_ids, "source span IDs")
        known_spans = set(span_ids)

        entity_groups = (
            [item.claim_id for item in self.claims],
            [item.experience_id for item in self.formative_experiences],
            [item.link_id for item in self.formative_links],
            [item.capsule_id for item in self.meaning_capsules],
            [item.premise_template_id for item in self.premise_templates],
        )
        entity_ids = [item for group in entity_groups for item in group]
        pattern_ids = [item.pattern_id for item in self.contextual_voice_patterns]
        _unique(entity_ids + pattern_ids, "persona graph entity IDs")
        known_entities = set(entity_ids)

        def require_spans(values: Sequence[str], owner: str) -> None:
            missing = set(values).difference(known_spans)
            if missing:
                raise ValueError(f"{owner} references unknown source spans: {sorted(missing)}")

        claims = {item.claim_id: item for item in self.claims}
        experiences = {item.experience_id: item for item in self.formative_experiences}
        links = {item.link_id: item for item in self.formative_links}
        capsules = {item.capsule_id: item for item in self.meaning_capsules}
        templates = {item.premise_template_id: item for item in self.premise_templates}

        dependency_graph: Dict[str, List[str]] = {}
        for claim in self.claims:
            require_spans(claim.source_span_ids, claim.claim_id)
            missing = set(claim.required_dependency_ids).difference(known_entities)
            if missing:
                raise ValueError(
                    f"{claim.claim_id} references unknown dependencies: {sorted(missing)}"
                )
            dependency_graph[claim.claim_id] = [
                item for item in claim.required_dependency_ids if item in claims
            ]

        for experience in self.formative_experiences:
            require_spans(experience.source_span_ids, experience.experience_id)

        tension_pairs = set()
        for link in self.formative_links:
            require_spans(link.source_span_ids, link.link_id)
            if link.relation in {
                FormativeLinkType.SUPPORTS,
                FormativeLinkType.EXPLAINS,
                FormativeLinkType.EXPRESSES,
                FormativeLinkType.SHAPES_ATTACHMENT,
            }:
                if link.from_id not in experiences or link.to_id not in claims:
                    raise ValueError(
                        f"{link.relation.value} links must point from experience to claim"
                    )
            elif link.relation == FormativeLinkType.TENSIONS_WITH:
                if link.from_id not in claims or link.to_id not in claims:
                    raise ValueError("tensions_with links must connect two claims")
                pair = tuple(sorted((link.from_id, link.to_id)))
                if pair in tension_pairs:
                    raise ValueError("duplicate tensions_with claim pair")
                tension_pairs.add(pair)
            elif link.relation == FormativeLinkType.RELATIONSHIP_SPECIFIC:
                if link.from_id not in claims and link.from_id not in experiences:
                    raise ValueError(
                        "relationship_specific links must start at a claim or experience"
                    )
                if link.to_id not in templates:
                    raise ValueError(
                        "relationship_specific links must target a premise template"
                    )

        capsules_by_claim: Dict[str, List[MeaningCapsuleCandidate]] = {}
        for capsule in self.meaning_capsules:
            require_spans(capsule.source_span_ids, capsule.capsule_id)
            if capsule.claim_id not in claims:
                raise ValueError(f"{capsule.capsule_id} references an unknown claim")
            if not set(capsule.experience_ids).issubset(experiences):
                raise ValueError(f"{capsule.capsule_id} references unknown experiences")
            if not set(capsule.link_ids).issubset(links):
                raise ValueError(f"{capsule.capsule_id} references unknown links")
            for link_id in capsule.link_ids:
                link = links[link_id]
                if link.to_id != capsule.claim_id or link.from_id not in capsule.experience_ids:
                    raise ValueError(
                        f"{capsule.capsule_id} contains a link that does not ground its claim"
                    )
            capsules_by_claim.setdefault(capsule.claim_id, []).append(capsule)

        grounded_kinds = {
            PersonaClaimKind.SELF_CONCEPT,
            PersonaClaimKind.VALUE,
            PersonaClaimKind.DESIRE,
            PersonaClaimKind.FEAR,
            PersonaClaimKind.NEED,
            PersonaClaimKind.TENDENCY,
        }
        for claim in self.claims:
            if (
                claim.activation_tier == PersonaActivationTier.FOUNDATION
                and claim.applicability == PersonaApplicability.APPLICABLE
                and claim.kind in grounded_kinds
                and claim.claim_id not in capsules_by_claim
            ):
                raise ValueError(
                    f"foundation claim {claim.claim_id!r} requires a Meaning Capsule"
                )

        for template in self.premise_templates:
            require_spans(template.source_span_ids, template.premise_template_id)
            for experience_id in template.premise_experience_ids:
                experience = experiences.get(experience_id)
                if experience is None:
                    raise ValueError(
                        f"{template.premise_template_id} references an unknown experience"
                    )
                if experience.scope != PersonaScope.CANONICAL_RELATIONSHIP:
                    raise ValueError(
                        "premise experiences must have canonical_relationship scope"
                    )

        for pattern in self.contextual_voice_patterns:
            require_spans(pattern.source_span_ids, pattern.pattern_id)
            missing_claims = set(pattern.required_claim_ids).difference(claims)
            if missing_claims:
                raise ValueError(
                    f"{pattern.pattern_id} references unknown voice claims: "
                    f"{sorted(missing_claims)}"
                )
            missing_experiences = set(pattern.required_experience_ids).difference(
                experiences
            )
            if missing_experiences:
                raise ValueError(
                    f"{pattern.pattern_id} references unknown experiences: "
                    f"{sorted(missing_experiences)}"
                )
            missing_templates = set(
                pattern.canonical_premise_template_ids
            ).difference(templates)
            if missing_templates:
                raise ValueError(
                    f"{pattern.pattern_id} references unknown premise templates: "
                    f"{sorted(missing_templates)}"
                )

            required_claims = [claims[item] for item in pattern.required_claim_ids]
            if any(
                claim.kind != PersonaClaimKind.VOICE
                or claim.activation_tier != PersonaActivationTier.SITUATIONAL
                or claim.applicability != PersonaApplicability.APPLICABLE
                for claim in required_claims
            ):
                raise ValueError(
                    "contextual voice patterns may depend only on applicable "
                    "SITUATIONAL VOICE claims"
                )
            required_experiences = [
                experiences[item] for item in pattern.required_experience_ids
            ]
            dependency_scopes = {
                item.scope for item in (*required_claims, *required_experiences)
            }
            if (
                pattern.scope == PersonaScope.CHARACTER
                and dependency_scopes.difference({PersonaScope.CHARACTER})
            ):
                raise ValueError(
                    "character voice patterns cannot inherit relationship-scoped "
                    "dependencies"
                )
            if pattern.scope == PersonaScope.CANONICAL_RELATIONSHIP:
                if PersonaScope.CANONICAL_RELATIONSHIP not in dependency_scopes:
                    raise ValueError(
                        "canonical relationship voice patterns require a canonical "
                        "relationship dependency"
                    )
            if (
                pattern.scope == PersonaScope.RELATIONSHIP_TENDENCY
                and PersonaScope.CANONICAL_RELATIONSHIP in dependency_scopes
            ):
                raise ValueError(
                    "relationship tendencies cannot inherit canonical relationship "
                    "dependencies"
                )

        def dependency_children(entity_id: str) -> Sequence[str]:
            if entity_id in claims:
                return claims[entity_id].required_dependency_ids
            if entity_id in capsules:
                capsule = capsules[entity_id]
                return (
                    capsule.claim_id,
                    *capsule.experience_ids,
                    *capsule.link_ids,
                )
            if entity_id in links:
                link = links[entity_id]
                return (link.from_id, link.to_id)
            if entity_id in templates:
                return templates[entity_id].premise_experience_ids
            return ()

        # Applicable character meaning must never reactivate content that the
        # compiler marked as incapable of granting host authority. Check the
        # complete graph closure, not just direct claim-to-claim dependencies.
        for root_claim in self.claims:
            if root_claim.applicability != PersonaApplicability.APPLICABLE:
                continue
            pending = list(root_claim.required_dependency_ids)
            visited_dependencies = set()
            while pending:
                dependency_id = pending.pop()
                if dependency_id in visited_dependencies:
                    continue
                visited_dependencies.add(dependency_id)
                dependency_claim = claims.get(dependency_id)
                if (
                    dependency_claim is not None
                    and dependency_claim.applicability
                    == PersonaApplicability.INAPPLICABLE_HOST_AUTHORITY
                ):
                    raise ValueError(
                        f"applicable claim {root_claim.claim_id!r} dependency closure "
                        f"references inapplicable host-authority claim "
                        f"{dependency_id!r}"
                    )
                pending.extend(dependency_children(dependency_id))

        visiting = set()
        visited = set()

        def visit(claim_id: str) -> None:
            if claim_id in visiting:
                raise ValueError("claim required_dependency_ids must be acyclic")
            if claim_id in visited:
                return
            visiting.add(claim_id)
            for dependency in dependency_graph.get(claim_id, []):
                visit(dependency)
            visiting.remove(claim_id)
            visited.add(claim_id)

        for claim_id in dependency_graph:
            visit(claim_id)
        return self


@dataclass(frozen=True)
class PersonaCompilationProposal:
    """One immutable-content compilation proposal revision and review state."""

    proposal_id: str
    revision: int
    blueprint_id: str
    blueprint_revision: int
    source_sha256: str
    candidate: PersonaManifestCandidate
    content_fingerprint: str
    status: PersonaCompilationStatus = PersonaCompilationStatus.PENDING
    parent_revision: Optional[int] = None
    created_at: str = field(default_factory=utc_now)
    created_by: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    decision_reason: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in (
            "proposal_id",
            "blueprint_id",
            "source_sha256",
            "content_fingerprint",
            "created_at",
        ):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), field_name))
        if self.revision < 1 or self.blueprint_revision < 1:
            raise ValueError("proposal and blueprint revisions must be positive")
        if self.revision == 1 and self.parent_revision is not None:
            raise ValueError("first proposal revision cannot have a parent revision")
        if self.revision > 1 and self.parent_revision != self.revision - 1:
            raise ValueError("proposal parent_revision must name the immediately preceding revision")
        candidate = self.candidate
        if not isinstance(candidate, PersonaManifestCandidate):
            candidate = PersonaManifestCandidate.model_validate(candidate)
            object.__setattr__(self, "candidate", candidate)
        status = self.status
        if isinstance(status, str):
            status = PersonaCompilationStatus(status)
            object.__setattr__(self, "status", status)
        for fingerprint_name in ("source_sha256", "content_fingerprint"):
            value = getattr(self, fingerprint_name)
            if len(value) != 64:
                raise ValueError(f"{fingerprint_name} must be a SHA-256 hexadecimal digest")
            try:
                int(value, 16)
            except ValueError as exc:
                raise ValueError(f"{fingerprint_name} must be hexadecimal") from exc
        expected_fingerprint = _sha256_text(
            _canonical_json(
                {
                    "blueprint_id": self.blueprint_id,
                    "blueprint_revision": self.blueprint_revision,
                    "source_sha256": self.source_sha256,
                    "candidate": candidate.model_dump(mode="json"),
                }
            )
        )
        if self.content_fingerprint != expected_fingerprint:
            raise ValueError("proposal content_fingerprint does not match its immutable content")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "revision": self.revision,
            "parent_revision": self.parent_revision,
            "blueprint_id": self.blueprint_id,
            "blueprint_revision": self.blueprint_revision,
            "source_sha256": self.source_sha256,
            "candidate": self.candidate.model_dump(mode="json"),
            "content_fingerprint": self.content_fingerprint,
            "status": self.status.value,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "decision_reason": self.decision_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersonaCompilationProposal":
        return cls(
            proposal_id=str(data["proposal_id"]),
            revision=int(data["revision"]),
            parent_revision=(
                int(data["parent_revision"]) if data.get("parent_revision") is not None else None
            ),
            blueprint_id=str(data["blueprint_id"]),
            blueprint_revision=int(data.get("blueprint_revision", 1)),
            source_sha256=str(data["source_sha256"]),
            candidate=PersonaManifestCandidate.model_validate(data["candidate"]),
            content_fingerprint=str(data["content_fingerprint"]),
            status=PersonaCompilationStatus(
                data.get("status", PersonaCompilationStatus.PENDING.value)
            ),
            created_at=str(data["created_at"]),
            created_by=data.get("created_by"),
            decided_by=data.get("decided_by"),
            decided_at=data.get("decided_at"),
            decision_reason=data.get("decision_reason"),
        )


@dataclass(frozen=True)
class PersonaManifest:
    """An approved, immutable Persona Interpretation manifest."""

    manifest_id: str
    blueprint_id: str
    blueprint_revision: int
    source_sha256: str
    candidate: PersonaManifestCandidate
    content_fingerprint: str
    approved_proposal_id: str
    approved_revision: int
    approved_by: str
    approved_at: str

    def __post_init__(self) -> None:
        for field_name in (
            "manifest_id",
            "blueprint_id",
            "source_sha256",
            "content_fingerprint",
            "approved_proposal_id",
            "approved_by",
            "approved_at",
        ):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), field_name))
        if self.blueprint_revision < 1 or self.approved_revision < 1:
            raise ValueError("manifest revisions must be positive")
        if not isinstance(self.candidate, PersonaManifestCandidate):
            object.__setattr__(
                self,
                "candidate",
                PersonaManifestCandidate.model_validate(self.candidate),
            )
        expected_fingerprint = _sha256_text(
            _canonical_json(
                {
                    "blueprint_id": self.blueprint_id,
                    "blueprint_revision": self.blueprint_revision,
                    "source_sha256": self.source_sha256,
                    "candidate": self.candidate.model_dump(mode="json"),
                }
            )
        )
        if self.content_fingerprint != expected_fingerprint:
            raise ValueError("manifest content_fingerprint does not match its immutable content")

    @property
    def claims(self):
        return tuple(self.candidate.claims)

    @property
    def formative_experiences(self):
        return tuple(self.candidate.formative_experiences)

    @property
    def formative_links(self):
        return tuple(self.candidate.formative_links)

    @property
    def meaning_capsules(self):
        return tuple(self.candidate.meaning_capsules)

    @property
    def premise_templates(self):
        return tuple(self.candidate.premise_templates)

    @property
    def contextual_voice_patterns(self):
        return tuple(self.candidate.contextual_voice_patterns)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "blueprint_id": self.blueprint_id,
            "blueprint_revision": self.blueprint_revision,
            "source_sha256": self.source_sha256,
            "candidate": self.candidate.model_dump(mode="json"),
            "content_fingerprint": self.content_fingerprint,
            "approved_proposal_id": self.approved_proposal_id,
            "approved_revision": self.approved_revision,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersonaManifest":
        return cls(
            manifest_id=str(data["manifest_id"]),
            blueprint_id=str(data["blueprint_id"]),
            blueprint_revision=int(data.get("blueprint_revision", 1)),
            source_sha256=str(data["source_sha256"]),
            candidate=PersonaManifestCandidate.model_validate(data["candidate"]),
            content_fingerprint=str(data["content_fingerprint"]),
            approved_proposal_id=str(data["approved_proposal_id"]),
            approved_revision=int(data["approved_revision"]),
            approved_by=str(data["approved_by"]),
            approved_at=str(data["approved_at"]),
        )


__all__ = [
    "BaselineLevel",
    "CanonicalPremiseTemplateCandidate",
    "ContextualVoicePatternCandidate",
    "FormativeExperienceCandidate",
    "FormativeLinkCandidate",
    "FormativeLinkType",
    "MeaningCapsuleCandidate",
    "PersonaActivationTier",
    "PersonaApplicability",
    "PersonaClaimCandidate",
    "PersonaClaimKind",
    "PersonaCompilationConflictError",
    "PersonaCompilationDecision",
    "PersonaCompilationProposal",
    "PersonaCompilationStatus",
    "PersonaDeliveryMode",
    "PersonaInterpretationBasis",
    "PersonaManifest",
    "PersonaManifestCandidate",
    "PersonaScope",
    "PersonaSourceSpan",
    "RelationshipPremiseMode",
    "VoicePatternCondition",
    "VoicePatternConditionType",
]
