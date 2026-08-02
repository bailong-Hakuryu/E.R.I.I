"""Strict message-level evidence for modern archival artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Mapping, Sequence, Tuple

from erii.models._wire_codec import canonical_wire_sha256
from erii.models.turn import TurnRole


ARCHIVAL_EVIDENCE_CITATION_VERSION = "archival-evidence-citation/v1"
ARTIFACT_EVIDENCE_REFERENCE_VERSION = "artifact-evidence-reference/v1"
_MESSAGE_SPAN_KIND = "message_span"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_ID = re.compile(r"^ae1_[0-9a-f]{64}$")


def _canonical_text(value: object, field_name: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{field_name} must be a canonical non-empty string")
    return value


def _span_offset(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class ArchivalEvidenceCitation:
    """An untrusted extractor claim about one exact Source Message span."""

    source_id: str
    source_revision: str
    quote: str
    start: int
    end: int
    citation_version: str = ARCHIVAL_EVIDENCE_CITATION_VERSION
    kind: str = _MESSAGE_SPAN_KIND

    _FIELDS = frozenset(
        {
            "citation_version",
            "kind",
            "source_id",
            "source_revision",
            "quote",
            "start",
            "end",
        }
    )

    def __post_init__(self) -> None:
        if self.citation_version != ARCHIVAL_EVIDENCE_CITATION_VERSION:
            raise ValueError("unsupported ArchivalEvidenceCitation version")
        if self.kind != _MESSAGE_SPAN_KIND:
            raise ValueError("unsupported ArchivalEvidenceCitation kind")
        object.__setattr__(
            self,
            "source_id",
            _canonical_text(self.source_id, "source_id"),
        )
        object.__setattr__(
            self,
            "source_revision",
            _canonical_text(self.source_revision, "source_revision", maximum=64),
        )
        if (
            not isinstance(self.quote, str)
            or not self.quote
            or len(self.quote) > 4000
        ):
            raise ValueError("quote must be a non-empty string of at most 4000 characters")
        start = _span_offset(self.start, "start")
        end = _span_offset(self.end, "end")
        if end <= start:
            raise ValueError("archival evidence requires start < end")
        if end - start != len(self.quote):
            raise ValueError("archival evidence span length must match quote")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "citation_version": self.citation_version,
            "kind": self.kind,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "quote": self.quote,
            "start": self.start,
            "end": self.end,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchivalEvidenceCitation":
        if not isinstance(data, Mapping) or set(data) != cls._FIELDS:
            raise ValueError(
                "ArchivalEvidenceCitation contains unknown or missing fields"
            )
        return cls(
            citation_version=data["citation_version"],
            kind=data["kind"],
            source_id=data["source_id"],
            source_revision=data["source_revision"],
            quote=data["quote"],
            start=data["start"],
            end=data["end"],
        )


def archival_evidence_citations_from_value(
    values: object,
) -> Tuple[ArchivalEvidenceCitation, ...]:
    """Parses one bounded extractor-supplied citation array."""
    if not isinstance(values, Sequence) or isinstance(
        values,
        (str, bytes, bytearray),
    ):
        raise ValueError("archival evidence citations must be an array")
    citations = tuple(
        item
        if isinstance(item, ArchivalEvidenceCitation)
        else ArchivalEvidenceCitation.from_dict(item)
        for item in values
    )
    if len(citations) > 16:
        raise ValueError("one artifact permits at most 16 evidence citations")
    if len(citations) != len(set(citations)):
        raise ValueError("archival evidence citations must not repeat")
    return citations


def artifact_evidence_id(
    *,
    relationship_id: str,
    source_turn_id: str,
    source_id: str,
    source_revision: str,
    message_sha256: str,
    start: int,
    end: int,
) -> str:
    """Builds the canonical identity for one verified archival source span."""
    relationship_id = _canonical_text(relationship_id, "relationship_id")
    source_turn_id = _canonical_text(source_turn_id, "source_turn_id")
    source_id = _canonical_text(source_id, "source_id")
    source_revision = _canonical_text(
        source_revision,
        "source_revision",
        maximum=64,
    )
    if not isinstance(message_sha256, str) or _HEX_64.fullmatch(message_sha256) is None:
        raise ValueError("message_sha256 must be a lowercase SHA-256 digest")
    start = _span_offset(start, "start")
    end = _span_offset(end, "end")
    if end <= start:
        raise ValueError("artifact evidence requires start < end")
    digest = canonical_wire_sha256(
        wire_type="ArtifactEvidenceReference",
        wire_version=ARTIFACT_EVIDENCE_REFERENCE_VERSION,
        identity_payload={
            "relationship_id": relationship_id,
            "source_turn_id": source_turn_id,
            "source_id": source_id,
            "source_revision": source_revision,
            "message_sha256": message_sha256,
            "start": start,
            "end": end,
        },
    )
    return f"ae1_{digest}"


@dataclass(frozen=True)
class ArtifactEvidenceReference:
    """A quote-free, kernel-resolved reference stored with an artifact."""

    relationship_id: str
    source_turn_id: str
    source_id: str
    source_revision: str
    role: TurnRole
    message_sha256: str
    start: int
    end: int
    evidence_id: str
    reference_version: str = ARTIFACT_EVIDENCE_REFERENCE_VERSION
    kind: str = _MESSAGE_SPAN_KIND

    _FIELDS = frozenset(
        {
            "reference_version",
            "kind",
            "evidence_id",
            "relationship_id",
            "source_turn_id",
            "source_id",
            "source_revision",
            "role",
            "message_sha256",
            "start",
            "end",
        }
    )

    def __post_init__(self) -> None:
        if self.reference_version != ARTIFACT_EVIDENCE_REFERENCE_VERSION:
            raise ValueError("unsupported ArtifactEvidenceReference version")
        if self.kind != _MESSAGE_SPAN_KIND:
            raise ValueError("unsupported ArtifactEvidenceReference kind")
        for field_name, maximum in (
            ("relationship_id", 256),
            ("source_turn_id", 256),
            ("source_id", 256),
            ("source_revision", 64),
        ):
            object.__setattr__(
                self,
                field_name,
                _canonical_text(
                    getattr(self, field_name),
                    field_name,
                    maximum=maximum,
                ),
            )
        role = self.role
        if not isinstance(role, TurnRole):
            role = TurnRole(role)
            object.__setattr__(self, "role", role)
        if (
            not isinstance(self.message_sha256, str)
            or _HEX_64.fullmatch(self.message_sha256) is None
        ):
            raise ValueError("message_sha256 must be a lowercase SHA-256 digest")
        start = _span_offset(self.start, "start")
        end = _span_offset(self.end, "end")
        if end <= start:
            raise ValueError("artifact evidence requires start < end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        if not isinstance(self.evidence_id, str) or _EVIDENCE_ID.fullmatch(
            self.evidence_id
        ) is None:
            raise ValueError("evidence_id must be an ae1_ SHA-256 identity")
        expected = artifact_evidence_id(
            relationship_id=self.relationship_id,
            source_turn_id=self.source_turn_id,
            source_id=self.source_id,
            source_revision=self.source_revision,
            message_sha256=self.message_sha256,
            start=start,
            end=end,
        )
        if self.evidence_id != expected:
            raise ValueError("evidence_id does not match the artifact evidence locator")

    @classmethod
    def create(
        cls,
        *,
        relationship_id: str,
        source_turn_id: str,
        source_id: str,
        source_revision: str,
        role: TurnRole,
        message_sha256: str,
        start: int,
        end: int,
    ) -> "ArtifactEvidenceReference":
        return cls(
            relationship_id=relationship_id,
            source_turn_id=source_turn_id,
            source_id=source_id,
            source_revision=source_revision,
            role=role,
            message_sha256=message_sha256,
            start=start,
            end=end,
            evidence_id=artifact_evidence_id(
                relationship_id=relationship_id,
                source_turn_id=source_turn_id,
                source_id=source_id,
                source_revision=source_revision,
                message_sha256=message_sha256,
                start=start,
                end=end,
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reference_version": self.reference_version,
            "kind": self.kind,
            "evidence_id": self.evidence_id,
            "relationship_id": self.relationship_id,
            "source_turn_id": self.source_turn_id,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "role": self.role.value,
            "message_sha256": self.message_sha256,
            "start": self.start,
            "end": self.end,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactEvidenceReference":
        if not isinstance(data, Mapping) or set(data) != cls._FIELDS:
            raise ValueError(
                "ArtifactEvidenceReference contains unknown or missing fields"
            )
        return cls(
            reference_version=data["reference_version"],
            kind=data["kind"],
            evidence_id=data["evidence_id"],
            relationship_id=data["relationship_id"],
            source_turn_id=data["source_turn_id"],
            source_id=data["source_id"],
            source_revision=data["source_revision"],
            role=data["role"],
            message_sha256=data["message_sha256"],
            start=data["start"],
            end=data["end"],
        )


def artifact_evidence_references_from_value(
    values: object,
) -> Tuple[ArtifactEvidenceReference, ...]:
    """Parses one canonical set-valued reference array without reordering it."""
    if not isinstance(values, Sequence) or isinstance(
        values,
        (str, bytes, bytearray),
    ):
        raise ValueError("artifact evidence references must be an array")
    references = tuple(
        item
        if isinstance(item, ArtifactEvidenceReference)
        else ArtifactEvidenceReference.from_dict(item)
        for item in values
    )
    if len(references) > 16:
        raise ValueError("one artifact permits at most 16 evidence references")
    identities = tuple(item.evidence_id for item in references)
    if len(identities) != len(set(identities)):
        raise ValueError("artifact evidence references must not repeat identities")
    if identities != tuple(sorted(identities)):
        raise ValueError("artifact evidence references must be sorted by evidence_id")
    return references


__all__ = [
    "ARCHIVAL_EVIDENCE_CITATION_VERSION",
    "ARTIFACT_EVIDENCE_REFERENCE_VERSION",
    "ArchivalEvidenceCitation",
    "ArtifactEvidenceReference",
    "archival_evidence_citations_from_value",
    "artifact_evidence_id",
    "artifact_evidence_references_from_value",
]
