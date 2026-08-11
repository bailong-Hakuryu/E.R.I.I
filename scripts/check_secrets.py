"""Fail when credential-shaped literals are committed or awaiting commit.

This deliberately reports only a path and line number.  Printing the matched
value would copy an exposed credential into CI logs and make cleanup harder.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


SECRET_PATTERNS = (
    re.compile(rb"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"(?<![A-Za-z0-9_-])sk_live_[A-Za-z0-9]{20,}"),
    re.compile(rb"(?<![A-Za-z0-9_])gh[opusr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"),
    re.compile(rb"(?<![A-Za-z0-9_-])AIza[A-Za-z0-9_-]{30,}"),
    re.compile(rb"(?<![A-Za-z0-9_-])xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(rb"(?<![A-Za-z0-9_-])npm_[A-Za-z0-9]{30,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        rb"(?:api[_-]?key|token|secret|password|credential)"
        rb"\s*(?::|=)\s*[\"']?"
        rb"(?=[^\s\"'`]{20,}(?:[\s\"']|$))"
        rb"(?=[^\s\"'`]*[A-Za-z])"
        rb"(?=[^\s\"'`]*[0-9])"
        rb"[^\s\"'`]{20,}",
        re.IGNORECASE,
    ),
)


def _version_control_candidates(root: Path) -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tuple(
        root / item.decode("utf-8", errors="surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    )


def find_secret_locations(root: Path) -> tuple[tuple[Path, int], ...]:
    """Return credential-shaped literal locations without returning values."""

    # Directory patterns to exclude
    SKIP_DIRS = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".venv",
        "venv",
        "build",
        "dist",
        ".scratch",
    }

    findings: list[tuple[Path, int]] = []
    for path in _version_control_candidates(root):
        if not path.is_file():
            continue

        # Skip if in excluded directory
        if any(skip_dir in path.parts for skip_dir in SKIP_DIRS):
            continue

        # Get relative path for reporting.
        rel_path = path.relative_to(root)

        try:
            payload = path.read_bytes()
        except OSError:
            continue
        if b"\0" in payload:
            continue
        for line_number, line in enumerate(payload.splitlines(), start=1):
            if any(pattern.search(line) for pattern in SECRET_PATTERNS):
                findings.append((rel_path, line_number))
    return tuple(findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Git worktree to scan (defaults to this repository)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    findings = find_secret_locations(root)
    if not findings:
        print("Secret scan passed: no credential-shaped literals in commit candidates.")
        return 0
    print("Secret scan failed; rotate the credential and clean these locations:")
    for path, line_number in findings:
        print(f"- {path}:{line_number}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
