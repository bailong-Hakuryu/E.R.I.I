"""Unified logging system for E.R.I.I. Engine.

Provides structured, secure logging with:
- Automatic API key redaction
- Configurable output formats (text, JSON)
- Context-aware logging
- Audit trail for critical operations
- Performance monitoring

Follows Google Python Style Guide.
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from erii.security.credential_manager import RedactingFormatter


class LogLevel(str, Enum):
    """Log level enumeration."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogFormat(str, Enum):
    """Log output format."""
    TEXT = "text"
    JSON = "json"


class StructuredLogger:
    """Structured logger with context support.

    Provides rich logging with structured fields, automatic redaction,
    and audit capabilities.

    Example:
        >>> logger = StructuredLogger.get_logger("erii.engine")
        >>> logger.info("Processing turn", turn_id="turn-123", user="alice")
    """

    _loggers: dict[str, logging.Logger] = {}

    @staticmethod
    def get_logger(
        name: str,
        level: LogLevel = LogLevel.INFO,
        format_type: LogFormat = LogFormat.TEXT,
    ) -> logging.Logger:
        """Get or create a structured logger.

        Args:
            name: Logger name (typically module path).
            level: Minimum log level.
            format_type: Output format (text or JSON).

        Returns:
            Configured logger instance.
        """
        if name in StructuredLogger._loggers:
            return StructuredLogger._loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, level.value))
        logger.propagate = False

        # Remove existing handlers
        logger.handlers.clear()

        # Add console handler with appropriate formatter
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, level.value))

        if format_type == LogFormat.JSON:
            handler.setFormatter(JSONFormatter())
        else:
            # Use redacting formatter for text output
            fmt = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            handler.setFormatter(RedactingFormatter(fmt))

        logger.addHandler(handler)
        StructuredLogger._loggers[name] = logger

        return logger

    @staticmethod
    def configure_from_dict(config: dict[str, Any]) -> None:
        """Configure logging from dictionary.

        Args:
            config: Configuration dictionary with keys:
                - level: str (DEBUG, INFO, WARNING, ERROR, CRITICAL)
                - format: str (text, json)
                - file: Optional[str] (log file path)
                - max_bytes: Optional[int] (max file size)
                - backup_count: Optional[int] (number of backup files)

        Example:
            >>> StructuredLogger.configure_from_dict({
            ...     'level': 'INFO',
            ...     'format': 'json',
            ...     'file': '/var/log/erii.log',
            ...     'max_bytes': 10485760,  # 10MB
            ...     'backup_count': 5
            ... })
        """
        level = LogLevel(config.get('level', 'INFO'))
        format_type = LogFormat(config.get('format', 'text'))
        log_file = config.get('file')

        # Configure root logger
        root_logger = logging.getLogger('erii')
        root_logger.setLevel(getattr(logging, level.value))
        root_logger.handlers.clear()

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, level.value))

        if format_type == LogFormat.JSON:
            console_handler.setFormatter(JSONFormatter())
        else:
            fmt = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            console_handler.setFormatter(RedactingFormatter(fmt))

        root_logger.addHandler(console_handler)

        # File handler (with rotation if specified)
        if log_file:
            from logging.handlers import RotatingFileHandler

            max_bytes = config.get('max_bytes', 10 * 1024 * 1024)  # 10MB default
            backup_count = config.get('backup_count', 5)

            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(getattr(logging, level.value))

            if format_type == LogFormat.JSON:
                file_handler.setFormatter(JSONFormatter())
            else:
                fmt = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
                file_handler.setFormatter(RedactingFormatter(fmt))

            root_logger.addHandler(file_handler)


