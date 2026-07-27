"""MemoryPack data format model for E.R.I.I. Engine.

Provides portable export/import and versioned schema migration capabilities.
Follows Google Python Style Guide.
"""

from datetime import datetime
import json
from typing import Any, Dict, List, Optional
from erii.models.adjudication import AdjudicationRecord, PersonaGrowthProposal
from erii.models.node import MemoryNode
from erii.models.persona import PersonaCompilationProposal, PersonaManifest
from erii.models.relationship import RelationshipEvent, RelationshipProfile


class MemoryPack:
    """Portable container data structure for agent/user memory export & import."""

    CURRENT_VERSION = "0.4.0a3"

    def __init__(
        self,
        agent_id: str,
        user_id: str,
        core_memory: str = "",
        nodes: Optional[List[MemoryNode]] = None,
        timeline: Optional[List[Dict[str, str]]] = None,
        version: str = CURRENT_VERSION,
        exported_at: Optional[str] = None,
        relationship: Optional[RelationshipProfile] = None,
        relationship_events: Optional[List[RelationshipEvent]] = None,
        relationship_adjudications: Optional[List[AdjudicationRecord]] = None,
        persona_growth_proposals: Optional[List[PersonaGrowthProposal]] = None,
        persona_compilation_proposals: Optional[List[PersonaCompilationProposal]] = None,
        persona_manifests: Optional[List[PersonaManifest]] = None,
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
            relationship: Immutable relationship/persona profile, when initialized.
            relationship_events: Append-only relationship history.
            relationship_adjudications: Candidate receipts and verified evidence.
            persona_growth_proposals: Pending and decided relationship-persona growth.
            persona_compilation_proposals: Reviewable Persona Compiler revisions.
            persona_manifests: Approved, immutable Persona Interpretation manifests.
        """
        self.agent_id = agent_id
        self.user_id = user_id
        self.core_memory = core_memory
        self.nodes = nodes or []
        self.timeline = timeline or []
        self.relationship = relationship
        self.relationship_events = relationship_events or []
        self.relationship_adjudications = relationship_adjudications or []
        self.persona_growth_proposals = persona_growth_proposals or []
        self.persona_compilation_proposals = persona_compilation_proposals or []
        self.persona_manifests = persona_manifests or []
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
            "relationship": self.relationship.to_dict() if self.relationship else None,
            "relationship_events": [
                event.to_dict() for event in self.relationship_events
            ],
            "relationship_adjudications": [
                record.to_dict() for record in self.relationship_adjudications
            ],
            "persona_growth_proposals": [
                proposal.to_dict() for proposal in self.persona_growth_proposals
            ],
            "persona_compilation_proposals": [
                proposal.to_dict() for proposal in self.persona_compilation_proposals
            ],
            "persona_manifests": [manifest.to_dict() for manifest in self.persona_manifests],
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
        raw_relationship = data.get("relationship")
        raw_relationship_events = data.get("relationship_events", [])
        raw_adjudications = data.get("relationship_adjudications", [])
        raw_growth_proposals = data.get("persona_growth_proposals", [])
        raw_compilation_proposals = data.get("persona_compilation_proposals", [])
        raw_persona_manifests = data.get("persona_manifests", [])

        nodes = [MemoryNode.from_dict(item) for item in raw_nodes]

        return cls(
            agent_id=agent_id,
            user_id=user_id,
            core_memory=core_mem,
            nodes=nodes,
            timeline=timeline,
            relationship=(
                RelationshipProfile.from_dict(raw_relationship)
                if raw_relationship
                else None
            ),
            relationship_events=[
                RelationshipEvent.from_dict(item) for item in raw_relationship_events
            ],
            relationship_adjudications=[
                AdjudicationRecord.from_dict(item) for item in raw_adjudications
            ],
            persona_growth_proposals=[
                PersonaGrowthProposal.from_dict(item) for item in raw_growth_proposals
            ],
            persona_compilation_proposals=[
                PersonaCompilationProposal.from_dict(item)
                for item in raw_compilation_proposals
            ],
            persona_manifests=[
                PersonaManifest.from_dict(item) for item in raw_persona_manifests
            ],
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
