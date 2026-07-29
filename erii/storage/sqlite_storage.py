"""Embedded SQLite Storage driver for E.R.I.I. Engine.

Provides relational, single-file database storage using Python standard sqlite3.
Follows Google Python Style Guide.
"""

from dataclasses import replace
from datetime import datetime
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from typing import List, Optional, Union
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
    utc_now,
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
                current_version = 1
            if current_version < 2:
                self._migrate_relationship_adjudication_v2(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (2, "relationship-adjudication-alpha2", utc_now()),
                )
                current_version = 2
            if current_version < 3:
                self._migrate_persona_structured_recall_v3(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (3, "persona-structured-recall-alpha3", utc_now()),
                )
                current_version = 3
            if current_version < 4:
                self._migrate_turn_ledger_v4(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (4, "durable-turn-ledger-alpha5", utc_now()),
                )
                current_version = 4
            if current_version < 5:
                self._migrate_reliable_archival_v5(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (5, "reliable-archival-alpha6", utc_now()),
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

    @staticmethod
    def _migrate_relationship_adjudication_v2(cursor: sqlite3.Cursor) -> None:
        """Adds atomic candidate receipts and persona-growth proposals."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS relationship_adjudications (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                source_turn_id TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                processing_identity TEXT NOT NULL,
                candidate_key TEXT NOT NULL,
                data JSON NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (
                    relationship_id, source_turn_id, source_revision,
                    processing_identity, candidate_key
                ),
                FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_relationship_adjudications_order
            ON relationship_adjudications(relationship_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_growth_proposals (
                proposal_id TEXT PRIMARY KEY,
                relationship_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                data JSON NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_persona_growth_relationship
            ON persona_growth_proposals(relationship_id, created_at)
            """
        )

    @staticmethod
    def _migrate_persona_structured_recall_v3(cursor: sqlite3.Cursor) -> None:
        """Adds immutable compilation revisions, manifests, and initial context."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_compilation_revisions (
                proposal_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                blueprint_id TEXT NOT NULL,
                content_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                data JSON NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (proposal_id, revision)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_persona_compilation_blueprint
            ON persona_compilation_revisions(blueprint_id, proposal_id, revision)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_manifests (
                manifest_id TEXT PRIMARY KEY,
                blueprint_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL,
                proposal_revision INTEGER NOT NULL,
                content_fingerprint TEXT NOT NULL,
                data JSON NOT NULL,
                approved_at TEXT NOT NULL,
                UNIQUE (proposal_id, proposal_revision),
                FOREIGN KEY (proposal_id, proposal_revision)
                    REFERENCES persona_compilation_revisions(proposal_id, revision)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_persona_manifest_blueprint
            ON persona_manifests(blueprint_id, approved_at)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS relationship_initial_context (
                relationship_id TEXT PRIMARY KEY,
                data JSON NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id)
            )
            """
        )

    @staticmethod
    def _migrate_turn_ledger_v4(cursor: sqlite3.Cursor) -> None:
        """Adds the canonical relationship-scoped source-turn ledger."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS source_turns (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id TEXT NOT NULL,
                relationship_id TEXT NOT NULL,
                status TEXT NOT NULL,
                data JSON NOT NULL,
                opened_at TEXT NOT NULL,
                UNIQUE (relationship_id, turn_id),
                FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_source_turns_order
            ON source_turns(relationship_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS reply_attempts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                data JSON NOT NULL,
                attempted_at TEXT NOT NULL,
                UNIQUE (relationship_id, turn_id, attempt_number),
                FOREIGN KEY (relationship_id, turn_id)
                    REFERENCES source_turns(relationship_id, turn_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reply_attempts_order
            ON reply_attempts(relationship_id, turn_id, attempt_number)
            """
        )

    @staticmethod
    def _migrate_reliable_archival_v5(cursor: sqlite3.Cursor) -> None:
        """Adds the persistent archival ledger and structured timeline fields."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS archival_records (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                archival_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                idempotency_fingerprint TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                next_attempt_at REAL,
                lease_expires_at REAL,
                data JSON NOT NULL,
                submitted_at TEXT NOT NULL,
                UNIQUE (relationship_id, idempotency_fingerprint),
                UNIQUE (relationship_id, request_fingerprint),
                FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_archival_ready
            ON archival_records(status, next_attempt_at, lease_expires_at, sequence)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS archival_consumer_leases (
                lease_name TEXT PRIMARY KEY,
                consumer_id TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS archival_tombstones (
                archival_id TEXT PRIMARY KEY,
                relationship_id TEXT NOT NULL,
                data JSON NOT NULL,
                terminal_at TEXT NOT NULL,
                FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_archival_tombstone_relationship
            ON archival_tombstones(relationship_id, terminal_at)
            """
        )
        timeline_columns = {
            row[1]
            for row in cursor.execute("PRAGMA table_info(timeline_entries)").fetchall()
        }
        if "timeline_entry_id" not in timeline_columns:
            cursor.execute(
                "ALTER TABLE timeline_entries ADD COLUMN timeline_entry_id TEXT"
            )
        if "source_archival_id" not in timeline_columns:
            cursor.execute(
                "ALTER TABLE timeline_entries ADD COLUMN source_archival_id TEXT"
            )
        if "data" not in timeline_columns:
            cursor.execute("ALTER TABLE timeline_entries ADD COLUMN data JSON")
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_timeline_entry_identity
            ON timeline_entries(timeline_entry_id)
            WHERE timeline_entry_id IS NOT NULL
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
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.cursor()
                existing_rows = cursor.execute(
                    """
                    SELECT node_id, data FROM memory_nodes
                    WHERE agent_id = ? AND user_id = ?
                    """,
                    (clean_agent, clean_user),
                ).fetchall()
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

                removable_ids = []
                for row in existing_rows:
                    if row["node_id"] in keep_ids:
                        continue
                    try:
                        existing_node = MemoryNode.from_dict(
                            json.loads(row["data"])
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        existing_node = None
                    if (
                        existing_node is None
                        or existing_node.source_archival_id is None
                    ):
                        removable_ids.append(str(row["node_id"]))
                if removable_ids:
                    placeholders = ",".join(["?"] * len(removable_ids))
                    cursor.execute(
                        f"""
                        DELETE FROM memory_nodes
                        WHERE agent_id = ? AND user_id = ?
                        AND node_id IN ({placeholders})
                        """,
                        [clean_agent, clean_user] + removable_ids,
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

    def list_timeline_entries(
        self,
        agent_id: str,
        user_id: str,
    ) -> List[TimelineEntry]:
        """Projects every legacy or modern Timeline row without inventing UTC."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        relationship = self.get_relationship(clean_agent, clean_user)
        relationship_id = (
            relationship.relationship_id
            if relationship is not None
            else "legacy_unavailable"
        )
        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT id, content, timestamp, data
                FROM timeline_entries
                WHERE agent_id = ? AND user_id = ?
                ORDER BY id
                """,
                (clean_agent, clean_user),
            ).fetchall()
        result = []
        for row in rows:
            if row["data"]:
                result.append(TimelineEntry.from_dict(json.loads(row["data"])))
                continue
            entry_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"erii:legacy-timeline:{clean_agent}:{clean_user}:"
                        f"{row['id']}"
                    ),
                )
            )
            result.append(
                TimelineEntry(
                    timeline_entry_id=entry_id,
                    relationship_id=relationship_id,
                    agent_id=clean_agent,
                    user_id=clean_user,
                    content=str(row["content"]),
                    recorded_at=None,
                    legacy_timestamp=(
                        str(row["timestamp"]) if row["timestamp"] else None
                    ),
                    provenance_state=(
                        ArtifactProvenanceState.LEGACY_UNAVAILABLE
                    ),
                )
            )
        return result

    def import_timeline_entries(
        self,
        agent_id: str,
        user_id: str,
        entries: List[TimelineEntry],
    ) -> None:
        """Idempotently imports stable structured Timeline identities."""
        clean_agent = SecuritySanitizer.validate_key(agent_id, "agent_id")
        clean_user = SecuritySanitizer.validate_key(user_id, "user_id")
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for entry in entries:
                if entry.agent_id != clean_agent or entry.user_id != clean_user:
                    raise ArchivalConflictError(
                        "Timeline entry belongs to another Agent x User scope"
                    )
                raw = json.dumps(entry.to_dict(), ensure_ascii=False)
                current = conn.execute(
                    """
                    SELECT data FROM timeline_entries
                    WHERE timeline_entry_id = ?
                    """,
                    (entry.timeline_entry_id,),
                ).fetchone()
                if current is not None:
                    if current["data"] != raw:
                        raise ArchivalConflictError(
                            "Timeline entry identity conflict"
                        )
                    continue
                conn.execute(
                    """
                    INSERT INTO timeline_entries (
                        agent_id, user_id, content, timestamp,
                        timeline_entry_id, source_archival_id, data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_agent,
                        clean_user,
                        entry.content,
                        entry.recorded_at
                        or entry.legacy_timestamp
                        or "unknown",
                        entry.timeline_entry_id,
                        entry.source_archival_id,
                        raw,
                    ),
                )
            conn.commit()

    def list_archival_tombstones(
        self,
        relationship_id: str,
    ) -> List[ArchivalTombstone]:
        """Returns imported and locally derived terminal archival identities."""
        by_id = {}
        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT data FROM archival_tombstones
                WHERE relationship_id = ?
                ORDER BY terminal_at
                """,
                (relationship_id,),
            ).fetchall()
            for row in rows:
                tombstone = ArchivalTombstone.from_dict(json.loads(row["data"]))
                by_id[tombstone.archival_id] = tombstone
            rows = conn.execute(
                """
                SELECT data FROM archival_records
                WHERE relationship_id = ? AND status IN (?, ?)
                ORDER BY sequence
                """,
                (
                    relationship_id,
                    ArchivalStatus.COMPLETED.value,
                    ArchivalStatus.FAILED.value,
                ),
            ).fetchall()
            for row in rows:
                tombstone = ArchivalTombstone.from_record(
                    self._archival_record_from_row(row)
                )
                by_id[tombstone.archival_id] = tombstone
        return list(by_id.values())

    def import_archival_tombstones(
        self,
        relationship_id: str,
        tombstones: List[ArchivalTombstone],
    ) -> None:
        """Idempotently imports the portable archival ledger."""
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing_rows = conn.execute(
                "SELECT data FROM archival_tombstones ORDER BY terminal_at"
            ).fetchall()
            live_rows = conn.execute(
                "SELECT data FROM archival_records ORDER BY sequence"
            ).fetchall()
            existing = tuple(
                ArchivalTombstone.from_dict(json.loads(row["data"]))
                for row in existing_rows
            )
            merged = merge_archival_tombstone_batch(
                relationship_id,
                tombstones,
                existing=existing,
                live_records=tuple(
                    self._archival_record_from_row(row)
                    for row in live_rows
                ),
            )
            existing_ids = {item.archival_id for item in existing}
            for tombstone in merged:
                if tombstone.archival_id in existing_ids:
                    continue
                raw = json.dumps(tombstone.to_dict(), ensure_ascii=False)
                conn.execute(
                    """
                    INSERT INTO archival_tombstones (
                        archival_id, relationship_id, data, terminal_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        tombstone.archival_id,
                        relationship_id,
                        raw,
                        tombstone.terminal_at,
                    ),
                )
            conn.commit()

    def validate_archival_tombstones(
        self,
        relationship_id: str,
        tombstones: List[ArchivalTombstone],
    ) -> None:
        """Preflights a portable ledger batch without mutating storage."""
        with closing(self._get_connection()) as conn:
            existing_rows = conn.execute(
                "SELECT data FROM archival_tombstones ORDER BY terminal_at"
            ).fetchall()
            live_rows = conn.execute(
                "SELECT data FROM archival_records ORDER BY sequence"
            ).fetchall()
            merge_archival_tombstone_batch(
                relationship_id,
                tombstones,
                existing=tuple(
                    ArchivalTombstone.from_dict(json.loads(row["data"]))
                    for row in existing_rows
                ),
                live_records=tuple(
                    self._archival_record_from_row(row)
                    for row in live_rows
                ),
            )

    def atomic_archival_store_v1(self):
        """Returns this adapter's atomic archival capability."""
        return self

    @staticmethod
    def _archival_record_from_row(row: sqlite3.Row) -> ArchivalRecord:
        return ArchivalRecord.from_dict(json.loads(row["data"]))

    @staticmethod
    def _write_archival_row(
        cursor: sqlite3.Cursor,
        record: ArchivalRecord,
    ) -> None:
        cursor.execute(
            """
            UPDATE archival_records
            SET status = ?, next_attempt_at = ?, lease_expires_at = ?, data = ?
            WHERE archival_id = ?
            """,
            (
                record.receipt.status.value,
                record.receipt.next_attempt_at,
                record.lease_expires_at,
                json.dumps(record.to_dict(), ensure_ascii=False),
                record.receipt.archival_id,
            ),
        )

    def create_archival_record(
        self,
        record: ArchivalRecord,
    ) -> Union[ArchivalRecord, ArchivalTombstone]:
        """Creates one idempotent submission under a SQLite write lock."""
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data FROM archival_tombstones WHERE archival_id = ?",
                (record.receipt.archival_id,),
            ).fetchone()
            if row is not None:
                raise ArchivalConflictError("archival_id already exists")
            rows = conn.execute(
                """
                SELECT data FROM archival_tombstones
                WHERE relationship_id = ?
                """,
                (record.receipt.relationship_id,),
            ).fetchall()
            for row in rows:
                tombstone = ArchivalTombstone.from_dict(json.loads(row["data"]))
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
                    conn.commit()
                    return tombstone
                if tombstone.request_fingerprint == record.request_fingerprint:
                    conn.commit()
                    return tombstone
            row = conn.execute(
                """
                SELECT data FROM archival_records
                WHERE relationship_id = ? AND idempotency_fingerprint = ?
                """,
                (
                    record.receipt.relationship_id,
                    record.idempotency_fingerprint,
                ),
            ).fetchone()
            if row is not None:
                existing = self._archival_record_from_row(row)
                if existing.request_fingerprint != record.request_fingerprint:
                    raise ArchivalConflictError(
                        "idempotency key is already bound to another archival request"
                    )
                conn.commit()
                return existing
            row = conn.execute(
                """
                SELECT data FROM archival_records
                WHERE relationship_id = ? AND request_fingerprint = ?
                """,
                (
                    record.receipt.relationship_id,
                    record.request_fingerprint,
                ),
            ).fetchone()
            if row is not None:
                conn.commit()
                return self._archival_record_from_row(row)
            row = conn.execute(
                "SELECT data FROM archival_records WHERE archival_id = ?",
                (record.receipt.archival_id,),
            ).fetchone()
            if row is not None:
                existing = self._archival_record_from_row(row)
                if existing.to_dict() != record.to_dict():
                    raise ArchivalConflictError("archival_id already exists")
                conn.commit()
                return existing
            conn.execute(
                """
                INSERT INTO archival_records (
                    archival_id, relationship_id, agent_id, user_id,
                    idempotency_fingerprint, request_fingerprint,
                    status, next_attempt_at,
                    lease_expires_at, data, submitted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.receipt.archival_id,
                    record.receipt.relationship_id,
                    record.receipt.agent_id,
                    record.receipt.user_id,
                    record.idempotency_fingerprint,
                    record.request_fingerprint,
                    record.receipt.status.value,
                    record.receipt.next_attempt_at,
                    record.lease_expires_at,
                    json.dumps(record.to_dict(), ensure_ascii=False),
                    record.receipt.submitted_at,
                ),
            )
            conn.commit()
            return record

    def compact_archival_records(self, *, before: str) -> int:
        """Atomically replaces expired terminal receipts with tombstones."""
        cutoff = datetime.fromisoformat(before.replace("Z", "+00:00"))
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("archival compaction cutoff must include an offset")
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT archival_id, data
                FROM archival_records
                WHERE status IN (?, ?)
                ORDER BY sequence
                """,
                (
                    ArchivalStatus.COMPLETED.value,
                    ArchivalStatus.FAILED.value,
                ),
            ).fetchall()
            compacted = 0
            for row in rows:
                record = self._archival_record_from_row(row)
                terminal_text = (
                    record.receipt.completed_at or record.receipt.updated_at
                )
                terminal_at = datetime.fromisoformat(
                    terminal_text.replace("Z", "+00:00")
                )
                if (
                    terminal_at.tzinfo is None
                    or terminal_at.utcoffset() is None
                    or terminal_at > cutoff
                ):
                    continue
                tombstone = ArchivalTombstone.from_record(record)
                existing_row = conn.execute(
                    """
                    SELECT data FROM archival_tombstones
                    WHERE archival_id = ?
                    """,
                    (tombstone.archival_id,),
                ).fetchone()
                if existing_row is not None:
                    existing = ArchivalTombstone.from_dict(
                        json.loads(existing_row["data"])
                    )
                    if existing != tombstone:
                        raise ArchivalConflictError(
                            "archival tombstone conflicts with terminal receipt"
                        )
                else:
                    conn.execute(
                        """
                        INSERT INTO archival_tombstones (
                            archival_id, relationship_id, data, terminal_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            tombstone.archival_id,
                            tombstone.relationship_id,
                            json.dumps(
                                tombstone.to_dict(),
                                ensure_ascii=False,
                            ),
                            tombstone.terminal_at,
                        ),
                    )
                conn.execute(
                    "DELETE FROM archival_records WHERE archival_id = ?",
                    (tombstone.archival_id,),
                )
                compacted += 1
            conn.commit()
            return compacted

    def get_archival_record(
        self,
        relationship_id: str,
        archival_id: str,
    ) -> ArchivalRecord:
        """Loads one record only inside the supplied relationship scope."""
        with closing(self._get_connection()) as conn:
            row = conn.execute(
                """
                SELECT data FROM archival_records
                WHERE relationship_id = ? AND archival_id = ?
                """,
                (relationship_id, archival_id),
            ).fetchone()
        if row is None:
            raise ArchivalNotFoundError("archival was not found in this relationship")
        return self._archival_record_from_row(row)

    def list_archival_records(
        self,
        relationship_id: Optional[str] = None,
    ) -> List[ArchivalRecord]:
        """Lists archival records in durable submission order."""
        with closing(self._get_connection()) as conn:
            if relationship_id is None:
                rows = conn.execute(
                    "SELECT data FROM archival_records ORDER BY sequence"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT data FROM archival_records
                    WHERE relationship_id = ? ORDER BY sequence
                    """,
                    (relationship_id,),
                ).fetchall()
        return [self._archival_record_from_row(row) for row in rows]

    def claim_next_archival_record(
        self,
        *,
        now: float,
        lease_seconds: float,
        permit_seconds: float,
        archival_id: Optional[str] = None,
    ) -> Optional[ArchivalRecord]:
        """Leases one ready record with first-writer-wins SQLite locking."""
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            params = [
                ArchivalStatus.PENDING.value,
                ArchivalStatus.RETRY_WAIT.value,
                now,
                ArchivalStatus.PROCESSING.value,
                now,
            ]
            archival_filter = ""
            if archival_id is not None:
                archival_filter = "AND archival_id = ?"
                params.append(archival_id)
            row = conn.execute(
                f"""
                SELECT data FROM archival_records
                WHERE (
                    status = ?
                    OR (status = ? AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                    OR (
                        status = ?
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= ?
                    )
                )
                {archival_filter}
                ORDER BY sequence
                LIMIT 1
                """,
                params,
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            existing = self._archival_record_from_row(row)
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
                updated_at=utc_now(),
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
            self._write_archival_row(conn.cursor(), claimed)
            conn.commit()
            return claimed

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
        """Renews one current, unexpired processing lease transactionally."""
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT data FROM archival_records
                WHERE relationship_id = ? AND archival_id = ?
                """,
                (relationship_id, archival_id),
            ).fetchone()
            if row is None:
                conn.commit()
                return False
            existing = self._archival_record_from_row(row)
            if (
                existing.receipt.status != ArchivalStatus.PROCESSING
                or existing.attempt_id != attempt_id
                or existing.lease_token != lease_token
                or existing.lease_expires_at is None
                or existing.lease_expires_at <= now
            ):
                conn.commit()
                return False
            renewed = replace(
                existing,
                lease_expires_at=now + lease_seconds,
            )
            self._write_archival_row(conn.cursor(), renewed)
            conn.commit()
            return True

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
        """Persists an immutable batch binding under the active lease."""
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data FROM archival_records WHERE archival_id = ?",
                (record.receipt.archival_id,),
            ).fetchone()
            if row is None:
                raise ArchivalNotFoundError("archival was not found")
            existing = self._archival_record_from_row(row)
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
                raise ArchivalConflictError("archival is bound to another batch")
            self._write_archival_row(conn.cursor(), record)
            conn.commit()
            return record

    def commit_archival_batch(self, record: ArchivalRecord) -> ArchivalRecord:
        """Publishes nodes, timeline, and terminal receipt in one transaction."""
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data FROM archival_records WHERE archival_id = ?",
                (record.receipt.archival_id,),
            ).fetchone()
            if row is None:
                raise ArchivalNotFoundError("archival was not found")
            existing = self._archival_record_from_row(row)
            self._validate_archival_update(existing, record)
            batch = existing.prepared_batch
            if (
                batch is None
                or existing.commit_binding_digest != batch.batch_digest
                or existing.commit_permit is None
                or existing.commit_permit != record.commit_permit
                or existing.commit_permit.binding_digest
                != existing.commit_binding_digest
                or existing.commit_permit.expires_at <= time.time()
            ):
                raise ArchivalConflictError("archival commit permit is invalid")
            for node in batch.memories:
                conn.execute(
                    """
                    INSERT INTO memory_nodes (node_id, agent_id, user_id, data)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        node.node_id,
                        record.receipt.agent_id,
                        record.receipt.user_id,
                        json.dumps(node.to_dict(), ensure_ascii=False),
                    ),
                )
            for entry in batch.timeline:
                conn.execute(
                    """
                    INSERT INTO timeline_entries (
                        agent_id, user_id, content, timestamp,
                        timeline_entry_id, source_archival_id, data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.agent_id,
                        entry.user_id,
                        entry.content,
                        entry.recorded_at,
                        entry.timeline_entry_id,
                        entry.source_archival_id,
                        json.dumps(entry.to_dict(), ensure_ascii=False),
                    ),
                )
            stored = replace(
                record,
                lease_token=None,
                lease_expires_at=None,
                attempt_id=None,
                commit_permit=None,
                recovered_expired_lease=False,
            )
            self._write_archival_row(conn.cursor(), stored)
            conn.commit()
            return stored

    def acquire_archival_consumer(
        self,
        consumer_id: str,
        *,
        now: float,
        lease_seconds: float,
    ) -> bool:
        """Acquires the singleton archival consumer lease transactionally."""
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT consumer_id, expires_at
                FROM archival_consumer_leases
                WHERE lease_name = 'global'
                """
            ).fetchone()
            if (
                row is not None
                and row["consumer_id"] != consumer_id
                and float(row["expires_at"]) > now
            ):
                conn.commit()
                return False
            conn.execute(
                """
                INSERT INTO archival_consumer_leases (
                    lease_name, consumer_id, expires_at
                ) VALUES ('global', ?, ?)
                ON CONFLICT(lease_name) DO UPDATE SET
                    consumer_id = excluded.consumer_id,
                    expires_at = excluded.expires_at
                """,
                (consumer_id, now + lease_seconds),
            )
            conn.commit()
            return True

    def release_archival_consumer(self, consumer_id: str) -> None:
        """Releases the singleton lease only for its current owner."""
        with closing(self._get_connection()) as conn:
            conn.execute(
                """
                DELETE FROM archival_consumer_leases
                WHERE lease_name = 'global' AND consumer_id = ?
                """,
                (consumer_id,),
            )
            conn.commit()

    def update_archival_record(self, record: ArchivalRecord) -> ArchivalRecord:
        """Persists a retry or failed transition under its current lease."""
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data FROM archival_records WHERE archival_id = ?",
                (record.receipt.archival_id,),
            ).fetchone()
            if row is None:
                raise ArchivalNotFoundError("archival was not found")
            existing = self._archival_record_from_row(row)
            self._validate_archival_update(existing, record)
            stored = replace(
                record,
                lease_token=None,
                lease_expires_at=None,
                attempt_id=None,
                commit_permit=None,
                recovered_expired_lease=False,
            )
            self._write_archival_row(conn.cursor(), stored)
            conn.commit()
            return stored

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
    def _profile_from_row(
        row: sqlite3.Row,
        context_data: Optional[str] = None,
    ) -> RelationshipProfile:
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
        if context_data:
            context = json.loads(context_data)
            data.update(
                {
                    "premise": context.get("premise", {}),
                    "baseline": context.get("baseline"),
                    "manifest_id": context.get("manifest_id"),
                }
            )
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
                if row is None:
                    conn.rollback()
                    raise RuntimeError("relationship profile could not be created")
                initial_context = {
                    "premise": profile.premise.to_dict(),
                    "baseline": profile.baseline.to_dict(),
                    "manifest_id": profile.manifest_id,
                }
                conn.execute(
                    """
                    INSERT OR IGNORE INTO relationship_initial_context
                        (relationship_id, data, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        row["relationship_id"],
                        json.dumps(initial_context, ensure_ascii=False),
                        profile.created_at,
                    ),
                )
                context_row = conn.execute(
                    "SELECT data FROM relationship_initial_context WHERE relationship_id = ?",
                    (row["relationship_id"],),
                ).fetchone()
                stored = self._profile_from_row(
                    row,
                    context_row["data"] if context_row is not None else None,
                )
                if stored.to_dict() != profile.to_dict():
                    conn.rollback()
                    raise PersonaConflictError(
                        "relationship initialization conflicts with its immutable profile"
                    )
                conn.commit()
                return stored

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
                if row is None:
                    return None
                context_row = conn.execute(
                    "SELECT data FROM relationship_initial_context WHERE relationship_id = ?",
                    (row["relationship_id"],),
                ).fetchone()
                return self._profile_from_row(
                    row,
                    context_row["data"] if context_row is not None else None,
                )

    def create_turn_record(self, record: TurnRecord) -> TurnRecord:
        """Creates one exact turn identity without overwriting prior content."""
        with self.lock_manager.lock("__turn_records__", record.relationship_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT data FROM source_turns
                    WHERE relationship_id = ? AND turn_id = ?
                    """,
                    (record.relationship_id, record.turn_id),
                ).fetchone()
                if row is not None:
                    existing = TurnRecord.from_dict(json.loads(row["data"]))
                    conn.commit()
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
                conn.execute(
                    """
                    INSERT INTO source_turns
                        (turn_id, relationship_id, status, data, opened_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.turn_id,
                        record.relationship_id,
                        record.status.value,
                        json.dumps(record.to_dict(), ensure_ascii=False),
                        record.opened_at,
                    ),
                )
                conn.commit()
                return record

    def get_turn_record(self, relationship_id: str, turn_id: str) -> TurnRecord:
        """Loads one turn from its relationship scope."""
        with self.lock_manager.lock("__turn_records__", relationship_id):
            with closing(self._get_connection()) as conn:
                row = conn.execute(
                    """
                    SELECT data FROM source_turns
                    WHERE relationship_id = ? AND turn_id = ?
                    """,
                    (relationship_id, turn_id),
                ).fetchone()
                if row is not None:
                    return TurnRecord.from_dict(json.loads(row["data"]))
        raise TurnNotFoundError(f"turn {turn_id!r} was not found")

    def list_turn_records(self, relationship_id: str) -> List[TurnRecord]:
        """Returns source turns in durable opening order."""
        with self.lock_manager.lock("__turn_records__", relationship_id):
            with closing(self._get_connection()) as conn:
                rows = conn.execute(
                    """
                    SELECT data FROM source_turns
                    WHERE relationship_id = ?
                    ORDER BY sequence
                    """,
                    (relationship_id,),
                ).fetchall()
                return [
                    TurnRecord.from_dict(json.loads(row["data"])) for row in rows
                ]

    def transition_turn_record(
        self,
        record: TurnRecord,
        expected_status: TurnStatus,
        expected_record_version: int,
    ) -> TurnRecord:
        """Atomically installs one terminal revision with status/revision CAS."""
        with self.lock_manager.lock("__turn_records__", record.relationship_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT data FROM source_turns
                    WHERE relationship_id = ? AND turn_id = ?
                    """,
                    (record.relationship_id, record.turn_id),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    raise TurnNotFoundError(f"turn {record.turn_id!r} was not found")
                existing = TurnRecord.from_dict(json.loads(row["data"]))
                if existing == record:
                    conn.commit()
                    return existing
                if (
                    existing.status != expected_status
                    or existing.record_version != expected_record_version
                    or not record.is_terminal_transition_from(existing)
                ):
                    conn.rollback()
                    raise TurnTerminalConflictError(
                        f"turn {record.turn_id!r} transition violates its immutable opening"
                    )
                cursor = conn.execute(
                    """
                    UPDATE source_turns
                    SET status = ?, data = ?
                    WHERE relationship_id = ? AND turn_id = ?
                      AND status = ?
                    """,
                    (
                        record.status.value,
                        json.dumps(record.to_dict(), ensure_ascii=False),
                        record.relationship_id,
                        record.turn_id,
                        expected_status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    raise TurnTerminalConflictError(
                        f"turn {record.turn_id!r} changed concurrently"
                    )
                conn.commit()
                return record

    def append_reply_attempt(self, attempt: ReplyAttemptRecord) -> ReplyAttemptRecord:
        """Appends safe failure metadata only while its turn remains open."""
        with self.lock_manager.lock("__turn_records__", attempt.relationship_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                turn_row = conn.execute(
                    """
                    SELECT data FROM source_turns
                    WHERE relationship_id = ? AND turn_id = ?
                    """,
                    (attempt.relationship_id, attempt.turn_id),
                ).fetchone()
                if turn_row is None:
                    conn.rollback()
                    raise TurnNotFoundError(
                        f"turn {attempt.turn_id!r} was not found"
                    )
                turn = TurnRecord.from_dict(json.loads(turn_row["data"]))
                if turn.status != TurnStatus.OPEN:
                    conn.rollback()
                    raise TurnTerminalConflictError(
                        f"turn {attempt.turn_id!r} no longer accepts reply attempts"
                    )
                row = conn.execute(
                    """
                    SELECT data FROM reply_attempts
                    WHERE attempt_id = ?
                       OR (
                           relationship_id = ? AND turn_id = ?
                           AND attempt_number = ?
                       )
                    """,
                    (
                        attempt.attempt_id,
                        attempt.relationship_id,
                        attempt.turn_id,
                        attempt.attempt_number,
                    ),
                ).fetchone()
                if row is not None:
                    existing = ReplyAttemptRecord.from_dict(json.loads(row["data"]))
                    conn.commit()
                    if existing.same_payload_as(attempt):
                        return existing
                    raise ReplyAttemptConflictError(
                        "reply attempt identity already has different metadata"
                    )
                conn.execute(
                    """
                    INSERT INTO reply_attempts (
                        attempt_id, relationship_id, turn_id, attempt_number,
                        data, attempted_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt.attempt_id,
                        attempt.relationship_id,
                        attempt.turn_id,
                        attempt.attempt_number,
                        json.dumps(attempt.to_dict(), ensure_ascii=False),
                        attempt.attempted_at,
                    ),
                )
                conn.commit()
                return attempt

    def list_reply_attempts(
        self,
        relationship_id: str,
        turn_id: str,
    ) -> List[ReplyAttemptRecord]:
        """Returns safe attempt records in attempt-number order."""
        with self.lock_manager.lock("__turn_records__", relationship_id):
            with closing(self._get_connection()) as conn:
                rows = conn.execute(
                    """
                    SELECT data FROM reply_attempts
                    WHERE relationship_id = ? AND turn_id = ?
                    ORDER BY attempt_number
                    """,
                    (relationship_id, turn_id),
                ).fetchall()
                return [
                    ReplyAttemptRecord.from_dict(json.loads(row["data"]))
                    for row in rows
                ]

    def append_relationship_event(self, event: RelationshipEvent) -> RelationshipEvent:
        """Appends an event once and rejects conflicting event ID reuse."""
        with self.lock_manager.lock("__relationship_history__", event.relationship_id):
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

                direct_rows = conn.execute(
                    """
                    SELECT data FROM relationship_events
                    WHERE relationship_id = ? ORDER BY sequence ASC
                    """,
                    (event.relationship_id,),
                ).fetchall()
                adjudication_rows = conn.execute(
                    """
                    SELECT data FROM relationship_adjudications
                    WHERE relationship_id = ? ORDER BY sequence ASC
                    """,
                    (event.relationship_id,),
                ).fetchall()
                existing_events = [
                    RelationshipEvent.from_dict(json.loads(item["data"]))
                    for item in direct_rows
                ]
                existing_events.extend(
                    accepted_event
                    for item in adjudication_rows
                    for accepted_event in AdjudicationRecord.from_dict(
                        json.loads(item["data"])
                    ).events
                )
                TemporalHistoryValidator.validate_append(existing_events, event)

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

    def commit_relationship_adjudication(
        self,
        record: AdjudicationRecord,
    ) -> AdjudicationRecord:
        """Atomically persists one full candidate decision record."""
        receipt = record.receipt
        with self.lock_manager.lock("__relationship_history__", receipt.relationship_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT data FROM relationship_adjudications
                    WHERE decision_id = ? OR (
                        relationship_id = ? AND source_turn_id = ?
                        AND source_revision = ? AND processing_identity = ?
                        AND candidate_key = ?
                    )
                    """,
                    (
                        receipt.decision_id,
                        receipt.relationship_id,
                        receipt.source_turn_id,
                        receipt.source_revision,
                        (
                            f"{receipt.processing_mode.value}:"
                            f"{receipt.reprocessing_id or ''}"
                        ),
                        receipt.candidate_key,
                    ),
                ).fetchone()
                if row is not None:
                    existing = AdjudicationRecord.from_dict(json.loads(row["data"]))
                    conn.commit()
                    if (
                        existing.receipt.candidate_fingerprint
                        != record.receipt.candidate_fingerprint
                    ):
                        raise CandidateConflictError(
                            "candidate decision identity already has different persisted content"
                        )
                    return existing

                relationship = conn.execute(
                    "SELECT 1 FROM relationships WHERE relationship_id = ?",
                    (receipt.relationship_id,),
                ).fetchone()
                if relationship is None:
                    conn.rollback()
                    raise ValueError("adjudication references an unknown relationship")
                direct_rows = conn.execute(
                    """
                    SELECT data FROM relationship_events
                    WHERE relationship_id = ? ORDER BY sequence ASC
                    """,
                    (receipt.relationship_id,),
                ).fetchall()
                adjudication_rows = conn.execute(
                    """
                    SELECT data FROM relationship_adjudications
                    WHERE relationship_id = ? ORDER BY sequence ASC
                    """,
                    (receipt.relationship_id,),
                ).fetchall()
                existing_events = [
                    RelationshipEvent.from_dict(json.loads(item["data"]))
                    for item in direct_rows
                ]
                existing_events.extend(
                    accepted_event
                    for item in adjudication_rows
                    for accepted_event in AdjudicationRecord.from_dict(
                        json.loads(item["data"])
                    ).events
                )
                for accepted_event in record.events:
                    TemporalHistoryValidator.validate_append(
                        existing_events,
                        accepted_event,
                    )
                    existing_events.append(accepted_event)
                conn.execute(
                    """
                    INSERT INTO relationship_adjudications (
                        decision_id, relationship_id, source_turn_id,
                        source_revision, processing_identity, candidate_key,
                        data, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.decision_id,
                        receipt.relationship_id,
                        receipt.source_turn_id,
                        receipt.source_revision,
                        (
                            f"{receipt.processing_mode.value}:"
                            f"{receipt.reprocessing_id or ''}"
                        ),
                        receipt.candidate_key,
                        json.dumps(record.to_dict(), ensure_ascii=False),
                        receipt.created_at,
                    ),
                )
                conn.commit()
                return record

    def list_relationship_adjudications(
        self,
        relationship_id: str,
    ) -> List[AdjudicationRecord]:
        """Loads candidate decisions for one relationship in commit order."""
        with self.lock_manager.lock("__relationship_adjudication__", relationship_id):
            with closing(self._get_connection()) as conn:
                rows = conn.execute(
                    """
                    SELECT data FROM relationship_adjudications
                    WHERE relationship_id = ? ORDER BY sequence ASC
                    """,
                    (relationship_id,),
                ).fetchall()
                return [AdjudicationRecord.from_dict(json.loads(row["data"])) for row in rows]

    def save_persona_growth_proposal(
        self,
        proposal: PersonaGrowthProposal,
        expected_status: Optional[PersonaGrowthStatus] = None,
    ) -> PersonaGrowthProposal:
        """Creates or conditionally updates one growth proposal."""
        with self.lock_manager.lock("__persona_growth__", proposal.relationship_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT data FROM persona_growth_proposals WHERE proposal_id = ?",
                    (proposal.proposal_id,),
                ).fetchone()
                if row is None:
                    if expected_status is not None:
                        conn.rollback()
                        raise PersonaGrowthConflictError(
                            "persona growth proposal no longer exists"
                        )
                    relationship = conn.execute(
                        "SELECT 1 FROM relationships WHERE relationship_id = ?",
                        (proposal.relationship_id,),
                    ).fetchone()
                    if relationship is None:
                        conn.rollback()
                        raise ValueError(
                            "persona growth proposal references an unknown relationship"
                        )
                    conn.execute(
                        """
                        INSERT INTO persona_growth_proposals (
                            proposal_id, relationship_id, revision, status,
                            data, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            proposal.proposal_id,
                            proposal.relationship_id,
                            proposal.revision,
                            proposal.status.value,
                            json.dumps(proposal.to_dict(), ensure_ascii=False),
                            proposal.created_at,
                            proposal.decided_at or proposal.created_at,
                        ),
                    )
                    conn.commit()
                    return proposal

                existing = PersonaGrowthProposal.from_dict(json.loads(row["data"]))
                if self._proposal_content(existing) != self._proposal_content(proposal):
                    conn.rollback()
                    raise PersonaGrowthConflictError("persona growth proposal content is immutable")
                if expected_status is None:
                    conn.commit()
                    if self._proposal_lifecycle(existing) != self._proposal_lifecycle(
                        proposal
                    ):
                        raise PersonaGrowthConflictError(
                            "updating a proposal requires its expected status"
                        )
                    return existing
                cursor = conn.execute(
                    """
                    UPDATE persona_growth_proposals
                    SET status = ?, data = ?, updated_at = ?
                    WHERE proposal_id = ? AND status = ?
                    """,
                    (
                        proposal.status.value,
                        json.dumps(proposal.to_dict(), ensure_ascii=False),
                        proposal.decided_at or utc_now(),
                        proposal.proposal_id,
                        expected_status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    raise PersonaGrowthConflictError("persona growth proposal status changed")
                conn.commit()
                return proposal

    def list_persona_growth_proposals(
        self,
        relationship_id: str,
    ) -> List[PersonaGrowthProposal]:
        """Loads persona growth proposals in creation order."""
        with self.lock_manager.lock("__persona_growth__", relationship_id):
            with closing(self._get_connection()) as conn:
                rows = conn.execute(
                    """
                    SELECT data FROM persona_growth_proposals
                    WHERE relationship_id = ? ORDER BY created_at ASC, proposal_id ASC
                    """,
                    (relationship_id,),
                ).fetchall()
                return [PersonaGrowthProposal.from_dict(json.loads(row["data"])) for row in rows]

    def save_persona_compilation_proposal(
        self,
        proposal: PersonaCompilationProposal,
        expected_status: Optional[PersonaCompilationStatus] = None,
    ) -> PersonaCompilationProposal:
        """Appends or conditionally updates one immutable compilation revision."""
        with self.lock_manager.lock("__persona_compilation__", proposal.blueprint_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT data FROM persona_compilation_revisions
                    WHERE proposal_id = ? AND revision = ?
                    """,
                    (proposal.proposal_id, proposal.revision),
                ).fetchone()
                if row is None:
                    if expected_status is not None:
                        conn.rollback()
                        raise PersonaCompilationConflictError(
                            "persona compilation proposal revision no longer exists"
                        )
                    if proposal.revision > 1:
                        parent = conn.execute(
                            """
                            SELECT 1 FROM persona_compilation_revisions
                            WHERE proposal_id = ? AND revision = ?
                            """,
                            (proposal.proposal_id, proposal.parent_revision),
                        ).fetchone()
                        if parent is None:
                            conn.rollback()
                            raise PersonaCompilationConflictError(
                                "persona compilation parent revision does not exist"
                            )
                    conn.execute(
                        """
                        INSERT INTO persona_compilation_revisions (
                            proposal_id, revision, blueprint_id, content_fingerprint,
                            status, data, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            proposal.proposal_id,
                            proposal.revision,
                            proposal.blueprint_id,
                            proposal.content_fingerprint,
                            proposal.status.value,
                            json.dumps(proposal.to_dict(), ensure_ascii=False),
                            proposal.created_at,
                            proposal.decided_at or proposal.created_at,
                        ),
                    )
                    conn.commit()
                    return proposal

                existing = PersonaCompilationProposal.from_dict(json.loads(row["data"]))
                if self._compilation_content(existing) != self._compilation_content(proposal):
                    conn.rollback()
                    raise PersonaCompilationConflictError(
                        "persona compilation revision content is immutable"
                    )
                if expected_status is None:
                    conn.commit()
                    if self._compilation_lifecycle(existing) != self._compilation_lifecycle(
                        proposal
                    ):
                        raise PersonaCompilationConflictError(
                            "updating a compilation decision requires its expected status"
                        )
                    return existing
                cursor = conn.execute(
                    """
                    UPDATE persona_compilation_revisions
                    SET status = ?, data = ?, updated_at = ?
                    WHERE proposal_id = ? AND revision = ? AND status = ?
                    """,
                    (
                        proposal.status.value,
                        json.dumps(proposal.to_dict(), ensure_ascii=False),
                        proposal.decided_at or utc_now(),
                        proposal.proposal_id,
                        proposal.revision,
                        expected_status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    raise PersonaCompilationConflictError(
                        "persona compilation proposal status changed"
                    )
                conn.commit()
                return proposal

    def list_persona_compilation_proposals(
        self,
        blueprint_id: str,
    ) -> List[PersonaCompilationProposal]:
        """Loads compilation revisions in stable proposal/revision order."""
        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT data FROM persona_compilation_revisions
                WHERE blueprint_id = ? ORDER BY proposal_id ASC, revision ASC
                """,
                (blueprint_id,),
            ).fetchall()
            return [
                PersonaCompilationProposal.from_dict(json.loads(row["data"]))
                for row in rows
            ]

    def approve_persona_manifest(
        self,
        proposal: PersonaCompilationProposal,
        manifest: PersonaManifest,
        expected_status: PersonaCompilationStatus = PersonaCompilationStatus.PENDING,
    ) -> PersonaManifest:
        """Atomically applies an exact proposal approval and stores its Manifest."""
        self._validate_manifest_approval(proposal, manifest)
        with self.lock_manager.lock("__persona_compilation__", proposal.blueprint_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing_manifest_row = conn.execute(
                    "SELECT data FROM persona_manifests WHERE manifest_id = ?",
                    (manifest.manifest_id,),
                ).fetchone()
                if existing_manifest_row is not None:
                    existing_manifest = PersonaManifest.from_dict(
                        json.loads(existing_manifest_row["data"])
                    )
                    conn.commit()
                    if existing_manifest.to_dict() != manifest.to_dict():
                        raise PersonaCompilationConflictError(
                            "manifest ID has different content"
                        )
                    return existing_manifest

                row = conn.execute(
                    """
                    SELECT data, status FROM persona_compilation_revisions
                    WHERE proposal_id = ? AND revision = ?
                    """,
                    (proposal.proposal_id, proposal.revision),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    raise PersonaCompilationConflictError(
                        "persona compilation revision is missing"
                    )
                existing = PersonaCompilationProposal.from_dict(json.loads(row["data"]))
                if existing.status != expected_status:
                    conn.rollback()
                    raise PersonaCompilationConflictError(
                        "persona compilation proposal status changed"
                    )
                if self._compilation_content(existing) != self._compilation_content(proposal):
                    conn.rollback()
                    raise PersonaCompilationConflictError(
                        "approved proposal content differs from persisted revision"
                    )
                cursor = conn.execute(
                    """
                    UPDATE persona_compilation_revisions
                    SET status = ?, data = ?, updated_at = ?
                    WHERE proposal_id = ? AND revision = ? AND status = ?
                    """,
                    (
                        proposal.status.value,
                        json.dumps(proposal.to_dict(), ensure_ascii=False),
                        proposal.decided_at or utc_now(),
                        proposal.proposal_id,
                        proposal.revision,
                        expected_status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    raise PersonaCompilationConflictError(
                        "persona compilation proposal status changed"
                    )
                try:
                    conn.execute(
                        """
                        INSERT INTO persona_manifests (
                            manifest_id, blueprint_id, proposal_id, proposal_revision,
                            content_fingerprint, data, approved_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            manifest.manifest_id,
                            manifest.blueprint_id,
                            manifest.approved_proposal_id,
                            manifest.approved_revision,
                            manifest.content_fingerprint,
                            json.dumps(manifest.to_dict(), ensure_ascii=False),
                            manifest.approved_at,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    raise PersonaCompilationConflictError(
                        "proposal revision already has a different manifest"
                    ) from exc
                conn.commit()
                return manifest

    def approve_and_bind_persona_manifest(
        self,
        profile: RelationshipProfile,
        proposal: PersonaCompilationProposal,
        manifest: PersonaManifest,
        expected_status: PersonaCompilationStatus = PersonaCompilationStatus.PENDING,
    ) -> PersonaManifest:
        """Approves and binds one exact Manifest in a single SQLite transaction."""
        self._validate_manifest_approval(proposal, manifest)
        with self.lock_manager.lock("__persona_compilation__", proposal.blueprint_id):
            with self.lock_manager.lock(profile.agent_id, profile.user_id):
                with closing(self._get_connection()) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    relationship_row = conn.execute(
                        "SELECT * FROM relationships WHERE relationship_id = ?",
                        (profile.relationship_id,),
                    ).fetchone()
                    if relationship_row is None:
                        conn.rollback()
                        raise ValueError("relationship profile does not exist")
                    if (
                        relationship_row["agent_id"] != profile.agent_id
                        or relationship_row["user_id"] != profile.user_id
                        or relationship_row["blueprint_id"] != proposal.blueprint_id
                    ):
                        conn.rollback()
                        raise PersonaCompilationConflictError(
                            "Manifest approval targets a different relationship or Blueprint"
                        )

                    context_row = conn.execute(
                        "SELECT data FROM relationship_initial_context WHERE relationship_id = ?",
                        (profile.relationship_id,),
                    ).fetchone()
                    context = (
                        json.loads(context_row["data"])
                        if context_row is not None
                        else {
                            "premise": profile.premise.to_dict(),
                            "baseline": profile.baseline.to_dict(),
                            "manifest_id": None,
                        }
                    )
                    existing_binding = context.get("manifest_id")
                    if existing_binding not in (None, manifest.manifest_id):
                        conn.rollback()
                        raise PersonaCompilationConflictError(
                            "relationship is already pinned to a different Manifest"
                        )

                    proposal_row = conn.execute(
                        """
                        SELECT data FROM persona_compilation_revisions
                        WHERE proposal_id = ? AND revision = ?
                        """,
                        (proposal.proposal_id, proposal.revision),
                    ).fetchone()
                    if proposal_row is None:
                        conn.rollback()
                        raise PersonaCompilationConflictError(
                            "persona compilation revision is missing"
                        )
                    persisted_proposal = PersonaCompilationProposal.from_dict(
                        json.loads(proposal_row["data"])
                    )
                    if self._compilation_content(
                        persisted_proposal
                    ) != self._compilation_content(proposal):
                        conn.rollback()
                        raise PersonaCompilationConflictError(
                            "approved proposal content differs from persisted revision"
                        )
                    if persisted_proposal.status != expected_status:
                        conn.rollback()
                        raise PersonaCompilationConflictError(
                            "persona compilation proposal status changed"
                        )
                    if expected_status == PersonaCompilationStatus.APPROVED:
                        if self._compilation_lifecycle(
                            persisted_proposal
                        ) != self._compilation_lifecycle(proposal):
                            conn.rollback()
                            raise PersonaCompilationConflictError(
                                "approved proposal lifecycle differs from persisted revision"
                            )
                    else:
                        cursor = conn.execute(
                            """
                            UPDATE persona_compilation_revisions
                            SET status = ?, data = ?, updated_at = ?
                            WHERE proposal_id = ? AND revision = ? AND status = ?
                            """,
                            (
                                proposal.status.value,
                                json.dumps(proposal.to_dict(), ensure_ascii=False),
                                proposal.decided_at or utc_now(),
                                proposal.proposal_id,
                                proposal.revision,
                                expected_status.value,
                            ),
                        )
                        if cursor.rowcount != 1:
                            conn.rollback()
                            raise PersonaCompilationConflictError(
                                "persona compilation proposal status changed"
                            )

                    manifest_rows = conn.execute(
                        """
                        SELECT data FROM persona_manifests
                        WHERE manifest_id = ? OR (proposal_id = ? AND proposal_revision = ?)
                        """,
                        (
                            manifest.manifest_id,
                            proposal.proposal_id,
                            proposal.revision,
                        ),
                    ).fetchall()
                    persisted_manifest = None
                    for manifest_row in manifest_rows:
                        candidate = PersonaManifest.from_dict(
                            json.loads(manifest_row["data"])
                        )
                        if candidate.to_dict() != manifest.to_dict():
                            conn.rollback()
                            raise PersonaCompilationConflictError(
                                "proposal revision already has a different Manifest"
                            )
                        persisted_manifest = candidate
                    if persisted_manifest is None:
                        try:
                            conn.execute(
                                """
                                INSERT INTO persona_manifests (
                                    manifest_id, blueprint_id, proposal_id,
                                    proposal_revision, content_fingerprint, data,
                                    approved_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    manifest.manifest_id,
                                    manifest.blueprint_id,
                                    manifest.approved_proposal_id,
                                    manifest.approved_revision,
                                    manifest.content_fingerprint,
                                    json.dumps(manifest.to_dict(), ensure_ascii=False),
                                    manifest.approved_at,
                                ),
                            )
                        except sqlite3.IntegrityError as exc:
                            conn.rollback()
                            raise PersonaCompilationConflictError(
                                "proposal revision already has a different Manifest"
                            ) from exc

                    context["manifest_id"] = manifest.manifest_id
                    conn.execute(
                        """
                        INSERT INTO relationship_initial_context
                            (relationship_id, data, created_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(relationship_id) DO UPDATE SET data = excluded.data
                        """,
                        (
                            profile.relationship_id,
                            json.dumps(context, ensure_ascii=False),
                            profile.created_at,
                        ),
                    )
                    conn.commit()
                    return persisted_manifest or manifest

    def get_persona_manifest(self, manifest_id: str) -> Optional[PersonaManifest]:
        """Loads one approved Persona Manifest."""
        with closing(self._get_connection()) as conn:
            row = conn.execute(
                "SELECT data FROM persona_manifests WHERE manifest_id = ?",
                (manifest_id,),
            ).fetchone()
            return (
                PersonaManifest.from_dict(json.loads(row["data"]))
                if row is not None
                else None
            )

    def list_persona_manifests(self, blueprint_id: str) -> List[PersonaManifest]:
        """Loads approved Persona Manifests for one Blueprint."""
        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT data FROM persona_manifests
                WHERE blueprint_id = ? ORDER BY approved_at ASC, manifest_id ASC
                """,
                (blueprint_id,),
            ).fetchall()
            return [PersonaManifest.from_dict(json.loads(row["data"])) for row in rows]

    def bind_relationship_manifest(
        self,
        profile: RelationshipProfile,
        manifest_id: str,
    ) -> RelationshipProfile:
        """Pins an approved Manifest in the relationship initial-context record."""
        manifest = self.get_persona_manifest(manifest_id)
        if manifest is None or manifest.blueprint_id != profile.blueprint.blueprint_id:
            raise PersonaCompilationConflictError(
                "manifest is missing or belongs to a different Character Blueprint"
            )
        with self.lock_manager.lock(profile.agent_id, profile.user_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM relationships WHERE relationship_id = ?",
                    (profile.relationship_id,),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    raise ValueError("relationship profile does not exist")
                context_row = conn.execute(
                    "SELECT data FROM relationship_initial_context WHERE relationship_id = ?",
                    (profile.relationship_id,),
                ).fetchone()
                context = (
                    json.loads(context_row["data"])
                    if context_row is not None
                    else {
                        "premise": profile.premise.to_dict(),
                        "baseline": profile.baseline.to_dict(),
                        "manifest_id": None,
                    }
                )
                existing_manifest_id = context.get("manifest_id")
                if existing_manifest_id is not None and existing_manifest_id != manifest_id:
                    conn.rollback()
                    raise PersonaCompilationConflictError(
                        "relationship is already pinned to a different Manifest"
                    )
                context["manifest_id"] = manifest_id
                conn.execute(
                    """
                    INSERT INTO relationship_initial_context (relationship_id, data, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(relationship_id) DO UPDATE SET data = excluded.data
                    """,
                    (
                        profile.relationship_id,
                        json.dumps(context, ensure_ascii=False),
                        profile.created_at,
                    ),
                )
                conn.commit()
                return replace(profile, manifest_id=manifest_id)

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
    def _compilation_content(proposal: PersonaCompilationProposal):
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
    def _proposal_content(proposal: PersonaGrowthProposal):
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
