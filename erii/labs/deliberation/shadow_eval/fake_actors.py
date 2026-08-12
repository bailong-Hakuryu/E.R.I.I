"""Deterministic offline actors for validating D0-D4 harness mechanics."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from erii.deliberation.contracts import ProviderUsage
from erii.deliberation.schemas import (
    AwarenessLevel,
    BehavioralIntent,
    CharacterInteriorSceneV1,
    CommunicationStrategy,
    CompactDecisionV1,
    DeliberationPlanV1,
    DeliberationSemanticFrameV1,
    DisclosureLevel,
    ExpressionRelation,
    InterpersonalPosture,
    MessagePart,
    NarrativeBudget,
    Perspective,
    ReplyRealizationV1,
    ResultKind,
    RouterSignal,
    SelfInterpretation,
    VisibleReplyEnvelopeV1,
    VoiceMode,
)
from erii.deliberation.strict_codec import StrictCanonicalCodec

from .contracts import RouteTaken, ShadowEvaluationInputV1


@dataclass(frozen=True)
class ShadowActorExecution:
    """Physical fake calls and validated artifacts produced for one run."""

    success: bool
    route_taken: RouteTaken
    reply_envelope: VisibleReplyEnvelopeV1 | None
    compact_decision: CompactDecisionV1 | None = None
    plan: DeliberationPlanV1 | None = None
    realization: ReplyRealizationV1 | None = None
    usage: ProviderUsage | None = None
    attempt_count: int = 0
    escalation_occurred: bool = False


class DeterministicShadowActor:
    """Execute mechanically distinct deterministic fixtures for D0-D4."""

    def execute(self, shadow_input: ShadowEvaluationInputV1) -> ShadowActorExecution:
        label = shadow_input.config.config_label
        key = self._sample_key(shadow_input)
        if label == "D0":
            reply = self._reply(shadow_input, key, "direct")
            return ShadowActorExecution(True, "direct", reply, usage=self._usage(100, 50), attempt_count=1)
        if label == "D1":
            decision = self._compact(shadow_input, key, RouterSignal.NONE)
            return ShadowActorExecution(
                True,
                "compact",
                decision.reply_candidate,
                compact_decision=decision,
                usage=self._usage(150, 100),
                attempt_count=1,
            )
        if label == "D2":
            plan = self._plan(key)
            realization = self._realize(shadow_input, key, plan)
            return ShadowActorExecution(
                True,
                "staged",
                realization.reply_candidate,
                plan=plan,
                realization=realization,
                usage=self._usage(260, 190),
                attempt_count=2,
            )
        if label == "D3":
            if self._should_escalate(shadow_input):
                plan = self._plan(key)
                realization = self._realize(shadow_input, key, plan)
                return ShadowActorExecution(
                    True,
                    "staged",
                    realization.reply_candidate,
                    plan=plan,
                    realization=realization,
                    usage=self._usage(260, 190),
                    attempt_count=2,
                    escalation_occurred=True,
                )
            decision = self._compact(shadow_input, key, RouterSignal.NONE)
            return ShadowActorExecution(
                True,
                "compact",
                decision.reply_candidate,
                compact_decision=decision,
                usage=self._usage(150, 100),
                attempt_count=1,
            )
        if label == "D4":
            reply = self._reply(shadow_input, key, "equal-compute-direct")
            input_tokens, output_tokens, attempt_count = self._comparison_usage(
                shadow_input
            )
            return ShadowActorExecution(
                True,
                "equal_compute_direct",
                reply,
                usage=self._usage(input_tokens, output_tokens),
                attempt_count=attempt_count,
            )
        raise ValueError("unknown Shadow configuration")

    def _compact(
        self,
        shadow_input: ShadowEvaluationInputV1,
        key: str,
        signal: RouterSignal,
    ) -> CompactDecisionV1:
        frame, scene = self._frame_and_scene("compact")
        return CompactDecisionV1(
            result_kind=ResultKind.CANDIDATE,
            frame=frame,
            interior_scene=scene,
            reply_candidate=self._reply(shadow_input, key, "compact"),
            router_signal=signal,
        )

    def _plan(self, key: str) -> DeliberationPlanV1:
        frame, scene = self._frame_and_scene("staged")
        provisional = DeliberationPlanV1(
            frame=frame,
            interior_scene=scene,
            plan_fingerprint="0" * 64,
        )
        payload = provisional.model_dump(mode="json", exclude={"plan_fingerprint"})
        fingerprint = StrictCanonicalCodec.fingerprint(
            {"sample_key": key, "plan": payload},
            domain="erii-shadow-plan/v1",
        )
        return provisional.model_copy(update={"plan_fingerprint": fingerprint})

    def _realize(
        self,
        shadow_input: ShadowEvaluationInputV1,
        key: str,
        plan: DeliberationPlanV1,
    ) -> ReplyRealizationV1:
        return ReplyRealizationV1(
            plan_fingerprint=plan.plan_fingerprint,
            reply_candidate=self._reply(shadow_input, key, "staged"),
        )

    @staticmethod
    def _frame_and_scene(mode: str) -> tuple[DeliberationSemanticFrameV1, CharacterInteriorSceneV1]:
        frame = DeliberationSemanticFrameV1(
            result_kind=ResultKind.CANDIDATE,
            self_interpretation=SelfInterpretation(
                awareness=AwarenessLevel.UNFORMED,
                bounded_summary=f"Offline {mode} fixture",
            ),
            behavioral_intent=BehavioralIntent(
                kind="reply",
                bounded_summary="Produce a deterministic contract fixture",
            ),
            communication_strategy=CommunicationStrategy(
                expression_relation=ExpressionRelation.DIRECT,
                disclosure=DisclosureLevel.DIRECT,
                interpersonal_posture=InterpersonalPosture.OPEN,
                tone_goal=VoiceMode.CHARACTER_NATIVE,
            ),
        )
        scene = CharacterInteriorSceneV1(
            voice_mode=VoiceMode.CHARACTER_NATIVE,
            perspective=Perspective.MINIMAL,
            narrative_budget=NarrativeBudget.GLIMPSE,
            text=f"Offline {mode} interior fixture",
        )
        return frame, scene

    def _reply(
        self,
        shadow_input: ShadowEvaluationInputV1,
        key: str,
        route: str,
    ) -> VisibleReplyEnvelopeV1:
        digest = hashlib.sha256(f"{route}:{key}".encode("utf-8")).hexdigest()[:12]
        source = shadow_input.user_envelope.parts[0].exact_utf8
        return VisibleReplyEnvelopeV1(
            parts=(
                MessagePart(
                    part_id="reply-1",
                    kind="text",
                    exact_utf8=f"Offline fixture [{digest}] for: {source[:30]}",
                ),
            )
        )

    @staticmethod
    def _sample_key(shadow_input: ShadowEvaluationInputV1) -> str:
        return ":".join(
            (
                shadow_input.scenario.scenario_id,
                shadow_input.config.config_label,
                str(shadow_input.config.seed),
                str(shadow_input.sample_index),
            )
        )

    @staticmethod
    def _should_escalate(shadow_input: ShadowEvaluationInputV1) -> bool:
        return shadow_input.scenario.scenario_id == "adaptive-escalate-structural-20"

    def _comparison_usage(
        self,
        shadow_input: ShadowEvaluationInputV1,
    ) -> tuple[int, int, int]:
        target = shadow_input.config.comparison_target
        if target == "D1":
            return 150, 100, 1
        if target == "D2":
            return 260, 190, 2
        if target == "D3":
            if self._should_escalate(shadow_input):
                return 260, 190, 2
            return 150, 100, 1
        raise ValueError("D4 requires a supported comparison target")

    @staticmethod
    def _usage(input_tokens: int, output_tokens: int) -> ProviderUsage:
        return ProviderUsage(input_tokens=input_tokens, output_tokens=output_tokens)


__all__ = ["ShadowActorExecution", "DeterministicShadowActor"]
