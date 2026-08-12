"""Offline G2 orchestration over Compact deliberation and ERII continuity."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from erii.deliberation.contracts import ProviderErrorCode, ProviderResult
from erii.deliberation.core_validator import TrustedAuthoritySecret
from erii.deliberation.fake_actor import FakeActor, FakeActorConfig, create_abstain_decision
from erii.deliberation.host_bridge import fingerprint_evidence_view
from erii.deliberation.orchestration import (
    CompactDeliberationOrchestrator,
    DeliberationMode,
    EngineDeliberationRuntime,
    PreparationFailureCode,
    ReplySource,
    build_user_envelope,
)
from erii.deliberation.schemas import EvidenceViewV1, MessagePart
from erii.engine import ERIIEngine
from erii.models.continuity import (
    ContinuityAxis,
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
    ContinuityEvaluatorDescriptor,
)
from erii.models.turn import (
    ContinuityVerdict,
    SourceTranscript,
    TurnMessage,
    TurnRecord,
    TurnRole,
    TurnStatus,
)
from erii.models.turn_context import (
    TurnBlueprintReference,
    TurnContextBaseline,
    TurnPremiseReference,
)


def _open_turn() -> TurnRecord:
    baseline = TurnContextBaseline.create(
        relationship_id="relationship-1",
        turn_id="turn-1",
        persona_id="persona-1",
        blueprint=TurnBlueprintReference(
            blueprint_id="blueprint-1",
            revision=1,
            source_sha256="a" * 64,
        ),
        manifest=None,
        approved_growth_refs=(),
        premise=TurnPremiseReference(
            premise_id="premise-1",
            content_fingerprint="b" * 64,
        ),
        direct_event_count=0,
        adjudication_count=0,
        history_prefix_fingerprint="c" * 64,
        policy_versions={
            "relationship_baseline_policy": "v1",
            "relationship_history_projection": "v1",
            "relationship_safety_policy": "v1",
            "interaction_context_policy": "v1",
            "voice_matcher_policy": "v1",
        },
    )
    return TurnRecord(
        turn_id="turn-1",
        relationship_id="relationship-1",
        status=TurnStatus.OPEN,
        transcript=SourceTranscript(
            user_message=TurnMessage(
                message_id="user-message-1",
                role=TurnRole.USER,
                content="Stay with me for a moment.",
            )
        ),
        context_baseline=baseline,
    )


def _evidence_view(turn: TurnRecord) -> EvidenceViewV1:
    provisional = EvidenceViewV1(
        view_id="view-1",
        relationship_id=turn.relationship_id,
        turn_id=turn.turn_id,
        items=(),
        allowed_claim_kinds=(),
        view_fingerprint="0" * 64,
    )
    return provisional.model_copy(
        update={"view_fingerprint": fingerprint_evidence_view(provisional)}
    )


@dataclass
class FakeRuntime:
    turn: TurnRecord
    verdicts: list[ContinuityVerdict]

    def __post_init__(self) -> None:
        self.evaluated_replies: list[str] = []
        self.completed_replies: list[str] = []
        self.failures: list[tuple[int, str, str]] = []

    def resolve_open_turn(self, turn_id: str) -> TurnRecord:
        assert turn_id == self.turn.turn_id
        return self.turn

    def relationship_guard(self, relationship_id: str):
        assert relationship_id == self.turn.relationship_id
        return nullcontext()

    def evaluate_reply_continuity(
        self,
        turn_id: str,
        proposed_reply: str,
        *,
        persona_context_refs=(),
        relationship_context_refs=(),
        interaction_context=(),
    ):
        del persona_context_refs, relationship_context_refs, interaction_context
        assert turn_id == self.turn.turn_id
        self.evaluated_replies.append(proposed_reply)
        verdict = self.verdicts.pop(0)
        return SimpleNamespace(
            assessment=SimpleNamespace(verdict=verdict),
            review_binding=SimpleNamespace(
                verify_reply=lambda reply: (
                    None
                    if reply == proposed_reply
                    else (_ for _ in ()).throw(ValueError("reply changed"))
                )
            ),
        )

    def complete_turn(self, turn_id: str, reply: str, continuity_result):
        continuity_result.review_binding.verify_reply(reply)
        if self.turn.status is not TurnStatus.OPEN:
            raise ValueError("turn is no longer open")
        self.completed_replies.append(reply)
        return SimpleNamespace(turn_id=turn_id)

    def record_attempt_failure(
        self,
        turn_id: str,
        *,
        attempt_number: int,
        stage: str,
        capability_descriptor: str,
        failure_classification: str,
    ) -> None:
        assert turn_id == self.turn.turn_id
        assert capability_descriptor.startswith("deliberation_")
        self.failures.append((attempt_number, stage, failure_classification))


def _orchestrator(runtime: FakeRuntime) -> CompactDeliberationOrchestrator:
    return CompactDeliberationOrchestrator(
        runtime=runtime,
        secret=TrustedAuthoritySecret(b"k" * 32),
    )


def _prepare(
    runtime: FakeRuntime,
    *,
    actor: FakeActor | None = None,
    mode: DeliberationMode = DeliberationMode.COMPACT,
    direct_reply="Direct fallback.",
):
    turn = runtime.turn
    direct_callback = (
        direct_reply if callable(direct_reply) else lambda _turn: direct_reply
    )
    return _orchestrator(runtime).prepare_reply(
        turn_id=turn.turn_id,
        user_envelope=build_user_envelope(turn),
        evidence_view=_evidence_view(turn),
        actor=actor or FakeActor(),
        mode=mode,
        direct_reply=direct_callback,
        timeout=5.0,
        run_epoch=1,
        idempotency_key="attempt-1",
        attempt_number=1,
    )


def test_compact_reply_is_reviewed_and_only_completed_after_exact_display() -> None:
    runtime = FakeRuntime(_open_turn(), [ContinuityVerdict.ALIGNED])

    outcome = _prepare(runtime)

    assert outcome.ready
    assert outcome.reply is not None
    assert outcome.reply.source is ReplySource.COMPACT
    assert not outcome.reply.not_deliberated
    assert runtime.completed_replies == []
    assert "Fake Actor" not in repr(outcome.reply)

    receipt = _orchestrator(runtime).finalize_shown(
        outcome.reply,
        shown_reply=outcome.reply.exact_reply,
    )
    assert receipt.turn_id == "turn-1"
    assert runtime.completed_replies == [outcome.reply.exact_reply]


def test_provider_failure_records_sanitized_attempt_and_uses_direct_fallback() -> None:
    runtime = FakeRuntime(_open_turn(), [ContinuityVerdict.ALIGNED])
    actor = FakeActor(FakeActorConfig(inject_error=ProviderErrorCode.TIMEOUT))

    outcome = _prepare(runtime, actor=actor)

    assert outcome.ready
    assert outcome.reply is not None
    assert outcome.reply.source is ReplySource.DIRECT
    assert outcome.reply.not_deliberated
    assert outcome.reply.fallback_reason == "provider_timeout"
    assert runtime.failures == [(1, "generation", "provider_timeout")]


def test_actor_exception_is_sanitized_before_direct_fallback() -> None:
    runtime = FakeRuntime(_open_turn(), [ContinuityVerdict.ALIGNED])
    actor = FakeActor(
        FakeActorConfig(
            response_factory=lambda _request: (_ for _ in ()).throw(
                RuntimeError("provider body must not escape")
            )
        )
    )

    outcome = _prepare(runtime, actor=actor)

    assert outcome.ready
    assert outcome.reply is not None
    assert outcome.reply.source is ReplySource.DIRECT
    assert outcome.reply.fallback_reason == "provider_unavailable"
    assert "provider body" not in repr(outcome)
    assert runtime.failures == [(1, "generation", "provider_unavailable")]


class _WrongDataActor:
    descriptor = FakeActor().descriptor

    def compact(self, request, *, timeout):
        del request, timeout
        return ProviderResult(success=True, data="not-a-compact-decision")


def test_wrong_provider_data_type_falls_back_as_invalid_schema() -> None:
    runtime = FakeRuntime(_open_turn(), [ContinuityVerdict.ALIGNED])

    outcome = _prepare(runtime, actor=_WrongDataActor())

    assert outcome.ready
    assert outcome.reply is not None
    assert outcome.reply.fallback_reason == "provider_output_schema_invalid"
    assert runtime.failures == [
        (1, "generation", "provider_output_schema_invalid")
    ]


@pytest.mark.parametrize(
    ("actor", "reason"),
    [
        (
            FakeActor(FakeActorConfig(response_factory=lambda _request: create_abstain_decision())),
            "deliberation_abstained",
        ),
        (
            FakeActor(
                FakeActorConfig(
                    response_factory=lambda request: FakeActor().compact(
                        request, timeout=5.0
                    ).data.model_copy(
                        update={
                            "reply_candidate": FakeActor().compact(
                                request, timeout=5.0
                            ).data.reply_candidate.model_copy(
                                update={
                                    "parts": (
                                        MessagePart(part_id="part-1", exact_utf8="One"),
                                        MessagePart(part_id="part-2", exact_utf8="Two"),
                                    )
                                }
                            )
                        }
                    )
                )
            ),
            "reply_envelope_unsupported",
        ),
    ],
)
def test_non_deliverable_compact_candidate_uses_direct_fallback(
    actor: FakeActor,
    reason: str,
) -> None:
    runtime = FakeRuntime(_open_turn(), [ContinuityVerdict.ALIGNED])

    outcome = _prepare(runtime, actor=actor)

    assert outcome.ready
    assert outcome.reply is not None
    assert outcome.reply.source is ReplySource.DIRECT
    assert outcome.reply.fallback_reason == reason


def test_compact_continuity_rejection_falls_back_and_reviews_direct_reply() -> None:
    runtime = FakeRuntime(
        _open_turn(),
        [ContinuityVerdict.UNSUPPORTED_DRIFT, ContinuityVerdict.ALIGNED],
    )

    outcome = _prepare(runtime)

    assert outcome.ready
    assert outcome.reply is not None
    assert outcome.reply.source is ReplySource.DIRECT
    assert outcome.reply.fallback_reason == "continuity_unsupported_drift"
    assert runtime.evaluated_replies[-1] == "Direct fallback."
    assert runtime.failures == [
        (1, "continuity_evaluation", "continuity_unsupported_drift")
    ]


def test_turn_stays_open_when_compact_and_direct_reply_fail_continuity() -> None:
    runtime = FakeRuntime(
        _open_turn(),
        [ContinuityVerdict.REVIEW_REQUIRED, ContinuityVerdict.UNSUPPORTED_DRIFT],
    )

    outcome = _prepare(runtime)

    assert not outcome.ready
    assert outcome.reply is None
    assert outcome.failure_code is PreparationFailureCode.CONTINUITY_REJECTED
    assert runtime.turn.status is TurnStatus.OPEN
    assert runtime.completed_replies == []
    assert runtime.failures == [
        (1, "continuity_evaluation", "continuity_review_required"),
        (2, "continuity_evaluation", "continuity_unsupported_drift"),
    ]


def test_provider_and_direct_generation_failures_have_separate_attempts() -> None:
    runtime = FakeRuntime(_open_turn(), [])
    actor = FakeActor(FakeActorConfig(inject_error=ProviderErrorCode.TIMEOUT))

    outcome = _prepare(
        runtime,
        actor=actor,
        direct_reply=lambda _turn: (_ for _ in ()).throw(
            RuntimeError("direct body must not escape")
        ),
    )

    assert not outcome.ready
    assert outcome.failure_code is PreparationFailureCode.DIRECT_REPLY_INVALID
    assert runtime.failures == [
        (1, "generation", "provider_timeout"),
        (2, "generation", "direct_generation_failed"),
    ]
    assert "direct body" not in repr(outcome)


def test_finalize_rejects_changed_or_stale_visible_reply() -> None:
    runtime = FakeRuntime(_open_turn(), [ContinuityVerdict.ALIGNED])
    outcome = _prepare(runtime)
    assert outcome.reply is not None

    with pytest.raises(ValueError, match="exact prepared reply"):
        _orchestrator(runtime).finalize_shown(outcome.reply, shown_reply="Changed")
    assert runtime.turn.status is TurnStatus.OPEN

    runtime.turn = replace(runtime.turn, record_version=2)
    with pytest.raises(ValueError, match="source Turn authority is stale"):
        _orchestrator(runtime).finalize_shown(
            outcome.reply,
            shown_reply=outcome.reply.exact_reply,
        )


def test_off_mode_skips_actor_and_marks_reply_not_deliberated() -> None:
    runtime = FakeRuntime(_open_turn(), [ContinuityVerdict.ALIGNED])
    actor = FakeActor(
        FakeActorConfig(response_factory=lambda _request: (_ for _ in ()).throw(AssertionError))
    )

    outcome = _prepare(runtime, actor=actor, mode=DeliberationMode.OFF)

    assert outcome.ready
    assert outcome.reply is not None
    assert outcome.reply.source is ReplySource.DIRECT
    assert outcome.reply.not_deliberated
    assert outcome.reply.fallback_reason == "deliberation_disabled"


class _AlignedEvaluator:
    descriptor = ContinuityEvaluatorDescriptor(
        evaluator_id="tests.g2-aligned",
        evaluator_version="1",
        evaluation_schema_version="1",
    )

    def evaluate(self, request):
        return {
            "kind": "findings",
            "findings": [
                {
                    "finding_id": f"g2-{axis.value}",
                    "axis": axis.value,
                    "assessment": "aligned",
                    "severity": "info",
                    "reason_code": "aligned",
                    "reply_start": 0,
                    "reply_end": len(request.proposed_reply),
                    "reply_quote": request.proposed_reply,
                    "supporting_basis_refs": [request.persona_context_refs[0].ref_id],
                    "conflicting_source_refs": [],
                }
                for axis in ContinuityAxis
            ],
        }


def _persona_candidate() -> dict:
    return {
        "schema_version": "0.4.0a7",
        "compiler_version": "tests.g2-persona-compiler/1",
        "source_spans": [
            {"span_id": "span-1", "start": 0, "end": 12, "quote": "Playful line"}
        ],
        "claims": [
            {
                "claim_id": "voice-1",
                "kind": "voice",
                "statement": "She answers clearly.",
                "activation_tier": "foundation",
                "basis": "explicit",
                "scope": "character",
                "source_span_ids": ["span-1"],
            }
        ],
        "contextual_voice_patterns": [],
    }


def test_engine_runtime_completes_real_turn_with_reviewed_compact_reply(tmp_path) -> None:
    with ERIIEngine(
        storage_dir=str(tmp_path),
        continuity_evaluator=_AlignedEvaluator(),
    ) as engine:
        engine.initialize_relationship("agent-1", "user-1", "Playful line")
        proposal = engine.propose_persona_compilation(
            "agent-1", "user-1", _persona_candidate()
        )
        engine.decide_persona_compilation(
            "agent-1",
            "user-1",
            proposal.proposal_id,
            proposal.revision,
            "owner",
            "approve",
        )
        manifest = engine.get_persona_manifest("agent-1", "user-1")
        assert manifest is not None
        persona_ref = ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.PERSONA_CLAIM,
            {
                "manifest_id": manifest.manifest_id,
                "content_fingerprint": manifest.content_fingerprint,
                "claim_id": "voice-1",
            },
        )
        turn = engine.begin_turn(
            "agent-1",
            "user-1",
            "Stay with me for a moment.",
            turn_id="turn-g2",
        )
        runtime = EngineDeliberationRuntime(
            engine=engine,
            agent_id="agent-1",
            user_id="user-1",
        )
        orchestrator = CompactDeliberationOrchestrator(
            runtime=runtime,
            secret=TrustedAuthoritySecret(b"z" * 32),
        )
        outcome = orchestrator.prepare_reply(
            turn_id=turn.turn_id,
            user_envelope=build_user_envelope(turn),
            evidence_view=_evidence_view(turn),
            actor=FakeActor(),
            mode=DeliberationMode.COMPACT,
            direct_reply=lambda _turn: "Direct fallback.",
            timeout=5.0,
            run_epoch=1,
            idempotency_key="attempt-g2",
            attempt_number=1,
            persona_context_refs=(persona_ref,),
        )
        assert outcome.ready
        assert outcome.reply is not None

        orchestrator.finalize_shown(
            outcome.reply,
            shown_reply=outcome.reply.exact_reply,
        )

        completed = engine.get_turn("agent-1", "user-1", turn.turn_id)
        assert completed.status is TurnStatus.COMPLETED
        assert completed.transcript.agent_message is not None
        assert completed.transcript.agent_message.content == outcome.reply.exact_reply
