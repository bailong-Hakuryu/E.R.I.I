"""Pure projection of temporal commitments and unfinished relationship matters."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

from erii.models.node import MemoryNode
from erii.models.recall import (
    RecallAudience,
    RecallSignalAuthority,
    RecallSignalProjection,
    RecallSignalReason,
    RecallSignalType,
    RecallSourceReference,
    WorldTime,
)
from erii.models.relationship import RelationshipEvent
from erii.models.temporal import (
    OpenLoopResolution,
    OpenLoopSpec,
    PromiseConditionConfirmation,
    PromiseResolution,
    PromiseResponsibleParty,
    PromiseSpec,
    WorldMoment,
)


class RecallSignalDeriver:
    """Derives current signals from immutable inputs without storage or clocks."""

    _SORT_PRIORITY = {
        RecallSignalType.PROMISE_OVERDUE: 0,
        RecallSignalType.PROMISE_DUE: 1,
        RecallSignalType.OPEN_LOOP: 2,
    }

    @classmethod
    def derive(
        cls,
        events: Sequence[RelationshipEvent],
        world_time: Optional[WorldTime],
        legacy_nodes: Sequence[MemoryNode] = (),
    ) -> Tuple[RecallSignalProjection, ...]:
        """Returns deterministic due, overdue, and open-loop projections.

        ``world_time`` is the only value used for deadline comparison. Recorded,
        occurred, observed, and system wall-clock values never enter this module.
        """
        ordered_events = sorted(events, key=lambda item: (item.recorded_at, item.event_id))
        promises: Dict[str, Tuple[RelationshipEvent, PromiseSpec]] = {}
        condition_confirmations: Dict[
            Tuple[str, str], RelationshipEvent
        ] = {}
        resolved_promises: Set[str] = set()
        open_loops: Dict[str, Tuple[RelationshipEvent, OpenLoopSpec]] = {}
        resolved_open_loops: Set[str] = set()

        # Collect roots first. Projection must not depend on a caller preserving
        # one merged append order across the direct-event and adjudication journals,
        # nor on imported ``recorded_at`` strings being chronologically sortable.
        for event in ordered_events:
            payload = event.temporal_payload
            if isinstance(payload, PromiseSpec):
                promises.setdefault(event.event_id, (event, payload))
                continue
            if isinstance(payload, OpenLoopSpec):
                open_loops.setdefault(event.event_id, (event, payload))

        for event in ordered_events:
            payload = event.temporal_payload
            if isinstance(payload, PromiseConditionConfirmation):
                target = promises.get(payload.promise_event_id)
                if target is None or target[0].relationship_id != event.relationship_id:
                    continue
                condition = target[1].activation_condition
                if condition is None or condition.condition_id != payload.condition_id:
                    continue
                condition_confirmations.setdefault(
                    (payload.promise_event_id, payload.condition_id),
                    event,
                )
                continue
            if isinstance(payload, PromiseResolution):
                target = promises.get(payload.promise_event_id)
                if target is not None and target[0].relationship_id == event.relationship_id:
                    resolved_promises.add(payload.promise_event_id)
                continue
            if isinstance(payload, OpenLoopResolution):
                target = open_loops.get(payload.open_loop_event_id)
                if target is not None and target[0].relationship_id == event.relationship_id:
                    resolved_open_loops.add(payload.open_loop_event_id)

        signals: List[RecallSignalProjection] = []
        for promise_event_id, (_event, promise) in promises.items():
            if promise_event_id in resolved_promises:
                continue
            confirmation_event = cls._condition_confirmation(
                promise_event_id,
                promise,
                condition_confirmations,
            )
            if promise.activation_condition is not None and confirmation_event is None:
                continue

            comparison = cls._compare(world_time, promise.due_at)
            if comparison is not None:
                if comparison < 0:
                    continue
                signal_type = (
                    RecallSignalType.PROMISE_DUE
                    if comparison == 0
                    else RecallSignalType.PROMISE_OVERDUE
                )
                reason = (
                    RecallSignalReason.AT_DEADLINE
                    if comparison == 0
                    else RecallSignalReason.PAST_DEADLINE
                )
                signals.append(
                    cls._promise_signal(
                        promise_event_id,
                        promise,
                        signal_type,
                        reason,
                        confirmation_event,
                    )
                )
            elif (
                promise.due_at is None
                and promise.activation_condition is not None
                and confirmation_event is not None
            ):
                signals.append(
                    cls._promise_signal(
                        promise_event_id,
                        promise,
                        RecallSignalType.PROMISE_DUE,
                        RecallSignalReason.CONDITION_CONFIRMED,
                        confirmation_event,
                        include_due_time=False,
                    )
                )

        formal_origin_memory_ids = {
            loop.origin_memory_node_id
            for _event, loop in open_loops.values()
            if loop.origin_memory_node_id is not None
        }
        for open_loop_event_id, (_event, open_loop) in open_loops.items():
            if open_loop_event_id in resolved_open_loops:
                continue
            continuation = (
                f" Expected continuation: {open_loop.expected_continuation}"
                if open_loop.expected_continuation is not None
                else ""
            )
            signals.append(
                RecallSignalProjection(
                    projection_id=f"signal:open_loop:{open_loop_event_id}",
                    source_id=open_loop_event_id,
                    source_kind="open_loop_event",
                    visibility=RecallAudience.AGENT_PRIVATE,
                    selection_reason="unresolved_formal_open_loop",
                    source_references=(cls._event_reference(open_loop_event_id),),
                    signal_type=RecallSignalType.OPEN_LOOP,
                    summary=(
                        f"Unfinished relationship matter: {open_loop.subject}."
                        f"{continuation}"
                    ),
                    subject_id=open_loop_event_id,
                    authority=RecallSignalAuthority.FORMAL_RELATIONSHIP_HISTORY,
                    reason=RecallSignalReason.UNRESOLVED_FORMAL_LOOP,
                    source_event_ids=(open_loop_event_id,),
                )
            )

        for node in sorted(legacy_nodes, key=lambda item: (item.created_at, item.node_id)):
            if not node.is_unresolved or not node.is_latest:
                continue
            if node.node_id in formal_origin_memory_ids:
                continue
            signals.append(
                RecallSignalProjection(
                    projection_id=f"signal:legacy_open_loop:{node.node_id}",
                    source_id=node.node_id,
                    source_kind="legacy_unresolved_memory",
                    visibility=RecallAudience.AGENT_PRIVATE,
                    selection_reason="legacy_unresolved_flag_low_authority",
                    signal_type=RecallSignalType.OPEN_LOOP,
                    summary=(
                        "Legacy unresolved memory (low authority, not a formal "
                        f"relationship obligation): {node.content}"
                    ),
                    subject_id=node.node_id,
                    authority=RecallSignalAuthority.LEGACY_UNRESOLVED_MEMORY,
                    reason=RecallSignalReason.LEGACY_UNRESOLVED_FLAG,
                    source_memory_ids=(node.node_id,),
                )
            )

        return tuple(sorted(signals, key=cls._sort_key))

    @staticmethod
    def _condition_confirmation(
        promise_event_id: str,
        promise: PromiseSpec,
        confirmations: Dict[Tuple[str, str], RelationshipEvent],
    ) -> Optional[RelationshipEvent]:
        condition = promise.activation_condition
        if condition is None:
            return None
        return confirmations.get((promise_event_id, condition.condition_id))

    @staticmethod
    def _compare(
        observed: Optional[WorldTime],
        due_at: Optional[WorldMoment],
    ) -> Optional[int]:
        if observed is None or due_at is None:
            return None
        observed_order = observed.order_value
        due_order = due_at.order_value
        if (
            observed.clock_id != due_at.clock_id
            or observed_order is None
            or due_order is None
            or not math.isfinite(observed_order)
            or not math.isfinite(due_order)
        ):
            return None
        if observed_order < due_order:
            return -1
        if observed_order > due_order:
            return 1
        return 0

    @classmethod
    def _promise_signal(
        cls,
        promise_event_id: str,
        promise: PromiseSpec,
        signal_type: RecallSignalType,
        reason: RecallSignalReason,
        confirmation_event: Optional[RelationshipEvent],
        *,
        include_due_time: bool = True,
    ) -> RecallSignalProjection:
        parties = cls._party_text(promise.responsible_parties)
        due_world_time = (
            cls._recall_world_time(promise.due_at)
            if include_due_time and promise.due_at is not None
            else None
        )
        condition = promise.activation_condition
        source_event_ids = [promise_event_id]
        references = [cls._event_reference(promise_event_id)]
        if confirmation_event is not None:
            source_event_ids.append(confirmation_event.event_id)
            references.append(cls._event_reference(confirmation_event.event_id))

        if signal_type == RecallSignalType.PROMISE_OVERDUE:
            summary = (
                f"The recorded deadline has passed for {parties} to {promise.action}. "
                "This is a timing signal, not a breach finding."
            )
        elif reason == RecallSignalReason.AT_DEADLINE:
            summary = f"A recorded promise is due: {parties} committed to {promise.action}."
        else:
            summary = (
                f"A recorded promise condition is confirmed: {parties} committed "
                f"to {promise.action}."
            )

        return RecallSignalProjection(
            projection_id=f"signal:{signal_type.value}:{promise_event_id}",
            source_id=promise_event_id,
            source_kind="promise_event",
            visibility=RecallAudience.AGENT_PRIVATE,
            selection_reason=reason.value,
            source_references=tuple(references),
            signal_type=signal_type,
            summary=summary,
            subject_id=promise_event_id,
            authority=RecallSignalAuthority.FORMAL_RELATIONSHIP_HISTORY,
            reason=reason,
            source_event_ids=tuple(source_event_ids),
            due_world_time=due_world_time,
            condition_id=condition.condition_id if condition is not None else None,
            clock_id=due_world_time.clock_id if due_world_time is not None else None,
        )

    @staticmethod
    def _party_text(parties: Sequence[PromiseResponsibleParty]) -> str:
        values = [party.value for party in parties]
        return values[0] if len(values) == 1 else " and ".join(values)

    @staticmethod
    def _recall_world_time(moment: WorldMoment) -> WorldTime:
        return WorldTime(
            clock_id=moment.clock_id,
            display_value=moment.display_value,
            order_value=moment.order_value,
        )

    @staticmethod
    def _event_reference(event_id: str) -> RecallSourceReference:
        return RecallSourceReference(
            source_id=event_id,
            source_kind="relationship_event",
        )

    @classmethod
    def _sort_key(cls, signal: RecallSignalProjection) -> Tuple[int, int, str]:
        legacy_rank = (
            1
            if signal.authority == RecallSignalAuthority.LEGACY_UNRESOLVED_MEMORY
            else 0
        )
        return cls._SORT_PRIORITY[signal.signal_type], legacy_rank, signal.source_id


__all__ = ["RecallSignalDeriver"]
