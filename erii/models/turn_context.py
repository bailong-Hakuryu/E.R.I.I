"""Strict portable context frozen when an a8 Turn opens."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
import re

from erii.models._wire_codec import canonical_wire_sha256


TURN_CONTEXT_BASELINE_VERSION = "turn-context-baseline/v1"
RELATIONSHIP_PREMISE_REFERENCE_VERSION = "relationship-premise-reference/v1"
PERSONA_GROWTH_REFERENCE_VERSION = "turn-persona-growth-reference/v1"
RELATIONSHIP_HISTORY_PREFIX_VERSION = "relationship-history-prefix/v1"

_POLICY_VERSION_KEYS = frozenset(
    {
        "relationship_baseline_policy",
        "relationship_history_projection",
        "relationship_safety_policy",
        "interaction_context_policy",
        "voice_matcher_policy",
    }
)


def _require_exact_fields(
    data: Mapping[str, Any],
    expected: frozenset[str],
    object_name: str,
) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{object_name} must be an object")
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"{object_name} fields are invalid; missing={missing}, unknown={unknown}"
        )


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty canonical string")
    return value


def _require_fingerprint(value: object, field_name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 fingerprint")
    return value


def _require_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _require_non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class TurnBlueprintReference:
    """Exact immutable Character Blueprint authority visible at opening."""

    blueprint_id: str
    revision: int
    source_sha256: str

    _FIELDS = frozenset({"blueprint_id", "revision", "source_sha256"})

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "blueprint_id",
            _require_text(self.blueprint_id, "blueprint_id"),
        )
        object.__setattr__(
            self,
            "revision",
            _require_positive_int(self.revision, "blueprint revision"),
        )
        object.__setattr__(
            self,
            "source_sha256",
            _require_fingerprint(self.source_sha256, "source_sha256"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "revision": self.revision,
            "source_sha256": self.source_sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TurnBlueprintReference":
        _require_exact_fields(data, cls._FIELDS, "Turn Blueprint reference")
        return cls(
            blueprint_id=data["blueprint_id"],
            revision=data["revision"],
            source_sha256=data["source_sha256"],
        )


@dataclass(frozen=True)
class TurnManifestReference:
    """Exact approved Persona Manifest authority visible at opening."""

    manifest_id: str
    content_fingerprint: str

    _FIELDS = frozenset({"manifest_id", "content_fingerprint"})

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "manifest_id",
            _require_text(self.manifest_id, "manifest_id"),
        )
        object.__setattr__(
            self,
            "content_fingerprint",
            _require_fingerprint(
                self.content_fingerprint,
                "Manifest content_fingerprint",
            ),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "manifest_id": self.manifest_id,
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TurnManifestReference":
        _require_exact_fields(data, cls._FIELDS, "Turn Manifest reference")
        return cls(
            manifest_id=data["manifest_id"],
            content_fingerprint=data["content_fingerprint"],
        )


@dataclass(frozen=True)
class TurnApprovedGrowthReference:
    """One approved Persona Growth revision in opening-time authority order."""

    proposal_id: str
    revision: int
    content_fingerprint: str

    _FIELDS = frozenset({"proposal_id", "revision", "content_fingerprint"})

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proposal_id",
            _require_text(self.proposal_id, "proposal_id"),
        )
        object.__setattr__(
            self,
            "revision",
            _require_positive_int(self.revision, "growth revision"),
        )
        object.__setattr__(
            self,
            "content_fingerprint",
            _require_fingerprint(
                self.content_fingerprint,
                "growth content_fingerprint",
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "revision": self.revision,
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TurnApprovedGrowthReference":
        _require_exact_fields(data, cls._FIELDS, "Turn approved-growth reference")
        return cls(
            proposal_id=data["proposal_id"],
            revision=data["revision"],
            content_fingerprint=data["content_fingerprint"],
        )


@dataclass(frozen=True)
class TurnPremiseReference:
    """Exact relationship-local narrative premise visible at opening."""

    premise_id: str
    content_fingerprint: str

    _FIELDS = frozenset({"premise_id", "content_fingerprint"})

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "premise_id",
            _require_text(self.premise_id, "premise_id"),
        )
        object.__setattr__(
            self,
            "content_fingerprint",
            _require_fingerprint(
                self.content_fingerprint,
                "Premise content_fingerprint",
            ),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "premise_id": self.premise_id,
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TurnPremiseReference":
        _require_exact_fields(data, cls._FIELDS, "Turn Premise reference")
        return cls(
            premise_id=data["premise_id"],
            content_fingerprint=data["content_fingerprint"],
        )


@dataclass(frozen=True)
class TurnContextBaseline:
    """Small verifiable boundary over all authority visible at Turn Opening."""

    baseline_version: str
    relationship_id: str
    turn_id: str
    persona_id: str
    blueprint: TurnBlueprintReference
    manifest: Optional[TurnManifestReference]
    approved_growth_refs: Tuple[TurnApprovedGrowthReference, ...]
    premise: TurnPremiseReference
    direct_event_count: int
    adjudication_count: int
    history_prefix_fingerprint: str
    policy_versions: Mapping[str, str]
    baseline_fingerprint: str

    _FIELDS = frozenset(
        {
            "baseline_version",
            "relationship_id",
            "turn_id",
            "persona_id",
            "blueprint",
            "manifest",
            "approved_growth_refs",
            "premise",
            "direct_event_count",
            "adjudication_count",
            "history_prefix_fingerprint",
            "policy_versions",
            "baseline_fingerprint",
        }
    )

    def __post_init__(self) -> None:
        if self.baseline_version != TURN_CONTEXT_BASELINE_VERSION:
            raise ValueError("unsupported TurnContextBaseline version")
        for field_name in ("relationship_id", "turn_id", "persona_id"):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.blueprint, TurnBlueprintReference):
            object.__setattr__(
                self,
                "blueprint",
                TurnBlueprintReference.from_dict(self.blueprint),
            )
        if self.manifest is not None and not isinstance(
            self.manifest,
            TurnManifestReference,
        ):
            object.__setattr__(
                self,
                "manifest",
                TurnManifestReference.from_dict(self.manifest),
            )
        growth_refs = tuple(
            item
            if isinstance(item, TurnApprovedGrowthReference)
            else TurnApprovedGrowthReference.from_dict(item)
            for item in self.approved_growth_refs
        )
        growth_ids = tuple((item.proposal_id, item.revision) for item in growth_refs)
        if len(growth_ids) != len(set(growth_ids)):
            raise ValueError("approved_growth_refs must not repeat a proposal revision")
        object.__setattr__(self, "approved_growth_refs", growth_refs)
        if not isinstance(self.premise, TurnPremiseReference):
            object.__setattr__(
                self,
                "premise",
                TurnPremiseReference.from_dict(self.premise),
            )
        object.__setattr__(
            self,
            "direct_event_count",
            _require_non_negative_int(
                self.direct_event_count,
                "direct_event_count",
            ),
        )
        object.__setattr__(
            self,
            "adjudication_count",
            _require_non_negative_int(
                self.adjudication_count,
                "adjudication_count",
            ),
        )
        object.__setattr__(
            self,
            "history_prefix_fingerprint",
            _require_fingerprint(
                self.history_prefix_fingerprint,
                "history_prefix_fingerprint",
            ),
        )
        if not isinstance(self.policy_versions, Mapping):
            raise ValueError("policy_versions must be an object")
        if set(self.policy_versions) != _POLICY_VERSION_KEYS:
            raise ValueError("policy_versions must contain the exact a8 policy set")
        policy_versions = {
            key: _require_text(value, f"policy_versions.{key}")
            for key, value in self.policy_versions.items()
        }
        object.__setattr__(
            self,
            "policy_versions",
            MappingProxyType(policy_versions),
        )
        supplied_fingerprint = _require_fingerprint(
            self.baseline_fingerprint,
            "baseline_fingerprint",
        )
        if supplied_fingerprint != self.fingerprint_for_payload(self.identity_payload()):
            raise ValueError("baseline_fingerprint does not match the frozen context")
        object.__setattr__(self, "baseline_fingerprint", supplied_fingerprint)

    def identity_payload(self) -> Dict[str, Any]:
        """Returns every identity field except the self-authenticating digest."""
        return {
            "baseline_version": self.baseline_version,
            "relationship_id": self.relationship_id,
            "turn_id": self.turn_id,
            "persona_id": self.persona_id,
            "blueprint": self.blueprint.to_dict(),
            "manifest": self.manifest.to_dict() if self.manifest is not None else None,
            "approved_growth_refs": [
                item.to_dict() for item in self.approved_growth_refs
            ],
            "premise": self.premise.to_dict(),
            "direct_event_count": self.direct_event_count,
            "adjudication_count": self.adjudication_count,
            "history_prefix_fingerprint": self.history_prefix_fingerprint,
            "policy_versions": dict(self.policy_versions),
        }

    @staticmethod
    def fingerprint_for_payload(payload: Mapping[str, Any]) -> str:
        return canonical_wire_sha256(
            wire_type="turn_context_baseline",
            wire_version=TURN_CONTEXT_BASELINE_VERSION,
            identity_payload=payload,
        )

    @classmethod
    def create(
        cls,
        *,
        relationship_id: str,
        turn_id: str,
        persona_id: str,
        blueprint: TurnBlueprintReference,
        manifest: Optional[TurnManifestReference],
        approved_growth_refs: Sequence[TurnApprovedGrowthReference],
        premise: TurnPremiseReference,
        direct_event_count: int,
        adjudication_count: int,
        history_prefix_fingerprint: str,
        policy_versions: Mapping[str, str],
    ) -> "TurnContextBaseline":
        payload = {
            "baseline_version": TURN_CONTEXT_BASELINE_VERSION,
            "relationship_id": relationship_id,
            "turn_id": turn_id,
            "persona_id": persona_id,
            "blueprint": blueprint.to_dict(),
            "manifest": manifest.to_dict() if manifest is not None else None,
            "approved_growth_refs": [item.to_dict() for item in approved_growth_refs],
            "premise": premise.to_dict(),
            "direct_event_count": direct_event_count,
            "adjudication_count": adjudication_count,
            "history_prefix_fingerprint": history_prefix_fingerprint,
            "policy_versions": dict(policy_versions),
        }
        return cls(
            baseline_version=TURN_CONTEXT_BASELINE_VERSION,
            relationship_id=relationship_id,
            turn_id=turn_id,
            persona_id=persona_id,
            blueprint=blueprint,
            manifest=manifest,
            approved_growth_refs=tuple(approved_growth_refs),
            premise=premise,
            direct_event_count=direct_event_count,
            adjudication_count=adjudication_count,
            history_prefix_fingerprint=history_prefix_fingerprint,
            policy_versions=policy_versions,
            baseline_fingerprint=cls.fingerprint_for_payload(payload),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {**self.identity_payload(), "baseline_fingerprint": self.baseline_fingerprint}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TurnContextBaseline":
        _require_exact_fields(data, cls._FIELDS, "TurnContextBaseline")
        if data["baseline_version"] != TURN_CONTEXT_BASELINE_VERSION:
            raise ValueError("unsupported TurnContextBaseline version")
        raw_growth = data["approved_growth_refs"]
        if not isinstance(raw_growth, list):
            raise ValueError("approved_growth_refs must be an array")
        raw_policies = data["policy_versions"]
        if not isinstance(raw_policies, Mapping):
            raise ValueError("policy_versions must be an object")
        return cls(
            baseline_version=data["baseline_version"],
            relationship_id=data["relationship_id"],
            turn_id=data["turn_id"],
            persona_id=data["persona_id"],
            blueprint=TurnBlueprintReference.from_dict(data["blueprint"]),
            manifest=(
                TurnManifestReference.from_dict(data["manifest"])
                if data["manifest"] is not None
                else None
            ),
            approved_growth_refs=tuple(
                TurnApprovedGrowthReference.from_dict(item) for item in raw_growth
            ),
            premise=TurnPremiseReference.from_dict(data["premise"]),
            direct_event_count=data["direct_event_count"],
            adjudication_count=data["adjudication_count"],
            history_prefix_fingerprint=data["history_prefix_fingerprint"],
            policy_versions=raw_policies,
            baseline_fingerprint=data["baseline_fingerprint"],
        )


def premise_content_fingerprint(premise: Mapping[str, Any]) -> str:
    return canonical_wire_sha256(
        wire_type="relationship_premise_reference",
        wire_version=RELATIONSHIP_PREMISE_REFERENCE_VERSION,
        identity_payload=premise,
    )


def persona_growth_content_fingerprint(proposal: Mapping[str, Any]) -> str:
    immutable_fields = {
        key: proposal[key]
        for key in (
            "proposal_id",
            "relationship_id",
            "revision",
            "intent_key",
            "review_id",
            "statement",
            "rationale",
            "proposed_changes",
            "supporting_event_ids",
            "trigger_kind",
            "created_at",
        )
    }
    return canonical_wire_sha256(
        wire_type="turn_persona_growth_reference",
        wire_version=PERSONA_GROWTH_REFERENCE_VERSION,
        identity_payload=immutable_fields,
    )


def history_prefix_fingerprint(
    direct_events: Sequence[Mapping[str, Any]],
    adjudications: Sequence[Mapping[str, Any]],
) -> str:
    return canonical_wire_sha256(
        wire_type="relationship_history_prefix",
        wire_version=RELATIONSHIP_HISTORY_PREFIX_VERSION,
        identity_payload={
            "direct_events": list(direct_events),
            "adjudications": list(adjudications),
        },
    )


__all__ = [
    "TURN_CONTEXT_BASELINE_VERSION",
    "TurnApprovedGrowthReference",
    "TurnBlueprintReference",
    "TurnContextBaseline",
    "TurnManifestReference",
    "TurnPremiseReference",
    "history_prefix_fingerprint",
    "persona_growth_content_fingerprint",
    "premise_content_fingerprint",
]
