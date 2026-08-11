# Turn and REST Error Handling

This document covers the exceptions and HTTP error envelope implemented by the
current E.R.I.I. Turn lifecycle and reference server.

## REST error envelope

Every reference-server error uses this top-level shape:

```json
{
  "detail": {
    "code": "turn_not_found",
    "retryable": false,
    "safe_summary": "Turn was not found in this relationship scope."
  }
}
```

The required fields are:

| Field | Meaning |
|---|---|
| `code` | Stable machine-readable classification |
| `retryable` | Server advice for retrying the same request later |
| `safe_summary` | Client-displayable summary without internal diagnostics |

Accepted archival failures can additionally include a sanitized `receipt`.
Clients must tolerate additive fields while continuing to branch on `code`.

Request-parser details, raw exception messages, database paths, SQL, provider
responses, API keys, and transcript content are not returned by the error
envelope. Full diagnostics are logged on the server side.

## Canonical status and code mapping

### Framework boundary

| HTTP | Code | Retryable | Meaning |
|---:|---|:---:|---|
| 400 | `invalid_request` | no | Input reached an endpoint but was not acceptable |
| 401 | `authentication_required` | no | Missing, duplicate, or incorrect owner key |
| 403 | `loopback_access_required` | no | Local unauthenticated mode was called remotely |
| 404 | `route_not_found` | no | No route matches the request |
| 413 | `request_too_large` | no | Body exceeds the configured REST limit |
| 422 | `validation_error` | no | Body, query, path, or domain validation failed |
| 500 | `internal_error` | no | Unexpected server failure |
| 503 | `server_access_unconfigured` | no | Reference-server access has no configured boundary |

Other reference endpoints use the same envelope, including
`thought_not_found` (404) and `invalid_memory_pack` (422).

FastAPI body/query/path validation is deliberately collapsed into one safe 422
response. Field-level parser output is not part of the public error contract.

### Turn and continuity boundary

| HTTP | Code | Retryable | Meaning |
|---:|---|:---:|---|
| 404 | `relationship_not_found` | no | The requested relationship is absent |
| 404 | `turn_not_found` | no | The Turn is absent in the requested scope |
| 409 | `turn_conflict` | no | Stable Turn identity or terminal state conflicts |
| 409 | `persona_manifest_required` | no | The operation requires an approved Manifest |
| 503 | `continuity_capability_unavailable` | no | No continuity evaluator is configured |

Configuration errors are not marked retryable because repeating the same
request without changing host configuration does not resolve them.

### Archival and relationship processing boundary

| HTTP | Code | Retryable | Meaning |
|---:|---|:---:|---|
| 404 | `archival_not_found` | no | Receipt is absent in this relationship scope |
| 409 | `archival_conflict` | no | Idempotency key conflicts with an existing binding |
| 409 | `relationship_conflict` | no | Relationship history/adjudication conflicts |
| 422 | `invalid_source_turn` | no | Archival source is absent or not completed |
| 422 | `recall_budget_unsatisfied` | no | Declared recall budget cannot be satisfied |
| 503 | `archival_capability_unavailable` | no | No reliable memory extractor is configured |

An archival failure after acceptance uses the receipt's safe outcome code and
`retryable` value. That is the current case where `retryable` can be true.
Preserve the idempotency key when retrying it.

## Python exception model

The direct Python API raises typed exceptions. Relevant public types include:

```text
RelationshipNotFoundError
TurnNotFoundError
TurnConflictError
  +-- TurnTerminalConflictError
  +-- ReplyAttemptConflictError
ContinuityEvaluationCapabilityError
PersonaManifestRequiredError
ArchivalCapabilityError
ArchivalSubmissionError
ArchivalConflictError
ArchivalNotFoundError
ArchivalProcessingError
RelationshipProcessingCapabilityError
RelationshipProcessingSubmissionError
RelationshipProcessingError
ValueError
```

The three relationship-processing classes are defined in
`erii.core.relationship_processing`. Inspect the installed version's exports
before importing them from the package root.

### `TurnNotFoundError`

Raised when a Turn identity does not exist inside the exact relationship scope.
The same external symptom can result from using the wrong Agent/User pair;
callers should not search other relationships for a matching ID.

```python
from erii import TurnNotFoundError

try:
    turn = engine.get_turn("AGENT", "USER", "TURN_ID")
except TurnNotFoundError:
    turn = None
```

