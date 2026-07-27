"""Domain models for isolated relationship-persona histories.

The models in this module are deliberately independent from LLM output.  They
represent the validated facts accepted by the relationship kernel.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence


RELATIONSHIP_DIMENSIONS = (
    "familiarity",
    "trust",
    "intimacy",
    "safety",
    "conflict_tension",
)
MAX_AUTOMATIC_STATE_DELTA = 0.1


def utc_now() -> str:
    """Returns an ISO-8601 UTC timestamp for new domain records."""
    return datetime.now(timezone.utc).isoformat()


def _freeze_json(value: Any) -> Any:
    """Converts JSON-like data into recursively immutable values."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    """Converts recursively immutable values back to JSON-compatible data."""
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_json(value: Any, field_name: str) -> None:
    try:
        json.dumps(_thaw_json(value), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain JSON-compatible values") from exc


class PersonaConflictError(ValueError):
    """Raised when a caller tries to overwrite a relationship's persona source."""


class RelationshipNotFoundError(LookupError):
    """Raised when relationship behavior is requested before initialization."""


class EventConflictError(ValueError):
    """Raised when an existing event ID is reused with different content."""


class IdentityKind(str, Enum):
    """Kinds of stable identity represented in a relationship."""

    AGENT = "agent"
    USER = "user"


class RelationshipPremiseMode(str, Enum):
    """Explicit narrative starting modes for one isolated relationship."""

    FRESH = "fresh"
    ADDRESS_ONLY = "address_only"
    CANONICAL_CONTINUATION = "canonical_continuation"


class BaselineLevel(str, Enum):
    """Qualitative inputs accepted by the deterministic premise policy."""

    MINIMAL = "minimal"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    DEEP = "deep"
    MIXED = "mixed"


class RelationshipEventType(str, Enum):
    """Canonical event categories accepted by the relationship kernel."""

    SHARED_EXPERIENCE = "shared_experience"
    OBSERVATION = "observation"
    PROMISE = "promise"
    CONFLICT = "conflict"
    REPAIR = "repair"
    REFLECTION = "reflection"
    CORRECTION = "correction"


class BeliefOperation(str, Enum):
    """Operations supported by the current-belief projector."""

    SET = "set"
    RETRACT = "retract"


