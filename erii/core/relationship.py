"""Deterministic projection of append-only relationship events."""

from datetime import datetime, timezone
from typing import Dict, Optional, Sequence

from erii.models.relationship import (
    BeliefOperation,
    CurrentBelief,
    RELATIONSHIP_DIMENSIONS,
    RelationshipEvent,
    RelationshipProfile,
    RelationshipSnapshot,
    RelationshipState,
    StateReason,
    TemporalContext,
)


class RelationshipProjector:
    """Rebuilds current state and beliefs from accepted historical events."""

    INITIAL_STATE = RelationshipState()

    @classmethod
    def project(
        cls,
        profile: RelationshipProfile,
        events: Sequence[RelationshipEvent],
        observed_at: Optional[str] = None,
    ) -> RelationshipSnapshot:
        """Projects a relationship snapshot in event storage order."""
        state_values = cls.INITIAL_STATE.to_dict()
        beliefs: Dict[str, CurrentBelief] = {}
        reasons: Dict[str, StateReason] = {}

        for event in events:
            if event.relationship_id != profile.relationship_id:
                raise ValueError("relationship event does not belong to the projected profile")

            for dimension, delta in event.state_delta.items():
                state_values[dimension] = min(
                    1.0,
                    max(0.0, state_values[dimension] + delta),
                )
                reasons[dimension] = StateReason(
                    dimension=dimension,
                    delta=delta,
                    evidence_event_id=event.event_id,
                    explanation=event.content,
                    updated_at=event.recorded_at,
                )

            for update in event.belief_updates:
                if update.operation == BeliefOperation.RETRACT:
                    beliefs.pop(update.key, None)
                    continue
                beliefs[update.key] = CurrentBelief(
                    key=update.key,
                    value=update.value,
                    confidence=update.confidence,
                    evidence_event_id=event.event_id,
                    updated_at=event.recorded_at,
                )

        state = RelationshipState(
            **{dimension: state_values[dimension] for dimension in RELATIONSHIP_DIMENSIONS}
        )
        temporal_context = None
        if observed_at is not None:
            observed = cls._parse_timestamp(observed_at, "observed_at")
            last_recorded_at = events[-1].recorded_at if events else None
            elapsed_seconds = None
            if last_recorded_at is not None:
                last_recorded = cls._parse_timestamp(last_recorded_at, "event recorded_at")
                elapsed_seconds = max(0.0, (observed - last_recorded).total_seconds())
            temporal_context = TemporalContext(
                observed_at=observed_at,
                last_event_recorded_at=last_recorded_at,
                elapsed_seconds=elapsed_seconds,
            )

        return RelationshipSnapshot(
            profile=profile,
            state=state,
            beliefs=beliefs,
            state_reasons=reasons,
            event_count=len(events),
            last_event_id=events[-1].event_id if events else None,
            temporal_context=temporal_context,
        )

    @staticmethod
    def _parse_timestamp(value: str, field_name: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
