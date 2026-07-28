"""Canonical lifecycle coordinator for durable visible conversation turns."""

from dataclasses import replace
from typing import Iterable, List, Mapping, Optional, Union
import uuid

from erii.models.relationship import RelationshipProfile
from erii.models.relationship import utc_now
from erii.models.turn import (
    ContextSignalSource,
    DeliveryDisposition,
    InteractionContextSignal,
    ReplyAttemptRecord,
    ReplyAttemptStage,
    ReplyContinuityAssessment,
    SourceTranscript,
    SourceProcessingChannel,
    SourceProcessingOutcome,
    SourceProcessingPlan,
    SourceTurnReceipt,
    TurnMessage,
    TurnRecord,
    TurnRole,
    TurnStatus,
    TurnTerminalConflictError,
)
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage


class TurnLedger:
    """Keeps turn lifecycle rules behind one small Engine-facing interface."""

    def __init__(self, storage: BaseStorage) -> None:
        self._storage = storage

    def open(
        self,
        profile: RelationshipProfile,
        user_message: str,
        *,
        turn_id: Optional[str] = None,
        interaction_context: Iterable[
            Union[InteractionContextSignal, Mapping[str, object]]
        ] = (),
    ) -> TurnRecord:
        """Persists the visible user message before any reply is generated."""
        stable_turn_id = SecuritySanitizer.validate_key(
            turn_id or str(uuid.uuid4()),
            "turn_id",
        )
        context_signals = tuple(
            item
            if isinstance(item, InteractionContextSignal)
            else InteractionContextSignal.from_dict(item)
            for item in interaction_context
        )
        if any(
            signal.source != ContextSignalSource.HOST_OBSERVED
            for signal in context_signals
        ):
            raise ValueError(
                "begin_turn() accepts only host_observed interaction context signals"
            )
        record = TurnRecord(
            turn_id=stable_turn_id,
            relationship_id=profile.relationship_id,
            status=TurnStatus.OPEN,
            transcript=SourceTranscript(
                user_message=TurnMessage(
                    message_id=f"{stable_turn_id}:user",
                    role=TurnRole.USER,
                    content=user_message,
                )
            ),
            interaction_context=context_signals,
        )
        return self._storage.create_turn_record(record)

    def get(self, profile: RelationshipProfile, turn_id: str) -> TurnRecord:
        """Loads one turn scoped to its isolated relationship."""
        stable_turn_id = SecuritySanitizer.validate_key(turn_id, "turn_id")
        return self._storage.get_turn_record(
            profile.relationship_id,
            stable_turn_id,
        )

    def list(
        self,
        profile: RelationshipProfile,
        *,
        status: Optional[Union[TurnStatus, str]] = None,
    ) -> List[TurnRecord]:
        """Lists this relationship's turns in durable opening order."""
        records = self._storage.list_turn_records(profile.relationship_id)
        if status is None:
            return records
        selected = status if isinstance(status, TurnStatus) else TurnStatus(status)
        return [record for record in records if record.status == selected]

    def record_reply_attempt_failure(
        self,
        profile: RelationshipProfile,
        turn_id: str,
        *,
        attempt_number: int,
        stage: Union[ReplyAttemptStage, str],
        capability_descriptor: str,
        failure_classification: str,
    ) -> ReplyAttemptRecord:
        """Records sanitized failure metadata while keeping the turn open."""
        turn = self.get(profile, turn_id)
        if turn.status != TurnStatus.OPEN:
            raise TurnTerminalConflictError(
                f"turn {turn.turn_id!r} no longer accepts reply attempts"
            )
        safe_classification = SecuritySanitizer.validate_key(
            failure_classification,
            "failure_classification",
        )
        attempt_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"erii:{profile.relationship_id}:reply-attempt:"
                    f"{turn.turn_id}:{attempt_number}"
                ),
            )
        )
        attempt = ReplyAttemptRecord(
            attempt_id=attempt_id,
            relationship_id=profile.relationship_id,
            turn_id=turn.turn_id,
            attempt_number=attempt_number,
            stage=(
                stage if isinstance(stage, ReplyAttemptStage) else ReplyAttemptStage(stage)
            ),
            capability_descriptor=capability_descriptor,
            failure_classification=safe_classification,
        )
        return self._storage.append_reply_attempt(attempt)

    def list_reply_attempts(
        self,
        profile: RelationshipProfile,
        turn_id: str,
    ) -> List[ReplyAttemptRecord]:
        """Returns sanitized attempt metadata scoped through a known turn."""
        turn = self.get(profile, turn_id)
        return self._storage.list_reply_attempts(
            profile.relationship_id,
            turn.turn_id,
        )

    def complete(
        self,
        profile: RelationshipProfile,
        turn_id: str,
        agent_message: str,
        *,
        continuity_assessment: Optional[
            Union[ReplyContinuityAssessment, Mapping[str, object]]
        ] = None,
        delivery_disposition: Union[
            DeliveryDisposition,
            str,
        ] = DeliveryDisposition.SHOWN,
        processing_channels: Optional[Iterable[Union[SourceProcessingChannel, str]]] = None,
    ) -> SourceTurnReceipt:
        """Atomically seals an open turn around the reply actually shown."""
        existing = self.get(profile, turn_id)
        assessment = (
            ReplyContinuityAssessment()
            if continuity_assessment is None
            else (
                continuity_assessment
                if isinstance(continuity_assessment, ReplyContinuityAssessment)
                else ReplyContinuityAssessment.from_dict(continuity_assessment)
            )
        )
        disposition = (
            delivery_disposition
            if isinstance(delivery_disposition, DeliveryDisposition)
            else DeliveryDisposition(delivery_disposition)
        )
        channels = tuple(
            item if isinstance(item, SourceProcessingChannel) else SourceProcessingChannel(item)
            for item in (
                processing_channels
                if processing_channels is not None
                else ()
            )
        )
        if existing.status == TurnStatus.COMPLETED:
            if self._same_completion(
                existing,
                agent_message,
                assessment,
                disposition,
                channels,
            ):
                return SourceTurnReceipt.from_record(existing)
            raise TurnTerminalConflictError(
                f"turn {existing.turn_id!r} already has a different completion"
            )
        if existing.status != TurnStatus.OPEN:
            raise TurnTerminalConflictError(
                f"turn {existing.turn_id!r} cannot be completed from {existing.status.value}"
            )

        plan = SourceProcessingPlan(channels=channels)
        completed_at = TurnMessage(
            message_id=f"{existing.turn_id}:agent",
            role=TurnRole.AGENT,
            content=agent_message,
        )
        completed = replace(
            existing,
            status=TurnStatus.COMPLETED,
            transcript=SourceTranscript(
                user_message=existing.transcript.user_message,
                agent_message=completed_at,
            ),
            record_version=existing.record_version + 1,
            continuity_assessment=assessment,
            delivery_disposition=disposition,
            processing_plan=plan,
            processing_outcomes=tuple(
                SourceProcessingOutcome(
                    channel=channel,
                    updated_at=completed_at.recorded_at,
                )
                for channel in channels
            ),
            completed_at=completed_at.recorded_at,
        )
        try:
            stored = self._storage.transition_turn_record(
                completed,
                TurnStatus.OPEN,
                existing.record_version,
            )
        except TurnTerminalConflictError:
            winner = self.get(profile, turn_id)
            if winner.status == TurnStatus.COMPLETED and self._same_completion(
                winner,
                agent_message,
                assessment,
                disposition,
                channels,
            ):
                stored = winner
            else:
                raise
        return SourceTurnReceipt.from_record(stored)

    def abandon(
        self,
        profile: RelationshipProfile,
        turn_id: str,
        *,
        reason: str,
    ) -> TurnRecord:
        """Explicitly terminates an unanswered turn without creating a reply."""
        existing = self.get(profile, turn_id)
        reason_code = SecuritySanitizer.validate_key(reason, "reason")
        if existing.status == TurnStatus.ABANDONED:
            if existing.abandonment_reason == reason_code:
                return existing
            raise TurnTerminalConflictError(
                f"turn {existing.turn_id!r} already has a different abandonment"
            )
        if existing.status != TurnStatus.OPEN:
            raise TurnTerminalConflictError(
                f"turn {existing.turn_id!r} cannot be abandoned from {existing.status.value}"
            )
        abandoned = replace(
            existing,
            status=TurnStatus.ABANDONED,
            record_version=existing.record_version + 1,
            abandoned_at=utc_now(),
            abandonment_reason=reason_code,
        )
        try:
            return self._storage.transition_turn_record(
                abandoned,
                TurnStatus.OPEN,
                existing.record_version,
            )
        except TurnTerminalConflictError:
            winner = self.get(profile, turn_id)
            if (
                winner.status == TurnStatus.ABANDONED
                and winner.abandonment_reason == reason_code
            ):
                return winner
            raise

    def record(
        self,
        profile: RelationshipProfile,
        user_message: str,
        agent_message: str,
        *,
        turn_id: Optional[str] = None,
        continuity_assessment: Optional[
            Union[ReplyContinuityAssessment, Mapping[str, object]]
        ] = None,
        delivery_disposition: Union[
            DeliveryDisposition,
            str,
        ] = DeliveryDisposition.SHOWN,
        processing_channels: Optional[Iterable[Union[SourceProcessingChannel, str]]] = None,
    ) -> SourceTurnReceipt:
        """Atomically inserts an already-complete visible exchange."""
        stable_turn_id = SecuritySanitizer.validate_key(
            turn_id or str(uuid.uuid4()),
            "turn_id",
        )
        assessment = (
            ReplyContinuityAssessment()
            if continuity_assessment is None
            else (
                continuity_assessment
                if isinstance(continuity_assessment, ReplyContinuityAssessment)
                else ReplyContinuityAssessment.from_dict(continuity_assessment)
            )
        )
        disposition = (
            delivery_disposition
            if isinstance(delivery_disposition, DeliveryDisposition)
            else DeliveryDisposition(delivery_disposition)
        )
        channels = tuple(
            item if isinstance(item, SourceProcessingChannel) else SourceProcessingChannel(item)
            for item in (
                processing_channels
                if processing_channels is not None
                else ()
            )
        )
        visible_user = TurnMessage(
            message_id=f"{stable_turn_id}:user",
            role=TurnRole.USER,
            content=user_message,
        )
        visible_agent = TurnMessage(
            message_id=f"{stable_turn_id}:agent",
            role=TurnRole.AGENT,
            content=agent_message,
        )
        plan = SourceProcessingPlan(channels=channels)
        record = TurnRecord(
            turn_id=stable_turn_id,
            relationship_id=profile.relationship_id,
            status=TurnStatus.COMPLETED,
            transcript=SourceTranscript(
                user_message=visible_user,
                agent_message=visible_agent,
            ),
            opened_at=visible_user.recorded_at,
            continuity_assessment=assessment,
            delivery_disposition=disposition,
            processing_plan=plan,
            processing_outcomes=tuple(
                SourceProcessingOutcome(
                    channel=channel,
                    updated_at=visible_agent.recorded_at,
                )
                for channel in channels
            ),
            completed_at=visible_agent.recorded_at,
        )
        stored = self._storage.create_turn_record(record)
        return SourceTurnReceipt.from_record(stored)

    @staticmethod
    def _same_completion(
        record: TurnRecord,
        agent_message: str,
        assessment: ReplyContinuityAssessment,
        disposition: DeliveryDisposition,
        channels: tuple[SourceProcessingChannel, ...],
    ) -> bool:
        return (
            record.transcript.agent_message is not None
            and record.transcript.agent_message.content == agent_message
            and record.continuity_assessment == assessment
            and record.delivery_disposition == disposition
            and record.processing_plan is not None
            and record.processing_plan.channels == channels
        )
    InteractionContextSignal,
