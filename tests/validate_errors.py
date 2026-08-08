"""Tests for enhanced error handling system."""

import sys
import os

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from erii.errors import (
    ERIIError,
    ErrorCode,
    ErrorSeverity,
    StorageError,
    StorageIntegrityError,
    APIConnectionError,
    APIRateLimitError,
    ValidationError,
    RelationshipNotFoundError,
)


def test_basic_error():
    """Test basic error creation."""
    print("Testing basic error creation...")

    error = ERIIError(
        "Something went wrong",
        code=ErrorCode.INTERNAL_ERROR,
        severity=ErrorSeverity.HIGH
    )

    assert error.code == ErrorCode.INTERNAL_ERROR
    assert error.severity == ErrorSeverity.HIGH
    assert "Something went wrong" in str(error)
    assert "E9000" in str(error)  # Error code should appear

    print("  ✓ Basic error created correctly")


def test_error_with_context():
    """Test error with context."""
    print("\nTesting error with context...")

    error = StorageError(
        "Write failed",
        code=ErrorCode.STORAGE_WRITE_FAILED,
        context={
            'file_path': '/tmp/test.db',
            'operation': 'write',
            'bytes': 1024
        }
    )

    assert error.context['file_path'] == '/tmp/test.db'
    assert 'Context:' in str(error)

    print(f"  ✓ Error with context: {error}")


def test_error_with_recovery_hint():
    """Test error with recovery hint."""
    print("\nTesting error with recovery hint...")

    error = StorageIntegrityError("Database corrupted")

    assert error.recovery_hint is not None
    assert 'Recovery:' in str(error)
    assert error.severity == ErrorSeverity.CRITICAL

    print(f"  ✓ Error with recovery hint: {error}")


def test_error_with_cause():
    """Test error wrapping another exception."""
    print("\nTesting error with cause...")

    try:
        raise ValueError("Original error")
    except ValueError as e:
        error = StorageError(
            "Failed to parse data",
            cause=e
        )

        assert error.cause is not None
        assert 'Caused by:' in str(error)
        assert 'ValueError' in str(error)

    print(f"  ✓ Error with cause: {error}")


def test_api_errors():
    """Test API-specific errors."""
    print("\nTesting API errors...")

    # Connection error
    conn_error = APIConnectionError(
        "Failed to connect to https://api.example.com"
    )
    assert conn_error.code == ErrorCode.API_CONNECTION_FAILED
    assert conn_error.severity == ErrorSeverity.HIGH
    print(f"  ✓ Connection error: {conn_error.code}")

    # Rate limit error
    rate_error = APIRateLimitError(
        "Rate limit exceeded: 1000 requests per hour"
    )
    assert rate_error.code == ErrorCode.API_RATE_LIMIT
    assert rate_error.severity == ErrorSeverity.MEDIUM
    print(f"  ✓ Rate limit error: {rate_error.code}")


def test_error_to_dict():
    """Test error serialization to dict."""
    print("\nTesting error serialization...")

    error = ValidationError(
        "Invalid input",
        context={'field': 'email', 'value': 'invalid'},
        recovery_hint="Provide a valid email address"
    )

    error_dict = error.to_dict()

    assert 'error' in error_dict
    assert error_dict['error']['code'] == ErrorCode.VALIDATION_FAILED
    assert error_dict['error']['message'] == "Invalid input"
    assert error_dict['error']['context']['field'] == 'email'

    print(f"  ✓ Serialized to dict: {error_dict}")


def test_error_hierarchy():
    """Test error inheritance."""
    print("\nTesting error hierarchy...")

    # StorageError is ERIIError
    storage_error = StorageError("Storage failed")
    assert isinstance(storage_error, ERIIError)
    assert isinstance(storage_error, RuntimeError)

    # APIConnectionError is APIError and ERIIError
    api_error = APIConnectionError("Connection failed")
    assert isinstance(api_error, ERIIError)

    # RelationshipNotFoundError is LookupError
    rel_error = RelationshipNotFoundError("Relationship not found")
    assert isinstance(rel_error, LookupError)
    assert isinstance(rel_error, ERIIError)

    print("  ✓ Error hierarchy correct")


def test_sensitive_data_redaction():
    """Test that sensitive data is redacted from error strings."""
    print("\nTesting sensitive data redaction...")

    error = StorageError(
        "Authentication failed",
        context={
            'user': 'alice',
            'api_key': 'sk-secret-key-12345',
            'password': 'hunter2',
            'file_path': '/tmp/data.db'
        }
    )

    error_str = str(error)

    # Sensitive keys should not appear
    assert 'sk-secret-key-12345' not in error_str
    assert 'hunter2' not in error_str

    # Non-sensitive keys should appear
    assert 'alice' in error_str or 'user' in error_str
    assert 'file_path' in error_str or '/tmp/data.db' in error_str

    print("  ✓ Sensitive data redacted")


def test_error_codes():
    """Test error code enumeration."""
    print("\nTesting error codes...")

    codes = [
        ErrorCode.STORAGE_INTEGRITY,
        ErrorCode.API_TIMEOUT,
        ErrorCode.VALIDATION_FAILED,
        ErrorCode.RELATIONSHIP_NOT_FOUND,
    ]

    for code in codes:
        assert isinstance(code, str)
        assert code.startswith('E')
        print(f"  ✓ Code: {code}")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Enhanced Error Handling Tests")
    print("=" * 60)

    try:
        test_basic_error()
        test_error_with_context()
        test_error_with_recovery_hint()
        test_error_with_cause()
        test_api_errors()
        test_error_to_dict()
        test_error_hierarchy()
        test_sensitive_data_redaction()
        test_error_codes()

        print("\n" + "=" * 60)
        print("✓ All error handling tests passed!")
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
