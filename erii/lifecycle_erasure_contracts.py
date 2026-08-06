"""Storage-independent contracts for deterministic lifecycle erasure."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple


class ErasureStorageKind(str, Enum):
    """Physical staging formats supported by the erasure transform."""

    FILE_STORAGE = "file_storage"
    SQLITE = "sqlite"


class ErasureScope(str, Enum):
    """Closed set of data-owner erasure scopes."""

    RELATIONSHIP = "relationship"
    SOURCE_TURN = "source_turn"
    RELATIONSHIP_EVENT = "relationship_event"
    COMPLETE_USER = "complete_user"


def _required_text(value: Optional[str], field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class ErasureSelector:
    """Strict, serializable identity selector for one erasure scope."""

    scope: ErasureScope
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    relationship_id: Optional[str] = None
    source_turn_id: Optional[str] = None
    relationship_event_id: Optional[str] = None
    user_identity_id: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.scope, str):
            object.__setattr__(self, "scope", ErasureScope(self.scope))
        relation_fields = {
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "relationship_id": self.relationship_id,
        }
        if self.scope is ErasureScope.COMPLETE_USER:
            _required_text(self.user_id, "user_id")
            _required_text(self.user_identity_id, "user_identity_id")
            forbidden = {
                "agent_id": self.agent_id,
                "relationship_id": self.relationship_id,
                "source_turn_id": self.source_turn_id,
                "relationship_event_id": self.relationship_event_id,
            }
        else:
            for name, value in relation_fields.items():
                _required_text(value, name)
            forbidden = {"user_identity_id": self.user_identity_id}
            if self.scope is ErasureScope.RELATIONSHIP:
                forbidden.update(
                    {
                        "source_turn_id": self.source_turn_id,
                        "relationship_event_id": self.relationship_event_id,
                    }
                )
            elif self.scope is ErasureScope.SOURCE_TURN:
                _required_text(self.source_turn_id, "source_turn_id")
                forbidden["relationship_event_id"] = self.relationship_event_id
            elif self.scope is ErasureScope.RELATIONSHIP_EVENT:
                _required_text(
                    self.relationship_event_id,
                    "relationship_event_id",
                )
                forbidden["source_turn_id"] = self.source_turn_id
            else:  # pragma: no cover - Enum construction closes this branch.
                raise ValueError("unsupported erasure scope")
        present = [name for name, value in forbidden.items() if value is not None]
        if present:
            raise ValueError(
                f"{self.scope.value} selector forbids fields: {', '.join(sorted(present))}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope.value,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "relationship_id": self.relationship_id,
            "source_turn_id": self.source_turn_id,
            "relationship_event_id": self.relationship_event_id,
            "user_identity_id": self.user_identity_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ErasureSelector":
        fields = {
            "scope",
            "agent_id",
            "user_id",
            "relationship_id",
            "source_turn_id",
            "relationship_event_id",
            "user_identity_id",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("erasure selector fields are invalid")
        return cls(
            scope=ErasureScope(value["scope"]),
            agent_id=value["agent_id"],
            user_id=value["user_id"],
            relationship_id=value["relationship_id"],
            source_turn_id=value["source_turn_id"],
            relationship_event_id=value["relationship_event_id"],
            user_identity_id=value["user_identity_id"],
        )


_INVENTORY_DISPOSITIONS = (
    "deleted",
    "rebuilt",
    "delegated",
    "unverified_external",
)


@dataclass(frozen=True, slots=True)
class ErasureInventory:
    """Content-free aggregate of erasure work and unresolved external work."""

    counts: Mapping[str, Mapping[str, int]]

    def __post_init__(self) -> None:
        if set(self.counts) != set(_INVENTORY_DISPOSITIONS):
            raise ValueError("erasure inventory dispositions are incomplete")
        normalized: Dict[str, Mapping[str, int]] = {}
        for disposition in _INVENTORY_DISPOSITIONS:
            raw = self.counts[disposition]
            if not isinstance(raw, Mapping):
                raise TypeError("erasure inventory groups must be mappings")
            group: Dict[str, int] = {}
            for kind, count in raw.items():
                name = _required_text(kind, "artifact kind")
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ValueError("artifact counts must be non-negative integers")
                if count:
                    group[name] = count
            normalized[disposition] = MappingProxyType(group)
        object.__setattr__(self, "counts", MappingProxyType(normalized))

    def to_dict(self) -> Dict[str, Dict[str, int]]:
        return {
            disposition: dict(self.counts[disposition])
            for disposition in _INVENTORY_DISPOSITIONS
        }


@dataclass(frozen=True, slots=True)
class RelationshipRebuildProof:
    """Non-content digest proving projections were rebuilt after erasure."""

    relationship_id: str
    event_count: int
    state_digest: str
    belief_digest: str
    consolidation_digest: str
    episode_count: int
    chapter_count: int
    consequence_count: int
    tension_link_count: int
    tension_count: int
    tension_digest: str

    def __post_init__(self) -> None:
        _required_text(self.relationship_id, "relationship_id")
        for label, value in (
            ("event_count", self.event_count),
            ("episode_count", self.episode_count),
            ("chapter_count", self.chapter_count),
            ("consequence_count", self.consequence_count),
            ("tension_link_count", self.tension_link_count),
            ("tension_count", self.tension_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        for label, value in (
            ("state_digest", self.state_digest),
            ("belief_digest", self.belief_digest),
            ("consolidation_digest", self.consolidation_digest),
            ("tension_digest", self.tension_digest),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "event_count": self.event_count,
            "state_digest": self.state_digest,
            "belief_digest": self.belief_digest,
            "consolidation_digest": self.consolidation_digest,
            "episode_count": self.episode_count,
            "chapter_count": self.chapter_count,
            "consequence_count": self.consequence_count,
            "tension_link_count": self.tension_link_count,
            "tension_count": self.tension_count,
            "tension_digest": self.tension_digest,
        }


@dataclass(frozen=True, slots=True)
class ErasureTransformResult:
    """Verified, content-free outcome of one staging-only transform."""

    storage_kind: ErasureStorageKind
    selector: ErasureSelector
    affected_relationship_ids: Tuple[str, ...]
    rebuild_proofs: Tuple[RelationshipRebuildProof, ...]
    inventory: ErasureInventory

    def __post_init__(self) -> None:
        if not isinstance(self.storage_kind, ErasureStorageKind):
            raise TypeError("storage_kind must be an ErasureStorageKind")
        if not isinstance(self.selector, ErasureSelector):
            raise TypeError("selector must be an ErasureSelector")
        if tuple(sorted(set(self.affected_relationship_ids))) != tuple(
            self.affected_relationship_ids
        ):
            raise ValueError("affected_relationship_ids must be sorted and unique")
        if any(
            not isinstance(item, str) or not item
            for item in self.affected_relationship_ids
        ):
            raise ValueError("affected relationship IDs must be non-empty strings")
        if any(
            not isinstance(item, RelationshipRebuildProof)
            for item in self.rebuild_proofs
        ):
            raise TypeError("rebuild_proofs must contain RelationshipRebuildProof")
        if not isinstance(self.inventory, ErasureInventory):
            raise TypeError("inventory must be an ErasureInventory")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "storage_kind": self.storage_kind.value,
            "selector": self.selector.to_dict(),
            "affected_relationship_ids": list(self.affected_relationship_ids),
            "rebuild_proofs": [item.to_dict() for item in self.rebuild_proofs],
            "inventory": self.inventory.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ErasureScopeInspection:
    """Read-only, content-free preview of one exact selector match."""

    storage_kind: ErasureStorageKind
    selector: ErasureSelector
    affected_relationship_ids: Tuple[str, ...]
    inventory_estimate: ErasureInventory

    def __post_init__(self) -> None:
        if not isinstance(self.storage_kind, ErasureStorageKind):
            raise TypeError("storage_kind must be an ErasureStorageKind")
        if not isinstance(self.selector, ErasureSelector):
            raise TypeError("selector must be an ErasureSelector")
        if tuple(sorted(set(self.affected_relationship_ids))) != tuple(
            self.affected_relationship_ids
        ):
            raise ValueError("affected_relationship_ids must be sorted and unique")
        if not isinstance(self.inventory_estimate, ErasureInventory):
            raise TypeError("inventory_estimate must be an ErasureInventory")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "storage_kind": self.storage_kind.value,
            "selector": self.selector.to_dict(),
            "affected_relationship_ids": list(self.affected_relationship_ids),
            "inventory_estimate": self.inventory_estimate.to_dict(),
        }


class ErasureSelectionError(ValueError):
    """Selector is missing, ambiguous, or crosses an identity boundary."""


__all__ = [
    "ErasureInventory",
    "ErasureScope",
    "ErasureScopeInspection",
    "ErasureSelectionError",
    "ErasureSelector",
    "ErasureStorageKind",
    "ErasureTransformResult",
    "RelationshipRebuildProof",
]
