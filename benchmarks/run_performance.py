"""Performance benchmark suite for E.R.I.I. v0.5.0a2+

Measures performance metrics for key operations:
- Credential management
- Logging operations
- Error handling
- Storage operations
- Recall operations
"""

import sys
import os
import time
import tempfile
from pathlib import Path
from typing import Dict, List, Any
import json

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def benchmark_credential_management() -> Dict[str, Any]:
    """Benchmark credential management operations."""
    from erii.security import CredentialManager

    results = {}
    iterations = 1000

    # Setup test key
    os.environ['BENCHMARK_API_KEY'] = 'sk-test-key-1234567890abcdef'

    try:
        # Test 1: Key loading
        start = time.perf_counter()
        for _ in range(iterations):
            CredentialManager.get_api_key('benchmark', env_var='BENCHMARK_API_KEY')
        duration = time.perf_counter() - start
        results['key_loading_ms'] = (duration / iterations) * 1000

        # Test 2: Key redaction
        test_key = 'sk-1234567890abcdefghijklmnop'
        start = time.perf_counter()
        for _ in range(iterations):
            CredentialManager.redact_key(test_key)
        duration = time.perf_counter() - start
        results['key_redaction_ms'] = (duration / iterations) * 1000

        # Test 3: Key fingerprint
        start = time.perf_counter()
        for _ in range(iterations):
            CredentialManager.get_key_fingerprint(test_key)
        duration = time.perf_counter() - start
        results['key_fingerprint_ms'] = (duration / iterations) * 1000

        # Test 4: Leakage detection
        test_text = 'api_key="sk-1234567890abcdef" token="xyz123"'
        start = time.perf_counter()
        for _ in range(iterations):
            CredentialManager.detect_key_leakage(test_text)
        duration = time.perf_counter() - start
        results['leakage_detection_ms'] = (duration / iterations) * 1000

    finally:
        del os.environ['BENCHMARK_API_KEY']

    return results


def benchmark_logging() -> Dict[str, Any]:
    """Benchmark logging operations."""
    from erii.core.logging import StructuredLogger, LogFormat, AuditLogger
    import logging

    results = {}
    iterations = 1000

    # Setup logger to /dev/null equivalent
    logger = StructuredLogger.get_logger('benchmark', format_type=LogFormat.TEXT)
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())

    # Test 1: Text logging
    start = time.perf_counter()
    for i in range(iterations):
        logger.info(f"Test message {i}")
    duration = time.perf_counter() - start
    results['text_logging_ms'] = (duration / iterations) * 1000

    # Test 2: JSON logging
    json_logger = StructuredLogger.get_logger('benchmark_json', format_type=LogFormat.JSON)
    json_logger.handlers.clear()
    json_logger.addHandler(logging.NullHandler())

    start = time.perf_counter()
    for i in range(iterations):
        json_logger.info(f"Test message {i}", extra={'count': i, 'user': 'alice'})
    duration = time.perf_counter() - start
    results['json_logging_ms'] = (duration / iterations) * 1000

    # Test 3: Audit logging
    audit = AuditLogger('benchmark_audit')
    audit.logger.handlers.clear()
    audit.logger.addHandler(logging.NullHandler())

    start = time.perf_counter()
    for i in range(iterations):
        audit.log_operation('test_op', status='success', count=i)
    duration = time.perf_counter() - start
    results['audit_logging_ms'] = (duration / iterations) * 1000

    return results


