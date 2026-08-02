"""Derived authority policy for memory recall projections."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from collections import defaultdict
from typing import DefaultDict, Dict, Optional, Sequence, Tuple, Union

from erii.core.evidence_authority import has_exceptional_delivery
from erii.core.retriever import MemoryRetriever
from erii.models.archival import TimelineEntry
from erii.models.node import MemoryNode
from erii.models.provenance import ArtifactProvenanceState
from erii.models.recall import (
    MemoryRecallProjection,
    RecallAudience,
    RecallAuthorityTier,
)
from erii.models.turn import (
    TurnMessage,
    TurnRecord,
    TurnRole,
    TurnStatus,
)


RecallAuthorityArtifact = Union[MemoryNode, TimelineEntry]


@dataclass(frozen=True)
class RecallAuthoritySelection:
    """Selected projections plus fallbacks for best-effort Legacy slots."""

    projections: Tuple[MemoryRecallProjection, ...]
    legacy_fallbacks: Dict[str, MemoryRecallProjection]


class RecallAuthorityClassifier:
    """Derives generation authority without persisting a second truth field."""

    @classmethod
    def classify(
        cls,
        artifact: RecallAuthorityArtifact,
        *,
        source_turn: Optional[TurnRecord],
        authority_source_chain: bool,
    ) -> RecallAuthorityTier:
        descriptor = artifact.extractor_descriptor
        schema_version = (
            descriptor.extraction_schema_version
            if descriptor is not None
            else None
        )
        if schema_version != "2":
            if cls._is_exceptional_source(artifact, source_turn):
                return RecallAuthorityTier.QUARANTINED_HISTORY
            return RecallAuthorityTier.LEGACY_CONTEXT

        if (
            artifact.provenance_state != ArtifactProvenanceState.COMPLETE
            or not authority_source_chain
            or not cls._schema_two_references_are_valid(artifact, source_turn)
        ):
            return RecallAuthorityTier.QUARANTINED_HISTORY
        return RecallAuthorityTier.ORDINARY

    @staticmethod
    def _is_exceptional_source(
        artifact: RecallAuthorityArtifact,
        source_turn: Optional[TurnRecord],
    ) -> bool:
        return bool(
            source_turn is not None
            and source_turn.status == TurnStatus.COMPLETED
            and source_turn.turn_id == artifact.source_turn_id
            and source_turn.relationship_id == artifact.relationship_id
            and has_exceptional_delivery(source_turn)
        )

    @classmethod
    def _schema_two_references_are_valid(
        cls,
        artifact: RecallAuthorityArtifact,
        source_turn: Optional[TurnRecord],
    ) -> bool:
        if (
            source_turn is None
            or source_turn.status != TurnStatus.COMPLETED
            or source_turn.turn_id != artifact.source_turn_id
            or source_turn.relationship_id != artifact.relationship_id
            or not artifact.evidence_references
        ):
            return False
        messages = cls._messages_by_id(source_turn)
        exceptional = has_exceptional_delivery(source_turn)
        for reference in artifact.evidence_references:
            message = messages.get(reference.source_id)
            if (
                message is None
                or reference.relationship_id != source_turn.relationship_id
                or reference.source_turn_id != source_turn.turn_id
                or reference.source_revision != source_turn.source_revision
                or reference.role != message.role
                or reference.message_sha256
                != hashlib.sha256(message.content.encode("utf-8")).hexdigest()
                or not 0 <= reference.start < reference.end <= len(message.content)
                or (exceptional and reference.role == TurnRole.AGENT)
            ):
                return False
        return True

    @staticmethod
    def _messages_by_id(turn: TurnRecord) -> Dict[str, Optional[TurnMessage]]:
        messages = [turn.transcript.user_message]
        if turn.transcript.agent_message is not None:
            messages.append(turn.transcript.agent_message)
        result: Dict[str, Optional[TurnMessage]] = {}
        for message in messages:
            result[message.message_id] = (
                None if message.message_id in result else message
            )
        return result


class RecallAuthoritySelector:
    """Applies bounded authority selection without replacing retrieval rank."""

    @staticmethod
    def select(
        projections: Sequence[MemoryRecallProjection],
        *,
        audience: RecallAudience,
        query: str,
        top_k: int,
        max_per_type: int = 2,
    ) -> RecallAuthoritySelection:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if max_per_type < 1:
            raise ValueError("max_per_type must be positive")
        query_tokens = MemoryRetriever.tokenize(query)
        ranked = tuple(
            item
            for item in projections
            if item.authority_tier != RecallAuthorityTier.QUARANTINED_HISTORY
        )
        all_ordinary = tuple(
            item
            for item in ranked
            if item.authority_tier == RecallAuthorityTier.ORDINARY
        )
        ordinary = RecallAuthoritySelector._apply_diversity_cap(
            all_ordinary,
            max_per_type=max_per_type,
        )
        if audience == RecallAudience.PUBLIC:
            return RecallAuthoritySelection(ordinary[:top_k], {})

        ordinary_contents = {
            item.content.encode("utf-8") for item in all_ordinary
        }
        legacy = tuple(
            item
            for item in ranked
            if item.authority_tier == RecallAuthorityTier.LEGACY_CONTEXT
            and item.content.encode("utf-8") not in ordinary_contents
        )
        if len(ordinary) < top_k:
            selected = list(ordinary)
            type_counts = RecallAuthoritySelector._type_counts(selected)
            for item in legacy:
                if len(selected) >= top_k:
                    break
                if not RecallAuthoritySelector._fits_diversity_cap(
                    item,
                    type_counts,
                    max_per_type=max_per_type,
                ):
                    continue
                selected.append(item)
                RecallAuthoritySelector._increment_type_count(item, type_counts)
            return RecallAuthoritySelection(
                tuple(selected),
                {},
            )
        if top_k == 1 or not legacy:
            return RecallAuthoritySelection(ordinary[:top_k], {})

        displaced_ordinary = ordinary[top_k - 1]
        relevant_legacy = tuple(
            item
            for item in legacy
            if (
                not query_tokens
                or query_tokens.intersection(
                    MemoryRetriever.tokenize(item.content)
                )
            )
        )
        retained_ordinary = ordinary[: top_k - 1]
        type_counts = RecallAuthoritySelector._type_counts(retained_ordinary)
        reserved_legacy = next(
            (
                item
                for item in relevant_legacy
                if RecallAuthoritySelector._fits_diversity_cap(
                    item,
                    type_counts,
                    max_per_type=max_per_type,
                )
            ),
            None,
        )
        if reserved_legacy is None:
            return RecallAuthoritySelection(ordinary[:top_k], {})
        return RecallAuthoritySelection(
            retained_ordinary + (reserved_legacy,),
            {reserved_legacy.projection_id: displaced_ordinary},
        )

    @staticmethod
    def _apply_diversity_cap(
        projections: Sequence[MemoryRecallProjection],
        *,
        max_per_type: int,
    ) -> Tuple[MemoryRecallProjection, ...]:
        selected = []
        type_counts: DefaultDict[str, int] = defaultdict(int)
        for item in projections:
            if not RecallAuthoritySelector._fits_diversity_cap(
                item,
                type_counts,
                max_per_type=max_per_type,
            ):
                continue
            selected.append(item)
            RecallAuthoritySelector._increment_type_count(item, type_counts)
        return tuple(selected)

    @staticmethod
    def _type_counts(
        projections: Sequence[MemoryRecallProjection],
    ) -> DefaultDict[str, int]:
        counts: DefaultDict[str, int] = defaultdict(int)
        for item in projections:
            RecallAuthoritySelector._increment_type_count(item, counts)
        return counts

    @staticmethod
    def _fits_diversity_cap(
        projection: MemoryRecallProjection,
        type_counts: DefaultDict[str, int],
        *,
        max_per_type: int,
    ) -> bool:
        return (
            projection.memory_type == "core"
            or type_counts[projection.memory_type] < max_per_type
        )

    @staticmethod
    def _increment_type_count(
        projection: MemoryRecallProjection,
        type_counts: DefaultDict[str, int],
    ) -> None:
        type_counts[projection.memory_type] += 1


__all__ = [
    "RecallAuthorityClassifier",
    "RecallAuthoritySelection",
    "RecallAuthoritySelector",
]
