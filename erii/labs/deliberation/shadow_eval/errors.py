"""Shadow evaluation error codes."""

from __future__ import annotations

from enum import Enum


class ShadowFailureCode(str, Enum):
    """Shadow evaluation failure classification."""

    # Transport
    TRANSPORT_TIMEOUT = "transport_timeout"
    TRANSPORT_NETWORK_ERROR = "transport_network_error"
    TRANSPORT_PROVIDER_ERROR = "transport_provider_error"

    # Schema
    SCHEMA_PARSE_FAILED = "schema_parse_failed"
    SCHEMA_INVALID_STRUCTURE = "schema_invalid_structure"
    SCHEMA_DUPLICATE_IDS = "schema_duplicate_ids"

    # Evidence and binding
    EVIDENCE_SCOPE_VIOLATION = "evidence_scope_violation"
    CROSS_RELATIONSHIP_LEAK = "cross_relationship_leak"
    BINDING_MISMATCH = "binding_mismatch"
    STALE_BASELINE = "stale_baseline"

    # Canary
    CANARY_LEAK_DETECTED = "canary_leak_detected"

    # Configuration
    CONFIGURATION_INVALID = "configuration_invalid"
    SCENARIO_INVALID = "scenario_invalid"


__all__ = ["ShadowFailureCode"]
