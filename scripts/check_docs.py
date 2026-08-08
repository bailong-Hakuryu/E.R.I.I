"""Check repository-local Markdown links without making network requests."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


EXCLUDED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
}
DOCUMENTATION_DIRECTORIES = ("docs", ".github", "experiments")
INLINE_LINK = re.compile(
    r"!?\[[^\]\n]*\]\(\s*(?P<target><[^>\n]+>|[^)\s]+)"
)
REFERENCE_TARGET = re.compile(
    r"^\s{0,3}\[[^\]\n]+\]:\s*(?P<target><[^>\n]+>|\S+)"
)
HEADING = re.compile(r"^\s{0,3}(?P<marks>#{1,6})\s+(?P<text>.*?)\s*#*\s*$")
HTML_ANCHOR = re.compile(
    r"<(?:a|[A-Za-z][A-Za-z0-9:-]*)\b[^>]*\b(?:id|name)=[\"']"
    r"(?P<anchor>[^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)
INLINE_CODE = re.compile(r"`+[^`\n]*`+")
MARKDOWN_LINK_TEXT = re.compile(r"\[([^\]]+)\]\([^)]+\)")
URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class LinkError:
    source: Path
    line_number: int
    target: str
    reason: str


def _markdown_files(root: Path) -> list[Path]:
    candidates = list(root.glob("*.md"))
    for directory_name in DOCUMENTATION_DIRECTORIES:
        directory = root / directory_name
        if directory.is_dir():
            candidates.extend(directory.rglob("*.md"))
    return sorted(
        path
        for path in candidates
        if path.is_file()
        and not any(
            part in EXCLUDED_DIRECTORIES
            for part in path.relative_to(root).parts
        )
    )


def _visible_lines(path: Path) -> list[tuple[int, str]]:
    visible: list[tuple[int, str]] = []
    fence: str | None = None
    in_comment = False
    for line_number, original in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        stripped = original.lstrip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```"):
            fence = "```"
            continue
        if stripped.startswith("~~~"):
            fence = "~~~"
            continue

        line = original
        if in_comment:
            if "-->" in line:
                line = line.split("-->", 1)[1]
                in_comment = False
            else:
                continue
        while "<!--" in line:
            prefix, remainder = line.split("<!--", 1)
            if "-->" in remainder:
                line = prefix + remainder.split("-->", 1)[1]
            else:
                line = prefix
                in_comment = True
                break
        visible.append((line_number, line))
    return visible


def _github_slug(text: str) -> str:
    text = MARKDOWN_LINK_TEXT.sub(r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "").replace("*", "").replace("_", "_")
    allowed = "".join(
        character
        for character in text.strip().lower()
        if character.isalnum() or character in {" ", "-", "_"}
    )
    return re.sub(r"\s+", "-", allowed)


def _anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    slug_counts: dict[str, int] = {}
    for _, line in _visible_lines(path):
        match = HEADING.match(line)
        if match:
            base = _github_slug(match.group("text"))
            count = slug_counts.get(base, 0)
            slug_counts[base] = count + 1
            anchors.add(base if count == 0 else f"{base}-{count}")
        anchors.update(
            unquote(match.group("anchor"))
            for match in HTML_ANCHOR.finditer(line)
        )
    return anchors


def _exact_case_exists(root: Path, target: Path) -> bool:
    try:
        relative = target.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        if not current.is_dir():
            return False
        names = {child.name for child in current.iterdir()}
        if part not in names:
            return False
        current = current / part
    return current.exists()


def _extract_targets(path: Path) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []
    for line_number, line in _visible_lines(path):
        searchable = INLINE_CODE.sub("", line)
        targets.extend(
            (line_number, match.group("target"))
            for match in INLINE_LINK.finditer(searchable)
        )
        reference = REFERENCE_TARGET.match(searchable)
        if reference:
            targets.append((line_number, reference.group("target")))
    return targets


def check_repository(root: Path) -> tuple[list[LinkError], int, int]:
    """Return link errors, Markdown file count, and checked local-link count."""
    root = root.resolve()
    files = _markdown_files(root)
    anchor_cache: dict[Path, set[str]] = {}
    errors: list[LinkError] = []
    local_link_count = 0

    for source in files:
        for line_number, raw_target in _extract_targets(source):
            target = raw_target[1:-1] if raw_target.startswith("<") else raw_target
            if (
                not target
                or target.startswith("//")
                or URI_SCHEME.match(target)
            ):
                continue

            parsed = urlsplit(target)
            relative_path = unquote(parsed.path)
            fragment = unquote(parsed.fragment)
            candidate = (
                source
                if not relative_path
                else (source.parent / relative_path).resolve(strict=False)
            )
            local_link_count += 1

            try:
                candidate.relative_to(root)
            except ValueError:
                errors.append(
                    LinkError(source, line_number, target, "escapes repository root")
                )
                continue
            if not _exact_case_exists(root, candidate):
                errors.append(
                    LinkError(source, line_number, target, "target does not exist")
                )
                continue
            if fragment and candidate.is_file() and candidate.suffix.lower() == ".md":
                anchors = anchor_cache.setdefault(candidate, _anchors(candidate))
                if fragment not in anchors:
                    errors.append(
                        LinkError(
                            source,
                            line_number,
                            target,
                            f"Markdown anchor #{fragment} does not exist",
                        )
                    )

    return errors, len(files), local_link_count


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Check relative file and Markdown-anchor links."
    )
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to scan (defaults to this script's repository)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"scan root is not a directory: {root}")

    errors, file_count, link_count = check_repository(root)
    for error in errors:
        source = error.source.relative_to(root).as_posix()
        print(
            f"{source}:{error.line_number}: {error.reason}: {error.target}"
        )
    if errors:
        print(
            f"Checked {file_count} Markdown files and {link_count} local links: "
            f"{len(errors)} error(s)"
        )
        return 1
    print(f"Checked {file_count} Markdown files and {link_count} local links: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
