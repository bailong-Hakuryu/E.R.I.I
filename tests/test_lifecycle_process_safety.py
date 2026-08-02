"""Process-exclusion and durability regressions for lifecycle execution."""

import errno
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from erii.data_lifecycle import (
    BackupRequest,
    DataLifecycleCoordinator,
    LifecycleTarget,
    LifecycleTargetKind,
)
from erii.errors import LifecycleConflictError, StorageWriteError


def _hold_documented_destination_lock(
    lock_path_value: str,
    status_writer,
    release_event,
) -> None:
    """Holds the documented lifecycle lock from a spawn-safe child process."""
    handle = None
    locked = False
    try:
        handle = Path(lock_path_value).open("a+b")
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        status_writer.send(("locked", ""))
        release_event.wait(20)
    except BaseException as exc:  # pragma: no cover - reported to the parent
        status_writer.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        if locked and handle is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        if handle is not None:
            handle.close()
        status_writer.close()


class LifecycleProcessSafetyTests(unittest.TestCase):
    @staticmethod
    def _backup_plan(root: Path):
        lifecycle = DataLifecycleCoordinator()
        source_path = root / "source-store"
        source_path.mkdir()
        source_target = LifecycleTarget(
            kind=LifecycleTargetKind.FILE_STORAGE,
            path=str(source_path),
        )
        destination = LifecycleTarget(
            kind=LifecycleTargetKind.BACKUP,
            path=str(root / "snapshot.eriibak"),
        )
        plan = lifecycle.plan(
            BackupRequest(
                source=lifecycle.inspect(source_target),
                destination=destination,
            )
        )
        return lifecycle, plan, destination

    def test_execute_rejects_a_destination_locked_by_another_spawned_process(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            lifecycle, plan, destination = self._backup_plan(root)
            destination_path = Path(destination.path)
            lock_path = destination_path.parent / (f".{destination_path.name}.erii-lifecycle.lock")
            context = multiprocessing.get_context("spawn")
            status_reader, status_writer = context.Pipe(duplex=False)
            release_event = context.Event()
            process = context.Process(
                target=_hold_documented_destination_lock,
                args=(str(lock_path), status_writer, release_event),
            )
            process.start()
            status_writer.close()
            try:
                self.assertTrue(
                    status_reader.poll(15),
                    "spawned lock holder did not report readiness",
                )
                status, detail = status_reader.recv()
                self.assertEqual(status, "locked", detail)

                with self.assertRaises(LifecycleConflictError):
                    lifecycle.execute(plan)
            finally:
                release_event.set()
                process.join(15)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                status_reader.close()

            self.assertEqual(process.exitcode, 0)
            self.assertFalse(Path(destination.path).exists())

    def test_directory_fsync_failure_cannot_return_applied(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            try:
                probe_descriptor = os.open(root, os.O_RDONLY)
            except OSError:
                self.skipTest("this platform does not expose directory descriptors to fsync")
            else:
                os.close(probe_descriptor)

            lifecycle, plan, destination = self._backup_plan(root)
            real_fsync = os.fsync

            def fail_directory_fsync(descriptor: int) -> None:
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError("injected directory fsync failure")
                real_fsync(descriptor)

            with mock.patch(
                "erii.data_lifecycle.os.fsync",
                side_effect=fail_directory_fsync,
            ):
                with self.assertRaises(StorageWriteError):
                    lifecycle.execute(plan)

            self.assertFalse(Path(destination.path).exists())

    def test_windows_best_effort_does_not_swallow_real_directory_io_errors(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            lifecycle, plan, destination = self._backup_plan(root)
            real_open = os.open

            def fail_target_parent_open(path, flags, mode=0o777, *, dir_fd=None):
                if Path(path) == root and not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
                    raise OSError(errno.EIO, "injected directory I/O failure")
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with mock.patch(
                "erii.data_lifecycle.os.open",
                side_effect=fail_target_parent_open,
            ):
                with self.assertRaises(StorageWriteError):
                    lifecycle.execute(plan)

            self.assertFalse(Path(destination.path).exists())


if __name__ == "__main__":
    unittest.main()
