"""
Strict Codec 回归测试 - 必须先失败

验证 Strict Codec 的所有边界条件。
这些测试预期失败，修复后应该通过。
"""

import pytest

from erii.deliberation.strict_codec import StrictCanonicalCodec
from erii.deliberation.schemas import MessagePart


class TestStrictCodecRegression:
    """Strict Codec 回归测试"""

    def test_lone_surrogate_rejected(self):
        """孤立代理字符必须拒绝"""
        # JSON 中的转义代理字符
        json_with_surrogate = r'{"text": "test\uD800"}'

        with pytest.raises(ValueError, match="surrogate|invalid|Unicode"):
            StrictCanonicalCodec.deserialize(json_with_surrogate)

    def test_nul_character_rejected(self):
        """NUL 字符必须拒绝"""
        json_with_nul = '{"text": "test\x00value"}'

        with pytest.raises(ValueError, match="NUL|control|invalid|Invalid JSON"):
            StrictCanonicalCodec.deserialize(json_with_nul)

    def test_illegal_control_characters_rejected(self):
        """非法控制字符必须拒绝"""
        # 控制字符 0x01-0x1F (除了 \t \n \r)
        json_with_control = '{"text": "test\x01value"}'

        with pytest.raises(ValueError, match="control|invalid|Invalid JSON"):
            StrictCanonicalCodec.deserialize(json_with_control)

    def test_non_string_object_key_rejected(self):
        """非字符串 object key 必须拒绝"""
        # Python dict 可以有非字符串 key
        invalid_obj = {123: "value"}  # int key

        with pytest.raises((ValueError, TypeError), match="key.*字符串|key.*string"):
            StrictCanonicalCodec.serialize(invalid_obj)

    def test_tuple_rejected(self):
        """tuple 必须拒绝（只接受 list）"""
        obj_with_tuple = {"data": (1, 2, 3)}

        with pytest.raises(ValueError, match="tuple|不支持|unsupported"):
            StrictCanonicalCodec.serialize(obj_with_tuple)

    def test_set_rejected(self):
        """set 必须拒绝"""
        obj_with_set = {"data": {1, 2, 3}}

        with pytest.raises(ValueError, match="set|不支持|unsupported"):
            StrictCanonicalCodec.serialize(obj_with_set)

    def test_custom_object_rejected(self):
        """自定义对象必须拒绝"""
        class CustomObj:
            pass

        obj_with_custom = {"data": CustomObj()}

        with pytest.raises((ValueError, TypeError)):
            StrictCanonicalCodec.serialize(obj_with_custom)

    def test_wire_model_without_required_fields_rejected(self):
        """Wire model 缺少必需字段必须拒绝（不能使用默认值）"""
        # MessagePart 缺少 part_id（真正的必需字段，无默认值）
        json_missing_part_id = '{"kind": "text", "exact_utf8": "content"}'

        with pytest.raises(ValueError, match="required|missing|必需|part_id"):
            StrictCanonicalCodec.decode_as(json_missing_part_id, MessagePart)

    def test_decode_error_does_not_leak_input(self):
        """decode_as 的错误不得包含 sentinel input_value"""
        sentinel = "SECRET_CANARY_DO_NOT_LOG"
        json_with_sentinel = f'{{"part_id": "test", "kind": "text", "exact_utf8": "{sentinel}"}}'

        try:
            # 故意使用无效 JSON 触发错误
            StrictCanonicalCodec.decode_as(json_with_sentinel[:-1], MessagePart)  # 缺少 }
        except ValueError as e:
            error_str = str(e)
            # 错误消息不应该包含 sentinel
            assert sentinel not in error_str, f"Error leaked sentinel: {error_str}"

    def test_decode_error_repr_does_not_leak_input(self):
        """decode 错误的 repr 不得包含输入值"""
        sentinel = "SECRET_DATA"
        json_invalid = '{"part_id": "test", "kind": "INVALID", "exact_utf8": "' + sentinel + '"}'

        try:
            StrictCanonicalCodec.decode_as(json_invalid, MessagePart)
        except ValueError as e:
            repr_str = repr(e)
            # repr 不应该包含 sentinel
            assert sentinel not in repr_str, f"repr leaked sentinel: {repr_str}"

    def test_fingerprint_non_64_hex_rejected(self):
        """非 64 位十六进制 fingerprint 必须被验证时拒绝"""
        from erii.deliberation.core_validator import _validate_fingerprint

        # fingerprint 应该是 64 位小写十六进制
        invalid_fingerprints = [
            ("ABCDEF" + "0" * 58, "大写"),
            ("abc", "太短"),
            ("g" * 64, "非十六进制"),
        ]

        for fp, reason in invalid_fingerprints:
            with pytest.raises(ValueError, match="fingerprint.*格式|长度|小写|十六进制"):
                _validate_fingerprint(fp, "test_fingerprint")


def _validate_fingerprint_format(fp: str) -> None:
    """验证 fingerprint 格式（已移除，使用 core_validator._validate_fingerprint）"""
    from erii.deliberation.core_validator import _validate_fingerprint
    _validate_fingerprint(fp, "fingerprint")
