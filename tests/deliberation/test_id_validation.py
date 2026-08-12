"""
ID 字符串严格验证测试

验证 ID 拒绝所有控制字符、格式字符、分隔符。
"""

import pytest

from erii.deliberation.core_validator import (
    AuthorityState,
    TurnStatus,
)


class TestIDStringValidation:
    """ID 字符串验证（不是 narrative 文本）"""

    def test_tab_rejected(self):
        """TAB 被拒绝"""
        with pytest.raises(ValueError, match="控制字符.*U\\+0009"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel\t-1",  # TAB
                active_turn_id="turn-1",
                active_persona_id="persona-1",
            )

    def test_newline_rejected(self):
        """LF 被拒绝"""
        with pytest.raises(ValueError, match="控制字符.*U\\+000A"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel-1",
                active_turn_id="turn\n-1",  # LF
                active_persona_id="persona-1",
            )

    def test_carriage_return_rejected(self):
        """CR 被拒绝"""
        with pytest.raises(ValueError, match="控制字符.*U\\+000D"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel-1",
                active_turn_id="turn-1",
                active_persona_id="persona\r-1",  # CR
            )

    def test_zwsp_rejected(self):
        """ZERO WIDTH SPACE (U+200B) 被拒绝"""
        with pytest.raises(ValueError, match="Unicode.*Cf.*U\\+200B"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel​-1",  # ZWSP
                active_turn_id="turn-1",
                active_persona_id="persona-1",
            )

    def test_bom_rejected(self):
        """BYTE ORDER MARK (U+FEFF) 被拒绝"""
        with pytest.raises(ValueError, match="Unicode.*Cf.*U\\+FEFF"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel-1",
                active_turn_id="﻿ turn-1",  # BOM
                active_persona_id="persona-1",
            )

    def test_word_joiner_rejected(self):
        """WORD JOINER (U+2060) 被拒绝"""
        with pytest.raises(ValueError, match="Unicode.*Cf.*U\\+2060"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel-1",
                active_turn_id="turn-1",
                active_persona_id="persona⁠-1",  # WORD JOINER
            )

    def test_line_separator_rejected(self):
        """LINE SEPARATOR (U+2028) 被拒绝"""
        with pytest.raises(ValueError, match="行/段分隔符.*U\\+2028"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel -1",  # LINE SEPARATOR
                active_turn_id="turn-1",
                active_persona_id="persona-1",
            )

    def test_paragraph_separator_rejected(self):
        """PARAGRAPH SEPARATOR (U+2029) 被拒绝"""
        with pytest.raises(ValueError, match="行/段分隔符.*U\\+2029"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel-1",
                active_turn_id="turn -1",  # PARAGRAPH SEPARATOR
                active_persona_id="persona-1",
            )

    def test_delete_control_rejected(self):
        """DEL (U+007F) 被拒绝"""
        with pytest.raises(ValueError, match="控制字符.*U\\+007F"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel-1",
                active_turn_id="turn-1",
                active_persona_id="persona\x7f-1",  # DEL
            )

    def test_c1_control_rejected(self):
        """C1 控制字符 (U+0080-U+009F) 被拒绝"""
        with pytest.raises(ValueError, match="控制字符.*U\\+0080"):
            AuthorityState(
                current_epoch=1,
                turn_status=TurnStatus.OPEN,
                active_relationship_id="rel-1",  # C1 control
                active_turn_id="turn-1",
                active_persona_id="persona-1",
            )

    def test_valid_ascii_id(self):
        """有效的 ASCII ID"""
        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-123",
            active_turn_id="turn-456",
            active_persona_id="persona-789",
        )
        assert authority.active_relationship_id == "rel-123"

    def test_valid_unicode_id(self):
        """有效的 Unicode ID（普通字符）"""
        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="关系-123",
            active_turn_id="回合-456",
            active_persona_id="角色-789",
        )
        assert authority.active_relationship_id == "关系-123"

    def test_emoji_id_allowed(self):
        """Emoji 在 ID 中允许（虽然不推荐）"""
        authority = AuthorityState(
            current_epoch=1,
            turn_status=TurnStatus.OPEN,
            active_relationship_id="rel-🎯",
            active_turn_id="turn-1",
            active_persona_id="persona-1",
        )
        assert authority.active_relationship_id == "rel-🎯"