@dataclass(frozen=True)
class CharacterBlueprint:
    """Immutable authority snapshot of imported persona source and compilation."""

    blueprint_id: str
    source_text: str
    compiled: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    revision: int = 1
    source_sha256: Optional[str] = None
    source_format: str = "text/plain"
    source_name: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "blueprint_id", _require_text(self.blueprint_id, "blueprint_id"))
        if not isinstance(self.source_text, str) or not self.source_text.strip():
            raise ValueError("source_text must be a non-empty string")
        # The imported source is the authority. Preserve it byte-for-byte at the
        # Python string boundary, including intentional leading/trailing space.
        expected_hash = hashlib.sha256(self.source_text.encode("utf-8")).hexdigest()
        supplied_hash = self.source_sha256
        if supplied_hash is not None:
            if not isinstance(supplied_hash, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", supplied_hash
            ):
                raise ValueError("source_sha256 must be a 64-character hex digest")
            if supplied_hash.lower() != expected_hash:
                raise ValueError("source_sha256 does not match source_text")
        object.__setattr__(self, "source_sha256", expected_hash)
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise ValueError("revision must be a positive integer")
        if self.revision < 1:
            raise ValueError("revision must be a positive integer")
        object.__setattr__(self, "source_format", _require_text(self.source_format, "source_format"))
        if self.source_name is not None:
            object.__setattr__(self, "source_name", _require_text(self.source_name, "source_name"))
        _require_json(self.compiled, "compiled")
        object.__setattr__(self, "compiled", _freeze_json(self.compiled))
        object.__setattr__(self, "created_at", _require_text(self.created_at, "created_at"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "source_text": self.source_text,
            "compiled": _thaw_json(self.compiled),
            "created_at": self.created_at,
            "revision": self.revision,
            "source_sha256": self.source_sha256,
            "source_format": self.source_format,
            "source_name": self.source_name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CharacterBlueprint":
        return cls(
            blueprint_id=str(data["blueprint_id"]),
            source_text=str(data["source_text"]),
            compiled=data.get("compiled", {}),
            created_at=str(data["created_at"]),
            revision=int(data.get("revision", 1)),
            source_sha256=data.get("source_sha256"),
            source_format=str(data.get("source_format", "text/plain")),
            source_name=data.get("source_name"),
        )


@dataclass(frozen=True)
class PremiseExperience:
    """An imported relationship premise experience grounded in source spans."""

    experience_id: str
    summary: str
    source_spans: Sequence[Mapping[str, Any]]

    _ALLOWED_SPAN_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "start",
            "end",
            "quote",
            "source_sha256",
            "section",
            "blueprint_id",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "experience_id",
            _require_text(self.experience_id, "experience_id"),
        )
        object.__setattr__(self, "summary", _require_text(self.summary, "summary"))
        if isinstance(self.source_spans, (str, bytes)) or not self.source_spans:
            raise ValueError("source_spans must contain at least one source range")

        normalized = []
        for index, raw_span in enumerate(self.source_spans):
            if not isinstance(raw_span, Mapping):
                raise ValueError(f"source_spans[{index}] must be a mapping")
            unknown = set(raw_span) - self._ALLOWED_SPAN_KEYS
            if unknown:
                raise ValueError(
                    f"source_spans[{index}] contains unknown keys: {sorted(unknown)}"
                )
            if "start" not in raw_span or "end" not in raw_span:
                raise ValueError(f"source_spans[{index}] requires start and end")
            start = raw_span["start"]
            end = raw_span["end"]
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
            ):
                raise ValueError(
                    f"source_spans[{index}] must use integer offsets with 0 <= start < end"
                )
            span = dict(raw_span)
            if "quote" in span:
                if not isinstance(span["quote"], str) or not span["quote"]:
                    raise ValueError(f"source_spans[{index}].quote must be a non-empty string")
            for optional_text in ("source_sha256", "section", "blueprint_id"):
                if optional_text in span:
                    span[optional_text] = _require_text(
                        span[optional_text],
                        f"source_spans[{index}].{optional_text}",
                    )
            normalized.append(_freeze_json(span))
        object.__setattr__(self, "source_spans", tuple(normalized))

    def validate_against(self, blueprint: CharacterBlueprint) -> None:
        """Checks every range, optional quote, and source identity exactly."""
        for index, span in enumerate(self.source_spans):
            start = span["start"]
            end = span["end"]
            if end > len(blueprint.source_text):
                raise ValueError(f"source_spans[{index}] exceeds the Character Blueprint")
            if span.get("source_sha256") not in (None, blueprint.source_sha256):
                raise ValueError(f"source_spans[{index}] references a different source hash")
            if span.get("blueprint_id") not in (None, blueprint.blueprint_id):
                raise ValueError(f"source_spans[{index}] references a different blueprint")
            if "quote" in span and blueprint.source_text[start:end] != span["quote"]:
                raise ValueError(f"source_spans[{index}] quote does not match source_text")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "summary": self.summary,
            "source_spans": [_thaw_json(span) for span in self.source_spans],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PremiseExperience":
        return cls(
            experience_id=str(data["experience_id"]),
            summary=str(data["summary"]),
            source_spans=data.get("source_spans", ()),
        )


