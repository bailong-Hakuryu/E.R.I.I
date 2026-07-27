"""Domain models for isolated relationship-persona histories.

The models in this module are deliberately independent from LLM output.  They
represent the validated facts accepted by the relationship kernel.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import math
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "blueprint_id", _require_text(self.blueprint_id, "blueprint_id"))
        object.__setattr__(self, "source_text", _require_text(self.source_text, "source_text"))
        _require_json(self.compiled, "compiled")
        object.__setattr__(self, "compiled", _freeze_json(self.compiled))
        object.__setattr__(self, "created_at", _require_text(self.created_at, "created_at"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "source_text": self.source_text,
            "compiled": _thaw_json(self.compiled),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CharacterBlueprint":
        return cls(
            blueprint_id=str(data["blueprint_id"]),
            source_text=str(data["source_text"]),
            compiled=data.get("compiled", {}),
            created_at=str(data["created_at"]),
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
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationshipProfile":
        return cls(
            relationship_id=str(data["relationship_id"]),
            persona_id=str(data["persona_id"]),
            agent_identity_id=str(data["agent_identity_id"]),
            user_identity_id=str(data["user_identity_id"]),
            agent_id=str(data["agent_id"]),
            user_id=str(data["user_id"]),
            blueprint=CharacterBlueprint.from_dict(data["blueprint"]),
            created_at=str(data["created_at"]),
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
