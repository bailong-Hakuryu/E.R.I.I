"""Immutable temporal commitments and open-loop payloads.

These records are durable relationship-event payloads, not mutable status
objects.  Promise and Open Loop state is projected from their original event
and later append-only confirmation or resolution events.
"""

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Dict, Mapping, Optional, Sequence, Union


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Optional[str], field_name: str) -> Optional[str]:
    return None if value is None else _require_text(value, field_name)


def _validate_mapping_keys(
    data: Mapping[str, Any],
    *,
    payload_name: str,
    required: Sequence[str],
    optional: Sequence[str] = (),
    expected_payload_type: Optional[str] = None,
) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{payload_name} payload must be a mapping")
    allowed = set(required).union(optional)
    if expected_payload_type is not None:
        allowed.add("payload_type")
    unknown = set(data).difference(allowed)
    if unknown:
        raise ValueError(f"{payload_name} payload contains unknown keys: {sorted(unknown)}")
    missing = set(required).difference(data)
    if missing:
        raise ValueError(f"{payload_name} payload is missing keys: {sorted(missing)}")
    if (
        expected_payload_type is not None
        and data.get("payload_type", expected_payload_type) != expected_payload_type
    ):
        raise ValueError(
            f"{payload_name} payload_type must be {expected_payload_type!r}"
        )


class PromiseResponsibleParty(str, Enum):
    """Relationship-local parties that can explicitly accept responsibility."""

    AGENT = "agent"
    USER = "user"


class TemporalPayloadType(str, Enum):
    """Stable discriminator values for durable temporal event payloads."""

    PROMISE = "promise"
    PROMISE_CONDITION_CONFIRMED = "promise_condition_confirmed"
    PROMISE_RESOLUTION = "promise_resolution"
    OPEN_LOOP = "open_loop"
    OPEN_LOOP_RESOLUTION = "open_loop_resolution"


@dataclass(frozen=True)
class WorldMoment:
    """One host-owned moment in a named real or fictional clock."""

    clock_id: str
    display_value: str
    order_value: Optional[float] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "clock_id", _require_text(self.clock_id, "clock_id"))
        object.__setattr__(
            self,
            "display_value",
            _require_text(self.display_value, "display_value"),
        )
        if self.order_value is not None:
            if isinstance(self.order_value, bool) or not isinstance(
                self.order_value, (int, float)
            ):
                raise ValueError("order_value must be numeric when supplied")
            numeric = float(self.order_value)
            if not math.isfinite(numeric):
                raise ValueError("order_value must be finite")
            object.__setattr__(self, "order_value", numeric)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clock_id": self.clock_id,
            "display_value": self.display_value,
            "order_value": self.order_value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorldMoment":
        _validate_mapping_keys(
            data,
            payload_name="WorldMoment",
            required=("clock_id", "display_value"),
            optional=("order_value",),
        )
        return cls(
            clock_id=data["clock_id"],
            display_value=data["display_value"],
            order_value=data.get("order_value"),
        )


