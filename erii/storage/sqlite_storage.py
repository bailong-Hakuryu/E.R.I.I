"""Embedded SQLite Storage driver for E.R.I.I. Engine.

Provides relational, single-file database storage using Python standard sqlite3.
Follows Google Python Style Guide.
"""

from datetime import datetime
import json
import logging
import os
import sqlite3
from contextlib import closing
from typing import List, Optional
import uuid

from erii.models.node import MemoryNode
from erii.models.relationship import (
    EventConflictError,
    IdentityKind,
    RelationshipEvent,
    RelationshipProfile,
    utc_now,
)
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
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        """Initializes SQLite database tables."""
        with closing(self._get_connection()) as conn:
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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            cursor.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
            current_version = int(cursor.fetchone()[0])
            if current_version < 1:
                self._migrate_relationship_kernel_v1(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (1, "relationship-kernel-alpha1", utc_now()),
                )
            conn.commit()

    @staticmethod
    def _migrate_relationship_kernel_v1(cursor: sqlite3.Cursor) -> None:
        """Adds stable identities, immutable profiles, and append-only events."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stable_identities (
                identity_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                external_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (kind, external_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS relationships (
                relationship_id TEXT PRIMARY KEY,
                persona_id TEXT NOT NULL UNIQUE,
                blueprint_id TEXT NOT NULL UNIQUE,
                agent_identity_id TEXT NOT NULL,
                user_identity_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                blueprint_data JSON NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (agent_id, user_id),
                FOREIGN KEY (agent_identity_id) REFERENCES stable_identities(identity_id),
                FOREIGN KEY (user_identity_id) REFERENCES stable_identities(identity_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS relationship_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                data JSON NOT NULL,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_relationship_events_order
            ON relationship_events(relationship_id, sequence)
            """
        )

    @property
    def schema_version(self) -> int:
        """Returns the latest applied storage schema migration version."""
        with closing(self._get_connection()) as conn:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
            return int(row[0])

    def save_nodes(
        self, agent_id: str, user_id: str, nodes: List[MemoryNode]
    ) -> None:
        """Saves memory nodes into SQLite database."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        with self.lock_manager.lock(clean_agent, clean_user):
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                keep_ids = set()
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
                    keep_ids.add(node.node_id)

                # Prune nodes that have been removed
                if keep_ids:
                    placeholders = ",".join(["?"] * len(keep_ids))
                    cursor.execute(
                        f"""
                        DELETE FROM memory_nodes
                        WHERE agent_id = ? AND user_id = ? AND node_id NOT IN ({placeholders})
                        """,
                        [clean_agent, clean_user] + list(keep_ids),
                    )
                else:
                    cursor.execute(
                        "DELETE FROM memory_nodes WHERE agent_id = ? AND user_id = ?",
                        (clean_agent, clean_user),
                    )
                conn.commit()

    def load_nodes(self, agent_id: str, user_id: str) -> List[MemoryNode]:
        """Loads memory nodes from SQLite database."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")

        with self.lock_manager.lock(clean_agent, clean_user):
            with closing(self._get_connection()) as conn:
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
            with closing(self._get_connection()) as conn:
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
            with closing(self._get_connection()) as conn:
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
            with closing(self._get_connection()) as conn:
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
            with closing(self._get_connection()) as conn:
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

    def get_or_create_identity(self, kind: IdentityKind, external_id: str) -> str:
        """Atomically resolves an external key to a stable identity ID."""
        if isinstance(kind, str):
            kind = IdentityKind(kind)
        clean_external = SecuritySanitizer.validate_key(external_id, f"{kind.value}_id")
        with self.lock_manager.lock("__domain_registry__", "identities"):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT identity_id FROM stable_identities WHERE kind = ? AND external_id = ?",
                    (kind.value, clean_external),
                ).fetchone()
                if row is not None:
                    conn.commit()
                    return str(row["identity_id"])

                identity_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO stable_identities (identity_id, kind, external_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (identity_id, kind.value, clean_external, utc_now()),
                )
                conn.commit()
                return identity_id

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> RelationshipProfile:
        data = {
            "relationship_id": row["relationship_id"],
            "persona_id": row["persona_id"],
            "agent_identity_id": row["agent_identity_id"],
            "user_identity_id": row["user_identity_id"],
            "agent_id": row["agent_id"],
            "user_id": row["user_id"],
            "blueprint": json.loads(row["blueprint_data"]),
            "created_at": row["created_at"],
        }
        return RelationshipProfile.from_dict(data)

    def create_relationship(self, profile: RelationshipProfile) -> RelationshipProfile:
        """Atomically creates an immutable relationship profile."""
        clean_agent = SecuritySanitizer.validate_key(profile.agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(profile.user_id, "user_id")
        with self.lock_manager.lock(clean_agent, clean_user):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                mappings = (
                    (
                        profile.agent_identity_id,
                        IdentityKind.AGENT.value,
                        clean_agent,
                    ),
                    (
                        profile.user_identity_id,
                        IdentityKind.USER.value,
                        clean_user,
                    ),
                )
                for identity_id, kind, external_id in mappings:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO stable_identities
                            (identity_id, kind, external_id, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (identity_id, kind, external_id, profile.created_at),
                    )
                    mapped = conn.execute(
                        """
                        SELECT identity_id FROM stable_identities
                        WHERE kind = ? AND external_id = ?
                        """,
                        (kind, external_id),
                    ).fetchone()
                    if mapped is None or mapped["identity_id"] != identity_id:
                        conn.rollback()
                        raise ValueError(f"{kind} identity mapping conflicts with profile")

                conn.execute(
                    """
                    INSERT OR IGNORE INTO relationships (
                        relationship_id, persona_id, blueprint_id,
                        agent_identity_id, user_identity_id, agent_id, user_id,
                        blueprint_data, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.relationship_id,
                        profile.persona_id,
                        profile.blueprint.blueprint_id,
                        profile.agent_identity_id,
                        profile.user_identity_id,
                        clean_agent,
                        clean_user,
                        json.dumps(profile.blueprint.to_dict(), ensure_ascii=False),
                        profile.created_at,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM relationships WHERE agent_id = ? AND user_id = ?",
                    (clean_agent, clean_user),
                ).fetchone()
                conn.commit()
                if row is None:
                    raise RuntimeError("relationship profile could not be created")
                return self._profile_from_row(row)

    def get_relationship(
        self, agent_id: str, user_id: str
    ) -> Optional[RelationshipProfile]:
        """Loads the profile mapped to an external Agent x User pair."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        with self.lock_manager.lock(clean_agent, clean_user):
            with closing(self._get_connection()) as conn:
                row = conn.execute(
                    "SELECT * FROM relationships WHERE agent_id = ? AND user_id = ?",
                    (clean_agent, clean_user),
                ).fetchone()
                return self._profile_from_row(row) if row is not None else None

    def append_relationship_event(self, event: RelationshipEvent) -> RelationshipEvent:
        """Appends an event once and rejects conflicting event ID reuse."""
        with self.lock_manager.lock("__relationship_events__", event.relationship_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT data FROM relationship_events WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
                if row is not None:
                    existing = RelationshipEvent.from_dict(json.loads(row["data"]))
                    conn.commit()
                    if not existing.same_payload_as(event):
                        raise EventConflictError(
                            f"event_id {event.event_id!r} already has different content"
                        )
                    return existing

                relationship = conn.execute(
                    "SELECT 1 FROM relationships WHERE relationship_id = ?",
                    (event.relationship_id,),
                ).fetchone()
                if relationship is None:
                    conn.rollback()
                    raise ValueError("relationship event references an unknown relationship")

                conn.execute(
                    """
                    INSERT INTO relationship_events
                        (event_id, relationship_id, data, recorded_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.relationship_id,
                        json.dumps(event.to_dict(), ensure_ascii=False),
                        event.recorded_at,
                    ),
                )
                conn.commit()
                return event

    def list_relationship_events(self, relationship_id: str) -> List[RelationshipEvent]:
        """Loads events for a relationship in append order."""
        with self.lock_manager.lock("__relationship_events__", relationship_id):
            with closing(self._get_connection()) as conn:
                rows = conn.execute(
                    """
                    SELECT data FROM relationship_events
                    WHERE relationship_id = ? ORDER BY sequence ASC
                    """,
                    (relationship_id,),
                ).fetchall()
                return [RelationshipEvent.from_dict(json.loads(row["data"])) for row in rows]
