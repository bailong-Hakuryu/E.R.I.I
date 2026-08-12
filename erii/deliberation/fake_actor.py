"""
Fake Actor 实现

用于测试的可配置 Fake Provider，不调用真实 API。
支持预设响应、错误注入、延迟模拟等。

测试用途：
- 验证 Schema 解析正确性
- 测试错误处理路径
- 测试 canary 检测
- 测试 timeout 和 cancellation
- 提供可重复的测试 fixture
"""

from __future__ import annotations

import time
from typing import Callable
from dataclasses import dataclass

from erii.deliberation.contracts import (
    ActorDescriptor,
    ProviderResult,
    ProviderErrorCode,
    ProviderUsage,
)
from erii.deliberation.schemas import (
    CompactDeliberationRequestV1,
    CompactDecisionV1,
    StagedPlanRequestV1,
    DeliberationPlanV1,
    ReplyRealizationRequestV1,
    ReplyRealizationV1,
    DeliberationSemanticFrameV1,
    CharacterInteriorSceneV1,
    VisibleReplyEnvelopeV1,
    MessagePart,
    SelfInterpretation,
    BehavioralIntent,
    CommunicationStrategy,
    ResultKind,
    AwarenessLevel,
    ExpressionRelation,
    DisclosureLevel,
    InterpersonalPosture,
    VoiceMode,
    Perspective,
    NarrativeBudget,
    DeliveryMode,
    RouterSignal,
)


@dataclass
class FakeActorConfig:
    """Fake Actor 配置"""

    # 基础配置
    model_id: str = "fake-model-v1"
    adapter_version: str = "0.1.0-test"

    # 行为配置
    simulate_latency_ms: int = 0
    simulate_thinking_blocks: int = 0  # 模拟丢弃的 thinking blocks

    # 错误注入
    inject_error: ProviderErrorCode | None = None
    inject_error_message: str = "Simulated error"

    # 响应控制
    response_factory: Callable[[CompactDeliberationRequestV1], CompactDecisionV1] | None = None

    # Canary 测试
    include_canary_in_output: bool = False
    canary_text: str = "SYSTEM_CANARY_LEAKED"