@dataclass(frozen=True)
class PromiseCondition:
    """One explicit condition whose later confirmation activates a Promise."""

    condition_id: str
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "condition_id",
            _require_text(self.condition_id, "condition_id"),
        )
        object.__setattr__(
            self,
            "description",
            _require_text(self.description, "condition description"),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "condition_id": self.condition_id,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PromiseCondition":
        _validate_mapping_keys(
            data,
            payload_name="PromiseCondition",
            required=("condition_id", "description"),
        )
        return cls(
            condition_id=data["condition_id"],
            description=data["description"],
        )


@dataclass(frozen=True)
class PromiseSpec:
    """An immutable evidence-backed commitment by one or both parties."""

    responsible_parties: Sequence[PromiseResponsibleParty]
    action: str
    due_at: Optional[WorldMoment] = None
    activation_condition: Optional[PromiseCondition] = None

    payload_type = TemporalPayloadType.PROMISE

    def __post_init__(self) -> None:
        if isinstance(self.responsible_parties, (str, bytes)):
            raise ValueError("responsible_parties must be a non-empty sequence")
        parties = tuple(PromiseResponsibleParty(item) for item in self.responsible_parties)
        if not parties:
            raise ValueError("responsible_parties must not be empty")
        if len(parties) != len(set(parties)):
            raise ValueError("responsible_parties must not contain duplicates")
        party_order = {
            PromiseResponsibleParty.AGENT: 0,
            PromiseResponsibleParty.USER: 1,
        }
        parties = tuple(sorted(parties, key=party_order.__getitem__))
        object.__setattr__(self, "responsible_parties", parties)
        object.__setattr__(self, "action", _require_text(self.action, "promise action"))
        if self.due_at is not None and not isinstance(self.due_at, WorldMoment):
            object.__setattr__(self, "due_at", WorldMoment.from_dict(self.due_at))
        if self.activation_condition is not None and not isinstance(
            self.activation_condition, PromiseCondition
        ):
            object.__setattr__(
                self,
                "activation_condition",
                PromiseCondition.from_dict(self.activation_condition),
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payload_type": self.payload_type.value,
            "responsible_parties": [item.value for item in self.responsible_parties],
            "action": self.action,
            "due_at": self.due_at.to_dict() if self.due_at else None,
            "activation_condition": (
                self.activation_condition.to_dict() if self.activation_condition else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PromiseSpec":
        _validate_mapping_keys(
            data,
            payload_name="PromiseSpec",
            required=("responsible_parties", "action"),
            optional=("due_at", "activation_condition"),
            expected_payload_type=cls.payload_type.value,
        )
        return cls(
            responsible_parties=data["responsible_parties"],
            action=data["action"],
            due_at=(
                WorldMoment.from_dict(data["due_at"])
                if data.get("due_at") is not None
                else None
            ),
            activation_condition=(
                PromiseCondition.from_dict(data["activation_condition"])
                if data.get("activation_condition") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class PromiseConditionConfirmation:
    """Append-only evidence that one Promise activation condition occurred."""

    promise_event_id: str
    condition_id: str
    confirmed_at: Optional[WorldMoment] = None

    payload_type = TemporalPayloadType.PROMISE_CONDITION_CONFIRMED

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "promise_event_id",
            _require_text(self.promise_event_id, "promise_event_id"),
        )
        object.__setattr__(
            self,
            "condition_id",
            _require_text(self.condition_id, "condition_id"),
        )
        if self.confirmed_at is not None and not isinstance(self.confirmed_at, WorldMoment):
            object.__setattr__(
                self,
                "confirmed_at",
                WorldMoment.from_dict(self.confirmed_at),
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payload_type": self.payload_type.value,
            "promise_event_id": self.promise_event_id,
            "condition_id": self.condition_id,
            "confirmed_at": self.confirmed_at.to_dict() if self.confirmed_at else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PromiseConditionConfirmation":
        _validate_mapping_keys(
            data,
            payload_name="PromiseConditionConfirmation",
            required=("promise_event_id", "condition_id"),
            optional=("confirmed_at",),
            expected_payload_type=cls.payload_type.value,
        )
        return cls(
            promise_event_id=data["promise_event_id"],
            condition_id=data["condition_id"],
            confirmed_at=(
                WorldMoment.from_dict(data["confirmed_at"])
                if data.get("confirmed_at") is not None
                else None
            ),
        )


class PromiseResolutionKind(str, Enum):
    """Append-only outcomes that resolve a Promise without rewriting it."""

    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    WILL_NOT_FULFILL = "will_not_fulfill"


@dataclass(frozen=True)
class PromiseResolution:
    """A later event resolving one immutable Promise event."""

    promise_event_id: str
    resolution_kind: PromiseResolutionKind
    resolved_at: Optional[WorldMoment] = None
    superseding_promise_event_id: Optional[str] = None
    note: Optional[str] = None

    payload_type = TemporalPayloadType.PROMISE_RESOLUTION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "promise_event_id",
            _require_text(self.promise_event_id, "promise_event_id"),
        )
        if not isinstance(self.resolution_kind, PromiseResolutionKind):
            object.__setattr__(
                self,
                "resolution_kind",
                PromiseResolutionKind(self.resolution_kind),
            )
        if self.resolved_at is not None and not isinstance(self.resolved_at, WorldMoment):
            object.__setattr__(
                self,
                "resolved_at",
                WorldMoment.from_dict(self.resolved_at),
            )
        superseding_id = _optional_text(
            self.superseding_promise_event_id,
            "superseding_promise_event_id",
        )
        object.__setattr__(self, "superseding_promise_event_id", superseding_id)
        object.__setattr__(self, "note", _optional_text(self.note, "resolution note"))
        if self.resolution_kind == PromiseResolutionKind.SUPERSEDED:
            if superseding_id is None:
                raise ValueError(
                    "superseded Promise resolution requires superseding_promise_event_id"
                )
            if superseding_id == self.promise_event_id:
                raise ValueError("a Promise cannot supersede itself")
        elif superseding_id is not None:
            raise ValueError(
                "superseding_promise_event_id is only valid for a superseded resolution"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payload_type": self.payload_type.value,
            "promise_event_id": self.promise_event_id,
            "resolution_kind": self.resolution_kind.value,
            "resolved_at": self.resolved_at.to_dict() if self.resolved_at else None,
            "superseding_promise_event_id": self.superseding_promise_event_id,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PromiseResolution":
        _validate_mapping_keys(
            data,
            payload_name="PromiseResolution",
            required=("promise_event_id", "resolution_kind"),
            optional=("resolved_at", "superseding_promise_event_id", "note"),
            expected_payload_type=cls.payload_type.value,
        )
        return cls(
            promise_event_id=data["promise_event_id"],
            resolution_kind=PromiseResolutionKind(data["resolution_kind"]),
            resolved_at=(
                WorldMoment.from_dict(data["resolved_at"])
                if data.get("resolved_at") is not None
                else None
            ),
            superseding_promise_event_id=data.get("superseding_promise_event_id"),
            note=data.get("note"),
        )


@dataclass(frozen=True)
class OpenLoopSpec:
    """An unfinished relationship matter without accepted responsibility."""

    subject: str
    expected_continuation: Optional[str] = None
    origin_memory_node_id: Optional[str] = None

    payload_type = TemporalPayloadType.OPEN_LOOP

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject",
            _require_text(self.subject, "open-loop subject"),
        )
        object.__setattr__(
            self,
            "expected_continuation",
            _optional_text(self.expected_continuation, "expected_continuation"),
        )
        object.__setattr__(
            self,
            "origin_memory_node_id",
            _optional_text(self.origin_memory_node_id, "origin_memory_node_id"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payload_type": self.payload_type.value,
            "subject": self.subject,
            "expected_continuation": self.expected_continuation,
            "origin_memory_node_id": self.origin_memory_node_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OpenLoopSpec":
        _validate_mapping_keys(
            data,
            payload_name="OpenLoopSpec",
            required=("subject",),
            optional=("expected_continuation", "origin_memory_node_id"),
            expected_payload_type=cls.payload_type.value,
        )
        return cls(
            subject=data["subject"],
            expected_continuation=data.get("expected_continuation"),
            origin_memory_node_id=data.get("origin_memory_node_id"),
        )


class OpenLoopResolutionKind(str, Enum):
    """Append-only outcomes that close an Open Loop."""

    COMPLETED = "completed"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class OpenLoopResolution:
    """A later event resolving one immutable Open Loop event."""

    open_loop_event_id: str
    resolution_kind: OpenLoopResolutionKind
    superseding_open_loop_event_id: Optional[str] = None
    note: Optional[str] = None

    payload_type = TemporalPayloadType.OPEN_LOOP_RESOLUTION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "open_loop_event_id",
            _require_text(self.open_loop_event_id, "open_loop_event_id"),
        )
        if not isinstance(self.resolution_kind, OpenLoopResolutionKind):
            object.__setattr__(
                self,
                "resolution_kind",
                OpenLoopResolutionKind(self.resolution_kind),
            )
        superseding_id = _optional_text(
            self.superseding_open_loop_event_id,
            "superseding_open_loop_event_id",
        )
        object.__setattr__(self, "superseding_open_loop_event_id", superseding_id)
        object.__setattr__(self, "note", _optional_text(self.note, "resolution note"))
        if self.resolution_kind == OpenLoopResolutionKind.SUPERSEDED:
            if superseding_id is None:
                raise ValueError(
                    "superseded Open Loop resolution requires superseding_open_loop_event_id"
                )
            if superseding_id == self.open_loop_event_id:
                raise ValueError("an Open Loop cannot supersede itself")
        elif superseding_id is not None:
            raise ValueError(
                "superseding_open_loop_event_id is only valid for a superseded resolution"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payload_type": self.payload_type.value,
            "open_loop_event_id": self.open_loop_event_id,
            "resolution_kind": self.resolution_kind.value,
            "superseding_open_loop_event_id": self.superseding_open_loop_event_id,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OpenLoopResolution":
        _validate_mapping_keys(
            data,
            payload_name="OpenLoopResolution",
            required=("open_loop_event_id", "resolution_kind"),
            optional=("superseding_open_loop_event_id", "note"),
            expected_payload_type=cls.payload_type.value,
        )
        return cls(
            open_loop_event_id=data["open_loop_event_id"],
            resolution_kind=OpenLoopResolutionKind(data["resolution_kind"]),
            superseding_open_loop_event_id=data.get("superseding_open_loop_event_id"),
            note=data.get("note"),
        )


TemporalPayload = Union[
    PromiseSpec,
    PromiseConditionConfirmation,
    PromiseResolution,
    OpenLoopSpec,
    OpenLoopResolution,
]

_TEMPORAL_PAYLOAD_CLASSES = {
    TemporalPayloadType.PROMISE: PromiseSpec,
    TemporalPayloadType.PROMISE_CONDITION_CONFIRMED: PromiseConditionConfirmation,
    TemporalPayloadType.PROMISE_RESOLUTION: PromiseResolution,
    TemporalPayloadType.OPEN_LOOP: OpenLoopSpec,
    TemporalPayloadType.OPEN_LOOP_RESOLUTION: OpenLoopResolution,
}


def temporal_payload_to_dict(payload: TemporalPayload) -> Dict[str, Any]:
    """Serializes a known temporal payload with its stable discriminator."""
    if not isinstance(payload, tuple(_TEMPORAL_PAYLOAD_CLASSES.values())):
        raise TypeError("payload must be a supported temporal payload")
    return payload.to_dict()


def temporal_payload_from_dict(data: Mapping[str, Any]) -> TemporalPayload:
    """Deserializes a temporal payload through its required discriminator."""
    if not isinstance(data, Mapping):
        raise ValueError("temporal payload must be a mapping")
    raw_type = data.get("payload_type")
    if raw_type is None:
        raise ValueError("temporal payload requires payload_type")
    try:
        payload_type = TemporalPayloadType(raw_type)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported temporal payload_type: {raw_type!r}") from exc
    return _TEMPORAL_PAYLOAD_CLASSES[payload_type].from_dict(data)


__all__ = [
    "OpenLoopResolution",
    "OpenLoopResolutionKind",
    "OpenLoopSpec",
    "PromiseCondition",
    "PromiseConditionConfirmation",
    "PromiseResolution",
    "PromiseResolutionKind",
    "PromiseResponsibleParty",
    "PromiseSpec",
    "TemporalPayload",
    "TemporalPayloadType",
    "WorldMoment",
    "temporal_payload_from_dict",
    "temporal_payload_to_dict",
]
