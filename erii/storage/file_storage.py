"""JSON File Storage driver for E.R.I.I. Engine.

Provides zero-dependency, file-based persistence for memory nodes,
core persona impressions, and experiential timeline events.

Follows Google Python Style Guide.
"""

from datetime import datetime
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import uuid

from erii.models.node import MemoryNode
from erii.models.relationship import (
    EventConflictError,
    IdentityKind,
    RelationshipEvent,
    RelationshipProfile,
)
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
            if not os.path.exists(file_path):
                return []
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw_list = json.load(f)
                return [MemoryNode.from_dict(item) for item in raw_list]
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
        with self.lock_manager.lock(agent_id, user_id):
            file_path = self._get_timeline_path(agent_id, user_id)
            if not os.path.exists(file_path):
                return []
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                recent = entries[-limit:]
                return [f"[{item['timestamp']}] {item['content']}" for item in recent]
            except Exception as e:
                logger.error("Failed to read timeline for %s/%s: %s", agent_id, user_id, str(e))
                return []

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
                    return RelationshipProfile.from_dict(json.load(file_obj))

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

    def append_relationship_event(self, event: RelationshipEvent) -> RelationshipEvent:
        """Appends an event once, rejecting conflicting reuse of an event ID."""
        with self.lock_manager.lock("__relationship_events__", event.relationship_id):
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
