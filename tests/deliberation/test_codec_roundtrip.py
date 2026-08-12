"""
Canonical Codec Round-trip 测试

验证 serialize 和 deserialize 的对称性。
"""

import pytest

from erii.deliberation.strict_codec import StrictCanonicalCodec


class TestCanonicalCodecRoundtrip:
    """Canonical Codec 对称性测试"""

    def test_escaped_nul_roundtrip(self):
        """转义的 NUL 字符往返"""
        # JSON 中转义的 NUL
        json_with_escaped_nul = '{"text":"hello\\u0000world"}'

        # deserialize 应该拒绝
        with pytest.raises(ValueError, match="NUL"):
            StrictCanonicalCodec.deserialize(json_with_escaped_nul)

    def test_escaped_control_char_roundtrip(self):
        """转义的控制字符往返"""
        # JSON 中转义的控制字符 0x01
        json_with_escaped_control = '{"text":"hello\\u0001world"}'

        # deserialize 应该拒绝
        with pytest.raises(ValueError, match="控制字符|control"):
            StrictCanonicalCodec.deserialize(json_with_escaped_control)

    def test_escaped_surrogate_roundtrip(self):
        """转义的孤立代理字符往返"""
        # JSON 中转义的代理字符
        json_with_escaped_surrogate = '{"text":"hello\\ud800world"}'

        # deserialize 应该拒绝
        with pytest.raises(ValueError, match="代理|surrogate"):
            StrictCanonicalCodec.deserialize(json_with_escaped_surrogate)

    def test_serialize_rejects_nul_in_string(self):
        """serialize 拒绝包含 NUL 的字符串"""
        obj_with_nul = {"text": "hello\x00world"}

        with pytest.raises(ValueError, match="NUL"):
            StrictCanonicalCodec.serialize(obj_with_nul)

    def test_serialize_rejects_control_in_string(self):
        """serialize 拒绝包含控制字符的字符串"""
        obj_with_control = {"text": "hello\x01world"}

        with pytest.raises(ValueError, match="控制字符|control"):
            StrictCanonicalCodec.serialize(obj_with_control)

    def test_serialize_rejects_surrogate_in_string(self):
        """serialize 拒绝包含孤立代理字符的字符串"""
        obj_with_surrogate = {"text": "hello\ud800world"}

        with pytest.raises(ValueError, match="代理|surrogate"):
            StrictCanonicalCodec.serialize(obj_with_surrogate)

    def test_serialize_rejects_nul_in_key(self):
        """serialize 拒绝包含 NUL 的 key"""
        obj_with_nul_key = {"key\x00test": "value"}

        with pytest.raises(ValueError, match="NUL"):
            StrictCanonicalCodec.serialize(obj_with_nul_key)

    def test_valid_roundtrip(self):
        """有效对象的往返"""
        obj = {
            "text": "hello world",
            "number": 42,
            "nested": {"array": [1, 2, 3]},
            "bool": True,
            "null": None,
        }

        # serialize
        json_str = StrictCanonicalCodec.serialize(obj)

        # deserialize
        result = StrictCanonicalCodec.deserialize(json_str)

        # 应该相等
        assert result == obj

    def test_roundtrip_preserves_order(self):
        """往返保持键顺序（规范化）"""
        obj = {"z": 1, "a": 2, "m": 3}

        json_str = StrictCanonicalCodec.serialize(obj)

        # 应该按字母顺序
        assert json_str == '{"a":2,"m":3,"z":1}'

        result = StrictCanonicalCodec.deserialize(json_str)
        assert result == obj

    def test_no_kernel_generated_json_deserialize_rejects(self):
        """内核不应生成自身 deserialize 会拒绝的 JSON"""
        # 这是一个元测试：确保 serialize 的输出能被 deserialize 接受

        valid_objects = [
            {"simple": "test"},
            {"nested": {"deep": {"value": 42}}},
            {"array": [1, 2, 3, "four"]},
            {"unicode": "你好世界"},
            {"tab": "hello\tworld"},  # 允许的控制字符
            {"newline": "hello\nworld"},
            {"return": "hello\rworld"},
        ]

        for obj in valid_objects:
            json_str = StrictCanonicalCodec.serialize(obj)
            # 应该能成功 deserialize
            result = StrictCanonicalCodec.deserialize(json_str)
            assert result == obj
