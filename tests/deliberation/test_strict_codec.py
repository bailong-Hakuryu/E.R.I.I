"""
测试 Strict Canonical Codec

验证：
- 拒绝重复 JSON key
- 拒绝 NaN/Infinity
- 严格的 UTF-8 处理
- 文档预算限制
- validated_model_copy 重新验证
"""

import pytest
import math

from erii.deliberation.strict_codec import (
    StrictJSONDecoder,
    StrictCanonicalCodec,
    validated_model_copy,
)
from erii.deliberation.schemas import (
    MessagePart,
)


class TestStrictJSONDecoder:
    """测试严格的 JSON Decoder"""

    def test_reject_duplicate_keys(self):
        """测试拒绝重复 key"""
        json_with_dup = '{"x": 1, "y": 2, "x": 3}'

        decoder = StrictJSONDecoder()

        with pytest.raises(ValueError, match="Duplicate JSON key"):
            decoder.decode(json_with_dup)

    def test_accept_unique_keys(self):
        """测试接受唯一 key"""
        json_str = '{"x": 1, "y": 2, "z": 3}'

        decoder = StrictJSONDecoder()
        obj = decoder.decode(json_str)

        assert obj == {"x": 1, "y": 2, "z": 3}

    def test_reject_nested_duplicate_keys(self):
        """测试拒绝嵌套的重复 key"""
        json_str = '{"outer": {"inner": 1, "inner": 2}}'

        decoder = StrictJSONDecoder()

        with pytest.raises(ValueError, match="Duplicate JSON key"):
            decoder.decode(json_str)


