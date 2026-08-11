"""Local micro-benchmark suite for the active E.R.I.I. source checkout.

Measures performance metrics for key operations:
- Credential management
- Logging operations
- Error handling
- Storage operations
"""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Dict

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def benchmark_credential_management() -> Dict[str, Any]:
    """Benchmark credential management operations."""
    from erii.security import CredentialManager

    results = {}
    iterations = 1000

    # Build a credential-shaped fixture without committing one literal.
    test_key = "sk-" + ("a" * 32)
    original_value = os.environ.get("BENCHMARK_API_KEY")
    os.environ['BENCHMARK_API_KEY'] = test_key

    try:
        # Test 1: Key loading
        start = time.perf_counter()
        for _ in range(iterations):
            CredentialManager.get_api_key('benchmark', env_var='BENCHMARK_API_KEY')
        duration = time.perf_counter() - start
        results['key_loading_ms'] = (duration / iterations) * 1000

        # Test 2: Key redaction
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
        test_text = f'api_key="{test_key}" token="xyz123"'
        start = time.perf_counter()
        for _ in range(iterations):
            CredentialManager.detect_key_leakage(test_text)
        duration = time.perf_counter() - start
        results['leakage_detection_ms'] = (duration / iterations) * 1000

    finally:
        if original_value is None:
            os.environ.pop("BENCHMARK_API_KEY", None)
        else:
            os.environ["BENCHMARK_API_KEY"] = original_value

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
        ErrorCode
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
    """Benchmark verified core-memory write/read cycles."""
    from erii.storage import FileStorage, SQLiteStorage

    results = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        iterations = 100
        stores = {
            'file_storage_cycle_ms': FileStorage(
                root_dir=Path(tmpdir) / 'file_storage'
            ),
            'sqlite_storage_cycle_ms': SQLiteStorage(
                db_path=Path(tmpdir) / 'test.db'
            ),
        }
        for metric, storage in stores.items():
            start = time.perf_counter()
            for index in range(iterations):
                expected = f"benchmark-payload-{index}-" + ("x" * 256)
                storage.save_core_memory("benchmark-agent", "benchmark-user", expected)
                actual = storage.get_core_memory("benchmark-agent", "benchmark-user")
                if actual != expected:
                    raise RuntimeError(f"{metric} read-after-write verification failed")
            duration = time.perf_counter() - start
            results[metric] = (duration / iterations) * 1000

    return results


def run_all_benchmarks() -> Dict[str, Any]:
    """Run all benchmark suites."""
    print("=" * 60)
    print("E.R.I.I. Performance Benchmark Suite")
    print("=" * 60)

    from erii import __version__

    results = {
        'version': __version__,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'benchmarks': {}
    }

    # Credential management
    print("\n[1/4] Running credential management benchmarks...")
    results['benchmarks']['credential_management'] = benchmark_credential_management()
    print("  PASS")

    # Logging
    print("\n[2/4] Running logging benchmarks...")
    results['benchmarks']['logging'] = benchmark_logging()
    print("  PASS")

    # Error handling
    print("\n[3/4] Running error handling benchmarks...")
    results['benchmarks']['error_handling'] = benchmark_error_handling()
    print("  PASS")

    # Storage operations
    print("\n[4/4] Running storage operation benchmarks...")
    results['benchmarks']['storage'] = benchmark_storage_operations()
    print("  PASS")

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
    print(f"\nResults saved to {output_path}")


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON output path; omitted by default to avoid modifying the tree",
    )
    args = parser.parse_args(argv)
    try:
        results = run_all_benchmarks()
        print_results(results)
        if args.output is not None:
            save_results(results, args.output)

        return 0
    except Exception as e:
        print(f"\nBenchmark failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
