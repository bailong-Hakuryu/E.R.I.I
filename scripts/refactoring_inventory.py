"""Generate the deterministic refactoring inventory from tracked Python sources."""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "architecture" / "refactoring-r0-inventory.md"
SCANNED_PREFIXES = ("benchmarks/", "clients/", "erii/", "examples/", "experiments/", "scripts/", "tests/")
MEMORY_PACK_CALLS = frozenset({"export_memory", "import_memory"})
INTERFACE_IMPORTS = frozenset({"DataLifecycleCoordinator", "ERIIEngine", "LifecycleInspector"})


class InventoryError(RuntimeError):
    """The tracked source inventory could not be constructed."""


@dataclass(frozen=True)
class MethodRecord:
    name: str
    line: int
    end_line: int


@dataclass(frozen=True)
class CallSite:
    path: str
    line: int
    method: str


@dataclass(frozen=True)
class ImportSite:
    path: str
    line: int
    name: str
    module: str


@dataclass(frozen=True)
class Inventory:
    engine_lines: int
    engine_methods: tuple[MethodRecord, ...]
    memory_pack_helpers: tuple[MethodRecord, ...]
    memory_pack_analysis_lines: int
    memory_pack_analysis_functions: tuple[MethodRecord, ...]
    memory_pack_transfer_lines: int
    memory_pack_transfer_functions: tuple[MethodRecord, ...]
    root_exports: tuple[str, ...]
    storage_methods: tuple[MethodRecord, ...]
    lifecycle_lines: int
    lifecycle_coordinator_methods: tuple[MethodRecord, ...]
    lifecycle_top_level_types: tuple[MethodRecord, ...]
    calls: tuple[CallSite, ...]
    imports: tuple[ImportSite, ...]