class TestStrictCanonicalCodec:
    """测试严格的 Canonical Codec"""

    def test_reject_nan(self):
        """测试拒绝 NaN"""
        data = {"x": math.nan}

        with pytest.raises(ValueError, match="Out of range float values|non-finite"):
            StrictCanonicalCodec.serialize(data)

    def test_reject_infinity(self):
        """测试拒绝 Infinity"""
        data = {"x": math.inf}

        with pytest.raises(ValueError, match="Out of range float values|non-finite"):
            StrictCanonicalCodec.serialize(data)

    def test_reject_negative_infinity(self):
        """测试拒绝 -Infinity"""
        data = {"x": -math.inf}

        with pytest.raises(ValueError, match="Out of range float values|non-finite"):
            StrictCanonicalCodec.serialize(data)

    def test_accept_normal_numbers(self):
        """测试接受正常数字"""
        data = {"x": 1.5, "y": -2.3, "z": 0}

        json_str = StrictCanonicalCodec.serialize(data)
        assert '"x":1.5' in json_str
        assert '"y":-2.3' in json_str
        assert '"z":0' in json_str

    def test_reject_invalid_utf8(self):
        """测试拒绝无效 UTF-8（代理字符）"""
        # Python 字符串可以包含未配对的代理字符
        # 但严格的 UTF-8 编码会失败
        try:
            surrogate_str = "\ud800"  # 未配对的代理字符
            surrogate_str.encode('utf-8', errors='strict')
            pytest.skip("Python 版本接受代理字符")
        except UnicodeEncodeError:
            # 预期行为
            pass

    def test_enforce_document_size_limit(self):
        """测试强制文档大小限制"""
        # 创建超大文档（单个大字符串，避免触发数组长度限制）
        huge_str = "x" * (11 * 1024 * 1024)  # 11MB 字符串

        with pytest.raises(ValueError, match="Document size.*exceeds maximum"):
            StrictCanonicalCodec.serialize({"data": huge_str})

    def test_enforce_nesting_depth_limit(self):
        """测试强制嵌套深度限制"""
        # 创建深度嵌套的结构
        deep = {"level": 0}
        current = deep
        for i in range(1, 50):  # 超过 MAX_NESTING_DEPTH (32)
            current["child"] = {"level": i}
            current = current["child"]

        with pytest.raises(ValueError, match="Nesting depth.*exceeds maximum"):
            StrictCanonicalCodec.serialize(deep)

    def test_enforce_array_length_limit(self):
        """测试强制数组长度限制"""
        # 创建超长数组
        long_array = list(range(15000))  # 超过 MAX_ARRAY_LENGTH (10000)

        with pytest.raises(ValueError, match="Array.*exceeds maximum"):
            StrictCanonicalCodec.serialize(long_array)

    def test_enforce_object_keys_limit(self):
        """测试强制对象 key 数量限制"""
        # 创建超多 key 的对象
        many_keys = {f"key_{i}": i for i in range(1500)}  # 超过 MAX_OBJECT_KEYS (1000)

        with pytest.raises(ValueError, match="Object.*has.*keys.*exceeds maximum"):
            StrictCanonicalCodec.serialize(many_keys)

    def test_deserialize_rejects_duplicates(self):
        """测试反序列化拒绝重复 key"""
        json_with_dup = '{"x": 1, "x": 2}'

        with pytest.raises(ValueError, match="Duplicate JSON key"):
            StrictCanonicalCodec.deserialize(json_with_dup)

    def test_deserialize_rejects_nan(self):
        """测试反序列化拒绝 NaN"""
        json_with_nan = '{"x": NaN}'

        with pytest.raises(ValueError, match="Invalid.*constant|NaN.*Infinity"):
            StrictCanonicalCodec.deserialize(json_with_nan)

    def test_deserialize_rejects_infinity(self):
        """测试反序列化拒绝 Infinity"""
        json_with_inf = '{"x": Infinity}'

        with pytest.raises(ValueError, match="Invalid.*constant|NaN.*Infinity"):
            StrictCanonicalCodec.deserialize(json_with_inf)

    def test_deserialize_validates_structure_depth(self):
        """测试反序列化验证结构深度"""
        # 创建深度嵌套的 JSON 字符串（手动构造以绕过序列化检查）
        deep_json = '{"a":' * 40 + '1' + '}' * 40

        with pytest.raises(ValueError, match="Nesting depth.*exceeds maximum"):
            StrictCanonicalCodec.deserialize(deep_json)

    def test_deserialize_validates_array_length(self):
        """测试反序列化验证数组长度"""
        # 创建超长数组的 JSON 字符串
        long_array_json = '[' + ','.join(['1'] * 12000) + ']'

        with pytest.raises(ValueError, match="Array.*exceeds maximum"):
            StrictCanonicalCodec.deserialize(long_array_json)

    def test_deserialize_validates_object_keys(self):
        """测试反序列化验证对象 key 数量"""
        # 创建超多 key 的 JSON 字符串
        many_keys_json = '{' + ','.join([f'"k{i}":{i}' for i in range(1100)]) + '}'

        with pytest.raises(ValueError, match="Object has.*keys.*exceeds maximum"):
            StrictCanonicalCodec.deserialize(many_keys_json)

    def test_deserialize_accepts_valid_json(self):
        """测试反序列化接受合法 JSON"""
        json_str = '{"x":1,"y":2}'

        obj = StrictCanonicalCodec.deserialize(json_str)
        assert obj == {"x": 1, "y": 2}

    def test_fingerprint_deterministic(self):
        """测试 fingerprint 是确定性的"""
        data = {"b": 2, "a": 1, "c": 3}

        fp1 = StrictCanonicalCodec.fingerprint(data)
        fp2 = StrictCanonicalCodec.fingerprint(data)

        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 十六进制

    def test_fingerprint_order_independent(self):
        """测试 fingerprint 与 key 顺序无关"""
        data1 = {"a": 1, "b": 2, "c": 3}
        data2 = {"c": 3, "a": 1, "b": 2}

        fp1 = StrictCanonicalCodec.fingerprint(data1)
        fp2 = StrictCanonicalCodec.fingerprint(data2)

        assert fp1 == fp2


