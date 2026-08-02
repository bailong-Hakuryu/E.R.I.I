"""Small, explicit host capabilities shared by the runnable examples."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from typing import Any

from erii import (
    ArchivalArtifactsDecision,
    ArchivalEvidenceCitation,
    ArchivalStatus,
    DeliveryDisposition,
    DeliveryExceptionActorKind,
    DeliveryExceptionReasonCode,
    DeliveryExceptionRecord,
    ERIIEngine,
    ExtractorDescriptor,
    MemoryCandidate,
    MemoryExtractionRequest,
    MemoryType,
    TimelineCandidate,
)


def visible_exchange_delivery_exception(
    actor_id: str,
) -> DeliveryExceptionRecord:
    """Declares that ``record_turn`` receives an exchange already shown by the host."""
    return DeliveryExceptionRecord(
        disposition=DeliveryDisposition.SHOWN_UNREVIEWED,
        actor_kind=DeliveryExceptionActorKind.HOST_POLICY,
        actor_id=actor_id,
        reason_code=DeliveryExceptionReasonCode.PREEXISTING_VISIBLE_EXCHANGE,
        decided_at="2026-08-03T00:00:00+00:00",
    )


def _user_message_evidence(
    request: MemoryExtractionRequest,
) -> tuple[ArchivalEvidenceCitation, ...]:
    message = request.transcript.user_message
    return (
        ArchivalEvidenceCitation(
            source_id=message.message_id,
            source_revision=request.source_revision,
            quote=message.content,
            start=0,
            end=len(message.content),
        ),
    )


class DeterministicMemoryExtractor:
    """Example-only MemoryExtractorV1 with fixed, reviewable output."""

    descriptor = ExtractorDescriptor(
        extractor_id="examples.deterministic-memory-extractor",
        extractor_version="1.0",
        extraction_schema_version="2",
    )

    def __init__(
        self,
        *,
        timeline_content: str,
        memory_content: str,
        tags: tuple[str, ...],
        node_type: MemoryType = MemoryType.PREFERENCE,
        base_importance: float = 0.8,
        emotional_score: float = 0.2,
    ) -> None:
        self.timeline_content = timeline_content
        self.memory_content = memory_content
        self.tags = tags
        self.node_type = node_type
        self.base_importance = base_importance
        self.emotional_score = emotional_score

    def extract(
        self,
        request: MemoryExtractionRequest,
    ) -> ArchivalArtifactsDecision:
        evidence = _user_message_evidence(request)
        return ArchivalArtifactsDecision(
            timeline=(
                TimelineCandidate(
                    content=self.timeline_content,
                    evidence=evidence,
                ),
            ),
            memories=(
                MemoryCandidate(
                    node_type=self.node_type,
                    content=self.memory_content,
                    tags=self.tags,
                    base_importance=self.base_importance,
                    emotional_score=self.emotional_score,
                    evidence=evidence,
                ),
            ),
        )


class CallableJSONMemoryExtractor:
    """Adapts a custom ``prompt -> JSON`` callable to MemoryExtractorV1."""

    descriptor = ExtractorDescriptor(
        extractor_id="examples.callable-json-memory-extractor",
        extractor_version="1.0",
        extraction_schema_version="2",
    )

    def __init__(self, generate: Callable[[str], str]) -> None:
        self.generate = generate

    def extract(
        self,
        request: MemoryExtractionRequest,
    ) -> ArchivalArtifactsDecision:
        prompt = (
            "Extract durable memory as JSON with timeline_entry and impressions.\n"
            f"User: {request.transcript.user_message.content}\n"
            f"Assistant: {request.transcript.agent_message.content}"
        )
        payload = json.loads(self.generate(prompt))
        if not isinstance(payload, Mapping):
            raise ValueError("custom extractor output must be a JSON object")
        timeline_content = payload.get("timeline_entry")
        raw_impressions = payload.get("impressions")
        if not isinstance(timeline_content, str) or not timeline_content.strip():
            raise ValueError("custom extractor timeline_entry must be non-empty")
        if not isinstance(raw_impressions, list) or not raw_impressions:
            raise ValueError("custom extractor impressions must be a non-empty array")

        evidence = _user_message_evidence(request)
        memories = tuple(
            self._memory_candidate(item, evidence)
            for item in raw_impressions
        )
        return ArchivalArtifactsDecision(
            timeline=(TimelineCandidate(timeline_content, evidence=evidence),),
            memories=memories,
        )

    @staticmethod
    def _memory_candidate(
        value: Any,
        evidence: tuple[ArchivalEvidenceCitation, ...],
    ) -> MemoryCandidate:
        if not isinstance(value, Mapping):
            raise ValueError("each custom extractor impression must be an object")
        return MemoryCandidate(
            node_type=MemoryType(value["type"]),
            content=value["content"],
            tags=tuple(value.get("tags", ())),
            base_importance=value.get("base_importance", 0.5),
            emotional_score=value.get("emotional_score", 0.0),
            evidence=evidence,
        )


def record_and_archive_visible_exchange(
    engine: ERIIEngine,
    *,
    agent_id: str,
    user_id: str,
    user_message: str,
    agent_message: str,
    turn_id: str,
    actor_id: str,
) -> None:
    """Records a stable Source Turn and verifies synchronous archival completed."""
    source = engine.record_turn(
        agent_id=agent_id,
        user_id=user_id,
        user_message=user_message,
        agent_message=agent_message,
        turn_id=turn_id,
        delivery_exception=visible_exchange_delivery_exception(actor_id),
    )
    archival = engine.archive_turn(
        agent_id=agent_id,
        user_id=user_id,
        source_turn_id=source.source_turn_id,
        idempotency_key=f"{turn_id}:memory-archive",
    )
    if getattr(archival, "status", None) is not ArchivalStatus.COMPLETED:
        raise RuntimeError("example expected inline archival to complete")