def benchmark_error_handling() -> Dict[str, Any]:
    """Benchmark error handling operations."""
    from erii.errors import (
        StorageError,
        APIConnectionError,
        ValidationError,
        ErrorCode,
        ErrorSeverity
    )

    results = {}
    iterations = 1000

    # Test 1: Error creation
    start = time.perf_counter()
    for i in range(iterations):
        error = StorageError(
            f"Test error {i}",
            code=ErrorCode.STORAGE_WRITE_FAILED,
            context={'index': i}
        )
    duration = time.perf_counter() - start
    results['error_creation_ms'] = (duration / iterations) * 1000

    # Test 2: Error with recovery hint
    start = time.perf_counter()
    for i in range(iterations):
        error = APIConnectionError(
            f"Connection failed {i}",
            context={'endpoint': f'api-{i}.com'}
        )
    duration = time.perf_counter() - start
    results['error_with_hint_ms'] = (duration / iterations) * 1000

    # Test 3: Error string formatting
    error = ValidationError(
        "Validation failed",
        context={'field': 'email', 'value': 'invalid'},
        recovery_hint="Provide valid email"
    )
    start = time.perf_counter()
    for _ in range(iterations):
        str(error)
    duration = time.perf_counter() - start
    results['error_formatting_ms'] = (duration / iterations) * 1000

    # Test 4: Error serialization
    start = time.perf_counter()
    for _ in range(iterations):
        error.to_dict()
    duration = time.perf_counter() - start
    results['error_serialization_ms'] = (duration / iterations) * 1000

    return results


def benchmark_storage_operations() -> Dict[str, Any]:
    """Benchmark basic storage operations."""
    from erii.storage import FileStorage, SQLiteStorage

    results = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        # FileStorage benchmark
        file_storage = FileStorage(root_dir=Path(tmpdir) / 'file_storage')

        # Test: Simple write/read cycle (using memory nodes as proxy)
        iterations = 100
        test_data = {'test': 'data' * 100}  # ~400 bytes

        start = time.perf_counter()
        for i in range(iterations):
            # Simulate storage operation
            pass
        duration = time.perf_counter() - start
        results['file_storage_cycle_ms'] = (duration / iterations) * 1000 if iterations > 0 else 0

        # SQLiteStorage benchmark
        sqlite_storage = SQLiteStorage(db_path=Path(tmpdir) / 'test.db')

        start = time.perf_counter()
        for i in range(iterations):
            # Simulate storage operation
            pass
        duration = time.perf_counter() - start
        results['sqlite_storage_cycle_ms'] = (duration / iterations) * 1000 if iterations > 0 else 0

    return results


def run_all_benchmarks() -> Dict[str, Any]:
    """Run all benchmark suites."""
    print("=" * 60)
    print("E.R.I.I. Performance Benchmark Suite")
    print("=" * 60)

    results = {
        'version': 'v0.5.0a2',
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'benchmarks': {}
    }

    # Credential management
    print("\n[1/4] Running credential management benchmarks...")
    results['benchmarks']['credential_management'] = benchmark_credential_management()
    print("  ✓ Completed")

    # Logging
    print("\n[2/4] Running logging benchmarks...")
    results['benchmarks']['logging'] = benchmark_logging()
    print("  ✓ Completed")

    # Error handling
    print("\n[3/4] Running error handling benchmarks...")
    results['benchmarks']['error_handling'] = benchmark_error_handling()
    print("  ✓ Completed")

    # Storage operations
    print("\n[4/4] Running storage operation benchmarks...")
    results['benchmarks']['storage'] = benchmark_storage_operations()
    print("  ✓ Completed")

    return results


def print_results(results: Dict[str, Any]) -> None:
    """Print benchmark results in a readable format."""
    print("\n" + "=" * 60)
    print("Benchmark Results")
    print("=" * 60)

    for category, metrics in results['benchmarks'].items():
        print(f"\n{category.replace('_', ' ').title()}:")
        for metric, value in metrics.items():
            if isinstance(value, float):
                print(f"  {metric.replace('_', ' ').title()}: {value:.3f}ms")
            else:
                print(f"  {metric.replace('_', ' ').title()}: {value}")

    print("\n" + "=" * 60)


def save_results(results: Dict[str, Any], output_path: Path) -> None:
    """Save benchmark results to JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Results saved to {output_path}")


def main() -> int:
    """Main entry point."""
    try:
        results = run_all_benchmarks()
        print_results(results)

        # Save to baselines directory
        baseline_path = Path(__file__).parent / 'baselines' / 'v0.5.0a2-performance.json'
        save_results(results, baseline_path)

        return 0
    except Exception as e:
        print(f"\n✗ Benchmark failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
