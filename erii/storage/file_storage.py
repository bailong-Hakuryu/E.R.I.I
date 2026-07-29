"""JSON File Storage driver for E.R.I.I. Engine.

Provides zero-dependency, file-based persistence for memory nodes,
core persona impressions, and experiential timeline events.

Follows Google Python Style Guide.
"""

from dataclasses import replace
from datetime import datetime
from contextlib import contextmanager
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Union
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
from erii.storage.base import BaseStorage

logger = logging.getLogger("erii")


class FileStorage(BaseStorage):
    """File-based memory storage using JSON files."""

    def __init__(self, root_dir: str = "./erii_memory") -> None:
        """Initializes FileStorage.

        Args:
            root_dir: Root directory path for memory file storage.
        """
        super().__init__()
        self.root_dir = os.path.abspath(root_dir)
        os.makedirs(self.root_dir, exist_ok=True)
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

        path = os.path.join(self.root_dir, safe_agent_dir, safe_user_dir)
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
        return os.path.join(self.root_dir, "_relationship_identities.json")

    def _get_relationship_events_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self.root_dir, "_relationship_events")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_relationship_adjudications_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self.root_dir, "_relationship_adjudications")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_persona_growth_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self.root_dir, "_persona_growth")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_turn_records_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self.root_dir, "_turn_records")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_reply_attempts_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self.root_dir, "_reply_attempts")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_archival_state_path(self) -> str:
        return os.path.join(self.root_dir, "_archival_state.json")

    def _get_turn_lock_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self.root_dir, "_turn_locks")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.lock")

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

    def _get_persona_compilation_path(self, blueprint_id: str) -> str:
        digest = hashlib.sha256(blueprint_id.encode("utf-8")).hexdigest()
        directory = os.path.join(self.root_dir, "_persona_compilations")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.json")

    def _get_persona_approval_journal_dir(self) -> str:
        directory = os.path.join(self.root_dir, "_persona_approval_transactions")
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

    @staticmethod
    def _write_json_atomic(file_path: str, data: Any) -> None:
        """Writes JSON via replace so readers never observe a partial document."""
        temp_path = f"{file_path}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as file_obj:
                json.dump(data, file_obj, ensure_ascii=False, indent=2)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            os.replace(temp_path, file_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

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

    def save_nodes(
        self, agent_id: str, user_id: str, nodes: List[MemoryNode]
    ) -> None:
        """Saves memory nodes to nodes.json file."""
        with self.lock_manager.lock(agent_id, user_id):
            file_path = self._get_nodes_path(agent_id, user_id)
            data = [node.to_dict() for node in nodes]
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error("Failed to save nodes for %s/%s: %s", agent_id, user_id, str(e))

    def load_nodes(self, agent_id: str, user_id: str) -> List[MemoryNode]:
        """Loads memory nodes from nodes.json file."""
        with self.lock_manager.lock(agent_id, user_id):
            file_path = self._get_nodes_path(agent_id, user_id)
            nodes_by_id: Dict[str, MemoryNode] = {}
            try:
                with self._turn_guard("__archival_global__"):
                    state = self._load_archival_state()
                    for raw_batch in state["artifacts"].values():
                        batch = PreparedArchivalBatch.from_dict(raw_batch)
                        for node in batch.memories:
                            if (
                                node.agent_id == agent_id
                                and node.user_id == user_id
                            ):
                                nodes_by_id[node.node_id] = node
                if os.path.exists(file_path):
                    with open(file_path, "r", encoding="utf-8") as f:
                        raw_list = json.load(f)
                    for item in raw_list:
                        node = MemoryNode.from_dict(item)
                        nodes_by_id[node.node_id] = node
                return list(nodes_by_id.values())
            except Exception as e:
                logger.error("Failed to load nodes for %s/%s: %s", agent_id, user_id, str(e))
                return []

    def get_core_memory(self, agent_id: str, user_id: str) -> str:
        """Loads core persona memory from core_memory.json file."""
        with self.lock_manager.lock(agent_id, user_id):
            file_path = self._get_core_path(agent_id, user_id)
            if not os.path.exists(file_path):
                return ""
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("content", "")
            except Exception as e:
                logger.error("Failed to read core memory for %s/%s: %s", agent_id, user_id, str(e))
                return ""

    def save_core_memory(self, agent_id: str, user_id: str, content: str) -> None:
        """Saves core persona memory to core_memory.json file."""
        with self.lock_manager.lock(agent_id, user_id):
            file_path = self._get_core_path(agent_id, user_id)
            data = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "content": content,
            }
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error("Failed to save core memory for %s/%s: %s", agent_id, user_id, str(e))

    def add_timeline_entry(
        self, agent_id: str, user_id: str, entry: str, timestamp: Optional[str] = None
    ) -> None:
        """Appends entry to experiential timeline.json file."""
        with self.lock_manager.lock(agent_id, user_id):
            file_path = self._get_timeline_path(agent_id, user_id)
            entries = []
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        entries = json.load(f)
                except Exception:
                    entries = []

            ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entries.append({"timestamp": ts, "content": entry})

            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(entries, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error("Failed to add timeline entry for %s/%s: %s", agent_id, user_id, str(e))

    def get_recent_timeline(
        self, agent_id: str, user_id: str, limit: int = 5
    ) -> List[str]:
        """Retrieves formatted recent timeline entries."""
        try:
            entries = self.list_timeline_entries(agent_id, user_id)
            recent = entries[-limit:] if limit > 0 else []
            return [
                (
                    f"[{item.recorded_at or item.legacy_timestamp or 'unknown'}] "
                    f"{item.content}"
                )
                for item in recent
            ]
        except Exception as e:
            logger.error(
                "Failed to read timeline for %s/%s: %s",
                agent_id,
                user_id,
                str(e),
            )
            return []

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
                with open(file_path, "r", encoding="utf-8") as file_obj:
                    legacy_entries = json.load(file_obj)
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
            key=lambda item: (
                item.recorded_at or item.legacy_timestamp or "",
                item.timeline_entry_id,
            ),
        )

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
                    by_id[tombstone.archival_id] = tombstone
        return list(by_id.values())

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

    def atomic_archival_store_v1(self):
        """Returns this adapter's atomic archival capability."""
        return self

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
                if existing is not None and existing != tombstone:
                    raise ArchivalConflictError(
                        "archival tombstone conflicts with terminal receipt"
                    )
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
            for index, raw in enumerate(state["records"]):
                existing = ArchivalRecord.from_dict(raw)
                if archival_id is not None and existing.receipt.archival_id != archival_id:
                    continue
                if not self._archival_ready(existing, now):
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
                    lease_expires_at=now + lease_seconds,
                    attempt_id=str(uuid.uuid4()),
                    recovered_expired_lease=recovered_expired_lease,
                    commit_permit=(
                        CommitPermit(
                            token=uuid.uuid4().hex,
                            binding_digest=str(existing.commit_binding_digest),
                            expires_at=now + permit_seconds,
                        )
                        if phase == ArchivalPhase.COMMIT
                        else None
                    ),
                )
                state["records"][index] = claimed.to_dict()
                self._write_json_atomic(self._get_archival_state_path(), state)
                return claimed
        return None

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
                    or existing.lease_expires_at <= now
                ):
                    return False
                renewed = replace(
                    existing,
                    lease_expires_at=now + lease_seconds,
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

    def commit_archival_batch(self, record: ArchivalRecord) -> ArchivalRecord:
        """Publishes artifacts and the terminal receipt at one JSON replace."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            for index, raw in enumerate(state["records"]):
                existing = ArchivalRecord.from_dict(raw)
                if existing.receipt.archival_id != record.receipt.archival_id:
                    continue
                self._validate_archival_update(existing, record)
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
            lease = state.get("consumer_lease")
            if (
                lease is not None
                and lease.get("consumer_id") != consumer_id
                and float(lease.get("expires_at", 0.0)) > now
            ):
                return False
            state["consumer_lease"] = {
                "consumer_id": consumer_id,
                "expires_at": now + lease_seconds,
            }
            self._write_json_atomic(self._get_archival_state_path(), state)
            return True

    def release_archival_consumer(self, consumer_id: str) -> None:
        """Releases the consumer lease only when still owned by this caller."""
        with self._turn_guard("__archival_global__"):
            state = self._load_archival_state()
            lease = state.get("consumer_lease")
            if lease is not None and lease.get("consumer_id") == consumer_id:
                state["consumer_lease"] = None
                self._write_json_atomic(self._get_archival_state_path(), state)

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

    def list_turn_records(self, relationship_id: str) -> List[TurnRecord]:
        """Returns source turns in durable append order."""
        with self._turn_guard(relationship_id):
            file_path = self._get_turn_records_path(relationship_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return [TurnRecord.from_dict(item) for item in json.load(file_obj)]

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

    def append_relationship_event(self, event: RelationshipEvent) -> RelationshipEvent:
        """Appends an event once, rejecting conflicting reuse of an event ID."""
        with self.lock_manager.lock("__relationship_history__", event.relationship_id):
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
        with self.lock_manager.lock("__relationship_events__", relationship_id):
            file_path = self._get_relationship_events_path(relationship_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return [RelationshipEvent.from_dict(item) for item in json.load(file_obj)]

    def commit_relationship_adjudication(
        self,
        record: AdjudicationRecord,
    ) -> AdjudicationRecord:
        """Atomically appends one complete adjudication record to its journal."""
        relationship_id = record.receipt.relationship_id
        with self.lock_manager.lock("__relationship_history__", relationship_id):
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
        with self.lock_manager.lock("__relationship_adjudication__", relationship_id):
            file_path = self._get_relationship_adjudications_path(relationship_id)
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as file_obj:
                return [AdjudicationRecord.from_dict(item) for item in json.load(file_obj)]

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
        directory = os.path.join(self.root_dir, "_persona_compilations")
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