def _git_lines(*arguments: str) -> list[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        raise InventoryError(completed.stderr.strip() or "git command failed")
    return [line for line in completed.stdout.splitlines() if line]


def _tracked_python_paths() -> tuple[str, ...]:
    return tuple(
        path
        for path in _git_lines(
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "*.py",
        )
        if path.startswith(SCANNED_PREFIXES)
        and (ROOT / path).is_file()
    )


def _parse(path: str) -> tuple[str, ast.Module]:
    try:
        source = (ROOT / path).read_text(encoding="utf-8-sig")
        return source, ast.parse(source, filename=path)
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise InventoryError(f"cannot parse {path}: {exc}") from exc


def _method_record(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> MethodRecord:
    return MethodRecord(node.name, node.lineno, node.end_lineno or node.lineno)


def _class_methods(tree: ast.Module, class_name: str) -> tuple[MethodRecord, ...]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return tuple(
                _method_record(item)
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    raise InventoryError(f"class {class_name} is missing")


def _top_level_functions(tree: ast.Module) -> tuple[MethodRecord, ...]:
    return tuple(
        _method_record(node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _root_exports(tree: ast.Module) -> tuple[str, ...]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError) as exc:
            raise InventoryError("erii.__all__ must be a literal collection") from exc
        if not isinstance(value, (list, tuple)) or not all(type(item) is str for item in value):
            raise InventoryError("erii.__all__ must contain strings")
        return tuple(value)
    raise InventoryError("erii.__all__ is missing")


def _is_memory_pack_helper(name: str) -> bool:
    if name in {"export_memory", "import_memory", "_import_memory_unlocked"}:
        return True
    return (
        name.startswith("_validate_") and ("pack" in name or "import_conflicts" in name)
    ) or name.startswith("_remap_") or name.startswith("_import_persona_")


def _scan_callers(paths: Iterable[str]) -> tuple[tuple[CallSite, ...], tuple[ImportSite, ...]]:
    calls: list[CallSite] = []
    imports: list[ImportSite] = []
    for path in paths:
        _source, tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in MEMORY_PACK_CALLS:
                    calls.append(CallSite(path, node.lineno, node.func.attr))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    if alias.name in INTERFACE_IMPORTS:
                        imports.append(ImportSite(path, node.lineno, alias.name, module))
    return (
        tuple(sorted(calls, key=lambda item: (item.path, item.line, item.method))),
        tuple(sorted(imports, key=lambda item: (item.path, item.line, item.name))),
    )


def build_inventory() -> Inventory:
    """Build the inventory from the current tracked source tree."""

    engine_source, engine_tree = _parse("erii/engine.py")
    engine_methods = _class_methods(engine_tree, "ERIIEngine")
    analysis_source, analysis_tree = _parse(
        "erii/_engine/memory_pack_analysis.py"
    )
    transfer_source, transfer_tree = _parse(
        "erii/_engine/memory_pack_transfer.py"
    )
    _root_source, root_tree = _parse("erii/__init__.py")
    _storage_source, storage_tree = _parse("erii/storage/base.py")
    lifecycle_source, lifecycle_tree = _parse("erii/data_lifecycle.py")
    lifecycle_types = tuple(
        _method_record(node)
        for node in lifecycle_tree.body
        if isinstance(node, ast.ClassDef)
    )
    calls, imports = _scan_callers(_tracked_python_paths())
    return Inventory(
        engine_lines=len(engine_source.splitlines()),
        engine_methods=engine_methods,
        memory_pack_helpers=tuple(method for method in engine_methods if _is_memory_pack_helper(method.name)),
        memory_pack_analysis_lines=len(analysis_source.splitlines()),
        memory_pack_analysis_functions=_top_level_functions(analysis_tree),
        memory_pack_transfer_lines=len(transfer_source.splitlines()),
        memory_pack_transfer_functions=_top_level_functions(transfer_tree),
        root_exports=_root_exports(root_tree),
        storage_methods=_class_methods(storage_tree, "BaseStorage"),
        lifecycle_lines=len(lifecycle_source.splitlines()),
        lifecycle_coordinator_methods=_class_methods(lifecycle_tree, "DataLifecycleCoordinator"),
        lifecycle_top_level_types=lifecycle_types,
        calls=calls,
        imports=imports,
    )


def _method_table(methods: Iterable[MethodRecord]) -> list[str]:
    return [
        f"| `{method.name}` | {method.line} | {method.end_line - method.line + 1} |"
        for method in methods
    ]


def _group_calls(calls: Iterable[CallSite]) -> list[str]:
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for call in calls:
        grouped[(call.path, call.method)].append(call.line)
    return [
        f"| `{path}` | `{method}` | {len(lines)} | {', '.join(map(str, lines))} |"
        for (path, method), lines in sorted(grouped.items())
    ]


def _group_imports(imports: Iterable[ImportSite]) -> list[str]:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item in imports:
        grouped[(item.name, item.module)].append(item.path)
    return [
        f"| `{name}` | `{module}` | {len(set(paths))} | "
        + ", ".join(f"`{path}`" for path in sorted(set(paths)))
        + " |"
        for (name, module), paths in sorted(grouped.items())
    ]


def render_inventory(inventory: Inventory) -> str:
    """Render the current inventory as deterministic Markdown."""

    public_engine = tuple(method for method in inventory.engine_methods if not method.name.startswith("_"))
    private_engine = tuple(method for method in inventory.engine_methods if method.name.startswith("_"))
    test_calls = tuple(call for call in inventory.calls if call.path.startswith("tests/"))
    runtime_calls = tuple(call for call in inventory.calls if not call.path.startswith("tests/"))
    call_file_counts = Counter(call.path for call in inventory.calls)
    lines = [
        "# R0 Refactoring Inventory",
        "",
        "<!-- Generated by scripts/refactoring_inventory.py; do not edit by hand. -->",
        "",
        "This inventory records the structural and caller baseline used by R1. It is generated from tracked and unignored commit-candidate Python sources with the standard-library AST and contains no user data.",
        "",
        "## Structural Baseline",
        "",
        "| Surface | Lines or symbols | Public methods | Private methods |",
        "| --- | ---: | ---: | ---: |",
        f"| `erii/engine.py` | {inventory.engine_lines} lines | {len(public_engine)} | {len(private_engine)} |",
        f"| `erii/__init__.py` | {len(inventory.root_exports)} exports | n/a | n/a |",
        f"| `BaseStorage` | {len(inventory.storage_methods)} methods | {sum(not method.name.startswith('_') for method in inventory.storage_methods)} | {sum(method.name.startswith('_') for method in inventory.storage_methods)} |",
        f"| `erii/data_lifecycle.py` | {inventory.lifecycle_lines} lines | {sum(not method.name.startswith('_') for method in inventory.lifecycle_coordinator_methods)} coordinator | {sum(method.name.startswith('_') for method in inventory.lifecycle_coordinator_methods)} coordinator |",
        "",
        "Counts locate risk; they are not completion targets. R1 succeeds only when MemoryPack complexity moves behind a smaller Interface without public or format changes.",
        "",
        "## R1 MemoryPack Implementation Cluster",
        "",
        "### Remaining In Engine",
        "",
        "| Method | Start line | Span |",
        "| --- | ---: | ---: |",
        *_method_table(inventory.memory_pack_helpers),
        "",
        "The remaining cluster includes the public export/import entry points, guarded import execution, target conflict helper Implementation, Persona target-state transitions, causal history commit, and real Storage writes. R1B now routes first-write preflight reads through the private transfer Recorder and consumes its deterministic zero-write payload plan; locking, transactions, conflict enforcement, execution order, and writes remain in Engine.",
        "",
        "### Extracted No-Write Analysis",
        "",
        f"`erii/_engine/memory_pack_analysis.py`: {inventory.memory_pack_analysis_lines} lines.",
        "",
        "| Function | Start line | Span |",
        "| --- | ---: | ---: |",
        *_method_table(inventory.memory_pack_analysis_functions),
        "",
        "This private Module is not exported from `erii.__all__`. Its Interface accepts a MemoryPack snapshot, performs no Storage access, returns immutable derived facts, and preserves the existing ValueError messages for the rules it owns.",
        "",
        "### Snapshot-Bound Transfer Planning",
        "",
        f"`erii/_engine/memory_pack_transfer.py`: {inventory.memory_pack_transfer_lines} lines.",
        "",
        "| Function | Start line | Span |",
        "| --- | ---: | ---: |",
        *_method_table(inventory.memory_pack_transfer_functions),
        "",
        "This private Module freezes the portable source, target relationship, overwrite intent, ordered target conflict read set, deterministic ID remaps, and zero-write payload batches. Its read-only Recorder accepts the existing Storage Interface, records capability outcomes and canonical result fingerprints, and is replayed before the first write; the write-planning Interface accepts no Storage, imports no concrete Storage Adapter, and exposes no root-level public symbol.",
        "",
        "## MemoryPack Callers",
        "",
        f"Tracked call sites: {len(inventory.calls)} across {len(call_file_counts)} files; {len(runtime_calls)} runtime/example/benchmark call sites and {len(test_calls)} test call sites.",
        "",
        "| Path | Method | Calls | Lines |",
        "| --- | --- | ---: | --- |",
        *_group_calls(inventory.calls),
        "",
        "The broad test surface is intentional evidence: MemoryPack crosses relationship, persona, Turn, temporal, consequence, lifecycle, REST, security, and backward-compatibility behavior. R1 must keep these public tests while replacing duplicated implementation-level tests with tests at the new internal Interface.",
        "",
        "## Direct Interface Importers",
        "",
        "| Symbol | Imported from | Files | Paths |",
        "| --- | --- | ---: | --- |",
        *_group_imports(inventory.imports),
        "",
        "## Lifecycle Coordinator Shape",
        "",
        "| Method | Start line | Span |",
        "| --- | ---: | ---: |",
        *_method_table(inventory.lifecycle_coordinator_methods),
        "",
        f"Top-level Lifecycle types/classes: {len(inventory.lifecycle_top_level_types)}. R2 separates contracts and no-write paths before any R3 write-path extraction.",
        "",
        "## R1 Protected Behavior Matrix",
        "",
        "R1 changes must preserve at least these existing caller groups:",
        "",
        "- public `ERIIEngine.export_memory()` and `import_memory()` signatures, errors, warnings, and return values;",
        "- FileStorage to FileStorage, FileStorage to SQLite, SQLite to FileStorage, and SQLite to SQLite behavior;",
        "- relationship identity, direct-event authority, source evidence, Turn, temporal, consequence, persona, processing, and archival ledgers;",
        "- old Adapter behavior where optional newer Storage methods raise `NotImplementedError`;",
        "- Golden Demo, REST round trip, Lifecycle fresh-target import, erasure/rebuild, and contract snapshots;",
        "- duplicate import, overwrite, conflict, stale guard, malformed pack, and cross-relationship rejection;",
        "- absence of credentials, prompts, raw Provider thinking, and discarded private drafts in portable data.",
        "",
        "## Update Rule",
        "",
        "Regenerate this file whenever a refactoring change modifies Engine methods, Lifecycle Coordinator methods, root exports, BaseStorage methods, or MemoryPack call sites:",
        "",
        "```powershell",
        "python scripts/refactoring_inventory.py --write",
        "python scripts/refactoring_inventory.py --check",
        "```",
        "",
    ]
    return "\n".join(lines)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
        rendered = render_inventory(build_inventory())
        if args.write:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8", newline="\n")
            print(f"Wrote {args.output.relative_to(ROOT)}")
            return 0
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError as exc:
            raise InventoryError(f"cannot read generated inventory: {exc}") from exc
        if current != rendered:
            print(
                "Refactoring inventory is stale; run "
                "python scripts/refactoring_inventory.py --write"
            )
            return 1
        print(f"Refactoring inventory is current: {args.output.relative_to(ROOT)}")
        return 0
    except InventoryError as exc:
        print(f"Refactoring inventory failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
