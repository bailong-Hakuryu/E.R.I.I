"""Base Storage Driver interface for E.R.I.I. Engine.

Follows Google Python Style Guide.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
import errno
import os
import threading
import time
from typing import Dict, List, Optional
from erii.models.adjudication import (
    AdjudicationRecord,
    PersonaGrowthProposal,
    PersonaGrowthStatus,
)
from erii.models.node import MemoryNode
from erii.models.persona import (
    PersonaCompilationProposal,
    PersonaCompilationStatus,
    PersonaManifest,
)
from erii.models.relationship import (
    IdentityKind,
    RelationshipEvent,
    RelationshipProfile,
)
from erii.models.turn import ReplyAttemptRecord, TurnRecord, TurnStatus
from erii.models.archival import ArchivalTombstone, TimelineEntry
from erii.models.consolidation import (
    PersonaReflectionDecisionRecord,
    PersonaReflectionRecord,
    RelationshipProcessingRun,
)
from erii.storage.archival import AtomicArchivalStoreV1


class KeyLockManager:
    """Manages thread-safe locks per (agent_id, user_id) key pair."""

    def __init__(self) -> None:
        self._locks: Dict[str, threading.RLock] = {}
        self._global_lock = threading.Lock()

    def get_lock(self, agent_id: str, user_id: str) -> threading.RLock:
        key = f"{agent_id}:{user_id}"
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = threading.RLock()
            return self._locks[key]

    @contextmanager
    def lock(self, agent_id: str, user_id: str):
        lock = self.get_lock(agent_id, user_id)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


_FILE_LOCKS: Dict[str, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()
_FILE_LOCK_DEPTHS = threading.local()


@contextmanager
def cross_process_file_lock(lock_path: str):
    """Serializes a critical section across threads, instances, and processes."""
    normalized_path = os.path.normcase(
        os.path.realpath(os.path.abspath(lock_path))
    )
    with _FILE_LOCKS_GUARD:
        process_lock = _FILE_LOCKS.setdefault(
            normalized_path,
            threading.RLock(),
        )
    with process_lock:
        depths = getattr(_FILE_LOCK_DEPTHS, "values", None)
        if depths is None:
            depths = {}
            _FILE_LOCK_DEPTHS.values = depths
        if normalized_path in depths:
            depths[normalized_path] += 1
            try:
                yield
            finally:
                depths[normalized_path] -= 1
            return

        parent = os.path.dirname(normalized_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(normalized_path, "a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
                os.fsync(lock_file.fileno())
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                while True:
                    lock_file.seek(0)
                    try:
                        msvcrt.locking(
                            lock_file.fileno(),
                            msvcrt.LK_NBLCK,
                            1,
                        )
                        break
                    except OSError as exc:
                        if (
                            exc.errno
                            not in {
                                errno.EACCES,
                                errno.EAGAIN,
                                errno.EDEADLK,
                            }
                            and getattr(exc, "winerror", None) != 33
                        ):
                            raise
                        # LK_LOCK gives up after ten one-second retries on
                        # Windows. Keep the host-controlled processing guard
                        # valid for model calls that legitimately exceed 10s.
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            depths[normalized_path] = 1
            try:
                yield
            finally:
                try:
                    lock_file.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(
                            lock_file.fileno(),
                            msvcrt.LK_UNLCK,
                            1,
                        )
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                finally:
                    del depths[normalized_path]


class BaseStorage(ABC):
    """Abstract interface for memory persistence drivers."""

    def __init__(self) -> None:
        self.lock_manager = KeyLockManager()

    @contextmanager
    def relationship_processing_guard(self, relationship_id: str):
        """Serializes external relationship-processing calls for one relation.

        Custom adapters that share durable state across processes should
        override this with a cross-process implementation.
        """
        with self.lock_manager.lock(
            "__relationship_processing__",
            relationship_id,
        ):
            yield

    @abstractmethod
    def save_nodes(
        self, agent_id: str, user_id: str, nodes: List[MemoryNode]
    ) -> None:
        """Saves memory nodes to storage.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            nodes: List of MemoryNode objects to save.
        """
        pass

    @abstractmethod
    def load_nodes(self, agent_id: str, user_id: str) -> List[MemoryNode]:
        """Loads memory nodes from storage.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.

        Returns:
            List of MemoryNode objects.
        """
        pass

    @abstractmethod
    def get_core_memory(self, agent_id: str, user_id: str) -> str:
        """Retrieves core memory text for target agent and user.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.

        Returns:
            String content of core memory.
        """
        pass

    @abstractmethod
    def save_core_memory(self, agent_id: str, user_id: str, content: str) -> None:
        """Saves core memory text for target agent and user.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            content: Core memory content string.
        """
        pass

    @abstractmethod
    def add_timeline_entry(
        self, agent_id: str, user_id: str, entry: str, timestamp: Optional[str] = None
    ) -> None:
        """Appends a first-person experiential timeline entry.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            entry: First-person experience timeline text.
            timestamp: Timestamp string (optional).
        """
        pass

    @abstractmethod
    def get_recent_timeline(
        self, agent_id: str, user_id: str, limit: int = 5
    ) -> List[str]:
        """Retrieves recent first-person timeline entries.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            limit: Maximum entries to return.

        Returns:
            List of formatted timeline entry strings.
        """
        pass

    def get_or_create_identity(self, kind: IdentityKind, external_id: str) -> str:
        """Resolves a mutable external key to a stable internal identity ID.

        Relationship-aware custom storage adapters should override this method.
        It remains concrete so pre-v0.4 adapters can still be instantiated for
        legacy memory behavior.
        """
        raise NotImplementedError("storage adapter does not support relationship identities")

    def create_relationship(self, profile: RelationshipProfile) -> RelationshipProfile:
        """Creates an immutable relationship profile or returns the existing one."""
        raise NotImplementedError("storage adapter does not support relationship profiles")

    def get_relationship(
        self, agent_id: str, user_id: str
    ) -> Optional[RelationshipProfile]:
        """Loads the relationship profile mapped to an external Agent x User pair."""
        raise NotImplementedError("storage adapter does not support relationship profiles")

    def append_relationship_event(self, event: RelationshipEvent) -> RelationshipEvent:
        """Appends an immutable event, idempotently keyed by event ID."""
        raise NotImplementedError("storage adapter does not support relationship events")

    def list_relationship_events(self, relationship_id: str) -> List[RelationshipEvent]:
        """Returns accepted events for a relationship in append order."""
        raise NotImplementedError("storage adapter does not support relationship events")

    def commit_relationship_adjudication(
        self,
        record: AdjudicationRecord,
    ) -> AdjudicationRecord:
        """Atomically persists one complete candidate decision record."""
        raise NotImplementedError("storage adapter does not support relationship adjudication")

    def list_relationship_adjudications(
        self,
        relationship_id: str,
    ) -> List[AdjudicationRecord]:
        """Returns candidate decision records in commit order."""
        raise NotImplementedError("storage adapter does not support relationship adjudication")

    def save_persona_growth_proposal(
        self,
        proposal: PersonaGrowthProposal,
        expected_status: Optional[PersonaGrowthStatus] = None,
    ) -> PersonaGrowthProposal:
        """Creates or conditionally updates an immutable-content growth proposal."""
        raise NotImplementedError("storage adapter does not support persona growth proposals")

    def list_persona_growth_proposals(
        self,
        relationship_id: str,
    ) -> List[PersonaGrowthProposal]:
        """Returns persona growth proposals for one relationship."""
        raise NotImplementedError("storage adapter does not support persona growth proposals")

    def get_persona_growth_proposal(
        self,
        proposal_id: str,
    ) -> Optional[PersonaGrowthProposal]:
        """Loads one globally stable persona-growth identity when supported."""
        raise NotImplementedError("storage adapter does not support persona growth proposals")

    def save_persona_compilation_proposal(
        self,
        proposal: PersonaCompilationProposal,
        expected_status: Optional[PersonaCompilationStatus] = None,
    ) -> PersonaCompilationProposal:
        """Appends a proposal revision or conditionally updates its lifecycle."""
        raise NotImplementedError("storage adapter does not support persona compilation")

    def list_persona_compilation_proposals(
        self,
        blueprint_id: str,
    ) -> List[PersonaCompilationProposal]:
        """Returns every immutable compilation revision for one Blueprint."""
        raise NotImplementedError("storage adapter does not support persona compilation")

    def approve_persona_manifest(
        self,
        proposal: PersonaCompilationProposal,
        manifest: PersonaManifest,
        expected_status: PersonaCompilationStatus = PersonaCompilationStatus.PENDING,
    ) -> PersonaManifest:
        """Atomically applies an exact approval and stores its immutable Manifest."""
        raise NotImplementedError("storage adapter does not support persona manifests")

    def approve_and_bind_persona_manifest(
        self,
        profile: RelationshipProfile,
        proposal: PersonaCompilationProposal,
        manifest: PersonaManifest,
        expected_status: PersonaCompilationStatus = PersonaCompilationStatus.PENDING,
    ) -> PersonaManifest:
        """Atomically approves a Manifest and pins it to one relationship.

        Storage adapters implementing Persona Compilation must make this
        operation transactional or crash-recoverable.  The separate approval
        and binding methods remain available for MemoryPack restoration and
        low-level maintenance, but the Engine never composes them itself.
        """
        raise NotImplementedError("storage adapter does not support atomic persona approval")

    def get_persona_manifest(self, manifest_id: str) -> Optional[PersonaManifest]:
        """Loads one approved Persona Manifest by stable ID."""
        raise NotImplementedError("storage adapter does not support persona manifests")

    def list_persona_manifests(self, blueprint_id: str) -> List[PersonaManifest]:
        """Returns approved manifests for one Character Blueprint."""
        raise NotImplementedError("storage adapter does not support persona manifests")

    def bind_relationship_manifest(
        self,
        profile: RelationshipProfile,
        manifest_id: str,
    ) -> RelationshipProfile:
        """Pins the first approved Manifest to a relationship exactly once."""
        raise NotImplementedError("storage adapter does not support persona manifests")

    def create_turn_record(self, record: TurnRecord) -> TurnRecord:
        """Creates one durable turn record idempotently."""
        raise NotImplementedError("storage adapter does not support turn recording")

    def get_turn_record(self, relationship_id: str, turn_id: str) -> TurnRecord:
        """Loads one relationship-scoped turn or raises TurnNotFoundError."""
        raise NotImplementedError("storage adapter does not support turn recording")

    def get_turn_records(
        self,
        relationship_id: str,
        turn_ids: List[str],
    ) -> List[TurnRecord]:
        """Loads a selected Turn batch with a compatibility fallback."""
        wanted = set(turn_ids)
        if not wanted:
            return []
        return [
            item
            for item in self.list_turn_records(relationship_id)
            if item.turn_id in wanted
        ]

    def list_turn_records(self, relationship_id: str) -> List[TurnRecord]:
        """Returns durable turns for one relationship in opening order."""
        raise NotImplementedError("storage adapter does not support turn recording")

    def transition_turn_record(
        self,
        record: TurnRecord,
        expected_status: TurnStatus,
        expected_record_version: int,
    ) -> TurnRecord:
        """Atomically applies one first-writer-wins turn lifecycle transition."""
        raise NotImplementedError("storage adapter does not support turn recording")

    def append_reply_attempt(self, attempt: ReplyAttemptRecord) -> ReplyAttemptRecord:
        """Appends sanitized metadata for one failed reply attempt."""
        raise NotImplementedError("storage adapter does not support reply attempts")

    def list_reply_attempts(
        self,
        relationship_id: str,
        turn_id: str,
    ) -> List[ReplyAttemptRecord]:
        """Returns failed reply attempts in attempt-number order."""
        raise NotImplementedError("storage adapter does not support reply attempts")

    def list_timeline_entries(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[TimelineEntry]:
        """Returns structured Timeline records when supported."""
        raise NotImplementedError("storage adapter does not support structured timeline")

    def get_recent_timeline_entries(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 5,
    ) -> List[TimelineEntry]:
        """Returns a bounded chronological tail of structured Timeline records.

        Existing custom adapters inherit this compatibility implementation.
        Storage drivers with a queryable Timeline should override it so the
        complete history is not materialized.
        """
        if limit <= 0:
            return []
        return self.list_timeline_entries(agent_id, user_id)[-limit:]

    def import_timeline_entries(
        self,
        agent_id: str,
        user_id: str,
        entries: List[TimelineEntry],
    ) -> None:
        """Idempotently imports structured Timeline records."""
        raise NotImplementedError("storage adapter does not support structured timeline")

    def list_archival_tombstones(
        self,
        relationship_id: str,
    ) -> List[ArchivalTombstone]:
        """Returns portable terminal archival identities."""
        raise NotImplementedError("storage adapter does not support archival ledger")

    def import_archival_tombstones(
        self,
        relationship_id: str,
        tombstones: List[ArchivalTombstone],
    ) -> None:
        """Idempotently imports portable terminal archival identities."""
        raise NotImplementedError("storage adapter does not support archival ledger")

    def validate_archival_tombstones(
        self,
        relationship_id: str,
        tombstones: List[ArchivalTombstone],
    ) -> None:
        """Preflights portable archival identities without writing them."""
        raise NotImplementedError("storage adapter does not support archival ledger")

    def atomic_archival_store_v1(self) -> Optional[AtomicArchivalStoreV1]:
        """Returns the optional versioned reliable-archival capability."""
        return None

    def create_relationship_processing_run(
        self,
        run: RelationshipProcessingRun,
    ) -> RelationshipProcessingRun:
        """Freezes one source relationship-processing identity idempotently."""
        raise NotImplementedError(
            "storage adapter does not support relationship processing"
        )

    def get_relationship_processing_run(
        self,
        relationship_id: str,
        processing_id: str,
    ) -> Optional[RelationshipProcessingRun]:
        """Loads one relationship-scoped processing run."""
        raise NotImplementedError(
            "storage adapter does not support relationship processing"
        )

    def list_relationship_processing_runs(
        self,
        relationship_id: str,
    ) -> List[RelationshipProcessingRun]:
        """Returns processing runs in durable creation order."""
        raise NotImplementedError(
            "storage adapter does not support relationship processing"
        )

    def update_relationship_processing_run(
        self,
        run: RelationshipProcessingRun,
        expected_record_version: int,
    ) -> RelationshipProcessingRun:
        """CAS-updates one run without changing its frozen input."""
        raise NotImplementedError(
            "storage adapter does not support relationship processing"
        )

    def commit_persona_reflection_decision(
        self,
        decision: PersonaReflectionDecisionRecord,
    ) -> PersonaReflectionDecisionRecord:
        """Atomically stores a reflection decision and optional formal record."""
        raise NotImplementedError(
            "storage adapter does not support persona reflections"
        )

    def get_persona_reflection_decision(
        self,
        relationship_id: str,
        decision_id: str,
    ) -> Optional[PersonaReflectionDecisionRecord]:
        """Loads one relationship-scoped reflection decision."""
        raise NotImplementedError(
            "storage adapter does not support persona reflections"
        )

    def list_persona_reflection_decisions(
        self,
        relationship_id: str,
    ) -> List[PersonaReflectionDecisionRecord]:
        """Returns reflection and no-reflection outcomes in append order."""
        raise NotImplementedError(
            "storage adapter does not support persona reflections"
        )

    def get_persona_reflection_record(
        self,
        relationship_id: str,
        reflection_id: str,
    ) -> Optional[PersonaReflectionRecord]:
        """Loads one formal reflection record inside its relationship."""
        raise NotImplementedError(
            "storage adapter does not support persona reflections"
        )

    def list_persona_reflection_records(
        self,
        relationship_id: str,
    ) -> List[PersonaReflectionRecord]:
        """Returns append-only formal reflection history."""
        raise NotImplementedError(
            "storage adapter does not support persona reflections"
        )
