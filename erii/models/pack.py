"""MemoryPack data format model for E.R.I.I. Engine.

Provides portable export/import and versioned schema migration capabilities.
Follows Google Python Style Guide.
"""

from datetime import datetime
import json
from typing import Any, Dict, List, Optional
from erii.models.node import MemoryNode


class MemoryPack:
    """Portable container data structure for agent/user memory export & import."""

    CURRENT_VERSION = "0.2.0"

    def __init__(
        self,
        agent_id: str,
        user_id: str,
        core_memory: str = "",
        nodes: Optional[List[MemoryNode]] = None,
        timeline: Optional[List[Dict[str, str]]] = None,
        version: str = CURRENT_VERSION,
        exported_at: Optional[str] = None,
    ) -> None:
        """Initializes MemoryPack.

        Args:
            agent_id: Agent identifier.
            user_id: User identifier.
            core_memory: Core memory string.
            nodes: List of MemoryNode objects.
            timeline: List of timeline dicts {"timestamp": ..., "content": ...}.
            version: MemoryPack format version string.
            exported_at: ISO timestamp string of export.
        """
        self.agent_id = agent_id
        self.user_id = user_id
        self.core_memory = core_memory
        self.nodes = nodes or []
        self.timeline = timeline or []
        self.version = version
        self.exported_at = exported_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict[str, Any]:
        """Serializes MemoryPack to dictionary."""
        return {
            "metadata": {
                "version": self.version,
                "agent_id": self.agent_id,
                "user_id": self.user_id,
                "exported_at": self.exported_at,
            },
            "core_memory": self.core_memory,
            "nodes": [node.to_dict() for node in self.nodes],
            "timeline": self.timeline,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryPack":
        """Deserializes MemoryPack from dictionary with automatic version migration."""
        meta = data.get("metadata", {})
        version = meta.get("version", "0.1.0")
        agent_id = meta.get("agent_id", "default_agent")
        user_id = meta.get("user_id", "default_user")
        exported_at = meta.get("exported_at")

        core_mem = data.get("core_memory", "")
        raw_nodes = data.get("nodes", [])
        timeline = data.get("timeline", [])

        nodes = [MemoryNode.from_dict(item) for item in raw_nodes]

        return cls(
            agent_id=agent_id,
            user_id=user_id,
            core_memory=core_mem,
            nodes=nodes,
            timeline=timeline,
            version=version,
            exported_at=exported_at,
        )

    def to_json(self) -> str:
        """Serializes MemoryPack to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "MemoryPack":
        """Deserializes MemoryPack from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)
