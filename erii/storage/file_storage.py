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
from typing import Dict, List, Optional

from erii.models.node import MemoryNode
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
