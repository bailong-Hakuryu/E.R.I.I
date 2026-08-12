"""
测试 Fake Actor 实现

验证：
- 基本响应生成
- 错误注入
- 延迟模拟
- Usage 统计
- Timeout 处理
"""

import time

from erii.deliberation.fake_actor import (
    FakeActor,
    FakeActorConfig,
    create_minimal_decision,
    create_abstain_decision,
    create_rich_interior_scene_decision,
)
from erii.deliberation.contracts import (
    ProviderErrorCode,
    ActorDescriptor,
)
from erii.deliberation.schemas import (
    CompactDeliberationRequestV1,
    UserMessageEnvelope,
    EvidenceViewV1,
    MessagePart,
    ResultKind,
)


class TestFakeActor:
    """测试 Fake Actor 基础功能"""

    def test_descriptor(self):
        """测试 Actor 描述符"""
        actor = FakeActor()
        descriptor = actor.descriptor

        assert isinstance(descriptor, ActorDescriptor)
        assert descriptor.provider_kind == "fake"
        assert descriptor.supports_compact
        assert not descriptor.supports_staged  # C0 阶段未实现
        assert not descriptor.supports_cancellation  # C0 阶段未实现

    def test_default_response(self):
        """测试默认响应生成"""
        actor = FakeActor()

        request = CompactDeliberationRequestV1(
            user_envelope=UserMessageEnvelope(
                parts=[
                    MessagePart(
                        part_id="u1",
                        kind="text",
                        exact_utf8="你好",
                    )
                ],
                canonical_fingerprint="test-fp",
            ),
            evidence_view=EvidenceViewV1(
                view_id="test-view",
                relationship_id="test-rel",
                turn_id="test-turn",
                items=[],
                view_fingerprint="test-fp",
            ),
            relationship_id="test-rel",
            turn_id="test-turn",
        )

        result = actor.compact(request, timeout=5.0)

        assert result.success
        assert result.data is not None
        assert result.data.result_kind == ResultKind.CANDIDATE
        assert len(result.data.reply_candidate.parts) > 0
        assert result.usage is not None
        assert result.usage.input_tokens > 0
        assert result.usage.output_tokens > 0

    def test_custom_response_factory(self):
        """测试自定义响应工厂"""
        def custom_factory(request):
            from erii.deliberation.schemas import MessagePart, VisibleReplyEnvelopeV1, DeliveryMode

            decision = create_minimal_decision()
            # 使用 model_copy 创建新对象
            custom_part = MessagePart(
                part_id="reply-1",
                kind="text",
                exact_utf8="自定义回复",
            )
            custom_reply = VisibleReplyEnvelopeV1(
                parts=[custom_part],
                delivery_mode=DeliveryMode.SEQUENTIAL,
            )
            return decision.model_copy(update={'reply_candidate': custom_reply})

        config = FakeActorConfig(response_factory=custom_factory)
        actor = FakeActor(config)

        request = CompactDeliberationRequestV1(
            user_envelope=UserMessageEnvelope(
                parts=[MessagePart(part_id="u1", kind="text", exact_utf8="测试")],
                canonical_fingerprint="test-fp",
            ),
            evidence_view=EvidenceViewV1(
                view_id="test-view",
                relationship_id="test-rel",
                turn_id="test-turn",
                items=[],
                view_fingerprint="test-fp",
            ),
            relationship_id="test-rel",
            turn_id="test-turn",
        )

        result = actor.compact(request, timeout=5.0)

        assert result.success
        assert result.data.reply_candidate.parts[0].exact_utf8 == "自定义回复"


class TestFakeActorErrorInjection:
    """测试错误注入功能"""

    def test_inject_timeout(self):
        """测试注入超时错误"""
        config = FakeActorConfig(
            inject_error=ProviderErrorCode.TIMEOUT,
            inject_error_message="模拟超时",
        )
        actor = FakeActor(config)

        request = CompactDeliberationRequestV1(
            user_envelope=UserMessageEnvelope(
                parts=[MessagePart(part_id="u1", kind="text", exact_utf8="测试")],
                canonical_fingerprint="test-fp",
            ),
            evidence_view=EvidenceViewV1(
                view_id="test-view",
                relationship_id="test-rel",
                turn_id="test-turn",
                items=[],
                view_fingerprint="test-fp",
            ),
            relationship_id="test-rel",
            turn_id="test-turn",
        )

        result = actor.compact(request, timeout=5.0)

        assert not result.success
        assert result.error_code == ProviderErrorCode.TIMEOUT
        assert result.error_message == ProviderErrorCode.TIMEOUT.value

    def test_inject_rate_limit(self):
        """测试注入限流错误"""
        config = FakeActorConfig(
            inject_error=ProviderErrorCode.RATE_LIMITED,
        )
        actor = FakeActor(config)

        request = CompactDeliberationRequestV1(
            user_envelope=UserMessageEnvelope(
                parts=[MessagePart(part_id="u1", kind="text", exact_utf8="测试")],
                canonical_fingerprint="test-fp",
            ),
            evidence_view=EvidenceViewV1(
                view_id="test-view",
                relationship_id="test-rel",
                turn_id="test-turn",
                items=[],
                view_fingerprint="test-fp",
            ),
            relationship_id="test-rel",
            turn_id="test-turn",
        )

        result = actor.compact(request, timeout=5.0)

        assert not result.success
        assert result.error_code == ProviderErrorCode.RATE_LIMITED

    def test_inject_schema_invalid(self):
        """测试注入 Schema 无效错误"""
        config = FakeActorConfig(
            inject_error=ProviderErrorCode.OUTPUT_SCHEMA_INVALID,
        )
        actor = FakeActor(config)

        request = CompactDeliberationRequestV1(
            user_envelope=UserMessageEnvelope(
                parts=[MessagePart(part_id="u1", kind="text", exact_utf8="测试")],
                canonical_fingerprint="test-fp",
            ),
            evidence_view=EvidenceViewV1(
                view_id="test-view",
                relationship_id="test-rel",
                turn_id="test-turn",
                items=[],
                view_fingerprint="test-fp",
            ),
            relationship_id="test-rel",
            turn_id="test-turn",
        )

        result = actor.compact(request, timeout=5.0)

        assert not result.success
        assert result.error_code == ProviderErrorCode.OUTPUT_SCHEMA_INVALID


