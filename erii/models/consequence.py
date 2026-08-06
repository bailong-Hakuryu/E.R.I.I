"""Immutable relationship-consequence and narrative-tension models.

The records in this module form an append-only journal independent of the
general Relationship Event ledger.  They describe an accepted relationship
consequence and later, explicit evidence about the state of the tension
created by that consequence.  In particular, elapsed time is not itself a
tension update.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Dict, Mapping, Sequence, Tuple


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_wire_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _validate_exact_fields(
    data: Mapping[str, Any],
    *,
    model_name: str,
    fields: frozenset[str],
) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{model_name} must be a mapping")
    if set(data) != fields:
        raise ValueError(f"{model_name} contains unknown or missing fields")


def _enum_value(value: object, enum_type: type[Enum], field_name: str) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not a supported value") from exc


def _unique_text_tuple(values: Sequence[object], field_name: str) -> Tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{field_name} must be a sequence")
    normalized = tuple(_require_text(item, field_name) for item in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


class ConsequenceConflictError(ValueError):
    """Raised when an immutable consequence identity has conflicting content."""


class NarrativeTensionConflictError(ConsequenceConflictError):
    """Raised when a tension link or projection violates journal causality."""


class RelationshipConsequenceKind(str, Enum):
    """Kinds of explicit consequence that one relationship event can carry."""

    HARM = "harm"
    COMFORT = "comfort"
    REFUSAL = "refusal"
    ANGER = "anger"
    BOUNDARY_EXPRESSION = "boundary_expression"
    TRUST_DECREASE = "trust_decrease"
    TEMPORARY_DISTANCE = "temporary_distance"
    RELATIONSHIP_END = "relationship_end"
    REPAIR_ATTEMPT = "repair_attempt"
    REPAIR_REFUSED = "repair_refused"
    CONFLICT = "conflict"


class NarrativeTensionOutcome(str, Enum):
    """Current evidence-backed outcomes for one narrative tension."""

    UNADDRESSED = "unaddressed"
    ADDRESSED_UNRESOLVED = "addressed_unresolved"
    MUTUALLY_RECONCILED = "mutually_reconciled"
    BOUNDARY_STABILIZED = "boundary_stabilized"
    RELATIONSHIP_ENDED = "relationship_ended"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class RelationshipConsequence:
    """An immutable journal record rooted in one accepted source decision."""

    consequence_id: str
    relationship_id: str
    tension_id: str
    source_turn_id: str
    source_revision: str
    source_decision_id: str
    source_event_id: str
    source_message_id: str
    effects: Sequence[RelationshipConsequenceKind]
    summary: str
    recorded_at: str

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "consequence_id",
            "relationship_id",
            "tension_id",
            "source_turn_id",
            "source_revision",
            "source_decision_id",
            "source_event_id",
            "source_message_id",
            "effects",
            "summary",
            "recorded_at",
        }
    )

    def __post_init__(self) -> None:
        for field_name in (
            "consequence_id",
            "relationship_id",
            "tension_id",
            "source_turn_id",
            "source_revision",
            "source_decision_id",
            "source_event_id",
            "source_message_id",
            "summary",
            "recorded_at",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        if isinstance(self.effects, (str, bytes)) or not isinstance(
            self.effects, Sequence
        ):
            raise ValueError("effects must be a non-empty sequence")
        effects = tuple(
            _enum_value(item, RelationshipConsequenceKind, "effects")
            for item in self.effects
        )
        if not effects:
            raise ValueError("effects must be a non-empty sequence")
        if len(effects) != len(set(effects)):
            raise ValueError("effects must not contain duplicates")
        effect_order = {
            value: index for index, value in enumerate(RelationshipConsequenceKind)
        }
        object.__setattr__(
            self,
            "effects",
            tuple(sorted(effects, key=effect_order.__getitem__)),
        )

    @property
    def kind(self) -> RelationshipConsequenceKind:
        """Compatibility view returning the first canonical effect."""
        return self.effects[0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consequence_id": self.consequence_id,
            "relationship_id": self.relationship_id,
            "tension_id": self.tension_id,
            "source_turn_id": self.source_turn_id,
            "source_revision": self.source_revision,
            "source_decision_id": self.source_decision_id,
            "source_event_id": self.source_event_id,
            "source_message_id": self.source_message_id,
            "effects": [item.value for item in self.effects],
            "summary": self.summary,
            "recorded_at": self.recorded_at,
        }

    def same_payload_as(self, other: object) -> bool:
        """Compares stable journal input while preserving first-recorded time."""
        if not isinstance(other, RelationshipConsequence):
            return False
        own_data = self.to_dict()
        other_data = other.to_dict()
        own_data.pop("recorded_at")
        other_data.pop("recorded_at")
        return own_data == other_data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationshipConsequence":
        _validate_exact_fields(
            data,
            model_name="RelationshipConsequence",
            fields=cls._FIELDS,
        )
        for field_name in cls._FIELDS.difference({"effects"}):
            _require_wire_string(data[field_name], field_name)
        raw_effects = data["effects"]
        if not isinstance(raw_effects, list):
            raise ValueError("effects must be an array")
        for index, value in enumerate(raw_effects):
            _require_wire_string(value, f"effects[{index}]")
        return cls(
            consequence_id=data["consequence_id"],
            relationship_id=data["relationship_id"],
            tension_id=data["tension_id"],
            source_turn_id=data["source_turn_id"],
            source_revision=data["source_revision"],
            source_decision_id=data["source_decision_id"],
            source_event_id=data["source_event_id"],
            source_message_id=data["source_message_id"],
            effects=raw_effects,
            summary=data["summary"],
            recorded_at=data["recorded_at"],
        )


@dataclass(frozen=True)
class NarrativeTensionLink:
    """A later append-only journal link updating one consequence's tension."""

    link_id: str
    relationship_id: str
    tension_id: str
    consequence_id: str
    source_turn_id: str
    source_revision: str
    source_decision_id: str
    source_event_id: str
    outcome: NarrativeTensionOutcome
    summary: str
    recorded_at: str

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "link_id",
            "relationship_id",
            "tension_id",
            "consequence_id",
            "source_turn_id",
            "source_revision",
            "source_decision_id",
            "source_event_id",
            "outcome",
            "summary",
            "recorded_at",
        }
    )

    def __post_init__(self) -> None:
        for field_name in (
            "link_id",
            "relationship_id",
            "tension_id",
            "consequence_id",
            "source_turn_id",
            "source_revision",
            "source_decision_id",
            "source_event_id",
            "summary",
            "recorded_at",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "outcome",
            _enum_value(self.outcome, NarrativeTensionOutcome, "outcome"),
        )
        if self.outcome == NarrativeTensionOutcome.UNADDRESSED:
            raise ValueError("narrative tension links cannot use unaddressed outcome")

    def to_dict(self) -> Dict[str, str]:
        return {
            "link_id": self.link_id,
            "relationship_id": self.relationship_id,
            "tension_id": self.tension_id,
            "consequence_id": self.consequence_id,
            "source_turn_id": self.source_turn_id,
            "source_revision": self.source_revision,
            "source_decision_id": self.source_decision_id,
            "source_event_id": self.source_event_id,
            "outcome": self.outcome.value,
            "summary": self.summary,
            "recorded_at": self.recorded_at,
        }

    def same_payload_as(self, other: object) -> bool:
        """Compares stable journal input while preserving first-recorded time."""
        if not isinstance(other, NarrativeTensionLink):
            return False
        own_data = self.to_dict()
        other_data = other.to_dict()
        own_data.pop("recorded_at")
        other_data.pop("recorded_at")
        return own_data == other_data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NarrativeTensionLink":
        _validate_exact_fields(
            data,
            model_name="NarrativeTensionLink",
            fields=cls._FIELDS,
        )
        for field_name in cls._FIELDS:
            _require_wire_string(data[field_name], field_name)
        return cls(
            link_id=data["link_id"],
            relationship_id=data["relationship_id"],
            tension_id=data["tension_id"],
            consequence_id=data["consequence_id"],
            source_turn_id=data["source_turn_id"],
            source_revision=data["source_revision"],
            source_decision_id=data["source_decision_id"],
            source_event_id=data["source_event_id"],
            outcome=_enum_value(
                data["outcome"],
                NarrativeTensionOutcome,
                "outcome",
            ),
            summary=data["summary"],
            recorded_at=data["recorded_at"],
        )


