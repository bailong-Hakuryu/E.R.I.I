"""
Authority/Envelope 严格验证回归测试

验证类型、枚举和字符串边界条件。
"""

import pytest

from erii.deliberation.core_validator import (
    TrustedAuthoritySecret,
    AuthorityState,
    CoreTrustedValidator,
    TurnStatus,
)


class TestAuthorityEnvelopeStrictValidation:
    """Authority 和 Envelope 严格验证"""

    def test_bool_epoch_rejected(self):
        """bool 类型的 epoch 被拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        valid_fp = "0" * 64

        with pytest.raises(ValueError, match="run_epoch.*int.*bool"):
            validator.create_envelope(
                relationship_id="rel-1",
                turn_id="turn-1",
                persona_id="persona-1",
                evidence_view_fingerprint=valid_fp,
                user_message_fingerprint=valid_fp,
                run_epoch=True,  # bool 而非 int
                expected_turn_state=TurnStatus.OPEN,
            )

    def test_arbitrary_state_string_rejected(self):
        """任意字符串状态被拒绝（必须使用 TurnStatus 枚举）"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        valid_fp = "0" * 64

        with pytest.raises(ValueError, match="expected_turn_state.*TurnStatus"):
            validator.create_envelope(
                relationship_id="rel-1",
                turn_id="turn-1",
                persona_id="persona-1",
                evidence_view_fingerprint=valid_fp,
                user_message_fingerprint=valid_fp,
                run_epoch=1,
                expected_turn_state="ARBITRARY_STRING",  # type: ignore
            )

    def test_nul_in_relationship_id_rejected(self):
        """relationship_id 包含 NUL 被拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        valid_fp = "0" * 64

        with pytest.raises(ValueError, match="relationship_id.*NUL"):
            validator.create_envelope(
                relationship_id="rel\x00-1",  # 包含 NUL
                turn_id="turn-1",
                persona_id="persona-1",
                evidence_view_fingerprint=valid_fp,
                user_message_fingerprint=valid_fp,
                run_epoch=1,
                expected_turn_state=TurnStatus.OPEN,
            )

    def test_control_char_in_turn_id_rejected(self):
        """turn_id 包含控制字符被拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        valid_fp = "0" * 64

        with pytest.raises(ValueError, match="turn_id.*控制字符|control"):
            validator.create_envelope(
                relationship_id="rel-1",
                turn_id="turn\x01-1",  # 包含控制字符
                persona_id="persona-1",
                evidence_view_fingerprint=valid_fp,
                user_message_fingerprint=valid_fp,
                run_epoch=1,
                expected_turn_state=TurnStatus.OPEN,
            )

    def test_surrogate_in_persona_id_rejected(self):
        """persona_id 包含孤立代理字符被拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        valid_fp = "0" * 64

        # 孤立代理字符
        surrogate_str = "persona-\ud800-1"

        with pytest.raises(ValueError, match="persona_id.*代理|surrogate"):
            validator.create_envelope(
                relationship_id="rel-1",
                turn_id="turn-1",
                persona_id=surrogate_str,
                evidence_view_fingerprint=valid_fp,
                user_message_fingerprint=valid_fp,
                run_epoch=1,
                expected_turn_state=TurnStatus.OPEN,
            )

    def test_overlength_id_rejected(self):
        """超长 ID 被拒绝"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        valid_fp = "0" * 64
        overlength_id = "x" * 300  # 超过 256

        with pytest.raises(ValueError, match="长度.*256|length.*256"):
            validator.create_envelope(
                relationship_id=overlength_id,
                turn_id="turn-1",
                persona_id="persona-1",
                evidence_view_fingerprint=valid_fp,
                user_message_fingerprint=valid_fp,
                run_epoch=1,
                expected_turn_state=TurnStatus.OPEN,
            )

    def test_authority_state_validates_on_construction(self):
        """AuthorityState 在构造时验证"""
        # bool epoch
        with pytest.raises(ValueError, match="current_epoch.*int.*bool"):
            AuthorityState(
                current_epoch=True,  # type: ignore
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel-1",
                active_turn_id="turn-1",
                active_persona_id="persona-1",
            )

        # 负数 epoch
        with pytest.raises(ValueError, match="current_epoch.*非负|negative"):
            AuthorityState(
                current_epoch=-1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel-1",
                active_turn_id="turn-1",
                active_persona_id="persona-1",
            )

        # NUL in ID
        with pytest.raises(ValueError, match="active_relationship_id.*NUL"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel\x00-1",
                active_turn_id="turn-1",
                active_persona_id="persona-1",
            )
