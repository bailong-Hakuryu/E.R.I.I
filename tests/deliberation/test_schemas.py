"""
测试 Schema 定义的正确性

验证：
- Pydantic 模型的严格性
- 必填字段验证
- 额外字段拒绝
- 枚举值验证
- 文本长度限制
"""

import pytest
from pydantic import ValidationError

from erii.deliberation.schemas import (
    CompactDecisionV1,
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


class TestMessagePart:
    """测试 MessagePart Schema"""

    def test_valid_message_part(self):
        """测试合法的消息片段"""
        part = MessagePart(
            part_id="test-1",
            kind="text",
            exact_utf8="测试内容",
        )
        assert part.part_id == "test-1"
        assert part.kind == "text"
        assert part.exact_utf8 == "测试内容"

    def test_empty_content_rejected(self):
        """测试空内容被拒绝"""
        with pytest.raises(ValidationError):
            MessagePart(
                part_id="test-1",
                kind="text",
                exact_utf8="",
            )

    def test_extra_fields_rejected(self):
        """测试额外字段被拒绝"""
        with pytest.raises(ValidationError):
            MessagePart(
                part_id="test-1",
                kind="text",
                exact_utf8="内容",
                extra_field="不应该存在",
            )

    def test_content_max_length(self):
        """测试内容长度限制"""
        # 合法长度
        part = MessagePart(
            part_id="test-1",
            kind="text",
            exact_utf8="x" * 10000,
        )
        assert len(part.exact_utf8) == 10000

        # 超长被拒绝
        with pytest.raises(ValidationError):
            MessagePart(
                part_id="test-1",
                kind="text",
                exact_utf8="x" * 10001,
            )


class TestCharacterInteriorSceneV1:
    """测试 CharacterInteriorSceneV1 Schema"""

    def test_minimal_valid_scene(self):
        """测试最小合法场景"""
        scene = CharacterInteriorSceneV1(
            voice_mode=VoiceMode.CHARACTER_NATIVE,
            perspective=Perspective.FIRST_PERSON,
            narrative_budget=NarrativeBudget.GLIMPSE,
            text="一个简短的想法。",
        )
        assert scene.text == "一个简短的想法。"
        assert scene.projection_eligibility == "not_assessed"

    def test_empty_text_rejected(self):
        """测试空文本被拒绝"""
        with pytest.raises(ValidationError):
            CharacterInteriorSceneV1(
                voice_mode=VoiceMode.CHARACTER_NATIVE,
                perspective=Perspective.FIRST_PERSON,
                narrative_budget=NarrativeBudget.GLIMPSE,
                text="",
            )

    def test_whitespace_only_rejected(self):
        """测试纯空格被拒绝"""
        with pytest.raises(ValidationError):
            CharacterInteriorSceneV1(
                voice_mode=VoiceMode.CHARACTER_NATIVE,
                perspective=Perspective.FIRST_PERSON,
                narrative_budget=NarrativeBudget.GLIMPSE,
                text="   \n  \t  ",
            )

    def test_rich_scene_with_anchors(self):
        """测试丰富场景"""
        scene = CharacterInteriorSceneV1(
            voice_mode=VoiceMode.CHARACTER_NATIVE,
            perspective=Perspective.MIXED,
            narrative_budget=NarrativeBudget.RICH,
            text="她停顿了一下。\n\n那句话转了好几圈，却说不出口。",
            semantic_anchor_ids=["appraisal-1", "tension-1"],
            factual_echo_refs=["relationship:event:123"],
        )
        assert len(scene.semantic_anchor_ids) == 2
        assert len(scene.factual_echo_refs) == 1

    def test_text_max_length(self):
        """测试文本长度限制"""
        # 合法长度
        scene = CharacterInteriorSceneV1(
            voice_mode=VoiceMode.CHARACTER_NATIVE,
            perspective=Perspective.FIRST_PERSON,
            narrative_budget=NarrativeBudget.SCENE,
            text="x" * 5000,
        )
        assert len(scene.text) == 5000

        # 超长被拒绝
        with pytest.raises(ValidationError):
            CharacterInteriorSceneV1(
                voice_mode=VoiceMode.CHARACTER_NATIVE,
                perspective=Perspective.FIRST_PERSON,
                narrative_budget=NarrativeBudget.SCENE,
                text="x" * 5001,
            )


class TestDeliberationSemanticFrameV1:
    """测试 DeliberationSemanticFrameV1 Schema"""

    def test_minimal_valid_frame(self):
        """测试最小合法框架"""
        frame = DeliberationSemanticFrameV1(
            result_kind=ResultKind.CANDIDATE,
            self_interpretation=SelfInterpretation(
                awareness=AwarenessLevel.UNFORMED,
                bounded_summary="未形成明确理解",
            ),
            behavioral_intent=BehavioralIntent(
                kind="minimal",
                bounded_summary="最小意图",
            ),
            communication_strategy=CommunicationStrategy(
                expression_relation=ExpressionRelation.DIRECT,
                disclosure=DisclosureLevel.DIRECT,
                interpersonal_posture=InterpersonalPosture.OPEN,
                tone_goal=VoiceMode.CHARACTER_NATIVE,
            ),
        )
        assert frame.result_kind == ResultKind.CANDIDATE
        assert len(frame.situation_appraisals) == 0
        assert len(frame.uncertainties) == 0

    def test_abstain_result(self):
        """测试 abstain 结果"""
        frame = DeliberationSemanticFrameV1(
            result_kind=ResultKind.ABSTAIN,
            self_interpretation=SelfInterpretation(
                awareness=AwarenessLevel.UNFORMED,
                bounded_summary="无法形成确定理解",
            ),
            behavioral_intent=BehavioralIntent(
                kind="abstain",
                bounded_summary="无法决定",
            ),
            communication_strategy=CommunicationStrategy(
                expression_relation=ExpressionRelation.WITHHOLD,
                disclosure=DisclosureLevel.WITHHELD,
                interpersonal_posture=InterpersonalPosture.GUARDED,
                tone_goal=VoiceMode.CHARACTER_NATIVE,
            ),
        )
        assert frame.result_kind == ResultKind.ABSTAIN

    def test_array_size_limit(self):
        """测试数组大小限制"""
        from erii.deliberation.schemas import SituationAppraisal, EpistemicStatus

        # 合法大小
        appraisals = [
            SituationAppraisal(
                appraisal_id=f"appraisal-{i}",
                bounded_summary=f"评估 {i}",
                epistemic_status=EpistemicStatus.TENTATIVE,
            )
            for i in range(50)
        ]

        frame = DeliberationSemanticFrameV1(
            result_kind=ResultKind.CANDIDATE,
            situation_appraisals=appraisals,
            self_interpretation=SelfInterpretation(
                awareness=AwarenessLevel.UNFORMED,
                bounded_summary="测试",
            ),
            behavioral_intent=BehavioralIntent(
                kind="test",
                bounded_summary="测试",
            ),
            communication_strategy=CommunicationStrategy(
                expression_relation=ExpressionRelation.DIRECT,
                disclosure=DisclosureLevel.DIRECT,
                interpersonal_posture=InterpersonalPosture.OPEN,
                tone_goal=VoiceMode.CHARACTER_NATIVE,
            ),
        )
        assert len(frame.situation_appraisals) == 50

        # 超大数组被拒绝
        too_many = [
            SituationAppraisal(
                appraisal_id=f"appraisal-{i}",
                bounded_summary=f"评估 {i}",
                epistemic_status=EpistemicStatus.TENTATIVE,
            )
            for i in range(51)
        ]

        with pytest.raises(ValidationError):
            DeliberationSemanticFrameV1(
                result_kind=ResultKind.CANDIDATE,
                situation_appraisals=too_many,
                self_interpretation=SelfInterpretation(
                    awareness=AwarenessLevel.UNFORMED,
                    bounded_summary="测试",
                ),
                behavioral_intent=BehavioralIntent(
                    kind="test",
                    bounded_summary="测试",
                ),
                communication_strategy=CommunicationStrategy(
                    expression_relation=ExpressionRelation.DIRECT,
                    disclosure=DisclosureLevel.DIRECT,
                    interpersonal_posture=InterpersonalPosture.OPEN,
                    tone_goal=VoiceMode.CHARACTER_NATIVE,
                ),
            )


class TestCompactDecisionV1:
    """测试 CompactDecisionV1 Schema"""

    def test_complete_valid_decision(self):
        """测试完整合法决策"""
        decision = CompactDecisionV1(
            result_kind=ResultKind.CANDIDATE,
            frame=DeliberationSemanticFrameV1(
                result_kind=ResultKind.CANDIDATE,
                self_interpretation=SelfInterpretation(
                    awareness=AwarenessLevel.UNFORMED,
                    bounded_summary="测试场景",
                ),
                behavioral_intent=BehavioralIntent(
                    kind="test",
                    bounded_summary="测试意图",
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
                perspective=Perspective.FIRST_PERSON,
                narrative_budget=NarrativeBudget.STANDARD,
                text="内心的想法。",
            ),
            reply_candidate=VisibleReplyEnvelopeV1(
                parts=[
                    MessagePart(
                        part_id="reply-1",
                        kind="text",
                        exact_utf8="回复内容。",
                    )
                ],
                delivery_mode=DeliveryMode.SEQUENTIAL,
            ),
            router_signal=RouterSignal.NONE,
        )

        assert decision.result_kind == ResultKind.CANDIDATE
        assert decision.decision_version == "erii-compact-decision/v1"
        assert decision.frame.result_kind == ResultKind.CANDIDATE
        assert len(decision.reply_candidate.parts) == 1

    def test_multi_part_reply(self):
        """测试多片段回复"""
        decision = CompactDecisionV1(
            result_kind=ResultKind.CANDIDATE,
            frame=DeliberationSemanticFrameV1(
                result_kind=ResultKind.CANDIDATE,
                self_interpretation=SelfInterpretation(
                    awareness=AwarenessLevel.UNFORMED,
                    bounded_summary="测试",
                ),
                behavioral_intent=BehavioralIntent(
                    kind="test",
                    bounded_summary="测试",
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
                perspective=Perspective.FIRST_PERSON,
                narrative_budget=NarrativeBudget.GLIMPSE,
                text="想法",
            ),
            reply_candidate=VisibleReplyEnvelopeV1(
                parts=[
                    MessagePart(part_id="reply-1", kind="text", exact_utf8="第一条"),
                    MessagePart(part_id="reply-2", kind="text", exact_utf8="第二条"),
                    MessagePart(part_id="reply-3", kind="text", exact_utf8="第三条"),
                ],
            ),
        )

        assert len(decision.reply_candidate.parts) == 3

    def test_serialization_roundtrip(self):
        """测试序列化往返"""
        from erii.deliberation.fake_actor import create_minimal_decision

        decision = create_minimal_decision()

        # 序列化为 JSON
        json_str = decision.model_dump_json()
        assert isinstance(json_str, str)

        # 反序列化
        decision2 = CompactDecisionV1.model_validate_json(json_str)
        assert decision2.result_kind == decision.result_kind
        assert decision2.frame.result_kind == decision.frame.result_kind
