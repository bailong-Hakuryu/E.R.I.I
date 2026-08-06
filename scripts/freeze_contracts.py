"""Generate or verify deterministic v0.5.0 contract snapshots.

The snapshots are deliberately content-free: they freeze public symbol names,
HTTP shapes, durable-format envelopes, lifecycle protocol identifiers, and the
current SQLite schema.  They never inspect a user's storage.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict, fields
import difflib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence, get_args


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import erii  # noqa: E402
from erii.compatibility import (  # noqa: E402
    COMPATIBILITY_CATALOG,
    MEMORY_PACK_METADATA_FIELDS,
    MEMORY_PACK_ROOT_FIELDS,
)
from erii.data_lifecycle import (  # noqa: E402
    LIFECYCLE_PLAN_CONTRACT_VERSION,
    BackupRequest,
    DataLifecycleCoordinator,
    LifecycleOperation,
    LifecycleOutcome,
    LifecyclePlan,
    LifecycleRequest,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    MemoryPackImportOptions,
    _READABLE_LIFECYCLE_PLAN_CONTRACT_VERSIONS,
)
from erii.lifecycle_erasure_contracts import (  # noqa: E402
    ErasureScope,
    ErasureSelector,
)
from erii.server.app import app as reference_app  # noqa: E402
from erii.storage.sqlite_storage import SQLiteStorage  # noqa: E402


SNAPSHOT_RELEASE = "0.5.0a1"
SNAPSHOT_FILENAMES = (
    f"v{SNAPSHOT_RELEASE}-python-api.json",
    f"v{SNAPSHOT_RELEASE}-openapi.json",
    f"v{SNAPSHOT_RELEASE}-data-formats.json",
    f"v{SNAPSHOT_RELEASE}-sqlite-schema.json",
)
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "contracts"


def _release_line(version: str) -> str:
    """Drops only a local development suffix from one release identity."""
    release_line = version.split(".dev", maxsplit=1)[0]
    if release_line != SNAPSHOT_RELEASE:
        raise RuntimeError(
            "this freezer is pinned to package release line "
            f"{SNAPSHOT_RELEASE!r}, but erii.__version__ is {version!r}"
        )
    return release_line


def _json_bytes(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _format_compatibility(value: Any) -> dict[str, Any]:
    document = asdict(value)
    document["readable_versions"] = sorted(
        document["readable_versions"],
        key=lambda version: (len(version), version),
    )
    return document


def _public_python_api_snapshot() -> dict[str, Any]:
    symbols = list(erii.__all__)
    if len(symbols) != len(set(symbols)):
        duplicates = sorted({name for name in symbols if symbols.count(name) > 1})
        raise RuntimeError(
            "erii.__all__ contains duplicate symbols: " + ", ".join(duplicates)
        )
    missing = sorted(name for name in symbols if not hasattr(erii, name))
    if missing:
        raise RuntimeError(
            "erii.__all__ names missing module attributes: " + ", ".join(missing)
        )
    sorted_symbols = sorted(symbols)
    return {
        "snapshot_release": SNAPSHOT_RELEASE,
        "public_api": {
            "module": "erii",
            "symbol_count": len(sorted_symbols),
            "symbols": sorted_symbols,
        },
    }


def _openapi_snapshot() -> dict[str, Any]:
    if reference_app is None:
        raise RuntimeError(
            "FastAPI is required to freeze the REST contract; install erii[dev]"
        )
    # Avoid returning or mutating the reference application's shared cache.
    schema = deepcopy(reference_app.openapi())
    schema["info"]["version"] = _release_line(erii.__version__)
    schema["paths"] = {
        path: value
        for path, value in schema.get("paths", {}).items()
        if path.startswith("/api/v1")
    }
    return {
        "snapshot_release": SNAPSHOT_RELEASE,
        "scope": "FastAPI /api/v1 paths and complete component schemas",
        "openapi": schema,
    }


def _current_plan_document_fields() -> list[str]:
    """Exercises the production serializer instead of duplicating its field list."""
    with tempfile.TemporaryDirectory(prefix="erii-contract-plan-") as directory:
        root = Path(directory)
        source_path = root / "source"
        source_path.mkdir()
        coordinator = DataLifecycleCoordinator()
        source = coordinator.inspect(
            LifecycleTarget(LifecycleTargetKind.FILE_STORAGE, str(source_path))
        )
        plan = coordinator.plan(
            BackupRequest(
                source=source,
                destination=LifecycleTarget(
                    LifecycleTargetKind.BACKUP,
                    str(root / "backup"),
                ),
            )
        )
        document = json.loads(plan.to_json())
    return sorted(document)


def _data_formats_snapshot() -> dict[str, Any]:
    catalog = COMPATIBILITY_CATALOG
    if (
        catalog.lifecycle_plan.current_version != LIFECYCLE_PLAN_CONTRACT_VERSION
        or set(catalog.lifecycle_plan.readable_versions)
        != set(_READABLE_LIFECYCLE_PLAN_CONTRACT_VERSIONS)
    ):
        raise RuntimeError(
            "compatibility catalog and lifecycle plan serializer versions disagree"
        )
    request_types = sorted(request_type.__name__ for request_type in get_args(LifecycleRequest))
    return {
        "snapshot_release": SNAPSHOT_RELEASE,
        "compatibility_catalog": {
            "package_version": _release_line(catalog.package_version),
            "python_requires": catalog.python_requires,
            "python_tested_through": catalog.python_tested_through,
            "formats": {
                "file_storage": _format_compatibility(catalog.file_storage),
                "lifecycle_backup": _format_compatibility(catalog.lifecycle_backup),
                "lifecycle_plan": _format_compatibility(catalog.lifecycle_plan),
                "memory_pack": _format_compatibility(catalog.memory_pack),
                "sqlite": _format_compatibility(catalog.sqlite),
            },
        },
        "memory_pack_envelope": {
            "metadata_fields": sorted(MEMORY_PACK_METADATA_FIELDS),
            "root_fields": sorted(MEMORY_PACK_ROOT_FIELDS),
        },
        "lifecycle_plan": {
            "current_contract_version": LIFECYCLE_PLAN_CONTRACT_VERSION,
            "readable_contract_versions": sorted(
                _READABLE_LIFECYCLE_PLAN_CONTRACT_VERSIONS,
                key=int,
            ),
            "current_document_fields": _current_plan_document_fields(),
            "plan_dataclass_fields": sorted(field.name for field in fields(LifecyclePlan)),
            "operations": sorted(operation.value for operation in LifecycleOperation),
            "outcomes": sorted(outcome.value for outcome in LifecycleOutcome),
            "request_types": request_types,
            "statuses": sorted(status.value for status in LifecycleStatus),
            "target_kinds": sorted(kind.value for kind in LifecycleTargetKind),
            "selector_contracts": {
                "erasure": {
                    "fields": sorted(
                        ErasureSelector(
                            scope=ErasureScope.COMPLETE_USER,
                            user_id="contract-user",
                            user_identity_id="contract-identity",
                        ).to_dict()
                    ),
                    "scopes": sorted(scope.value for scope in ErasureScope),
                },
                "memory_pack_import": {
                    "fields": sorted(
                        field.name for field in fields(MemoryPackImportOptions)
                    ),
                },
            },
        },
    }


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


def _sqlite_schema_snapshot() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="erii-contract-sqlite-") as directory:
        database_path = Path(directory) / "schema.sqlite3"
        SQLiteStorage(str(database_path))
        with closing(sqlite3.connect(database_path)) as connection:
            schema_rows = connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
                ORDER BY type, name, tbl_name
                """
            ).fetchall()
            migration_rows = connection.execute(
                """
                SELECT version, name
                FROM schema_migrations
                ORDER BY version
                """
            ).fetchall()
    schema = [
        {
            "type": row[0],
            "name": row[1],
            "table": row[2],
            "sql": _normalize_sql(row[3]),
        }
        for row in schema_rows
    ]
    return {
        "snapshot_release": SNAPSHOT_RELEASE,
        "sqlite": {
            "format_id": COMPATIBILITY_CATALOG.sqlite.format_id,
            "current_schema_version": SQLiteStorage.CURRENT_SCHEMA_VERSION,
            "schema_object_count": len(schema),
            "sqlite_master": schema,
            "migrations": [
                {"version": version, "name": name}
                for version, name in migration_rows
            ],
        },
    }


