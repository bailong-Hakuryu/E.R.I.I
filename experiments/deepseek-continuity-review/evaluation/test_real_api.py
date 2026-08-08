"""Compatibility entry point for the opt-in real-provider evaluation CLI.

The API key is read only from ``DEEPSEEK_API_KEY``.  This file contains no
pytest tests; CI exercises the offline unit suite instead.
"""

try:
    from .comprehensive_test import main
except ImportError:  # Direct script execution from the evaluation directory.
    from comprehensive_test import main  # type: ignore[no-redef]


if __name__ == "__main__":
    raise SystemExit(main())
