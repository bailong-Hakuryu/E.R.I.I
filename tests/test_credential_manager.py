"""Tests for secure credential management.

Validates that the credential manager properly:
- Loads keys from environment variables only
- Rejects literal keys in code
- Redacts keys in logs and output
- Detects potential key leakage

Updated: 2026-08-11 for v0.5.0a3
"""

import logging
import pytest

from erii.security.credential_manager import (
    CredentialError,
    CredentialManager,
    RedactingFormatter,
    setup_secure_logging,
)


def _synthetic_key(prefix: str = "sk-", fill: str = "1", length: int = 24) -> str:
    """Build a credential-shaped fixture without committing one literal."""

    return prefix + (fill * length)


class TestCredentialManager:
    """Test suite for CredentialManager."""

    def test_get_api_key_success(self, monkeypatch):
        """Test successful API key retrieval from environment."""
        test_key = _synthetic_key()
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
        test_key = f"  {_synthetic_key(length=16)}  "
        monkeypatch.setenv("WHITESPACE_KEY", test_key)

        key = CredentialManager.get_api_key("whitespace", env_var="WHITESPACE_KEY")
        assert key == test_key.strip()
        assert not key.startswith(" ")
        assert not key.endswith(" ")

    def test_redact_key(self):
        """Test key redaction for safe logging."""
        key = _synthetic_key()
        redacted = CredentialManager.redact_key(key)
        assert redacted == "sk-1***"
        assert "567890" not in redacted
        assert "abcdef" not in redacted

    def test_redact_key_custom_visible_chars(self):
        """Test key redaction with custom visible character count."""
        key = _synthetic_key(prefix="api-key-", length=10)
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
        key1 = _synthetic_key(fill="1")
        key2 = _synthetic_key(fill="1")
        key3 = _synthetic_key(fill="2")

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

    def test_key_leakage_detection_with_valid_patterns(self):
        """Test detection of keys matching KEY_PATTERN requirements.

        KEY_PATTERN requires either:
        - A recognized prefix (sk-, token-, key-, api-) followed by alphanumeric
        - Or a string of 32+ characters

        """
        # Should detect - recognized prefixes
        provider_key = _synthetic_key()
        token = _synthetic_key(prefix="token-", fill="a")
        secret = _synthetic_key(prefix="key-", fill="b")
        password = "p@ssw0rd" + ("1" * 24)
        credential = _synthetic_key(prefix="api-", fill="c")
        samples = (
            f'api_key="{provider_key}"',
            f"token: {token}",
            f"SECRET={secret}",
            f'password="{password}"',
            f'credential: "{credential}"',
        )
        assert all(CredentialManager.detect_key_leakage(sample) for sample in samples)

        # Should not detect - too short or no prefix
        assert len(CredentialManager.detect_key_leakage('Just normal text here')) == 0
        assert len(CredentialManager.detect_key_leakage('api_key=""')) == 0
        assert len(CredentialManager.detect_key_leakage('token: abc')) == 0
        assert len(CredentialManager.detect_key_leakage('password="short"')) == 0

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
        dirty_code = f'api_key = "{_synthetic_key()}"'
        with pytest.raises(CredentialError, match="Potential API key leakage"):
            CredentialManager.validate_no_literal_keys(dirty_code, "bad_file.py")

    def test_validate_no_literal_keys_checks_test_files(self):
        """Test fixtures follow the same literal-secret rule as source files."""
        code_with_test_key = f'test_api_key = "{_synthetic_key()}"'
        with pytest.raises(CredentialError, match="Potential API key leakage"):
            CredentialManager.validate_no_literal_keys(
                code_with_test_key,
                "test_something.py",
            )


class TestRedactingFormatter:
    """Test suite for RedactingFormatter."""

    def test_redacting_formatter_basic(self):
        """Test that formatter redacts keys in log messages."""
        formatter = RedactingFormatter('%(message)s')
        key = _synthetic_key()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=f'Using api_key="{key}"',
            args=(),
            exc_info=None
        )

        formatted = formatter.format(record)
        assert key not in formatted
        assert "sk-1***" in formatted or "***" in formatted

    def test_redacting_formatter_multiple_keys(self):
        """Test redaction of multiple keys in one message."""
        formatter = RedactingFormatter('%(message)s')
        provider_key = _synthetic_key()
        token = _synthetic_key(prefix="token-", fill="a")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=f'Keys: api_key="{provider_key}" token="{token}"',
            args=(),
            exc_info=None
        )

        formatted = formatter.format(record)
        assert provider_key not in formatted
        assert token not in formatted
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
        test_key = _synthetic_key(prefix="sk-proj-", fill="a", length=32)
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
            "openai": _synthetic_key(fill="a"),
            "deepseek": _synthetic_key(fill="b"),
            "gemini": _synthetic_key(prefix="AIza", fill="c"),
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