class TestFakeActorLatency:
    """测试延迟模拟"""

    def test_simulate_latency(self):
        """测试延迟模拟"""
        config = FakeActorConfig(simulate_latency_ms=100)
        actor = FakeActor(config)

        request = CompactDeliberationRequestV1(
            user_envelope=UserMessageEnvelope(
                parts=[MessagePart(part_id="u1", kind="text", exact_utf8="测试")],
                canonical_fingerprint="test-fp",
            ),
            evidence_view=EvidenceViewV1(
                view_id="test-view",
                relationship_id="test-rel",
                turn_id="test-turn",
                items=[],
                view_fingerprint="test-fp",
            ),
            relationship_id="test-rel",
            turn_id="test-turn",
        )

        start = time.time()
        result = actor.compact(request, timeout=5.0)
        elapsed = time.time() - start

        assert result.success
        assert elapsed >= 0.1  # 至少 100ms

    def test_timeout_respected(self):
        """测试 timeout 限制"""
        config = FakeActorConfig(simulate_latency_ms=2000)  # 2秒延迟
        actor = FakeActor(config)

        request = CompactDeliberationRequestV1(
            user_envelope=UserMessageEnvelope(
                parts=[MessagePart(part_id="u1", kind="text", exact_utf8="测试")],
                canonical_fingerprint="test-fp",
            ),
            evidence_view=EvidenceViewV1(
                view_id="test-view",
                relationship_id="test-rel",
                turn_id="test-turn",
                items=[],
                view_fingerprint="test-fp",
            ),
            relationship_id="test-rel",
            turn_id="test-turn",
        )

        result = actor.compact(request, timeout=1.0)  # 1秒超时

        assert not result.success
        assert result.error_code == ProviderErrorCode.TIMEOUT


class TestFakeActorThinkingBlocks:
    """测试 thinking blocks 模拟"""

    def test_simulate_thinking_blocks_discarded(self):
        """测试模拟丢弃的 thinking blocks"""
        config = FakeActorConfig(simulate_thinking_blocks=3)
        actor = FakeActor(config)

        request = CompactDeliberationRequestV1(
            user_envelope=UserMessageEnvelope(
                parts=[MessagePart(part_id="u1", kind="text", exact_utf8="测试")],
                canonical_fingerprint="test-fp",
            ),
            evidence_view=EvidenceViewV1(
                view_id="test-view",
                relationship_id="test-rel",
                turn_id="test-turn",
                items=[],
                view_fingerprint="test-fp",
            ),
            relationship_id="test-rel",
            turn_id="test-turn",
        )

        result = actor.compact(request, timeout=5.0)

        assert result.success
        assert result.discarded_reasoning_blocks == 3


class TestFixtureFactories:
    """测试预设 Fixture 工厂"""

    def test_create_minimal_decision(self):
        """测试创建最小决策"""
        decision = create_minimal_decision()

        assert decision.result_kind == ResultKind.CANDIDATE
        assert decision.frame.result_kind == ResultKind.CANDIDATE
        assert len(decision.reply_candidate.parts) == 1
        assert len(decision.interior_scene.text) > 0

    def test_create_abstain_decision(self):
        """测试创建 abstain 决策"""
        decision = create_abstain_decision()

        assert decision.result_kind == ResultKind.ABSTAIN
        assert decision.frame.result_kind == ResultKind.ABSTAIN

    def test_create_rich_interior_scene_decision(self):
        """测试创建丰富内在场景决策"""
        decision = create_rich_interior_scene_decision()

        assert decision.result_kind == ResultKind.CANDIDATE
        assert len(decision.interior_scene.text) > 100  # 丰富场景应该较长
        assert "\n" in decision.interior_scene.text  # 应该有换行


class TestStagedNotImplemented:
    """测试 Staged 模式尚未实现"""

    def test_plan_returns_capability_unavailable(self):
        """测试 plan 返回能力不可用"""
        actor = FakeActor()

        from erii.deliberation.schemas import StagedPlanRequestV1

        request = StagedPlanRequestV1()

        result = actor.plan(request, timeout=5.0)

        assert not result.success
        assert result.error_code == ProviderErrorCode.CAPABILITY_UNAVAILABLE

    def test_realize_returns_capability_unavailable(self):
        """测试 realize 返回能力不可用"""
        actor = FakeActor()

        from erii.deliberation.schemas import ReplyRealizationRequestV1

        request = ReplyRealizationRequestV1()

        result = actor.realize(request, timeout=5.0)

        assert not result.success
        assert result.error_code == ProviderErrorCode.CAPABILITY_UNAVAILABLE