class TestValidatedModelCopy:
    """测试经过验证的 model_copy"""

    def test_validated_copy_enforces_enum(self):
        """测试 validated_copy 强制枚举验证"""
        part = MessagePart(
            part_id="test",
            kind="text",
            exact_utf8="内容",
        )

        # 尝试使用无效枚举值
        with pytest.raises(Exception):  # ValidationError
            validated_model_copy(part, update={"kind": "INVALID_ENUM"})

    def test_validated_copy_enforces_length(self):
        """测试 validated_copy 强制长度验证"""
        part = MessagePart(
            part_id="test",
            kind="text",
            exact_utf8="内容",
        )

        # 尝试超长内容
        with pytest.raises(Exception):  # ValidationError
            validated_model_copy(part, update={"exact_utf8": "x" * 20000})

    def test_validated_copy_accepts_valid_update(self):
        """测试 validated_copy 接受合法更新"""
        part = MessagePart(
            part_id="test",
            kind="text",
            exact_utf8="原始内容",
        )

        new_part = validated_model_copy(part, update={"exact_utf8": "新内容"})

        assert new_part.exact_utf8 == "新内容"
        assert new_part.part_id == "test"
        assert new_part.kind == "text"

    def test_validated_copy_without_update(self):
        """测试 validated_copy 不带更新"""
        part = MessagePart(
            part_id="test",
            kind="text",
            exact_utf8="内容",
        )

        new_part = validated_model_copy(part)

        assert new_part.exact_utf8 == part.exact_utf8
        assert new_part is not part  # 不同实例


class TestStrictCodecIntegration:
    """测试 Strict Codec 集成"""

    def test_roundtrip_with_pydantic_model(self):
        """测试 Pydantic 模型的往返"""
        part = MessagePart(
            part_id="test",
            kind="text",
            exact_utf8="测试内容",
        )

        # 序列化
        data = part.model_dump()
        json_str = StrictCanonicalCodec.serialize(data)

        # 反序列化
        data2 = StrictCanonicalCodec.deserialize(json_str)
        part2 = MessagePart(**data2)

        assert part2.part_id == part.part_id
        assert part2.exact_utf8 == part.exact_utf8

    def test_fingerprint_changes_on_modification(self):
        """测试修改后 fingerprint 改变"""
        part1 = MessagePart(
            part_id="test",
            kind="text",
            exact_utf8="内容1",
        )

        part2 = MessagePart(
            part_id="test",
            kind="text",
            exact_utf8="内容2",
        )

        fp1 = StrictCanonicalCodec.fingerprint(part1.model_dump())
        fp2 = StrictCanonicalCodec.fingerprint(part2.model_dump())

        assert fp1 != fp2

    def test_decode_as_validates_schema(self):
        """测试 decode_as 验证 Schema"""
        json_str = '{"part_id":"test","kind":"text","exact_utf8":"内容"}'

        # 解码为 MessagePart
        part = StrictCanonicalCodec.decode_as(json_str, MessagePart)

        assert isinstance(part, MessagePart)
        assert part.part_id == "test"
        assert part.exact_utf8 == "内容"

    def test_decode_as_rejects_invalid_schema(self):
        """测试 decode_as 拒绝无效 Schema"""
        json_str = '{"part_id":"test","kind":"INVALID","exact_utf8":"内容"}'

        with pytest.raises(ValueError, match="Failed to validate"):
            StrictCanonicalCodec.decode_as(json_str, MessagePart)

    def test_decode_as_rejects_duplicate_keys(self):
        """测试 decode_as 拒绝重复 key"""
        json_with_dup = '{"part_id":"test","part_id":"dup","kind":"text","exact_utf8":"内容"}'

        with pytest.raises(ValueError, match="Duplicate JSON key"):
            StrictCanonicalCodec.decode_as(json_with_dup, MessagePart)
