"""Reliable, host-controlled archival orchestration for canonical Source Turns."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import time
from typing import List, Optional, Union
import uuid

from erii._version import __version__
from erii.core.archival_evidence import ArchivalEvidenceResolver
from erii.models.archival import (
    ArchivalArtifactsDecision,
    ArchivalCapabilityError,
    ArchivalConflictError,
    ArchivalDrainReport,
    ArchivalNoMemoryDecision,
    ArchivalNotFoundError,
    ArchivalOutcomeCode,
    ArchivalPhase,
    ArchivalProcessingError,
    ArchivalReceipt,
    ArchivalRecord,
    ArchivalStatus,
    ArchivalSubmissionError,
    ArchivalTombstone,
    CommitPermit,
    MemoryExtractionRequest,
    MemoryExtractorV1,
    PermanentArchivalError,
    PreparedArchivalBatch,
    RetryableArchivalError,
    ShutdownReport,
    TimelineEntry,
    archival_decision_from_value,
)
from erii.models.node import MemoryNode
from erii.models.provenance import ArtifactProvenanceState, ExtractorDescriptor
from erii.models.relationship import RelationshipProfile, utc_now
from erii.models.turn import TurnRecord, TurnStatus
from erii.security.sanitizer import SecuritySanitizer
from erii.storage.archival import AtomicArchivalStoreV1
from erii.storage.base import BaseStorage


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ArchivalCoordinator:
    """Deep module owning acceptance, extraction, retry, and atomic publication."""

    def __init__(
        self,
        *,
        storage: BaseStorage,
        memory_extractor: Optional[MemoryExtractorV1],
        enable_sanitizer: bool,
        enable_pii_scrubbing: bool,
        max_attempts: int,
        base_delay_seconds: float,
        lease_seconds: float,
        commit_permit_seconds: float,
        consumer_lease_seconds: float,
        max_memory_candidates: int,
        receipt_retention_days: int,
    ) -> None:
        self.storage = storage
        self.store: Optional[AtomicArchivalStoreV1] = (
            storage.atomic_archival_store_v1()
        )
        self.memory_extractor = memory_extractor
        self.enable_sanitizer = enable_sanitizer
        self.enable_pii_scrubbing = enable_pii_scrubbing
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self.lease_seconds = lease_seconds
        self.commit_permit_seconds = commit_permit_seconds
        self.consumer_lease_seconds = consumer_lease_seconds
        self.max_memory_candidates = max_memory_candidates
        self.receipt_retention_days = receipt_retention_days
        self.consumer_id = str(uuid.uuid4())
        self._accepting = True
        self._in_flight_archival_id: Optional[str] = None
        self._processing_active = False
        self._state_condition = threading.Condition()
        self._processing_lock = threading.Lock()
        self._host_descriptor = self._validate_extractor(memory_extractor)
        self._evidence_resolver = ArchivalEvidenceResolver()

    @property
    def available(self) -> bool:
        """Whether both host extraction and atomic storage are configured."""
        return (
            self.memory_extractor is not None
            and self._host_descriptor is not None
            and self.store is not None
        )

    @property
    def query_available(self) -> bool:
        """Whether durable archival receipts can be queried."""
        return self.store is not None

    def ensure_available(self) -> None:
        """Raises the public capability error before any scope lookup."""
        self._require_capability()

    @staticmethod
    def _validate_extractor(
        extractor: Optional[MemoryExtractorV1],
    ) -> Optional[ExtractorDescriptor]:
        if extractor is None:
            return None
        descriptor = getattr(extractor, "descriptor", None)
        if not isinstance(descriptor, ExtractorDescriptor):
            raise ArchivalCapabilityError(
                "memory_extractor must expose an ExtractorDescriptor"
            )
        if descriptor.erii_version is not None or descriptor.processed_at is not None:
            raise ArchivalCapabilityError(
                "host extractor descriptor cannot assign kernel processing metadata"
            )
        if not callable(getattr(extractor, "extract", None)):
            raise ArchivalCapabilityError(
                "memory_extractor must implement extract(request)"
            )
        return descriptor

    def _require_capability(self) -> AtomicArchivalStoreV1:
        if self.memory_extractor is None or self._host_descriptor is None:
            raise ArchivalCapabilityError(
                "archival_capability_unavailable: configure MemoryExtractorV1"
            )
        if self.store is None:
            raise ArchivalCapabilityError(
                "archival_capability_unavailable: storage lacks AtomicArchivalStoreV1"
            )
        return self.store

    def _require_store(self) -> AtomicArchivalStoreV1:
        if self.store is None:
            raise ArchivalCapabilityError(
                "archival_capability_unavailable: storage lacks AtomicArchivalStoreV1"
            )
        return self.store

    def submit(
        self,
        profile: RelationshipProfile,
        source_turn: TurnRecord,
        *,
        idempotency_key: str,
        process_inline: bool,
    ) -> Union[ArchivalReceipt, ArchivalTombstone]:
        """Accepts one canonical Source Turn and optionally processes it inline."""
        store = self._require_capability()
        descriptor = self._host_descriptor
        if (
            descriptor is None
            or descriptor.extraction_schema_version != "2"
        ):
            raise ArchivalSubmissionError(
                "extractor_schema_upgrade_required: archival requires extraction schema 2"
            )
        with self._state_condition:
            if not self._accepting:
                raise ArchivalCapabilityError(
                    "engine is closing and rejects new archivals"
                )
        if source_turn.status != TurnStatus.COMPLETED:
            raise ArchivalSubmissionError(
                "invalid_source_turn: archival requires a completed Source Turn"
            )
        if source_turn.relationship_id != profile.relationship_id:
            raise ArchivalSubmissionError(
                "invalid_source_turn: Source Turn belongs to another relationship"
            )
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ArchivalSubmissionError("idempotency_key must be a non-empty string")
        if len(idempotency_key.strip()) > 256:
            raise ArchivalSubmissionError("idempotency_key exceeds 256 characters")
        self.compact_expired()

        request_payload = {
            "relationship_id": profile.relationship_id,
            "source_turn_id": source_turn.turn_id,
            "source_revision": source_turn.source_revision,
            "extractor": descriptor.to_dict(),
            "processing_mode": "canonical_memory_archival",
        }
        request_fingerprint = _fingerprint(request_payload)
        idempotency_fingerprint = _fingerprint(
            {
                "relationship_id": profile.relationship_id,
                "idempotency_key": idempotency_key.strip(),
            }
        )
        now_text = utc_now()
        receipt = ArchivalReceipt(
            archival_id=str(uuid.uuid4()),
            relationship_id=profile.relationship_id,
            agent_id=profile.agent_id,
            user_id=profile.user_id,
            source_turn_id=source_turn.turn_id,
            source_revision=source_turn.source_revision,
            status=ArchivalStatus.PENDING,
            phase=ArchivalPhase.EXTRACTION,
            extractor_descriptor=descriptor,
            submitted_at=now_text,
            updated_at=now_text,
        )
        with self._state_condition:
            if not self._accepting:
                raise ArchivalCapabilityError(
                    "engine is closing and rejects new archivals"
                )
            stored = store.create_archival_record(
                ArchivalRecord(
                    receipt=receipt,
                    idempotency_fingerprint=idempotency_fingerprint,
                    request_fingerprint=request_fingerprint,
                )
            )
        if isinstance(stored, ArchivalTombstone):
            return stored
        if (
            process_inline
            and stored.receipt.status == ArchivalStatus.FAILED
        ):
            raise ArchivalProcessingError(stored.receipt)
        if not process_inline or stored.receipt.status == ArchivalStatus.COMPLETED:
            return stored.receipt
        self.process_pending(max_tasks=1, archival_id=stored.receipt.archival_id)
        current = store.get_archival_record(
            profile.relationship_id,
            stored.receipt.archival_id,
        ).receipt
        if current.status != ArchivalStatus.COMPLETED:
            raise ArchivalProcessingError(current)
        return current

    def get(
        self,
        relationship_id: str,
        archival_id: str,
    ) -> Union[ArchivalReceipt, ArchivalTombstone]:
        store = self._require_store()
        self.compact_expired()
        try:
            return store.get_archival_record(
                relationship_id,
                archival_id,
            ).receipt
        except ArchivalNotFoundError:
            for tombstone in self.storage.list_archival_tombstones(
                relationship_id
            ):
                if tombstone.archival_id == archival_id:
                    return tombstone
            raise

    def list(
        self,
        relationship_id: str,
    ) -> List[Union[ArchivalReceipt, ArchivalTombstone]]:
        store = self._require_store()
        self.compact_expired()
        receipts = [
            record.receipt
            for record in store.list_archival_records(relationship_id)
        ]
        known = {item.archival_id for item in receipts}
        receipts.extend(
            tombstone
            for tombstone in self.storage.list_archival_tombstones(
                relationship_id
            )
            if tombstone.archival_id not in known
        )
        return receipts

    def compact_expired(self) -> int:
        """Compacts full terminal receipts after the configured retention window."""
        store = self._require_store()
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self.receipt_retention_days
        )
        return store.compact_archival_records(before=cutoff.isoformat())

    def process_pending(
        self,
        max_tasks: Optional[int] = None,
        *,
        archival_id: Optional[str] = None,
    ) -> int:
        """Processes claimed attempts synchronously under one consumer lease."""
        store = self._require_capability()
        if max_tasks is not None and max_tasks < 0:
            raise ValueError("max_tasks cannot be negative")
        if not self._processing_lock.acquire(blocking=False):
            return 0
        with self._state_condition:
            self._processing_active = True
        try:
            now = time.time()
            consumer_lease_seconds = max(
                self.consumer_lease_seconds,
                self.lease_seconds,
            )
            if not store.acquire_archival_consumer(
                self.consumer_id,
                now=now,
                lease_seconds=consumer_lease_seconds,
            ):
                return 0
            processed = 0
            try:
                while max_tasks is None or processed < max_tasks:
                    with self._state_condition:
                        if not self._accepting:
                            break
                        consumer_lease_started_at = time.monotonic()
                        if not store.acquire_archival_consumer(
                            self.consumer_id,
                            now=time.time(),
                            lease_seconds=consumer_lease_seconds,
                        ):
                            break
                        claimed = store.claim_next_archival_record(
                            now=time.time(),
                            lease_seconds=self.lease_seconds,
                            permit_seconds=self.commit_permit_seconds,
                            archival_id=archival_id,
                        )
                        if claimed is not None:
                            self._in_flight_archival_id = (
                                claimed.receipt.archival_id
                            )
                    if claimed is None:
                        break
                    try:
                        self._process_claimed(
                            claimed,
                            consumer_lease_started_at=consumer_lease_started_at,
                        )
                    finally:
                        with self._state_condition:
                            self._in_flight_archival_id = None
                            self._state_condition.notify_all()
                    processed += 1
                    if archival_id is not None:
                        break
                return processed
            finally:
                with self._state_condition:
                    self._in_flight_archival_id = None
                    self._state_condition.notify_all()
                store.release_archival_consumer(self.consumer_id)
        finally:
            with self._state_condition:
                self._processing_active = False
                self._state_condition.notify_all()
            self._processing_lock.release()

    def _process_claimed(
        self,
        record: ArchivalRecord,
        *,
        consumer_lease_started_at: float,
    ) -> ArchivalReceipt:
        current = record
        try:
            if (
                current.receipt.phase == ArchivalPhase.EXTRACTION
                and current.receipt.extractor_descriptor.extraction_schema_version
                != "2"
            ):
                return self._record_failure(
                    current,
                    outcome=(
                        ArchivalOutcomeCode.EXTRACTOR_SCHEMA_UPGRADE_REQUIRED
                    ),
                    retryable=False,
                    safe_summary=(
                        "the pending archival requires extraction schema 2"
                    ),
                )
            if current.recovered_expired_lease:
                return self._record_failure(
                    current,
                    outcome=ArchivalOutcomeCode.PROCESSING_LEASE_EXPIRED,
                    retryable=True,
                    safe_summary="the previous archival processing lease expired",
                )
            if current.receipt.phase == ArchivalPhase.EXTRACTION:
                current = self._extract_and_bind(
                    current,
                    consumer_lease_started_at=consumer_lease_started_at,
                )
            return self._commit(current)
        except ArchivalConflictError:
            return self._latest_receipt(current)
        except PermanentArchivalError:
            return self._record_failure(
                current,
                outcome=ArchivalOutcomeCode.PERMANENT_FAILURE,
                retryable=False,
                safe_summary="memory extractor reported a permanent capability failure",
            )
        except RetryableArchivalError:
            return self._record_failure(
                current,
                outcome=ArchivalOutcomeCode.EXTRACTOR_TEMPORARY_FAILURE,
                retryable=True,
                safe_summary="memory extractor is temporarily unavailable",
            )
        except (ValueError, TypeError):
            return self._record_failure(
                current,
                outcome=ArchivalOutcomeCode.INVALID_EXTRACTOR_OUTPUT,
                retryable=True,
                safe_summary="memory extractor returned an invalid decision",
            )
        except Exception:
            outcome = (
                ArchivalOutcomeCode.COMMIT_TEMPORARY_FAILURE
                if current.receipt.phase == ArchivalPhase.COMMIT
                else ArchivalOutcomeCode.EXTRACTOR_TEMPORARY_FAILURE
            )
            summary = (
                "atomic archival commit is temporarily unavailable"
                if current.receipt.phase == ArchivalPhase.COMMIT
                else "memory extractor is temporarily unavailable"
            )
            return self._record_failure(
                current,
                outcome=outcome,
                retryable=True,
                safe_summary=summary,
            )

    def _extract_and_bind(
        self,
        record: ArchivalRecord,
        *,
        consumer_lease_started_at: float,
    ) -> ArchivalRecord:
        store = self._require_capability()
        turn = self.storage.get_turn_record(
            record.receipt.relationship_id,
            record.receipt.source_turn_id,
        )
        if (
            turn.status != TurnStatus.COMPLETED
            or turn.source_revision != record.receipt.source_revision
        ):
            raise PermanentArchivalError("canonical Source Turn changed or disappeared")
        request = MemoryExtractionRequest(
            source_turn_id=turn.turn_id,
            source_revision=turn.source_revision,
            relationship_id=turn.relationship_id,
            agent_id=record.receipt.agent_id,
            user_id=record.receipt.user_id,
            transcript=turn.transcript,
            interaction_context=turn.interaction_context,
        )
        raw_decision, record = self._extract_with_heartbeat(
            record,
            request,
            consumer_lease_started_at=consumer_lease_started_at,
        )
        host_descriptor = self._host_descriptor
        if host_descriptor is None:
            raise ArchivalCapabilityError(
                "archival_capability_unavailable: configure MemoryExtractorV1"
            )
        decision = archival_decision_from_value(
            raw_decision,
            extraction_schema_version=(
                host_descriptor.extraction_schema_version
            ),
        )
        if (
            isinstance(decision, ArchivalArtifactsDecision)
            and len(decision.memories) > self.max_memory_candidates
        ):
            raise ValueError("configured Memory Candidate limit exceeded")

        timeline_evidence = ()
        memory_evidence = ()
        if isinstance(decision, ArchivalArtifactsDecision):
            timeline_evidence = tuple(
                self._evidence_resolver.resolve(turn, candidate.evidence)
                for candidate in decision.timeline
            )
            memory_evidence = tuple(
                self._evidence_resolver.resolve(turn, candidate.evidence)
                for candidate in decision.memories
            )

        processed_at = utc_now()
        descriptor = host_descriptor.for_processing(
            erii_version=__version__,
            processed_at=processed_at,
        )
        namespace = uuid.UUID(record.receipt.archival_id)
        timeline = ()
        memories = ()
        outcome = ArchivalOutcomeCode.NO_MEMORY
        if isinstance(decision, ArchivalArtifactsDecision):
            timeline = tuple(
                TimelineEntry(
                    timeline_entry_id=str(
                        uuid.uuid5(namespace, f"timeline:{index}")
                    ),
                    relationship_id=record.receipt.relationship_id,
                    agent_id=record.receipt.agent_id,
                    user_id=record.receipt.user_id,
                    content=self._sanitize(candidate.content),
                    recorded_at=processed_at,
                    source_turn_id=record.receipt.source_turn_id,
                    source_archival_id=record.receipt.archival_id,
                    provenance_state=ArtifactProvenanceState.COMPLETE,
                    extractor_descriptor=descriptor,
                    evidence_references=timeline_evidence[index],
                )
                for index, candidate in enumerate(decision.timeline)
            )
            memories = tuple(
                MemoryNode(
                    node_id=str(uuid.uuid5(namespace, f"memory:{index}")),
                    relationship_id=record.receipt.relationship_id,
                    agent_id=record.receipt.agent_id,
                    user_id=record.receipt.user_id,
                    node_type=candidate.node_type,
                    content=self._sanitize(candidate.content),
                    tags=list(candidate.tags),
                    base_importance=candidate.base_importance,
                    emotional_score=candidate.emotional_score,
                    confidence=candidate.confidence,
                    decayable=candidate.decayable,
                    visibility=candidate.visibility,
                    is_unresolved=candidate.is_unresolved,
                    foreshadowing_tags=list(candidate.foreshadowing_tags),
                    source_turn_id=record.receipt.source_turn_id,
                    source_archival_id=record.receipt.archival_id,
                    provenance_state=ArtifactProvenanceState.COMPLETE,
                    extractor_descriptor=descriptor,
                    evidence_references=memory_evidence[index],
                    created_at=processed_at,
                    last_accessed_at=datetime.fromisoformat(processed_at).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                )
                for index, candidate in enumerate(decision.memories)
            )
            outcome = ArchivalOutcomeCode.ARTIFACTS_COMMITTED
        elif not isinstance(decision, ArchivalNoMemoryDecision):
            raise ValueError("unsupported extraction decision")

        batch = PreparedArchivalBatch(
            archival_id=record.receipt.archival_id,
            relationship_id=record.receipt.relationship_id,
            source_turn_id=record.receipt.source_turn_id,
            source_revision=record.receipt.source_revision,
            descriptor=descriptor,
            timeline=timeline,
            memories=memories,
        )
        now = time.time()
        bound = replace(
            record,
            receipt=replace(
                record.receipt,
                phase=ArchivalPhase.COMMIT,
                extractor_descriptor=descriptor,
                commit_attempts=record.receipt.commit_attempts + 1,
                updated_at=processed_at,
            ),
            record_version=record.record_version + 1,
            prepared_batch=batch,
            prepared_outcome_code=outcome,
            commit_binding_digest=batch.batch_digest,
            commit_permit=CommitPermit(
                token=uuid.uuid4().hex,
                binding_digest=batch.batch_digest,
                expires_at=now + self.commit_permit_seconds,
            ),
        )
        return store.bind_prepared_archival_batch(bound, batch)

    def _extract_with_heartbeat(
        self,
        record: ArchivalRecord,
        request: MemoryExtractionRequest,
        *,
        consumer_lease_started_at: float,
    ):
        """Runs one explicit extraction while renewing its fenced leases."""
        store = self._require_capability()
        if record.attempt_id is None or record.lease_token is None:
            raise ArchivalConflictError("archival attempt has no lease identity")
        consumer_lease_seconds = max(
            self.consumer_lease_seconds,
            self.lease_seconds,
        )

        processing_heartbeat_interval = max(
            0.0001,
            min(1.0, self.lease_seconds / 3.0),
        )
        consumer_heartbeat_interval = max(
            0.0001,
            min(1.0, consumer_lease_seconds / 3.0),
        )
        next_consumer_renewal = (
            consumer_lease_started_at + consumer_heartbeat_interval
        )

        def renew_processing_lease():
            renewed = store.renew_archival_lease(
                relationship_id=record.receipt.relationship_id,
                archival_id=record.receipt.archival_id,
                attempt_id=record.attempt_id,
                lease_token=record.lease_token,
                now=time.time(),
                lease_seconds=self.lease_seconds,
            )
            # The storage call can include a durable file replacement and fsync.
            # Schedule the next heartbeat from the successful completion time,
            # rather than from its start, so that a slow renewal does not cause
            # an immediate follow-up against an already-expired lease window.
            return time.monotonic() if renewed else None

        def renew_leases(*, consumer_due: bool):
            nonlocal next_consumer_renewal
            processing_started_at = renew_processing_lease()
            if processing_started_at is None:
                return None
            if not consumer_due:
                return processing_started_at
            if not store.acquire_archival_consumer(
                self.consumer_id,
                now=time.time(),
                lease_seconds=consumer_lease_seconds,
            ):
                return None
            next_consumer_renewal = (
                time.monotonic() + consumer_heartbeat_interval
            )
            return renew_processing_lease()

        processing_started_at = renew_leases(
            consumer_due=time.monotonic() >= next_consumer_renewal,
        )
        if processing_started_at is None:
            raise ArchivalConflictError("archival processing lease expired")
        next_processing_renewal = (
            processing_started_at + processing_heartbeat_interval
        )
        stopped = threading.Event()
        lease_lost = threading.Event()

        def heartbeat() -> None:
            processing_deadline = next_processing_renewal
            while True:
                deadline = min(processing_deadline, next_consumer_renewal)
                if stopped.wait(max(0.0, deadline - time.monotonic())):
                    return
                try:
                    processing_started = renew_leases(
                        consumer_due=time.monotonic() >= next_consumer_renewal,
                    )
                except Exception:
                    processing_started = None
                if processing_started is None:
                    lease_lost.set()
                    return
                processing_deadline = (
                    processing_started + processing_heartbeat_interval
                )

        heartbeat_thread = threading.Thread(
            target=heartbeat,
            name=f"erii-archival-heartbeat-{record.receipt.archival_id}",
            daemon=False,
        )
        heartbeat_thread.start()
        try:
            raw_decision = self.memory_extractor.extract(request)
        finally:
            stopped.set()
            heartbeat_thread.join()
        if lease_lost.is_set():
            raise ArchivalConflictError("archival processing lease expired")
        processing_started_at = renew_leases(
            consumer_due=time.monotonic() >= next_consumer_renewal,
        )
        if processing_started_at is None:
            raise ArchivalConflictError("archival processing lease expired")
        latest = store.get_archival_record(
            record.receipt.relationship_id,
            record.receipt.archival_id,
        )
        if (
            latest.attempt_id != record.attempt_id
            or latest.lease_token != record.lease_token
        ):
            raise ArchivalConflictError("archival attempt lost its lease")
        return raw_decision, latest

    def _commit(self, record: ArchivalRecord) -> ArchivalReceipt:
        store = self._require_capability()
        if (
            record.receipt.phase != ArchivalPhase.COMMIT
            or record.prepared_batch is None
            or record.prepared_outcome_code is None
            or record.commit_permit is None
        ):
            raise ArchivalConflictError("archival has no prepared commit binding")
        completed_at = utc_now()
        completed = replace(
            record,
            receipt=replace(
                record.receipt,
                status=ArchivalStatus.COMPLETED,
                outcome_code=record.prepared_outcome_code,
                retryable=False,
                safe_summary=None,
                next_attempt_at=None,
                completed_at=completed_at,
                updated_at=completed_at,
                artifact_manifest=record.prepared_batch.manifest,
            ),
            record_version=record.record_version + 1,
            prepared_batch=None,
        )
        return store.commit_archival_batch(completed).receipt

    def _record_failure(
        self,
        record: ArchivalRecord,
        *,
        outcome: ArchivalOutcomeCode,
        retryable: bool,
        safe_summary: str,
    ) -> ArchivalReceipt:
        store = self._require_capability()
        attempts = (
            record.receipt.extraction_attempts
            if record.receipt.phase == ArchivalPhase.EXTRACTION
            else record.receipt.commit_attempts
        )
        exhausted = retryable and attempts >= self.max_attempts
        status = (
            ArchivalStatus.RETRY_WAIT
            if retryable and not exhausted
            else ArchivalStatus.FAILED
        )
        effective_outcome = (
            ArchivalOutcomeCode.RETRY_EXHAUSTED if exhausted else outcome
        )
        next_attempt_at = None
        if status == ArchivalStatus.RETRY_WAIT:
            next_attempt_at = time.time() + self.base_delay_seconds * (
                2 ** max(0, attempts - 1)
            )
        failed = replace(
            record,
            receipt=replace(
                record.receipt,
                status=status,
                outcome_code=effective_outcome,
                retryable=(status == ArchivalStatus.RETRY_WAIT),
                safe_summary=safe_summary,
                next_attempt_at=next_attempt_at,
                updated_at=utc_now(),
            ),
            record_version=record.record_version + 1,
        )
        try:
            return store.update_archival_record(failed).receipt
        except ArchivalConflictError:
            return self._latest_receipt(record)

    def _latest_receipt(self, record: ArchivalRecord) -> ArchivalReceipt:
        store = self._require_capability()
        try:
            return store.get_archival_record(
                record.receipt.relationship_id,
                record.receipt.archival_id,
            ).receipt
        except ArchivalNotFoundError:
            return record.receipt

    def drain(self, timeout: float) -> ArchivalDrainReport:
        """Drains the non-terminal submission snapshot visible at call time."""
        store = self._require_capability()
        if timeout < 0:
            raise ValueError("timeout cannot be negative")
        snapshot = tuple(
            record.receipt.archival_id
            for record in store.list_archival_records()
            if record.receipt.status
            not in {ArchivalStatus.COMPLETED, ArchivalStatus.FAILED}
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() <= deadline:
            remaining = []
            for archival_id in snapshot:
                records = [
                    item
                    for item in store.list_archival_records()
                    if item.receipt.archival_id == archival_id
                ]
                if records and records[0].receipt.status not in {
                    ArchivalStatus.COMPLETED,
                    ArchivalStatus.FAILED,
                }:
                    remaining.append(archival_id)
            if not remaining:
                break
            progressed = 0
            for archival_id in remaining:
                progressed += self.process_pending(
                    max_tasks=1,
                    archival_id=archival_id,
                )
            if progressed == 0:
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))

        records_by_id = {
            record.receipt.archival_id: record
            for record in store.list_archival_records()
            if record.receipt.archival_id in snapshot
        }
        completed = sum(
            item.receipt.status == ArchivalStatus.COMPLETED
            for item in records_by_id.values()
        )
        failed = sum(
            item.receipt.status == ArchivalStatus.FAILED
            for item in records_by_id.values()
        )
        unfinished = tuple(
            archival_id
            for archival_id in snapshot
            if archival_id not in records_by_id
            or records_by_id[archival_id].receipt.status
            not in {ArchivalStatus.COMPLETED, ArchivalStatus.FAILED}
        )
        return ArchivalDrainReport(
            snapshot_size=len(snapshot),
            completed=completed,
            failed=failed,
            unfinished_archival_ids=unfinished,
        )

    def close(self, timeout: float = 1.0) -> ShutdownReport:
        """Stops acceptance without draining queued archival work."""
        if timeout < 0:
            raise ValueError("timeout cannot be negative")
        deadline = time.monotonic() + timeout
        with self._state_condition:
            self._accepting = False
            while self._processing_active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._state_condition.wait(timeout=remaining)
            in_flight = self._in_flight_archival_id
            return ShutdownReport(
                worker_stopped=not self._processing_active,
                unfinished_archival_ids=(
                    (in_flight,) if in_flight is not None else ()
                ),
            )

    def _sanitize(self, content: str) -> str:
        value = content
        if self.enable_sanitizer:
            value = SecuritySanitizer.sanitize_text(value)
        if self.enable_pii_scrubbing:
            value = SecuritySanitizer.scrub_pii(value)
        if not value.strip():
            raise ValueError("sanitized archival artifact is empty")
        if len(value) > 4096:
            raise ValueError("sanitized archival artifact exceeds 4096 characters")
        return value


__all__ = ["ArchivalCoordinator"]
