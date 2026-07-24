"""Embedded SQLite Storage driver for E.R.I.I. Engine.

Provides relational, single-file database storage using Python standard sqlite3.
Follows Google Python Style Guide.
"""

from datetime import datetime
import json
import logging
import sqlite3
from typing import List, Optional

from erii.models.node import MemoryNode
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.base import BaseStorage

logger = logging.getLogger("erii")


class SQLiteStorage(BaseStorage):
    """SQLite-backed memory storage driver."""

    def __init__(self, db_path: str = "./erii_memory.db") -> None:
        """Initializes SQLiteStorage driver and sets up database schema.

        Args:
            db_path: Path to SQLite database file.
        """
        super().__init__()
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        """Initializes SQLite database tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_nodes (
                    node_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    data JSON NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_user ON memory_nodes(agent_id, user_id)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS core_memories (
                    agent_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (agent_id, user_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS timeline_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save_nodes(
        self, agent_id: str, user_id: str, nodes: List[MemoryNode]
    ) -> None:
        """Saves memory nodes into SQLite database."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        with self.lock_manager.lock(clean_agent, clean_user):
            with self._get_connection() as conn:
                cursor = conn.cursor()
                for node in nodes:
                    node_json = json.dumps(node.to_dict(), ensure_ascii=False)
                    cursor.execute(
                        """
                        INSERT INTO memory_nodes (node_id, agent_id, user_id, data)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(node_id) DO UPDATE SET data = excluded.data
                        """,
                        (node.node_id, clean_agent, clean_user, node_json),
                    )
                conn.commit()

    def load_nodes(self, agent_id: str, user_id: str) -> List[MemoryNode]:
        """Loads memory nodes from SQLite database."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        with self.lock_manager.lock(clean_agent, clean_user):
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT data FROM memory_nodes WHERE agent_id = ? AND user_id = ?",
                    (clean_agent, clean_user),
                )
                rows = cursor.fetchall()
                nodes = []
                for row in rows:
                    try:
                        data = json.loads(row["data"])
                        nodes.append(MemoryNode.from_dict(data))
                    except Exception as e:
                        logger.error("Error parsing node DB data: %s", str(e))
                return nodes

    def get_core_memory(self, agent_id: str, user_id: str) -> str:
        """Retrieves core memory string from SQLite."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        with self.lock_manager.lock(clean_agent, clean_user):
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT content FROM core_memories WHERE agent_id = ? AND user_id = ?",
                    (clean_agent, clean_user),
                )
                row = cursor.fetchone()
                return row["content"] if row else ""

    def save_core_memory(self, agent_id: str, user_id: str, content: str) -> None:
        """Saves core memory string into SQLite."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.lock_manager.lock(clean_agent, clean_user):
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO core_memories (agent_id, user_id, content, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(agent_id, user_id) DO UPDATE SET
                        content = excluded.content,
                        updated_at = excluded.updated_at
                    """,
                    (clean_agent, clean_user, content, now),
                )
                conn.commit()

    def add_timeline_entry(
        self, agent_id: str, user_id: str, entry: str, timestamp: Optional[str] = None
    ) -> None:
        """Appends entry to timeline table."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.lock_manager.lock(clean_agent, clean_user):
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO timeline_entries (agent_id, user_id, content, timestamp)
                    VALUES (?, ?, ?, ?)
                    """,
                    (clean_agent, clean_user, entry, ts),
                )
                conn.commit()

    def get_recent_timeline(
        self, agent_id: str, user_id: str, limit: int = 5
    ) -> List[str]:
        """Retrieves recent timeline entries from SQLite."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        with self.lock_manager.lock(clean_agent, clean_user):
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT timestamp, content FROM timeline_entries
                    WHERE agent_id = ? AND user_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (clean_agent, clean_user, limit),
                )
                rows = cursor.fetchall()
                # Reverse order so earliest is first
                rows.reverse()
                return [f"[{row['timestamp']}] {row['content']}" for row in rows]