class JSONFormatter(logging.Formatter):
    """JSON log formatter with structured fields.

    Outputs logs as JSON for machine parsing and log aggregation systems.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.

        Args:
            record: Log record to format.

        Returns:
            JSON-formatted log string.
        """
        log_data = {
            'timestamp': datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in [
                'name', 'msg', 'args', 'created', 'filename', 'funcName',
                'levelname', 'levelno', 'lineno', 'module', 'msecs',
                'message', 'pathname', 'process', 'processName',
                'relativeCreated', 'thread', 'threadName', 'exc_info',
                'exc_text', 'stack_info'
            ]:
                log_data[key] = value

        return json.dumps(log_data, ensure_ascii=False)


class AuditLogger:
    """Audit logger for critical operations.

    Records security-relevant and data-modifying operations with
    detailed context for compliance and debugging.
    """

    def __init__(self, logger_name: str = "erii.audit"):
        """Initialize audit logger.

        Args:
            logger_name: Name for the audit logger.
        """
        self.logger = StructuredLogger.get_logger(
            logger_name,
            level=LogLevel.INFO,
            format_type=LogFormat.JSON
        )

    def log_operation(
        self,
        operation: str,
        status: str,
        **context: Any
    ) -> None:
        """Log an auditable operation.

        Args:
            operation: Operation name (e.g., "relationship_init", "persona_approve").
            status: Operation status (success, failure, pending).
            **context: Additional context fields.

        Example:
            >>> audit = AuditLogger()
            >>> audit.log_operation(
            ...     "persona_approve",
            ...     status="success",
            ...     relationship_id="rel-123",
            ...     proposal_id="prop-456"
            ... )
        """
        self.logger.info(
            f"AUDIT: {operation}",
            extra={
                'audit_operation': operation,
                'audit_status': status,
                'audit_timestamp': datetime.now(timezone.utc).isoformat(),
                **context
            }
        )

    def log_relationship_init(
        self,
        relationship_id: str,
        agent: str,
        user: str,
        status: str = "success"
    ) -> None:
        """Log relationship initialization.

        Args:
            relationship_id: Unique relationship identifier.
            agent: Agent name.
            user: User identifier.
            status: Operation status.
        """
        self.log_operation(
            "relationship_init",
            status=status,
            relationship_id=relationship_id,
            agent=agent,
            user=user
        )

    def log_persona_decision(
        self,
        relationship_id: str,
        proposal_id: str,
        decision: str,
        status: str = "success"
    ) -> None:
        """Log persona approval/rejection decision.

        Args:
            relationship_id: Relationship identifier.
            proposal_id: Proposal identifier.
            decision: Decision type (approve, reject, revoke).
            status: Operation status.
        """
        self.log_operation(
            "persona_decision",
            status=status,
            relationship_id=relationship_id,
            proposal_id=proposal_id,
            decision=decision
        )

    def log_data_import(
        self,
        source_type: str,
        record_count: int,
        status: str = "success"
    ) -> None:
        """Log data import operation.

        Args:
            source_type: Type of import (memorypack, backup, etc.).
            record_count: Number of records imported.
            status: Operation status.
        """
        self.log_operation(
            "data_import",
            status=status,
            source_type=source_type,
            record_count=record_count
        )

    def log_data_export(
        self,
        target_type: str,
        record_count: int,
        status: str = "success"
    ) -> None:
        """Log data export operation.

        Args:
            target_type: Type of export (memorypack, backup, etc.).
            record_count: Number of records exported.
            status: Operation status.
        """
        self.log_operation(
            "data_export",
            status=status,
            target_type=target_type,
            record_count=record_count
        )

    def log_data_deletion(
        self,
        deletion_type: str,
        scope: str,
        record_count: int,
        status: str = "success"
    ) -> None:
        """Log data deletion operation.

        Args:
            deletion_type: Type of deletion (relationship, turn, user, etc.).
            scope: Scope identifier.
            record_count: Number of records deleted.
            status: Operation status.
        """
        self.log_operation(
            "data_deletion",
            status=status,
            deletion_type=deletion_type,
            scope=scope,
            record_count=record_count
        )


class PerformanceLogger:
    """Performance monitoring logger.

    Tracks operation durations and provides timing context managers.
    """

    def __init__(self, logger_name: str = "erii.performance"):
        """Initialize performance logger.

        Args:
            logger_name: Name for the performance logger.
        """
        self.logger = StructuredLogger.get_logger(
            logger_name,
            level=LogLevel.DEBUG
        )

    def log_timing(
        self,
        operation: str,
        duration_ms: float,
        **context: Any
    ) -> None:
        """Log operation timing.

        Args:
            operation: Operation name.
            duration_ms: Duration in milliseconds.
            **context: Additional context.
        """
        self.logger.debug(
            f"PERF: {operation} took {duration_ms:.2f}ms",
            extra={
                'perf_operation': operation,
                'perf_duration_ms': duration_ms,
                **context
            }
        )

    def timer(self, operation: str, **context: Any):
        """Context manager for timing operations.

        Args:
            operation: Operation name.
            **context: Additional context.

        Returns:
            Timer context manager.

        Example:
            >>> perf = PerformanceLogger()
            >>> with perf.timer("recall", relationship_id="rel-123"):
            ...     # Operation to time
            ...     pass
        """
        return TimerContext(self, operation, context)


class TimerContext:
    """Context manager for timing operations."""

    def __init__(
        self,
        perf_logger: PerformanceLogger,
        operation: str,
        context: dict[str, Any]
    ):
        """Initialize timer context.

        Args:
            perf_logger: Performance logger instance.
            operation: Operation name.
            context: Additional context.
        """
        self.perf_logger = perf_logger
        self.operation = operation
        self.context = context
        self.start_time: Optional[float] = None

    def __enter__(self):
        """Start timer."""
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop timer and log duration."""
        if self.start_time is not None:
            duration_ms = (time.perf_counter() - self.start_time) * 1000
            self.perf_logger.log_timing(
                self.operation,
                duration_ms,
                **self.context
            )


# Global instances
_default_logger: Optional[logging.Logger] = None
_audit_logger: Optional[AuditLogger] = None
_perf_logger: Optional[PerformanceLogger] = None


def get_logger(name: str = "erii") -> logging.Logger:
    """Get the default logger.

    Args:
        name: Logger name.

    Returns:
        Logger instance.
    """
    global _default_logger
    if _default_logger is None:
        _default_logger = StructuredLogger.get_logger(name)
    return _default_logger


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger.

    Returns:
        AuditLogger instance.
    """
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def get_performance_logger() -> PerformanceLogger:
    """Get the global performance logger.

    Returns:
        PerformanceLogger instance.
    """
    global _perf_logger
    if _perf_logger is None:
        _perf_logger = PerformanceLogger()
    return _perf_logger
