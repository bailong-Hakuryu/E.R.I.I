"""Base Storage Driver interface for E.R.I.I. Engine.

Follows Google Python Style Guide.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
import threading
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


class BaseStorage(ABC):
    """Abstract interface for memory persistence drivers."""

    def __init__(self) -> None:
        self.lock_manager = KeyLockManager()

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
