"""Validate and render the repository's project-status catalog."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs" / "project-status.json"
DEFAULT_OUTPUT = ROOT / "docs" / "PROJECT_STATUS.md"

SCHEMA_VERSION = "erii-project-status/v1"
TRACKS = frozenset({"adapter", "client", "core", "experiment", "host", "labs", "product"})
MATURITIES = (
    "maintenance",
    "active-alpha",
    "experimental",
    "placeholder",
    "planned",
)
PUBLIC_INTERFACES = frozenset({"golden", "advanced", "experimental", "internal", "none"})
PROGRAM_STATUSES = frozenset({"planned", "in_progress", "blocked", "complete"})
REQUIRED_MODULE_FIELDS = frozenset(
    {
        "id",
        "name",
        "track",
        "maturity",
        "paths",
        "public_interface",
        "persistence_impact",
        "ci",
        "summary",
        "next_gate",
    }
)


class CatalogError(ValueError):
    """One or more catalog invariants are invalid."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise CatalogError(f"{label} must be an object")
    return value


def _required_text(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise CatalogError(f"{label} must be a non-empty trimmed string")
    return value


def _date(value: Any, label: str) -> str:
    text = _required_text(value, label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise CatalogError(f"{label} must be an ISO date") from exc
    if parsed.isoformat() != text:
        raise CatalogError(f"{label} must use YYYY-MM-DD")
    return text


def _string_list(value: Any, label: str) -> list[str]:
    if type(value) is not list or not value:
        raise CatalogError(f"{label} must be a non-empty array")
    result = [_required_text(item, f"{label} item") for item in value]
    if len(result) != len(set(result)):
        raise CatalogError(f"{label} contains duplicates")
    return result


def _tracked_paths(root: Path) -> frozenset[str]:
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        raise CatalogError(f"git ls-files failed: {completed.stderr.strip()}")
    return frozenset(line for line in completed.stdout.splitlines() if line)


def _path_is_tracked(path: str, tracked: frozenset[str]) -> bool:
    return path in tracked or any(item.startswith(path.rstrip("/") + "/") for item in tracked)


def load_catalog(path: Path = DEFAULT_SOURCE) -> dict[str, Any]:
    """Decode the catalog with duplicate-key and type checks."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CatalogError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot decode {path}: {exc}") from exc


def validate_catalog(catalog: dict[str, Any], *, root: Path = ROOT) -> None:
    """Validate structure, vocabulary, ordering, and repository paths."""

    if set(catalog) != {
        "schema_version",
        "as_of",
        "source_version",
        "baseline_commit",
        "program",
        "modules",
    }:
        raise CatalogError("catalog root fields do not match the v1 contract")
    if catalog["schema_version"] != SCHEMA_VERSION:
        raise CatalogError(f"schema_version must be {SCHEMA_VERSION}")
    _date(catalog["as_of"], "as_of")
    _required_text(catalog["source_version"], "source_version")
    baseline = _required_text(catalog["baseline_commit"], "baseline_commit")
    if len(baseline) != 40 or any(character not in "0123456789abcdef" for character in baseline):
        raise CatalogError("baseline_commit must be a lowercase full commit SHA")

    program = _object(catalog["program"], "program")
    if set(program) != {"phase", "status", "window_start", "window_end", "next_gate"}:
        raise CatalogError("program fields do not match the v1 contract")
    _required_text(program["phase"], "program.phase")
    if program["status"] not in PROGRAM_STATUSES:
        raise CatalogError("program.status is invalid")
    window_start = _date(program["window_start"], "program.window_start")
    window_end = _date(program["window_end"], "program.window_end")
    if window_end < window_start:
        raise CatalogError("program window ends before it starts")
    _required_text(program["next_gate"], "program.next_gate")

    modules = catalog["modules"]
    if type(modules) is not list or not modules:
        raise CatalogError("modules must be a non-empty array")
    ids: list[str] = []
    tracked = _tracked_paths(root)
    for index, raw_module in enumerate(modules):
        module = _object(raw_module, f"modules[{index}]")
        if set(module) != REQUIRED_MODULE_FIELDS:
            raise CatalogError(f"modules[{index}] fields do not match the v1 contract")
        module_id = _required_text(module["id"], f"modules[{index}].id")
        if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in module_id):
            raise CatalogError(f"modules[{index}].id must use lowercase kebab-case")
        ids.append(module_id)
        _required_text(module["name"], f"modules[{index}].name")
        if module["track"] not in TRACKS:
            raise CatalogError(f"modules[{index}].track is invalid")
        if module["maturity"] not in MATURITIES:
            raise CatalogError(f"modules[{index}].maturity is invalid")
        if module["public_interface"] not in PUBLIC_INTERFACES:
            raise CatalogError(f"modules[{index}].public_interface is invalid")
        _required_text(module["persistence_impact"], f"modules[{index}].persistence_impact")
        _required_text(module["summary"], f"modules[{index}].summary")
        _required_text(module["next_gate"], f"modules[{index}].next_gate")
        _string_list(module["ci"], f"modules[{index}].ci")
        paths = _string_list(module["paths"], f"modules[{index}].paths")
        for path in paths:
            parsed = PurePosixPath(path)
            if parsed.is_absolute() or ".." in parsed.parts or "\\" in path:
                raise CatalogError(f"modules[{index}].paths contains an unsafe path")
            if not _path_is_tracked(path, tracked):
                raise CatalogError(f"modules[{index}].paths is not tracked: {path}")
    if len(ids) != len(set(ids)):
        raise CatalogError("module ids must be unique")


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_catalog(catalog: dict[str, Any]) -> str:
    """Render a deterministic Markdown dashboard."""

    modules = catalog["modules"]
    maturity_counts = {
        maturity: sum(module["maturity"] == maturity for module in modules)
        for maturity in MATURITIES
    }
    program = catalog["program"]
    lines = [
        "# E.R.I.I. Project Status",
        "",
        "<!-- Generated by scripts/project_status.py; edit docs/project-status.json. -->",
        "",
        f"> As of: `{catalog['as_of']}`",
        ">",
        f"> Source: `{catalog['source_version']}` at `{catalog['baseline_commit']}`",
        ">",
        f"> Refactoring program: `{program['phase']}` / `{program['status']}` "
        f"(`{program['window_start']}` to `{program['window_end']}`)",
        "",
        "This dashboard separates maintained Core, active Alpha surfaces, removable experiments, honest placeholders, and planned work. Status is curated in `docs/project-status.json`; file counts or the presence of code do not promote maturity.",
        "",
        "## Maturity Summary",
        "",
        "| Maturity | Modules | Meaning |",
        "| --- | ---: | --- |",
        f"| `maintenance` | {maturity_counts['maintenance']} | Accepted behavior under defect, security, and compatibility maintenance. |",
        f"| `active-alpha` | {maturity_counts['active-alpha']} | Implemented and tested source behavior that may still evolve before 1.0. |",
        f"| `experimental` | {maturity_counts['experimental']} | Removable evaluation surface without stable or production claims. |",
        f"| `placeholder` | {maturity_counts['placeholder']} | Reserved seam that deliberately reports unavailable behavior. |",
        f"| `planned` | {maturity_counts['planned']} | Direction only; not an implemented runtime capability. |",
        "",
        "## Current Program",
        "",
        "```mermaid",
        "flowchart LR",
        "    R0[\"R0 baseline and map\"] --> R1[\"R1 MemoryPack Transfer\"]",
        "    R1 --> R2[\"R2 Lifecycle read paths\"]",
        "    R2 --> R3[\"R3 Lifecycle write paths\"]",
        "    R3 --> G{\"stability checkpoint\"}",
        "    G -->|\"pass\"| R4[\"R4 Engine workflows\"]",
        "    G -->|\"fail\"| S[\"stop and repair\"]",
        "```",
        "",
        f"Current gate: {program['next_gate']}",
        "",
        "## Module Catalog",
        "",
        "| Module | Track | Maturity | Interface | Persistence | CI |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for module in modules:
        lines.append(
            "| "
            + " | ".join(
                (
                    _escape_cell(module["name"]),
                    f"`{module['track']}`",
                    f"`{module['maturity']}`",
                    f"`{module['public_interface']}`",
                    f"`{module['persistence_impact']}`",
                    _escape_cell(", ".join(module["ci"])),
                )
            )
            + " |"
        )
    lines.extend(("", "## Next Gates", ""))
    for module in modules:
        paths = ", ".join(f"`{path}`" for path in module["paths"])
        lines.extend(
            (
                f"### {module['name']}",
                "",
                module["summary"],
                "",
                f"Paths: {paths}",
                "",
                f"Next gate: {module['next_gate']}",
                "",
            )
        )
    lines.extend(
        (
            "## Update Rule",
            "",
            "Update `docs/project-status.json` in the same change when a Module changes track, maturity, public Interface, persistence impact, CI coverage, or promotion gate. Then run:",
            "",
            "```powershell",
            "python scripts/project_status.py --write",
            "python scripts/project_status.py --check",
            "```",
            "",
            "A successful build, parser test, or live Provider call does not by itself promote maturity. Promotion requires the gate recorded for that Module and any applicable roadmap admission criteria.",
            "",
        )
    )
    return "\n".join(lines)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments(argv)
    try:
        catalog = load_catalog(args.source)
        validate_catalog(catalog, root=ROOT)
        rendered = render_catalog(catalog)
        if args.write:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8", newline="\n")
            print(f"Wrote {args.output.relative_to(ROOT)} from {args.source.relative_to(ROOT)}")
            return 0
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError as exc:
            raise CatalogError(f"cannot read generated dashboard: {exc}") from exc
        if current != rendered:
            print(
                "Project status dashboard is stale; run "
                "python scripts/project_status.py --write"
            )
            return 1
        print(f"Project status catalog and {args.output.relative_to(ROOT)} are current")
        return 0
    except CatalogError as exc:
        print(f"Project status validation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