def build_contract_snapshots() -> dict[str, bytes]:
    """Builds every deterministic snapshot without writing repository files."""
    documents = (
        _public_python_api_snapshot(),
        _openapi_snapshot(),
        _data_formats_snapshot(),
        _sqlite_schema_snapshot(),
    )
    return {
        filename: _json_bytes(document)
        for filename, document in zip(SNAPSHOT_FILENAMES, documents, strict=True)
    }


def _limited_diff(expected: bytes, actual: bytes, filename: str) -> Iterable[str]:
    expected_lines = expected.decode("utf-8", errors="replace").splitlines()
    actual_lines = actual.decode("utf-8", errors="replace").splitlines()
    diff = list(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile=f"committed/{filename}",
            tofile=f"generated/{filename}",
            lineterm="",
        )
    )
    limit = 200
    yield from diff[:limit]
    if len(diff) > limit:
        yield f"... diff truncated ({len(diff) - limit} more lines)"


def _write_snapshots(output_dir: Path, snapshots: Mapping[str, bytes]) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in snapshots.items():
        (output_dir / filename).write_bytes(payload)
    print(f"wrote {len(snapshots)} contract snapshots to {output_dir}")
    return 0


def _check_snapshots(output_dir: Path, snapshots: Mapping[str, bytes]) -> int:
    failed = False
    for filename, generated in snapshots.items():
        path = output_dir / filename
        if not path.is_file():
            failed = True
            print(
                f"contract snapshot is stale: missing {path}",
                file=sys.stderr,
            )
            continue
        committed = path.read_bytes()
        if committed == generated:
            continue
        failed = True
        print(f"contract snapshot is stale: {path}", file=sys.stderr)
        for line in _limited_diff(committed, generated, filename):
            print(line, file=sys.stderr)
    if failed:
        print(
            "regenerate intentionally with: python scripts/freeze_contracts.py",
            file=sys.stderr,
        )
        return 1
    print(f"contract snapshots are current ({len(snapshots)} files)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare generated snapshots with disk without writing",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="snapshot directory (defaults to docs/contracts)",
    )
    args = parser.parse_args(argv)
    snapshots = build_contract_snapshots()
    if args.check:
        return _check_snapshots(args.output_dir, snapshots)
    return _write_snapshots(args.output_dir, snapshots)


if __name__ == "__main__":
    raise SystemExit(main())
