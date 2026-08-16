"""Measure the R0 MemoryPack and Lifecycle refactoring baseline."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Callable

from erii import (
    BackupRequest,
    DataLifecycleCoordinator,
    ERIIEngine,
    FileStorage,
    LifecycleOutcome,
    LifecycleTarget,
    LifecycleTargetKind,
    MemoryNode,
    MemoryType,
    SQLiteStorage,
    __version__,
)


ROOT = Path(__file__).resolve().parents[1]
SUITE_VERSION = "refactoring-r0-baseline/v1"
AGENT_ID = "baseline-agent"
USER_ID = "baseline-user"
BLUEPRINT = "An original synthetic character who values precise continuity."


@dataclass(frozen=True)
class Measurement:
    median_ms: float
    minimum_ms: float
    maximum_ms: float
    samples_ms: tuple[float, ...]


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def _full_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git rev-parse failed")
    value = completed.stdout.strip()
    if len(value) != 40:
        raise RuntimeError("git rev-parse did not return a full commit SHA")
    return value


def _measure(iterations: int, operation: Callable[[], None]) -> Measurement:
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - start) * 1000)
    return Measurement(
        median_ms=statistics.median(samples),
        minimum_ms=min(samples),
        maximum_ms=max(samples),
        samples_ms=tuple(samples),
    )


def _storage(kind: str, path: Path):
    if kind == "file":
        return FileStorage(str(path))
    if kind == "sqlite":
        return SQLiteStorage(str(path))
    raise ValueError(f"unknown storage kind: {kind}")


def _build_source(root: Path, kind: str) -> tuple[ERIIEngine, object]:
    path = root / ("source-store" if kind == "file" else "source.db")
    engine = ERIIEngine(storage_driver=_storage(kind, path))
    profile = engine.initialize_relationship(AGENT_ID, USER_ID, BLUEPRINT)
    nodes = [
        MemoryNode(
            node_id=f"baseline-node-{index:03d}",
            agent_id=AGENT_ID,
            user_id=USER_ID,
            relationship_id=profile.relationship_id,
            content=f"Synthetic portable memory {index:03d}.",
            node_type=MemoryType.FACT,
        )
        for index in range(64)
    ]
    engine.storage.save_nodes(AGENT_ID, USER_ID, nodes)
    for index in range(32):
        engine.storage.add_timeline_entry(
            AGENT_ID,
            USER_ID,
            f"Synthetic timeline entry {index:03d}.",
        )
    return engine, profile


def _memory_pack_metrics(root: Path, iterations: int) -> dict[str, Measurement]:
    metrics: dict[str, Measurement] = {}
    packs = {}
    engines: list[ERIIEngine] = []
    try:
        for source_kind in ("file", "sqlite"):
            source_root = root / f"memory-pack-{source_kind}"
            engine, profile = _build_source(source_root, source_kind)
            engines.append(engine)
            exported = engine.export_memory(AGENT_ID, USER_ID)
            if exported.relationship is None:
                raise RuntimeError("benchmark source relationship is missing")
            if exported.relationship.relationship_id != profile.relationship_id:
                raise RuntimeError("benchmark relationship identity changed")
            if len(exported.nodes) != 64:
                raise RuntimeError("benchmark source node count is wrong")
            packs[source_kind] = exported

            def export_operation(engine=engine) -> None:
                pack = engine.export_memory(AGENT_ID, USER_ID)
                if len(pack.nodes) != 64 or pack.relationship is None:
                    raise RuntimeError("MemoryPack export verification failed")

            metrics[f"memory_pack_export_{source_kind}_ms"] = _measure(
                iterations,
                export_operation,
            )

        for source_kind, pack in packs.items():
            for target_kind in ("file", "sqlite"):
                sample_index = 0

                def import_operation(
                    source_kind=source_kind,
                    target_kind=target_kind,
                    pack=pack,
                ) -> None:
                    nonlocal sample_index
                    sample_root = root / f"import-{source_kind}-to-{target_kind}-{sample_index}"
                    sample_index += 1
                    target_path = sample_root / (
                        "target-store" if target_kind == "file" else "target.db"
                    )
                    engine = ERIIEngine(storage_driver=_storage(target_kind, target_path))
                    try:
                        imported = engine.import_memory(pack)
                        if len(imported.nodes) != 64 or imported.relationship is None:
                            raise RuntimeError("MemoryPack import verification failed")
                        stored_nodes = engine.storage.load_nodes(AGENT_ID, USER_ID)
                        if len(stored_nodes) != 64:
                            raise RuntimeError("imported node count is wrong")
                    finally:
                        engine.close()

                metrics[f"memory_pack_import_{source_kind}_to_{target_kind}_ms"] = _measure(
                    iterations,
                    import_operation,
                )
    finally:
        for engine in engines:
            engine.close()
    return metrics


def _lifecycle_kind(kind: str) -> LifecycleTargetKind:
    return LifecycleTargetKind.FILE_STORAGE if kind == "file" else LifecycleTargetKind.SQLITE


def _lifecycle_metrics(root: Path, iterations: int) -> dict[str, Measurement]:
    metrics: dict[str, Measurement] = {}
    lifecycle = DataLifecycleCoordinator()
    for kind in ("file", "sqlite"):
        source_root = root / f"lifecycle-{kind}"
        engine, _profile = _build_source(source_root, kind)
        engine.close()
        source_path = source_root / ("source-store" if kind == "file" else "source.db")
        source_target = LifecycleTarget(_lifecycle_kind(kind), str(source_path))
        source = lifecycle.inspect(source_target)

        def inspect_operation() -> None:
            assessment = lifecycle.inspect(source_target)
            if assessment.fingerprint != source.fingerprint:
                raise RuntimeError("Lifecycle inspection fingerprint changed")

        metrics[f"lifecycle_inspect_{kind}_ms"] = _measure(iterations, inspect_operation)

        plan_index = 0

        def plan_operation() -> None:
            nonlocal plan_index
            destination = LifecycleTarget(
                LifecycleTargetKind.BACKUP,
                str(root / f"plan-{kind}-{plan_index}.eriibak"),
            )
            plan_index += 1
            plan = lifecycle.plan(BackupRequest(source=source, destination=destination))
            if plan.source.fingerprint != source.fingerprint:
                raise RuntimeError("Lifecycle plan source binding changed")

        metrics[f"lifecycle_plan_backup_{kind}_ms"] = _measure(iterations, plan_operation)

        execute_index = 0

        def execute_operation() -> None:
            nonlocal execute_index
            destination = LifecycleTarget(
                LifecycleTargetKind.BACKUP,
                str(root / f"execute-{kind}-{execute_index}.eriibak"),
            )
            execute_index += 1
            plan = lifecycle.plan(BackupRequest(source=source, destination=destination))
            report = lifecycle.execute(plan)
            if report.outcome is not LifecycleOutcome.APPLIED:
                raise RuntimeError("Lifecycle backup was not applied")
            if report.content_fingerprint != source.fingerprint:
                raise RuntimeError("Lifecycle backup report fingerprint changed")
            restored = lifecycle.inspect(destination)
            if restored.status.value != "current" or restored.fingerprint is None:
                raise RuntimeError("Lifecycle backup verification failed")

        metrics[f"lifecycle_execute_backup_{kind}_ms"] = _measure(
            iterations,
            execute_operation,
        )
    return metrics


def run(iterations: int) -> dict[str, object]:
    """Run all R0 benchmarks and return a content-free report."""

    if type(iterations) is not int or iterations < 5:
        raise ValueError("iterations must be an integer of at least 5")
    with tempfile.TemporaryDirectory(prefix="erii-refactoring-r0-") as directory:
        root = Path(directory)
        metrics = {
            **_memory_pack_metrics(root, iterations),
            **_lifecycle_metrics(root, iterations),
        }
    return {
        "suite_version": SUITE_VERSION,
        "source_version": __version__,
        "commit": _full_sha(),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
        "iterations": iterations,
        "fixture": {
            "nodes": 64,
            "timeline_entries": 32,
            "relationship": True,
            "content": "original-synthetic",
        },
        "metrics": {
            name: asdict(measurement)
            for name, measurement in sorted(metrics.items())
        },
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments(argv)
    try:
        report = run(args.iterations)
        payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            print(payload, end="")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8", newline="\n")
            print(f"Wrote refactoring baseline to {args.output}")
        return 0
    except Exception as exc:
        print(f"Refactoring baseline failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
