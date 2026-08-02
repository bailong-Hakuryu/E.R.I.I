"""Shared public errors for durable data and compatibility boundaries."""


class StorageError(RuntimeError):
    """Base error for a storage operation that could not be completed safely."""


class StorageIntegrityError(StorageError):
    """Stored data is unreadable, malformed, or inconsistent with its identity."""


class StorageWriteError(StorageError):
    """A durable write failed without being allowed to replace prior data."""


class UnsupportedFormatError(ValueError):
    """Stored data uses a format version this reader cannot safely interpret."""


class MigrationRequiredError(StorageError):
    """Stored data is supported but must be upgraded through the lifecycle API."""


class LifecycleError(RuntimeError):
    """Base error for a data-lifecycle operation that could not finish safely."""


class LifecyclePlanError(LifecycleError, ValueError):
    """A lifecycle request or serialized plan is invalid or unsafe."""


class StaleLifecyclePlanError(LifecycleError):
    """A source or destination changed after its lifecycle plan was frozen."""


class LifecycleConflictError(LifecycleError):
    """A lifecycle destination is occupied by a different operation or payload."""


class LifecycleVerificationError(LifecycleError):
    """A staged or published lifecycle result could not be verified."""

    def __init__(self, message: str, *, recovery_status: str) -> None:
        super().__init__(message)
        self.recovery_status = recovery_status


__all__ = [
    "LifecycleConflictError",
    "LifecycleError",
    "LifecyclePlanError",
    "LifecycleVerificationError",
    "MigrationRequiredError",
    "StaleLifecyclePlanError",
    "StorageError",
    "StorageIntegrityError",
    "StorageWriteError",
    "UnsupportedFormatError",
]
