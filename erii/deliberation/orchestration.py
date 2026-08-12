"""Explicit, offline G2 Compact orchestration over host-owned ERII APIs.

The module prepares a reply for display and finalizes it only after the host
confirms the exact text shown. It owns no storage, network client, worker, or
recall capability. Private deliberation content is discarded before the
prepared reply crosses the delivery boundary.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol, Sequence

from erii.models.continuity import ContinuityEvaluationResult
from erii.models.turn import (
    ContinuityVerdict,
    ReplyAttemptStage,
    SourceTurnReceipt,
    TurnRecord,
    TurnStatus,
)

from .contracts import CharacterActor, ProviderErrorCode
from .core_validator import TrustedAuthoritySecret
from .host_bridge import (
    DeliberationHostBridge,
    fingerprint_user_envelope,
)
from .identifiers import validate_identifier
from .schemas import (
    EvidenceViewV1,
    MessagePart,
    ResultKind,
    RouterSignal,
    UserMessageEnvelope,
)
from .strict_codec import StrictCanonicalCodec


class DeliberationMode(str, Enum):
    """G2 host modes; later routing modes remain outside this module."""

    OFF = "off"
    COMPACT = "compact"


class ReplySource(str, Enum):
    COMPACT = "compact"
    DIRECT = "direct"


class PreparationFailureCode(str, Enum):
    CONTINUITY_REJECTED = "continuity_rejected"
    CONTINUITY_EVALUATION_FAILED = "continuity_evaluation_failed"
    DIRECT_REPLY_INVALID = "direct_reply_invalid"


class DeliberationRuntime(Protocol):
    """Host-owned operations required by the removable G2 orchestrator."""

    def resolve_open_turn(self, turn_id: str) -> TurnRecord:
        ...

    def relationship_guard(
        self,
        relationship_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def evaluate_reply_continuity(
        self,
        turn_id: str,
        proposed_reply: str,
        *,
        persona_context_refs: Sequence[Any] = (),
        relationship_context_refs: Sequence[Any] = (),
        interaction_context: Sequence[Any] = (),
    ) -> ContinuityEvaluationResult:
        ...

    def complete_turn(
        self,
        turn_id: str,
        reply: str,
        continuity_result: ContinuityEvaluationResult,
    ) -> SourceTurnReceipt:
        ...

    def record_attempt_failure(
        self,
        turn_id: str,
        *,
        attempt_number: int,
        stage: str,
        capability_descriptor: str,
        failure_classification: str,
    ) -> Any:
        ...


@dataclass(frozen=True)
class PreparedVisibleReplyV1:
    """A continuity-reviewed exact reply waiting for host display."""

    relationship_id: str
    turn_id: str
    source_revision: str
    turn_record_version: int
    context_baseline_fingerprint: str
    exact_reply: str = field(repr=False)
    exact_reply_fingerprint: str
    source: ReplySource
    not_deliberated: bool
    fallback_reason: str | None
    continuity_result: ContinuityEvaluationResult = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("relationship_id", "turn_id", "source_revision"):
            validate_identifier(getattr(self, name), name)
        if type(self.turn_record_version) is not int or self.turn_record_version < 0:
            raise ValueError("turn_record_version must be a non-negative int")
        _require_fingerprint(
            self.context_baseline_fingerprint,
            "context_baseline_fingerprint",
        )
        if type(self.exact_reply) is not str or not self.exact_reply:
            raise ValueError("exact_reply must be a non-empty string")
        if len(self.exact_reply) > 10_000:
            raise ValueError("exact_reply exceeds the G2 single-part limit")
        _require_fingerprint(self.exact_reply_fingerprint, "exact_reply_fingerprint")
        if fingerprint_visible_reply(self.exact_reply) != self.exact_reply_fingerprint:
            raise ValueError("exact_reply_fingerprint does not match exact_reply")
        if not isinstance(self.source, ReplySource):
            object.__setattr__(self, "source", ReplySource(self.source))
        if type(self.not_deliberated) is not bool:
            raise TypeError("not_deliberated must be bool")
        if self.not_deliberated is not (self.source is ReplySource.DIRECT):
            raise ValueError("only direct replies may be marked not_deliberated")
        if self.source is ReplySource.DIRECT and not self.fallback_reason:
            raise ValueError("direct reply requires a stable fallback_reason")
        if self.source is ReplySource.COMPACT and self.fallback_reason is not None:
            raise ValueError("compact reply cannot claim a fallback_reason")


@dataclass(frozen=True)
class ReplyPreparationOutcomeV1:
    reply: PreparedVisibleReplyV1 | None = None
    failure_code: PreparationFailureCode | None = None

    def __post_init__(self) -> None:
        if (self.reply is None) == (self.failure_code is None):
            raise ValueError("outcome requires exactly one reply or failure_code")

    @property
    def ready(self) -> bool:
        return self.reply is not None


class EngineDeliberationRuntime:
    """Narrow adapter over one relationship-scoped ``ERIIEngine`` instance."""

    def __init__(self, *, engine: Any, agent_id: str, user_id: str) -> None:
        self._engine = engine
        self._agent_id = validate_identifier(agent_id, "agent_id")
        self._user_id = validate_identifier(user_id, "user_id")

    def resolve_open_turn(self, turn_id: str) -> TurnRecord:
        return self._engine.get_turn(self._agent_id, self._user_id, turn_id)

    def relationship_guard(self, relationship_id: str) -> AbstractContextManager[Any]:
        return self._engine.storage.relationship_processing_guard(relationship_id)

    def evaluate_reply_continuity(
        self,
        turn_id: str,
        proposed_reply: str,
        *,
        persona_context_refs: Sequence[Any] = (),
        relationship_context_refs: Sequence[Any] = (),
        interaction_context: Sequence[Any] = (),
    ) -> ContinuityEvaluationResult:
        return self._engine.evaluate_reply_continuity(
            self._agent_id,
            self._user_id,
            turn_id,
            proposed_reply,
            persona_context_refs=persona_context_refs,
            relationship_context_refs=relationship_context_refs,
            interaction_context=interaction_context,
        )

    def complete_turn(
        self,
        turn_id: str,
        reply: str,
        continuity_result: ContinuityEvaluationResult,
    ) -> SourceTurnReceipt:
        return self._engine.complete_turn(
            self._agent_id,
            self._user_id,
            turn_id,
            reply,
            continuity_result=continuity_result,
        )

    def record_attempt_failure(
        self,
        turn_id: str,
        *,
        attempt_number: int,
        stage: str,
        capability_descriptor: str,
        failure_classification: str,
    ) -> Any:
        return self._engine.record_reply_attempt_failure(
            self._agent_id,
            self._user_id,
            turn_id,
            attempt_number=attempt_number,
            stage=stage,
            capability_descriptor=capability_descriptor,
            failure_classification=failure_classification,
        )


class CompactDeliberationOrchestrator:
    """Prepare and finalize one explicit G2 reply without hidden side effects."""

    _ACCEPTED_VERDICTS = frozenset(
        {ContinuityVerdict.ALIGNED, ContinuityVerdict.SUPPORTED_NEW_CHOICE}
    )

    def __init__(
        self,
        *,
        runtime: DeliberationRuntime,
        secret: TrustedAuthoritySecret,
    ) -> None:
        self._runtime = runtime
        self._bridge = DeliberationHostBridge(resolver=runtime, secret=secret)

    def prepare_reply(
        self,
        *,
        turn_id: str,
        user_envelope: UserMessageEnvelope,
        evidence_view: EvidenceViewV1,
        actor: CharacterActor,
        mode: DeliberationMode,
        direct_reply: Callable[[TurnRecord], str],
        timeout: float,
        run_epoch: int,
        idempotency_key: str,
        attempt_number: int,
        persona_context_refs: Sequence[Any] = (),
        relationship_context_refs: Sequence[Any] = (),
        interaction_context: Sequence[Any] = (),
    ) -> ReplyPreparationOutcomeV1:
        validate_identifier(turn_id, "turn_id")
        if not isinstance(mode, DeliberationMode):
            mode = DeliberationMode(mode)
        if type(timeout) not in {int, float} or timeout <= 0:
            raise ValueError("timeout must be positive")
        if type(attempt_number) is not int or attempt_number < 1:
            raise ValueError("attempt_number must be a positive int")

        turn = self._runtime.resolve_open_turn(turn_id)
        if not isinstance(turn, TurnRecord) or turn.status is not TurnStatus.OPEN:
            raise ValueError("private transient deliberation requires an open Turn")
        capability = _capability_descriptor(actor)
        failure_recorded = False

        with self._runtime.relationship_guard(turn.relationship_id):
            if mode is DeliberationMode.OFF:
                return self._prepare_direct(
                    turn=turn,
                    direct_reply=direct_reply,
                    fallback_reason="deliberation_disabled",
                    attempt_number=attempt_number,
                    failure_already_recorded=False,
                    persona_context_refs=persona_context_refs,
                    relationship_context_refs=relationship_context_refs,
                    interaction_context=interaction_context,
                )

            prepared = self._bridge.prepare_compact(
                turn_id=turn_id,
                user_envelope=user_envelope,
                evidence_view=evidence_view,
                actor_descriptor=actor.descriptor,
                router_policy={
                    "mode": mode.value,
                    "version": "erii-deliberation-g2/v1",
                },
                run_epoch=run_epoch,
                idempotency_key=idempotency_key,
            )
            provider_result = self._bridge.execute_compact(
                prepared=prepared,
                actor=actor,
                timeout=float(timeout),
            )
            fallback_reason: str | None = None
            if not provider_result.success or provider_result.data is None:
                fallback_reason = _provider_failure_code(provider_result.error_code)
            else:
                decision = provider_result.data.decision
                if decision.result_kind is ResultKind.ABSTAIN:
                    fallback_reason = "deliberation_abstained"
                elif (
                    decision.result_kind is ResultKind.NEEDS_STAGED_DELIBERATION
                    or decision.router_signal is RouterSignal.NEEDS_STAGED_DELIBERATION
                ):
                    fallback_reason = "deliberation_requires_staged"
                elif len(decision.reply_candidate.parts) != 1:
                    fallback_reason = "reply_envelope_unsupported"
                else:
                    compact_reply = decision.reply_candidate.parts[0].exact_utf8
                    review = self._review(
                        turn_id,
                        compact_reply,
                        persona_context_refs=persona_context_refs,
                        relationship_context_refs=relationship_context_refs,
                        interaction_context=interaction_context,
                    )
                    if review is not None and self._accepted(review):
                        return self._ready(
                            turn,
                            compact_reply,
                            ReplySource.COMPACT,
                            None,
                            review,
                        )
                    fallback_reason = _continuity_failure_code(review)

            failure_recorded = self._record_failure(
                turn_id=turn_id,
                attempt_number=attempt_number,
                stage=(
                    ReplyAttemptStage.CONTINUITY_EVALUATION
                    if fallback_reason.startswith("continuity_")
                    else ReplyAttemptStage.GENERATION
                ),
                capability_descriptor=capability,
                failure_classification=fallback_reason,
            )
            return self._prepare_direct(
                turn=turn,
                direct_reply=direct_reply,
                fallback_reason=fallback_reason,
                attempt_number=attempt_number,
                failure_already_recorded=failure_recorded,
                persona_context_refs=persona_context_refs,
                relationship_context_refs=relationship_context_refs,
                interaction_context=interaction_context,
            )

    def finalize_shown(
        self,
        prepared: PreparedVisibleReplyV1,
        *,
        shown_reply: str,
    ) -> SourceTurnReceipt:
        if type(shown_reply) is not str or shown_reply != prepared.exact_reply:
            raise ValueError("shown_reply must equal the exact prepared reply")
        if fingerprint_visible_reply(shown_reply) != prepared.exact_reply_fingerprint:
            raise ValueError("shown_reply fingerprint does not match the prepared reply")
        with self._runtime.relationship_guard(prepared.relationship_id):
            current = self._runtime.resolve_open_turn(prepared.turn_id)
            if not _matches_prepared_turn(prepared, current):
                raise ValueError("source Turn authority is stale")
            prepared.continuity_result.review_binding.verify_reply(shown_reply)
            return self._runtime.complete_turn(
                prepared.turn_id,
                shown_reply,
                prepared.continuity_result,
            )

    def _prepare_direct(
        self,
        *,
        turn: TurnRecord,
        direct_reply: Callable[[TurnRecord], str],
        fallback_reason: str,
        attempt_number: int,
        failure_already_recorded: bool,
        persona_context_refs: Sequence[Any],
        relationship_context_refs: Sequence[Any],
        interaction_context: Sequence[Any],
    ) -> ReplyPreparationOutcomeV1:
        direct_attempt_number = attempt_number + int(failure_already_recorded)
        try:
            reply = direct_reply(turn)
        except Exception:
            self._record_failure(
                turn_id=turn.turn_id,
                attempt_number=direct_attempt_number,
                stage=ReplyAttemptStage.GENERATION,
                capability_descriptor="deliberation_direct_fallback",
                failure_classification="direct_generation_failed",
            )
            return ReplyPreparationOutcomeV1(
                failure_code=PreparationFailureCode.DIRECT_REPLY_INVALID
            )
        if type(reply) is not str or not reply or len(reply) > 10_000:
            self._record_failure(
                turn_id=turn.turn_id,
                attempt_number=direct_attempt_number,
                stage=ReplyAttemptStage.GENERATION,
                capability_descriptor="deliberation_direct_fallback",
                failure_classification="direct_reply_invalid",
            )
            return ReplyPreparationOutcomeV1(
                failure_code=PreparationFailureCode.DIRECT_REPLY_INVALID
            )
        review = self._review(
            turn.turn_id,
            reply,
            persona_context_refs=persona_context_refs,
            relationship_context_refs=relationship_context_refs,
            interaction_context=interaction_context,
        )
        if review is None:
            self._record_failure(
                turn_id=turn.turn_id,
                attempt_number=direct_attempt_number,
                stage=ReplyAttemptStage.CONTINUITY_EVALUATION,
                capability_descriptor="deliberation_direct_fallback",
                failure_classification="continuity_evaluation_failed",
            )
            return ReplyPreparationOutcomeV1(
                failure_code=PreparationFailureCode.CONTINUITY_EVALUATION_FAILED
            )
        if not self._accepted(review):
            self._record_failure(
                turn_id=turn.turn_id,
                attempt_number=direct_attempt_number,
                stage=ReplyAttemptStage.CONTINUITY_EVALUATION,
                capability_descriptor="deliberation_direct_fallback",
                failure_classification=_continuity_failure_code(review),
            )
            return ReplyPreparationOutcomeV1(
                failure_code=PreparationFailureCode.CONTINUITY_REJECTED
            )
        return self._ready(turn, reply, ReplySource.DIRECT, fallback_reason, review)

    def _review(
        self,
        turn_id: str,
        reply: str,
        *,
        persona_context_refs: Sequence[Any],
        relationship_context_refs: Sequence[Any],
        interaction_context: Sequence[Any],
    ) -> ContinuityEvaluationResult | None:
        try:
            return self._runtime.evaluate_reply_continuity(
                turn_id,
                reply,
                persona_context_refs=persona_context_refs,
                relationship_context_refs=relationship_context_refs,
                interaction_context=interaction_context,
            )
        except Exception:
            return None

    @classmethod
    def _accepted(cls, review: ContinuityEvaluationResult) -> bool:
        return review.assessment.verdict in cls._ACCEPTED_VERDICTS

    @staticmethod
    def _ready(
        turn: TurnRecord,
        reply: str,
        source: ReplySource,
        fallback_reason: str | None,
        review: ContinuityEvaluationResult,
    ) -> ReplyPreparationOutcomeV1:
        baseline = turn.context_baseline
        if baseline is None:
            raise ValueError("open Turn is missing its frozen context baseline")
        return ReplyPreparationOutcomeV1(
            reply=PreparedVisibleReplyV1(
                relationship_id=turn.relationship_id,
                turn_id=turn.turn_id,
                source_revision=turn.source_revision,
                turn_record_version=turn.record_version,
                context_baseline_fingerprint=baseline.baseline_fingerprint,
                exact_reply=reply,
                exact_reply_fingerprint=fingerprint_visible_reply(reply),
                source=source,
                not_deliberated=source is ReplySource.DIRECT,
                fallback_reason=fallback_reason,
                continuity_result=review,
            )
        )

    def _record_failure(
        self,
        *,
        turn_id: str,
        attempt_number: int,
        stage: ReplyAttemptStage,
        capability_descriptor: str,
        failure_classification: str,
    ) -> bool:
        self._runtime.record_attempt_failure(
            turn_id,
            attempt_number=attempt_number,
            stage=stage.value,
            capability_descriptor=capability_descriptor,
            failure_classification=failure_classification,
        )
        return True


def build_user_envelope(turn: TurnRecord) -> UserMessageEnvelope:
    """Build the exact single-part G2 user envelope from an open Turn."""
    if not isinstance(turn, TurnRecord) or turn.status is not TurnStatus.OPEN:
        raise ValueError("user envelope requires an open TurnRecord")
    user_message = turn.transcript.user_message
    provisional = UserMessageEnvelope(
        parts=(
            MessagePart(
                part_id=user_message.message_id,
                kind="text",
                exact_utf8=user_message.content,
            ),
        ),
        canonical_fingerprint="0" * 64,
    )
    return provisional.model_copy(
        update={"canonical_fingerprint": fingerprint_user_envelope(provisional)}
    )


def fingerprint_visible_reply(reply: str) -> str:
    return StrictCanonicalCodec.fingerprint(
        {"exact_reply": reply},
        domain="erii-deliberation-g2-visible-reply/v1",
    )


def _capability_descriptor(actor: CharacterActor) -> str:
    fingerprint = StrictCanonicalCodec.fingerprint(
        asdict(actor.descriptor),
        domain="erii-deliberation-actor-descriptor/v1",
    )
    return f"deliberation_actor_{fingerprint[:24]}"


def _provider_failure_code(code: ProviderErrorCode | None) -> str:
    return (
        ProviderErrorCode.OUTPUT_SCHEMA_INVALID.value
        if code is None
        else code.value
    )


def _continuity_failure_code(review: ContinuityEvaluationResult | None) -> str:
    if review is None:
        return "continuity_evaluation_failed"
    verdict = review.assessment.verdict
    if verdict is None:
        return "continuity_evaluation_failed"
    return f"continuity_{verdict.value}"


def _matches_prepared_turn(
    prepared: PreparedVisibleReplyV1,
    current: TurnRecord,
) -> bool:
    return bool(
        isinstance(current, TurnRecord)
        and current.status is TurnStatus.OPEN
        and current.relationship_id == prepared.relationship_id
        and current.turn_id == prepared.turn_id
        and current.source_revision == prepared.source_revision
        and current.record_version == prepared.turn_record_version
        and current.context_baseline is not None
        and current.context_baseline.baseline_fingerprint
        == prepared.context_baseline_fingerprint
    )


def _require_fingerprint(value: str, field_name: str) -> None:
    if type(value) is not str or len(value) != 64:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 fingerprint")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 fingerprint")


__all__ = [
    "CompactDeliberationOrchestrator",
    "DeliberationMode",
    "DeliberationRuntime",
    "EngineDeliberationRuntime",
    "PreparationFailureCode",
    "PreparedVisibleReplyV1",
    "ReplyPreparationOutcomeV1",
    "ReplySource",
    "build_user_envelope",
    "fingerprint_visible_reply",
]
