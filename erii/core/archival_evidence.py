"""Kernel resolution of exact message evidence for archival artifacts."""

from __future__ import annotations

import hashlib
from typing import Mapping, Sequence, Tuple

from erii.models.archival_evidence import (
    ArchivalEvidenceCitation,
    ArtifactEvidenceReference,
)
from erii.core.evidence_authority import has_exceptional_delivery
from erii.models.turn import TurnMessage, TurnRecord, TurnRole


class ArchivalEvidenceResolver:
    """Resolves untrusted citations against one canonical persisted Source Turn."""

    @staticmethod
    def resolve(
        turn: TurnRecord,
        citations: Sequence[object],
    ) -> Tuple[ArtifactEvidenceReference, ...]:
        """Returns the canonical reference set for one schema-2 artifact."""
        if isinstance(citations, (str, bytes, bytearray)):
            raise ValueError("archival evidence citations must be an array")
        if not 1 <= len(citations) <= 16:
            raise ValueError("schema 2 artifacts require between 1 and 16 citations")

        parsed = tuple(
            item
            if isinstance(item, ArchivalEvidenceCitation)
            else ArchivalEvidenceCitation.from_dict(item)
            for item in citations
        )
        messages = ArchivalEvidenceResolver._messages_by_id(turn)
        references = []
        for citation in parsed:
            if citation.source_revision != turn.source_revision:
                raise ValueError(
                    "archival evidence source_revision does not match the Source Turn"
                )
            matching_messages = messages.get(citation.source_id, ())
            if not matching_messages:
                raise ValueError("archival evidence source message was not found")
            if len(matching_messages) != 1:
                raise ValueError("archival evidence source message is ambiguous")
            message = matching_messages[0]
            if citation.end > len(message.content):
                raise ValueError("archival evidence span exceeds its source message")
            if message.content[citation.start : citation.end] != citation.quote:
                raise ValueError("archival evidence quote does not match its exact span")

            references.append(
                ArtifactEvidenceReference.create(
                    relationship_id=turn.relationship_id,
                    source_turn_id=turn.turn_id,
                    source_id=message.message_id,
                    source_revision=turn.source_revision,
                    role=message.role,
                    message_sha256=hashlib.sha256(
                        message.content.encode("utf-8")
                    ).hexdigest(),
                    start=citation.start,
                    end=citation.end,
                )
            )

        identities = tuple(item.evidence_id for item in references)
        if len(identities) != len(set(identities)):
            raise ValueError("archival evidence citations must not repeat identities")
        canonical = tuple(sorted(references, key=lambda item: item.evidence_id))

        if (
            has_exceptional_delivery(turn)
            and any(item.role == TurnRole.AGENT for item in canonical)
        ):
            raise ValueError("continuity_exception_agent_evidence_quarantined")
        return canonical

    @staticmethod
    def _messages_by_id(
        turn: TurnRecord,
    ) -> Mapping[str, Tuple[TurnMessage, ...]]:
        transcript = turn.transcript
        visible_messages = [transcript.user_message]
        if transcript.agent_message is not None:
            visible_messages.append(transcript.agent_message)
        result = {}
        for message in visible_messages:
            result.setdefault(message.message_id, []).append(message)
        return {
            message_id: tuple(matches)
            for message_id, matches in result.items()
        }


__all__ = ["ArchivalEvidenceResolver"]