# Temporary compatibility spelling used while the independent journal design
# was being finalized.  Both names describe the same strict wire contract.
NarrativeTensionUpdate = NarrativeTensionLink


@dataclass(frozen=True)
class NarrativeTensionProjection:
    """The current, reproducible state of one consequence-rooted tension."""

    relationship_id: str
    tension_id: str
    consequence_id: str
    source_turn_id: str
    source_revision: str
    source_decision_id: str
    source_event_id: str
    source_message_id: str
    effects: Sequence[RelationshipConsequenceKind]
    outcome: NarrativeTensionOutcome
    summary: str
    link_ids: Sequence[str] = field(default_factory=tuple)

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "relationship_id",
            "tension_id",
            "consequence_id",
            "source_turn_id",
            "source_revision",
            "source_decision_id",
            "source_event_id",
            "source_message_id",
            "effects",
            "outcome",
            "summary",
            "link_ids",
        }
    )

    def __post_init__(self) -> None:
        for field_name in (
            "relationship_id",
            "tension_id",
            "consequence_id",
            "source_turn_id",
            "source_revision",
            "source_decision_id",
            "source_event_id",
            "source_message_id",
            "summary",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        if isinstance(self.effects, (str, bytes)) or not isinstance(
            self.effects, Sequence
        ):
            raise ValueError("effects must be a non-empty sequence")
        effects = tuple(
            _enum_value(item, RelationshipConsequenceKind, "effects")
            for item in self.effects
        )
        if not effects:
            raise ValueError("effects must be a non-empty sequence")
        if len(effects) != len(set(effects)):
            raise ValueError("effects must not contain duplicates")
        effect_order = {
            value: index for index, value in enumerate(RelationshipConsequenceKind)
        }
        object.__setattr__(
            self,
            "effects",
            tuple(sorted(effects, key=effect_order.__getitem__)),
        )
        object.__setattr__(
            self,
            "outcome",
            _enum_value(self.outcome, NarrativeTensionOutcome, "outcome"),
        )
        link_ids = _unique_text_tuple(
            self.link_ids,
            "link_ids",
        )
        object.__setattr__(self, "link_ids", link_ids)
        if not link_ids and self.outcome != NarrativeTensionOutcome.UNADDRESSED:
            raise ValueError("a tension without updates must remain unaddressed")
        if link_ids and self.outcome == NarrativeTensionOutcome.UNADDRESSED:
            raise ValueError("an updated tension cannot remain unaddressed")

    @property
    def consequence_kind(self) -> RelationshipConsequenceKind:
        """Explicit alias naming the kind as consequence metadata."""
        return self.kind

    @property
    def kind(self) -> RelationshipConsequenceKind:
        """Compatibility view returning the first canonical effect."""
        return self.effects[0]

    @property
    def current_outcome(self) -> NarrativeTensionOutcome:
        return self.outcome

    @property
    def current_summary(self) -> str:
        return self.summary

    @property
    def latest_link_id(self) -> str | None:
        return self.link_ids[-1] if self.link_ids else None

    @property
    def update_event_ids(self) -> Tuple[str, ...]:
        """Compatibility view of the append-only link identities."""
        return tuple(self.link_ids)

    @property
    def latest_update_event_id(self) -> str | None:
        return self.latest_link_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "tension_id": self.tension_id,
            "consequence_id": self.consequence_id,
            "source_turn_id": self.source_turn_id,
            "source_revision": self.source_revision,
            "source_decision_id": self.source_decision_id,
            "source_event_id": self.source_event_id,
            "source_message_id": self.source_message_id,
            "effects": [item.value for item in self.effects],
            "outcome": self.outcome.value,
            "summary": self.summary,
            "link_ids": list(self.link_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NarrativeTensionProjection":
        _validate_exact_fields(
            data,
            model_name="NarrativeTensionProjection",
            fields=cls._FIELDS,
        )
        for field_name in cls._FIELDS.difference({"effects", "link_ids"}):
            _require_wire_string(data[field_name], field_name)
        raw_effects = data["effects"]
        if not isinstance(raw_effects, list):
            raise ValueError("effects must be an array")
        for index, value in enumerate(raw_effects):
            _require_wire_string(value, f"effects[{index}]")
        raw_link_ids = data["link_ids"]
        if not isinstance(raw_link_ids, list):
            raise ValueError("link_ids must be an array")
        for index, value in enumerate(raw_link_ids):
            _require_wire_string(value, f"link_ids[{index}]")
        return cls(
            relationship_id=data["relationship_id"],
            tension_id=data["tension_id"],
            consequence_id=data["consequence_id"],
            source_turn_id=data["source_turn_id"],
            source_revision=data["source_revision"],
            source_decision_id=data["source_decision_id"],
            source_event_id=data["source_event_id"],
            source_message_id=data["source_message_id"],
            effects=raw_effects,
            outcome=_enum_value(
                data["outcome"],
                NarrativeTensionOutcome,
                "outcome",
            ),
            summary=data["summary"],
            link_ids=raw_link_ids,
        )


__all__ = [
    "ConsequenceConflictError",
    "NarrativeTensionConflictError",
    "NarrativeTensionOutcome",
    "NarrativeTensionLink",
    "NarrativeTensionProjection",
    "NarrativeTensionUpdate",
    "RelationshipConsequence",
    "RelationshipConsequenceKind",
]
