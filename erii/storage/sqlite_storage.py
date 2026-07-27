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
from contextlib import closing
from typing import List, Optional
import uuid

from erii.models.adjudication import (
    AdjudicationRecord,
    CandidateConflictError,
    PersonaGrowthConflictError,
    PersonaGrowthProposal,
    PersonaGrowthStatus,
)
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

    def commit_relationship_adjudication(
        self,
        record: AdjudicationRecord,
    ) -> AdjudicationRecord:
        """Atomically persists one full candidate decision record."""
        receipt = record.receipt
        with self.lock_manager.lock(
            "__relationship_adjudication__",
            receipt.relationship_id,
        ):
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
