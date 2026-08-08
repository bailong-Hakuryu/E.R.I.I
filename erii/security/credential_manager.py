"""Secure credential management for E.R.I.I. Engine.

This module provides secure API key and credential management following security best practices:
- Keys are ONLY loaded from environment variables or external secret managers
- Keys are NEVER stored in source code, logs, documentation, or persistent data
- Keys are redacted in all logging and error messages
- Key leakage detection for CI/CD pipelines

Follows Google Python Style Guide.
"""

import hashlib
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("erii.security")


class CredentialError(Exception):
    """Raised when credential operations fail."""
    pass


class CredentialManager:
    """Manages secure loading and handling of API credentials.

    All API keys and secrets must be provided via environment variables or
    external secret management systems. Direct string literals are rejected.
    """

    # Minimum key length for security validation
    MIN_KEY_LENGTH = 8

    # Pattern to detect potential key leakage in logs/output
    # Matches common API key patterns with actual key prefixes (sk-, api-, token-, etc.)
    # More specific to reduce false positives
    KEY_PATTERN = re.compile(
        r'(?:api[_-]?key|token|secret|password|credential)[\s:="\']+' +
        r'((?:sk-|api[-_]|token[-_]|key[-_])[a-zA-Z0-9_\-]{12,}|[a-zA-Z0-9_\-]{32,})',
        re.IGNORECASE
    )

    @staticmethod
    def get_api_key(
        provider: str,
        env_var: Optional[str] = None,
        required: bool = True
    ) -> Optional[str]:
        """Retrieves API key from environment variables.

        Args:
            provider: Provider name (e.g., 'openai', 'deepseek', 'gemini').
            env_var: Custom environment variable name. If None, uses standard
                naming convention: {PROVIDER}_API_KEY (e.g., OPENAI_API_KEY).
            required: If True, raises CredentialError when key is missing.
                If False, returns None for missing keys.

        Returns:
            API key string, or None if not required and not found.

        Raises:
            CredentialError: If required=True and key is not found or invalid.

        Example:
            >>> key = CredentialManager.get_api_key('openai')
            >>> # Reads from OPENAI_API_KEY environment variable

            >>> key = CredentialManager.get_api_key(
            ...     'custom', env_var='MY_CUSTOM_KEY'
            ... )
            >>> # Reads from MY_CUSTOM_KEY environment variable
        """
        # Determine environment variable name
        if env_var is None:
            env_var = f"{provider.upper()}_API_KEY"

        # Retrieve from environment
        api_key = os.environ.get(env_var)

        if api_key is None:
            if required:
                raise CredentialError(
                    f"Missing required API key for '{provider}'. "
                    f"Please set environment variable: {env_var}"
                )
            return None

        # Validate key format
        api_key = api_key.strip()
        if len(api_key) < CredentialManager.MIN_KEY_LENGTH:
            raise CredentialError(
                f"API key for '{provider}' is too short (minimum {CredentialManager.MIN_KEY_LENGTH} characters). "
                f"Check environment variable: {env_var}"
            )

        logger.info(
            "Loaded API key for provider '%s' from environment variable '%s' (length: %d)",
            provider,
            env_var,
            len(api_key)
        )

        return api_key

    @staticmethod
    def redact_key(key: str, visible_chars: int = 4) -> str:
        """Redacts an API key for safe logging/display.

        Args:
            key: The API key to redact.
            visible_chars: Number of characters to show at the start.

        Returns:
            Redacted key string (e.g., "sk-1234***").

        Example:
            >>> CredentialManager.redact_key("sk-1234567890abcdef")
            'sk-1234***'
        """
        if not key:
            return "<empty>"

        if len(key) <= visible_chars:
            return "***"

        return f"{key[:visible_chars]}***"

    @staticmethod
    def get_key_fingerprint(key: str) -> str:
        """Generates a stable fingerprint for key identification.

        Useful for logging and debugging without exposing the actual key.

        Args:
            key: The API key.

        Returns:
            SHA-256 hash prefix (first 8 characters).

        Example:
            >>> CredentialManager.get_key_fingerprint("sk-1234567890")
            'a3d5e8f1'
        """
        if not key:
            return "<no-key>"

        return hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]

    @staticmethod
    def detect_key_leakage(text: str) -> list[str]:
        """Detects potential API key leakage in text.

        This is used for CI/CD pipeline checks to prevent accidental
        key exposure in logs, documentation, or test output.

        Args:
            text: Text to scan for potential keys.

        Returns:
            List of detected potential keys (redacted).

        Example:
            >>> text = 'api_key="sk-1234567890abcdef"'
            >>> CredentialManager.detect_key_leakage(text)
            ['sk-1234***']
        """
        matches = CredentialManager.KEY_PATTERN.findall(text)
        return [CredentialManager.redact_key(match, visible_chars=6) for match in matches]

    @staticmethod
    def validate_no_literal_keys(code: str, file_path: str = "<unknown>") -> None:
        """Validates that code does not contain literal API keys.

        This should be run in CI/CD pipelines on all source files.

        Args:
            code: Source code or text content to validate.
            file_path: File path for error reporting.

        Raises:
            CredentialError: If potential keys are detected.

        Example:
            >>> with open('my_adapter.py') as f:
            ...     CredentialManager.validate_no_literal_keys(
            ...         f.read(), 'my_adapter.py'
            ...     )
        """
        # Skip validation for this file itself, test files, and validation scripts
        skip_patterns = [
            'credential_manager.py',
            'test_',
            'validate_',
            'check_key_leakage.py',
        ]

        if any(pattern in file_path for pattern in skip_patterns):
            return

        detected = CredentialManager.detect_key_leakage(code)
        if detected:
            raise CredentialError(
                f"Potential API key leakage detected in {file_path}. "
                f"Found {len(detected)} potential key(s): {detected[:3]}. "
                "API keys must ONLY be loaded from environment variables."
            )


class RedactingFormatter(logging.Formatter):
    """Logging formatter that automatically redacts API keys.

    Use this formatter for all logging handlers to prevent accidental
    key exposure in logs.

    Example:
        >>> handler = logging.StreamHandler()
        >>> handler.setFormatter(RedactingFormatter(
        ...     '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ... ))
        >>> logger.addHandler(handler)
    """

    def format(self, record: logging.LogRecord) -> str:
        """Formats log record with key redaction.

        Args:
            record: Log record to format.

        Returns:
            Formatted log message with keys redacted.
        """
        # Format the original message
        original = super().format(record)

        # Redact any potential keys
        matches = CredentialManager.KEY_PATTERN.findall(original)
        redacted = original
        for match in matches:
            redacted_key = CredentialManager.redact_key(match, visible_chars=4)
            redacted = redacted.replace(match, redacted_key)

        return redacted


def setup_secure_logging(logger_instance: logging.Logger) -> None:
    """Configures a logger with key redaction.

    Args:
        logger_instance: Logger to configure.

    Example:
        >>> import logging
        >>> logger = logging.getLogger('erii')
        >>> setup_secure_logging(logger)
    """
    for handler in logger_instance.handlers:
        if not isinstance(handler.formatter, RedactingFormatter):
            # Preserve existing format if available
            fmt = handler.formatter._fmt if handler.formatter else None
            handler.setFormatter(RedactingFormatter(fmt))
