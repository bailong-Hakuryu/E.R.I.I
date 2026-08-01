"""Strict, portable references to authoritative continuity evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Dict, Mapping

from erii.models._wire_codec import canonical_wire_sha256


CONTINUITY_EVIDENCE_REF_VERSION = "continuity-evidence-ref/v1"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class ContinuityEvidenceKind(str, Enum):
    """Version-one allowlist of direct continuity authorities."""

    CHARACTER_BLUEPRINT = "character_blueprint"
    CHARACTER_SOURCE_SPAN = "character_source_span"
    PERSONA_CLAIM = "persona_claim"
    FORMATIVE_EXPERIENCE = "formative_experience"
    MEANING_CAPSULE = "meaning_capsule"
    CONTEXTUAL_VOICE_PATTERN = "contextual_voice_pattern"
    APPROVED_PERSONA_GROWTH = "approved_persona_growth"
    RELATIONSHIP_PREMISE = "relationship_premise"
    PREMISE_EXPERIENCE = "premise_experience"
    SOURCE_TURN = "source_turn"
    RELATIONSHIP_EVENT = "relationship_event"
    PERSONA_REFLECTION_RECORD = "persona_reflection_record"
    MEMORY_NODE = "memory_node"


PERSONA_EVIDENCE_KINDS = frozenset(
    {
        ContinuityEvidenceKind.CHARACTER_BLUEPRINT,
        ContinuityEvidenceKind.CHARACTER_SOURCE_SPAN,
        ContinuityEvidenceKind.PERSONA_CLAIM,
        ContinuityEvidenceKind.FORMATIVE_EXPERIENCE,
        ContinuityEvidenceKind.MEANING_CAPSULE,
        ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN,
        ContinuityEvidenceKind.APPROVED_PERSONA_GROWTH,
    }
)
RELATIONSHIP_EVIDENCE_KINDS = frozenset(
    set(ContinuityEvidenceKind) - PERSONA_EVIDENCE_KINDS
)


_LOCATOR_FIELDS = {
    ContinuityEvidenceKind.CHARACTER_BLUEPRINT: {
        "blueprint_id": str,
        "revision": int,
        "source_sha256": str,
    },
    ContinuityEvidenceKind.CHARACTER_SOURCE_SPAN: {
        "blueprint_id": str,
        "revision": int,
        "source_sha256": str,
        "start": int,
        "end": int,
        "quote_sha256": str,
    },
    ContinuityEvidenceKind.PERSONA_CLAIM: {
        "manifest_id": str,
        "content_fingerprint": str,
        "claim_id": str,
    },
    ContinuityEvidenceKind.FORMATIVE_EXPERIENCE: {
        "manifest_id": str,
        "content_fingerprint": str,
        "experience_id": str,
    },
    ContinuityEvidenceKind.MEANING_CAPSULE: {
        "manifest_id": str,
        "content_fingerprint": str,
        "capsule_id": str,
    },
    ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN: {
        "manifest_id": str,
        "content_fingerprint": str,
        "pattern_id": str,
    },
    ContinuityEvidenceKind.APPROVED_PERSONA_GROWTH: {
        "relationship_id": str,
        "proposal_id": str,
        "revision": int,
        "content_fingerprint": str,
    },
    ContinuityEvidenceKind.RELATIONSHIP_PREMISE: {
        "relationship_id": str,
        "premise_id": str,
        "content_fingerprint": str,
    },
    ContinuityEvidenceKind.PREMISE_EXPERIENCE: {
        "relationship_id": str,
        "premise_id": str,
        "content_fingerprint": str,
        "experience_id": str,
    },
    ContinuityEvidenceKind.SOURCE_TURN: {
        "relationship_id": str,
        "turn_id": str,
        "source_revision": str,
    },
    ContinuityEvidenceKind.RELATIONSHIP_EVENT: {
        "relationship_id": str,
        "event_id": str,
    },
    ContinuityEvidenceKind.PERSONA_REFLECTION_RECORD: {
        "relationship_id": str,
        "reflection_id": str,
        "content_fingerprint": str,
    },
    ContinuityEvidenceKind.MEMORY_NODE: {
        "relationship_id": str,
        "node_id": str,
        "artifact_fingerprint": str,
    },
}


def _normalize_locator(
    kind: ContinuityEvidenceKind,
    locator: Mapping[str, object],
) -> Mapping[str, object]:
    if not isinstance(locator, Mapping):
        raise ValueError("continuity evidence locator must be an object")
    expected = _LOCATOR_FIELDS[kind]
    if set(locator) != set(expected):
        raise ValueError(
            "continuity evidence locator contains unknown or missing fields"
        )
    normalized: Dict[str, object] = {}
    for field_name, field_type in expected.items():
        value = locator[field_name]
        if field_type is int:
            minimum = 0 if field_name == "start" else 1
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < minimum
            ):
                raise ValueError(
                    f"continuity evidence locator {field_name} has an invalid integer"
                )
            normalized[field_name] = value
            continue
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(
                f"continuity evidence locator {field_name} must be a canonical string"
            )
        if len(value) > 256:
            raise ValueError(
                f"continuity evidence locator {field_name} is too long"
            )
        if (
            field_name.endswith("sha256") or field_name.endswith("fingerprint")
        ) and _HEX_64.fullmatch(value) is None:
            raise ValueError(
                f"{field_name} must be a lowercase SHA-256 digest"
            )
        normalized[field_name] = value
    if kind == ContinuityEvidenceKind.CHARACTER_SOURCE_SPAN:
        if normalized["end"] <= normalized["start"]:
            raise ValueError("character source span requires start < end")
    return MappingProxyType(normalized)


def continuity_evidence_ref_id(
    kind: ContinuityEvidenceKind,
    locator: Mapping[str, object],
) -> str:
    """Returns the canonical, domain-separated identity for one locator."""
    normalized_kind = (
        kind if isinstance(kind, ContinuityEvidenceKind) else ContinuityEvidenceKind(kind)
    )
    normalized_locator = _normalize_locator(normalized_kind, locator)
    return canonical_wire_sha256(
        wire_type="ContinuityEvidenceRef",
        wire_version=CONTINUITY_EVIDENCE_REF_VERSION,
        identity_payload={
            "kind": normalized_kind.value,
            "locator": dict(normalized_locator),
        },
    )


@dataclass(frozen=True)
class ContinuityEvidenceRef:
    """A typed locator whose identity is recomputed on every boundary read."""

    kind: ContinuityEvidenceKind
    locator: Mapping[str, object]
    ref_id: str
    ref_version: str = CONTINUITY_EVIDENCE_REF_VERSION

    _FIELDS = frozenset({"ref_version", "kind", "locator", "ref_id"})

    def __post_init__(self) -> None:
        if self.ref_version != CONTINUITY_EVIDENCE_REF_VERSION:
            raise ValueError("unsupported ContinuityEvidenceRef version")
        kind = self.kind
        if not isinstance(kind, ContinuityEvidenceKind):
            kind = ContinuityEvidenceKind(kind)
            object.__setattr__(self, "kind", kind)
        locator = _normalize_locator(kind, self.locator)
        object.__setattr__(self, "locator", locator)
        if not isinstance(self.ref_id, str) or _HEX_64.fullmatch(self.ref_id) is None:
            raise ValueError("ref_id must be a lowercase SHA-256 digest")
        expected = continuity_evidence_ref_id(kind, locator)
        if self.ref_id != expected:
            raise ValueError("ref_id does not match the continuity evidence locator")

    @classmethod
    def create(
        cls,
        kind: ContinuityEvidenceKind,
        locator: Mapping[str, object],
    ) -> "ContinuityEvidenceRef":
        """Builds a correctly fingerprinted reference for a known locator."""
        normalized_kind = (
            kind
            if isinstance(kind, ContinuityEvidenceKind)
            else ContinuityEvidenceKind(kind)
        )
        normalized_locator = _normalize_locator(normalized_kind, locator)
        return cls(
            kind=normalized_kind,
            locator=normalized_locator,
            ref_id=continuity_evidence_ref_id(normalized_kind, normalized_locator),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref_version": self.ref_version,
            "kind": self.kind.value,
            "locator": dict(self.locator),
            "ref_id": self.ref_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContinuityEvidenceRef":
        if not isinstance(data, Mapping) or set(data) != cls._FIELDS:
            raise ValueError(
                "ContinuityEvidenceRef contains unknown or missing fields"
            )
        if not isinstance(data["ref_version"], str):
            raise ValueError("ref_version must be a string")
        if not isinstance(data["kind"], str):
            raise ValueError("continuity evidence kind must be a string")
        if not isinstance(data["locator"], Mapping):
            raise ValueError("continuity evidence locator must be an object")
        if not isinstance(data["ref_id"], str):
            raise ValueError("ref_id must be a string")
        return cls(
            ref_version=data["ref_version"],
            kind=ContinuityEvidenceKind(data["kind"]),
            locator=data["locator"],
            ref_id=data["ref_id"],
        )


__all__ = [
    "CONTINUITY_EVIDENCE_REF_VERSION",
    "PERSONA_EVIDENCE_KINDS",
    "RELATIONSHIP_EVIDENCE_KINDS",
    "ContinuityEvidenceKind",
    "ContinuityEvidenceRef",
    "continuity_evidence_ref_id",
]
