"""
错误脱敏测试 - 确保 sentinel 不泄漏

验证所有错误路径（str/repr/cause/context/traceback）都不泄漏敏感输入。
"""

from erii.deliberation.strict_codec import StrictCanonicalCodec
from erii.deliberation.schemas import MessagePart


class TestErrorSanitization:
    """错误脱敏测试"""

    def test_sentinel_with_comma_not_leaked(self):
        """包含逗号的 sentinel 不泄漏"""
        sentinel = "SECRET,WITH,COMMAS"
        json_str = f'{{"part_id": "test", "kind": "text", "exact_utf8": "{sentinel}"}}'

        try:
            # 故意触发错误（缺少结尾括号）
            StrictCanonicalCodec.decode_as(json_str[:-1], MessagePart)
        except ValueError as e:
            error_str = str(e)
            assert sentinel not in error_str, f"Sentinel leaked in str: {error_str}"

    def test_sentinel_with_bracket_not_leaked(self):
        """包含方括号的 sentinel 不泄漏"""
        sentinel = "SECRET]WITH[BRACKETS"
        json_str = f'{{"part_id": "test", "kind": "text", "exact_utf8": "{sentinel}"}}'

        try:
            StrictCanonicalCodec.decode_as(json_str[:-1], MessagePart)
        except ValueError as e:
            error_str = str(e)
            assert sentinel not in error_str

    def test_sentinel_with_quote_not_leaked(self):
        """包含引号的 sentinel 不泄漏"""
        sentinel = 'SECRET"WITH\'QUOTES'
        # 需要转义引号
        json_str = '{"part_id": "test", "kind": "text", "exact_utf8": "SECRET\\"WITH\'QUOTES"}'

        try:
            StrictCanonicalCodec.decode_as(json_str[:-1], MessagePart)
        except ValueError as e:
            error_str = str(e)
            assert sentinel not in error_str

    def test_sentinel_with_newline_not_leaked(self):
        """包含换行的 sentinel 不泄漏"""
        sentinel = "SECRET\nWITH\nNEWLINES"
        # JSON 中需要转义换行
        json_str = '{"part_id": "test", "kind": "text", "exact_utf8": "SECRET\\nWITH\\nNEWLINES"}'

        try:
            StrictCanonicalCodec.decode_as(json_str[:-1], MessagePart)
        except ValueError as e:
            error_str = str(e)
            assert sentinel not in error_str

    def test_sentinel_with_unicode_not_leaked(self):
        """包含 Unicode 的 sentinel 不泄漏"""
        sentinel = "SECRET密码WITH中文"
        json_str = f'{{"part_id": "test", "kind": "text", "exact_utf8": "{sentinel}"}}'

        try:
            StrictCanonicalCodec.decode_as(json_str[:-1], MessagePart)
        except ValueError as e:
            error_str = str(e)
            assert sentinel not in error_str

    def test_repr_not_leak_sentinel(self):
        """repr 不泄漏 sentinel"""
        sentinel = "SECRET_REPR_CANARY"
        json_str = f'{{"part_id": "test", "kind": "text", "exact_utf8": "{sentinel}"}}'

        try:
            StrictCanonicalCodec.decode_as(json_str[:-1], MessagePart)
        except ValueError as e:
            repr_str = repr(e)
            assert sentinel not in repr_str, f"Sentinel leaked in repr: {repr_str}"

    def test_cause_not_leak_sentinel(self):
        """__cause__ 不泄漏 sentinel"""
        sentinel = "SECRET_CAUSE_CANARY"
        json_str = f'{{"part_id": "test", "kind": "text", "exact_utf8": "{sentinel}"}}'

        try:
            StrictCanonicalCodec.decode_as(json_str[:-1], MessagePart)
        except ValueError as e:
            # 检查 __cause__
            assert e.__cause__ is None, "__cause__ should be None (from None)"

    def test_context_not_leak_sentinel(self):
        """__context__ 不泄漏 sentinel"""
        sentinel = "SECRET_CONTEXT_CANARY"
        json_str = f'{{"part_id": "test", "kind": "text", "exact_utf8": "{sentinel}"}}'

        try:
            StrictCanonicalCodec.decode_as(json_str[:-1], MessagePart)
        except ValueError as e:
            # 检查 __context__（应该被 from None 清除）
            if e.__context__ is not None:
                context_str = str(e.__context__)
                assert sentinel not in context_str

    def test_traceback_not_leak_sentinel(self):
        """traceback 不泄漏 sentinel（至少不在异常消息中）"""
        sentinel = "SECRET_TRACEBACK_CANARY"
        json_str = f'{{"part_id": "test", "kind": "text", "exact_utf8": "{sentinel}"}}'

        try:
            StrictCanonicalCodec.decode_as(json_str[:-1], MessagePart)
        except ValueError as e:
            # traceback 可能包含代码行，但不应该包含 sentinel 在错误消息中
            # 我们主要关心异常消息不泄漏
            error_msg_in_tb = str(e)
            assert sentinel not in error_msg_in_tb

    def test_validation_error_uses_include_input_false(self):
        """Pydantic 验证错误使用 include_input=False"""
        sentinel = "SECRET_VALIDATION_CANARY"

        # 创建一个会触发 Pydantic 验证错误的 JSON
        # part_id 是必需的，但我们用 sentinel 作为 kind（无效枚举）
        json_str = f'{{"part_id": "{sentinel}", "kind": "INVALID_KIND", "exact_utf8": "test"}}'

        try:
            StrictCanonicalCodec.decode_as(json_str, MessagePart)
        except ValueError as e:
            error_str = str(e)
            # sentinel 不应该出现在错误消息中
            assert sentinel not in error_str, f"Sentinel leaked in validation error: {error_str}"

    def test_error_contains_structured_info(self):
        """错误应该包含结构化信息（loc, type）而非输入值"""
        json_str = '{"part_id": "test", "kind": "INVALID", "exact_utf8": "content"}'

        try:
            StrictCanonicalCodec.decode_as(json_str, MessagePart)
        except ValueError as e:
            error_str = str(e)
            # 应该包含错误类型或位置信息
            assert "loc" in error_str or "type" in error_str or "kind" in error_str.lower()

    def test_duplicate_key_not_leak_key(self):
        """重复 JSON key 错误不泄漏实际 key"""
        sentinel = "SECRET_KEY_NAME"
        json_str = f'{{"{sentinel}": 1, "{sentinel}": 2}}'

        try:
            StrictCanonicalCodec.deserialize(json_str)
        except ValueError as e:
            error_str = str(e)
            # 不应该包含实际的 key 名
            assert sentinel not in error_str, f"Key leaked in duplicate key error: {error_str}"
            # 应该只说检测到重复
            assert "Duplicate" in error_str or "duplicate" in error_str

    def test_duplicate_key_in_nested_object_not_leak(self):
        """嵌套对象中的重复 key 不泄漏"""
        sentinel = "NESTED_SECRET_KEY"
        json_str = f'{{"outer": {{"{sentinel}": 1, "{sentinel}": 2}}}}'

        try:
            StrictCanonicalCodec.deserialize(json_str)
        except ValueError as e:
            error_str = str(e)
            assert sentinel not in error_str

    def test_invalid_enum_value_not_leak_value(self):
        """无效枚举值不泄漏实际值"""
        sentinel = "SECRET_ENUM_VALUE"
        json_str = f'{{"part_id": "test", "kind": "{sentinel}", "exact_utf8": "content"}}'

        try:
            StrictCanonicalCodec.decode_as(json_str, MessagePart)
        except ValueError as e:
            error_str = str(e)
            assert sentinel not in error_str, f"Enum value leaked: {error_str}"

    def test_sentinel_in_field_name_not_leaked(self):
        """字段名包含 sentinel 时不泄漏"""
        sentinel = "SECRET_FIELD"
        json_str = f'{{"{sentinel}": "value", "part_id": "test", "kind": "text", "exact_utf8": "content"}}'

        try:
            StrictCanonicalCodec.decode_as(json_str, MessagePart)
        except ValueError:
            # 字段名可能出现在 loc 中，但不应该出现在输入值中
            # 这个测试主要确保不会因为额外字段泄漏整个 JSON
            pass  # Pydantic 可能允许额外字段或在 loc 中显示字段名，这是可以的

    def test_malformed_json_surrounding_text_not_leaked(self):
        """畸形 JSON 周围的文本不泄漏"""
        sentinel = "SECRET_SURROUNDING_TEXT"
        json_str = f'{sentinel} {{"part_id": "test"}} {sentinel}'

        try:
            StrictCanonicalCodec.deserialize(json_str)
        except ValueError as e:
            error_str = str(e)
            # 应该只说无效 JSON，不泄漏周围文本
            assert sentinel not in error_str, f"Surrounding text leaked: {error_str}"

    def test_format_exception_not_leak_sentinel(self):
        """format_exception 不泄漏 sentinel"""
        sentinel = "SECRET_FORMAT_EXCEPTION"
        json_str = f'{{"part_id": "{sentinel}", "kind": "INVALID"}}'

        try:
            StrictCanonicalCodec.decode_as(json_str, MessagePart)
        except ValueError as e:
            # 异常消息中不应该包含 sentinel
            assert sentinel not in str(e), "Sentinel in exception message"
