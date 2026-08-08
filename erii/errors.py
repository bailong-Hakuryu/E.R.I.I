"""Shared public errors for durable data and compatibility boundaries.

Enhanced error handling with:
- Error codes for programmatic handling
- Rich context information
- Recovery suggestions
- Severity levels
"""

from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    """Standard error codes for E.R.I.I. operations."""

    # Storage errors (1xxx)
    STORAGE_UNKNOWN = "E1000"
    STORAGE_INTEGRITY = "E1001"
    STORAGE_WRITE_FAILED = "E1002"
    STORAGE_READ_FAILED = "E1003"
    STORAGE_NOT_FOUND = "E1004"
    STORAGE_LOCKED = "E1005"

    # Format errors (2xxx)
    FORMAT_UNSUPPORTED = "E2000"
    FORMAT_MIGRATION_REQUIRED = "E2001"
    FORMAT_CORRUPTED = "E2002"
    FORMAT_VERSION_MISMATCH = "E2003"

    # Lifecycle errors (3xxx)
    LIFECYCLE_UNKNOWN = "E3000"
    LIFECYCLE_PLAN_INVALID = "E3001"
    LIFECYCLE_STALE = "E3002"
    LIFECYCLE_CONFLICT = "E3003"
    LIFECYCLE_VERIFICATION_FAILED = "E3004"

    # Credential errors (4xxx)
    CREDENTIAL_MISSING = "E4000"
    CREDENTIAL_INVALID = "E4001"
    CREDENTIAL_EXPIRED = "E4002"

    # API errors (5xxx)
    API_CONNECTION_FAILED = "E5000"
    API_TIMEOUT = "E5001"
    API_RATE_LIMIT = "E5002"
    API_AUTHENTICATION_FAILED = "E5003"
    API_INVALID_REQUEST = "E5004"
    API_INVALID_RESPONSE = "E5005"

    # Validation errors (6xxx)
    VALIDATION_FAILED = "E6000"
    VALIDATION_CONSTRAINT = "E6001"
    VALIDATION_TYPE_ERROR = "E6002"

    # Relationship errors (7xxx)
    RELATIONSHIP_NOT_FOUND = "E7000"
    RELATIONSHIP_CONFLICT = "E7001"
    RELATIONSHIP_UNINITIALIZED = "E7002"

    # Internal errors (9xxx)
    INTERNAL_ERROR = "E9000"
    NOT_IMPLEMENTED = "E9001"
    CONFIGURATION_ERROR = "E9002"


class ErrorSeverity(str, Enum):
    """Error severity levels."""
    LOW = "low"          # Informational, no action needed
    MEDIUM = "medium"    # Warning, operation may continue
    HIGH = "high"        # Error, operation failed but recoverable
    CRITICAL = "critical"  # Critical, system state may be compromised


class ERIIError(Exception):
    """Base exception for all E.R.I.I. errors.

    Provides rich error context including:
    - Error code for programmatic handling
    - Severity level
    - Context information
    - Recovery suggestions
    - Original exception (if wrapped)
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        severity: ErrorSeverity = ErrorSeverity.HIGH,
        context: Optional[dict[str, Any]] = None,
        recovery_hint: Optional[str] = None,
        cause: Optional[Exception] = None
    ) -> None:
        """Initialize enhanced error.

        Args:
            message: Human-readable error message.
            code: Error code for programmatic handling.
            severity: Error severity level.
            context: Additional context information (sanitized for logging).
            recovery_hint: Suggestion for recovering from this error.
            cause: Original exception if this wraps another error.
        """
        super().__init__(message)
        self.code = code
        self.severity = severity
        self.context = context or {}
        self.recovery_hint = recovery_hint
        self.cause = cause

    def __str__(self) -> str:
        """Format error as string."""
        parts = [f"[{self.code.value}] {super().__str__()}"]

        if self.context:
            # Redact sensitive keys
            safe_context = {
                k: v for k, v in self.context.items()
                if k not in ['api_key', 'password', 'token', 'secret']
            }
            if safe_context:
                parts.append(f"Context: {safe_context}")

        if self.recovery_hint:
            parts.append(f"Recovery: {self.recovery_hint}")

        if self.cause:
            parts.append(f"Caused by: {type(self.cause).__name__}: {self.cause}")

        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Convert error to dictionary for JSON serialization.

        Returns:
            Dictionary representation of the error.
        """
        return {
            'error': {
                'code': self.code,
                'severity': self.severity,
                'message': super().__str__(),
                'context': self.context,
                'recovery_hint': self.recovery_hint,
                'cause': str(self.cause) if self.cause else None
            }
        }


# Storage Errors

class StorageError(ERIIError, RuntimeError):
    """Base error for a storage operation that could not be completed safely."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.STORAGE_UNKNOWN,
        **kwargs
    ) -> None:
        super().__init__(message, code=code, **kwargs)


class StorageIntegrityError(StorageError):
    """Stored data is unreadable, malformed, or inconsistent with its identity."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.STORAGE_INTEGRITY,
            severity=ErrorSeverity.CRITICAL,
            recovery_hint="Restore from backup or reinitialize storage",
            **kwargs
        )


