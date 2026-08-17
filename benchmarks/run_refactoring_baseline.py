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
from typing import Any, Callable, Mapping

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
REGRESSION_THRESHOLD_PCT = 10.0
REGRESSION_THRESHOLD_MS = 2.0
BASELINE_JITTER_THRESHOLD_PCT = 8.0
METRIC_REGRESSION_BUDGETS = {
    # R1B adds a durable multi-file before-image journal and commit marker for
    # FileStorage imports.  On Windows this costs four or more fsync/replace
    # boundaries; the explicit budget preserves that correctness tradeoff
    # while still rejecting further growth.
    "memory_pack_import_file_to_file_ms": (55.0, 20.0),
    "memory_pack_import_sqlite_to_file_ms": (55.0, 20.0),
}
AGENT_ID = "baseline-agent"
USER_ID = "baseline-user"
BLUEPRINT = "An original synthetic character who values precise continuity."


@dataclass(frozen=True)
class Measurement:
    median_ms: float
    minimum_ms: float
    maximum_ms: float
    samples_ms: tuple[float, ...]


@dataclass(frozen=True)
class MetricComparison:
    """The median comparison for one benchmark metric."""

    name: str
    baseline_ms: float
    current_ms: float
    delta_pct: float
    baseline_jitter_pct: float
    threshold_pct: float = REGRESSION_THRESHOLD_PCT
    threshold_ms: float = REGRESSION_THRESHOLD_MS

    @property
    def delta_ms(self) -> float:
        return self.current_ms - self.baseline_ms

    @property
    def is_regression(self) -> bool:
        return bool(
            self.delta_pct > self.threshold_pct
            and self.delta_ms > self.threshold_ms
        )

    @property
    def uses_custom_budget(self) -> bool:
        return bool(
            self.threshold_pct != REGRESSION_THRESHOLD_PCT
            or self.threshold_ms != REGRESSION_THRESHOLD_MS
        )

    @property
    def baseline_is_unstable(self) -> bool:
        return self.baseline_jitter_pct > BASELINE_JITTER_THRESHOLD_PCT


@dataclass(frozen=True)
class BaselineComparison:
    """A validated comparison, or an explicit environment-incompatible skip."""

    compatible: bool
    environment_reason: str | None
    metrics: tuple[MetricComparison, ...]

    @property
    def regressions(self) -> tuple[MetricComparison, ...]:
        return tuple(metric for metric in self.metrics if metric.is_regression)

    @property
    def blocking_regressions(self) -> tuple[MetricComparison, ...]:
        """Regressions backed by a stable enough frozen measurement."""
        return tuple(
            metric
            for metric in self.regressions
            if not metric.baseline_is_unstable
        )

    @property
    def inconclusive_regressions(self) -> tuple[MetricComparison, ...]:
        """Large deltas that cannot be judged because the baseline is noisy."""
        return tuple(
            metric
            for metric in self.regressions
            if metric.baseline_is_unstable
        )

    @property
    def unstable_baseline_metrics(self) -> tuple[MetricComparison, ...]:
        return tuple(metric for metric in self.metrics if metric.baseline_is_unstable)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--compare",
        type=Path,
        help="compare medians with a frozen baseline JSON report",
    )
    return parser.parse_args(argv)


def _load_json_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read benchmark report {path}: {exc}") from exc
    if type(value) is not dict:
        raise ValueError(f"benchmark report {path} must contain a JSON object")
    return value


