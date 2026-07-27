"""Append-only integrity rules for typed temporal relationship events."""

from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set

from erii.models.relationship import RelationshipEvent, RelationshipEventType
from erii.models.temporal import (
    OpenLoopResolution,
    OpenLoopResolutionKind,
    OpenLoopSpec,
    PromiseConditionConfirmation,
    PromiseResolution,
    PromiseResolutionKind,
    PromiseSpec,
)


class TemporalHistoryConflictError(ValueError):
    """Raised when a temporal event cannot be appended to existing history."""


class TemporalHistoryValidator:
    """Validates references and single-resolution invariants without storage I/O."""

    @classmethod
    def validate_complete_history(
        cls,
        events: Sequence[RelationshipEvent],
    ) -> None:
        """Validates a complete history even when separate journals lost merge order.

        MemoryPack carries direct events and adjudication records separately.
        Their combined source order is therefore not authoritative. This method
        reconstructs a stable causal order from typed references, then applies
        the same append-only rules used by storage.
        """
        unique: List[RelationshipEvent] = []
        by_id: Dict[str, RelationshipEvent] = {}
        for event in events:
            existing = by_id.get(event.event_id)
            if existing is not None:
                if not existing.same_payload_as(event):
                    raise TemporalHistoryConflictError(
                        f"event_id {event.event_id!r} has conflicting history payloads"
                    )
                continue
            by_id[event.event_id] = event
            unique.append(event)

        all_ids = set(by_id)
        prerequisites = cls.causal_prerequisites(unique)
        for event in unique:
            missing = set(cls._reference_ids(event)).difference(all_ids)
            if missing:
                raise TemporalHistoryConflictError(
                    "temporal history references missing events: "
                    + ", ".join(sorted(missing))
                )

        accepted: List[RelationshipEvent] = []
        accepted_ids: Set[str] = set()
        pending = list(unique)
        while pending:
            for index, event in enumerate(pending):
                if not prerequisites[event.event_id].issubset(accepted_ids):
                    continue
                cls.validate_append(accepted, event)
                accepted.append(event)
                accepted_ids.add(event.event_id)
                del pending[index]
                break
            else:
                raise TemporalHistoryConflictError(
                    "temporal history has unresolved causal ordering"
                )

    @classmethod
    def causal_prerequisites(
        cls,
        events: Sequence[RelationshipEvent],
    ) -> Dict[str, Set[str]]:
        """Returns explicit references plus lifecycle ordering dependencies."""
        confirmations: Dict[str, Set[str]] = {}
        for event in events:
            payload = event.temporal_payload
            if isinstance(payload, PromiseConditionConfirmation):
                confirmations.setdefault(payload.promise_event_id, set()).add(
                    event.event_id
                )

        prerequisites: Dict[str, Set[str]] = {}
        for event in events:
            required = set(cls._reference_ids(event))
            payload = event.temporal_payload
            if isinstance(payload, PromiseResolution):
                # A complete history containing both must replay confirmation
                # before the terminal resolution, regardless of journal grouping.
                required.update(confirmations.get(payload.promise_event_id, set()))
            required.discard(event.event_id)
            prerequisites[event.event_id] = required
        return prerequisites

    @classmethod
    def validate_history(cls, events: Sequence[RelationshipEvent]) -> None:
        """Validates one complete history in its append order."""
        accepted: List[RelationshipEvent] = []
        for event in events:
            cls.validate_append(accepted, event)
            accepted.append(event)

    @classmethod
    def validate_append(
        cls,
        existing_events: Sequence[RelationshipEvent],
        event: RelationshipEvent,
    ) -> None:
        """Validates one prospective append against immutable prior events."""
        by_id: Dict[str, RelationshipEvent] = {}
        relationship_ids: Set[str] = set()
        for existing in existing_events:
            relationship_ids.add(existing.relationship_id)
            duplicate = by_id.get(existing.event_id)
            if duplicate is not None and not duplicate.same_payload_as(existing):
                raise TemporalHistoryConflictError(
                    f"event_id {existing.event_id!r} has conflicting history payloads"
                )
            by_id.setdefault(existing.event_id, existing)

        if relationship_ids and relationship_ids != {event.relationship_id}:
            raise TemporalHistoryConflictError(
                "temporal history contains events from another relationship"
            )
        duplicate = by_id.get(event.event_id)
        if duplicate is not None:
            if duplicate.same_payload_as(event):
                return
            raise TemporalHistoryConflictError(
                f"event_id {event.event_id!r} already has different content"
            )

        payload = event.temporal_payload
        if payload is None:
            return
        if isinstance(payload, PromiseSpec):
            return
        if isinstance(payload, OpenLoopSpec):
            cls._validate_open_loop_origin(existing_events, payload)
            return
        if isinstance(payload, PromiseConditionConfirmation):
            target = cls._typed_target(
                by_id,
                payload.promise_event_id,
                RelationshipEventType.PROMISE,
                PromiseSpec,
                "Promise condition confirmation",
            )
            target_payload = target.temporal_payload
            assert isinstance(target_payload, PromiseSpec)
            condition = target_payload.activation_condition
            if condition is None or condition.condition_id != payload.condition_id:
                raise TemporalHistoryConflictError(
                    "Promise condition confirmation does not match the target condition"
                )
            if cls._promise_resolution(existing_events, payload.promise_event_id) is not None:
                raise TemporalHistoryConflictError(
                    "a resolved Promise cannot receive a condition confirmation"
                )
            if any(
                isinstance(item.temporal_payload, PromiseConditionConfirmation)
                and item.temporal_payload.promise_event_id == payload.promise_event_id
                and item.temporal_payload.condition_id == payload.condition_id
                for item in existing_events
            ):
                raise TemporalHistoryConflictError(
                    "this Promise condition has already been confirmed"
                )
            return
        if isinstance(payload, PromiseResolution):
            cls._typed_target(
                by_id,
                payload.promise_event_id,
                RelationshipEventType.PROMISE,
                PromiseSpec,
                "Promise resolution",
            )
            if cls._promise_resolution(existing_events, payload.promise_event_id) is not None:
                raise TemporalHistoryConflictError("this Promise is already resolved")
            if payload.resolution_kind == PromiseResolutionKind.SUPERSEDED:
                successor_id = payload.superseding_promise_event_id
                assert successor_id is not None
                cls._typed_target(
                    by_id,
                    successor_id,
                    RelationshipEventType.PROMISE,
                    PromiseSpec,
                    "superseding Promise",
                )
                cls._reject_promise_supersession_cycle(
                    existing_events,
                    payload.promise_event_id,
                    successor_id,
                )
            return
        if isinstance(payload, OpenLoopResolution):
            cls._typed_target(
                by_id,
                payload.open_loop_event_id,
                RelationshipEventType.OPEN_LOOP,
                OpenLoopSpec,
                "Open Loop resolution",
            )
            if cls._open_loop_resolution(
                existing_events,
                payload.open_loop_event_id,
            ) is not None:
                raise TemporalHistoryConflictError("this Open Loop is already resolved")
            if payload.resolution_kind == OpenLoopResolutionKind.SUPERSEDED:
                successor_id = payload.superseding_open_loop_event_id
                assert successor_id is not None
                cls._typed_target(
                    by_id,
                    successor_id,
                    RelationshipEventType.OPEN_LOOP,
                    OpenLoopSpec,
                    "superseding Open Loop",
                )
                cls._reject_open_loop_supersession_cycle(
                    existing_events,
                    payload.open_loop_event_id,
                    successor_id,
                )

    @staticmethod
    def _typed_target(
        by_id: Mapping[str, RelationshipEvent],
        target_id: str,
        event_type: RelationshipEventType,
        payload_type,
        operation: str,
    ) -> RelationshipEvent:
        target = by_id.get(target_id)
        if target is None:
            raise TemporalHistoryConflictError(
                f"{operation} references a missing earlier event"
            )
        if target.event_type != event_type or not isinstance(
            target.temporal_payload,
            payload_type,
        ):
            raise TemporalHistoryConflictError(
                f"{operation} references an event of the wrong type"
            )
        return target

    @staticmethod
    def _reference_ids(event: RelationshipEvent) -> Sequence[str]:
        payload = event.temporal_payload
        if isinstance(payload, PromiseConditionConfirmation):
            return (payload.promise_event_id,)
        if isinstance(payload, PromiseResolution):
            references = [payload.promise_event_id]
            if payload.superseding_promise_event_id is not None:
                references.append(payload.superseding_promise_event_id)
            return tuple(references)
        if isinstance(payload, OpenLoopResolution):
            references = [payload.open_loop_event_id]
            if payload.superseding_open_loop_event_id is not None:
                references.append(payload.superseding_open_loop_event_id)
            return tuple(references)
        return ()

    @staticmethod
    def _promise_resolution(
        events: Iterable[RelationshipEvent],
        promise_event_id: str,
    ) -> Optional[PromiseResolution]:
        for item in events:
            payload = item.temporal_payload
            if isinstance(payload, PromiseResolution) and (
                payload.promise_event_id == promise_event_id
            ):
                return payload
        return None

    @staticmethod
    def _open_loop_resolution(
        events: Iterable[RelationshipEvent],
        open_loop_event_id: str,
    ) -> Optional[OpenLoopResolution]:
        for item in events:
            payload = item.temporal_payload
            if isinstance(payload, OpenLoopResolution) and (
                payload.open_loop_event_id == open_loop_event_id
            ):
                return payload
        return None

    @staticmethod
    def _validate_open_loop_origin(
        events: Iterable[RelationshipEvent],
        payload: OpenLoopSpec,
    ) -> None:
        origin_id = payload.origin_memory_node_id
        if origin_id is None:
            return
        for item in events:
            existing = item.temporal_payload
            if isinstance(existing, OpenLoopSpec) and (
                existing.origin_memory_node_id == origin_id
            ):
                raise TemporalHistoryConflictError(
                    "this legacy memory already has a formal Open Loop"
                )

    @classmethod
    def _reject_promise_supersession_cycle(
        cls,
        events: Iterable[RelationshipEvent],
        target_id: str,
        successor_id: str,
    ) -> None:
        successors = {
            payload.promise_event_id: payload.superseding_promise_event_id
            for item in events
            if isinstance((payload := item.temporal_payload), PromiseResolution)
            and payload.resolution_kind == PromiseResolutionKind.SUPERSEDED
        }
        cls._reject_cycle(successors, target_id, successor_id, "Promise")

    @classmethod
    def _reject_open_loop_supersession_cycle(
        cls,
        events: Iterable[RelationshipEvent],
        target_id: str,
        successor_id: str,
    ) -> None:
        successors = {
            payload.open_loop_event_id: payload.superseding_open_loop_event_id
            for item in events
            if isinstance((payload := item.temporal_payload), OpenLoopResolution)
            and payload.resolution_kind == OpenLoopResolutionKind.SUPERSEDED
        }
        cls._reject_cycle(successors, target_id, successor_id, "Open Loop")

    @staticmethod
    def _reject_cycle(
        successors: Mapping[str, Optional[str]],
        target_id: str,
        successor_id: str,
        subject: str,
    ) -> None:
        cursor: Optional[str] = successor_id
        seen: Set[str] = set()
        while cursor is not None and cursor not in seen:
            if cursor == target_id:
                raise TemporalHistoryConflictError(
                    f"{subject} supersession would create a cycle"
                )
            seen.add(cursor)
            cursor = successors.get(cursor)


__all__ = ["TemporalHistoryConflictError", "TemporalHistoryValidator"]
