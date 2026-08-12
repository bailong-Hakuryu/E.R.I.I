"""
测试 Core Trusted Validator

验证：
- 只有持有秘密的代码才能创建有效 Envelope
- 伪造的 Envelope 无法通过验证
- 必须与宿主权威状态一致
- Result Binding 正确工作
"""

import pytest

from erii.deliberation.core_validator import (
    TrustedAuthoritySecret,
    AuthorityState,
    TrustedEnvelopeV2,
    ResultBinding,
    CoreTrustedValidator,
    TurnStatus,
)
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
)


class TestTrustedAuthoritySecret:
    """测试 Trusted Authority Secret"""

    def test_create_secret(self):
        """测试创建秘密"""
        secret = TrustedAuthoritySecret()
        assert secret is not None

    def test_sign_and_verify(self):
        """测试签名和验证"""
        secret = TrustedAuthoritySecret()
        message = "test message"

        signature = secret.sign(message)
        assert isinstance(signature, str)
        assert len(signature) == 64  # SHA-256 十六进制

        # 验证应该通过
        assert secret.verify(message, signature)

        # 篡改消息后验证失败
        assert not secret.verify("tampered", signature)

        # 篡改签名后验证失败
        assert not secret.verify(message, "0" * 64)

    def test_different_secrets_produce_different_signatures(self):
        """测试不同秘密产生不同签名"""
        secret1 = TrustedAuthoritySecret()
        secret2 = TrustedAuthoritySecret()

        message = "test"
        sig1 = secret1.sign(message)
        sig2 = secret2.sign(message)

        # 不同秘密产生不同签名
        assert sig1 != sig2

        # 交叉验证失败
        assert not secret1.verify(message, sig2)
        assert not secret2.verify(message, sig1)