class StorageWriteError(StorageError):
    """A durable write failed without being allowed to replace prior data."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.STORAGE_WRITE_FAILED,
            severity=ErrorSeverity.HIGH,
            recovery_hint="Check file permissions and disk space",
            **kwargs
        )


# Format Errors

class UnsupportedFormatError(ERIIError, ValueError):
    """Stored data uses a format version this reader cannot safely interpret."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.FORMAT_UNSUPPORTED,
            severity=ErrorSeverity.HIGH,
            recovery_hint="Upgrade to a newer version of E.R.I.I.",
            **kwargs
        )


class MigrationRequiredError(StorageError):
    """Stored data is supported but must be upgraded through the lifecycle API."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.FORMAT_MIGRATION_REQUIRED,
            severity=ErrorSeverity.MEDIUM,
            recovery_hint="Run data migration using lifecycle API",
            **kwargs
        )


# Lifecycle Errors

class LifecycleError(ERIIError, RuntimeError):
    """Base error for a data-lifecycle operation that could not finish safely."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.LIFECYCLE_UNKNOWN,
        **kwargs
    ) -> None:
        super().__init__(message, code=code, **kwargs)


class LifecyclePlanError(LifecycleError, ValueError):
    """A lifecycle request or serialized plan is invalid or unsafe."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.LIFECYCLE_PLAN_INVALID,
            severity=ErrorSeverity.HIGH,
            **kwargs
        )


class StaleLifecyclePlanError(LifecycleError):
    """A source or destination changed after its lifecycle plan was frozen."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.LIFECYCLE_STALE,
            severity=ErrorSeverity.MEDIUM,
            recovery_hint="Regenerate lifecycle plan with current data",
            **kwargs
        )


class LifecycleConflictError(LifecycleError):
    """A lifecycle destination is occupied by a different operation or payload."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.LIFECYCLE_CONFLICT,
            severity=ErrorSeverity.HIGH,
            recovery_hint="Resolve conflict manually or use different destination",
            **kwargs
        )


class LifecycleVerificationError(LifecycleError):
    """A staged or published lifecycle result could not be verified."""

    def __init__(self, message: str, *, recovery_status: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.LIFECYCLE_VERIFICATION_FAILED,
            severity=ErrorSeverity.CRITICAL,
            context={'recovery_status': recovery_status},
            recovery_hint="Restore from backup",
            **kwargs
        )
        self.recovery_status = recovery_status


# API and Network Errors

class APIError(ERIIError):
    """Base error for API operations."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.API_CONNECTION_FAILED,
        **kwargs
    ) -> None:
        super().__init__(message, code=code, **kwargs)


class APIConnectionError(APIError):
    """Failed to connect to external API."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.API_CONNECTION_FAILED,
            severity=ErrorSeverity.HIGH,
            recovery_hint="Check network connection and API endpoint availability",
            **kwargs
        )


class APITimeoutError(APIError):
    """API request timed out."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.API_TIMEOUT,
            severity=ErrorSeverity.MEDIUM,
            recovery_hint="Retry with increased timeout or check API status",
            **kwargs
        )


class APIRateLimitError(APIError):
    """API rate limit exceeded."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.API_RATE_LIMIT,
            severity=ErrorSeverity.MEDIUM,
            recovery_hint="Wait before retrying or upgrade API plan",
            **kwargs
        )


class APIAuthenticationError(APIError):
    """API authentication failed."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.API_AUTHENTICATION_FAILED,
            severity=ErrorSeverity.HIGH,
            recovery_hint="Check API key validity and permissions",
            **kwargs
        )


# Validation Errors

class ValidationError(ERIIError, ValueError):
    """Data validation failed."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.VALIDATION_FAILED,
            severity=ErrorSeverity.MEDIUM,
            **kwargs
        )


# Relationship Errors

class RelationshipError(ERIIError):
    """Base error for relationship operations."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.RELATIONSHIP_CONFLICT,
        **kwargs
    ) -> None:
        super().__init__(message, code=code, **kwargs)


class RelationshipNotFoundError(RelationshipError, LookupError):
    """Relationship not found."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.RELATIONSHIP_NOT_FOUND,
            severity=ErrorSeverity.HIGH,
            recovery_hint="Initialize relationship first",
            **kwargs
        )


class RelationshipUninitializedError(RelationshipError):
    """Relationship not initialized."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.RELATIONSHIP_UNINITIALIZED,
            severity=ErrorSeverity.HIGH,
            recovery_hint="Call initialize_relationship() before use",
            **kwargs
        )


# Configuration Errors

class ConfigurationError(ERIIError):
    """Configuration error."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            code=ErrorCode.CONFIGURATION_ERROR,
            severity=ErrorSeverity.HIGH,
            recovery_hint="Check configuration file and environment variables",
            **kwargs
        )


__all__ = [
    # Error codes and severity
    "ErrorCode",
    "ErrorSeverity",
    # Base errors
    "ERIIError",
    # Storage errors
    "StorageError",
    "StorageIntegrityError",
    "StorageWriteError",
    # Format errors
    "UnsupportedFormatError",
    "MigrationRequiredError",
    # Lifecycle errors
    "LifecycleError",
    "LifecyclePlanError",
    "StaleLifecyclePlanError",
    "LifecycleConflictError",
    "LifecycleVerificationError",
    # API errors
    "APIError",
    "APIConnectionError",
    "APITimeoutError",
    "APIRateLimitError",
    "APIAuthenticationError",
    # Validation errors
    "ValidationError",
    # Relationship errors
    "RelationshipError",
    "RelationshipNotFoundError",
    "RelationshipUninitializedError",
    # Configuration errors
    "ConfigurationError",
]

