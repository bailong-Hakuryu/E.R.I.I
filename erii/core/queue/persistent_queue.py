"""Persistent Task Queue driver for E.R.I.I. Engine.

Uses SQLite database to manage persistent archival tasks with exponential backoff.
Follows Google Python Style Guide.
"""

import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from typing import Dict, Optional

from erii.core.queue.base import ArchivalTask, BaseTaskQueue, TaskStatus

logger = logging.getLogger("erii")


class PersistentTaskQueue(BaseTaskQueue):
    """SQLite-backed persistent task queue implementation with retry backoff."""

    def __init__(
        self,
        db_path: str = "./erii_memory.db",
        base_delay_seconds: float = 2.0,
        max_attempts: int = 3,
        processing_timeout_seconds: float = 300.0,
    ) -> None:
        """Initializes PersistentTaskQueue.

        Args:
            db_path: SQLite DB file path.
            base_delay_seconds: Exponential backoff base delay in seconds.
            max_attempts: Maximum retry attempts before marking task as FAILED.
            processing_timeout_seconds: Lease duration before a crashed worker's
                PROCESSING task becomes eligible for recovery.
        """
        self.db_path = db_path
        self.base_delay_seconds = base_delay_seconds
        self.max_attempts = max_attempts
        self.processing_timeout_seconds = processing_timeout_seconds
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        """Initializes queue table in SQLite database."""
        with self._lock:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS archival_tasks (
                        task_id TEXT PRIMARY KEY,
                        agent_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        user_msg TEXT NOT NULL,
                        bot_reply TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL,
                        max_attempts INTEGER NOT NULL,
                        created_at REAL NOT NULL,
                        next_attempt_at REAL NOT NULL,
                        error_msg TEXT,
                        processing_started_at REAL
                    )
                    """
                )
                columns = {
                    row["name"]
                    for row in cursor.execute("PRAGMA table_info(archival_tasks)")
                }
                if "processing_started_at" not in columns:
                    cursor.execute(
                        "ALTER TABLE archival_tasks ADD COLUMN processing_started_at REAL"
                    )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_task_status_time ON archival_tasks(status, next_attempt_at)
                    """
                )
                cutoff = time.time() - self.processing_timeout_seconds
                cursor.execute(
                    """
                    UPDATE archival_tasks
                    SET status = ?, next_attempt_at = ?, processing_started_at = NULL
                    WHERE status = ?
                      AND (processing_started_at IS NULL OR processing_started_at <= ?)
                    """,
                    (
                        TaskStatus.PENDING.value,
                        time.time(),
                        TaskStatus.PROCESSING.value,
                        cutoff,
                    ),
                )
                conn.commit()

    def enqueue(self, agent_id: str, user_id: str, user_msg: str, bot_reply: str) -> str:
        """Enqueues a task and returns task_id."""
        task_id = str(uuid.uuid4())
        now = time.time()
        with self._lock:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO archival_tasks
                    (task_id, agent_id, user_id, user_msg, bot_reply, status, attempts, max_attempts, created_at, next_attempt_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        agent_id,
                        user_id,
                        user_msg,
                        bot_reply,
                        TaskStatus.PENDING.value,
                        0,
                        self.max_attempts,
                        now,
                        now,
                    ),
                )
                conn.commit()
        return task_id

    def dequeue(self) -> Optional[ArchivalTask]:
        """Pulls next pending task whose next_attempt_at <= current time."""
        now = time.time()
        with self._lock:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute(
                    """
                    SELECT * FROM archival_tasks
                    WHERE status = ? AND next_attempt_at <= ?
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    (TaskStatus.PENDING.value, now),
                )
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return None

                task_id = row["task_id"]
                cursor.execute(
                    """
                    UPDATE archival_tasks
                    SET status = ?, processing_started_at = ?
                    WHERE task_id = ? AND status = ?
                    """,
                    (
                        TaskStatus.PROCESSING.value,
                        now,
                        task_id,
                        TaskStatus.PENDING.value,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return None
                conn.commit()

                return ArchivalTask(
                    task_id=row["task_id"],
                    agent_id=row["agent_id"],
                    user_id=row["user_id"],
                    user_msg=row["user_msg"],
                    bot_reply=row["bot_reply"],
                    status=TaskStatus.PROCESSING,
                    attempts=row["attempts"],
                    max_attempts=row["max_attempts"],
                    created_at=row["created_at"],
                    next_attempt_at=row["next_attempt_at"],
                    error_msg=row["error_msg"],
                )

    def complete(self, task_id: str) -> None:
        """Marks task as COMPLETED."""
        with self._lock:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE archival_tasks
                    SET status = ?, processing_started_at = NULL
                    WHERE task_id = ?
                    """,
                    (TaskStatus.COMPLETED.value, task_id),
                )
                conn.commit()

    def fail(self, task_id: str, error_msg: str) -> None:
        """Applies retry backoff or marks task as FAILED."""
        now = time.time()
        with self._lock:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT attempts, max_attempts FROM archival_tasks WHERE task_id = ?",
                    (task_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return

                new_attempts = row["attempts"] + 1
                max_att = row["max_attempts"]

                if new_attempts >= max_att:
                    # Exceeded max attempts, move to dead letter FAILED state
                    cursor.execute(
                        """
                        UPDATE archival_tasks
                        SET status = ?, attempts = ?, error_msg = ?,
                            processing_started_at = NULL
                        WHERE task_id = ?
                        """,
                        (TaskStatus.FAILED.value, new_attempts, error_msg, task_id),
                    )
                else:
                    # Exponential backoff: base_delay * (2 ^ (attempts - 1))
                    delay = self.base_delay_seconds * (2 ** (new_attempts - 1))
                    next_time = now + delay
                    cursor.execute(
                        """
                        UPDATE archival_tasks
                        SET status = ?, attempts = ?, next_attempt_at = ?, error_msg = ?,
                            processing_started_at = NULL
                        WHERE task_id = ?
                        """,
                        (TaskStatus.PENDING.value, new_attempts, next_time, error_msg, task_id),
                    )
                conn.commit()

    def get_status_summary(self) -> Dict[str, int]:
        """Returns task count per status."""
        summary = {
            TaskStatus.PENDING.value: 0,
            TaskStatus.PROCESSING.value: 0,
            TaskStatus.COMPLETED.value: 0,
            TaskStatus.FAILED.value: 0,
        }
        with self._lock:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT status, COUNT(*) as count FROM archival_tasks GROUP BY status"
                )
                for row in cursor.fetchall():
                    summary[row["status"]] = row["count"]
        return summary

    def retry_failed(self) -> int:
        """Resets all FAILED tasks to PENDING with zero attempts."""
        now = time.time()
        with self._lock:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE archival_tasks
                    SET status = ?, attempts = 0, next_attempt_at = ?,
                        processing_started_at = NULL
                    WHERE status = ?
                    """,
                    (TaskStatus.PENDING.value, now, TaskStatus.FAILED.value),
                )
                affected = cursor.rowcount
                conn.commit()
                return affected
