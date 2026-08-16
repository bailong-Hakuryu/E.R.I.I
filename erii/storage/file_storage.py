"""JSON File Storage driver for E.R.I.I. Engine.

Provides zero-dependency, file-based persistence for memory nodes,
core persona impressions, and experiential timeline events.

Follows Google Python Style Guide.
"""

import base64
from dataclasses import replace
from datetime import datetime
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union
import uuid

from erii.models.adjudication import (
    AdjudicationRecord,
    CandidateConflictError,
    PersonaGrowthConflictError,
    PersonaGrowthProposal,
    PersonaGrowthStatus,
)
from erii.models.archival import (
    ArchivalConflictError,
    ArchivalNotFoundError,
    ArchivalPhase,
    ArchivalRecord,
    ArchivalStatus,
    ArchivalTombstone,
    CommitPermit,
    PreparedArchivalBatch,
    TimelineEntry,
    merge_archival_tombstone_batch,
)
from erii.models.consolidation import (
    PersonaReflectionDecisionRecord,
    PersonaReflectionRecord,
    ReflectionProvenanceState,
    RelationshipProcessingConflictError,
    RelationshipProcessingRun,
)
from erii.models.consequence import (
    ConsequenceConflictError,
    NarrativeTensionConflictError,
    NarrativeTensionLink,
    RelationshipConsequence,
)
from erii.models.provenance import ArtifactProvenanceState
from erii.models.node import MemoryNode
from erii.models.persona import (
    PersonaCompilationConflictError,
    PersonaCompilationProposal,
    PersonaCompilationStatus,
    PersonaManifest,
)
from erii.models.relationship import (
    EventConflictError,
    IdentityKind,
    PersonaConflictError,
    RelationshipEvent,
    RelationshipProfile,
)
from erii.models.turn import (
    ReplyAttemptConflictError,
    ReplyAttemptRecord,
    TurnConflictError,
    TurnNotFoundError,
    TurnRecord,
    TurnStatus,
    TurnTerminalConflictError,
)
from erii.core.temporal_history import TemporalHistoryValidator
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.archival import ArchivalTombstoneValidationSource
from erii.storage.base import BaseStorage, cross_process_file_lock
from erii.storage.errors import StorageIntegrityError, StorageWriteError
from erii.storage.memory_pack import AtomicMemoryPackWriteStoreV1
from erii.storage.timeline_order import timeline_entry_order_key
from erii.storage.turn_context import (
    TurnContextSourceSnapshot,
    validate_turn_context_baseline_authority,
)
from erii.models.turn_context import TurnContextBaseline


_MemoryPackResultT = TypeVar("_MemoryPackResultT")


def _windows_extended_path(path: str) -> str:
    """Makes one internal absolute path independent of legacy MAX_PATH."""
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _memory_pack_write_barrier(method):
    """Serializes one FileStorage writer with whole-pack transactions."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._memory_pack_write_barrier_guard():
            return method(self, *args, **kwargs)

    return wrapped


def _turn_context_snapshot_writer(method):
    """Runs a FileStorage writer outside every finer-grained storage lock."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._turn_context_snapshot_guard():
            return method(self, *args, **kwargs)

    return wrapped


