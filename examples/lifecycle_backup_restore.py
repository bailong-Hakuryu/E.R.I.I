"""Create and restore a verified E.R.I.I. lifecycle backup.

Run this only while the source has no active writer. The example restores to a
new path and never overwrites existing data.
"""

from pathlib import Path
import tempfile

from erii import (
    BackupRequest,
    DataLifecycleCoordinator,
    LifecyclePlan,
    LifecycleTarget,
    LifecycleTargetKind,
    RestoreRequest,
    SQLiteStorage,
)


def backup_and_restore(source_path: Path, backup_path: Path, restore_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    restore_path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle = DataLifecycleCoordinator()
    source_target = LifecycleTarget(
        LifecycleTargetKind.SQLITE,
        str(source_path),
    )
    backup_target = LifecycleTarget(
        LifecycleTargetKind.BACKUP,
        str(backup_path),
    )
    source = lifecycle.inspect(source_target)
    backup_plan = lifecycle.plan(BackupRequest(source=source, destination=backup_target))

    # Plans contain no conversation text, but they do contain local paths and
    # fingerprints. Persist them as protected operational data when required.
    durable_plan = backup_plan.to_json()
    backup_report = lifecycle.execute(LifecyclePlan.from_json(durable_plan))
    print("backup:", backup_report.outcome.value)

    backup = lifecycle.inspect(backup_target)
    restore_target = LifecycleTarget(
        LifecycleTargetKind.SQLITE,
        str(restore_path),
    )
    restore_plan = lifecycle.plan(RestoreRequest(backup=backup, destination=restore_target))
    restore_report = lifecycle.execute(restore_plan)
    print("restore:", restore_report.outcome.value)


def main() -> None:
    # A disposable source keeps the example safe to run as-is. In a real host,
    # pass quiescent application paths to backup_and_restore().
    with tempfile.TemporaryDirectory(prefix="erii-lifecycle-example-") as root_value:
        root = Path(root_value)
        source_path = root / "data" / "erii.db"
        source_path.parent.mkdir()
        SQLiteStorage(str(source_path)).save_core_memory(
            "demo_agent",
            "demo_user",
            "A disposable persona used only by this example.",
        )
        backup_and_restore(
            source_path,
            root / "backups" / "erii.eriibak",
            root / "restored" / "erii.db",
        )


if __name__ == "__main__":
    main()