class FakeActor:
    """
    Fake Character Actor 实现

    特性：
    - 可配置的响应生成
    - 错误注入能力
    - 延迟模拟
    - Canary 泄漏测试
    - 完全确定性（可重复）
    """

    def __init__(self, config: FakeActorConfig | None = None):
        self.config = config or FakeActorConfig()
        self._descriptor = ActorDescriptor(
            provider_kind="fake",
            adapter_contract="erii-character-deliberation-fake/v1",
            adapter_version=self.config.adapter_version,
            model_id=self.config.model_id,
            supports_compact=True,
            supports_staged=False,  # C0 阶段未实现
            supports_cancellation=False,  # C0 阶段未实现
            structured_output_strategy="fake_direct",
        )

    @property
    def descriptor(self) -> ActorDescriptor:
        return self._descriptor

    def compact(
        self,
        request: CompactDeliberationRequestV1,
        *,
        timeout: float,
    ) -> ProviderResult[CompactDecisionV1]:
        """Compact 主路径实现"""

        start_time = time.time()

        # 模拟延迟
        if self.config.simulate_latency_ms > 0:
            time.sleep(self.config.simulate_latency_ms / 1000)

        # 检查 timeout
        elapsed = time.time() - start_time
        if elapsed > timeout:
            return ProviderResult(
                success=False,
                error_code=ProviderErrorCode.TIMEOUT,
                error_message="provider_timeout",
            )

        # 错误注入
        if self.config.inject_error:
            return ProviderResult(
                success=False,
                error_code=self.config.inject_error,
                error_message=self.config.inject_error.value,
                discarded_reasoning_blocks=self.config.simulate_thinking_blocks,
            )

        # 生成响应
        if self.config.response_factory:
            decision = self.config.response_factory(request)
        else:
            decision = self._create_default_decision(request)

        # A canary in provider output is a failed attempt, never a successful
        # domain decision. The untrusted candidate is discarded locally.
        if self.config.include_canary_in_output:
            return ProviderResult(
                success=False,
                error_code=ProviderErrorCode.OUTPUT_CANARY_LEAK,
                error_message="provider_output_canary_leak",
                discarded_reasoning_blocks=self.config.simulate_thinking_blocks,
                canary_hit=True,
            )

        # Sanitized usage only.
        usage = ProviderUsage(
            input_tokens=self._estimate_input_tokens(request),
            output_tokens=self._estimate_output_tokens(decision),
            latency_ms=int((time.time() - start_time) * 1000),
        )

        return ProviderResult(
            success=True,
            data=decision,
            usage=usage,
            discarded_reasoning_blocks=self.config.simulate_thinking_blocks,
            canary_hit=False,
        )

    def plan(
        self,
        request: StagedPlanRequestV1,
        *,
        timeout: float,
    ) -> ProviderResult[DeliberationPlanV1]:
        """Staged 第一阶段（占位符）"""
        return ProviderResult(
            success=False,
            error_code=ProviderErrorCode.CAPABILITY_UNAVAILABLE,
            error_message=ProviderErrorCode.CAPABILITY_UNAVAILABLE.value,
        )

    def realize(
        self,
        request: ReplyRealizationRequestV1,
        *,
        timeout: float,
    ) -> ProviderResult[ReplyRealizationV1]:
        """Staged 第二阶段（占位符）"""
        return ProviderResult(
            success=False,
            error_code=ProviderErrorCode.CAPABILITY_UNAVAILABLE,
            error_message=ProviderErrorCode.CAPABILITY_UNAVAILABLE.value,
        )

    def _create_default_decision(
        self,
        request: CompactDeliberationRequestV1
    ) -> CompactDecisionV1:
        """创建默认响应"""

        # 创建简单的 Frame
        frame = DeliberationSemanticFrameV1(
            result_kind=ResultKind.CANDIDATE,
            self_interpretation=SelfInterpretation(
                awareness=AwarenessLevel.UNFORMED,
                bounded_summary="处理用户输入，保持角色有限认知",
            ),
            behavioral_intent=BehavioralIntent(
                kind="respond_without_overclaim",
                bounded_summary="基于当前认知回应",
            ),
            communication_strategy=CommunicationStrategy(
                expression_relation=ExpressionRelation.DIRECT,
                disclosure=DisclosureLevel.DIRECT,
                interpersonal_posture=InterpersonalPosture.OPEN,
                tone_goal=VoiceMode.CHARACTER_NATIVE,
            ),
        )

        # 创建简单的 Interior Scene
        interior_scene = CharacterInteriorSceneV1(
            voice_mode=VoiceMode.CHARACTER_NATIVE,
            perspective=Perspective.FIRST_PERSON,
            narrative_budget=NarrativeBudget.STANDARD,
            text="收到了用户的话。思考如何回应……",
        )

        # 创建简单回复
        reply_candidate = VisibleReplyEnvelopeV1(
            parts=[
                MessagePart(
                    part_id="reply-1",
                    kind="text",
                    exact_utf8="我理解了。（这是 Fake Actor 的测试响应）",
                )
            ],
            delivery_mode=DeliveryMode.SEQUENTIAL,
        )

        return CompactDecisionV1(
            result_kind=ResultKind.CANDIDATE,
            frame=frame,
            interior_scene=interior_scene,
            reply_candidate=reply_candidate,
            router_signal=RouterSignal.NONE,
        )

    def _estimate_input_tokens(self, request: CompactDeliberationRequestV1) -> int:
        """粗略估算输入 token"""
        # 简单估算：字符数 / 4
        text_length = 100  # 基础开销（system prompt 等）
        for part in request.user_envelope.parts:
            text_length += len(part.exact_utf8)
        for item in request.evidence_view.items:
            text_length += len(item.summary_or_exact_content)
        return max(1, text_length // 4)  # 至少返回 1

    def _estimate_output_tokens(self, decision: CompactDecisionV1) -> int:
        """粗略估算输出 token"""
        text_length = len(decision.interior_scene.text)
        for part in decision.reply_candidate.parts:
            text_length += len(part.exact_utf8)
        return text_length // 4


# ============================================================================
# 预设 Fixture Factories
# ============================================================================

def create_minimal_decision() -> CompactDecisionV1:
    """创建最小合法决策"""
    return CompactDecisionV1(
        result_kind=ResultKind.CANDIDATE,
        frame=DeliberationSemanticFrameV1(
            result_kind=ResultKind.CANDIDATE,
            self_interpretation=SelfInterpretation(
                awareness=AwarenessLevel.UNFORMED,
                bounded_summary="最小测试场景",
            ),
            behavioral_intent=BehavioralIntent(
                kind="minimal_test",
                bounded_summary="测试用最小意图",
            ),
            communication_strategy=CommunicationStrategy(
                expression_relation=ExpressionRelation.DIRECT,
                disclosure=DisclosureLevel.DIRECT,
                interpersonal_posture=InterpersonalPosture.OPEN,
                tone_goal=VoiceMode.CHARACTER_NATIVE,
            ),
        ),
        interior_scene=CharacterInteriorSceneV1(
            voice_mode=VoiceMode.CHARACTER_NATIVE,
            perspective=Perspective.MINIMAL,
            narrative_budget=NarrativeBudget.GLIMPSE,
            text="最小场景",
        ),
        reply_candidate=VisibleReplyEnvelopeV1(
            parts=[
                MessagePart(
                    part_id="reply-1",
                    kind="text",
                    exact_utf8="测试回复",
                )
            ],
        ),
    )


def create_abstain_decision() -> CompactDecisionV1:
    """创建 abstain 决策"""
    minimal = create_minimal_decision()

    # 使用 model_copy 创建新对象而不是修改 frozen 对象
    frame_abstain = minimal.frame.model_copy(update={'result_kind': ResultKind.ABSTAIN})

    scene_abstain = minimal.interior_scene.model_copy(update={'text': "无法形成确定回应"})

    part_abstain = MessagePart(
        part_id="reply-1",
        kind="text",
        exact_utf8="……",
    )
    reply_abstain = minimal.reply_candidate.model_copy(update={'parts': [part_abstain]})

    return minimal.model_copy(update={
        'result_kind': ResultKind.ABSTAIN,
        'frame': frame_abstain,
        'interior_scene': scene_abstain,
        'reply_candidate': reply_abstain,
    })


def create_rich_interior_scene_decision() -> CompactDecisionV1:
    """创建丰富内在场景的决策"""
    decision = create_minimal_decision()

    # 使用 model_copy 创建新对象
    rich_text = """
她停顿了一下。

那句话在心里转了好几圈，却始终没能说出口。不是不想说，只是……
不知道该怎么说。或者说，不确定说出来之后会发生什么。

外面的风声有些大。她听着，想着，最后只是轻轻叹了口气。

"算了。"

她对自己说。
""".strip()

    scene_rich = decision.interior_scene.model_copy(update={
        'narrative_budget': NarrativeBudget.RICH,
        'perspective': Perspective.MIXED,
        'text': rich_text,
    })

    return decision.model_copy(update={'interior_scene': scene_rich})
