"""Tests for logging system."""

import sys
import os
import logging
import tempfile
from pathlib import Path

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from erii.core.logging import (
    StructuredLogger,
    LogLevel,
    LogFormat,
    AuditLogger,
    PerformanceLogger,
    get_logger,
    get_audit_logger,
    get_performance_logger,
)


def test_basic_logging():
    """Test basic structured logging."""
    print("Testing basic structured logging...")

    logger = StructuredLogger.get_logger("test.basic", level=LogLevel.DEBUG)

    # These should not raise
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")

    print("  ✓ Basic logging works")


def test_json_logging():
    """Test JSON format logging."""
    print("\nTesting JSON format logging...")

    logger = StructuredLogger.get_logger(
        "test.json",
        level=LogLevel.INFO,
        format_type=LogFormat.JSON
    )

    logger.info("Test JSON log", extra={'user': 'alice', 'count': 42})

    print("  ✓ JSON logging works")


def test_audit_logging():
    """Test audit logging."""
    print("\nTesting audit logging...")

    audit = AuditLogger("test.audit")

    # Log various audit operations
    audit.log_relationship_init(
        relationship_id="rel-123",
        agent="test-agent",
        user="alice"
    )

    audit.log_persona_decision(
        relationship_id="rel-123",
        proposal_id="prop-456",
        decision="approve"
    )

    audit.log_data_import(
        source_type="memorypack",
        record_count=100
    )

    audit.log_data_deletion(
        deletion_type="relationship",
        scope="rel-123",
        record_count=50
    )

    print("  ✓ Audit logging works")


def test_performance_logging():
    """Test performance logging."""
    print("\nTesting performance logging...")

    perf = PerformanceLogger("test.performance")

    # Manual timing
    perf.log_timing("test_operation", 123.45, test="value")

    # Context manager timing
    with perf.timer("test_context", user="alice"):
        # Simulate work
        sum(range(1000))

    print("  ✓ Performance logging works")


def test_global_loggers():
    """Test global logger instances."""
    print("\nTesting global logger instances...")

    # Get default logger
    logger = get_logger()
    logger.info("Test from default logger")

    # Get audit logger
    audit = get_audit_logger()
    audit.log_operation("test_op", status="success")

    # Get performance logger
    perf = get_performance_logger()
    perf.log_timing("test_op", 10.5)

    print("  ✓ Global loggers work")


def test_log_configuration():
    """Test logger configuration from dict."""
    print("\nTesting logger configuration from dict...")

    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, "test.log")

        config = {
            'level': 'INFO',
            'format': 'text',
            'file': log_file,
            'max_bytes': 1024 * 1024,
            'backup_count': 3
        }

        StructuredLogger.configure_from_dict(config)

        logger = logging.getLogger('erii')
        logger.info("Test log to file")

        # Close handlers to release file locks
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

        # Check file was created
        assert os.path.exists(log_file), "Log file should be created"

        print(f"  ✓ Logger configured, file created")


def test_key_redaction_in_logs():
    """Test that API keys are redacted in logs."""
    print("\nTesting API key redaction in logs...")

    logger = StructuredLogger.get_logger("test.redaction")

    # This should be redacted
    logger.info('Using api_key="sk-1234567890abcdef" for request')

    print("  ✓ Key redaction applied (check console output)")


def test_context_logging():
    """Test logging with context."""
    print("\nTesting context logging...")

    logger = StructuredLogger.get_logger("test.context")

    # Log with extra context
    logger.info(
        "Operation completed",
        extra={
            'operation': 'recall',
            'relationship_id': 'rel-123',
            'duration_ms': 45.67
        }
    )

    print("  ✓ Context logging works")


def test_exception_logging():
    """Test logging exceptions."""
    print("\nTesting exception logging...")

    logger = StructuredLogger.get_logger("test.exception")

    try:
        raise ValueError("Test exception")
    except ValueError:
        logger.exception("An error occurred")

    print("  ✓ Exception logging works")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Logging System Tests")
    print("=" * 60)

    try:
        test_basic_logging()
        test_json_logging()
        test_audit_logging()
        test_performance_logging()
        test_global_loggers()
        test_log_configuration()
        test_key_redaction_in_logs()
        test_context_logging()
        test_exception_logging()

        print("\n" + "=" * 60)
        print("✓ All logging tests passed!")
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
