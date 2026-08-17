"""
Lifecycle Plan Codec: strict JSON serialization for durable lifecycle plans.

This module provides zero-tolerance JSON encoding/decoding for lifecycle plans,
ensuring byte-level compatibility with historical plan formats.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "canonical_json",
    "sha256_json",
    "decode_strict_json",
    "is_sha256",
]


def is_sha256(value: object) -> bool:
    """Check if value looks like a SHA-256 hex digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def canonical_json(value: object) -> bytes:
    """Encode value as canonical JSON bytes (sorted keys, no whitespace)."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    """Return SHA-256 hex digest of canonical JSON encoding."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def decode_strict_json(json_text: str, *, label: str) -> Any:
    """Decode JSON with strict error messages and duplicate key detection."""
    if not isinstance(json_text, str):
        raise ValueError(f"{label} must be JSON text")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate field {key!r}")
            result[key] = value
        return result

    return json.loads(json_text, object_pairs_hook=reject_duplicates)
