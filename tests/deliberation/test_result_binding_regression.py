"""
Result Binding 回归测试 - 必须先失败

验证 Result Binding 的所有边界条件。
这些测试预期失败，修复后应该通过。
"""

import pytest

from erii.deliberation.core_validator import (
    TrustedAuthoritySecret,
    AuthorityState,
    CoreTrustedValidator,
    ResultBinding,
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


class TestResultBindingRegression:
    """Result Binding 回归测试"""

    def test_result_fingerprint_mismatch_rejected(self):
        """result_fingerprint 与实际三个 fingerprint 不一致必须拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-1",
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )

        # 使用有效的 64 位十六进制 fingerprint
        valid_fp = "0" * 64

        envelope = validator.create_envelope(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint=valid_fp,
            user_message_fingerprint=valid_fp,
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

        decision = self._create_test_decision()

        # 创建正常的 Binding
        binding = validator.create_result_binding(
            envelope=envelope,
            decision=decision,
            reply=decision.reply_candidate,
            authority=authority,
        )

        # 篡改 result_fingerprint（但保持 HMAC 有效）
        tampered_binding = ResultBinding(
            envelope_fingerprint=binding.envelope_fingerprint,
            decision_fingerprint=binding.decision_fingerprint,
            reply_fingerprint=binding.reply_fingerprint,
            result_fingerprint="f" * 64,  # 篡改为不同的有效 fingerprint
            hmac_signature=binding.hmac_signature,
        )

        # 验证应该失败（检测到 result_fingerprint 不一致）
        valid, errors = validator.verify_result_binding(
            binding=tampered_binding,
            envelope=envelope,
            decision=decision,
            reply=decision.reply_candidate,
            authority=authority,
        )

        assert not valid
        assert any("result" in err.lower() and "fingerprint" in err.lower() for err in errors)

    def test_empty_relationship_id_rejected(self):
        """空 relationship_id 必须拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        with pytest.raises(ValueError, match="relationship_id.*不能为空|不能为空"):
            validator.create_envelope(
                relationship_id="",  # 空
                turn_id="turn-1",
                persona_id="persona-1",
                evidence_view_fingerprint="ev-fp",
                user_message_fingerprint="um-fp",
                run_epoch=1,
                expected_turn_state=TurnStatus.OPEN,
            )

    def test_empty_turn_id_rejected(self):
        """空 turn_id 必须拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        with pytest.raises(ValueError, match="turn_id.*不能为空|不能为空"):
            validator.create_envelope(
                relationship_id="rel-1",
                turn_id="",  # 空
                persona_id="persona-1",
                evidence_view_fingerprint="ev-fp",
                user_message_fingerprint="um-fp",
                run_epoch=1,
                expected_turn_state=TurnStatus.OPEN,
            )

    def test_empty_persona_id_rejected(self):
        """空 persona_id 必须拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        with pytest.raises(ValueError, match="persona_id.*不能为空|不能为空"):
            validator.create_envelope(
                relationship_id="rel-1",
                turn_id="turn-1",
                persona_id="",  # 空
                evidence_view_fingerprint="ev-fp",
                user_message_fingerprint="um-fp",
                run_epoch=1,
                expected_turn_state=TurnStatus.OPEN,
            )

    def test_negative_epoch_rejected(self):
        """负数 epoch 必须拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        valid_fp = "0" * 64

        with pytest.raises(ValueError, match="epoch.*非负|负数"):
            validator.create_envelope(
                relationship_id="rel-1",
                turn_id="turn-1",
                persona_id="persona-1",
                evidence_view_fingerprint=valid_fp,
                user_message_fingerprint=valid_fp,
                run_epoch=-1,  # 负数
                expected_turn_state=TurnStatus.OPEN,
            )

    def test_invalid_fingerprint_format_rejected(self):
        """非 64 位小写 SHA-256 fingerprint 必须拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        valid_fp = "0" * 64

        # 大写 fingerprint
        with pytest.raises(ValueError, match="fingerprint|小写|十六进制"):
            validator.create_envelope(
                relationship_id="rel-1",
                turn_id="turn-1",
                persona_id="persona-1",
                evidence_view_fingerprint="ABCDEF" + "0" * 58,  # 大写
                user_message_fingerprint=valid_fp,
                run_epoch=1,
                expected_turn_state=TurnStatus.OPEN,
            )

        # 长度不足
        with pytest.raises(ValueError, match="fingerprint|长度"):
            validator.create_envelope(
                relationship_id="rel-1",
                turn_id="turn-1",
                persona_id="persona-1",
                evidence_view_fingerprint="abc123",  # 太短
                user_message_fingerprint=valid_fp,
                run_epoch=1,
                expected_turn_state=TurnStatus.OPEN,
            )

    def _create_test_decision(self) -> CompactDecisionV1:
        """创建测试用的 Decision"""
        return CompactDecisionV1(
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
