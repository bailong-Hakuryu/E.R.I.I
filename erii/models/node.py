"""Memory Node data structures for E.R.I.I. Engine.

Supports typed memories, multi-factor score calculation, versioning,
recall reinforcement, and lifecycle state management.

Follows Google Python Style Guide.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import math
from typing import Any, Dict, List, Optional


class MemoryType(str, Enum):
    """Supported memory classification types."""
    FACT = "fact"                  # Objective fact about user or environment
    PREFERENCE = "preference"      # User preference or habit
    EVENT = "event"                # Shared experience event
    EMOTION = "emotion"            # Emotional state or milestone
    RELATIONSHIP = "relationship"  # Interpersonal relation dynamic
    CORE = "core"                  # Core persona trait/rule
    INSTRUCTION = "instruction"    # Command directive (must never be persisted)
    THOUGHT = "thought"            # First-person psychological monologue
    DIARY = "diary"                # First-person timestamped diary entry


class MemoryVisibility(str, Enum):
    """Memory visibility scopes for privacy and UI exposure."""
    PUBLIC_LOG = "public_log"                    # Publicly accessible for front-end diary/monologue view
    INTERNAL_MONOLOGUE = "internal_monologue"    # Agent internal thought, restricted from public API


class MemoryState(str, Enum):
    """Memory lifecycle states."""
    ACTIVE = "active"      # Active and included in normal retrieval
    WEAK = "weak"          # Weakened due to low access and decay
    ARCHIVED = "archived"  # Archived, retrieved only on high relevance match


@dataclass
class MemoryNode:
    """Represents a single atomic memory unit with rich metadata."""

    node_id: str
    user_id: str
    content: str
    agent_id: str = "default"
    node_type: MemoryType = MemoryType.FACT
    tags: List[str] = field(default_factory=list)
    base_importance: float = 0.5
    emotional_score: float = 0.0
    confidence: float = 0.8
    access_count: int = 0
    decayable: bool = True
    is_latest: bool = True
    superseded_by: Optional[str] = None
    state: MemoryState = MemoryState.ACTIVE
    timeline_entry: Optional[str] = None
    visibility: str = MemoryVisibility.PUBLIC_LOG.value
    is_unresolved: bool = False
    foreshadowing_tags: List[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    last_accessed_at: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    def calculate_effective_weight(
        self, decay_rate: float = 0.05, max_weight_cap: float = 0.95
    ) -> float:
        """Calculates current dynamic weight applying time decay and emotional boost.

        Args:
            decay_rate: Time decay coefficient lambda.
            max_weight_cap: Upper bound saturation cap to prevent mono-topic dominance.

        Returns:
            Float value representing current dynamic weight between 0.0 and max_weight_cap.
        """
        if not self.is_latest or self.state == MemoryState.ARCHIVED:
            return 0.05

        try:
            last_time = datetime.strptime(self.last_accessed_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            last_time = datetime.now()

        elapsed_seconds = (datetime.now() - last_time).total_seconds()
        elapsed_days = max(0.0, elapsed_seconds / 86400.0)

        # Apply Emotional Resonance Decay Curve. Legacy ``is_unresolved`` is
        # projected as a low-authority recall signal; it no longer grants
        # permanent salience or bypasses ordinary decay.
        effective_decay_rate = decay_rate
        if (
            self.node_type in (MemoryType.THOUGHT, MemoryType.DIARY, MemoryType.EMOTION)
            and abs(self.emotional_score) >= 0.5
        ):
            # Emotional Resonance: slower decay rate for intense emotional memories (both joyful & poignant)
            effective_decay_rate = decay_rate * 0.3
            time_decay = math.exp(-effective_decay_rate * elapsed_days) if self.decayable else 1.0
        else:
            # Exponential decay: e^(-lambda * delta_t) if decayable
            time_decay = math.exp(-effective_decay_rate * elapsed_days) if self.decayable else 1.0

        # Frequency boost (up to +0.2)
        frequency_boost = min(0.2, self.access_count * 0.03)

        # Emotional boost factor
        emotional_boost = abs(self.emotional_score) * 0.15

        # Relationship / Thought boost
        relationship_boost = 0.2 if self.node_type == MemoryType.RELATIONSHIP else 0.0

        raw_score = (
            (self.base_importance * time_decay)
            + frequency_boost
            + emotional_boost
            + relationship_boost
        )

        final_weight = max(0.0, min(max_weight_cap, round(raw_score, 4)))

        # Update lifecycle state based on weight
        if final_weight < 0.15 and self.state == MemoryState.ACTIVE:
            self.state = MemoryState.WEAK

        return final_weight

    def reinforce_recall(self, boost: float = 0.1) -> None:
        """Reinforces memory importance when successfully recalled.

        Args:
            boost: Importance boost increment.
        """
        self.access_count += 1
        self.base_importance = min(0.95, round(self.base_importance + boost, 4))
        self.last_accessed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if self.state == MemoryState.WEAK:
            self.state = MemoryState.ACTIVE

    def to_dict(self) -> Dict[str, Any]:
        """Serializes memory node to dictionary representation."""
        data = asdict(self)
        data["node_type"] = (
            self.node_type.value if isinstance(self.node_type, MemoryType) else self.node_type
        )
        data["state"] = (
            self.state.value if isinstance(self.state, MemoryState) else self.state
        )
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryNode":
        """Instantiates a MemoryNode from dictionary representation.

        Args:
            data: Dictionary containing memory node attributes.

        Returns:
            Instantiated MemoryNode object.
        """
        data_copy = dict(data)
        if "node_type" in data_copy and isinstance(data_copy["node_type"], str):
            try:
                data_copy["node_type"] = MemoryType(data_copy["node_type"])
            except ValueError:
                data_copy["node_type"] = MemoryType.FACT

        if "state" in data_copy and isinstance(data_copy["state"], str):
            try:
                data_copy["state"] = MemoryState(data_copy["state"])
            except ValueError:
                data_copy["state"] = MemoryState.ACTIVE

        return cls(**data_copy)

