"""
验证 Deliberation 使用真实的 erii.models.turn.TurnStatus

确保不存在同名第二套枚举。
"""

from erii.deliberation.core_validator import (
    TrustedAuthoritySecret,
    AuthorityState,
    CoreTrustedValidator,
    TurnStatus,
)
from erii.models.turn import TurnStatus as ModelsTurnStatus


class TestTurnStatusIdentity:
    """验证 TurnStatus 是真实的领域枚举"""

    def test_turnstatus_is_models_turnstatus(self):
        """Deliberation 使用的 TurnStatus 就是 erii.models.turn.TurnStatus"""
        assert TurnStatus is ModelsTurnStatus

    def test_turnstatus_has_open(self):
        """TurnStatus.OPEN 存在"""
        assert hasattr(TurnStatus, 'OPEN')
        assert TurnStatus.OPEN.value == "open"

    def test_turnstatus_has_completed(self):
        """TurnStatus.COMPLETED 存在"""
        assert hasattr(TurnStatus, 'COMPLETED')
        assert TurnStatus.COMPLETED.value == "completed"

    def test_turnstatus_has_abandoned(self):
        """TurnStatus.ABANDONED 存在"""
        assert hasattr(TurnStatus, 'ABANDONED')
        assert TurnStatus.ABANDONED.value == "abandoned"

    def test_turnstatus_no_awaiting_decision(self):
        """TurnStatus 不应有 AWAITING_DECISION（那是 DeliberationRunPhase）"""
        assert not hasattr(TurnStatus, 'AWAITING_DECISION')

    def test_authority_state_accepts_turnstatus(self):
        """AuthorityState 接受真实的 TurnStatus"""
        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-1",
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )
        assert authority.turn_status == TurnStatus.OPEN

    def test_create_envelope_requires_turnstatus(self):
        """create_envelope 要求 TurnStatus 枚举"""
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        valid_fp = "0" * 64

        # 使用真实 TurnStatus
        envelope = validator.create_envelope(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint=valid_fp,
            user_message_fingerprint=valid_fp,
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,
        )

        # Envelope 内部保存字符串值
        assert envelope.expected_turn_state == "open"

    def test_deliberation_requires_open_turn(self):
        """Character Deliberation 必须在 TurnStatus.OPEN 时发生"""
        # 这是领域规则：deliberation 只在 OPEN 的 Turn 中运行
        # 当前测试只验证能使用 OPEN 状态
        secret = TrustedAuthoritySecret()
        validator = CoreTrustedValidator(secret)

        valid_fp = "0" * 64

        envelope = validator.create_envelope(
            relationship_id="rel-1",
            turn_id="turn-1",
            persona_id="persona-1",
            evidence_view_fingerprint=valid_fp,
            user_message_fingerprint=valid_fp,
            run_epoch=1,
            expected_turn_state=TurnStatus.OPEN,  # 必须是 OPEN
        )

        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-1",
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )

        # 验证应该通过
        is_valid, errors = envelope.verify_against_authority(authority)
        assert is_valid
        assert len(errors) == 0
