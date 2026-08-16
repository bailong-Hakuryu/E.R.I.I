"""Simple validation script for credential manager implementation.

Run this to verify the credential manager works correctly without pytest.
"""

import os
import sys

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from erii.security.credential_manager import (
    CredentialManager,
    CredentialError,
    RedactingFormatter,
)


def _synthetic_key(prefix="sk-", fill="1", length=24):
    """Build a test credential without committing one literal."""

    return prefix + (fill * length)


def test_redaction():
    """Test key redaction."""
    print("Testing key redaction...")

    key = _synthetic_key()
    redacted = CredentialManager.redact_key(key)

    assert redacted == "sk-1***", f"Expected 'sk-1***', got '{redacted}'"
    assert "567890" not in redacted
    print(f"  ✓ Key redacted correctly: {redacted}")


def test_fingerprint():
    """Test key fingerprint."""
    print("\nTesting key fingerprint...")

    key1 = _synthetic_key(fill="1")
    key2 = _synthetic_key(fill="1")
    key3 = _synthetic_key(fill="2")

    fp1 = CredentialManager.get_key_fingerprint(key1)
    fp2 = CredentialManager.get_key_fingerprint(key2)
    fp3 = CredentialManager.get_key_fingerprint(key3)

    assert fp1 == fp2, "Same keys should produce same fingerprint"
    assert fp1 != fp3, "Different keys should produce different fingerprints"
    assert len(fp1) == 8, f"Fingerprint should be 8 chars, got {len(fp1)}"

    print(f"  ✓ Fingerprint for key1: {fp1}")
    print(f"  ✓ Fingerprint for key2: {fp2}")
    print(f"  ✓ Fingerprint for key3: {fp3}")


def test_key_detection():
    """Test key leakage detection."""
    print("\nTesting key leakage detection...")

    generic_key = ("a" * 32) + "123456"
    test_cases = [
        (f'api_key="{_synthetic_key()}"', True),
        (f'token="{_synthetic_key(prefix="token-", fill="a")}"', True),
        (f'api_key="{generic_key}"', True),
        ('Just normal text here', False),
    ]

    for text, should_detect in test_cases:
        detected = CredentialManager.detect_key_leakage(text)
        if should_detect:
            assert len(detected) > 0, f"Should detect key in: {text}"
            print(f"  ✓ Detected key in: '{text[:30]}...'")
        else:
            assert len(detected) == 0, f"Should not detect key in: {text}"
            print(f"  ✓ No key detected in: '{text}'")


def test_env_loading():
    """Test loading from environment."""
    print("\nTesting environment variable loading...")

    # Set a test key
    test_key = _synthetic_key()
    os.environ["TEST_API_KEY"] = test_key

    try:
        loaded_key = CredentialManager.get_api_key("test", env_var="TEST_API_KEY")
        assert loaded_key == test_key, f"Expected '{test_key}', got '{loaded_key}'"
        print("  ✓ Loaded key from TEST_API_KEY")
        print(f"  ✓ Key fingerprint: {CredentialManager.get_key_fingerprint(loaded_key)}")
    finally:
        del os.environ["TEST_API_KEY"]


def test_missing_key():
    """Test handling of missing key."""
    print("\nTesting missing key handling...")

    # Required key should raise error
    try:
        CredentialManager.get_api_key("missing", env_var="NONEXISTENT_KEY", required=True)
        assert False, "Should have raised CredentialError"
    except CredentialError as e:
        print(f"  ✓ Correctly raised CredentialError: {str(e)[:50]}...")

    # Optional key should return None
    result = CredentialManager.get_api_key("missing", env_var="NONEXISTENT_KEY", required=False)
    assert result is None, "Optional missing key should return None"
    print("  ✓ Optional missing key returned None")


def test_redacting_formatter():
    """Test log formatter redaction."""
    print("\nTesting RedactingFormatter...")

    import logging

    formatter = RedactingFormatter('%(message)s')
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='Using api_key="sk-1234567890abcdef" for request',
        args=(),
        exc_info=None
    )

    formatted = formatter.format(record)
    assert "sk-1234567890abcdef" not in formatted, "Original key should be redacted"
    assert "***" in formatted or "sk-1***" in formatted, "Should contain redaction marker"
    print("  ✓ Original: 'Using api_key=\"sk-1234567890abcdef\" for request'")
    print(f"  ✓ Redacted: '{formatted}'")


def main():
    """Run all validation tests."""
    print("=" * 60)
    print("Credential Manager Validation")
    print("=" * 60)

    try:
        test_redaction()
        test_fingerprint()
        test_key_detection()
        test_env_loading()
        test_missing_key()
        test_redacting_formatter()

        print("\n" + "=" * 60)
        print("✓ All validation tests passed!")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