### `TurnConflictError`

Raised when a stable Turn or attempt identity is reused for different immutable
content. It is not a transient storage error.

```python
from erii import TurnConflictError

try:
    turn = engine.begin_turn(
        "AGENT",
        "USER",
        visible_user_message,
        turn_id="HOST_TURN_ID",
    )
except TurnConflictError:
    # Compare the host's durable operation with the stored Turn before acting.
    raise
```

### `TurnTerminalConflictError`

Raised when code attempts to change a completed or abandoned Turn. Identical
completion and abandonment retries are already handled idempotently; this
exception therefore means the requested terminal payload differs.

Do not overwrite the stored Turn and do not silently archive the new payload
under another ID. Resolve the host-side duplicate or ordering error.

### `ArchivalProcessingError`

Inline archival can fail after the request has a durable archival identity. The
exception carries a sanitized `receipt`:

```python
from erii import ArchivalProcessingError

try:
    receipt = engine.archive_turn(
        "AGENT",
        "USER",
        "TURN_ID",
        idempotency_key="HOST_ARCHIVAL_KEY",
    )
except ArchivalProcessingError as exc:
    receipt = exc.receipt
    if receipt.retryable:
        # Retry later with the same idempotency key.
        schedule_retry(receipt.archival_id)
    else:
        record_terminal_failure(receipt.archival_id)
```

Use only `receipt.safe_summary` for an end-user or public operational message.

## Retry decisions

### Safe retry candidates

Retry only when all of these are true:

1. The operation has a stable Turn or archival identity.
2. The immutable payload is unchanged.
3. The server/receipt explicitly says `retryable: true`, or transport failed
   before an HTTP response was received.
4. Retrying will use the same archival idempotency key.

### Do not automatically retry

- `validation_error`
- `invalid_request`
- `turn_conflict`
- `relationship_conflict`
- `archival_conflict`
- `persona_manifest_required`
- capability-not-configured codes
- authentication and access-boundary codes

These require a caller, configuration, or state decision rather than delay.

### Unknown delivery outcome

If the network fails after the host may have displayed a reply, first query the
stable Turn ID. Do not regenerate and write a new reply immediately.

```python
try:
    existing = engine.get_turn("AGENT", "USER", "HOST_TURN_ID")
except TurnNotFoundError:
    existing = None

if existing is None:
    # No durable Turn exists; follow the host's delivery reconciliation policy.
    reconcile_delivery()
elif existing.status.value == "completed":
    use_existing_terminal_record(existing)
```

## REST client example

```python
response = client.post(
    "/api/v1/turns/open",
    headers={"X-API-Key": service_key},
    json={
        "agent_id": agent_id,
        "user_id": user_id,
        "turn_id": host_turn_id,
        "user_message": visible_user_message,
    },
)

if response.is_error:
    detail = response.json()["detail"]
    if detail["retryable"]:
        retry_same_request_later()
    else:
        handle_by_code(detail["code"])
```

Do not display an arbitrary response body or stack trace. Read only the stable
error fields.

## Contract tests

`tests/test_rest_error_contract_public.py` verifies exact response snapshots for:

- framework 404;
- body and query 422 validation;
- scoped missing Turn;
- Turn conflicts without content echo;
- missing continuity capability;
- unexpected internal exceptions without diagnostic leakage.

`tests/test_security_regressions.py` additionally covers owner-key enforcement,
body-size limits, and internal error redaction.

Host integrations should add their own contract test at the service boundary.
For example:

```python
assert response.status_code == 422
assert response.json() == {
    "detail": {
        "code": "validation_error",
        "retryable": False,
        "safe_summary": "Request validation failed.",
    }
}
```

## Logging and observability

Log server diagnostics separately from client responses. Useful fields are:

- operation name;
- HTTP status and error code;
- Turn or archival identity when policy permits;
- attempt number;
- extractor/evaluator descriptor;
- archival status and retryability.

Avoid logging:

- API keys and authorization headers;
- complete request bodies;
- hidden/rejected drafts;
- raw provider responses;
- database connection strings;
- exported MemoryPacks.

## Related documents

- [Turn Lifecycle API Reference](turn-lifecycle.md)
- [Advanced Turn usage](turn-advanced-usage.md)
- [REST archival boundary ADR](../adr/0068-map-archival-lifecycle-truthfully-at-the-rest-boundary.md)
