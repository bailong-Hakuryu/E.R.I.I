"""Derived evidence authority for persisted Source Turns."""

from __future__ import annotations

from typing import FrozenSet

from erii.models.turn import DeliveryDisposition, TurnRecord


_EXCEPTIONAL_DISPOSITIONS = frozenset(
    {
        DeliveryDisposition.OVERRIDDEN,
        DeliveryDisposition.SHOWN_UNREVIEWED,
    }
)


def has_exceptional_delivery(turn: TurnRecord) -> bool:
    """Returns whether a visible Agent reply crossed the delivery gate exceptionally."""
    return turn.delivery_disposition in _EXCEPTIONAL_DISPOSITIONS


def quarantined_agent_source_ids(turn: TurnRecord) -> FrozenSet[str]:
    """Returns Agent message IDs denied automatic derived-write authority."""
    if not has_exceptional_delivery(turn):
        return frozenset()
    message = turn.transcript.agent_message
    if message is None:
        return frozenset()
    return frozenset({message.message_id})


__all__ = ["has_exceptional_delivery", "quarantined_agent_source_ids"]
