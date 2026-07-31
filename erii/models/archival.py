"""Reliable archival contracts built on canonical Source Turns."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Tuple, Union

from erii.models.node import MemoryNode, MemoryType, MemoryVisibility
from erii.models.provenance import (
    ArtifactProvenanceState,
    ExtractorDescriptor,
)
from erii.models.turn import InteractionContextSignal, SourceTranscript


def _text(value: object, field_name: str, *, max_length: int = 8192) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise ValueError(f"{field_name} exceeds its maximum length")
    return cleaned


def _optional_text(
    value: Optional[object],
    field_name: str,
    *,
    max_length: int = 512,
) -> Optional[str]:
    if value is None:
        return None
    return _text(value, field_name, max_length=max_length)


def _score(value: object, field_name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return number


def _tags(values: Sequence[object]) -> Tuple[str, ...]:
    if isinstance(values, (str, bytes)) or len(values) > 16:
        raise ValueError("tags must be a sequence of at most 16 strings")
    result = tuple(_text(value, "tag", max_length=64) for value in values)
    if len(result) != len(set(result)):
        raise ValueError("tags must be unique")
    return result


class ArchivalStatus(str, Enum):
    """Persistent lifecycle state for one accepted archival submission."""

    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"


class ArchivalPhase(str, Enum):
    """The phase that may run on the next archival attempt."""

    EXTRACTION = "extraction"
    COMMIT = "commit"


class ArchivalOutcomeCode(str, Enum):
    """Stable, non-sensitive result code separate from lifecycle status."""

    ARTIFACTS_COMMITTED = "artifacts_committed"
    NO_MEMORY = "no_memory"
    EXTRACTOR_TEMPORARY_FAILURE = "extractor_temporary_failure"
    INVALID_EXTRACTOR_OUTPUT = "invalid_extractor_output"
    COMMIT_TEMPORARY_FAILURE = "commit_temporary_failure"
    PROCESSING_LEASE_EXPIRED = "processing_lease_expired"
    PERMANENT_FAILURE = "permanent_failure"
    RETRY_EXHAUSTED = "retry_exhausted"


class ArchivalRetentionState(str, Enum):
    """Whether detailed receipt fields are still retained."""

    FULL = "full"
    COMPACTED = "compacted"


class ArchivalArtifactKind(str, Enum):
    """Stable artifact kinds included in a sanitized manifest."""

    TIMELINE_ENTRY = "timeline_entry"
    MEMORY_NODE = "memory_node"


@dataclass(frozen=True)
class TimelineCandidate:
    """Untrusted proposed narrative artifact without authoritative fields."""

    content: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "content",
            _text(self.content, "timeline content", max_length=4096),
        )

    def to_dict(self) -> Dict[str, str]:
        return {"content": self.content}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TimelineCandidate":
        if set(data) != {"content"}:
            raise ValueError("TimelineCandidate contains unknown or missing fields")
        return cls(content=data["content"])


@dataclass(frozen=True)
class MemoryCandidate:
    """Untrusted proposed MemoryNode content with bounded semantic fields."""

    node_type: MemoryType
    content: str
    tags: Tuple[str, ...] = ()
    base_importance: float = 0.5
    emotional_score: float = 0.0
    confidence: float = 0.8
    visibility: str = MemoryVisibility.PUBLIC_LOG.value
    decayable: bool = True
    is_unresolved: bool = False
    foreshadowing_tags: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        node_type = (
            self.node_type
            if isinstance(self.node_type, MemoryType)
            else MemoryType(self.node_type)
        )
        if node_type in (MemoryType.INSTRUCTION, MemoryType.CORE):
            raise ValueError("extractors cannot propose instruction or core memory")
        object.__setattr__(self, "node_type", node_type)
        object.__setattr__(
            self,
            "content",
            _text(self.content, "memory content", max_length=4096),
        )
        object.__setattr__(self, "tags", _tags(self.tags))
        object.__setattr__(
            self,
            "foreshadowing_tags",
            _tags(self.foreshadowing_tags),
        )
        object.__setattr__(
            self,
            "base_importance",
            _score(self.base_importance, "base_importance", 0.0, 1.0),
        )
        object.__setattr__(
            self,
            "emotional_score",
            _score(self.emotional_score, "emotional_score", -1.0, 1.0),
        )
        object.__setattr__(
            self,
            "confidence",
            _score(self.confidence, "confidence", 0.0, 1.0),
        )
        visibility = str(self.visibility)
        if visibility not in {
            MemoryVisibility.PUBLIC_LOG.value,
            MemoryVisibility.INTERNAL_MONOLOGUE.value,
        }:
            raise ValueError("unsupported memory visibility")
        object.__setattr__(self, "visibility", visibility)
        if not isinstance(self.decayable, bool) or not isinstance(
            self.is_unresolved,
            bool,
        ):
            raise ValueError("memory flags must be booleans")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_type": self.node_type.value,
            "content": self.content,
            "tags": list(self.tags),
            "base_importance": self.base_importance,
            "emotional_score": self.emotional_score,
            "confidence": self.confidence,
            "visibility": self.visibility,
            "decayable": self.decayable,
            "is_unresolved": self.is_unresolved,
            "foreshadowing_tags": list(self.foreshadowing_tags),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryCandidate":
        allowed = {
            "node_type",
            "content",
            "tags",
            "base_importance",
            "emotional_score",
            "confidence",
            "visibility",
            "decayable",
            "is_unresolved",
            "foreshadowing_tags",
        }
        if not {"node_type", "content"}.issubset(data) or not set(data).issubset(
            allowed
        ):
            raise ValueError("MemoryCandidate contains unknown or missing fields")
        return cls(
            node_type=data["node_type"],
            content=data["content"],
            tags=tuple(data.get("tags", ())),
            base_importance=data.get("base_importance", 0.5),
            emotional_score=data.get("emotional_score", 0.0),
            confidence=data.get("confidence", 0.8),
            visibility=data.get(
                "visibility",
                MemoryVisibility.PUBLIC_LOG.value,
            ),
            decayable=data.get("decayable", True),
            is_unresolved=data.get("is_unresolved", False),
            foreshadowing_tags=tuple(data.get("foreshadowing_tags", ())),
        )


@dataclass(frozen=True)
class ArchivalArtifactsDecision:
    """A strict successful extraction containing at least one artifact."""

    timeline: Tuple[TimelineCandidate, ...] = ()
    memories: Tuple[MemoryCandidate, ...] = ()

    kind = "artifacts"

    def __post_init__(self) -> None:
        timeline = tuple(
            item
            if isinstance(item, TimelineCandidate)
            else TimelineCandidate.from_dict(item)
            for item in self.timeline
        )
        memories = tuple(
            item
            if isinstance(item, MemoryCandidate)
            else MemoryCandidate.from_dict(item)
            for item in self.memories
        )
        if not timeline and not memories:
            raise ValueError("artifacts decision requires at least one artifact")
        if len(timeline) > 1:
            raise ValueError("one Source Turn permits at most one Timeline candidate")
        if len(memories) > 64:
            raise ValueError("the hard maximum is 64 Memory candidates")
        object.__setattr__(self, "timeline", timeline)
        object.__setattr__(self, "memories", memories)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "timeline": [item.to_dict() for item in self.timeline],
            "memories": [item.to_dict() for item in self.memories],
        }


_NO_MEMORY_REASON_CODES = frozenset(
    {
        "duplicate_information",
        "ephemeral_coordination",
        "no_new_information",
        "none",
        "nothing_durable",
        "ordinary_acknowledgement",
    }
)


@dataclass(frozen=True)
class ArchivalNoMemoryDecision:
    """A strict, explicit successful decision that yields zero artifacts."""

    reason_code: str

    kind = "no_memory"

    def __post_init__(self) -> None:
        if self.reason_code not in _NO_MEMORY_REASON_CODES:
            raise ValueError("reason_code is not in the no-memory allowlist")

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "reason_code": self.reason_code}


ArchivalExtractionDecision = Union[
    ArchivalArtifactsDecision,
    ArchivalNoMemoryDecision,
]


def archival_decision_from_value(value: object) -> ArchivalExtractionDecision:
    """Validates a host extractor result without permissive coercion."""
    if isinstance(
        value,
        (ArchivalArtifactsDecision, ArchivalNoMemoryDecision),
    ):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("extractor result must be a discriminated mapping")
    kind = value.get("kind")
    if kind == ArchivalArtifactsDecision.kind:
        if not set(value).issubset({"kind", "timeline", "memories"}):
            raise ValueError("artifacts decision contains unknown fields")
        return ArchivalArtifactsDecision(
            timeline=tuple(value.get("timeline", ())),
            memories=tuple(value.get("memories", ())),
        )
    if kind == ArchivalNoMemoryDecision.kind:
        if set(value) != {"kind", "reason_code"}:
            raise ValueError("no_memory decision contains unknown or missing fields")
        return ArchivalNoMemoryDecision(reason_code=value["reason_code"])
    raise ValueError("extractor result requires kind=artifacts|no_memory")


@dataclass(frozen=True)
class MemoryExtractionRequest:
    """Bounded canonical input passed to a host-provided memory extractor."""

    source_turn_id: str
    source_revision: str
    relationship_id: str
    agent_id: str
    user_id: str
    transcript: SourceTranscript
    interaction_context: Tuple[InteractionContextSignal, ...] = ()


class MemoryExtractorV1(Protocol):
    """Host implementation boundary for versioned memory extraction."""

    descriptor: ExtractorDescriptor

    def extract(
        self,
        request: MemoryExtractionRequest,
    ) -> ArchivalExtractionDecision:
        """Returns one strict decision and never writes kernel storage."""


@dataclass(frozen=True)
class ArchivalArtifactReference:
    """Content-free artifact identity exposed in an archival receipt.

    ``artifact_fingerprint`` was added after the original kind-and-ID
    manifest.  ``None`` therefore means that an older receipt can still be
    read, but cannot certify the current artifact payload.
    """

    kind: ArchivalArtifactKind
    artifact_id: str
    artifact_fingerprint: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ArchivalArtifactKind):
            object.__setattr__(self, "kind", ArchivalArtifactKind(self.kind))
        object.__setattr__(
            self,
            "artifact_id",
            _text(self.artifact_id, "artifact_id", max_length=128),
        )
        if self.artifact_fingerprint is not None:
            fingerprint = _text(
                self.artifact_fingerprint,
                "artifact_fingerprint",
                max_length=64,
            )
            if len(fingerprint) != 64 or any(
                character not in "0123456789abcdef" for character in fingerprint
            ):
                raise ValueError("artifact_fingerprint must be a lowercase SHA-256")
            object.__setattr__(self, "artifact_fingerprint", fingerprint)

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "kind": self.kind.value,
            "artifact_id": self.artifact_id,
            "artifact_fingerprint": self.artifact_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchivalArtifactReference":
        return cls(
            kind=ArchivalArtifactKind(str(data["kind"])),
            artifact_id=str(data["artifact_id"]),
            artifact_fingerprint=(
                str(data["artifact_fingerprint"])
                if data.get("artifact_fingerprint") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class TimelineEntry:
    """Structured experiential timeline artifact with explicit provenance."""

    timeline_entry_id: str
    relationship_id: str
    agent_id: str
    user_id: str
    content: str
    recorded_at: Optional[str]
    legacy_timestamp: Optional[str] = None
    source_turn_id: Optional[str] = None
    source_archival_id: Optional[str] = None
    provenance_state: ArtifactProvenanceState = ArtifactProvenanceState.COMPLETE
    extractor_descriptor: Optional[ExtractorDescriptor] = None

    def __post_init__(self) -> None:
        for field_name in (
            "timeline_entry_id",
            "relationship_id",
            "agent_id",
            "user_id",
            "content",
        ):
            object.__setattr__(
                self,
                field_name,
                _text(
                    getattr(self, field_name),
                    field_name,
                    max_length=4096 if field_name == "content" else 256,
                ),
            )
        if not isinstance(self.provenance_state, ArtifactProvenanceState):
            object.__setattr__(
                self,
                "provenance_state",
                ArtifactProvenanceState(self.provenance_state),
            )
        descriptor = self.extractor_descriptor
        if descriptor is not None and not isinstance(descriptor, ExtractorDescriptor):
            object.__setattr__(
                self,
                "extractor_descriptor",
                ExtractorDescriptor.from_dict(descriptor),
            )
        if self.recorded_at is not None:
            object.__setattr__(
                self,
                "recorded_at",
                _text(self.recorded_at, "recorded_at", max_length=128),
            )
        if self.legacy_timestamp is not None:
            object.__setattr__(
                self,
                "legacy_timestamp",
                _text(
                    self.legacy_timestamp,
                    "legacy_timestamp",
                    max_length=128,
                ),
            )
        if self.provenance_state == ArtifactProvenanceState.COMPLETE:
            if (
                self.recorded_at is None
                or self.legacy_timestamp is not None
                or not self.source_turn_id
                or not self.source_archival_id
                or self.extractor_descriptor is None
            ):
                raise ValueError(
                    "complete timeline provenance requires UTC time and all source fields"
                )
            parsed = datetime.fromisoformat(self.recorded_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("modern Timeline recorded_at must include UTC offset")
            if parsed.utcoffset().total_seconds() != 0:
                raise ValueError("modern Timeline recorded_at must be UTC")
        elif self.recorded_at is not None:
            raise ValueError(
                "legacy Timeline cannot promote an unverified timestamp to recorded_at"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timeline_entry_id": self.timeline_entry_id,
            "relationship_id": self.relationship_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "content": self.content,
            "recorded_at": self.recorded_at,
            "legacy_timestamp": self.legacy_timestamp,
            "source_turn_id": self.source_turn_id,
            "source_archival_id": self.source_archival_id,
            "provenance_state": self.provenance_state.value,
            "extractor_descriptor": (
                self.extractor_descriptor.to_dict()
                if self.extractor_descriptor is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TimelineEntry":
        descriptor = data.get("extractor_descriptor")
        return cls(
            timeline_entry_id=str(data["timeline_entry_id"]),
            relationship_id=str(data["relationship_id"]),
            agent_id=str(data["agent_id"]),
            user_id=str(data["user_id"]),
            content=str(data["content"]),
            recorded_at=(
                str(data["recorded_at"])
                if data.get("recorded_at") is not None
                else None
            ),
            legacy_timestamp=data.get("legacy_timestamp"),
            source_turn_id=data.get("source_turn_id"),
            source_archival_id=data.get("source_archival_id"),
            provenance_state=ArtifactProvenanceState(
                str(data.get("provenance_state", "legacy_unavailable"))
            ),
            extractor_descriptor=(
                ExtractorDescriptor.from_dict(descriptor)
                if descriptor is not None
                else None
            ),
        )


def archival_artifact_fingerprint(
    artifact: Union[MemoryNode, TimelineEntry],
) -> str:
    """Hashes every immutable field of one committed archival artifact.

    MemoryNode recall/lifecycle fields are intentionally absent: reinforcement
    may change ``base_importance``, ``access_count``, ``state``,
    ``is_unresolved``, ``is_latest``, ``superseded_by`` and
    ``last_accessed_at`` after a valid commit.  All remaining stored fields,
    including content, scope and the complete extractor descriptor, are bound.
    TimelineEntry is frozen, so its complete serialized payload is bound.
    """
    if isinstance(artifact, TimelineEntry):
        kind = ArchivalArtifactKind.TIMELINE_ENTRY
        payload = artifact.to_dict()
    elif isinstance(artifact, MemoryNode):
        kind = ArchivalArtifactKind.MEMORY_NODE
        descriptor = artifact.extractor_descriptor
        payload = {
            "node_id": artifact.node_id,
            "user_id": artifact.user_id,
            "agent_id": artifact.agent_id,
            "node_type": (
                artifact.node_type.value
                if isinstance(artifact.node_type, MemoryType)
                else str(artifact.node_type)
            ),
            "content": artifact.content,
            "tags": list(artifact.tags),
            "emotional_score": artifact.emotional_score,
            "confidence": artifact.confidence,
            "decayable": artifact.decayable,
            "timeline_entry": artifact.timeline_entry,
            "visibility": artifact.visibility,
            "foreshadowing_tags": list(artifact.foreshadowing_tags),
            "relationship_id": artifact.relationship_id,
            "source_turn_id": artifact.source_turn_id,
            "source_archival_id": artifact.source_archival_id,
            "provenance_state": artifact.provenance_state.value,
            "extractor_descriptor": (
                descriptor.to_dict() if descriptor is not None else None
            ),
            "created_at": artifact.created_at,
        }
    else:
        raise TypeError("unsupported archival artifact type")
    encoded = json.dumps(
        {"kind": kind.value, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PreparedArchivalBatch:
    """Validated immutable artifacts frozen before their publish point."""

    archival_id: str
    relationship_id: str
    source_turn_id: str
    source_revision: str
    descriptor: ExtractorDescriptor
    timeline: Tuple[TimelineEntry, ...] = ()
    memories: Tuple[MemoryNode, ...] = ()
    batch_digest: str = ""

    def __post_init__(self) -> None:
        timeline = tuple(
            item if isinstance(item, TimelineEntry) else TimelineEntry.from_dict(item)
            for item in self.timeline
        )
        memories = tuple(
            item if isinstance(item, MemoryNode) else MemoryNode.from_dict(item)
            for item in self.memories
        )
        object.__setattr__(self, "timeline", timeline)
        object.__setattr__(self, "memories", memories)
        expected = self.content_digest(
            self.archival_id,
            self.relationship_id,
            self.source_turn_id,
            self.source_revision,
            self.descriptor,
            timeline,
            memories,
        )
        if self.batch_digest and self.batch_digest != expected:
            raise ValueError("prepared archival batch digest mismatch")
        object.__setattr__(self, "batch_digest", expected)

    @staticmethod
    def content_digest(
        archival_id: str,
        relationship_id: str,
        source_turn_id: str,
        source_revision: str,
        descriptor: ExtractorDescriptor,
        timeline: Sequence[TimelineEntry],
        memories: Sequence[MemoryNode],
    ) -> str:
        payload = {
            "archival_id": archival_id,
            "relationship_id": relationship_id,
            "source_turn_id": source_turn_id,
            "source_revision": source_revision,
            "descriptor": descriptor.to_dict(),
            "timeline": [item.to_dict() for item in timeline],
            "memories": [item.to_dict() for item in memories],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def manifest(self) -> Tuple[ArchivalArtifactReference, ...]:
        return tuple(
            ArchivalArtifactReference(
                ArchivalArtifactKind.TIMELINE_ENTRY,
                item.timeline_entry_id,
                archival_artifact_fingerprint(item),
            )
            for item in self.timeline
        ) + tuple(
            ArchivalArtifactReference(
                ArchivalArtifactKind.MEMORY_NODE,
                item.node_id,
                archival_artifact_fingerprint(item),
            )
            for item in self.memories
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "archival_id": self.archival_id,
            "relationship_id": self.relationship_id,
            "source_turn_id": self.source_turn_id,
            "source_revision": self.source_revision,
            "descriptor": self.descriptor.to_dict(),
            "timeline": [item.to_dict() for item in self.timeline],
            "memories": [item.to_dict() for item in self.memories],
            "batch_digest": self.batch_digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PreparedArchivalBatch":
        return cls(
            archival_id=str(data["archival_id"]),
            relationship_id=str(data["relationship_id"]),
            source_turn_id=str(data["source_turn_id"]),
            source_revision=str(data["source_revision"]),
            descriptor=ExtractorDescriptor.from_dict(data["descriptor"]),
            timeline=tuple(
                TimelineEntry.from_dict(item) for item in data.get("timeline", ())
            ),
            memories=tuple(
                MemoryNode.from_dict(item) for item in data.get("memories", ())
            ),
            batch_digest=str(data.get("batch_digest", "")),
        )


@dataclass(frozen=True)
class CommitPermit:
    """Short-lived authority to publish one permanently bound batch."""

    token: str
    binding_digest: str
    expires_at: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "token",
            _text(self.token, "commit permit token", max_length=128),
        )
        object.__setattr__(
            self,
            "binding_digest",
            _text(self.binding_digest, "binding digest", max_length=128),
        )
        if isinstance(self.expires_at, bool) or not isinstance(
            self.expires_at,
            (int, float),
        ):
            raise ValueError("commit permit expiry must be numeric")
        object.__setattr__(self, "expires_at", float(self.expires_at))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token": self.token,
            "binding_digest": self.binding_digest,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommitPermit":
        return cls(
            token=str(data["token"]),
            binding_digest=str(data["binding_digest"]),
            expires_at=float(data["expires_at"]),
        )


@dataclass(frozen=True)
class ArchivalReceipt:
    """Non-sensitive persistent projection of one archival lifecycle."""

    archival_id: str
    relationship_id: str
    agent_id: str
    user_id: str
    source_turn_id: str
    source_revision: str
    status: ArchivalStatus
    phase: ArchivalPhase
    extractor_descriptor: ExtractorDescriptor
    submitted_at: str
    updated_at: str
    extraction_attempts: int = 0
    commit_attempts: int = 0
    outcome_code: Optional[ArchivalOutcomeCode] = None
    retryable: Optional[bool] = None
    safe_summary: Optional[str] = None
    next_attempt_at: Optional[float] = None
    completed_at: Optional[str] = None
    artifact_manifest: Optional[Tuple[ArchivalArtifactReference, ...]] = ()
    retention_state: ArchivalRetentionState = ArchivalRetentionState.FULL

    def __post_init__(self) -> None:
        for name, enum_cls in (
            ("status", ArchivalStatus),
            ("phase", ArchivalPhase),
            ("retention_state", ArchivalRetentionState),
        ):
            value = getattr(self, name)
            if not isinstance(value, enum_cls):
                object.__setattr__(self, name, enum_cls(value))
        if self.outcome_code is not None and not isinstance(
            self.outcome_code,
            ArchivalOutcomeCode,
        ):
            object.__setattr__(
                self,
                "outcome_code",
                ArchivalOutcomeCode(self.outcome_code),
            )
        descriptor = self.extractor_descriptor
        if not isinstance(descriptor, ExtractorDescriptor):
            object.__setattr__(
                self,
                "extractor_descriptor",
                ExtractorDescriptor.from_dict(descriptor),
            )
        manifest = self.artifact_manifest
        if manifest is not None:
            object.__setattr__(
                self,
                "artifact_manifest",
                tuple(
                    item
                    if isinstance(item, ArchivalArtifactReference)
                    else ArchivalArtifactReference.from_dict(item)
                    for item in manifest
                ),
            )
        if self.retention_state == ArchivalRetentionState.COMPACTED:
            if self.artifact_manifest is not None:
                raise ValueError("a compacted receipt cannot retain its manifest")
        elif self.artifact_manifest is None:
            raise ValueError("a full receipt requires an artifact manifest")
        if self.status == ArchivalStatus.COMPLETED and self.outcome_code not in {
            ArchivalOutcomeCode.ARTIFACTS_COMMITTED,
            ArchivalOutcomeCode.NO_MEMORY,
        }:
            raise ValueError("completed archival requires a success outcome")
        if self.status == ArchivalStatus.FAILED and self.outcome_code is None:
            raise ValueError("failed archival requires an outcome code")

    @property
    def timeline_count(self) -> Optional[int]:
        if self.artifact_manifest is None:
            return None
        return sum(
            item.kind == ArchivalArtifactKind.TIMELINE_ENTRY
            for item in self.artifact_manifest
        )

    @property
    def memory_node_count(self) -> Optional[int]:
        if self.artifact_manifest is None:
            return None
        return sum(
            item.kind == ArchivalArtifactKind.MEMORY_NODE
            for item in self.artifact_manifest
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "archival_id": self.archival_id,
            "relationship_id": self.relationship_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "source_turn_id": self.source_turn_id,
            "source_revision": self.source_revision,
            "status": self.status.value,
            "phase": self.phase.value,
            "extractor_descriptor": self.extractor_descriptor.to_dict(),
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
            "extraction_attempts": self.extraction_attempts,
            "commit_attempts": self.commit_attempts,
            "outcome_code": (
                self.outcome_code.value if self.outcome_code is not None else None
            ),
            "retryable": self.retryable,
            "safe_summary": self.safe_summary,
            "next_attempt_at": self.next_attempt_at,
            "completed_at": self.completed_at,
            "artifact_manifest": (
                [item.to_dict() for item in self.artifact_manifest]
                if self.artifact_manifest is not None
                else None
            ),
            "timeline_count": self.timeline_count,
            "memory_node_count": self.memory_node_count,
            "retention_state": self.retention_state.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchivalReceipt":
        manifest = data.get("artifact_manifest")
        outcome = data.get("outcome_code")
        return cls(
            archival_id=str(data["archival_id"]),
            relationship_id=str(data["relationship_id"]),
            agent_id=str(data["agent_id"]),
            user_id=str(data["user_id"]),
            source_turn_id=str(data["source_turn_id"]),
            source_revision=str(data["source_revision"]),
            status=ArchivalStatus(str(data["status"])),
            phase=ArchivalPhase(str(data["phase"])),
            extractor_descriptor=ExtractorDescriptor.from_dict(
                data["extractor_descriptor"]
            ),
            submitted_at=str(data["submitted_at"]),
            updated_at=str(data["updated_at"]),
            extraction_attempts=int(data.get("extraction_attempts", 0)),
            commit_attempts=int(data.get("commit_attempts", 0)),
            outcome_code=(
                ArchivalOutcomeCode(str(outcome)) if outcome is not None else None
            ),
            retryable=data.get("retryable"),
            safe_summary=data.get("safe_summary"),
            next_attempt_at=data.get("next_attempt_at"),
            completed_at=data.get("completed_at"),
            artifact_manifest=(
                tuple(ArchivalArtifactReference.from_dict(item) for item in manifest)
                if manifest is not None
                else None
            ),
            retention_state=ArchivalRetentionState(
                str(data.get("retention_state", "full"))
            ),
        )


@dataclass(frozen=True)
class ArchivalTombstone:
    """Portable minimal proof of one terminal archival identity."""

    archival_id: str
    relationship_id: str
    agent_id: str
    user_id: str
    source_turn_id: str
    source_revision: str
    status: ArchivalStatus
    outcome_code: ArchivalOutcomeCode
    terminal_at: str
    request_fingerprint: str
    idempotency_fingerprint: str

    retention_state = ArchivalRetentionState.COMPACTED

    def __post_init__(self) -> None:
        if not isinstance(self.status, ArchivalStatus):
            object.__setattr__(self, "status", ArchivalStatus(self.status))
        if not isinstance(self.outcome_code, ArchivalOutcomeCode):
            object.__setattr__(
                self,
                "outcome_code",
                ArchivalOutcomeCode(self.outcome_code),
            )
        if self.status not in {ArchivalStatus.COMPLETED, ArchivalStatus.FAILED}:
            raise ValueError("ArchivalTombstone requires a terminal status")
        for field_name in (
            "archival_id",
            "relationship_id",
            "agent_id",
            "user_id",
            "source_turn_id",
            "source_revision",
            "terminal_at",
            "request_fingerprint",
            "idempotency_fingerprint",
        ):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name, max_length=256),
            )

    @classmethod
    def from_record(cls, record: "ArchivalRecord") -> "ArchivalTombstone":
        receipt = record.receipt
        if receipt.status not in {ArchivalStatus.COMPLETED, ArchivalStatus.FAILED}:
            raise ValueError("only terminal archival records have tombstones")
        return cls(
            archival_id=receipt.archival_id,
            relationship_id=receipt.relationship_id,
            agent_id=receipt.agent_id,
            user_id=receipt.user_id,
            source_turn_id=receipt.source_turn_id,
            source_revision=receipt.source_revision,
            status=receipt.status,
            outcome_code=receipt.outcome_code,
            terminal_at=receipt.completed_at or receipt.updated_at,
            request_fingerprint=record.request_fingerprint,
            idempotency_fingerprint=record.idempotency_fingerprint,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "archival_id": self.archival_id,
            "relationship_id": self.relationship_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "source_turn_id": self.source_turn_id,
            "source_revision": self.source_revision,
            "status": self.status.value,
            "outcome_code": self.outcome_code.value,
            "terminal_at": self.terminal_at,
            "request_fingerprint": self.request_fingerprint,
            "idempotency_fingerprint": self.idempotency_fingerprint,
            "retention_state": self.retention_state.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchivalTombstone":
        return cls(
            archival_id=str(data["archival_id"]),
            relationship_id=str(data["relationship_id"]),
            agent_id=str(data["agent_id"]),
            user_id=str(data["user_id"]),
            source_turn_id=str(data["source_turn_id"]),
            source_revision=str(data["source_revision"]),
            status=ArchivalStatus(str(data["status"])),
            outcome_code=ArchivalOutcomeCode(str(data["outcome_code"])),
            terminal_at=str(data["terminal_at"]),
            request_fingerprint=str(data["request_fingerprint"]),
            idempotency_fingerprint=str(data["idempotency_fingerprint"]),
        )


@dataclass(frozen=True)
class ArchivalRecord:
    """Internal durable command state; never contains a transcript copy."""

    receipt: ArchivalReceipt
    idempotency_fingerprint: str
    request_fingerprint: str
    record_version: int = 1
    lease_token: Optional[str] = None
    lease_expires_at: Optional[float] = None
    attempt_id: Optional[str] = None
    prepared_batch: Optional[PreparedArchivalBatch] = None
    prepared_outcome_code: Optional[ArchivalOutcomeCode] = None
    commit_binding_digest: Optional[str] = None
    commit_permit: Optional[CommitPermit] = None
    recovered_expired_lease: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.recovered_expired_lease, bool):
            raise ValueError("recovered_expired_lease must be a boolean")
        if self.commit_permit is not None and not isinstance(
            self.commit_permit,
            CommitPermit,
        ):
            object.__setattr__(
                self,
                "commit_permit",
                CommitPermit.from_dict(self.commit_permit),
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt": self.receipt.to_dict(),
            "idempotency_fingerprint": self.idempotency_fingerprint,
            "request_fingerprint": self.request_fingerprint,
            "record_version": self.record_version,
            "lease_token": self.lease_token,
            "lease_expires_at": self.lease_expires_at,
            "attempt_id": self.attempt_id,
            "prepared_batch": (
                self.prepared_batch.to_dict()
                if self.prepared_batch is not None
                else None
            ),
            "prepared_outcome_code": (
                self.prepared_outcome_code.value
                if self.prepared_outcome_code is not None
                else None
            ),
            "commit_binding_digest": self.commit_binding_digest,
            "commit_permit": (
                self.commit_permit.to_dict()
                if self.commit_permit is not None
                else None
            ),
            "recovered_expired_lease": self.recovered_expired_lease,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchivalRecord":
        prepared = data.get("prepared_batch")
        prepared_outcome = data.get("prepared_outcome_code")
        permit = data.get("commit_permit")
        return cls(
            receipt=ArchivalReceipt.from_dict(data["receipt"]),
            idempotency_fingerprint=str(data["idempotency_fingerprint"]),
            request_fingerprint=str(data["request_fingerprint"]),
            record_version=int(data.get("record_version", 1)),
            lease_token=data.get("lease_token"),
            lease_expires_at=data.get("lease_expires_at"),
            attempt_id=data.get("attempt_id"),
            prepared_batch=(
                PreparedArchivalBatch.from_dict(prepared)
                if prepared is not None
                else None
            ),
            prepared_outcome_code=(
                ArchivalOutcomeCode(str(prepared_outcome))
                if prepared_outcome is not None
                else None
            ),
            commit_binding_digest=data.get("commit_binding_digest"),
            commit_permit=(
                CommitPermit.from_dict(permit) if permit is not None else None
            ),
            recovered_expired_lease=data.get("recovered_expired_lease", False),
        )


def merge_archival_tombstone_batch(
    relationship_id: str,
    incoming: Sequence[ArchivalTombstone],
    *,
    existing: Sequence[ArchivalTombstone],
    live_records: Sequence[ArchivalRecord],
) -> Tuple[ArchivalTombstone, ...]:
    """Validates a portable ledger batch before any storage mutation."""
    merged = {item.archival_id: item for item in existing}
    live_by_id = {
        item.receipt.archival_id: item for item in live_records
    }

    def conflicts(
        left: ArchivalTombstone,
        right: ArchivalTombstone,
    ) -> bool:
        return (
            left.relationship_id == right.relationship_id
            and (
                left.idempotency_fingerprint
                == right.idempotency_fingerprint
                or left.request_fingerprint == right.request_fingerprint
            )
            and left.archival_id != right.archival_id
        )

    accepted = list(existing)
    for tombstone in incoming:
        if tombstone.relationship_id != relationship_id:
            raise ArchivalConflictError(
                "Archival Tombstone belongs to another relationship"
            )
        current = merged.get(tombstone.archival_id)
        if current is not None and current != tombstone:
            raise ArchivalConflictError("Archival Tombstone identity conflict")
        live = live_by_id.get(tombstone.archival_id)
        matched_live = False
        if live is not None:
            if live.receipt.status not in {
                ArchivalStatus.COMPLETED,
                ArchivalStatus.FAILED,
            }:
                raise ArchivalConflictError(
                    "Archival Tombstone conflicts with a live archival"
                )
            if ArchivalTombstone.from_record(live) != tombstone:
                raise ArchivalConflictError(
                    "Archival Tombstone conflicts with a live archival"
                )
            matched_live = True
        for record in live_records:
            if (
                record.receipt.relationship_id == relationship_id
                and record.receipt.archival_id != tombstone.archival_id
                and (
                    record.idempotency_fingerprint
                    == tombstone.idempotency_fingerprint
                    or record.request_fingerprint
                    == tombstone.request_fingerprint
                )
            ):
                raise ArchivalConflictError(
                    "Archival Tombstone conflicts with a live archival binding"
                )
        if any(conflicts(item, tombstone) for item in accepted):
            raise ArchivalConflictError(
                "Archival Tombstone conflicts with an existing binding"
            )
        if current is None and not matched_live:
            merged[tombstone.archival_id] = tombstone
            accepted.append(tombstone)
    return tuple(merged.values())


class ArchivalError(Exception):
    """Base class for public archival failures."""


class ArchivalCapabilityError(ArchivalError):
    """The requested archival capability is not configured or supported."""


class ArchivalSubmissionError(ArchivalError, ValueError):
    """The command was rejected before an archival identity was created."""


class ArchivalConflictError(ArchivalError, ValueError):
    """An idempotency key or immutable archival binding conflicts."""


class ArchivalNotFoundError(ArchivalError, LookupError):
    """No archival exists in the requested relationship scope."""


class ArchivalProcessingError(ArchivalError):
    """Inline archival failed after acceptance and carries a safe receipt."""

    def __init__(self, receipt: ArchivalReceipt) -> None:
        super().__init__(
            receipt.safe_summary or "accepted archival processing did not complete"
        )
        self.receipt = receipt


class RetryableArchivalError(ArchivalError):
    """A host capability may raise this for a transient external failure."""


class PermanentArchivalError(ArchivalError):
    """A host capability may raise this for a non-retryable configuration error."""


@dataclass(frozen=True)
class ArchivalDrainReport:
    """Truthful bounded result for an explicit archival drain snapshot."""

    snapshot_size: int
    completed: int
    failed: int
    unfinished_archival_ids: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_size": self.snapshot_size,
            "completed": self.completed,
            "failed": self.failed,
            "unfinished_archival_ids": list(self.unfinished_archival_ids),
        }


@dataclass(frozen=True)
class ShutdownReport:
    """Cooperative shutdown observation; queued work is not implicitly drained."""

    worker_stopped: bool
    unfinished_archival_ids: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_stopped": self.worker_stopped,
            "unfinished_archival_ids": list(self.unfinished_archival_ids),
        }


__all__ = [
    "ArchivalArtifactKind",
    "ArchivalArtifactReference",
    "ArchivalArtifactsDecision",
    "ArchivalCapabilityError",
    "ArchivalConflictError",
    "ArchivalDrainReport",
    "ArchivalError",
    "ArchivalExtractionDecision",
    "ArchivalNoMemoryDecision",
    "ArchivalNotFoundError",
    "ArchivalOutcomeCode",
    "ArchivalPhase",
    "ArchivalProcessingError",
    "ArchivalReceipt",
    "ArchivalRecord",
    "ArchivalRetentionState",
    "ArchivalStatus",
    "ArchivalSubmissionError",
    "ArchivalTombstone",
    "CommitPermit",
    "MemoryCandidate",
    "MemoryExtractionRequest",
    "MemoryExtractorV1",
    "PermanentArchivalError",
    "PreparedArchivalBatch",
    "RetryableArchivalError",
    "ShutdownReport",
    "TimelineCandidate",
    "TimelineEntry",
    "archival_decision_from_value",
]
