#!/usr/bin/env python3
"""CI/CD script to detect API key leakage in source code.

This script scans all Python files in the repository for potential
API key literals and fails the build if any are detected.

Usage:
    python scripts/check_key_leakage.py

Exit codes:
    0: No keys detected
    1: Keys detected or script error
"""

import sys
from pathlib import Path

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from erii.security.credential_manager import CredentialManager, CredentialError  # noqa: E402


def scan_file(file_path: Path) -> list[str]:
    """Scan a file for potential key leakage.

    Args:
        file_path: Path to file to scan.

    Returns:
        List of detected issues (empty if clean).
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Skip documentation files - they contain example keys
        if file_path.suffix in ['.md', '.rst', '.txt']:
            return []

        # Use the credential manager's validation
        try:
            CredentialManager.validate_no_literal_keys(content, str(file_path))
            return []
        except CredentialError as e:
            return [str(e)]

    except Exception as e:
        return [f"Error reading {file_path}: {e}"]


def scan_directory(root_dir: Path, patterns: list[str] = None) -> dict[str, list[str]]:
    """Scan directory for key leakage.

    Args:
        root_dir: Root directory to scan.
        patterns: File patterns to scan (e.g., ['*.py', '*.md']).

    Returns:
        Dict mapping file paths to list of issues.
    """
    if patterns is None:
        patterns = ['*.py']

    issues = {}

    for pattern in patterns:
        for file_path in root_dir.rglob(pattern):
            # Skip certain directories
            skip_dirs = {'.git', '__pycache__', '.pytest_cache', 'node_modules',
                        '.venv', 'venv', 'build', 'dist', '.scratch'}
            if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
                continue

            file_issues = scan_file(file_path)
            if file_issues:
                issues[str(file_path.relative_to(root_dir))] = file_issues

    return issues


def main():
    """Main entry point."""
    print("=" * 70)
    print("API Key Leakage Detection")
    print("=" * 70)
    print()

    # Scan Python files
    print("Scanning Python source files...")
    py_issues = scan_directory(project_root / 'erii', ['*.py'])

    print("Scanning test files...")
    test_issues = scan_directory(project_root / 'tests', ['*.py'])

    print("Scanning documentation...")
    doc_issues = scan_directory(project_root / 'docs', ['*.md', '*.rst'])

    print("Scanning examples...")
    example_issues = scan_directory(project_root / 'examples', ['*.py'])

    # Combine all issues
    all_issues = {**py_issues, **test_issues, **doc_issues, **example_issues}

    print()
    print("=" * 70)

    if not all_issues:
        print("✓ No API key leakage detected!")
        print("=" * 70)
        return 0

    # Report issues
    print(f"✗ Detected potential key leakage in {len(all_issues)} file(s):")
    print("=" * 70)
    print()

    for file_path, issues in sorted(all_issues.items()):
        print(f"File: {file_path}")
        for issue in issues:
            print(f"  - {issue}")
        print()

    print("=" * 70)
    print("FAILURE: API keys must be loaded from environment variables.")
    print("Update code to use CredentialManager.get_api_key() instead.")
    print("=" * 70)

    return 1


if __name__ == '__main__':
    sys.exit(main())