def _platform_family(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("benchmark environment platform must be a non-empty string")
    normalized = value.strip().lower()
    if normalized.startswith("windows"):
        return "windows"
    if normalized.startswith("linux"):
        return "linux"
    if normalized.startswith(("macos", "darwin")):
        return "macos"
    return normalized.split("-", 1)[0]


def _platform_build_key(value: object) -> str:
    """Returns the full OS identity used to compare benchmark environments."""
    if type(value) is not str or not value.strip():
        raise ValueError("benchmark environment platform must be a non-empty string")
    return value.strip().lower()


def _python_major_minor(value: object) -> tuple[int, int]:
    if type(value) is not str:
        raise ValueError("benchmark environment python must be a version string")
    pieces = value.split(".")
    if len(pieces) < 2:
        raise ValueError("benchmark environment python must include major and minor")
    try:
        return int(pieces[0]), int(pieces[1])
    except ValueError as exc:
        raise ValueError("benchmark environment python is invalid") from exc


def _validate_report(report: Mapping[str, Any], label: str) -> None:
    if type(report) is not dict:
        raise ValueError(f"{label} report must contain a JSON object")
    if report.get("suite_version") != SUITE_VERSION:
        raise ValueError(f"{label} suite_version does not match {SUITE_VERSION}")
    fixture = report.get("fixture")
    expected_fixture = {
        "nodes": 64,
        "timeline_entries": 32,
        "relationship": True,
        "content": "original-synthetic",
    }
    if fixture != expected_fixture:
        raise ValueError(f"{label} fixture does not match the frozen benchmark fixture")
    environment = report.get("environment")
    if type(environment) is not dict:
        raise ValueError(f"{label} environment is missing or invalid")
    _python_major_minor(environment.get("python"))
    _platform_family(environment.get("platform"))
    if type(environment.get("implementation")) is not str:
        raise ValueError(f"{label} environment implementation is missing or invalid")
    metrics = report.get("metrics")
    if type(metrics) is not dict or not metrics:
        raise ValueError(f"{label} metrics are missing or invalid")
    for name, measurement in metrics.items():
        if type(name) is not str or type(measurement) is not dict:
            raise ValueError(f"{label} metric {name!r} is invalid")
        median = measurement.get("median_ms")
        samples = measurement.get("samples_ms")
        if type(median) not in (int, float) or median <= 0:
            raise ValueError(f"{label} metric {name!r} has an invalid median")
        if type(samples) not in (list, tuple) or len(samples) < 2:
            raise ValueError(f"{label} metric {name!r} needs at least two samples")
        if any(type(sample) not in (int, float) or sample <= 0 for sample in samples):
            raise ValueError(f"{label} metric {name!r} has invalid samples")


def _environment_mismatch(
    current: Mapping[str, Any], baseline: Mapping[str, Any]
) -> str | None:
    current_environment = current["environment"]
    baseline_environment = baseline["environment"]
    if current_environment["implementation"] != baseline_environment["implementation"]:
        return (
            "implementation differs "
            f"({current_environment['implementation']} vs {baseline_environment['implementation']})"
        )
    current_python = _python_major_minor(current_environment["python"])
    baseline_python = _python_major_minor(baseline_environment["python"])
    if current_python != baseline_python:
        return f"Python major/minor differs ({current_python[0]}.{current_python[1]} vs {baseline_python[0]}.{baseline_python[1]})"
    current_platform = _platform_family(current_environment["platform"])
    baseline_platform = _platform_family(baseline_environment["platform"])
    if current_platform != baseline_platform:
        return f"platform family differs ({current_platform} vs {baseline_platform})"
    current_build = _platform_build_key(current_environment["platform"])
    baseline_build = _platform_build_key(baseline_environment["platform"])
    if current_build != baseline_build:
        return f"platform build differs ({current_build} vs {baseline_build})"
    return None


def _jitter_pct(measurement: Mapping[str, Any]) -> float:
    """Returns robust sample jitter as median absolute deviation percent."""
    samples = [float(sample) for sample in measurement["samples_ms"]]
    median = float(measurement["median_ms"])
    absolute_deviations = [abs(sample - median) for sample in samples]
    return statistics.median(absolute_deviations) / median * 100.0


def unstable_report_metrics(report: Mapping[str, Any]) -> tuple[str, ...]:
    """Returns metrics whose robust sample jitter is too high for enforcement."""
    _validate_report(report, "benchmark")
    return tuple(
        name
        for name, measurement in sorted(report["metrics"].items())
        if _jitter_pct(measurement) > BASELINE_JITTER_THRESHOLD_PCT
    )


def compare_reports(
    current: Mapping[str, Any], baseline: Mapping[str, Any]
) -> BaselineComparison:
    """Validate and compare two reports using median latency.

    An interpreter or OS mismatch is an explicit skip because benchmark numbers
    from different execution environments are not an actionable regression.
    Contract and fixture mismatches remain hard errors.
    """

    _validate_report(current, "current")
    _validate_report(baseline, "baseline")
    mismatch = _environment_mismatch(current, baseline)
    if mismatch is not None:
        return BaselineComparison(False, mismatch, ())
    current_metrics = current["metrics"]
    baseline_metrics = baseline["metrics"]
    if set(current_metrics) != set(baseline_metrics):
        missing = sorted(set(baseline_metrics) - set(current_metrics))
        extra = sorted(set(current_metrics) - set(baseline_metrics))
        raise ValueError(
            "current and baseline metric sets differ "
            f"(missing={missing}, extra={extra})"
        )
    comparisons = []
    for name in sorted(baseline_metrics):
        baseline_measurement = baseline_metrics[name]
        current_measurement = current_metrics[name]
        baseline_median = float(baseline_measurement["median_ms"])
        current_median = float(current_measurement["median_ms"])
        threshold_pct, threshold_ms = METRIC_REGRESSION_BUDGETS.get(
            name,
            (REGRESSION_THRESHOLD_PCT, REGRESSION_THRESHOLD_MS),
        )
        comparisons.append(
            MetricComparison(
                name=name,
                baseline_ms=baseline_median,
                current_ms=current_median,
                delta_pct=(current_median - baseline_median) / baseline_median * 100.0,
                baseline_jitter_pct=_jitter_pct(baseline_measurement),
                threshold_pct=threshold_pct,
                threshold_ms=threshold_ms,
            )
        )
    return BaselineComparison(True, None, tuple(comparisons))


def render_comparison(comparison: BaselineComparison) -> str:
    """Render a concise, CI-friendly comparison summary."""

    if not comparison.compatible:
        return f"Performance comparison skipped: {comparison.environment_reason}."
    lines = ["Performance comparison (median latency):"]
    for metric in comparison.metrics:
        marker = " REGRESSION" if metric.is_regression else ""
        if (
            not metric.is_regression
            and metric.uses_custom_budget
            and metric.delta_pct > REGRESSION_THRESHOLD_PCT
        ):
            marker = " WITHIN DURABILITY BUDGET"
        lines.append(
            f"  {metric.name}: baseline={metric.baseline_ms:.3f} ms "
            f"current={metric.current_ms:.3f} ms delta={metric.delta_pct:+.1f}% "
            f"({metric.delta_ms:+.3f} ms){marker}"
        )
    if comparison.unstable_baseline_metrics:
        names = ", ".join(metric.name for metric in comparison.unstable_baseline_metrics)
        lines.append(
            "Baseline unstable (>8% median absolute deviation): "
            f"{names}. Re-record the frozen baseline before using it for a release decision."
        )
    if comparison.blocking_regressions:
        names = ", ".join(metric.name for metric in comparison.blocking_regressions)
        lines.append(
            "Performance regression gate failed against the metric budgets: "
            f"{names}"
        )
    elif comparison.inconclusive_regressions:
        names = ", ".join(metric.name for metric in comparison.inconclusive_regressions)
        lines.append(
            "Performance regression gate inconclusive: >10% deltas rely on "
            f"an unstable baseline ({names})."
        )
    elif comparison.unstable_baseline_metrics:
        lines.append(
            "Performance regression gate inconclusive: baseline unstable."
        )
    else:
        lines.append("Performance regression gate passed.")
    return "\n".join(lines)


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
    # Pytest and other embedders replace stdout with a capture stream whose
    # reconfigure() implementation may close the stream during teardown.
    if sys.stdout is sys.__stdout__ and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _arguments(argv)
    try:
        report = run(args.iterations)
        comparison: BaselineComparison | None = None
        if args.compare is not None:
            comparison = compare_reports(report, _load_json_report(args.compare))
        payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            print(payload, end="")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8", newline="\n")
            print(f"Wrote refactoring baseline to {args.output}")
        if comparison is not None:
            print(render_comparison(comparison))
            if comparison.blocking_regressions:
                return 1
        return 0
    except Exception as exc:
        print(f"Refactoring baseline failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
