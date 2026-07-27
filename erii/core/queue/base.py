"""Base Task Queue interface for E.R.I.I. Engine.

Follows Google Python Style Guide.
"""

from abc import ABC, abstractmethod
from enum import Enum
import time
from typing import Any, Dict, Optional


class TaskStatus(str, Enum):
    """Status enumeration for archival tasks."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ArchivalTask:
    """Dataclass / Container representing an archival task item."""

    def __init__(
        self,
        task_id: str,
        agent_id: str,
        user_id: str,
        user_msg: str,
        bot_reply: str,
        status: TaskStatus = TaskStatus.PENDING,
        attempts: int = 0,
        max_attempts: int = 3,
        created_at: Optional[float] = None,
        next_attempt_at: Optional[float] = None,
        error_msg: Optional[str] = None,
    ) -> None:
        self.task_id = task_id
        self.agent_id = agent_id
        self.user_id = user_id
        self.user_msg = user_msg
        self.bot_reply = bot_reply
        self.status = status
        self.attempts = attempts
        self.max_attempts = max_attempts
        self.created_at = created_at or time.time()
        self.next_attempt_at = next_attempt_at or self.created_at
        self.error_msg = error_msg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "user_msg": self.user_msg,
            "bot_reply": self.bot_reply,
            "status": self.status.value if isinstance(self.status, TaskStatus) else self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "created_at": self.created_at,
            "next_attempt_at": self.next_attempt_at,
            "error_msg": self.error_msg,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArchivalTask":
        status_val = data.get("status", TaskStatus.PENDING.value)
        status_enum = TaskStatus(status_val) if isinstance(status_val, str) else status_val
        return cls(
            task_id=data["task_id"],
            agent_id=data["agent_id"],
            user_id=data["user_id"],
            user_msg=data["user_msg"],
            bot_reply=data["bot_reply"],
            status=status_enum,
            attempts=data.get("attempts", 0),
            max_attempts=data.get("max_attempts", 3),
            created_at=data.get("created_at"),
            next_attempt_at=data.get("next_attempt_at"),
            error_msg=data.get("error_msg"),
        )


class BaseTaskQueue(ABC):
    """Abstract Task Queue interface."""

    @abstractmethod
    def enqueue(self, agent_id: str, user_id: str, user_msg: str, bot_reply: str) -> str:
        """Enqueues a new turn archival task.

        Returns:
            String task_id.
        """
        pass

    @abstractmethod
    def dequeue(self) -> Optional[ArchivalTask]:
        """Pulls the next ready task from queue.

        Returns:
            ArchivalTask or None if empty.
        """
        pass

    @abstractmethod
    def complete(self, task_id: str) -> None:
        """Marks task as successfully completed."""
        pass

    @abstractmethod
    def fail(self, task_id: str, error_msg: str) -> None:
        """Marks task attempt as failed, applying exponential backoff or dead-letter state."""
        pass

    @abstractmethod
    def get_status_summary(self) -> Dict[str, int]:
        """Returns task counts by status."""
        pass

    @abstractmethod
    def retry_failed(self) -> int:
        """Resets failed tasks back to pending.

        Returns:
            Count of reset tasks.
        """
        pass