@dataclass(frozen=True)
class RelationshipPremise:
    """Explicit, relationship-local selection of a narrative starting point."""

    premise_id: str = "fresh"
    mode: RelationshipPremiseMode = RelationshipPremiseMode.FRESH
    address_name: Optional[str] = None
    canonical_role: Optional[str] = None
    experiences: Sequence[PremiseExperience] = field(default_factory=tuple)
    baseline_levels: Mapping[str, BaselineLevel] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "premise_id", _require_text(self.premise_id, "premise_id"))
        mode = self.mode
        if isinstance(mode, str):
            mode = RelationshipPremiseMode(mode)
            object.__setattr__(self, "mode", mode)
        if self.address_name is not None:
            object.__setattr__(
                self,
                "address_name",
                _require_text(self.address_name, "address_name"),
            )
        if self.canonical_role is not None:
            object.__setattr__(
                self,
                "canonical_role",
                _require_text(self.canonical_role, "canonical_role"),
            )

        experiences = tuple(
            item if isinstance(item, PremiseExperience) else PremiseExperience.from_dict(item)
            for item in self.experiences
        )
        object.__setattr__(self, "experiences", experiences)

        levels: Dict[str, BaselineLevel] = {}
        for dimension, raw_level in self.baseline_levels.items():
            if dimension not in RELATIONSHIP_DIMENSIONS:
                raise ValueError(f"unknown baseline dimension: {dimension}")
            if not isinstance(raw_level, (str, BaselineLevel)):
                raise ValueError("baseline levels must be qualitative BaselineLevel values")
            levels[dimension] = BaselineLevel(raw_level)
        object.__setattr__(self, "baseline_levels", MappingProxyType(levels))

        if mode == RelationshipPremiseMode.FRESH:
            if self.address_name or self.canonical_role or experiences or levels:
                raise ValueError(
                    "fresh premise cannot carry an address, canonical role, "
                    "premise experiences, or custom baseline"
                )
        elif mode == RelationshipPremiseMode.ADDRESS_ONLY:
            if not self.address_name:
                raise ValueError("address_only premise requires address_name")
            if self.canonical_role or experiences or levels:
                raise ValueError(
                    "address_only premise cannot bind a canonical role, import "
                    "experiences, or change the baseline"
                )
        else:
            if not self.canonical_role:
                raise ValueError("canonical_continuation requires canonical_role")
            if not experiences:
                raise ValueError("canonical_continuation requires premise experiences")
            if set(levels) != set(RELATIONSHIP_DIMENSIONS):
                raise ValueError(
                    "canonical_continuation requires one qualitative level for "
                    "every relationship dimension"
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "premise_id": self.premise_id,
            "mode": self.mode.value,
            "address_name": self.address_name,
            "canonical_role": self.canonical_role,
            "experiences": [experience.to_dict() for experience in self.experiences],
            "baseline_levels": {
                dimension: level.value for dimension, level in self.baseline_levels.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationshipPremise":
        return cls(
            premise_id=str(data.get("premise_id", "fresh")),
            mode=RelationshipPremiseMode(
                data.get("mode", RelationshipPremiseMode.FRESH.value)
            ),
            address_name=data.get("address_name"),
            canonical_role=data.get("canonical_role"),
            experiences=[
                PremiseExperience.from_dict(item) for item in data.get("experiences", [])
            ],
            baseline_levels=data.get("baseline_levels", {}),
        )


@dataclass(frozen=True)
class RelationshipBaseline:
    """Immutable numeric starting state projected from qualitative premise levels."""

    policy_version: str
    premise_mode: RelationshipPremiseMode
    qualitative_levels: Mapping[str, BaselineLevel]
    state: Mapping[str, float]
    supporting_experience_ids: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_version",
            _require_text(self.policy_version, "policy_version"),
        )
        mode = self.premise_mode
        if isinstance(mode, str):
            mode = RelationshipPremiseMode(mode)
            object.__setattr__(self, "premise_mode", mode)
        if set(self.qualitative_levels) != set(RELATIONSHIP_DIMENSIONS):
            raise ValueError("qualitative_levels must contain all relationship dimensions")
        levels: Dict[str, BaselineLevel] = {}
        for dimension in RELATIONSHIP_DIMENSIONS:
            raw_level = self.qualitative_levels[dimension]
            if not isinstance(raw_level, (str, BaselineLevel)):
                raise ValueError("qualitative_levels must use BaselineLevel values")
            levels[dimension] = BaselineLevel(raw_level)
        object.__setattr__(self, "qualitative_levels", MappingProxyType(levels))

        if set(self.state) != set(RELATIONSHIP_DIMENSIONS):
            raise ValueError("baseline state must contain all relationship dimensions")
        state: Dict[str, float] = {}
        for dimension in RELATIONSHIP_DIMENSIONS:
            value = self.state[dimension]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"baseline state for {dimension} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
                raise ValueError(f"baseline state for {dimension} must be between 0 and 1")
            state[dimension] = numeric
        object.__setattr__(self, "state", MappingProxyType(state))

        experience_ids = tuple(
            _require_text(item, "supporting_experience_id")
            for item in self.supporting_experience_ids
        )
        if len(set(experience_ids)) != len(experience_ids):
            raise ValueError("supporting_experience_ids cannot contain duplicates")
        object.__setattr__(self, "supporting_experience_ids", experience_ids)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "premise_mode": self.premise_mode.value,
            "qualitative_levels": {
                dimension: level.value
                for dimension, level in self.qualitative_levels.items()
            },
            "state": dict(self.state),
            "supporting_experience_ids": list(self.supporting_experience_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationshipBaseline":
        return cls(
            policy_version=str(data["policy_version"]),
            premise_mode=RelationshipPremiseMode(data["premise_mode"]),
            qualitative_levels=data["qualitative_levels"],
            state=data["state"],
            supporting_experience_ids=data.get("supporting_experience_ids", ()),
        )


@dataclass(frozen=True)
class PremisePolicy:
    """Deterministically maps qualitative premise inputs to a numeric baseline."""

    version: str = "premise-v1"

    LEVEL_VALUES: ClassVar[Mapping[BaselineLevel, float]] = MappingProxyType(
        {
            BaselineLevel.MINIMAL: 0.0,
            BaselineLevel.LOW: 0.25,
            BaselineLevel.MODERATE: 0.5,
            BaselineLevel.HIGH: 0.75,
            BaselineLevel.DEEP: 1.0,
            BaselineLevel.MIXED: 0.5,
        }
    )
    DEFAULT_LEVELS: ClassVar[Mapping[str, BaselineLevel]] = MappingProxyType(
        {
            "familiarity": BaselineLevel.MINIMAL,
            "trust": BaselineLevel.MODERATE,
            "intimacy": BaselineLevel.MINIMAL,
            "safety": BaselineLevel.MODERATE,
            "conflict_tension": BaselineLevel.MINIMAL,
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", _require_text(self.version, "premise policy version"))

    def project(
        self,
        premise: RelationshipPremise,
        blueprint: Optional[CharacterBlueprint] = None,
    ) -> RelationshipBaseline:
        """Builds the only valid baseline for a premise under this policy."""
        if not isinstance(premise, RelationshipPremise):
            premise = RelationshipPremise.from_dict(premise)
        if premise.experiences and blueprint is None:
            raise ValueError("a Character Blueprint is required to validate premise source spans")
        if blueprint is not None:
            for experience in premise.experiences:
                experience.validate_against(blueprint)

        levels = (
            dict(premise.baseline_levels)
            if premise.mode == RelationshipPremiseMode.CANONICAL_CONTINUATION
            else dict(self.DEFAULT_LEVELS)
        )
        return RelationshipBaseline(
            policy_version=self.version,
            premise_mode=premise.mode,
            qualitative_levels=levels,
            state={
                dimension: self.LEVEL_VALUES[levels[dimension]]
                for dimension in RELATIONSHIP_DIMENSIONS
            },
            supporting_experience_ids=tuple(
                experience.experience_id for experience in premise.experiences
            ),
        )


@dataclass(frozen=True)
class RelationshipProfile:
    """Stable identity and persona IDs for one isolated Agent x User history."""

    relationship_id: str
    persona_id: str
    agent_identity_id: str
    user_identity_id: str
    agent_id: str
    user_id: str
    blueprint: CharacterBlueprint
    created_at: str = field(default_factory=utc_now)
    premise: RelationshipPremise = field(default_factory=RelationshipPremise)
    baseline: Optional[RelationshipBaseline] = None
    manifest_id: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in (
            "relationship_id",
            "persona_id",
            "agent_identity_id",
            "user_identity_id",
            "agent_id",
            "user_id",
            "created_at",
        ):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), field_name))
        if not isinstance(self.blueprint, CharacterBlueprint):
            object.__setattr__(self, "blueprint", CharacterBlueprint.from_dict(self.blueprint))
        premise = self.premise
        if not isinstance(premise, RelationshipPremise):
            premise = RelationshipPremise.from_dict(premise)
            object.__setattr__(self, "premise", premise)
        expected_baseline = PremisePolicy().project(premise, self.blueprint)
        baseline = self.baseline
        if baseline is None:
            baseline = expected_baseline
            object.__setattr__(self, "baseline", baseline)
        elif not isinstance(baseline, RelationshipBaseline):
            baseline = RelationshipBaseline.from_dict(baseline)
            object.__setattr__(self, "baseline", baseline)
        if baseline.to_dict() != expected_baseline.to_dict():
            raise ValueError(
                "relationship baseline does not match its qualitative premise and policy"
            )
        if self.manifest_id is not None:
            object.__setattr__(
                self,
                "manifest_id",
                _require_text(self.manifest_id, "manifest_id"),
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "persona_id": self.persona_id,
            "agent_identity_id": self.agent_identity_id,
            "user_identity_id": self.user_identity_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "blueprint": self.blueprint.to_dict(),
            "created_at": self.created_at,
            "premise": self.premise.to_dict(),
            "baseline": self.baseline.to_dict(),
            "manifest_id": self.manifest_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationshipProfile":
        blueprint = CharacterBlueprint.from_dict(data["blueprint"])
        premise = RelationshipPremise.from_dict(data.get("premise", {}))
        return cls(
            relationship_id=str(data["relationship_id"]),
            persona_id=str(data["persona_id"]),
            agent_identity_id=str(data["agent_identity_id"]),
            user_identity_id=str(data["user_identity_id"]),
            agent_id=str(data["agent_id"]),
            user_id=str(data["user_id"]),
            blueprint=blueprint,
            created_at=str(data["created_at"]),
            premise=premise,
            baseline=(
                RelationshipBaseline.from_dict(data["baseline"])
                if data.get("baseline") is not None
                else PremisePolicy().project(premise, blueprint)
            ),
            manifest_id=data.get("manifest_id"),
        )


@dataclass(frozen=True)
class BeliefUpdate:
    """A candidate accepted into history to set or retract a current belief."""

    key: str
    value: Any = None
    confidence: float = 1.0
    operation: BeliefOperation = BeliefOperation.SET

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _require_text(self.key, "belief key"))
        operation = self.operation
        if isinstance(operation, str):
            operation = BeliefOperation(operation)
            object.__setattr__(self, "operation", operation)
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("belief confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", confidence)
        _require_json(self.value, "belief value")
        object.__setattr__(self, "value", _freeze_json(self.value))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": _thaw_json(self.value),
            "confidence": self.confidence,
            "operation": self.operation.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BeliefUpdate":
        return cls(
            key=str(data["key"]),
            value=data.get("value"),
            confidence=float(data.get("confidence", 1.0)),
            operation=BeliefOperation(data.get("operation", BeliefOperation.SET.value)),
        )


@dataclass(frozen=True)
class RelationshipEvent:
    """An immutable historical event used to rebuild current relationship state."""

    event_id: str
    relationship_id: str
    event_type: RelationshipEventType
    content: str
    state_delta: Mapping[str, float] = field(default_factory=dict)
    belief_updates: Sequence[BeliefUpdate] = field(default_factory=tuple)
    occurred_at: Optional[str] = None
    recorded_at: str = field(default_factory=utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_text(self.event_id, "event_id"))
        object.__setattr__(
            self,
            "relationship_id",
            _require_text(self.relationship_id, "relationship_id"),
        )
        event_type = self.event_type
        if isinstance(event_type, str):
            event_type = RelationshipEventType(event_type)
            object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "content", _require_text(self.content, "event content"))
        object.__setattr__(self, "recorded_at", _require_text(self.recorded_at, "recorded_at"))
        if self.occurred_at is not None:
            object.__setattr__(
                self,
                "occurred_at",
                _require_text(self.occurred_at, "occurred_at"),
            )

        normalized_delta: Dict[str, float] = {}
        for dimension, raw_delta in self.state_delta.items():
            if dimension not in RELATIONSHIP_DIMENSIONS:
                raise ValueError(f"unknown relationship state dimension: {dimension}")
            if isinstance(raw_delta, bool) or not isinstance(raw_delta, (int, float)):
                raise ValueError(f"state delta for {dimension} must be numeric")
            delta = float(raw_delta)
            if not math.isfinite(delta):
                raise ValueError(f"state delta for {dimension} must be finite")
            if abs(delta) > MAX_AUTOMATIC_STATE_DELTA:
                raise ValueError(
                    f"state delta for {dimension} exceeds the automatic limit "
                    f"of {MAX_AUTOMATIC_STATE_DELTA}"
                )
            normalized_delta[dimension] = delta
        object.__setattr__(self, "state_delta", MappingProxyType(normalized_delta))

        updates: List[BeliefUpdate] = []
        for update in self.belief_updates:
            updates.append(
                update if isinstance(update, BeliefUpdate) else BeliefUpdate.from_dict(update)
            )
        object.__setattr__(self, "belief_updates", tuple(updates))
        _require_json(self.metadata, "event metadata")
        object.__setattr__(self, "metadata", _freeze_json(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "relationship_id": self.relationship_id,
            "event_type": self.event_type.value,
            "content": self.content,
            "state_delta": dict(self.state_delta),
            "belief_updates": [update.to_dict() for update in self.belief_updates],
            "metadata": _thaw_json(self.metadata),
            "occurred_at": self.occurred_at,
            "recorded_at": self.recorded_at,
        }

    def same_payload_as(self, other: "RelationshipEvent") -> bool:
        """Compares idempotent event input while preserving first-recorded time."""
        own_data = self.to_dict()
        other_data = other.to_dict()
        own_data.pop("recorded_at", None)
        other_data.pop("recorded_at", None)
        return own_data == other_data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationshipEvent":
        return cls(
            event_id=str(data["event_id"]),
            relationship_id=str(data["relationship_id"]),
            event_type=RelationshipEventType(data["event_type"]),
            content=str(data["content"]),
            state_delta=data.get("state_delta", {}),
            belief_updates=[
                BeliefUpdate.from_dict(item) for item in data.get("belief_updates", [])
            ],
            metadata=data.get("metadata", {}),
            occurred_at=data.get("occurred_at"),
            recorded_at=str(data["recorded_at"]),
        )


@dataclass(frozen=True)
class RelationshipState:
    """Normalized internal numeric state derived from accepted events."""

    familiarity: float = 0.0
    trust: float = 0.5
    intimacy: float = 0.0
    safety: float = 0.5
    conflict_tension: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {dimension: getattr(self, dimension) for dimension in RELATIONSHIP_DIMENSIONS}


@dataclass(frozen=True)
class CurrentBelief:
    """Latest supported belief plus its evidence event."""

    key: str
    value: Any
    confidence: float
    evidence_event_id: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze_json(self.value))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": _thaw_json(self.value),
            "confidence": self.confidence,
            "evidence_event_id": self.evidence_event_id,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class StateReason:
    """Latest evidence explaining one dimension of the numeric state."""

    dimension: str
    delta: float
    evidence_event_id: str
    explanation: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "delta": self.delta,
            "evidence_event_id": self.evidence_event_id,
            "explanation": self.explanation,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TemporalContext:
    """Explicitly observed wall-clock context that never mutates relationship state."""

    observed_at: str
    last_event_recorded_at: Optional[str]
    elapsed_seconds: Optional[float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _require_text(self.observed_at, "observed_at"))
        if self.last_event_recorded_at is not None:
            object.__setattr__(
                self,
                "last_event_recorded_at",
                _require_text(self.last_event_recorded_at, "last_event_recorded_at"),
            )
        if self.elapsed_seconds is not None:
            elapsed = float(self.elapsed_seconds)
            if not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError("elapsed_seconds must be a non-negative finite number")
            object.__setattr__(self, "elapsed_seconds", elapsed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "last_event_recorded_at": self.last_event_recorded_at,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass(frozen=True)
class RelationshipSnapshot:
    """Current projection returned to hosts from an isolated relationship history."""

    profile: RelationshipProfile
    state: RelationshipState
    beliefs: Mapping[str, CurrentBelief]
    state_reasons: Mapping[str, StateReason]
    event_count: int
    last_event_id: Optional[str] = None
    projection_version: int = 1
    temporal_context: Optional[TemporalContext] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "beliefs", MappingProxyType(dict(self.beliefs)))
        object.__setattr__(self, "state_reasons", MappingProxyType(dict(self.state_reasons)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "state": self.state.to_dict(),
            "beliefs": {key: belief.to_dict() for key, belief in self.beliefs.items()},
            "state_reasons": {
                key: reason.to_dict() for key, reason in self.state_reasons.items()
            },
            "event_count": self.event_count,
            "last_event_id": self.last_event_id,
            "projection_version": self.projection_version,
            "temporal_context": (
                self.temporal_context.to_dict() if self.temporal_context is not None else None
            ),
        }