class TestTrustedEnvelopeV2:
    """测试 Trusted Envelope V2"""

    def test_create_envelope_with_validator(self):
        """测试使用 Validator 创建 Envelope"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        envelope = validator.create_envelope(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint="0000000000000000000000000000000000000000000000000000000000000001",
            user_message_fingerprint="1111111111111111111111111111111111111111111111111111111111111111",
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

        # Envelope 应该包含签名
        assert envelope.hmac_signature
        assert len(envelope.hmac_signature) == 64

        # 签名应该有效
        assert envelope.verify_with_secret(secret)

    def test_forged_envelope_fails_verification(self):
        """测试伪造的 Envelope 无法通过验证"""
        secret = TrustedAuthoritySecret()

        # 尝试伪造 Envelope（没有正确的签名）
        forged = TrustedEnvelopeV2(
            relationship_id="FORGED",
            turn_id="FORGED",
            persona_id="FORGED",
            evidence_view_fingerprint="0" * 64,
            user_message_fingerprint="1" * 64,
            run_epoch=999,
            expected_turn_state="open",
            hmac_signature="0" * 64,  # 伪造的签名
        )

        # 验证应该失败
        assert not forged.verify_with_secret(secret)

    def test_envelope_without_authority_state_fails(self):
        """测试没有权威状态的 Envelope 无法通过完整验证"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        # 创建有效签名的 Envelope
        envelope = validator.create_envelope(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint="0000000000000000000000000000000000000000000000000000000000000000",
            user_message_fingerprint="1111111111111111111111111111111111111111111111111111111111111111",
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

        # 签名本身有效
        assert envelope.verify_with_secret(secret)

        # 但与不匹配的权威状态验证失败
        wrong_authority = AuthorityState(
            current_epoch=2,  # 不匹配
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-1",
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )

        valid, errors = validator.verify_envelope(envelope, wrong_authority)
        assert not valid
        assert any("Epoch 不匹配" in err for err in errors)


class TestCoreTrustedValidator:
    """测试 Core Trusted Validator"""

    def test_full_validation_flow(self):
        """测试完整的验证流程"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        # 宿主权威状态
        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-123",
            active_turn_id="turn-456",
            active_persona_id="persona-789",
        )

        # 创建 Envelope
        envelope = validator.create_envelope(
            relationship_id="rel-123",
            turn_id="turn-456",
            persona_id="persona-789",
            evidence_view_fingerprint="0000000000000000000000000000000000000000000000000000000000000000",
            user_message_fingerprint="1111111111111111111111111111111111111111111111111111111111111111",
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

        # 完整验证应该通过
        valid, errors = validator.verify_envelope(envelope, authority)
        assert valid
        assert len(errors) == 0

    def test_replay_attack_detected(self):
        """测试检测重放攻击"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        # 创建旧的 Envelope（epoch=1）
        old_envelope = validator.create_envelope(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint="0000000000000000000000000000000000000000000000000000000000000000",
            user_message_fingerprint="1111111111111111111111111111111111111111111111111111111111111111",
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

        # 宿主已经前进到 epoch=2
        current_authority = AuthorityState(
            current_epoch=2,  # 已前进
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-1",
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )

        # 旧 Envelope 应该被拒绝
        valid, errors = validator.verify_envelope(old_envelope, current_authority)
        assert not valid
        assert any("Epoch 不匹配" in err for err in errors)

    def test_cross_relationship_detected(self):
        """测试检测跨关系攻击"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        # 创建属于 rel-A 的 Envelope
        envelope_a = validator.create_envelope(
            relationship_id="rel-A",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint="000000000000000000000000000000000000000000000000000000000000000a",
            user_message_fingerprint="1111111111111111111111111111111111111111111111111111111111111111",
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

        # 宿主当前在 rel-B
        authority_b = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-B",  # 不同的关系
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )

        # 应该被拒绝
        valid, errors = validator.verify_envelope(envelope_a, authority_b)
        assert not valid
        assert any("Relationship 不匹配" in err for err in errors)


class TestResultBinding:
    """测试 Result Binding"""

    def test_create_and_verify_binding(self):
        """测试创建和验证 Result Binding"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        # 权威状态
        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-1",
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )

        # 创建 Envelope
        envelope = validator.create_envelope(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint="0000000000000000000000000000000000000000000000000000000000000000",
            user_message_fingerprint="1111111111111111111111111111111111111111111111111111111111111111",
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

        # 创建 Decision
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
                perspective=Perspective.MINIMAL,
                narrative_budget=NarrativeBudget.GLIMPSE,
                text="测试",
            ),
            reply_candidate=VisibleReplyEnvelopeV1(
                parts=[MessagePart(part_id="r1", kind="text", exact_utf8="测试")],
            ),
        )

        # 创建 Result Binding（必须提供 authority）
        binding = validator.create_result_binding(
            envelope=envelope,
            decision=decision,
            reply=decision.reply_candidate,
            authority=authority,
        )

        # 验证应该通过（必须提供所有实际对象）
        valid, errors = validator.verify_result_binding(
            binding=binding,
            envelope=envelope,
            decision=decision,
            reply=decision.reply_candidate,
            authority=authority,
        )
        assert valid
        assert len(errors) == 0

    def test_tampered_binding_fails(self):
        """测试篡改的 Binding 无法通过验证"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-1",
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )

        envelope = validator.create_envelope(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint="0000000000000000000000000000000000000000000000000000000000000000",
            user_message_fingerprint="1111111111111111111111111111111111111111111111111111111111111111",
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

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
                perspective=Perspective.MINIMAL,
                narrative_budget=NarrativeBudget.GLIMPSE,
                text="测试",
            ),
            reply_candidate=VisibleReplyEnvelopeV1(
                parts=[MessagePart(part_id="r1", kind="text", exact_utf8="测试")],
            ),
        )

        # 创建伪造的 Binding
        forged = ResultBinding(
            envelope_fingerprint="0" * 64,
            decision_fingerprint="1" * 64,
            reply_fingerprint="2" * 64,
            result_fingerprint="3" * 64,
            hmac_signature="0" * 64,
        )

        # 验证应该失败
        valid, errors = validator.verify_result_binding(
            binding=forged,
            envelope=envelope,
            decision=decision,
            reply=decision.reply_candidate,
            authority=authority,
        )
        assert not valid
        assert len(errors) > 0

    def test_invalid_envelope_rejected_on_binding_creation(self):
        """测试创建 Binding 时拒绝无效 Envelope"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-1",
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )

        # 创建伪造的 Envelope
        forged_envelope = TrustedEnvelopeV2(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint="0000000000000000000000000000000000000000000000000000000000000000",
            user_message_fingerprint="1111111111111111111111111111111111111111111111111111111111111111",
            run_epoch=1,
            expected_turn_state="open",
            hmac_signature="0" * 64,  # 伪造签名
        )

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
                perspective=Perspective.MINIMAL,
                narrative_budget=NarrativeBudget.GLIMPSE,
                text="测试",
            ),
            reply_candidate=VisibleReplyEnvelopeV1(
                parts=[MessagePart(part_id="r1", kind="text", exact_utf8="测试")],
            ),
        )

        # 创建 Binding 应该失败
        with pytest.raises(ValueError, match="Envelope HMAC 签名无效"):
            validator.create_result_binding(
                envelope=forged_envelope,
                decision=decision,
                reply=decision.reply_candidate,
                authority=authority,
            )

    def test_reply_mismatch_rejected(self):
        """测试 Reply 不匹配被拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-1",
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )

        envelope = validator.create_envelope(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint="0000000000000000000000000000000000000000000000000000000000000000",
            user_message_fingerprint="1111111111111111111111111111111111111111111111111111111111111111",
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

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
                perspective=Perspective.MINIMAL,
                narrative_budget=NarrativeBudget.GLIMPSE,
                text="测试",
            ),
            reply_candidate=VisibleReplyEnvelopeV1(
                parts=[MessagePart(part_id="r1", kind="text", exact_utf8="原始")],
            ),
        )

        # 尝试用不同的 Reply 创建 Binding
        different_reply = VisibleReplyEnvelopeV1(
            parts=[MessagePart(part_id="r2", kind="text", exact_utf8="不同")],
        )

        with pytest.raises(ValueError, match="Reply 必须与 decision.reply_candidate 完全相等"):
            validator.create_result_binding(
                envelope=envelope,
                decision=decision,
                reply=different_reply,
                authority=authority,
            )


class TestSecurityProperties:
    """测试安全属性"""

    def test_actor_cannot_forge_envelope(self):
        """测试 Actor 无法伪造 Envelope"""
        # Actor 没有秘密
        # Actor 尝试计算消息并伪造签名
        _message = TrustedEnvelopeV2.compute_message(
            relationship_id="FORGED",
            turn_id="FORGED",
            persona_id="FORGED",
            evidence_view_fingerprint="0" * 64,
            user_message_fingerprint="1" * 64,
            run_epoch=999,
            expected_turn_state="open",
        )

        # Actor 无法创建有效签名（没有秘密）
        fake_signature = "0" * 64

        forged = TrustedEnvelopeV2(
            relationship_id="FORGED",
            turn_id="FORGED",
            persona_id="FORGED",
            evidence_view_fingerprint="0" * 64,
            user_message_fingerprint="1" * 64,
            run_epoch=999,
            expected_turn_state="open",
            hmac_signature=fake_signature,
        )

        # 宿主验证应该失败
        secret = TrustedAuthoritySecret()
        assert not forged.verify_with_secret(secret)

    def test_different_secrets_incompatible(self):
        """测试不同秘密不兼容"""
        secret1 = TrustedAuthoritySecret()
        secret2 = TrustedAuthoritySecret()

        validator1 = CoreTrustedValidator(secret1)

        # 使用 secret1 创建 Envelope
        envelope = validator1.create_envelope(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint="0000000000000000000000000000000000000000000000000000000000000000",
            user_message_fingerprint="1111111111111111111111111111111111111111111111111111111111111111",
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

        # 使用 secret2 验证应该失败
        assert not envelope.verify_with_secret(secret2)
