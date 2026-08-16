"""Versioned deep storage seams for reliable archival coordination."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union

from erii.models.archival import (
    ArchivalRecord,
    ArchivalStatus,
    ArchivalTombstone,
    PreparedArchivalBatch,
)


@dataclass(frozen=True)
class ArchivalTombstoneValidationSource:
    """Coherent storage state consumed by portable tombstone validation."""

    relationship_id: str
    archival_ids: Tuple[str, ...]
    tombstones: Tuple[ArchivalTombstone, ...]
    live_records: Tuple[ArchivalRecord, ...]

    def __post_init__(self) -> None:
        archival_ids = tuple(sorted(set(self.archival_ids)))
        relevant_ids = frozenset(archival_ids)
        object.__setattr__(self, "archival_ids", archival_ids)
        object.__setattr__(
            self,
            "tombstones",
            tuple(
                item
                for item in self.tombstones
                if item.relationship_id == self.relationship_id
                or item.archival_id in relevant_ids
            ),
        )
        object.__setattr__(
            self,
            "live_records",
            tuple(
                item
                for item in self.live_records
                if item.receipt.relationship_id == self.relationship_id
                or item.receipt.archival_id in relevant_ids
            ),
        )

    def _tombstone_observation(
        self,
        tombstone: ArchivalTombstone,
    ) -> Dict[str, Any]:
        if tombstone.archival_id in self.archival_ids:
            return tombstone.to_dict()
        return {
            "archival_id": tombstone.archival_id,
            "relationship_id": tombstone.relationship_id,
            "request_fingerprint": tombstone.request_fingerprint,
            "idempotency_fingerprint": tombstone.idempotency_fingerprint,
        }

    def _live_record_observation(
        self,
        record: ArchivalRecord,
    ) -> Dict[str, Any]:
        receipt = record.receipt
        binding = {
            "archival_id": receipt.archival_id,
            "relationship_id": receipt.relationship_id,
            "request_fingerprint": record.request_fingerprint,
            "idempotency_fingerprint": record.idempotency_fingerprint,
        }
        if receipt.archival_id not in self.archival_ids:
            return binding
        if receipt.status in {ArchivalStatus.COMPLETED, ArchivalStatus.FAILED}:
            return ArchivalTombstone.from_record(record).to_dict()
        return {**binding, "terminal": False}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tombstones": [
                self._tombstone_observation(item)
                for item in sorted(
                    self.tombstones,
                    key=lambda item: item.archival_id,
                )
            ],
            "live_records": [
                self._live_record_observation(item)
                for item in sorted(
                    self.live_records,
                    key=lambda item: item.receipt.archival_id,
                )
            ],
        }


class AtomicArchivalStoreV1(Protocol):
    """Internal capability used by ArchivalCoordinator as one deep module."""

    def create_archival_record(
        self,
        record: ArchivalRecord,
    ) -> Union[ArchivalRecord, ArchivalTombstone]:
        ...

    def get_archival_record(
        self,
        relationship_id: str,
        archival_id: str,
    ) -> ArchivalRecord:
        ...

    def list_archival_records(
        self,
        relationship_id: Optional[str] = None,
    ) -> List[ArchivalRecord]:
        ...

    def claim_next_archival_record(
        self,
        *,
        now: float,
        lease_seconds: float,
        permit_seconds: float,
        archival_id: Optional[str] = None,
    ) -> Optional[ArchivalRecord]:
        ...

    def bind_prepared_archival_batch(
        self,
        record: ArchivalRecord,
        batch: PreparedArchivalBatch,
    ) -> ArchivalRecord:
        ...

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
        ...

    def commit_archival_batch(self, record: ArchivalRecord) -> ArchivalRecord:
        ...

    def update_archival_record(self, record: ArchivalRecord) -> ArchivalRecord:
        ...

    def acquire_archival_consumer(
        self,
        consumer_id: str,
        *,
        now: float,
        lease_seconds: float,
    ) -> bool:
        ...

    def release_archival_consumer(self, consumer_id: str) -> None:
        ...

    def compact_archival_records(self, *, before: str) -> int:
        ...
