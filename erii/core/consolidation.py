"""Deterministic, rebuildable Episode and Relationship Chapter projections."""

from collections import defaultdict
import hashlib
import json
from typing import Dict, Iterable, List, Mapping, Sequence
import uuid

from erii.models.consolidation import (
    Episode,
    RelationshipChapter,
    RelationshipConsolidation,
)
from erii.models.relationship import RelationshipEvent
from erii.models.temporal import (
    OpenLoopResolution,
    PromiseConditionConfirmation,
    PromiseResolution,
)


class RelationshipConsolidator:
    """Projects narrative layers without creating another authoritative history."""

    @classmethod
    def project(
        cls,
        relationship_id: str,
        events: Sequence[RelationshipEvent],
    ) -> RelationshipConsolidation:
        """Builds deterministic sourced groupings from explicit event identity links."""
        ordered = sorted(events, key=lambda item: (item.recorded_at, item.event_id))
        if any(event.relationship_id != relationship_id for event in ordered):
            raise ValueError("all consolidation events must belong to the relationship")

        parent: Dict[str, str] = {event.event_id: event.event_id for event in ordered}
        history_fingerprint = cls._history_fingerprint(ordered)

        def find(event_id: str) -> str:
            current = event_id
            while parent[current] != current:
                parent[current] = parent[parent[current]]
                current = parent[current]
            return current

        def union(left: str, right: str) -> None:
            if left not in parent or right not in parent:
                return
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                # The lexical choice keeps the result independent of input order.
                low, high = sorted((left_root, right_root))
                parent[high] = low

        occurrence_groups: Dict[str, List[str]] = defaultdict(list)
        for event in ordered:
            adjudication = event.metadata.get("adjudication", {})
            occurrence = adjudication.get("occurrence_fingerprint")
            if isinstance(occurrence, str) and occurrence.strip():
                occurrence_groups[occurrence].append(event.event_id)
        for event_ids in occurrence_groups.values():
            for event_id in event_ids[1:]:
                union(event_ids[0], event_id)

        for event in ordered:
            payload = event.temporal_payload
            target_id = None
            if isinstance(payload, PromiseResolution):
                target_id = payload.promise_event_id
            elif isinstance(payload, PromiseConditionConfirmation):
                target_id = payload.promise_event_id
            elif isinstance(payload, OpenLoopResolution):
                target_id = payload.open_loop_event_id
            if target_id is not None:
                union(event.event_id, target_id)

        components: Dict[str, List[RelationshipEvent]] = defaultdict(list)
        for event in ordered:
            components[find(event.event_id)].append(event)

        grouped = [
            sorted(group, key=lambda item: (item.recorded_at, item.event_id))
            for group in components.values()
            if len(group) >= 2
        ]
        grouped.sort(key=lambda group: (group[0].recorded_at, group[0].event_id))

        episodes = tuple(cls._episode(relationship_id, group) for group in grouped)
        covered = {
            event_id for episode in episodes for event_id in episode.event_ids
        }
        unconsolidated = tuple(
            event.event_id for event in ordered if event.event_id not in covered
        )

        chapters = cls._chapters(
            relationship_id,
            ordered,
            episodes,
            history_fingerprint,
        )

        return RelationshipConsolidation(
            relationship_id=relationship_id,
            episodes=episodes,
            chapters=chapters,
            covered_event_ids=tuple(
                event.event_id for event in ordered if event.event_id in covered
            ),
            unconsolidated_event_ids=unconsolidated,
            history_fingerprint=history_fingerprint,
        )

    @classmethod
    def _chapters(
        cls,
        relationship_id: str,
        events: Sequence[RelationshipEvent],
        episodes: Sequence[Episode],
        history_fingerprint: str,
    ) -> tuple[RelationshipChapter, ...]:
        """Connects Episodes only through explicit cross-Episode references."""
        if len(episodes) < 2:
            return ()
        event_to_episode = {
            event_id: episode.episode_id
            for episode in episodes
            for event_id in episode.event_ids
        }
        parent = {
            episode.episode_id: episode.episode_id
            for episode in episodes
        }

        def find(episode_id: str) -> str:
            current = episode_id
            while parent[current] != current:
                parent[current] = parent[parent[current]]
                current = parent[current]
            return current

        def union(left: str, right: str) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                low, high = sorted((left_root, right_root))
                parent[high] = low

        for event in events:
            source_episode = event_to_episode.get(event.event_id)
            if source_episode is None:
                continue
            adjudication = event.metadata.get("adjudication", {})
            references = (
                adjudication.get("references", ())
                if isinstance(adjudication, Mapping)
                else ()
            )
            if isinstance(references, (str, bytes)):
                continue
            for target_event_id in references:
                if not isinstance(target_event_id, str):
                    continue
                target_episode = event_to_episode.get(target_event_id)
                if (
                    target_episode is not None
                    and target_episode != source_episode
                ):
                    union(source_episode, target_episode)

        components: Dict[str, List[Episode]] = defaultdict(list)
        for episode in episodes:
            components[find(episode.episode_id)].append(episode)
        connected = [
            sorted(
                items,
                key=lambda item: (item.started_at, item.episode_id),
            )
            for items in components.values()
            if len(items) >= 2
        ]
        connected.sort(
            key=lambda items: (items[0].started_at, items[0].episode_id)
        )

        chapters = []
        for items in connected:
            episode_ids = tuple(item.episode_id for item in items)
            event_ids = tuple(
                event_id for episode in items for event_id in episode.event_ids
            )
            chapter_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"erii:{relationship_id}:relationship-chapter:v1:"
                        f"{'|'.join(episode_ids)}"
                    ),
                )
            )
            chapters.append(
                RelationshipChapter(
                    chapter_id=chapter_id,
                    relationship_id=relationship_id,
                    episode_ids=episode_ids,
                    event_ids=event_ids,
                    title=f"{items[0].title} — {items[-1].title}",
                    summary=" ".join(
                        episode.summary for episode in items
                    )[:4000],
                    started_at=items[0].started_at,
                    ended_at=items[-1].ended_at,
                    history_fingerprint=history_fingerprint,
                )
            )
        return tuple(chapters)

    @classmethod
    def _episode(
        cls,
        relationship_id: str,
        events: Iterable[RelationshipEvent],
    ) -> Episode:
        ordered = tuple(events)
        event_ids = tuple(item.event_id for item in ordered)
        episode_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"erii:{relationship_id}:episode:v1:{'|'.join(event_ids)}",
            )
        )
        return Episode(
            episode_id=episode_id,
            relationship_id=relationship_id,
            event_ids=event_ids,
            title=ordered[0].content[:256],
            summary=" ".join(item.content for item in ordered)[:4000],
            started_at=ordered[0].recorded_at,
            ended_at=ordered[-1].recorded_at,
            history_fingerprint=cls._history_fingerprint(ordered),
        )

    @staticmethod
    def _history_fingerprint(events: Iterable[RelationshipEvent]) -> str:
        payload = [item.to_dict() for item in events]
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["RelationshipConsolidator"]