class FileStorage(BaseStorage):
    """File-based memory storage using JSON files."""

    def __init__(self, root_dir: str = "./erii_memory") -> None:
        """Initializes FileStorage.

        Args:
            root_dir: Root directory path for memory file storage.
        """
        super().__init__()
        self.root_dir = os.path.abspath(root_dir)
        self._io_root_dir = _windows_extended_path(self.root_dir)
        os.makedirs(self._io_root_dir, exist_ok=True)
        self._memory_pack_write_local = threading.local()
        with self._memory_pack_write_barrier_guard():
            pass
        self._recover_persona_approval_transactions()

    def _get_user_dir(self, agent_id: str, user_id: str) -> str:
        """Computes and validates target directory path.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.

        Returns:
            Absolute path to directory.
        """
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        # Hash unicode keys to avoid OS filesystem encoding issues while preserving human-readable prefix
        agent_hash = hashlib.sha256(clean_agent.encode("utf-8")).hexdigest()[:8]
        user_hash = hashlib.sha256(clean_user.encode("utf-8")).hexdigest()[:8]
        
        safe_agent_dir = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', clean_agent)}_{agent_hash}"
        safe_user_dir = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', clean_user)}_{user_hash}"

        path = os.path.join(self._io_root_dir, safe_agent_dir, safe_user_dir)
        os.makedirs(path, exist_ok=True)
        return path

    def _get_nodes_path(self, agent_id: str, user_id: str) -> str:
        return os.path.join(self._get_user_dir(agent_id, user_id), "nodes.json")

    def _get_core_path(self, agent_id: str, user_id: str) -> str:
        return os.path.join(self._get_user_dir(agent_id, user_id), "core_memory.json")

    def _get_timeline_path(self, agent_id: str, user_id: str) -> str:
        return os.path.join(self._get_user_dir(agent_id, user_id), "timeline.json")

    def _get_relationship_path(self, agent_id: str, user_id: str) -> str:
        return os.path.join(self._get_user_dir(agent_id, user_id), "relationship.json")

    def _get_identity_registry_path(self) -> str:
        return os.path.join(self._io_root_dir, "_relationship_identities.json")

    def _get_relationship_events_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_relationship_events")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_relationship_adjudications_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_relationship_adjudications")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_relationship_consequences_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_relationship_consequences")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_narrative_tension_links_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_narrative_tension_links")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_persona_growth_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_persona_growth")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_turn_records_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_turn_records")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_reply_attempts_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_reply_attempts")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_archival_state_path(self) -> str:
        return os.path.join(self._io_root_dir, "_archival_state.json")

    def _get_relationship_processing_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_relationship_processing")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_turn_lock_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_turn_locks")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.lock")

    def _get_relationship_history_lock_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_relationship_history_locks")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.lock")

    def _get_turn_context_snapshot_lock_path(self) -> str:
        return os.path.join(self._io_root_dir, "_turn_context_snapshot.lock")

    def _get_relationship_processing_lock_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_relationship_processing_locks")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.lock")

    def _get_memory_pack_write_lock_path(self) -> str:
        """Returns the stable root-wide whole-pack writer lock path."""
        # Reuse the pre-existing root-wide snapshot lock. This keeps the flat-v2
        # control-file vocabulary unchanged, remains compatible with older
        # history writers, and avoids a nested long-path lock directory.
        return self._get_turn_context_snapshot_lock_path()

    def _get_memory_pack_write_journal_path(self) -> str:
        """Returns the flat-v2 before-image transaction journal path."""
        return os.path.join(self._io_root_dir, "_memory_pack_write_transaction.json")

    @contextmanager
    def _memory_pack_write_barrier_guard(self):
        """Places public RMW operations outside a whole-pack callback."""
        depth = getattr(self._memory_pack_write_local, "barrier_depth", 0)
        if depth:
            self._memory_pack_write_local.barrier_depth = depth + 1
            try:
                yield
            finally:
                self._memory_pack_write_local.barrier_depth -= 1
            return

        with self._turn_context_snapshot_guard():
            self._memory_pack_write_local.barrier_depth = 1
            try:
                self._recover_memory_pack_write_transaction_locked()
                yield
            finally:
                del self._memory_pack_write_local.barrier_depth

    @contextmanager
    def relationship_processing_guard(self, relationship_id: str):
        """Serializes host model calls before their decisions become durable."""
        with cross_process_file_lock(
            self._get_relationship_processing_lock_path(relationship_id)
        ):
            yield

    @contextmanager
    def _turn_context_snapshot_guard(self):
        """Serializes coherent Turn Context reads with every contributing writer."""
        with cross_process_file_lock(self._get_turn_context_snapshot_lock_path()):
            yield

    @contextmanager
    def _turn_guard(self, relationship_id: str):
        """Serializes turn aggregates across FileStorage instances/processes."""
        with self.lock_manager.lock("__turn_records__", relationship_id):
            lock_path = self._get_turn_lock_path(relationship_id)
            with open(lock_path, "a+b") as lock_file:
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                    os.fsync(lock_file.fileno())
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _relationship_history_guard(self, relationship_id: str):
        """Serializes one relationship history across instances and processes."""
        with self.lock_manager.lock("__relationship_history__", relationship_id):
            lock_path = self._get_relationship_history_lock_path(relationship_id)
            with open(lock_path, "a+b") as lock_file:
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                    os.fsync(lock_file.fileno())
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _get_persona_compilation_path(self, blueprint_id: str) -> str:
        digest = hashlib.sha256(blueprint_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self._io_root_dir, "_persona_compilations")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_persona_approval_journal_dir(self) -> str:
        directory = os.path.join(self._io_root_dir, "_persona_approval_transactions")
        os.makedirs(directory, exist_ok=True)
        return directory

    def _get_persona_approval_journal_path(
        self,
        relationship_id: str,
        proposal_id: str,
        revision: int,
    ) -> str:
        digest = hashlib.sha256(
            f"{relationship_id}\0{proposal_id}\0{revision}".encode("utf-8")
        ).hexdigest()
        return os.path.join(self._get_persona_approval_journal_dir(), f"{digest}.json")

    def atomic_memory_pack_write_store_v1(
        self,
    ) -> AtomicMemoryPackWriteStoreV1:
        """Returns this adapter's flat-v2 whole-pack write capability."""
        return self

    def execute_memory_pack_write(
        self,
        target_agent: str,
        target_user: str,
        relationship_id: Optional[str],
        operation: Callable[[Any], _MemoryPackResultT],
    ) -> _MemoryPackResultT:
        """Runs one payload callback with durable before-image rollback."""
        if getattr(self._memory_pack_write_local, "transaction", None) is not None:
            raise RuntimeError("nested MemoryPack write transactions are invalid")

        with self._memory_pack_write_barrier_guard():
            journal = {
                "version": 1,
                "state": "active",
                "transaction_id": uuid.uuid4().hex,
                "target_agent": target_agent,
                "target_user": target_user,
                "relationship_id": relationship_id,
                "entries": [],
            }
            self._write_memory_pack_journal_raw(journal)
            self._memory_pack_write_local.transaction = journal
            try:
                try:
                    result = operation(self)
                except BaseException as operation_error:
                    self._rollback_memory_pack_write_after_failure(
                        journal,
                        operation_error,
                    )
                    raise

                journal["state"] = "committed"
                try:
                    self._write_memory_pack_journal_raw(journal)
                except BaseException as publish_error:
                    try:
                        durable_journal = self._load_memory_pack_write_journal()
                    except BaseException as verification_error:
                        if hasattr(publish_error, "add_note"):
                            publish_error.add_note(
                                "FileStorage could not resolve whether its "
                                "MemoryPack commit marker became durable"
                            )
                        raise publish_error from verification_error
                    expected_active = dict(journal)
                    expected_active["state"] = "active"
                    if durable_journal == expected_active:
                        self._rollback_memory_pack_write_after_failure(
                            journal,
                            publish_error,
                        )
                        raise
                    if durable_journal != journal:
                        integrity_error = StorageIntegrityError(
                            "MemoryPack transaction commit state is ambiguous"
                        )
                        if hasattr(publish_error, "add_note"):
                            publish_error.add_note(str(integrity_error))
                        raise publish_error from integrity_error

                # Success is reported only after the final absence of the
                # journal is directory-durable.  A committed journal remains
                # restart-safe if unlink itself fails; an unlink fsync failure
                # is an indeterminate commit and must reach the caller.
                self._remove_memory_pack_write_journal()
                return result
            finally:
                del self._memory_pack_write_local.transaction

    def _rollback_memory_pack_write_after_failure(
        self,
        journal: Dict[str, Any],
        operation_error: BaseException,
    ) -> None:
        """Restores the baseline or preserves an active recovery journal."""
        try:
            self._rollback_memory_pack_write_journal(journal)
            self._remove_memory_pack_write_journal()
        except BaseException as rollback_error:
            if hasattr(operation_error, "add_note"):
                operation_error.add_note(
                    "FileStorage rollback remains recoverable from its "
                    "active MemoryPack journal"
                )
            raise operation_error from rollback_error

    def _root_relative_memory_pack_path(self, file_path: str) -> str:
        """Returns a journal-safe root-relative path for one target file."""
        lexical_root = os.path.normpath(os.path.abspath(self._io_root_dir))
        lexical_path = os.path.normpath(os.path.abspath(file_path))
        resolved_root = os.path.realpath(lexical_root)
        resolved_path = os.path.realpath(lexical_path)
        try:
            lexical_common = os.path.commonpath((lexical_root, lexical_path))
            resolved_common = os.path.commonpath((resolved_root, resolved_path))
        except ValueError as exc:
            raise StorageWriteError(
                "MemoryPack transaction target escapes the FileStorage root"
            ) from exc
        if (
            os.path.normcase(lexical_common) != os.path.normcase(lexical_root)
            or os.path.normcase(resolved_common) != os.path.normcase(resolved_root)
        ):
            raise StorageWriteError(
                "MemoryPack transaction target escapes the FileStorage root"
            )
        relative_path = os.path.relpath(lexical_path, lexical_root)
        if relative_path in {"", os.curdir}:
            raise StorageWriteError("MemoryPack transaction target must be a file")
        return relative_path.replace(os.sep, "/")

    def _resolve_memory_pack_journal_path(self, relative_path: str) -> str:
        """Resolves one untrusted journal path without leaving the storage root."""
        if not isinstance(relative_path, str) or "\\" in relative_path:
            raise StorageIntegrityError("MemoryPack transaction path is invalid")
        parts = relative_path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise StorageIntegrityError("MemoryPack transaction path is invalid")
        candidate = os.path.join(self._io_root_dir, *parts)
        try:
            checked = self._root_relative_memory_pack_path(candidate)
        except StorageWriteError as exc:
            raise StorageIntegrityError(
                "MemoryPack transaction path escapes the FileStorage root"
            ) from exc
        if checked != relative_path:
            raise StorageIntegrityError("MemoryPack transaction path is not canonical")
        control_paths = {
            os.path.normcase(os.path.realpath(self._get_memory_pack_write_lock_path())),
            os.path.normcase(
                os.path.realpath(self._get_memory_pack_write_journal_path())
            ),
        }
        if os.path.normcase(os.path.realpath(candidate)) in control_paths:
            raise StorageIntegrityError("MemoryPack transaction targets control state")
        return candidate

    @staticmethod
    def _fsync_parent_directory(file_path: str) -> None:
        """Durably records directory-entry changes where the platform allows."""
        if os.name == "nt":
            return
        directory = os.path.dirname(file_path) or os.curdir
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(directory, flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @classmethod
    def _write_bytes_atomic_raw(
        cls,
        file_path: str,
        payload: bytes,
        *,
        accept_replace_as_commit: bool = False,
    ) -> None:
        """Atomically writes raw bytes without entering the undo journal."""
        temp_path = f"{file_path}.{uuid.uuid4().hex}.tmp"
        temp_io_path = _windows_extended_path(temp_path)
        try:
            with open(temp_io_path, "wb") as file_obj:
                file_obj.write(payload)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            os.replace(temp_io_path, file_path)
            try:
                cls._fsync_parent_directory(file_path)
            except BaseException:
                if not accept_replace_as_commit:
                    raise
                # The committed journal marker is the one deliberate exception:
                # after replace succeeds it is the transaction commit point.
                # Starting rollback here could leave a durable "committed"
                # marker beside partially restored baseline files.
        except Exception as exc:
            raise StorageWriteError(
                f"failed to atomically write {os.path.basename(file_path)}"
            ) from exc
        finally:
            if os.path.exists(temp_io_path):
                try:
                    os.remove(temp_io_path)
                except OSError:
                    pass

    @classmethod
    def _write_json_atomic_raw(
        cls,
        file_path: str,
        data: Any,
        *,
        accept_replace_as_commit: bool = False,
    ) -> None:
        """Writes JSON without recursively journaling transaction control data."""
        payload = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        cls._write_bytes_atomic_raw(
            file_path,
            payload,
            accept_replace_as_commit=accept_replace_as_commit,
        )

    def _write_memory_pack_journal_raw(self, journal: Dict[str, Any]) -> None:
        """Durably replaces the transaction journal outside normal writes."""
        self._write_json_atomic_raw(
            self._get_memory_pack_write_journal_path(),
            journal,
            accept_replace_as_commit=journal.get("state") == "committed",
        )

    def _remove_memory_pack_write_journal(self) -> None:
        """Deletes only the stable whole-pack journal path."""
        journal_path = self._get_memory_pack_write_journal_path()
        if not os.path.exists(journal_path):
            return
        os.remove(journal_path)
        self._fsync_parent_directory(journal_path)

    def _record_memory_pack_before_image(self, file_path: str) -> None:
        """Persists one target's original bytes before its first replacement."""
        journal = getattr(self._memory_pack_write_local, "transaction", None)
        if journal is None:
            return
        relative_path = self._root_relative_memory_pack_path(file_path)
        if any(
            entry.get("path") == relative_path
            for entry in journal["entries"]
        ):
            return

        target_path = self._resolve_memory_pack_journal_path(relative_path)
        existed = os.path.lexists(target_path)
        before_image = None
        if existed:
            if os.path.islink(target_path) or not os.path.isfile(target_path):
                raise StorageIntegrityError(
                    "MemoryPack transaction target must be a regular file"
                )
            with open(target_path, "rb") as file_obj:
                before_image = base64.b64encode(file_obj.read()).decode("ascii")
        journal["entries"].append(
            {
                "path": relative_path,
                "existed": existed,
                "before_image": before_image,
            }
        )
        self._write_memory_pack_journal_raw(journal)

    def _validate_memory_pack_write_journal(
        self,
        journal: Any,
    ) -> Dict[str, Any]:
        """Validates recovery input before touching any recorded target."""
        if (
            not isinstance(journal, dict)
            or journal.get("version") != 1
            or journal.get("state") not in {"active", "committed"}
            or not isinstance(journal.get("entries"), list)
        ):
            raise StorageIntegrityError("MemoryPack transaction journal is invalid")
        seen_paths = set()
        for entry in journal["entries"]:
            if not isinstance(entry, dict):
                raise StorageIntegrityError(
                    "MemoryPack transaction journal entry is invalid"
                )
            relative_path = entry.get("path")
            existed = entry.get("existed")
            before_image = entry.get("before_image")
            self._resolve_memory_pack_journal_path(relative_path)
            if relative_path in seen_paths or not isinstance(existed, bool):
                raise StorageIntegrityError(
                    "MemoryPack transaction journal entry is invalid"
                )
            seen_paths.add(relative_path)
            if existed:
                if not isinstance(before_image, str):
                    raise StorageIntegrityError(
                        "MemoryPack transaction before-image is invalid"
                    )
                try:
                    base64.b64decode(before_image.encode("ascii"), validate=True)
                except (UnicodeEncodeError, ValueError) as exc:
                    raise StorageIntegrityError(
                        "MemoryPack transaction before-image is invalid"
                    ) from exc
            elif before_image is not None:
                raise StorageIntegrityError(
                    "MemoryPack transaction before-image is invalid"
                )
        return journal

    def _load_memory_pack_write_journal(self) -> Dict[str, Any]:
        """Loads and validates the stable transaction journal."""
        journal_path = self._get_memory_pack_write_journal_path()
        if not os.path.exists(journal_path):
            raise StorageIntegrityError(
                "MemoryPack transaction journal disappeared during commit"
            )
        return self._validate_memory_pack_write_journal(
            self._read_json(journal_path, "MemoryPack transaction journal")
        )

    def _rollback_memory_pack_write_journal(
        self,
        journal: Dict[str, Any],
    ) -> None:
        """Restores exact original bytes and removes only recorded new files."""
        validated = self._validate_memory_pack_write_journal(journal)
        for entry in reversed(validated["entries"]):
            target_path = self._resolve_memory_pack_journal_path(entry["path"])
            if entry["existed"]:
                before_image = base64.b64decode(
                    entry["before_image"].encode("ascii"),
                    validate=True,
                )
                self._write_bytes_atomic_raw(target_path, before_image)
            elif os.path.lexists(target_path):
                if os.path.islink(target_path) or not os.path.isfile(target_path):
                    raise StorageIntegrityError(
                        "MemoryPack rollback target must be a regular file"
                    )
                os.remove(target_path)
                self._fsync_parent_directory(target_path)

    def _recover_memory_pack_write_transaction_locked(self) -> None:
        """Rolls active work back and only cleans a committed journal."""
        journal_path = self._get_memory_pack_write_journal_path()
        if not os.path.exists(journal_path):
            return
        journal = self._load_memory_pack_write_journal()
        if journal["state"] == "active":
            self._rollback_memory_pack_write_journal(journal)
        self._remove_memory_pack_write_journal()

    def _write_json_atomic(self, file_path: str, data: Any) -> None:
        """Writes JSON atomically after preserving its first before-image."""
        self._record_memory_pack_before_image(file_path)
        self._write_json_atomic_raw(file_path, data)

    @staticmethod
    def _read_json(file_path: str, description: str) -> Any:
        """Reads one required JSON document without treating damage as absence."""
        try:
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return json.load(file_obj)
        except Exception as exc:
            raise StorageIntegrityError(
                f"{description} is unreadable or malformed"
            ) from exc

    @classmethod
    def _read_legacy_nodes(
        cls,
        file_path: str,
        agent_id: str,
        user_id: str,
    ) -> List[MemoryNode]:
        """Validates the complete legacy MemoryNode document."""
        raw_nodes = cls._read_json(file_path, "MemoryNode document")
        if not isinstance(raw_nodes, list):
            raise StorageIntegrityError("MemoryNode document must contain a list")
        try:
            nodes = [MemoryNode.from_dict(item) for item in raw_nodes]
        except Exception as exc:
            raise StorageIntegrityError(
                "MemoryNode document contains an invalid record"
            ) from exc
        if any(
            node.agent_id != agent_id or node.user_id != user_id
            for node in nodes
        ):
            raise StorageIntegrityError(
                "MemoryNode document contains a record from another Agent x User scope"
            )
        return nodes

    @classmethod
    def _read_legacy_core(cls, file_path: str) -> Dict[str, Any]:
        """Validates the complete legacy Core Memory document."""
        data = cls._read_json(file_path, "Core Memory document")
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            raise StorageIntegrityError(
                "Core Memory document must contain string content"
            )
        return data

    @classmethod
    def _read_legacy_timeline(cls, file_path: str) -> List[Dict[str, Any]]:
        """Validates the complete legacy Timeline document."""
        entries = cls._read_json(file_path, "Timeline document")
        if not isinstance(entries, list):
            raise StorageIntegrityError("Timeline document must contain a list")
        if any(
            not isinstance(item, dict)
            or "content" not in item
            or not isinstance(item.get("content"), str)
            or (
                item.get("timestamp") is not None
                and not isinstance(item.get("timestamp"), str)
            )
            for item in entries
        ):
            raise StorageIntegrityError("Timeline document contains an invalid record")
        return entries

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def _recover_persona_approval_transactions(self) -> None:
        """Rolls prepared cross-file approvals forward after an interrupted write."""
        directory = self._get_persona_approval_journal_dir()
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            self._complete_persona_approval_journal(os.path.join(directory, name))

    def _complete_persona_approval_journal(self, journal_path: str) -> None:
        """Idempotently installs both after-images from one durable journal."""
        with open(journal_path, "r", encoding="utf-8") as file_obj:
            transaction = json.load(file_obj)
        if transaction.get("version") != 1:
            raise PersonaCompilationConflictError(
                "unsupported persona approval transaction journal"
            )
        profile = RelationshipProfile.from_dict(transaction["relationship_profile"])
        aggregate = transaction["compilation_aggregate"]
        proposals = [
            PersonaCompilationProposal.from_dict(item)
            for item in aggregate.get("proposals", [])
        ]
        manifests = [
            PersonaManifest.from_dict(item) for item in aggregate.get("manifests", [])
        ]
        if any(item.blueprint_id != profile.blueprint.blueprint_id for item in proposals):
            raise PersonaCompilationConflictError(
                "persona approval journal contains a foreign proposal"
            )
        if any(item.blueprint_id != profile.blueprint.blueprint_id for item in manifests):
            raise PersonaCompilationConflictError(
                "persona approval journal contains a foreign Manifest"
            )
        self._write_json_atomic(
            self._get_persona_compilation_path(profile.blueprint.blueprint_id),
            aggregate,
        )
        self._write_json_atomic(
            self._get_relationship_path(profile.agent_id, profile.user_id),
            profile.to_dict(),
        )
        try:
            os.remove(journal_path)
        except OSError:
            pass

    def _load_identity_registry(self) -> Dict[str, Dict[str, str]]:
        file_path = self._get_identity_registry_path()
        if not os.path.exists(file_path):
            return {
                "agent": {},
                "user": {},
                "relationships": {},
                "personas": {},
                "blueprints": {},
            }
        with open(file_path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return {
            "agent": dict(data.get("agent", {})),
            "user": dict(data.get("user", {})),
            "relationships": dict(data.get("relationships", {})),
            "personas": dict(data.get("personas", {})),
            "blueprints": dict(data.get("blueprints", {})),
        }

    def _load_archival_state(self) -> Dict[str, Any]:
        file_path = self._get_archival_state_path()
        if not os.path.exists(file_path):
            return {
                "version": 1,
                "records": [],
                "artifacts": {},
                "consumer_lease": None,
                "imported_timeline": [],
                "tombstones": [],
            }
        with open(file_path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        if data.get("version") != 1 or not isinstance(data.get("records"), list):
            raise ArchivalConflictError("unsupported or invalid archival state")
        data.setdefault("artifacts", {})
        data.setdefault("consumer_lease", None)
        data.setdefault("imported_timeline", [])
        data.setdefault("tombstones", [])
        if not isinstance(data["artifacts"], dict):
            raise ArchivalConflictError("invalid archival artifact aggregate")
        return data

    @_memory_pack_write_barrier
    def save_nodes(
        self, agent_id: str, user_id: str, nodes: List[MemoryNode]
    ) -> None:
        """Saves memory nodes to nodes.json file."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        if any(
            node.agent_id != clean_agent or node.user_id != clean_user
            for node in nodes
        ):
            raise ValueError("MemoryNode belongs to another Agent x User scope")
        with self.lock_manager.lock(clean_agent, clean_user):
            file_path = self._get_nodes_path(clean_agent, clean_user)
            if os.path.exists(file_path):
                self._read_legacy_nodes(file_path, clean_agent, clean_user)
            data = [node.to_dict() for node in nodes]
            self._write_json_atomic(file_path, data)

    def load_nodes(self, agent_id: str, user_id: str) -> List[MemoryNode]:
        """Loads memory nodes from nodes.json file."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        with self.lock_manager.lock(clean_agent, clean_user):
            file_path = self._get_nodes_path(clean_agent, clean_user)
            nodes_by_id: Dict[str, MemoryNode] = {}
            with self._turn_guard("__archival_global__"):
                state = self._load_archival_state()
                for raw_batch in state["artifacts"].values():
                    batch = PreparedArchivalBatch.from_dict(raw_batch)
                    for node in batch.memories:
                        if (
                            node.agent_id == clean_agent
                            and node.user_id == clean_user
                        ):
                            nodes_by_id[node.node_id] = node
            if os.path.exists(file_path):
                for node in self._read_legacy_nodes(
                    file_path,
                    clean_agent,
                    clean_user,
                ):
                    nodes_by_id[node.node_id] = node
            return list(nodes_by_id.values())

    def get_core_memory(self, agent_id: str, user_id: str) -> str:
        """Loads core persona memory from core_memory.json file."""
        with self.lock_manager.lock(agent_id, user_id):
            file_path = self._get_core_path(agent_id, user_id)
            if not os.path.exists(file_path):
                return ""
            return self._read_legacy_core(file_path)["content"]

    @_memory_pack_write_barrier
    def save_core_memory(self, agent_id: str, user_id: str, content: str) -> None:
        """Saves core persona memory to core_memory.json file."""
        with self.lock_manager.lock(agent_id, user_id):
            file_path = self._get_core_path(agent_id, user_id)
            if os.path.exists(file_path):
                self._read_legacy_core(file_path)
            data = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "content": content,
            }
            self._write_json_atomic(file_path, data)

    @_memory_pack_write_barrier
    def add_timeline_entry(
        self, agent_id: str, user_id: str, entry: str, timestamp: Optional[str] = None
    ) -> None:
        """Appends entry to experiential timeline.json file."""
        with self.lock_manager.lock(agent_id, user_id):
            file_path = self._get_timeline_path(agent_id, user_id)
            entries = []
            if os.path.exists(file_path):
                entries = self._read_legacy_timeline(file_path)

            ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entries.append({"timestamp": ts, "content": entry})

            self._write_json_atomic(file_path, entries)

    def get_recent_timeline(
        self, agent_id: str, user_id: str, limit: int = 5
    ) -> List[str]:
        """Retrieves formatted recent timeline entries."""
        entries = self.list_timeline_entries(agent_id, user_id)
        recent = entries[-limit:] if limit > 0 else []
        return [
            (
                f"[{item.recorded_at or item.legacy_timestamp or 'unknown'}] "
                f"{item.content}"
            )
            for item in recent
        ]

    def list_timeline_entries(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[TimelineEntry]:
        """Projects legacy and modern Timeline data into stable records."""
        relationship = self.get_relationship(agent_id, user_id)
        relationship_id = (
            relationship.relationship_id
            if relationship is not None
            else "legacy_unavailable"
        )
        file_path = self._get_timeline_path(agent_id, user_id)
        by_id: Dict[str, TimelineEntry] = {}
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            for raw in state["imported_timeline"]:
                entry = TimelineEntry.from_dict(raw)
                if entry.agent_id == agent_id and entry.user_id == user_id:
                    by_id[entry.timeline_entry_id] = entry
            for raw_batch in state["artifacts"].values():
                batch = PreparedArchivalBatch.from_dict(raw_batch)
                for entry in batch.timeline:
                    if entry.agent_id == agent_id and entry.user_id == user_id:
                        by_id[entry.timeline_entry_id] = entry
            if os.path.exists(file_path):
                legacy_entries = self._read_legacy_timeline(file_path)
                for index, item in enumerate(legacy_entries):
                    timestamp = item.get("timestamp")
                    content = str(item.get("content", ""))
                    entry_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            (
                                f"erii:legacy-timeline:{agent_id}:{user_id}:"
                                f"{index}:{timestamp}:{content}"
                            ),
                        )
                    )
                    by_id.setdefault(
                        entry_id,
                        TimelineEntry(
                            timeline_entry_id=entry_id,
                            relationship_id=relationship_id,
                            agent_id=agent_id,
                            user_id=user_id,
                            content=content,
                            recorded_at=None,
                            legacy_timestamp=(
                                str(timestamp) if timestamp else None
                            ),
                            provenance_state=(
                                ArtifactProvenanceState.LEGACY_UNAVAILABLE
                            ),
                        ),
                    )
        return sorted(
            by_id.values(),
            key=timeline_entry_order_key,
        )

    def get_recent_timeline_entries(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 5,
    ) -> List[TimelineEntry]:
        """Returns a bounded tail from the file driver's aggregate Timeline."""
        if limit <= 0:
            return []
        return self.list_timeline_entries(agent_id, user_id)[-limit:]

    @_memory_pack_write_barrier
    def import_timeline_entries(
        self,
        agent_id: str,
        user_id: str,
        entries: List[TimelineEntry],
    ) -> None:
        """Idempotently imports structured Timeline records."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            existing = {
                item["timeline_entry_id"]: item
                for item in state["imported_timeline"]
            }
            for entry in entries:
                if entry.agent_id != agent_id or entry.user_id != user_id:
                    raise ArchivalConflictError(
                        "Timeline entry belongs to another Agent x User scope"
                    )
                raw = entry.to_dict()
                current = existing.get(entry.timeline_entry_id)
                if current is not None and current != raw:
                    raise ArchivalConflictError("Timeline entry identity conflict")
                existing[entry.timeline_entry_id] = raw
            state["imported_timeline"] = list(existing.values())
            self._write_json_atomic(self._get_archival_state_path(), state)

    def list_archival_tombstones(
        self,
        relationship_id: str,
    ) -> List[ArchivalTombstone]:
        """Returns imported and locally derived terminal ledger records."""
        by_id: Dict[str, ArchivalTombstone] = {}
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            for raw in state["tombstones"]:
                tombstone = ArchivalTombstone.from_dict(raw)
                if tombstone.relationship_id == relationship_id:
                    by_id[tombstone.archival_id] = tombstone
            for raw in state["records"]:
                record = ArchivalRecord.from_dict(raw)
                if (
                    record.receipt.relationship_id == relationship_id
                    and record.receipt.status
                    in {ArchivalStatus.COMPLETED, ArchivalStatus.FAILED}
                ):
                    tombstone = ArchivalTombstone.from_record(record)
                    existing = by_id.get(tombstone.archival_id)
                    if existing is not None:
                        tombstone = existing.prefer_stronger_commitment(
                            tombstone
                        )
                    by_id[tombstone.archival_id] = tombstone
        return list(by_id.values())

    @_memory_pack_write_barrier
    def import_archival_tombstones(
        self,
        relationship_id: str,
        tombstones: List[ArchivalTombstone],
    ) -> None:
        """Idempotently imports the portable archival ledger."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            merged = merge_archival_tombstone_batch(
                relationship_id,
                tombstones,
                existing=tuple(
                    ArchivalTombstone.from_dict(item)
                    for item in state["tombstones"]
                ),
                live_records=tuple(
                    ArchivalRecord.from_dict(item)
                    for item in state["records"]
                ),
            )
            state["tombstones"] = [item.to_dict() for item in merged]
            self._write_json_atomic(self._get_archival_state_path(), state)

    def validate_archival_tombstones(
        self,
        relationship_id: str,
        tombstones: List[ArchivalTombstone],
    ) -> None:
        """Preflights a portable ledger batch without mutating storage."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            merge_archival_tombstone_batch(
                relationship_id,
                tombstones,
                existing=tuple(
                    ArchivalTombstone.from_dict(item)
                    for item in state["tombstones"]
                ),
                live_records=tuple(
                    ArchivalRecord.from_dict(item)
                    for item in state["records"]
                ),
            )

    def capture_archival_tombstone_validation_source(
        self,
        relationship_id: str,
        archival_ids: List[str],
    ) -> ArchivalTombstoneValidationSource:
        """Captures the relevant archival ledger under one file lock."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            return ArchivalTombstoneValidationSource(
                relationship_id=relationship_id,
                archival_ids=tuple(archival_ids),
                tombstones=tuple(
                    ArchivalTombstone.from_dict(item)
                    for item in state["tombstones"]
                ),
                live_records=tuple(
                    ArchivalRecord.from_dict(item)
                    for item in state["records"]
                ),
            )

    def atomic_archival_store_v1(self):
        """Returns this adapter's atomic archival capability."""
        return self

    @_memory_pack_write_barrier
    def create_archival_record(
        self,
        record: ArchivalRecord,
    ) -> Union[ArchivalRecord, ArchivalTombstone]:
        """Creates an idempotent archival record in the global atomic ledger."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            for raw in state["tombstones"]:
                tombstone = ArchivalTombstone.from_dict(raw)
                if tombstone.archival_id == record.receipt.archival_id:
                    raise ArchivalConflictError("archival_id already exists")
                if tombstone.relationship_id != record.receipt.relationship_id:
                    continue
                if (
                    tombstone.idempotency_fingerprint
                    == record.idempotency_fingerprint
                ):
                    if (
                        tombstone.request_fingerprint
                        != record.request_fingerprint
                    ):
                        raise ArchivalConflictError(
                            "idempotency key is already bound to another archival request"
                        )
                    return tombstone
                if tombstone.request_fingerprint == record.request_fingerprint:
                    return tombstone
            for raw in state["records"]:
                existing = ArchivalRecord.from_dict(raw)
                if (
                    existing.receipt.relationship_id
                    == record.receipt.relationship_id
                    and existing.idempotency_fingerprint
                    == record.idempotency_fingerprint
                ):
                    if existing.request_fingerprint != record.request_fingerprint:
                        raise ArchivalConflictError(
                            "idempotency key is already bound to another archival request"
                        )
                    return existing
                if existing.receipt.archival_id == record.receipt.archival_id:
                    if existing.to_dict() != record.to_dict():
                        raise ArchivalConflictError("archival_id already exists")
                    return existing
            for raw in state["records"]:
                existing = ArchivalRecord.from_dict(raw)
                if (
                    existing.receipt.relationship_id
                    == record.receipt.relationship_id
                    and existing.request_fingerprint == record.request_fingerprint
                ):
                    return existing
            state["records"].append(record.to_dict())
            self._write_json_atomic(self._get_archival_state_path(), state)
            return record

    @_memory_pack_write_barrier
    def compact_archival_records(self, *, before: str) -> int:
        """Replaces expired terminal receipts with minimal durable tombstones."""
        cutoff = datetime.fromisoformat(before.replace("Z", "+00:00"))
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("archival compaction cutoff must include an offset")
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            tombstones = {
                item.archival_id: item
                for item in (
                    ArchivalTombstone.from_dict(raw)
                    for raw in state["tombstones"]
                )
            }
            retained = []
            compacted = 0
            for raw in state["records"]:
                record = ArchivalRecord.from_dict(raw)
                receipt = record.receipt
                if receipt.status not in {
                    ArchivalStatus.COMPLETED,
                    ArchivalStatus.FAILED,
                }:
                    retained.append(raw)
                    continue
                terminal_text = receipt.completed_at or receipt.updated_at
                terminal_at = datetime.fromisoformat(
                    terminal_text.replace("Z", "+00:00")
                )
                if terminal_at.tzinfo is None or terminal_at.utcoffset() is None:
                    retained.append(raw)
                    continue
                if terminal_at > cutoff:
                    retained.append(raw)
                    continue
                tombstone = ArchivalTombstone.from_record(record)
                existing = tombstones.get(tombstone.archival_id)
                if existing is not None:
                    try:
                        tombstone = existing.prefer_stronger_commitment(
                            tombstone
                        )
                    except ArchivalConflictError as exc:
                        raise ArchivalConflictError(
                            "archival tombstone conflicts with terminal receipt"
                        ) from exc
                tombstones[tombstone.archival_id] = tombstone
                compacted += 1
            if compacted:
                state["records"] = retained
                state["tombstones"] = [
                    item.to_dict() for item in tombstones.values()
                ]
                self._write_json_atomic(self._get_archival_state_path(), state)
            return compacted

    def get_archival_record(
        self,
        relationship_id: str,
        archival_id: str,
    ) -> ArchivalRecord:
        """Loads one record without allowing archival IDs to cross scope."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            for raw in state["records"]:
                record = ArchivalRecord.from_dict(raw)
                if (
                    record.receipt.relationship_id == relationship_id
                    and record.receipt.archival_id == archival_id
                ):
                    return record
        raise ArchivalNotFoundError("archival was not found in this relationship")

    def list_archival_records(
        self,
        relationship_id: Optional[str] = None,
    ) -> List[ArchivalRecord]:
        """Lists records in durable insertion order."""
        with self._turn_guard("__archival_global__"):
            records = [
                ArchivalRecord.from_dict(item)
                for item in self._load_archival_state()["records"]
            ]
        if relationship_id is not None:
            records = [
                item
                for item in records
                if item.receipt.relationship_id == relationship_id
            ]
        return records

    @staticmethod
    def _archival_ready(
        record: ArchivalRecord,
        now: float,
    ) -> bool:
        receipt = record.receipt
        if receipt.status == ArchivalStatus.PENDING:
            return True
        if receipt.status == ArchivalStatus.RETRY_WAIT:
            return receipt.next_attempt_at is None or receipt.next_attempt_at <= now
        return (
            receipt.status == ArchivalStatus.PROCESSING
            and record.lease_expires_at is not None
            and record.lease_expires_at <= now
        )

    @_memory_pack_write_barrier
    def claim_next_archival_record(
        self,
        *,
        now: float,
        lease_seconds: float,
        permit_seconds: float,
        archival_id: Optional[str] = None,
    ) -> Optional[ArchivalRecord]:
        """Claims one ready record through a cross-process atomic replacement."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            observed_at = max(now, time.time())
            for index, raw in enumerate(state["records"]):
                existing = ArchivalRecord.from_dict(raw)
                if archival_id is not None and existing.receipt.archival_id != archival_id:
                    continue
                if not self._archival_ready(existing, observed_at):
                    continue
                recovered_expired_lease = (
                    existing.receipt.status == ArchivalStatus.PROCESSING
                )
                phase = existing.receipt.phase
                receipt = replace(
                    existing.receipt,
                    status=ArchivalStatus.PROCESSING,
                    extraction_attempts=(
                        existing.receipt.extraction_attempts + 1
                        if (
                            phase == ArchivalPhase.EXTRACTION
                            and not recovered_expired_lease
                        )
                        else existing.receipt.extraction_attempts
                    ),
                    commit_attempts=(
                        existing.receipt.commit_attempts + 1
                        if (
                            phase == ArchivalPhase.COMMIT
                            and not recovered_expired_lease
                        )
                        else existing.receipt.commit_attempts
                    ),
                    retryable=None,
                    safe_summary=None,
                    next_attempt_at=None,
                    updated_at=datetime.now().astimezone().isoformat(),
                )
                claimed = replace(
                    existing,
                    receipt=receipt,
                    record_version=existing.record_version + 1,
                    lease_token=uuid.uuid4().hex,
                    lease_expires_at=observed_at + lease_seconds,
                    attempt_id=str(uuid.uuid4()),
                    recovered_expired_lease=recovered_expired_lease,
                    commit_permit=(
                        CommitPermit(
                            token=uuid.uuid4().hex,
                            binding_digest=str(existing.commit_binding_digest),
                            expires_at=observed_at + permit_seconds,
                        )
                        if phase == ArchivalPhase.COMMIT
                        else None
                    ),
                )
                state["records"][index] = claimed.to_dict()
                self._write_json_atomic(self._get_archival_state_path(), state)
                return claimed
        return None

    @_memory_pack_write_barrier
    def renew_archival_lease(
        self,
        *,
        relationship_id: str,
        archival_id: str,
        attempt_id: str,
        lease_token: str,
        now: float,
        lease_seconds: float,
    ) -> bool:
        """Renews one current, unexpired processing lease."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            observed_at = max(now, time.time())
            for index, raw in enumerate(state["records"]):
                existing = ArchivalRecord.from_dict(raw)
                if (
                    existing.receipt.relationship_id != relationship_id
                    or existing.receipt.archival_id != archival_id
                ):
                    continue
                if (
                    existing.receipt.status != ArchivalStatus.PROCESSING
                    or existing.attempt_id != attempt_id
                    or existing.lease_token != lease_token
                    or existing.lease_expires_at is None
                    or existing.lease_expires_at <= observed_at
                ):
                    return False
                renewed = replace(
                    existing,
                    lease_expires_at=observed_at + lease_seconds,
                )
                state["records"][index] = renewed.to_dict()
                self._write_json_atomic(self._get_archival_state_path(), state)
                return True
        return False

    @staticmethod
    def _validate_archival_update(
        existing: ArchivalRecord,
        record: ArchivalRecord,
    ) -> None:
        if (
            existing.receipt.archival_id != record.receipt.archival_id
            or existing.receipt.relationship_id != record.receipt.relationship_id
            or existing.idempotency_fingerprint != record.idempotency_fingerprint
            or existing.request_fingerprint != record.request_fingerprint
        ):
            raise ArchivalConflictError("immutable archival identity changed")
        if record.record_version != existing.record_version + 1:
            raise ArchivalConflictError("stale archival record version")
        if (
            existing.receipt.status != ArchivalStatus.PROCESSING
            or not existing.lease_token
            or existing.lease_token != record.lease_token
            or existing.lease_expires_at is None
            or existing.lease_expires_at <= time.time()
        ):
            raise ArchivalConflictError("archival processing lease is no longer valid")

    @staticmethod
    def _validate_bound_archival_commit(
        existing: ArchivalRecord,
        record: ArchivalRecord,
    ) -> None:
        if (
            existing.receipt.archival_id != record.receipt.archival_id
            or existing.receipt.relationship_id != record.receipt.relationship_id
            or existing.idempotency_fingerprint != record.idempotency_fingerprint
            or existing.request_fingerprint != record.request_fingerprint
        ):
            raise ArchivalConflictError("immutable archival identity changed")
        if record.record_version != existing.record_version + 1:
            raise ArchivalConflictError("stale archival record version")
        if (
            existing.receipt.status != ArchivalStatus.PROCESSING
            or existing.receipt.phase != ArchivalPhase.COMMIT
            or not existing.attempt_id
            or existing.attempt_id != record.attempt_id
            or not existing.lease_token
            or existing.lease_token != record.lease_token
        ):
            raise ArchivalConflictError("archival commit authority is no longer valid")

    @_memory_pack_write_barrier
    def bind_prepared_archival_batch(
        self,
        record: ArchivalRecord,
        batch: PreparedArchivalBatch,
    ) -> ArchivalRecord:
        """Freezes one exact batch before allowing its commit."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            for index, raw in enumerate(state["records"]):
                existing = ArchivalRecord.from_dict(raw)
                if existing.receipt.archival_id != record.receipt.archival_id:
                    continue
                self._validate_archival_update(existing, record)
                if record.prepared_batch != batch:
                    raise ArchivalConflictError("prepared batch payload mismatch")
                if (
                    record.commit_permit is None
                    or record.commit_permit.binding_digest != batch.batch_digest
                    or record.commit_permit.expires_at <= time.time()
                ):
                    raise ArchivalConflictError("prepared batch permit is invalid")
                if existing.commit_binding_digest not in (None, batch.batch_digest):
                    raise ArchivalConflictError(
                        "archival is already bound to another batch"
                    )
                state["records"][index] = record.to_dict()
                self._write_json_atomic(self._get_archival_state_path(), state)
                return record
        raise ArchivalNotFoundError("archival was not found")

    @_memory_pack_write_barrier
    def commit_archival_batch(self, record: ArchivalRecord) -> ArchivalRecord:
        """Publishes artifacts and the terminal receipt at one JSON replace."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            for index, raw in enumerate(state["records"]):
                existing = ArchivalRecord.from_dict(raw)
                if existing.receipt.archival_id != record.receipt.archival_id:
                    continue
                self._validate_bound_archival_commit(existing, record)
                if (
                    existing.prepared_batch is None
                    or existing.commit_binding_digest
                    != existing.prepared_batch.batch_digest
                    or existing.commit_permit is None
                    or existing.commit_permit != record.commit_permit
                    or existing.commit_permit.binding_digest
                    != existing.commit_binding_digest
                    or existing.commit_permit.expires_at <= time.time()
                ):
                    raise ArchivalConflictError("archival commit permit is invalid")
                state["artifacts"][
                    existing.receipt.archival_id
                ] = existing.prepared_batch.to_dict()
                stored = replace(
                    record,
                    lease_token=None,
                    lease_expires_at=None,
                    attempt_id=None,
                    commit_permit=None,
                    recovered_expired_lease=False,
                )
                state["records"][index] = stored.to_dict()
                self._write_json_atomic(self._get_archival_state_path(), state)
                return stored
        raise ArchivalNotFoundError("archival was not found")

    @_memory_pack_write_barrier
    def acquire_archival_consumer(
        self,
        consumer_id: str,
        *,
        now: float,
        lease_seconds: float,
    ) -> bool:
        """Enforces one active consumer for this FileStorage ledger."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            observed_at = max(now, time.time())
            lease = state.get("consumer_lease")
            if (
                lease is not None
                and lease.get("consumer_id") != consumer_id
                and float(lease.get("expires_at", 0.0)) > observed_at
            ):
                return False
            state["consumer_lease"] = {
                "consumer_id": consumer_id,
                "expires_at": observed_at + lease_seconds,
            }
            self._write_json_atomic(self._get_archival_state_path(), state)
            return True

    @_memory_pack_write_barrier
    def release_archival_consumer(self, consumer_id: str) -> None:
        """Releases the consumer lease only when still owned by this caller."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            lease = state.get("consumer_lease")
            if lease is not None and lease.get("consumer_id") == consumer_id:
                state["consumer_lease"] = None
                self._write_json_atomic(self._get_archival_state_path(), state)

    @_memory_pack_write_barrier
    def update_archival_record(self, record: ArchivalRecord) -> ArchivalRecord:
        """Persists a lease-fenced retry or terminal failure."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            for index, raw in enumerate(state["records"]):
                existing = ArchivalRecord.from_dict(raw)
                if existing.receipt.archival_id != record.receipt.archival_id:
                    continue
                self._validate_archival_update(existing, record)
                stored = replace(
                    record,
                    lease_token=None,
                    lease_expires_at=None,
                    attempt_id=None,
                    commit_permit=None,
                    recovered_expired_lease=False,
                )
                state["records"][index] = stored.to_dict()
                self._write_json_atomic(self._get_archival_state_path(), state)
                return stored
        raise ArchivalNotFoundError("archival was not found")

    @_memory_pack_write_barrier
    def get_or_create_identity(self, kind: IdentityKind, external_id: str) -> str:
        """Resolves an external key to a stable ID in the file registry."""
        if isinstance(kind, str):
            kind = IdentityKind(kind)
        clean_external = SecuritySanitizer.validate_key(external_id, f"{kind.value}_id")
        with self.lock_manager.lock("__domain_registry__", "identities"):
            registry = self._load_identity_registry()
            existing = registry[kind.value].get(clean_external)
            if existing:
                return existing
            identity_id = str(uuid.uuid4())
            registry[kind.value][clean_external] = identity_id
            self._write_json_atomic(self._get_identity_registry_path(), registry)
            return identity_id

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def create_relationship(self, profile: RelationshipProfile) -> RelationshipProfile:
        """Creates a profile once while preserving imported stable IDs."""
        clean_agent = SecuritySanitizer.validate_key(profile.agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(profile.user_id, "user_id")
        profile_path = self._get_relationship_path(clean_agent, clean_user)

        with self.lock_manager.lock(clean_agent, clean_user):
            if os.path.exists(profile_path):
                with open(profile_path, "r", encoding="utf-8") as file_obj:
                    existing = RelationshipProfile.from_dict(json.load(file_obj))
                if existing.to_dict() != profile.to_dict():
                    raise PersonaConflictError(
                        "relationship initialization conflicts with its immutable profile"
                    )
                return existing

            with self.lock_manager.lock("__domain_registry__", "identities"):
                registry = self._load_identity_registry()
                mappings = (
                    (IdentityKind.AGENT.value, clean_agent, profile.agent_identity_id),
                    (IdentityKind.USER.value, clean_user, profile.user_identity_id),
                )
                for kind, external_id, identity_id in mappings:
                    existing = registry[kind].get(external_id)
                    if existing is not None and existing != identity_id:
                        raise ValueError(
                            f"{kind} identity mapping conflicts with the relationship profile"
                        )
                    for mapped_external, mapped_id in registry[kind].items():
                        if mapped_id == identity_id and mapped_external != external_id:
                            raise ValueError(f"{kind} identity ID is already mapped elsewhere")
                    registry[kind][external_id] = identity_id
                pair_key = f"{clean_agent}\u0000{clean_user}"
                stable_ids = (
                    ("relationships", profile.relationship_id),
                    ("personas", profile.persona_id),
                    ("blueprints", profile.blueprint.blueprint_id),
                )
                for registry_key, stable_id in stable_ids:
                    existing_pair = registry[registry_key].get(stable_id)
                    if existing_pair is not None and existing_pair != pair_key:
                        raise ValueError(f"{registry_key[:-1]} ID is already mapped elsewhere")
                    registry[registry_key][stable_id] = pair_key

                self._write_json_atomic(profile_path, profile.to_dict())
                self._write_json_atomic(self._get_identity_registry_path(), registry)
            return profile

    def get_relationship(
        self, agent_id: str, user_id: str
    ) -> Optional[RelationshipProfile]:
        """Loads an isolated relationship profile from its pair directory."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        with self.lock_manager.lock(clean_agent, clean_user):
            profile_path = self._get_relationship_path(clean_agent, clean_user)
            if not os.path.exists(profile_path):
                return None
            with open(profile_path, "r", encoding="utf-8") as file_obj:
                return RelationshipProfile.from_dict(json.load(file_obj))

    def capture_turn_context_source(
        self,
        profile: RelationshipProfile,
    ) -> TurnContextSourceSnapshot:
        """Reads all Turn-opening authority and history under one root lock."""
        with self._turn_context_snapshot_guard():
            profile_path = self._get_relationship_path(
                profile.agent_id,
                profile.user_id,
            )
            if not os.path.exists(profile_path):
                raise ValueError("relationship profile does not exist")
            with open(profile_path, "r", encoding="utf-8") as file_obj:
                current_profile = RelationshipProfile.from_dict(json.load(file_obj))
            if (
                current_profile.relationship_id != profile.relationship_id
                or current_profile.persona_id != profile.persona_id
                or current_profile.blueprint.blueprint_id
                != profile.blueprint.blueprint_id
            ):
                raise PersonaConflictError(
                    "relationship profile identity changed before Turn Context capture"
                )

            pinned_manifest = None
            backing_proposal = None
            compilation_path = self._get_persona_compilation_path(
                current_profile.blueprint.blueprint_id
            )
            compilation_aggregate: Dict[str, Any] = {
                "proposals": [],
                "manifests": [],
            }
            if os.path.exists(compilation_path):
                with open(compilation_path, "r", encoding="utf-8") as file_obj:
                    compilation_aggregate.update(json.load(file_obj))
            if current_profile.manifest_id is not None:
                pinned_manifest = next(
                    (
                        PersonaManifest.from_dict(item)
                        for item in compilation_aggregate.get("manifests", [])
                        if item.get("manifest_id") == current_profile.manifest_id
                    ),
                    None,
                )
            if pinned_manifest is not None:
                backing_proposal = next(
                    (
                        PersonaCompilationProposal.from_dict(item)
                        for item in compilation_aggregate.get("proposals", [])
                        if item.get("proposal_id")
                        == pinned_manifest.approved_proposal_id
                        and int(item.get("revision", 0))
                        == pinned_manifest.approved_revision
                    ),
                    None,
                )

            growth_path = self._get_persona_growth_path(
                current_profile.relationship_id
            )
            raw_growth: List[Dict[str, Any]] = []
            if os.path.exists(growth_path):
                with open(growth_path, "r", encoding="utf-8") as file_obj:
                    raw_growth = json.load(file_obj)
            approved_growth = tuple(
                proposal
                for proposal in (
                    PersonaGrowthProposal.from_dict(item) for item in raw_growth
                )
                if proposal.status == PersonaGrowthStatus.APPROVED
            )

            event_path = self._get_relationship_events_path(
                current_profile.relationship_id
            )
            direct_events = ()
            if os.path.exists(event_path):
                with open(event_path, "r", encoding="utf-8") as file_obj:
                    direct_events = tuple(
                        RelationshipEvent.from_dict(item) for item in json.load(file_obj)
                    )

            adjudication_path = self._get_relationship_adjudications_path(
                current_profile.relationship_id
            )
            adjudications = ()
            if os.path.exists(adjudication_path):
                with open(adjudication_path, "r", encoding="utf-8") as file_obj:
                    adjudications = tuple(
                        AdjudicationRecord.from_dict(item)
                        for item in json.load(file_obj)
                    )
            return TurnContextSourceSnapshot(
                profile=current_profile,
                pinned_manifest=pinned_manifest,
                backing_compilation_proposal=backing_proposal,
                approved_growth=approved_growth,
                direct_events=direct_events,
                adjudications=adjudications,
            )

    @_memory_pack_write_barrier
    def create_turn_record(self, record: TurnRecord) -> TurnRecord:
        """Creates one exact turn identity without overwriting prior content."""
        with self._turn_guard(record.relationship_id):
            registry = self._load_identity_registry()
            if record.relationship_id not in registry["relationships"]:
                raise ValueError("turn references an unknown relationship")
            file_path = self._get_turn_records_path(record.relationship_id)
            raw_records: List[Dict[str, Any]] = []
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as file_obj:
                    raw_records = json.load(file_obj)
            for raw_record in raw_records:
                if raw_record.get("turn_id") != record.turn_id:
                    continue
                existing = TurnRecord.from_dict(raw_record)
                if (
                    record.status == TurnStatus.OPEN
                    and existing.same_opening_as(record)
                ):
                    return existing
                if (
                    record.status != TurnStatus.OPEN
                    and existing.same_terminal_payload_as(record)
                ):
                    return existing
                raise TurnConflictError(
                    f"turn_id {record.turn_id!r} already has different content"
                )
            raw_records.append(record.to_dict())
            self._write_json_atomic(file_path, raw_records)
            return record

    def get_turn_record(self, relationship_id: str, turn_id: str) -> TurnRecord:
        """Loads one turn from its relationship aggregate."""
        with self._turn_guard(relationship_id):
            file_path = self._get_turn_records_path(relationship_id)
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as file_obj:
                    for raw_record in json.load(file_obj):
                        if raw_record.get("turn_id") == turn_id:
                            return TurnRecord.from_dict(raw_record)
        raise TurnNotFoundError(f"turn {turn_id!r} was not found")

    def get_turn_records(
        self,
        relationship_id: str,
        turn_ids: List[str],
    ) -> List[TurnRecord]:
        """Loads selected turns with one aggregate-file read."""
        wanted = set(turn_ids)
        if not wanted:
            return []
        with self._turn_guard(relationship_id):
            file_path = self._get_turn_records_path(relationship_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return [
                    TurnRecord.from_dict(item)
                    for item in json.load(file_obj)
                    if item.get("turn_id") in wanted
                ]

    def list_turn_records(self, relationship_id: str) -> List[TurnRecord]:
        """Returns source turns in durable append order."""
        with self._turn_guard(relationship_id):
            file_path = self._get_turn_records_path(relationship_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return [TurnRecord.from_dict(item) for item in json.load(file_obj)]

    @_memory_pack_write_barrier
    def transition_turn_record(
        self,
        record: TurnRecord,
        expected_status: TurnStatus,
        expected_record_version: int,
    ) -> TurnRecord:
        """Atomically installs one terminal revision in the turn aggregate."""
        with self._turn_guard(record.relationship_id):
            file_path = self._get_turn_records_path(record.relationship_id)
            if not os.path.exists(file_path):
                raise TurnNotFoundError(f"turn {record.turn_id!r} was not found")
            with open(file_path, "r", encoding="utf-8") as file_obj:
                raw_records = json.load(file_obj)
            for index, raw_record in enumerate(raw_records):
                if raw_record.get("turn_id") != record.turn_id:
                    continue
                existing = TurnRecord.from_dict(raw_record)
                if existing == record:
                    return existing
                if (
                    existing.status != expected_status
                    or existing.record_version != expected_record_version
                    or not record.is_terminal_transition_from(existing)
                ):
                    raise TurnTerminalConflictError(
                        f"turn {record.turn_id!r} transition violates its immutable opening"
                    )
                raw_records[index] = record.to_dict()
                self._write_json_atomic(file_path, raw_records)
                return record
        raise TurnNotFoundError(f"turn {record.turn_id!r} was not found")

    def transition_reviewed_turn_record(
        self,
        profile: RelationshipProfile,
        record: TurnRecord,
        context_baseline: TurnContextBaseline,
        expected_status: TurnStatus,
        expected_record_version: int,
    ) -> TurnRecord:
        """Revalidates context authority and seals the Turn under one root lock."""
        if (
            record.relationship_id != profile.relationship_id
            or record.context_baseline != context_baseline
            or context_baseline.turn_id != record.turn_id
        ):
            raise ValueError("reviewed Turn does not match its context baseline")
        with self._turn_context_snapshot_guard():
            snapshot = self.capture_turn_context_source(profile)
            validate_turn_context_baseline_authority(snapshot, context_baseline)
            return self.transition_turn_record(
                record,
                expected_status,
                expected_record_version,
            )

    @_memory_pack_write_barrier
    def append_reply_attempt(self, attempt: ReplyAttemptRecord) -> ReplyAttemptRecord:
        """Appends safe failure metadata only while its turn remains open."""
        with self._turn_guard(attempt.relationship_id):
            turn_path = self._get_turn_records_path(attempt.relationship_id)
            if not os.path.exists(turn_path):
                raise TurnNotFoundError(f"turn {attempt.turn_id!r} was not found")
            with open(turn_path, "r", encoding="utf-8") as file_obj:
                turns = [TurnRecord.from_dict(item) for item in json.load(file_obj)]
            turn = next(
                (item for item in turns if item.turn_id == attempt.turn_id),
                None,
            )
            if turn is None:
                raise TurnNotFoundError(f"turn {attempt.turn_id!r} was not found")
            if turn.status != TurnStatus.OPEN:
                raise TurnTerminalConflictError(
                    f"turn {attempt.turn_id!r} no longer accepts reply attempts"
                )

            attempts_path = self._get_reply_attempts_path(attempt.relationship_id)
            raw_attempts: List[Dict[str, Any]] = []
            if os.path.exists(attempts_path):
                with open(attempts_path, "r", encoding="utf-8") as file_obj:
                    raw_attempts = json.load(file_obj)
            for raw_attempt in raw_attempts:
                existing = ReplyAttemptRecord.from_dict(raw_attempt)
                if (
                    existing.attempt_id != attempt.attempt_id
                    and (
                        existing.turn_id != attempt.turn_id
                        or existing.attempt_number != attempt.attempt_number
                    )
                ):
                    continue
                if existing.same_payload_as(attempt):
                    return existing
                raise ReplyAttemptConflictError(
                    "reply attempt identity already has different metadata"
                )
            raw_attempts.append(attempt.to_dict())
            self._write_json_atomic(attempts_path, raw_attempts)
            return attempt

    def list_reply_attempts(
        self,
        relationship_id: str,
        turn_id: str,
    ) -> List[ReplyAttemptRecord]:
        """Returns safe attempt records in attempt-number order."""
        with self._turn_guard(relationship_id):
            attempts_path = self._get_reply_attempts_path(relationship_id)
            if not os.path.exists(attempts_path):
                return []
            with open(attempts_path, "r", encoding="utf-8") as file_obj:
                attempts = [
                    ReplyAttemptRecord.from_dict(item)
                    for item in json.load(file_obj)
                    if item.get("turn_id") == turn_id
                ]
            return sorted(attempts, key=lambda item: item.attempt_number)

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def append_relationship_event(self, event: RelationshipEvent) -> RelationshipEvent:
        """Appends an event once, rejecting conflicting reuse of an event ID."""
        with self._relationship_history_guard(event.relationship_id):
            registry = self._load_identity_registry()
            if event.relationship_id not in registry["relationships"]:
                raise ValueError("relationship event references an unknown relationship")

            file_path = self._get_relationship_events_path(event.relationship_id)
            raw_events: List[Dict[str, Any]] = []
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as file_obj:
                    raw_events = json.load(file_obj)

            for raw_event in raw_events:
                if raw_event.get("event_id") != event.event_id:
                    continue
                existing = RelationshipEvent.from_dict(raw_event)
                if not existing.same_payload_as(event):
                    raise EventConflictError(
                        f"event_id {event.event_id!r} already has different content"
                    )
                return existing

            existing_events = [RelationshipEvent.from_dict(item) for item in raw_events]
            adjudication_path = self._get_relationship_adjudications_path(
                event.relationship_id
            )
            if os.path.exists(adjudication_path):
                with open(adjudication_path, "r", encoding="utf-8") as file_obj:
                    existing_events.extend(
                        accepted_event
                        for raw_record in json.load(file_obj)
                        for accepted_event in AdjudicationRecord.from_dict(raw_record).events
                    )
            TemporalHistoryValidator.validate_append(existing_events, event)
            raw_events.append(event.to_dict())
            self._write_json_atomic(file_path, raw_events)
            return event

    def list_relationship_events(self, relationship_id: str) -> List[RelationshipEvent]:
        """Loads events in append order."""
        with self._relationship_history_guard(relationship_id):
            file_path = self._get_relationship_events_path(relationship_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return [RelationshipEvent.from_dict(item) for item in json.load(file_obj)]

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def commit_relationship_adjudication(
        self,
        record: AdjudicationRecord,
    ) -> AdjudicationRecord:
        """Atomically appends one complete adjudication record to its journal."""
        relationship_id = record.receipt.relationship_id
        with self._relationship_history_guard(relationship_id):
            registry = self._load_identity_registry()
            if relationship_id not in registry["relationships"]:
                raise ValueError("adjudication references an unknown relationship")
            file_path = self._get_relationship_adjudications_path(relationship_id)
            raw_records: List[Dict[str, Any]] = []
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as file_obj:
                    raw_records = json.load(file_obj)

            for raw_record in raw_records:
                existing = AdjudicationRecord.from_dict(raw_record)
                same_source_key = (
                    existing.receipt.source_turn_id == record.receipt.source_turn_id
                    and existing.receipt.source_revision == record.receipt.source_revision
                    and existing.receipt.processing_mode == record.receipt.processing_mode
                    and existing.receipt.reprocessing_id == record.receipt.reprocessing_id
                    and existing.receipt.candidate_key == record.receipt.candidate_key
                )
                if existing.receipt.decision_id != record.receipt.decision_id and not same_source_key:
                    continue
                if (
                    existing.receipt.candidate_fingerprint
                    != record.receipt.candidate_fingerprint
                ):
                    raise CandidateConflictError(
                        "candidate decision identity already has different persisted content"
                    )
                return existing

            direct_path = self._get_relationship_events_path(relationship_id)
            existing_events: List[RelationshipEvent] = []
            if os.path.exists(direct_path):
                with open(direct_path, "r", encoding="utf-8") as file_obj:
                    existing_events.extend(
                        RelationshipEvent.from_dict(item) for item in json.load(file_obj)
                    )
            existing_events.extend(
                accepted_event
                for raw_record in raw_records
                for accepted_event in AdjudicationRecord.from_dict(raw_record).events
            )
            for accepted_event in record.events:
                TemporalHistoryValidator.validate_append(existing_events, accepted_event)
                existing_events.append(accepted_event)
            raw_records.append(record.to_dict())
            self._write_json_atomic(file_path, raw_records)
            return record

    def list_relationship_adjudications(
        self,
        relationship_id: str,
    ) -> List[AdjudicationRecord]:
        """Loads candidate decision records in commit order."""
        with self._relationship_history_guard(relationship_id):
            file_path = self._get_relationship_adjudications_path(relationship_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return [AdjudicationRecord.from_dict(item) for item in json.load(file_obj)]

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def append_relationship_consequence(
        self,
        consequence: RelationshipConsequence,
    ) -> RelationshipConsequence:
        """Appends an immutable consequence in relationship history order."""
        with self._relationship_history_guard(consequence.relationship_id):
            registry = self._load_identity_registry()
            if consequence.relationship_id not in registry["relationships"]:
                raise ValueError("consequence references an unknown relationship")

            file_path = self._get_relationship_consequences_path(
                consequence.relationship_id
            )
            raw_consequences: List[Dict[str, Any]] = []
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as file_obj:
                    raw_consequences = json.load(file_obj)

            for raw_consequence in raw_consequences:
                if raw_consequence.get("consequence_id") != consequence.consequence_id:
                    continue
                existing = RelationshipConsequence.from_dict(raw_consequence)
                if existing.same_payload_as(consequence):
                    return existing
                raise ConsequenceConflictError(
                    f"consequence_id {consequence.consequence_id!r} "
                    "already has different content"
                )

            raw_consequences.append(consequence.to_dict())
            self._write_json_atomic(file_path, raw_consequences)
            return consequence

    def list_relationship_consequences(
        self,
        relationship_id: str,
    ) -> List[RelationshipConsequence]:
        """Loads relationship consequences in durable append order."""
        with self._relationship_history_guard(relationship_id):
            file_path = self._get_relationship_consequences_path(relationship_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return [
                    RelationshipConsequence.from_dict(item)
                    for item in json.load(file_obj)
                ]

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def append_narrative_tension_link(
        self,
        link: NarrativeTensionLink,
    ) -> NarrativeTensionLink:
        """Appends one source-bound link to an existing relationship tension."""
        with self._relationship_history_guard(link.relationship_id):
            registry = self._load_identity_registry()
            if link.relationship_id not in registry["relationships"]:
                raise ValueError("Narrative Tension link references an unknown relationship")

            link_path = self._get_narrative_tension_links_path(link.relationship_id)
            raw_links: List[Dict[str, Any]] = []
            if os.path.exists(link_path):
                with open(link_path, "r", encoding="utf-8") as file_obj:
                    raw_links = json.load(file_obj)

            for raw_link in raw_links:
                existing = NarrativeTensionLink.from_dict(raw_link)
                same_source = (
                    existing.tension_id == link.tension_id
                    and existing.source_event_id == link.source_event_id
                )
                if existing.link_id != link.link_id and not same_source:
                    continue
                if existing.same_payload_as(link):
                    return existing
                raise NarrativeTensionConflictError(
                    "Narrative Tension link identity already has different content"
                )

            consequence_path = self._get_relationship_consequences_path(
                link.relationship_id
            )
            raw_consequences: List[Dict[str, Any]] = []
            if os.path.exists(consequence_path):
                with open(consequence_path, "r", encoding="utf-8") as file_obj:
                    raw_consequences = json.load(file_obj)
            consequence_exists = any(
                consequence.relationship_id == link.relationship_id
                and consequence.consequence_id == link.consequence_id
                and consequence.tension_id == link.tension_id
                for consequence in (
                    RelationshipConsequence.from_dict(item)
                    for item in raw_consequences
                )
            )
            if not consequence_exists:
                raise NarrativeTensionConflictError(
                    "Narrative Tension link references an unknown consequence or tension"
                )

            raw_links.append(link.to_dict())
            self._write_json_atomic(link_path, raw_links)
            return link

    def list_narrative_tension_links(
        self,
        relationship_id: str,
    ) -> List[NarrativeTensionLink]:
        """Loads Narrative Tension links in durable append order."""
        with self._relationship_history_guard(relationship_id):
            file_path = self._get_narrative_tension_links_path(relationship_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return [NarrativeTensionLink.from_dict(item) for item in json.load(file_obj)]

    def _load_relationship_processing_state(
        self,
        relationship_id: str,
    ) -> Dict[str, Any]:
        file_path = self._get_relationship_processing_path(relationship_id)
        if not os.path.exists(file_path):
            return {
                "version": 1,
                "runs": [],
                "reflection_decisions": [],
                "reflections": [],
            }
        with open(file_path, "r", encoding="utf-8") as file_obj:
            state = json.load(file_obj)
        state.setdefault("version", 1)
        state.setdefault("runs", [])
        state.setdefault("reflection_decisions", [])
        state.setdefault("reflections", [])
        return state

    def _require_completed_processing_turn(
        self,
        relationship_id: str,
        source_turn_id: str,
        source_revision: str,
    ) -> TurnRecord:
        file_path = self._get_turn_records_path(relationship_id)
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as file_obj:
                for raw_record in json.load(file_obj):
                    if raw_record.get("turn_id") != source_turn_id:
                        continue
                    record = TurnRecord.from_dict(raw_record)
                    if (
                        record.status != TurnStatus.COMPLETED
                        or record.source_revision != source_revision
                    ):
                        break
                    return record
        raise RelationshipProcessingConflictError(
            "relationship processing requires the exact completed Source Turn"
        )

    @_memory_pack_write_barrier
    def create_relationship_processing_run(
        self,
        run: RelationshipProcessingRun,
    ) -> RelationshipProcessingRun:
        """Freezes one run identity exactly once."""
        with self._relationship_history_guard(run.relationship_id):
            registry = self._load_identity_registry()
            if run.relationship_id not in registry["relationships"]:
                raise ValueError("relationship processing references an unknown relationship")
            self._require_completed_processing_turn(
                run.relationship_id,
                run.source_turn_id,
                run.source_revision,
            )
            state = self._load_relationship_processing_state(run.relationship_id)
            for raw_run in state["runs"]:
                existing = RelationshipProcessingRun.from_dict(raw_run)
                same_identity = (
                    existing.relationship_id == run.relationship_id
                    and existing.source_turn_id == run.source_turn_id
                    and existing.source_revision == run.source_revision
                    and existing.processing_identity == run.processing_identity
                )
                if existing.processing_id != run.processing_id and not same_identity:
                    continue
                if existing.same_frozen_input_as(run):
                    return existing
                raise RelationshipProcessingConflictError(
                    "relationship processing identity has different frozen input"
                )
            state["runs"].append(run.to_dict())
            self._write_json_atomic(
                self._get_relationship_processing_path(run.relationship_id),
                state,
            )
            return run

    def get_relationship_processing_run(
        self,
        relationship_id: str,
        processing_id: str,
    ) -> Optional[RelationshipProcessingRun]:
        """Loads one relationship-scoped processing run."""
        with self._relationship_history_guard(relationship_id):
            state = self._load_relationship_processing_state(relationship_id)
            for raw_run in state["runs"]:
                if raw_run.get("processing_id") == processing_id:
                    return RelationshipProcessingRun.from_dict(raw_run)
            return None

    def list_relationship_processing_runs(
        self,
        relationship_id: str,
    ) -> List[RelationshipProcessingRun]:
        """Returns runs in durable creation order."""
        with self._relationship_history_guard(relationship_id):
            state = self._load_relationship_processing_state(relationship_id)
            return [
                RelationshipProcessingRun.from_dict(item)
                for item in state["runs"]
            ]

    @_memory_pack_write_barrier
    def update_relationship_processing_run(
        self,
        run: RelationshipProcessingRun,
        expected_record_version: int,
    ) -> RelationshipProcessingRun:
        """CAS-updates mutable run progress while preserving frozen input."""
        with self._relationship_history_guard(run.relationship_id):
            state = self._load_relationship_processing_state(run.relationship_id)
            for index, raw_run in enumerate(state["runs"]):
                if raw_run.get("processing_id") != run.processing_id:
                    continue
                existing = RelationshipProcessingRun.from_dict(raw_run)
                if existing == run:
                    return existing
                if (
                    existing.record_version != expected_record_version
                    or run.record_version != expected_record_version + 1
                    or not existing.same_frozen_input_as(run)
                ):
                    raise RelationshipProcessingConflictError(
                        "relationship processing run changed concurrently"
                    )
                state["runs"][index] = run.to_dict()
                self._write_json_atomic(
                    self._get_relationship_processing_path(run.relationship_id),
                    state,
                )
                return run
        raise RelationshipProcessingConflictError(
            "relationship processing run does not exist"
        )

    def _relationship_history_for_reflection(
        self,
        relationship_id: str,
    ):
        events: Dict[str, RelationshipEvent] = {}
        event_path = self._get_relationship_events_path(relationship_id)
        if os.path.exists(event_path):
            with open(event_path, "r", encoding="utf-8") as file_obj:
                for item in json.load(file_obj):
                    event = RelationshipEvent.from_dict(item)
                    events[event.event_id] = event
        adjudications: List[AdjudicationRecord] = []
        adjudication_path = self._get_relationship_adjudications_path(
            relationship_id
        )
        if os.path.exists(adjudication_path):
            with open(adjudication_path, "r", encoding="utf-8") as file_obj:
                adjudications = [
                    AdjudicationRecord.from_dict(item)
                    for item in json.load(file_obj)
                ]
            for record in adjudications:
                for event in record.events:
                    events[event.event_id] = event
        return events, adjudications

    def _validate_reflection_decision(
        self,
        decision: PersonaReflectionDecisionRecord,
        existing_reflections: List[PersonaReflectionRecord],
    ) -> None:
        events, adjudications = self._relationship_history_for_reflection(
            decision.relationship_id
        )
        if decision.event_id not in events:
            raise RelationshipProcessingConflictError(
                "reflection references an unknown relationship event"
            )
        provenance = decision.context_provenance
        if provenance.relationship_event_id != decision.event_id:
            raise RelationshipProcessingConflictError(
                "reflection provenance references another event"
            )
        known_reflections = {
            item.reflection_id: item for item in existing_reflections
        }
        if (
            decision.target_reflection_id is not None
            and decision.target_reflection_id not in known_reflections
        ):
            raise RelationshipProcessingConflictError(
                "reflection target does not exist in this relationship"
            )
        missing_prior = set(provenance.prior_reflection_ids).difference(
            known_reflections
        )
        if missing_prior:
            raise RelationshipProcessingConflictError(
                "reflection provenance references unknown prior reflections"
            )
        if provenance.provenance_state != ReflectionProvenanceState.COMPLETE:
            return
        if (
            decision.source_turn_id != provenance.source_turn_id
            or decision.source_revision != provenance.source_revision
        ):
            raise RelationshipProcessingConflictError(
                "reflection decision and provenance source do not match"
            )
        self._require_completed_processing_turn(
            decision.relationship_id,
            decision.source_turn_id,
            decision.source_revision,
        )
        matching = [
            record
            for record in adjudications
            if any(event.event_id == decision.event_id for event in record.events)
        ]
        if len(matching) != 1:
            raise RelationshipProcessingConflictError(
                "complete reflection provenance requires one accepted decision"
            )
        receipt = matching[0].receipt
        evidence_ids = {item.evidence_id for item in receipt.evidence}
        if (
            provenance.decision_id != receipt.decision_id
            or receipt.source_turn_id != decision.source_turn_id
            or receipt.source_revision != decision.source_revision
            or not provenance.evidence_ids
            or not set(provenance.evidence_ids).issubset(evidence_ids)
        ):
            raise RelationshipProcessingConflictError(
                "reflection provenance is not bound to its decision evidence"
            )

    @_memory_pack_write_barrier
    def commit_persona_reflection_decision(
        self,
        decision: PersonaReflectionDecisionRecord,
    ) -> PersonaReflectionDecisionRecord:
        """Atomically appends a decision and its optional formal reflection."""
        with self._relationship_history_guard(decision.relationship_id):
            state = self._load_relationship_processing_state(
                decision.relationship_id
            )
            for raw_decision in state["reflection_decisions"]:
                existing = PersonaReflectionDecisionRecord.from_dict(raw_decision)
                if (
                    existing.decision_id != decision.decision_id
                    and existing.interpretation_identity
                    != decision.interpretation_identity
                ):
                    continue
                if existing.same_payload_as(decision):
                    return existing
                raise RelationshipProcessingConflictError(
                    "reflection interpretation identity has different content"
                )
            reflections = [
                PersonaReflectionRecord.from_dict(item)
                for item in state["reflections"]
            ]
            self._validate_reflection_decision(decision, reflections)
            record = decision.reflection_record
            if record is not None:
                for existing in reflections:
                    if existing.reflection_id != record.reflection_id:
                        continue
                    if existing.same_payload_as(record):
                        break
                    raise RelationshipProcessingConflictError(
                        "reflection ID has different content"
                    )
                else:
                    state["reflections"].append(record.to_dict())
            state["reflection_decisions"].append(decision.to_dict())
            self._write_json_atomic(
                self._get_relationship_processing_path(
                    decision.relationship_id
                ),
                state,
            )
            return decision

    def get_persona_reflection_decision(
        self,
        relationship_id: str,
        decision_id: str,
    ) -> Optional[PersonaReflectionDecisionRecord]:
        with self._relationship_history_guard(relationship_id):
            state = self._load_relationship_processing_state(relationship_id)
            for item in state["reflection_decisions"]:
                if item.get("decision_id") == decision_id:
                    return PersonaReflectionDecisionRecord.from_dict(item)
            return None

    def list_persona_reflection_decisions(
        self,
        relationship_id: str,
    ) -> List[PersonaReflectionDecisionRecord]:
        with self._relationship_history_guard(relationship_id):
            state = self._load_relationship_processing_state(relationship_id)
            return [
                PersonaReflectionDecisionRecord.from_dict(item)
                for item in state["reflection_decisions"]
            ]

    def get_persona_reflection_record(
        self,
        relationship_id: str,
        reflection_id: str,
    ) -> Optional[PersonaReflectionRecord]:
        with self._relationship_history_guard(relationship_id):
            state = self._load_relationship_processing_state(relationship_id)
            for item in state["reflections"]:
                if item.get("reflection_id") == reflection_id:
                    return PersonaReflectionRecord.from_dict(item)
            return None

    def list_persona_reflection_records(
        self,
        relationship_id: str,
    ) -> List[PersonaReflectionRecord]:
        with self._relationship_history_guard(relationship_id):
            state = self._load_relationship_processing_state(relationship_id)
            return [
                PersonaReflectionRecord.from_dict(item)
                for item in state["reflections"]
            ]

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def save_persona_growth_proposal(
        self,
        proposal: PersonaGrowthProposal,
        expected_status: Optional[PersonaGrowthStatus] = None,
    ) -> PersonaGrowthProposal:
        """Creates or conditionally updates one growth proposal atomically."""
        relationship_id = proposal.relationship_id
        with self.lock_manager.lock("__persona_growth__", relationship_id):
            registry = self._load_identity_registry()
            if relationship_id not in registry["relationships"]:
                raise ValueError("persona growth proposal references an unknown relationship")
            file_path = self._get_persona_growth_path(relationship_id)
            raw_proposals: List[Dict[str, Any]] = []
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as file_obj:
                    raw_proposals = json.load(file_obj)

            for index, raw_proposal in enumerate(raw_proposals):
                existing = PersonaGrowthProposal.from_dict(raw_proposal)
                if existing.proposal_id != proposal.proposal_id:
                    continue
                if self._proposal_content(existing) != self._proposal_content(proposal):
                    raise PersonaGrowthConflictError("persona growth proposal content is immutable")
                if expected_status is None:
                    if self._proposal_lifecycle(existing) != self._proposal_lifecycle(
                        proposal
                    ):
                        raise PersonaGrowthConflictError(
                            "updating a proposal requires its expected status"
                        )
                    return existing
                if existing.status != expected_status:
                    raise PersonaGrowthConflictError("persona growth proposal status changed")
                raw_proposals[index] = proposal.to_dict()
                self._write_json_atomic(file_path, raw_proposals)
                return proposal

            if expected_status is not None:
                raise PersonaGrowthConflictError("persona growth proposal no longer exists")
            raw_proposals.append(proposal.to_dict())
            self._write_json_atomic(file_path, raw_proposals)
            return proposal

    def list_persona_growth_proposals(
        self,
        relationship_id: str,
    ) -> List[PersonaGrowthProposal]:
        """Loads persona growth proposals in creation order."""
        with self.lock_manager.lock("__persona_growth__", relationship_id):
            file_path = self._get_persona_growth_path(relationship_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return [PersonaGrowthProposal.from_dict(item) for item in json.load(file_obj)]

    def get_persona_growth_proposal(
        self,
        proposal_id: str,
    ) -> Optional[PersonaGrowthProposal]:
        """Finds one stable proposal identity across relationship files."""
        with self.lock_manager.lock("__domain_registry__", "identities"):
            relationship_ids = tuple(
                self._load_identity_registry()["relationships"]
            )
        for relationship_id in relationship_ids:
            for proposal in self.list_persona_growth_proposals(
                relationship_id
            ):
                if proposal.proposal_id == proposal_id:
                    return proposal
        return None

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def save_persona_compilation_proposal(
        self,
        proposal: PersonaCompilationProposal,
        expected_status: Optional[PersonaCompilationStatus] = None,
    ) -> PersonaCompilationProposal:
        """Appends or conditionally updates one compilation revision atomically."""
        with self.lock_manager.lock("__persona_compilation__", proposal.blueprint_id):
            file_path = self._get_persona_compilation_path(proposal.blueprint_id)
            aggregate: Dict[str, Any] = {"proposals": [], "manifests": []}
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as file_obj:
                    aggregate.update(json.load(file_obj))
            raw_proposals = list(aggregate.get("proposals", []))
            for index, raw_proposal in enumerate(raw_proposals):
                existing = PersonaCompilationProposal.from_dict(raw_proposal)
                if (existing.proposal_id, existing.revision) != (
                    proposal.proposal_id,
                    proposal.revision,
                ):
                    continue
                if self._compilation_content(existing) != self._compilation_content(proposal):
                    raise PersonaCompilationConflictError(
                        "persona compilation revision content is immutable"
                    )
                if expected_status is None:
                    if self._compilation_lifecycle(existing) != self._compilation_lifecycle(
                        proposal
                    ):
                        raise PersonaCompilationConflictError(
                            "updating a compilation decision requires its expected status"
                        )
                    return existing
                if existing.status != expected_status:
                    raise PersonaCompilationConflictError(
                        "persona compilation proposal status changed"
                    )
                raw_proposals[index] = proposal.to_dict()
                aggregate["proposals"] = raw_proposals
                self._write_json_atomic(file_path, aggregate)
                return proposal

            if expected_status is not None:
                raise PersonaCompilationConflictError(
                    "persona compilation proposal revision no longer exists"
                )
            if proposal.revision > 1:
                parent = next(
                    (
                        PersonaCompilationProposal.from_dict(item)
                        for item in raw_proposals
                        if item.get("proposal_id") == proposal.proposal_id
                        and int(item.get("revision", 0)) == proposal.parent_revision
                    ),
                    None,
                )
                if parent is None:
                    raise PersonaCompilationConflictError(
                        "persona compilation parent revision does not exist"
                    )
            raw_proposals.append(proposal.to_dict())
            raw_proposals.sort(key=lambda item: (item["proposal_id"], item["revision"]))
            aggregate["proposals"] = raw_proposals
            self._write_json_atomic(file_path, aggregate)
            return proposal

    def list_persona_compilation_proposals(
        self,
        blueprint_id: str,
    ) -> List[PersonaCompilationProposal]:
        """Loads every proposal revision for one Blueprint."""
        with self.lock_manager.lock("__persona_compilation__", blueprint_id):
            file_path = self._get_persona_compilation_path(blueprint_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                aggregate = json.load(file_obj)
            return [
                PersonaCompilationProposal.from_dict(item)
                for item in aggregate.get("proposals", [])
            ]

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def approve_persona_manifest(
        self,
        proposal: PersonaCompilationProposal,
        manifest: PersonaManifest,
        expected_status: PersonaCompilationStatus = PersonaCompilationStatus.PENDING,
    ) -> PersonaManifest:
        """Atomically approves an exact proposal revision and stores its Manifest."""
        self._validate_manifest_approval(proposal, manifest)
        with self.lock_manager.lock("__persona_compilation__", proposal.blueprint_id):
            file_path = self._get_persona_compilation_path(proposal.blueprint_id)
            if not os.path.exists(file_path):
                raise PersonaCompilationConflictError("persona compilation proposal is missing")
            with open(file_path, "r", encoding="utf-8") as file_obj:
                aggregate = json.load(file_obj)
            raw_proposals = list(aggregate.get("proposals", []))
            raw_manifests = list(aggregate.get("manifests", []))
            for raw_manifest in raw_manifests:
                existing_manifest = PersonaManifest.from_dict(raw_manifest)
                if existing_manifest.manifest_id != manifest.manifest_id:
                    continue
                if existing_manifest.to_dict() != manifest.to_dict():
                    raise PersonaCompilationConflictError("manifest ID has different content")
                return existing_manifest

            matched = False
            for index, raw_proposal in enumerate(raw_proposals):
                existing = PersonaCompilationProposal.from_dict(raw_proposal)
                if (existing.proposal_id, existing.revision) != (
                    proposal.proposal_id,
                    proposal.revision,
                ):
                    continue
                if existing.status != expected_status:
                    raise PersonaCompilationConflictError(
                        "persona compilation proposal status changed"
                    )
                if self._compilation_content(existing) != self._compilation_content(proposal):
                    raise PersonaCompilationConflictError(
                        "approved proposal content differs from persisted revision"
                    )
                raw_proposals[index] = proposal.to_dict()
                matched = True
                break
            if not matched:
                raise PersonaCompilationConflictError("persona compilation revision is missing")
            raw_manifests.append(manifest.to_dict())
            aggregate["proposals"] = raw_proposals
            aggregate["manifests"] = raw_manifests
            self._write_json_atomic(file_path, aggregate)
            return manifest

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def approve_and_bind_persona_manifest(
        self,
        profile: RelationshipProfile,
        proposal: PersonaCompilationProposal,
        manifest: PersonaManifest,
        expected_status: PersonaCompilationStatus = PersonaCompilationStatus.PENDING,
    ) -> PersonaManifest:
        """Crash-recoverably approves and binds one exact Manifest."""
        self._validate_manifest_approval(proposal, manifest)
        journal_path = self._get_persona_approval_journal_path(
            profile.relationship_id,
            proposal.proposal_id,
            proposal.revision,
        )
        with self.lock_manager.lock("__persona_compilation__", proposal.blueprint_id):
            with self.lock_manager.lock(profile.agent_id, profile.user_id):
                if os.path.exists(journal_path):
                    self._complete_persona_approval_journal(journal_path)

                profile_path = self._get_relationship_path(
                    profile.agent_id,
                    profile.user_id,
                )
                if not os.path.exists(profile_path):
                    raise ValueError("relationship profile does not exist")
                with open(profile_path, "r", encoding="utf-8") as file_obj:
                    existing_profile = RelationshipProfile.from_dict(json.load(file_obj))
                if (
                    existing_profile.relationship_id != profile.relationship_id
                    or existing_profile.blueprint.blueprint_id != proposal.blueprint_id
                ):
                    raise PersonaCompilationConflictError(
                        "Manifest approval targets a different relationship or Blueprint"
                    )
                if existing_profile.manifest_id not in (None, manifest.manifest_id):
                    raise PersonaCompilationConflictError(
                        "relationship is already pinned to a different Manifest"
                    )

                compilation_path = self._get_persona_compilation_path(
                    proposal.blueprint_id
                )
                if not os.path.exists(compilation_path):
                    raise PersonaCompilationConflictError(
                        "persona compilation proposal is missing"
                    )
                with open(compilation_path, "r", encoding="utf-8") as file_obj:
                    aggregate = json.load(file_obj)
                raw_proposals = list(aggregate.get("proposals", []))
                raw_manifests = list(aggregate.get("manifests", []))

                proposal_index = None
                persisted_proposal = None
                for index, raw_proposal in enumerate(raw_proposals):
                    candidate = PersonaCompilationProposal.from_dict(raw_proposal)
                    if (candidate.proposal_id, candidate.revision) == (
                        proposal.proposal_id,
                        proposal.revision,
                    ):
                        proposal_index = index
                        persisted_proposal = candidate
                        break
                if persisted_proposal is None or proposal_index is None:
                    raise PersonaCompilationConflictError(
                        "persona compilation revision is missing"
                    )
                if self._compilation_content(persisted_proposal) != self._compilation_content(
                    proposal
                ):
                    raise PersonaCompilationConflictError(
                        "approved proposal content differs from persisted revision"
                    )
                if persisted_proposal.status != expected_status:
                    raise PersonaCompilationConflictError(
                        "persona compilation proposal status changed"
                    )
                if expected_status == PersonaCompilationStatus.APPROVED:
                    if self._compilation_lifecycle(
                        persisted_proposal
                    ) != self._compilation_lifecycle(proposal):
                        raise PersonaCompilationConflictError(
                            "approved proposal lifecycle differs from persisted revision"
                        )
                else:
                    raw_proposals[proposal_index] = proposal.to_dict()

                persisted_manifest = None
                for raw_manifest in raw_manifests:
                    candidate = PersonaManifest.from_dict(raw_manifest)
                    same_revision = (
                        candidate.approved_proposal_id == proposal.proposal_id
                        and candidate.approved_revision == proposal.revision
                    )
                    if candidate.manifest_id == manifest.manifest_id or same_revision:
                        if candidate.to_dict() != manifest.to_dict():
                            raise PersonaCompilationConflictError(
                                "proposal revision already has a different Manifest"
                            )
                        persisted_manifest = candidate
                if persisted_manifest is None:
                    raw_manifests.append(manifest.to_dict())

                aggregate_after = dict(aggregate)
                aggregate_after["proposals"] = raw_proposals
                aggregate_after["manifests"] = raw_manifests
                bound_profile = replace(existing_profile, manifest_id=manifest.manifest_id)
                if (
                    aggregate_after == aggregate
                    and bound_profile.to_dict() == existing_profile.to_dict()
                ):
                    return persisted_manifest or manifest

                transaction = {
                    "version": 1,
                    "relationship_profile": bound_profile.to_dict(),
                    "compilation_aggregate": aggregate_after,
                }
                self._write_json_atomic(journal_path, transaction)
                try:
                    self._complete_persona_approval_journal(journal_path)
                except Exception:
                    # A transient second-file failure can be repaired immediately;
                    # a persistent failure leaves the durable journal for startup.
                    if not os.path.exists(journal_path):
                        raise
                    self._complete_persona_approval_journal(journal_path)
                return persisted_manifest or manifest

    def get_persona_manifest(self, manifest_id: str) -> Optional[PersonaManifest]:
        """Loads a Persona Manifest by scanning compact Blueprint aggregates."""
        directory = os.path.join(self._io_root_dir, "_persona_compilations")
        if not os.path.isdir(directory):
            return None
        with self.lock_manager.lock("__persona_manifest__", manifest_id):
            for name in sorted(os.listdir(directory)):
                if not name.endswith(".json"):
                    continue
                with open(os.path.join(directory, name), "r", encoding="utf-8") as file_obj:
                    aggregate = json.load(file_obj)
                for raw_manifest in aggregate.get("manifests", []):
                    if raw_manifest.get("manifest_id") == manifest_id:
                        return PersonaManifest.from_dict(raw_manifest)
        return None

    def list_persona_manifests(self, blueprint_id: str) -> List[PersonaManifest]:
        """Loads approved Persona Manifests for one Blueprint."""
        with self.lock_manager.lock("__persona_compilation__", blueprint_id):
            file_path = self._get_persona_compilation_path(blueprint_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                aggregate = json.load(file_obj)
            return [
                PersonaManifest.from_dict(item)
                for item in aggregate.get("manifests", [])
            ]

    @_memory_pack_write_barrier
    @_turn_context_snapshot_writer
    def bind_relationship_manifest(
        self,
        profile: RelationshipProfile,
        manifest_id: str,
    ) -> RelationshipProfile:
        """Pins an approved Manifest to one existing relationship exactly once."""
        manifest = self.get_persona_manifest(manifest_id)
        if manifest is None or manifest.blueprint_id != profile.blueprint.blueprint_id:
            raise PersonaCompilationConflictError(
                "manifest is missing or belongs to a different Character Blueprint"
            )
        with self.lock_manager.lock(profile.agent_id, profile.user_id):
            profile_path = self._get_relationship_path(profile.agent_id, profile.user_id)
            if not os.path.exists(profile_path):
                raise ValueError("relationship profile does not exist")
            with open(profile_path, "r", encoding="utf-8") as file_obj:
                existing = RelationshipProfile.from_dict(json.load(file_obj))
            if existing.relationship_id != profile.relationship_id:
                raise PersonaConflictError("relationship profile identity changed")
            if existing.manifest_id is not None:
                if existing.manifest_id != manifest_id:
                    raise PersonaCompilationConflictError(
                        "relationship is already pinned to a different Manifest"
                    )
                return existing
            bound = replace(existing, manifest_id=manifest_id)
            self._write_json_atomic(profile_path, bound.to_dict())
            return bound

    @staticmethod
    def _validate_manifest_approval(
        proposal: PersonaCompilationProposal,
        manifest: PersonaManifest,
    ) -> None:
        if proposal.status != PersonaCompilationStatus.APPROVED:
            raise PersonaCompilationConflictError(
                "approval requires an approved proposal value"
            )
        if (
            manifest.blueprint_id != proposal.blueprint_id
            or manifest.blueprint_revision != proposal.blueprint_revision
            or manifest.source_sha256 != proposal.source_sha256
            or manifest.approved_proposal_id != proposal.proposal_id
            or manifest.approved_revision != proposal.revision
            or manifest.content_fingerprint != proposal.content_fingerprint
            or manifest.candidate.model_dump(mode="json")
            != proposal.candidate.model_dump(mode="json")
            or manifest.approved_by != proposal.decided_by
            or manifest.approved_at != proposal.decided_at
        ):
            raise PersonaCompilationConflictError(
                "manifest does not match the exact approved proposal"
            )

    @staticmethod
    def _compilation_content(proposal: PersonaCompilationProposal) -> Dict[str, Any]:
        data = proposal.to_dict()
        for key in (
            "status",
            "created_at",
            "created_by",
            "decided_by",
            "decided_at",
            "decision_reason",
        ):
            data.pop(key, None)
        return data

    @staticmethod
    def _compilation_lifecycle(proposal: PersonaCompilationProposal):
        return (
            proposal.status,
            proposal.decided_by,
            proposal.decided_at,
            proposal.decision_reason,
        )

    @staticmethod
    def _proposal_content(proposal: PersonaGrowthProposal) -> Dict[str, Any]:
        data = proposal.to_dict()
        for key in (
            "status",
            "created_at",
            "decided_by",
            "decided_at",
            "decision_reason",
        ):
            data.pop(key, None)
        return data

    @staticmethod
    def _proposal_lifecycle(proposal: PersonaGrowthProposal):
        return (
            proposal.status,
            proposal.decided_by,
            proposal.decided_at,
            proposal.decision_reason,
        )
