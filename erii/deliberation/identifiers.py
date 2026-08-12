"""Shared validation for Character Deliberation identifiers."""

from __future__ import annotations

import unicodedata


def validate_identifier(value: str, field_name: str, *, max_length: int = 256) -> str:
    """Return *value* after rejecting ambiguous or invisible identifier text."""
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    if not value or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    if value != value.strip():
        raise ValueError(f"{field_name} must not have surrounding whitespace")
    if len(value) > max_length:
        raise ValueError(f"{field_name} length must not exceed {max_length}")

    for index, character in enumerate(value):
        codepoint = ord(character)
        category = unicodedata.category(character)
        if codepoint in (0x2028, 0x2029):
            raise ValueError(
                f"{field_name} 包含行/段分隔符 U+{codepoint:04X} 于位置 {index}"
            )
        if codepoint <= 0x1F or 0x7F <= codepoint <= 0x9F:
            label = "NUL" if codepoint == 0 else "控制字符"
            raise ValueError(
                f"{field_name} 包含 {label} U+{codepoint:04X} 于位置 {index}"
            )
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError(
                f"{field_name} 包含孤立代理 surrogate U+{codepoint:04X} 于位置 {index}"
            )
        if category in {"Cf", "Cc", "Cs"}:
            raise ValueError(
                f"{field_name} 包含 Unicode {category} 字符 U+{codepoint:04X} 于位置 {index}"
            )
    return value


__all__ = ["validate_identifier"]
