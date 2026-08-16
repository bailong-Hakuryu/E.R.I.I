"""Embedded SQLite Storage driver for E.R.I.I. Engine.

Provides relational, single-file database storage using Python standard sqlite3.
Follows Google Python Style Guide.
"""

from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import ExitStack, closing, contextmanager
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union
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
from erii.models.consolidation import (
    PersonaReflectionDecisionRecord,
    PersonaReflectionRecord,
    ReflectionProvenanceState,
    RelationshipProcessingConflictError,
    RelationshipProcessingRun,
)
from erii.models.consequence import (
    ConsequenceConflictError,
    NarrativeTensionConflictError,
    NarrativeTensionLink,
    RelationshipConsequence,
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
from erii.models.turn_context import TurnContextBaseline
from erii.core.temporal_history import TemporalHistoryValidator
from erii.compatibility import SQLITE_FORMAT
from erii.data_lifecycle import read_sqlite_schema_version
from erii.errors import MigrationRequiredError, UnsupportedFormatError
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.archival import ArchivalTombstoneValidationSource
from erii.storage.base import BaseStorage, cross_process_file_lock
from erii.storage.errors import StorageIntegrityError
from erii.storage.memory_pack import AtomicMemoryPackWriteStoreV1
from erii.storage.timeline_order import timeline_timestamp_sort_key
from erii.storage.turn_context import (
    TurnContextSourceSnapshot,
    validate_turn_context_baseline_authority,
)

def _decode_json_object(value: object, field_name: str) -> Dict[str, Any]:
    """Decodes one storage JSON object without accepting scalar containers."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be stored as JSON text")
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must contain a JSON object")
    return decoded


_MemoryPackResultT = TypeVar("_MemoryPackResultT")


class _SQLiteMemoryPackTransactionConnection:
    """Keeps legacy method-local transaction calls inside one outer unit."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @staticmethod
    def _is_transaction_control(sql: object) -> bool:
        if not isinstance(sql, str):
            return False
        normalized = " ".join(sql.strip().upper().split())
        if normalized.endswith(";"):
            normalized = normalized[:-1].rstrip()
        return normalized in {
            "BEGIN",
            "BEGIN TRANSACTION",
            "BEGIN DEFERRED",
            "BEGIN DEFERRED TRANSACTION",
            "BEGIN IMMEDIATE",
            "BEGIN IMMEDIATE TRANSACTION",
            "BEGIN EXCLUSIVE",
            "BEGIN EXCLUSIVE TRANSACTION",
            "COMMIT",
            "COMMIT TRANSACTION",
            "END",
            "END TRANSACTION",
            "ROLLBACK",
            "ROLLBACK TRANSACTION",
        }

    def execute(self, sql, *args, **kwargs):
        if self._is_transaction_control(sql):
            return self._connection.cursor()
        return self._connection.execute(sql, *args, **kwargs)

    def commit(self) -> None:
        """Defers commit to the outer MemoryPack transaction."""

    def rollback(self) -> None:
        """Defers rollback to the outer MemoryPack transaction."""

    def close(self) -> None:
        """Keeps the shared connection open for later payload batches."""

    def __getattr__(self, name: str):
        return getattr(self._connection, name)


class SQLiteStorage(BaseStorage):
    """SQLite-backed memory storage driver."""

    CURRENT_SCHEMA_VERSION = int(SQLITE_FORMAT.current_version)

    def __init__(self, db_path: str = "./erii_memory.db") -> None:
        """Initializes SQLiteStorage driver and sets up database schema.

        Args:
            db_path: Path to SQLite database file.
        """
        super().__init__()
        self.db_path = db_path
        self._memory_pack_write_local = threading.local()
        schema_version = read_sqlite_schema_version(self.db_path, immutable=False)
        if (
            schema_version is not None
            and schema_version < self.CURRENT_SCHEMA_VERSION
        ):
            raise MigrationRequiredError(
                f"{SQLITE_FORMAT.format_id} schema {schema_version} must be "
                f"upgraded explicitly to schema {self.CURRENT_SCHEMA_VERSION} "
                "before SQLiteStorage can open it"
            )
        self._init_db()

    def _open_connection(self) -> sqlite3.Connection:
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _get_connection(self):
        transaction_connection = getattr(
            self._memory_pack_write_local,
            "connection",
            None,
        )
        if transaction_connection is not None:
            return transaction_connection
        return self._open_connection()

    def atomic_memory_pack_write_store_v1(
        self,
    ) -> Optional[AtomicMemoryPackWriteStoreV1]:
        """Returns the SQLite whole-pack single-transaction capability."""
        if getattr(self._memory_pack_write_local, "connection", None) is not None:
            return None
        return self

    def execute_memory_pack_write(
        self,
        target_agent: str,
        target_user: str,
        relationship_id: Optional[str],
        operation: Callable[[Any], _MemoryPackResultT],
    ) -> _MemoryPackResultT:
        """Runs every payload method through one BEGIN IMMEDIATE transaction."""
        if getattr(self._memory_pack_write_local, "connection", None) is not None:
            raise RuntimeError("nested MemoryPack write transactions are invalid")
        clean_agent = SecuritySanitizer.validate_key(target_agent, "agent_id")
        clean_user = SecuritySanitizer.validate_key(target_user, "user_id")
        lock_key = os.path.realpath(os.path.abspath(self.db_path))
        lock_keys = [
            ("__memory_pack_write__", lock_key),
            (clean_agent, clean_user),
        ]
        if relationship_id is not None:
            lock_keys.extend(
                (
                    ("__turn_records__", relationship_id),
                    ("__relationship_events__", relationship_id),
                    ("__relationship_adjudication__", relationship_id),
                    ("__relationship_history__", relationship_id),
                    ("__persona_growth__", relationship_id),
                )
            )

        with ExitStack() as lock_stack:
            for first_key, second_key in lock_keys:
                lock_stack.enter_context(
                    self.lock_manager.lock(first_key, second_key)
                )
            with closing(self._open_connection()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._memory_pack_write_local.connection = (
                    _SQLiteMemoryPackTransactionConnection(connection)
                )
                try:
                    try:
                        result = operation(self)
                    except BaseException:
                        connection.rollback()
                        raise

                    try:
                        connection.commit()
                    except BaseException:
                        if connection.in_transaction:
                            connection.rollback()
                        # If SQLite already left transactional mode, commit
                        # success and automatic rollback cannot be told apart
                        # without a witness in the main database.  Preserve
                        # whichever all-or-nothing state SQLite published and
                        # propagate the indeterminate outcome to the caller.
                        raise
                    return result
                finally:
                    del self._memory_pack_write_local.connection

    def _get_relationship_processing_lock_path(self, relationship_id: str) -> str:
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()
        database_path = os.path.realpath(os.path.abspath(self.db_path))
        directory = f"{database_path}.relationship_processing_locks"
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{digest}.lock")

    @contextmanager
    def relationship_processing_guard(self, relationship_id: str):
        """Serializes host model calls before their decisions become durable."""
        if self.db_path == ":memory:":
            with super().relationship_processing_guard(relationship_id):
                yield
            return
        with cross_process_file_lock(
            self._get_relationship_processing_lock_path(relationship_id)
        ):
            yield

    def capture_turn_context_source(
        self,
        profile: RelationshipProfile,
    ) -> TurnContextSourceSnapshot:
        """Reads all Turn Context sources from one SQLite read snapshot."""
        with closing(self._get_connection()) as conn:
            try:
                # A deferred read transaction fixes its WAL snapshot at the
                # first SELECT without taking the write reservation used by
                # BEGIN IMMEDIATE mutation paths.
                conn.execute("BEGIN")
                snapshot = self._capture_turn_context_source_with_connection(
                    conn,
                    profile,
                )
                conn.commit()
                return snapshot
            except Exception:
                conn.rollback()
                raise

    def _capture_turn_context_source_with_connection(
        self,
        conn: sqlite3.Connection,
        profile: RelationshipProfile,
    ) -> TurnContextSourceSnapshot:
        """Reads a Turn Context source through the caller's open transaction."""
        relationship_row = conn.execute(
            "SELECT * FROM relationships WHERE relationship_id = ?",
            (profile.relationship_id,),
        ).fetchone()
        if relationship_row is None:
            raise ValueError("Turn Context relationship does not exist")

        context_row = conn.execute(
            """
            SELECT data FROM relationship_initial_context
            WHERE relationship_id = ?
            """,
            (profile.relationship_id,),
        ).fetchone()
        _decode_json_object(
            relationship_row["blueprint_data"],
            "relationship blueprint_data",
        )
        context_data = None
        if context_row is not None:
            _decode_json_object(
                context_row["data"],
                "relationship initial context",
            )
            context_data = context_row["data"]
        snapshot_profile = self._profile_from_row(
            relationship_row,
            context_data,
        )
        expected_profile = profile.to_dict()
        actual_profile = snapshot_profile.to_dict()
        # The Manifest binding is intentionally re-read inside the
        # transaction; every other profile value is immutable.
        expected_profile.pop("manifest_id", None)
        actual_profile.pop("manifest_id", None)
        if actual_profile != expected_profile:
            raise ValueError(
                "Turn Context profile differs from persisted relationship"
            )

        pinned_manifest = None
        backing_proposal = None
        if snapshot_profile.manifest_id is not None:
            manifest_row = conn.execute(
                """
                SELECT manifest_id, blueprint_id, proposal_id,
                       proposal_revision, content_fingerprint, data
                FROM persona_manifests WHERE manifest_id = ?
                """,
                (snapshot_profile.manifest_id,),
            ).fetchone()
            if manifest_row is not None:
                manifest_data = _decode_json_object(
                    manifest_row["data"],
                    "Persona Manifest data",
                )
                pinned_manifest = PersonaManifest.from_dict(manifest_data)
                if (
                    pinned_manifest.manifest_id != manifest_row["manifest_id"]
                    or pinned_manifest.blueprint_id != manifest_row["blueprint_id"]
                    or pinned_manifest.approved_proposal_id
                    != manifest_row["proposal_id"]
                    or pinned_manifest.approved_revision
                    != manifest_row["proposal_revision"]
                    or pinned_manifest.content_fingerprint
                    != manifest_row["content_fingerprint"]
                ):
                    raise ValueError(
                        "Persona Manifest columns differ from stored data"
                    )

                proposal_row = conn.execute(
                    """
                    SELECT proposal_id, revision, blueprint_id,
                           content_fingerprint, status, data
                    FROM persona_compilation_revisions
                    WHERE proposal_id = ? AND revision = ?
                    """,
                    (
                        pinned_manifest.approved_proposal_id,
                        pinned_manifest.approved_revision,
                    ),
                ).fetchone()
                if proposal_row is not None:
                    proposal_data = _decode_json_object(
                        proposal_row["data"],
                        "Persona Compilation data",
                    )
                    backing_proposal = PersonaCompilationProposal.from_dict(
                        proposal_data
                    )
                    if (
                        backing_proposal.proposal_id != proposal_row["proposal_id"]
                        or backing_proposal.revision != proposal_row["revision"]
                        or backing_proposal.blueprint_id
                        != proposal_row["blueprint_id"]
                        or backing_proposal.content_fingerprint
                        != proposal_row["content_fingerprint"]
                        or backing_proposal.status.value != proposal_row["status"]
                    ):
                        raise ValueError(
                            "Persona Compilation columns differ from stored data"
                        )

        growth_rows = conn.execute(
            """
            SELECT proposal_id, relationship_id, revision, status,
                   created_at, data
            FROM persona_growth_proposals
            WHERE relationship_id = ? AND status = ?
            ORDER BY created_at ASC, proposal_id ASC, revision ASC
            """,
            (
                snapshot_profile.relationship_id,
                PersonaGrowthStatus.APPROVED.value,
            ),
        ).fetchall()
        approved_growth = []
        for row in growth_rows:
            growth_data = _decode_json_object(
                row["data"],
                "Persona Growth data",
            )
            proposal = PersonaGrowthProposal.from_dict(growth_data)
            if (
                proposal.proposal_id != row["proposal_id"]
                or proposal.relationship_id != row["relationship_id"]
                or proposal.revision != row["revision"]
                or proposal.status.value != row["status"]
                or proposal.created_at != row["created_at"]
            ):
                raise ValueError("Persona Growth columns differ from stored data")
            approved_growth.append(proposal)

        event_rows = conn.execute(
            """
            SELECT event_id, relationship_id, data
            FROM relationship_events
            WHERE relationship_id = ? ORDER BY sequence ASC
            """,
            (snapshot_profile.relationship_id,),
        ).fetchall()
        direct_events = []
        for row in event_rows:
            event_data = _decode_json_object(
                row["data"],
                "Relationship Event data",
            )
            event = RelationshipEvent.from_dict(event_data)
            if (
                event.event_id != row["event_id"]
                or event.relationship_id != row["relationship_id"]
            ):
                raise ValueError(
                    "Relationship Event columns differ from stored data"
                )
            direct_events.append(event)

        adjudication_rows = conn.execute(
            """
            SELECT decision_id, relationship_id, data
            FROM relationship_adjudications
            WHERE relationship_id = ? ORDER BY sequence ASC
            """,
            (snapshot_profile.relationship_id,),
        ).fetchall()
        adjudications = []
        for row in adjudication_rows:
            adjudication_data = _decode_json_object(
                row["data"],
                "Relationship Adjudication data",
            )
            adjudication = AdjudicationRecord.from_dict(adjudication_data)
            if (
                adjudication.receipt.decision_id != row["decision_id"]
                or adjudication.receipt.relationship_id != row["relationship_id"]
            ):
                raise ValueError(
                    "Relationship Adjudication columns differ from stored data"
                )
            adjudications.append(adjudication)

        return TurnContextSourceSnapshot(
            profile=snapshot_profile,
            pinned_manifest=pinned_manifest,
            backing_compilation_proposal=backing_proposal,
            approved_growth=tuple(approved_growth),
            direct_events=tuple(direct_events),
            adjudications=tuple(adjudications),
        )

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
            if current_version > self.CURRENT_SCHEMA_VERSION:
                raise UnsupportedFormatError(
                    f"unsupported {SQLITE_FORMAT.format_id} version "
                    f"{current_version!r}; current reader is "
                    f"{SQLITE_FORMAT.current_version!r}"
                )
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
                current_version = 5
            if current_version < 6:
                self._migrate_relationship_consolidation_v6(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (6, "relationship-consolidation-alpha7", utc_now()),
                )
                current_version = 6
            if current_version < 7:
                self._migrate_recent_timeline_index_v7(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (7, "bounded-recent-timeline-alpha7", utc_now()),
                )
                current_version = 7
            if current_version < 8:
                self._migrate_semantic_timeline_order_v8(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (8, "semantic-timeline-order-alpha7", utc_now()),
                )
                current_version = 8
            if current_version < 9:
                self._migrate_stable_timeline_order_v9(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (9, "utc-stable-timeline-order-alpha7", utc_now()),
                )
                current_version = 9
            if current_version < 10:
                self._migrate_relationship_consequence_v10(cursor)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (10, "relationship-consequence-journal-alpha1", utc_now()),
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

    @staticmethod
    def _migrate_relationship_consolidation_v6(cursor: sqlite3.Cursor) -> None:
        """Adds frozen relationship runs and append-only reflection history."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS relationship_processing_runs (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                processing_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                source_turn_id TEXT NOT NULL,
                source_revision TEXT NOT NULL,
                processing_identity TEXT NOT NULL,
                record_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                data JSON NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (
                    relationship_id, source_turn_id, source_revision,
                    processing_identity
                ),
                FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id),
                FOREIGN KEY (relationship_id, source_turn_id)
                    REFERENCES source_turns(relationship_id, turn_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_relationship_processing_order
            ON relationship_processing_runs(relationship_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_reflection_decisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                interpretation_identity TEXT NOT NULL,
                data JSON NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE (relationship_id, interpretation_identity),
                FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reflection_decision_order
            ON persona_reflection_decisions(relationship_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_reflection_records (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                reflection_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                target_reflection_id TEXT NOT NULL DEFAULT '',
                data JSON NOT NULL,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY (relationship_id) REFERENCES relationships(relationship_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reflection_record_order
            ON persona_reflection_records(relationship_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reflection_record_target
            ON persona_reflection_records(relationship_id, target_reflection_id)
            """
        )

    @staticmethod
    def _migrate_recent_timeline_index_v7(cursor: sqlite3.Cursor) -> None:
        """Adds the scope-and-order index used by bounded Timeline reads."""
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_timeline_recent
            ON timeline_entries(agent_id, user_id, id DESC)
            """
        )

    @staticmethod
    def _migrate_semantic_timeline_order_v8(cursor: sqlite3.Cursor) -> None:
        """Indexes the effective Timeline time instead of insertion sequence."""
        cursor.execute("DROP INDEX IF EXISTS idx_timeline_recent")
        cursor.execute(
            """
            CREATE INDEX idx_timeline_recent
            ON timeline_entries(
                agent_id,
                user_id,
                timestamp DESC,
                id DESC
            )
            """
        )

    @staticmethod
    def _migrate_stable_timeline_order_v9(cursor: sqlite3.Cursor) -> None:
        """Backfills UTC sort keys and stable identities for bounded reads."""
        timeline_columns = {
            row[1]
            for row in cursor.execute("PRAGMA table_info(timeline_entries)").fetchall()
        }
        if "sort_key" not in timeline_columns:
            cursor.execute(
                "ALTER TABLE timeline_entries "
                "ADD COLUMN sort_key TEXT NOT NULL DEFAULT ''"
            )

        rows = cursor.execute(
            """
            SELECT id, agent_id, user_id, timestamp, timeline_entry_id, data
            FROM timeline_entries
            """
        ).fetchall()
        for row in rows:
            entry_id = str(row["timeline_entry_id"] or "").strip()
            timestamp = row["timestamp"]
            if row["data"]:
                try:
                    data = json.loads(row["data"])
                except (TypeError, ValueError):
                    data = None
                if isinstance(data, dict):
                    entry_id = str(data.get("timeline_entry_id") or entry_id).strip()
                    timestamp = (
                        data.get("recorded_at")
                        or data.get("legacy_timestamp")
                        or timestamp
                    )
            if not entry_id:
                entry_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        (
                            f"erii:legacy-timeline:{row['agent_id']}:"
                            f"{row['user_id']}:{row['id']}"
                        ),
                    )
                )
            cursor.execute(
                """
                UPDATE timeline_entries
                SET timeline_entry_id = ?, sort_key = ?
                WHERE id = ?
                """,
                (
                    entry_id,
                    timeline_timestamp_sort_key(timestamp),
                    row["id"],
                ),
            )

        cursor.execute("DROP INDEX IF EXISTS idx_timeline_recent")
        cursor.execute(
            """
            CREATE INDEX idx_timeline_recent
            ON timeline_entries(
                agent_id,
                user_id,
                sort_key DESC,
                timeline_entry_id DESC
            )
            """
        )

    @staticmethod
    def _migrate_relationship_consequence_v10(cursor: sqlite3.Cursor) -> None:
        """Adds append-only consequence and Narrative Tension journals."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS relationship_consequences (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                consequence_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                tension_id TEXT NOT NULL,
                source_decision_id TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                data JSON NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE (
                    relationship_id, source_decision_id, source_event_id
                ),
                FOREIGN KEY (relationship_id)
                    REFERENCES relationships(relationship_id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_relationship_consequences_order
            ON relationship_consequences(relationship_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_relationship_consequences_tension
            ON relationship_consequences(relationship_id, tension_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS narrative_tension_links (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id TEXT NOT NULL UNIQUE,
                relationship_id TEXT NOT NULL,
                tension_id TEXT NOT NULL,
                consequence_id TEXT NOT NULL,
                source_decision_id TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                data JSON NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE (tension_id, source_decision_id, source_event_id),
                FOREIGN KEY (relationship_id)
                    REFERENCES relationships(relationship_id) ON DELETE CASCADE,
                FOREIGN KEY (consequence_id)
                    REFERENCES relationship_consequences(consequence_id)
                    ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_narrative_tension_links_order
            ON narrative_tension_links(relationship_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_narrative_tension_links_tension
            ON narrative_tension_links(relationship_id, tension_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_narrative_tension_links_consequence
            ON narrative_tension_links(consequence_id, sequence)
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
                    SELECT node_id, agent_id, user_id, data FROM memory_nodes
                    WHERE agent_id = ? AND user_id = ?
                    """,
                    (clean_agent, clean_user),
                ).fetchall()
                existing_nodes = {
                    str(row["node_id"]): self._memory_node_from_row(row)
                    for row in existing_rows
                }
                keep_ids = set()
                for node in nodes:
                    if node.agent_id != clean_agent or node.user_id != clean_user:
                        raise ValueError(
                            "MemoryNode belongs to another Agent x User scope"
                        )
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
                    existing_node = existing_nodes[str(row["node_id"])]
                    if existing_node.source_archival_id is None:
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
                    """
                    SELECT node_id, agent_id, user_id, data FROM memory_nodes
                    WHERE agent_id = ? AND user_id = ?
                    """,
                    (clean_agent, clean_user),
                )
                rows = cursor.fetchall()
                return [self._memory_node_from_row(row) for row in rows]

    @staticmethod
    def _memory_node_from_row(row: sqlite3.Row) -> MemoryNode:
        """Decodes one node row without allowing a partial collection."""
        try:
            node = MemoryNode.from_dict(json.loads(row["data"]))
        except Exception as exc:
            raise StorageIntegrityError(
                "SQLite MemoryNode row is unreadable or malformed"
            ) from exc
        if (
            node.node_id != row["node_id"]
            or node.agent_id != row["agent_id"]
            or node.user_id != row["user_id"]
        ):
            raise StorageIntegrityError(
                "SQLite MemoryNode row identity differs from its payload"
            )
        return node

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
        sort_key = timeline_timestamp_sort_key(ts)

        with self.lock_manager.lock(clean_agent, clean_user):
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO timeline_entries (
                        agent_id, user_id, content, timestamp,
                        timeline_entry_id, sort_key
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (clean_agent, clean_user, entry, ts, None, sort_key),
                )
                # Legacy Timeline calls do not supply a durable identity. Bind
                # one to the transactional row ID instead of uuid4 so a failed
                # whole-pack attempt and its retry produce the same snapshot.
                entry_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        (
                            f"erii:legacy-timeline:{clean_agent}:"
                            f"{clean_user}:{cursor.lastrowid}"
                        ),
                    )
                )
                cursor.execute(
                    """
                    UPDATE timeline_entries
                    SET timeline_entry_id = ?
                    WHERE id = ?
                    """,
                    (entry_id, cursor.lastrowid),
                )
                conn.commit()

    def get_recent_timeline(
        self, agent_id: str, user_id: str, limit: int = 5
    ) -> List[str]:
        """Retrieves the same semantic-time tail as structured Timeline recall."""
        return [
            (
                f"[{item.recorded_at or item.legacy_timestamp or 'unknown'}] "
                f"{item.content}"
            )
            for item in self.get_recent_timeline_entries(
                agent_id,
                user_id,
                limit,
            )
        ]

    @staticmethod
    def _timeline_entry_from_row(
        row: sqlite3.Row,
        relationship_id: str,
        agent_id: str,
        user_id: str,
    ) -> TimelineEntry:
        """Projects one SQLite row through the canonical Timeline model."""
        if row["data"]:
            try:
                entry = TimelineEntry.from_dict(json.loads(row["data"]))
            except Exception as exc:
                raise StorageIntegrityError(
                    "SQLite Timeline row is unreadable or malformed"
                ) from exc
            if (
                entry.agent_id != agent_id
                or entry.user_id != user_id
                or (
                    row["timeline_entry_id"]
                    and entry.timeline_entry_id != row["timeline_entry_id"]
                )
            ):
                raise StorageIntegrityError(
                    "SQLite Timeline row identity differs from its payload"
                )
            return entry
        entry_id = str(row["timeline_entry_id"] or "").strip()
        if not entry_id:
            entry_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"erii:legacy-timeline:{agent_id}:{user_id}:{row['id']}",
                )
            )
        return TimelineEntry(
            timeline_entry_id=entry_id,
            relationship_id=relationship_id,
            agent_id=agent_id,
            user_id=user_id,
            content=str(row["content"]),
            recorded_at=None,
            legacy_timestamp=(
                str(row["timestamp"]) if row["timestamp"] else None
            ),
            provenance_state=ArtifactProvenanceState.LEGACY_UNAVAILABLE,
        )

    def get_recent_timeline_entries(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 5,
    ) -> List[TimelineEntry]:
        """Queries only the bounded chronological tail of the Timeline."""
        if limit <= 0:
            return []
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
                SELECT id, content, timestamp, timeline_entry_id, data
                FROM timeline_entries
                WHERE agent_id = ? AND user_id = ?
                ORDER BY sort_key DESC, timeline_entry_id DESC
                LIMIT ?
                """,
                (clean_agent, clean_user, limit),
            ).fetchall()
        rows.reverse()
        return [
            self._timeline_entry_from_row(
                row,
                relationship_id,
                clean_agent,
                clean_user,
            )
            for row in rows
        ]

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
                SELECT id, content, timestamp, timeline_entry_id, data
                FROM timeline_entries
                WHERE agent_id = ? AND user_id = ?
                ORDER BY sort_key, timeline_entry_id
                """,
                (clean_agent, clean_user),
            ).fetchall()
        return [
            self._timeline_entry_from_row(
                row,
                relationship_id,
                clean_agent,
                clean_user,
            )
            for row in rows
        ]

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
                        timeline_entry_id, source_archival_id, data, sort_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_agent,
                        clean_user,
                        entry.content,
                        entry.recorded_at
                        or entry.legacy_timestamp
                        or "",
                        entry.timeline_entry_id,
                        entry.source_archival_id,
                        raw,
                        timeline_timestamp_sort_key(
                            entry.recorded_at or entry.legacy_timestamp
                        ),
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
                existing = by_id.get(tombstone.archival_id)
                if existing is not None:
                    tombstone = existing.prefer_stronger_commitment(
                        tombstone
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
            existing_by_id = {item.archival_id: item for item in existing}
            for tombstone in merged:
                current = existing_by_id.get(tombstone.archival_id)
                if current is not None:
                    if current != tombstone:
                        conn.execute(
                            """
                            UPDATE archival_tombstones
                            SET data = ?, terminal_at = ?
                            WHERE archival_id = ?
                            """,
                            (
                                json.dumps(
                                    tombstone.to_dict(),
                                    ensure_ascii=False,
                                ),
                                tombstone.terminal_at,
                                tombstone.archival_id,
                            ),
                        )
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

    def capture_archival_tombstone_validation_source(
        self,
        relationship_id: str,
        archival_ids: List[str],
    ) -> ArchivalTombstoneValidationSource:
        """Captures the relevant archival ledger in one read transaction."""
        relevant_ids = tuple(sorted(set(archival_ids)))
        where_clause = "relationship_id = ?"
        parameters: List[str] = [relationship_id]
        if relevant_ids:
            placeholders = ", ".join("?" for _ in relevant_ids)
            where_clause += f" OR archival_id IN ({placeholders})"
            parameters.extend(relevant_ids)
        with closing(self._get_connection()) as conn:
            conn.execute("BEGIN")
            existing_rows = conn.execute(
                f"""
                SELECT data FROM archival_tombstones
                WHERE {where_clause}
                ORDER BY terminal_at
                """,
                parameters,
            ).fetchall()
            live_rows = conn.execute(
                f"""
                SELECT data FROM archival_records
                WHERE {where_clause}
                ORDER BY sequence
                """,
                parameters,
            ).fetchall()
            return ArchivalTombstoneValidationSource(
                relationship_id=relationship_id,
                archival_ids=relevant_ids,
                tombstones=tuple(
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
                    try:
                        preferred = existing.prefer_stronger_commitment(
                            tombstone
                        )
                    except ArchivalConflictError as exc:
                        raise ArchivalConflictError(
                            "archival tombstone conflicts with terminal receipt"
                        ) from exc
                    if preferred is not existing:
                        conn.execute(
                            """
                            UPDATE archival_tombstones
                            SET data = ?, terminal_at = ?
                            WHERE archival_id = ?
                            """,
                            (
                                json.dumps(
                                    preferred.to_dict(),
                                    ensure_ascii=False,
                                ),
                                preferred.terminal_at,
                                preferred.archival_id,
                            ),
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
            observed_at = max(now, time.time())
            params = [
                ArchivalStatus.PENDING.value,
                ArchivalStatus.RETRY_WAIT.value,
                observed_at,
                ArchivalStatus.PROCESSING.value,
                observed_at,
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
                lease_expires_at=observed_at + lease_seconds,
                attempt_id=str(uuid.uuid4()),
                recovered_expired_lease=recovered_expired_lease,
                commit_permit=(
                    CommitPermit(
                        token=uuid.uuid4().hex,
                        binding_digest=str(existing.commit_binding_digest),
                        expires_at=observed_at + permit_seconds,
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
            observed_at = max(now, time.time())
            if (
                existing.receipt.status != ArchivalStatus.PROCESSING
                or existing.attempt_id != attempt_id
                or existing.lease_token != lease_token
                or existing.lease_expires_at is None
                or existing.lease_expires_at <= observed_at
            ):
                conn.commit()
                return False
            renewed = replace(
                existing,
                lease_expires_at=observed_at + lease_seconds,
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

    @staticmethod
    def _validate_bound_archival_commit(
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
            or existing.receipt.phase != ArchivalPhase.COMMIT
            or not existing.attempt_id
            or existing.attempt_id != record.attempt_id
            or not existing.lease_token
            or existing.lease_token != record.lease_token
        ):
            raise ArchivalConflictError("archival commit authority is no longer valid")

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
            self._validate_bound_archival_commit(existing, record)
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
                        timeline_entry_id, source_archival_id, data, sort_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.agent_id,
                        entry.user_id,
                        entry.content,
                        entry.recorded_at,
                        entry.timeline_entry_id,
                        entry.source_archival_id,
                        json.dumps(entry.to_dict(), ensure_ascii=False),
                        timeline_timestamp_sort_key(entry.recorded_at),
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
            observed_at = max(now, time.time())
            if (
                row is not None
                and row["consumer_id"] != consumer_id
                and float(row["expires_at"]) > observed_at
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
                (consumer_id, observed_at + lease_seconds),
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

    def get_turn_records(
        self,
        relationship_id: str,
        turn_ids: List[str],
    ) -> List[TurnRecord]:
        """Loads only the requested relationship-scoped turns in one query."""
        wanted = tuple(dict.fromkeys(turn_ids))
        if not wanted:
            return []
        placeholders = ", ".join("?" for _item in wanted)
        with self.lock_manager.lock("__turn_records__", relationship_id):
            with closing(self._get_connection()) as conn:
                rows = conn.execute(
                    f"""
                    SELECT data FROM source_turns
                    WHERE relationship_id = ?
                      AND turn_id IN ({placeholders})
                    ORDER BY sequence
                    """,
                    (relationship_id, *wanted),
                ).fetchall()
        return [
            TurnRecord.from_dict(json.loads(row["data"])) for row in rows
        ]

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

    def transition_reviewed_turn_record(
        self,
        profile: RelationshipProfile,
        record: TurnRecord,
        context_baseline: TurnContextBaseline,
        expected_status: TurnStatus,
        expected_record_version: int,
    ) -> TurnRecord:
        """Revalidates authority and applies the Turn CAS in one transaction."""
        if (
            profile.relationship_id != record.relationship_id
            or context_baseline.relationship_id != record.relationship_id
            or record.context_baseline != context_baseline
        ):
            raise TurnTerminalConflictError(
                "reviewed Turn transition has a different context baseline"
            )
        with self.lock_manager.lock("__turn_records__", record.relationship_id):
            with closing(self._get_connection()) as conn:
                try:
                    # The write reservation serializes this authority read and
                    # Turn update with every SQLite approval/revocation writer.
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        """
                        SELECT data FROM source_turns
                        WHERE relationship_id = ? AND turn_id = ?
                        """,
                        (record.relationship_id, record.turn_id),
                    ).fetchone()
                    if row is None:
                        raise TurnNotFoundError(
                            f"turn {record.turn_id!r} was not found"
                        )
                    existing = TurnRecord.from_dict(json.loads(row["data"]))
                    if existing == record:
                        # A completed Turn remains an idempotent success even
                        # when its opening authority is revoked afterwards.
                        conn.commit()
                        return existing
                    if (
                        existing.status != expected_status
                        or existing.record_version != expected_record_version
                        or existing.context_baseline != context_baseline
                        or not record.is_terminal_transition_from(existing)
                    ):
                        raise TurnTerminalConflictError(
                            f"turn {record.turn_id!r} transition violates its "
                            "immutable opening"
                        )

                    snapshot = self._capture_turn_context_source_with_connection(
                        conn,
                        profile,
                    )
                    validate_turn_context_baseline_authority(
                        snapshot,
                        context_baseline,
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
                        raise TurnTerminalConflictError(
                            f"turn {record.turn_id!r} changed concurrently"
                        )
                    conn.commit()
                    return record
                except Exception:
                    conn.rollback()
                    raise

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

    def append_relationship_consequence(
        self,
        consequence: RelationshipConsequence,
    ) -> RelationshipConsequence:
        """Appends one immutable, source-bound relationship consequence."""
        relationship_id = consequence.relationship_id
        with self.lock_manager.lock("__relationship_history__", relationship_id):
            with closing(self._get_connection()) as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    relationship = conn.execute(
                        "SELECT 1 FROM relationships WHERE relationship_id = ?",
                        (relationship_id,),
                    ).fetchone()
                    if relationship is None:
                        raise ValueError(
                            "consequence references an unknown relationship"
                        )

                    rows = conn.execute(
                        """
                        SELECT data FROM relationship_consequences
                        WHERE consequence_id = ? OR (
                            relationship_id = ? AND source_decision_id = ?
                            AND source_event_id = ?
                        )
                        """,
                        (
                            consequence.consequence_id,
                            relationship_id,
                            consequence.source_decision_id,
                            consequence.source_event_id,
                        ),
                    ).fetchall()
                    for row in rows:
                        existing = RelationshipConsequence.from_dict(
                            _decode_json_object(
                                row["data"],
                                "Relationship Consequence data",
                            )
                        )
                        if existing.same_payload_as(consequence):
                            conn.commit()
                            return existing
                    if rows:
                        raise ConsequenceConflictError(
                            "relationship consequence identity already has "
                            "different content"
                        )

                    conn.execute(
                        """
                        INSERT INTO relationship_consequences (
                            consequence_id, relationship_id, tension_id,
                            source_decision_id, source_event_id, data, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            consequence.consequence_id,
                            relationship_id,
                            consequence.tension_id,
                            consequence.source_decision_id,
                            consequence.source_event_id,
                            json.dumps(consequence.to_dict(), ensure_ascii=False),
                            consequence.recorded_at,
                        ),
                    )
                    conn.commit()
                    return consequence
                except sqlite3.IntegrityError as exc:
                    if conn.in_transaction:
                        conn.rollback()
                    if "FOREIGN KEY" in str(exc).upper():
                        raise ValueError(
                            "consequence references an unknown relationship"
                        ) from exc
                    raise ConsequenceConflictError(
                        "relationship consequence identity already has "
                        "different content"
                    ) from exc
                except Exception:
                    if conn.in_transaction:
                        conn.rollback()
                    raise

    def list_relationship_consequences(
        self,
        relationship_id: str,
    ) -> List[RelationshipConsequence]:
        """Loads relationship consequences in durable append order."""
        with self.lock_manager.lock("__relationship_history__", relationship_id):
            with closing(self._get_connection()) as conn:
                rows = conn.execute(
                    """
                    SELECT data FROM relationship_consequences
                    WHERE relationship_id = ? ORDER BY sequence ASC
                    """,
                    (relationship_id,),
                ).fetchall()
                return [
                    RelationshipConsequence.from_dict(
                        _decode_json_object(
                            row["data"],
                            "Relationship Consequence data",
                        )
                    )
                    for row in rows
                ]

    def append_narrative_tension_link(
        self,
        link: NarrativeTensionLink,
    ) -> NarrativeTensionLink:
        """Appends one source-bound link to an existing Narrative Tension."""
        relationship_id = link.relationship_id
        with self.lock_manager.lock("__relationship_history__", relationship_id):
            with closing(self._get_connection()) as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    relationship = conn.execute(
                        "SELECT 1 FROM relationships WHERE relationship_id = ?",
                        (relationship_id,),
                    ).fetchone()
                    if relationship is None:
                        raise ValueError(
                            "Narrative Tension link references an unknown "
                            "relationship"
                        )

                    rows = conn.execute(
                        """
                        SELECT data FROM narrative_tension_links
                        WHERE link_id = ? OR (
                            tension_id = ? AND source_decision_id = ?
                            AND source_event_id = ?
                        )
                        """,
                        (
                            link.link_id,
                            link.tension_id,
                            link.source_decision_id,
                            link.source_event_id,
                        ),
                    ).fetchall()
                    for row in rows:
                        existing = NarrativeTensionLink.from_dict(
                            _decode_json_object(
                                row["data"],
                                "Narrative Tension link data",
                            )
                        )
                        if existing.same_payload_as(link):
                            conn.commit()
                            return existing
                    if rows:
                        raise NarrativeTensionConflictError(
                            "Narrative Tension link identity already has "
                            "different content"
                        )

                    consequence_row = conn.execute(
                        """
                        SELECT relationship_id, tension_id, data
                        FROM relationship_consequences
                        WHERE consequence_id = ?
                        """,
                        (link.consequence_id,),
                    ).fetchone()
                    if consequence_row is None:
                        raise NarrativeTensionConflictError(
                            "Narrative Tension link references an unknown "
                            "consequence or tension"
                        )
                    consequence = RelationshipConsequence.from_dict(
                        _decode_json_object(
                            consequence_row["data"],
                            "Relationship Consequence data",
                        )
                    )
                    if (
                        consequence_row["relationship_id"] != relationship_id
                        or consequence_row["tension_id"] != link.tension_id
                        or consequence.relationship_id != relationship_id
                        or consequence.tension_id != link.tension_id
                        or consequence.consequence_id != link.consequence_id
                    ):
                        raise NarrativeTensionConflictError(
                            "Narrative Tension link references an unknown "
                            "consequence or tension"
                        )

                    conn.execute(
                        """
                        INSERT INTO narrative_tension_links (
                            link_id, relationship_id, tension_id, consequence_id,
                            source_decision_id, source_event_id, data, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            link.link_id,
                            relationship_id,
                            link.tension_id,
                            link.consequence_id,
                            link.source_decision_id,
                            link.source_event_id,
                            json.dumps(link.to_dict(), ensure_ascii=False),
                            link.recorded_at,
                        ),
                    )
                    conn.commit()
                    return link
                except sqlite3.IntegrityError as exc:
                    if conn.in_transaction:
                        conn.rollback()
                    raise NarrativeTensionConflictError(
                        "Narrative Tension link identity or consequence "
                        "constraint failed"
                    ) from exc
                except Exception:
                    if conn.in_transaction:
                        conn.rollback()
                    raise

    def list_narrative_tension_links(
        self,
        relationship_id: str,
    ) -> List[NarrativeTensionLink]:
        """Loads Narrative Tension links in durable append order."""
        with self.lock_manager.lock("__relationship_history__", relationship_id):
            with closing(self._get_connection()) as conn:
                rows = conn.execute(
                    """
                    SELECT data FROM narrative_tension_links
                    WHERE relationship_id = ? ORDER BY sequence ASC
                    """,
                    (relationship_id,),
                ).fetchall()
                return [
                    NarrativeTensionLink.from_dict(
                        _decode_json_object(
                            row["data"],
                            "Narrative Tension link data",
                        )
                    )
                    for row in rows
                ]

    @staticmethod
    def _require_completed_processing_turn(
        conn: sqlite3.Connection,
        relationship_id: str,
        source_turn_id: str,
        source_revision: str,
    ) -> TurnRecord:
        row = conn.execute(
            """
            SELECT data FROM source_turns
            WHERE relationship_id = ? AND turn_id = ?
            """,
            (relationship_id, source_turn_id),
        ).fetchone()
        if row is not None:
            record = TurnRecord.from_dict(json.loads(row["data"]))
            if (
                record.status == TurnStatus.COMPLETED
                and record.source_revision == source_revision
            ):
                return record
        raise RelationshipProcessingConflictError(
            "relationship processing requires the exact completed Source Turn"
        )

    def create_relationship_processing_run(
        self,
        run: RelationshipProcessingRun,
    ) -> RelationshipProcessingRun:
        """Freezes one relationship processing identity exactly once."""
        with self.lock_manager.lock("__relationship_history__", run.relationship_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                relationship = conn.execute(
                    "SELECT 1 FROM relationships WHERE relationship_id = ?",
                    (run.relationship_id,),
                ).fetchone()
                if relationship is None:
                    conn.rollback()
                    raise ValueError(
                        "relationship processing references an unknown relationship"
                    )
                self._require_completed_processing_turn(
                    conn,
                    run.relationship_id,
                    run.source_turn_id,
                    run.source_revision,
                )
                row = conn.execute(
                    """
                    SELECT data FROM relationship_processing_runs
                    WHERE processing_id = ? OR (
                        relationship_id = ? AND source_turn_id = ?
                        AND source_revision = ? AND processing_identity = ?
                    )
                    """,
                    (
                        run.processing_id,
                        run.relationship_id,
                        run.source_turn_id,
                        run.source_revision,
                        run.processing_identity,
                    ),
                ).fetchone()
                if row is not None:
                    existing = RelationshipProcessingRun.from_dict(
                        json.loads(row["data"])
                    )
                    conn.commit()
                    if existing.same_frozen_input_as(run):
                        return existing
                    raise RelationshipProcessingConflictError(
                        "relationship processing identity has different frozen input"
                    )
                conn.execute(
                    """
                    INSERT INTO relationship_processing_runs (
                        processing_id, relationship_id, source_turn_id,
                        source_revision, processing_identity, record_version,
                        status, data, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.processing_id,
                        run.relationship_id,
                        run.source_turn_id,
                        run.source_revision,
                        run.processing_identity,
                        run.record_version,
                        run.status.value,
                        json.dumps(run.to_dict(), ensure_ascii=False),
                        run.created_at,
                        run.updated_at,
                    ),
                )
                conn.commit()
                return run

    def get_relationship_processing_run(
        self,
        relationship_id: str,
        processing_id: str,
    ) -> Optional[RelationshipProcessingRun]:
        with self.lock_manager.lock("__relationship_history__", relationship_id):
            with closing(self._get_connection()) as conn:
                row = conn.execute(
                    """
                    SELECT data FROM relationship_processing_runs
                    WHERE relationship_id = ? AND processing_id = ?
                    """,
                    (relationship_id, processing_id),
                ).fetchone()
                return (
                    RelationshipProcessingRun.from_dict(json.loads(row["data"]))
                    if row is not None
                    else None
                )

    def list_relationship_processing_runs(
        self,
        relationship_id: str,
    ) -> List[RelationshipProcessingRun]:
        with self.lock_manager.lock("__relationship_history__", relationship_id):
            with closing(self._get_connection()) as conn:
                rows = conn.execute(
                    """
                    SELECT data FROM relationship_processing_runs
                    WHERE relationship_id = ? ORDER BY sequence ASC
                    """,
                    (relationship_id,),
                ).fetchall()
                return [
                    RelationshipProcessingRun.from_dict(json.loads(row["data"]))
                    for row in rows
                ]

    def update_relationship_processing_run(
        self,
        run: RelationshipProcessingRun,
        expected_record_version: int,
    ) -> RelationshipProcessingRun:
        """CAS-updates progress without permitting frozen-input drift."""
        with self.lock_manager.lock("__relationship_history__", run.relationship_id):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT data FROM relationship_processing_runs
                    WHERE relationship_id = ? AND processing_id = ?
                    """,
                    (run.relationship_id, run.processing_id),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    raise RelationshipProcessingConflictError(
                        "relationship processing run does not exist"
                    )
                existing = RelationshipProcessingRun.from_dict(
                    json.loads(row["data"])
                )
                if existing == run:
                    conn.commit()
                    return existing
                if (
                    existing.record_version != expected_record_version
                    or run.record_version != expected_record_version + 1
                    or not existing.same_frozen_input_as(run)
                ):
                    conn.rollback()
                    raise RelationshipProcessingConflictError(
                        "relationship processing run changed concurrently"
                    )
                cursor = conn.execute(
                    """
                    UPDATE relationship_processing_runs
                    SET record_version = ?, status = ?, data = ?, updated_at = ?
                    WHERE processing_id = ? AND relationship_id = ?
                      AND record_version = ?
                    """,
                    (
                        run.record_version,
                        run.status.value,
                        json.dumps(run.to_dict(), ensure_ascii=False),
                        run.updated_at,
                        run.processing_id,
                        run.relationship_id,
                        expected_record_version,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    raise RelationshipProcessingConflictError(
                        "relationship processing run changed concurrently"
                    )
                conn.commit()
                return run

    @staticmethod
    def _reflection_history(
        conn: sqlite3.Connection,
        relationship_id: str,
    ):
        direct_rows = conn.execute(
            """
            SELECT data FROM relationship_events
            WHERE relationship_id = ? ORDER BY sequence ASC
            """,
            (relationship_id,),
        ).fetchall()
        adjudication_rows = conn.execute(
            """
            SELECT data FROM relationship_adjudications
            WHERE relationship_id = ? ORDER BY sequence ASC
            """,
            (relationship_id,),
        ).fetchall()
        events = {
            event.event_id: event
            for event in (
                RelationshipEvent.from_dict(json.loads(row["data"]))
                for row in direct_rows
            )
        }
        adjudications = [
            AdjudicationRecord.from_dict(json.loads(row["data"]))
            for row in adjudication_rows
        ]
        for record in adjudications:
            for event in record.events:
                events[event.event_id] = event
        return events, adjudications

    def _validate_reflection_decision(
        self,
        conn: sqlite3.Connection,
        decision: PersonaReflectionDecisionRecord,
    ) -> None:
        events, adjudications = self._reflection_history(
            conn,
            decision.relationship_id,
        )
        if decision.event_id not in events:
            raise RelationshipProcessingConflictError(
                "reflection references an unknown relationship event"
            )
        provenance = decision.context_provenance
        if provenance.relationship_event_id != decision.event_id:
            raise RelationshipProcessingConflictError(
                "reflection provenance references another event"
            )
        if decision.target_reflection_id is not None:
            target = conn.execute(
                """
                SELECT 1 FROM persona_reflection_records
                WHERE relationship_id = ? AND reflection_id = ?
                """,
                (decision.relationship_id, decision.target_reflection_id),
            ).fetchone()
            if target is None:
                raise RelationshipProcessingConflictError(
                    "reflection target does not exist in this relationship"
                )
        if provenance.prior_reflection_ids:
            placeholders = ",".join("?" for _ in provenance.prior_reflection_ids)
            rows = conn.execute(
                f"""
                SELECT reflection_id FROM persona_reflection_records
                WHERE relationship_id = ? AND reflection_id IN ({placeholders})
                """,
                (decision.relationship_id, *provenance.prior_reflection_ids),
            ).fetchall()
            if {row["reflection_id"] for row in rows} != set(
                provenance.prior_reflection_ids
            ):
                raise RelationshipProcessingConflictError(
                    "reflection provenance references unknown prior reflections"
                )
        if provenance.provenance_state != ReflectionProvenanceState.COMPLETE:
            return
        if (
            decision.source_turn_id != provenance.source_turn_id
            or decision.source_revision != provenance.source_revision
        ):
            raise RelationshipProcessingConflictError(
                "reflection decision and provenance source do not match"
            )
        self._require_completed_processing_turn(
            conn,
            decision.relationship_id,
            decision.source_turn_id,
            decision.source_revision,
        )
        matching = [
            record
            for record in adjudications
            if any(event.event_id == decision.event_id for event in record.events)
        ]
        if len(matching) != 1:
            raise RelationshipProcessingConflictError(
                "complete reflection provenance requires one accepted decision"
            )
        receipt = matching[0].receipt
        evidence_ids = {item.evidence_id for item in receipt.evidence}
        if (
            provenance.decision_id != receipt.decision_id
            or receipt.source_turn_id != decision.source_turn_id
            or receipt.source_revision != decision.source_revision
            or not provenance.evidence_ids
            or not set(provenance.evidence_ids).issubset(evidence_ids)
        ):
            raise RelationshipProcessingConflictError(
                "reflection provenance is not bound to its decision evidence"
            )

    def commit_persona_reflection_decision(
        self,
        decision: PersonaReflectionDecisionRecord,
    ) -> PersonaReflectionDecisionRecord:
        """Atomically stores a decision and its optional formal record."""
        with self.lock_manager.lock(
            "__relationship_history__",
            decision.relationship_id,
        ):
            with closing(self._get_connection()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT data FROM persona_reflection_decisions
                    WHERE decision_id = ? OR (
                        relationship_id = ? AND interpretation_identity = ?
                    )
                    """,
                    (
                        decision.decision_id,
                        decision.relationship_id,
                        decision.interpretation_identity,
                    ),
                ).fetchone()
                if row is not None:
                    existing = PersonaReflectionDecisionRecord.from_dict(
                        json.loads(row["data"])
                    )
                    conn.commit()
                    if existing.same_payload_as(decision):
                        return existing
                    raise RelationshipProcessingConflictError(
                        "reflection interpretation identity has different content"
                    )
                self._validate_reflection_decision(conn, decision)
                record = decision.reflection_record
                if record is not None:
                    existing_row = conn.execute(
                        """
                        SELECT data FROM persona_reflection_records
                        WHERE reflection_id = ?
                        """,
                        (record.reflection_id,),
                    ).fetchone()
                    if existing_row is not None:
                        existing_record = PersonaReflectionRecord.from_dict(
                            json.loads(existing_row["data"])
                        )
                        if not existing_record.same_payload_as(record):
                            conn.rollback()
                            raise RelationshipProcessingConflictError(
                                "reflection ID has different content"
                            )
                    else:
                        conn.execute(
                            """
                            INSERT INTO persona_reflection_records (
                                reflection_id, relationship_id, event_id,
                                target_reflection_id, data, recorded_at
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                record.reflection_id,
                                record.relationship_id,
                                record.event_id,
                                record.target_reflection_id or "",
                                json.dumps(record.to_dict(), ensure_ascii=False),
                                record.recorded_at,
                            ),
                        )
                conn.execute(
                    """
                    INSERT INTO persona_reflection_decisions (
                        decision_id, relationship_id, event_id,
                        interpretation_identity, data, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision.decision_id,
                        decision.relationship_id,
                        decision.event_id,
                        decision.interpretation_identity,
                        json.dumps(decision.to_dict(), ensure_ascii=False),
                        decision.recorded_at,
                    ),
                )
                conn.commit()
                return decision

    def get_persona_reflection_decision(
        self,
        relationship_id: str,
        decision_id: str,
    ) -> Optional[PersonaReflectionDecisionRecord]:
        with closing(self._get_connection()) as conn:
            row = conn.execute(
                """
                SELECT data FROM persona_reflection_decisions
                WHERE relationship_id = ? AND decision_id = ?
                """,
                (relationship_id, decision_id),
            ).fetchone()
            return (
                PersonaReflectionDecisionRecord.from_dict(json.loads(row["data"]))
                if row is not None
                else None
            )

    def list_persona_reflection_decisions(
        self,
        relationship_id: str,
    ) -> List[PersonaReflectionDecisionRecord]:
        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT data FROM persona_reflection_decisions
                WHERE relationship_id = ? ORDER BY sequence ASC
                """,
                (relationship_id,),
            ).fetchall()
            return [
                PersonaReflectionDecisionRecord.from_dict(json.loads(row["data"]))
                for row in rows
            ]

    def get_persona_reflection_record(
        self,
        relationship_id: str,
        reflection_id: str,
    ) -> Optional[PersonaReflectionRecord]:
        with closing(self._get_connection()) as conn:
            row = conn.execute(
                """
                SELECT data FROM persona_reflection_records
                WHERE relationship_id = ? AND reflection_id = ?
                """,
                (relationship_id, reflection_id),
            ).fetchone()
            return (
                PersonaReflectionRecord.from_dict(json.loads(row["data"]))
                if row is not None
                else None
            )

    def list_persona_reflection_records(
        self,
        relationship_id: str,
    ) -> List[PersonaReflectionRecord]:
        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT data FROM persona_reflection_records
                WHERE relationship_id = ? ORDER BY sequence ASC
                """,
                (relationship_id,),
            ).fetchall()
            return [
                PersonaReflectionRecord.from_dict(json.loads(row["data"]))
                for row in rows
            ]

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

    def get_persona_growth_proposal(
        self,
        proposal_id: str,
    ) -> Optional[PersonaGrowthProposal]:
        """Loads one globally stable persona-growth identity."""
        with closing(self._get_connection()) as conn:
            row = conn.execute(
                "SELECT data FROM persona_growth_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        return (
            PersonaGrowthProposal.from_dict(json.loads(row["data"]))
            if row is not None
            else None
        )

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
