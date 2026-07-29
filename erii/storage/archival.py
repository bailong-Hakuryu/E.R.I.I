"""Versioned deep storage seam for reliable archival coordination."""

from typing import List, Optional, Protocol, Union

from erii.models.archival import (
    ArchivalRecord,
    ArchivalTombstone,
    PreparedArchivalBatch,
)


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
