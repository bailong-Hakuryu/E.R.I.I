"""Security sanitizer for E.R.I.I. Engine.

Provides anti-prompt injection, path traversal validation, and PII scrubbing.
Follows Google Python Style Guide.
"""

import re
from typing import List


class SecuritySanitizer:
    """Security guard for sanitizing inputs, validating keys, and scrubbing PII."""

    # Regex patterns for prompt injection attempts
    INJECTION_PATTERNS: List[re.Pattern] = [
        re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
        re.compile(r"system\s*:\s*override", re.IGNORECASE),
        re.compile(r"\[INSTRUCTION\]", re.IGNORECASE),
        re.compile(r"<\s*script[^>]*>", re.IGNORECASE),
        re.compile(r"YOU\s+ARE\s+NOW\s+A\s+NEW\s+AI", re.IGNORECASE),
    ]

    # PII Scrubbing patterns
    EMAIL_PATTERN: re.Pattern = re.compile(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    )
    PHONE_PATTERN: re.Pattern = re.compile(
        r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
    )
    API_KEY_PATTERN: re.Pattern = re.compile(
        r"(?:sk-[a-zA-Z0-9]{32,}|api[_-]?key[_-]?[a-zA-Z0-9]{16,})", re.IGNORECASE
    )

    @classmethod
    def validate_key(cls, key_str: str, key_name: str = "ID") -> str:
        """Validates key identifiers against Path Traversal and illegal characters.

        Args:
            key_str: Key string to validate (e.g., user_id or agent_id).
            key_name: Human-readable name of key for error message.

        Returns:
            Sanitized valid key string.

        Raises:
            ValueError: If key contains invalid characters or path traversal sequences.
        """
        if not key_str or not isinstance(key_str, str):
            raise ValueError(f"{key_name} must be a non-empty string.")

        sanitized = key_str.strip()
        # Check for path traversal characters & null bytes
        if ".." in sanitized or "/" in sanitized or "\\" in sanitized or "\x00" in sanitized:
            raise ValueError(
                f"Security Warning: Invalid {key_name} '{key_str}' contains path traversal characters."
            )

        return sanitized

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """Sanitizes raw text to neutralize potential prompt injection constructs.

        Args:
            text: Input string.

        Returns:
            Sanitized text string with injection attempts disarmed.
        """
        if not text:
            return ""

        cleaned = text
        for pattern in cls.INJECTION_PATTERNS:
            cleaned = pattern.sub("[FILTERED_INSTRUCTION]", cleaned)

        return cleaned

    @classmethod
    def scrub_pii(cls, text: str) -> str:
        """Scrubs Sensitive Personally Identifiable Information (PII) from text.

        Args:
            text: Input string.

        Returns:
            Text with emails, phone numbers, and API tokens masked.
        """
        if not text:
            return ""

        scrubbed = cls.EMAIL_PATTERN.sub("[EMAIL_REDACTED]", text)
        scrubbed = cls.PHONE_PATTERN.sub("[PHONE_REDACTED]", scrubbed)
        scrubbed = cls.API_KEY_PATTERN.sub("[API_KEY_REDACTED]", scrubbed)
        return scrubbed
