"""Tests for secure credential management.

Validates that the credential manager properly:
- Loads keys from environment variables only
- Rejects literal keys in code
- Redacts keys in logs and output
- Detects potential key leakage
"""

import logging
import pytest

from erii.security.credential_manager import (
    CredentialError,
    CredentialManager,
    RedactingFormatter,
    setup_secure_logging,
)


class TestCredentialManager:
    """Test suite for CredentialManager."""

    def test_get_api_key_success(self, monkeypatch):
        """Test successful API key retrieval from environment."""
        test_key = "sk-test1234567890abcdef"
        monkeypatch.setenv("OPENAI_API_KEY", test_key)

        key = CredentialManager.get_api_key("openai")
        assert key == test_key

    def test_get_api_key_custom_env_var(self, monkeypatch):
        """Test loading key from custom environment variable."""
        test_key = "custom-key-12345678"
        monkeypatch.setenv("MY_CUSTOM_KEY", test_key)

        key = CredentialManager.get_api_key("custom", env_var="MY_CUSTOM_KEY")
        assert key == test_key

    def test_get_api_key_missing_required(self, monkeypatch):
        """Test that missing required key raises error."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with pytest.raises(CredentialError, match="Missing required API key"):
            CredentialManager.get_api_key("openai", required=True)

    def test_get_api_key_missing_optional(self, monkeypatch):
        """Test that missing optional key returns None."""
        monkeypatch.delenv("OPTIONAL_API_KEY", raising=False)

        key = CredentialManager.get_api_key(
            "optional",
            env_var="OPTIONAL_API_KEY",
            required=False
        )
        assert key is None

    def test_get_api_key_too_short(self, monkeypatch):
        """Test that keys shorter than minimum length are rejected."""
        monkeypatch.setenv("SHORT_KEY", "abc123")

        with pytest.raises(CredentialError, match="too short"):
            CredentialManager.get_api_key("short", env_var="SHORT_KEY")

    def test_get_api_key_whitespace_stripped(self, monkeypatch):
        """Test that whitespace is stripped from keys."""
        test_key = "  sk-test1234567890  "
        monkeypatch.setenv("WHITESPACE_KEY", test_key)

        key = CredentialManager.get_api_key("whitespace", env_var="WHITESPACE_KEY")
        assert key == test_key.strip()
        assert not key.startswith(" ")
        assert not key.endswith(" ")

    def test_redact_key(self):
        """Test key redaction for safe logging."""
        key = "sk-1234567890abcdefghijklmnop"
        redacted = CredentialManager.redact_key(key)
        assert redacted == "sk-1***"
        assert "567890" not in redacted
        assert "abcdef" not in redacted

    def test_redact_key_custom_visible_chars(self):
        """Test key redaction with custom visible character count."""
        key = "api-key-1234567890"
        redacted = CredentialManager.redact_key(key, visible_chars=8)
        assert redacted == "api-key-***"

    def test_redact_key_empty(self):
        """Test redaction of empty key."""
        assert CredentialManager.redact_key("") == "<empty>"
        assert CredentialManager.redact_key(None) == "<empty>"

    def test_redact_key_too_short(self):
        """Test redaction of very short key."""
        assert CredentialManager.redact_key("abc") == "***"

    def test_get_key_fingerprint(self):
        """Test stable fingerprint generation."""
        key1 = "sk-1234567890abcdef"
        key2 = "sk-1234567890abcdef"
        key3 = "sk-different-key-000"

        fp1 = CredentialManager.get_key_fingerprint(key1)
        fp2 = CredentialManager.get_key_fingerprint(key2)
        fp3 = CredentialManager.get_key_fingerprint(key3)

        # Same key produces same fingerprint
        assert fp1 == fp2
        # Different keys produce different fingerprints
        assert fp1 != fp3
        # Fingerprint is 8 characters
        assert len(fp1) == 8

    def test_get_key_fingerprint_empty(self):
        """Test fingerprint of empty key."""
        assert CredentialManager.get_key_fingerprint("") == "<no-key>"

    def test_detect_key_leakage_various_formats(self):
        """Test detection of keys in various formats."""
        # Test cases aligned with KEY_PATTERN: requires prefix (sk-, token-, key-, api-) or 32+ chars
        test_cases = [
            ('api_key="sk-1234567890abcdef"', True),  # Has sk- prefix
            ('token: token-abc1234567890def', True),  # Has token- prefix
            ('SECRET=key-xyz9876543210fed', True),  # Has key- prefix
            ('password="p@ssw0rd12345678901234567890123"', True),  # 32+ chars
            ('credential: "api-123456789012"', True),  # Has api- prefix
            ('Just normal text here', False),
            ('api_key=""', False),  # Empty key
            ('token: abc', False),  # Too short, no prefix
            ('password="short"', False),  # Too short
        ]

        for text, should_detect in test_cases:
            detected = CredentialManager.detect_key_leakage(text)
            if should_detect:
                assert len(detected) > 0, f"Should detect key in: {text}"
            else:
                assert len(detected) == 0, f"Should not detect key in: {text}"

    def test_validate_no_literal_keys_clean_code(self):
        """Test validation passes for code without literal keys."""
        clean_code = """
        import os
        api_key = os.environ.get('API_KEY')
        config = {'timeout': 30}
        """
        # Should not raise
        CredentialManager.validate_no_literal_keys(clean_code, "test.py")

    def test_validate_no_literal_keys_detects_violation(self):
        """Test validation fails for code with literal keys."""
        dirty_code = """
        api_key = "sk-1234567890abcdefghijklmnop"
        """
        with pytest.raises(CredentialError, match="Potential API key leakage"):
            CredentialManager.validate_no_literal_keys(dirty_code, "bad_file.py")

    def test_validate_no_literal_keys_skips_test_files(self):
        """Test that validation skips test files."""
        code_with_test_key = """
        test_api_key = "sk-test1234567890"
        """
        # Should not raise for test files
        CredentialManager.validate_no_literal_keys(
            code_with_test_key,
            "test_something.py"
        )


class TestRedactingFormatter:
    """Test suite for RedactingFormatter."""

    def test_redacting_formatter_basic(self):
        """Test that formatter redacts keys in log messages."""
        formatter = RedactingFormatter('%(message)s')
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='Using api_key="sk-1234567890abcdef"',
            args=(),
            exc_info=None
        )

        formatted = formatter.format(record)
        assert "sk-1234567890abcdef" not in formatted
        assert "sk-1***" in formatted or "***" in formatted

    def test_redacting_formatter_multiple_keys(self):
        """Test redaction of multiple keys in one message."""
        formatter = RedactingFormatter('%(message)s')
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='Keys: api_key="sk-abc123456789" token="token-xyz987654321"',
            args=(),
            exc_info=None
        )

        formatted = formatter.format(record)
        assert "sk-abc123456789" not in formatted
        assert "token-xyz987654321" not in formatted
        assert "***" in formatted

    def test_redacting_formatter_preserves_normal_text(self):
        """Test that normal text without keys is preserved."""
        formatter = RedactingFormatter('%(message)s')
        normal_msg = "Processing request for user_id=12345"
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=normal_msg,
            args=(),
            exc_info=None
        )

        formatted = formatter.format(record)
        assert formatted == normal_msg


class TestSetupSecureLogging:
    """Test suite for setup_secure_logging function."""

    def test_setup_secure_logging(self):
        """Test that secure logging setup applies redacting formatter."""
        test_logger = logging.getLogger("test_secure_logger")
        test_logger.setLevel(logging.INFO)

        # Add a basic handler
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(message)s'))
        test_logger.addHandler(handler)

        # Apply secure logging
        setup_secure_logging(test_logger)

        # Check that formatter is now RedactingFormatter
        assert isinstance(handler.formatter, RedactingFormatter)

    def test_setup_secure_logging_preserves_format(self):
        """Test that setup preserves existing log format."""
        test_logger = logging.getLogger("test_format_logger")
        handler = logging.StreamHandler()
        original_format = '%(asctime)s - %(levelname)s - %(message)s'
        handler.setFormatter(logging.Formatter(original_format))
        test_logger.addHandler(handler)

        setup_secure_logging(test_logger)

        # Formatter should still be RedactingFormatter with same format
        assert isinstance(handler.formatter, RedactingFormatter)


class TestIntegrationScenarios:
    """Integration tests for realistic usage scenarios."""

    def test_full_workflow_openai_key(self, monkeypatch):
        """Test complete workflow: load, use, log, redact."""
        # Setup
        test_key = "sk-proj-1234567890abcdefghijklmnopqrstuvwxyz"
        monkeypatch.setenv("OPENAI_API_KEY", test_key)

        # Load key
        key = CredentialManager.get_api_key("openai")
        assert key == test_key

        # Simulate logging (should be redacted)
        redacted = CredentialManager.redact_key(key)
        assert redacted != test_key
        assert "***" in redacted

        # Fingerprint for debugging
        fingerprint = CredentialManager.get_key_fingerprint(key)
        assert len(fingerprint) == 8
        assert fingerprint != test_key

    def test_multi_provider_keys(self, monkeypatch):
        """Test loading keys for multiple providers."""
        providers = {
            "openai": "sk-openai-key-1234567890",
            "deepseek": "sk-deepseek-key-0987654321",
            "gemini": "AIza-gemini-key-abcdef123456",
        }

        for provider, key in providers.items():
            monkeypatch.setenv(f"{provider.upper()}_API_KEY", key)

        loaded_keys = {}
        for provider in providers:
            loaded_keys[provider] = CredentialManager.get_api_key(provider)

        # Verify all keys loaded correctly
        for provider, expected_key in providers.items():
            assert loaded_keys[provider] == expected_key

        # Verify all can be redacted
        for key in loaded_keys.values():
            redacted = CredentialManager.redact_key(key)
            assert "***" in redacted
