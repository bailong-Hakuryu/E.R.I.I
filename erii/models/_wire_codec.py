"""Canonical identities for new, strictly versioned portable wire records.

This module intentionally does not replace any pre-a8 fingerprint helper.  Old
formats retain their published identity algorithms; only modern wire objects
use this domain-separated envelope.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def _require_string_object_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("canonical wire objects require string keys")
            _require_string_object_keys(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _require_string_object_keys(item)


def canonical_wire_bytes(
    *,
    wire_type: str,
    wire_version: str,
    identity_payload: Mapping[str, Any],
) -> bytes:
    """Returns the exact canonical UTF-8 representation for one modern identity."""
    if not isinstance(wire_type, str) or not wire_type or wire_type != wire_type.strip():
        raise ValueError("wire_type must be a non-empty canonical string")
    if (
        not isinstance(wire_version, str)
        or not wire_version
        or wire_version != wire_version.strip()
    ):
        raise ValueError("wire_version must be a non-empty canonical string")
    if not isinstance(identity_payload, Mapping):
        raise ValueError("identity_payload must be a mapping")
    envelope = {
        "identity": identity_payload,
        "wire_type": wire_type,
        "wire_version": wire_version,
    }
    _require_string_object_keys(envelope)
    try:
        text = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("wire identity must be canonical UTF-8 JSON") from exc


def canonical_wire_sha256(
    *,
    wire_type: str,
    wire_version: str,
    identity_payload: Mapping[str, Any],
) -> str:
    """Returns a lowercase domain-separated SHA-256 portable identity."""
    return hashlib.sha256(
        canonical_wire_bytes(
            wire_type=wire_type,
            wire_version=wire_version,
            identity_payload=identity_payload,
        )
    ).hexdigest()


__all__ = ["canonical_wire_bytes", "canonical_wire_sha256"]
