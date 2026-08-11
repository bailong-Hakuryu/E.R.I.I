# Turn Lifecycle API Reference

**Status:** alpha public contract

**Verified against:** `ERIIEngine`, the REST models in `erii/server/app.py`, and
`examples/08_turn_lifecycle_integration.py`

The Turn lifecycle stores the exact conversation that was visible to the user.
It separates transcript capture from recall, reply generation, continuity
evaluation, archival, and relationship processing.

## Core invariants

1. A relationship must already exist for the exact `agent_id` and `user_id`.
2. `begin_turn()` stores the visible user message before a reply is generated.
3. Failed or rejected drafts are not stored as transcript messages.
4. `complete_turn()` stores only the agent reply actually displayed by the host.
5. A Turn is `open`, `completed`, or `abandoned`; terminal transitions are
   append-only.
6. Every read and write is scoped to one Agent x User relationship.
7. Archival is a separate operation with a required idempotency key.
8. The host controls processing. Creating an engine does not start a hidden
   reliable-archival thread.

## Recommended flow

```text
initialize relationship
        |
        v
begin_turn(user_message) -> OPEN
        |
        +--> recall_structured(audience=...)
        |
        +--> generate candidate reply outside E.R.I.I.
        |
        +--> optionally evaluate_reply_continuity(...)
        |
        +--> generation failed: abandon_turn(reason=...) -> ABANDONED
        |
        v
host displays reply
        |
        v
complete_turn(displayed_reply, delivery truth) -> COMPLETED
        |
        +--> archive_turn(idempotency_key=...)
        |
        +--> process_relationship_turn(...) when that capability is configured
```

Recall should happen after opening the Turn and before persisting the new agent
reply. The recalled result does not mutate the Turn transcript.

## Python API

### `begin_turn`

```python
begin_turn(
    agent_id: str,
    user_id: str,
    user_message: str,
    *,
    turn_id: str | None = None,
    interaction_context=(),
) -> TurnRecord
```

`interaction_context` accepts only `host_observed` signals through this public
entry point. Callers must not supply internal relationship, Turn, or producer
scope metadata.

An application-controlled `turn_id` is recommended. Repeating the same ID and
same opening payload is idempotent. Reusing it for different content raises
`TurnConflictError`.

### `evaluate_reply_continuity`

```python
evaluate_reply_continuity(
    agent_id: str,
    user_id: str,
    source_turn_id: str,
    proposed_reply: str,
    *,
    persona_context_refs,
    relationship_context_refs=(),
    interaction_context=(),
) -> ContinuityEvaluationResult
```

This operation requires:

- an open Turn;
- a Persona Manifest frozen at Turn opening;
- a configured continuity evaluator;
- evidence references that resolve inside that frozen context.

The proposed reply is evaluated before delivery. A result is self-bound to the
relationship, Turn, user message, Manifest, context baseline, and proposed reply.
A result with a changed binding is rejected by `complete_turn()`.

### `complete_turn`

```python
complete_turn(
    agent_id: str,
    user_id: str,
    turn_id: str,
    agent_message: str,
    *,
    continuity_assessment=None,
    continuity_result=None,
    delivery_exception=None,
    delivery_disposition=DeliveryDisposition.SHOWN,
    processing_channels=None,
) -> SourceTurnReceipt
```

`SourceTurnReceipt` contains non-transcript acceptance metadata:

- `source_turn_id`
- `relationship_id`
- `source_revision`
- `accepted_at`
- `processing_plan`
- `processing_outcomes`

It does not have `turn_id`, `task_id`, `state`, transcript, or message
fingerprint fields. Use `get_turn()` to read the stored transcript.

There are three delivery branches:

| Disposition | Required evidence | Delivery exception |
|---|---|---|
| `shown` | full `ContinuityEvaluationResult` | absent |
| `overridden` | full `ContinuityEvaluationResult` | required |
| `shown_unreviewed` | no successful continuity result | required |

An identical completion retry returns the existing receipt. A changed terminal
payload raises `TurnTerminalConflictError`, which is a `TurnConflictError`.

### `record_reply_attempt_failure`

```python
record_reply_attempt_failure(
    agent_id: str,
    user_id: str,
    turn_id: str,
    *,
    attempt_number: int,
    stage: ReplyAttemptStage | str,
    capability_descriptor: str,
    failure_classification: str,
) -> ReplyAttemptRecord
```

Valid stages are `generation`, `continuity_evaluation`, and
`delivery_preparation`. This stores sanitized failure metadata, not a draft.
Use `list_reply_attempts(agent_id, user_id, turn_id)` to read the records.

### `abandon_turn`

```python
abandon_turn(
    agent_id: str,
    user_id: str,
    turn_id: str,
    *,
    reason: str,
) -> TurnRecord
```

The required `reason` is a compact machine-readable key. An abandoned Turn keeps
the user message and has no agent message. Retrying with the same reason is
idempotent; a different terminal transition conflicts.

### `record_turn`

```python
record_turn(
    agent_id: str,
    user_id: str,
    user_message: str,
    agent_message: str,
    *,
    turn_id: str | None = None,
    continuity_assessment=None,
    delivery_exception=None,
    delivery_disposition=DeliveryDisposition.SHOWN_UNREVIEWED,
    processing_channels=None,
) -> SourceTurnReceipt
```

Use this only when both messages were already visible before E.R.I.I. received
them. It requires:

- `shown_unreviewed`;
- a `DeliveryExceptionRecord` whose reason is
  `preexisting_visible_exchange`.

It cannot retroactively establish a successful continuity review.

### Read operations

```python
get_turn(agent_id, user_id, turn_id) -> TurnRecord
list_turns(agent_id, user_id, *, status=None) -> list[TurnRecord]
```

`status` is absent or one of `open`, `completed`, and `abandoned`. Listing uses
durable opening order and never crosses the relationship boundary.

### `archive_turn`

```python
archive_turn(
    agent_id: str,
    user_id: str,
    source_turn_id: str,
    *,
    idempotency_key: str,
) -> ArchivalReceipt | ArchivalTombstone
```

Archival requires a configured memory extractor and an existing completed Turn.
The return object uses `archival_id`, `status`, `phase`, `outcome_code`,
`retryable`, and safe operational metadata. It does not expose the transcript.

- `ERIIConfig(async_archival=False)` processes the submission inline.
- `ERIIConfig(async_archival=True)` durably accepts it for later processing.
- A host can call `process_pending(max_tasks=...)` explicitly.
- `ERIIEngine.start()` starts only the legacy `remember()` worker; it is not the
  lifecycle control for reliable Turn archival.

An identical idempotency-key retry resolves to the same archival identity. A key
reused for a different immutable intent raises `ArchivalConflictError`.

### Relationship processing

```python
process_relationship_turn(
    agent_id: str,
    user_id: str,
    source_turn_id: str,
    *,
    processing_mode="normal",
    reprocessing_id: str | None = None,
) -> RelationshipProcessingRun
```

This synchronous operation requires a configured relationship-event extractor.
`processing_mode` is `normal` or `historical_reprocessing`. Historical mode uses
an explicit `reprocessing_id`. The result has `processing_id`, `status`,
`outcome`, decision/event IDs, frozen extraction output, and provenance fields;
it is not an archival task receipt.

## Minimal direct-Python example

```python
from erii import DeliveryDisposition, ERIIEngine

delivery_exception = {
    "exception_record_version": "delivery-exception-record/v1",
    "disposition": "shown_unreviewed",
    "actor_kind": "host_policy",
    "actor_id": "HOST_COMPONENT/v1",
    "reason_code": "availability_fallback",
    "decided_at": "TIMESTAMP",
    "reply_attempt_number": None,
}

with ERIIEngine(storage_dir="DATA_DIR") as engine:
    engine.initialize_relationship("AGENT", "USER", "PERSONA_SOURCE")
    turn = engine.begin_turn(
        "AGENT",
        "USER",
        "VISIBLE_USER_MESSAGE",
        turn_id="HOST_TURN_ID",
    )

    # Generate and display the reply in the host application first.
    receipt = engine.complete_turn(
        "AGENT",
        "USER",
        turn.turn_id,
        "VISIBLE_AGENT_REPLY",
        delivery_disposition=DeliveryDisposition.SHOWN_UNREVIEWED,
        delivery_exception=delivery_exception,
        processing_channels=[],
    )
    assert receipt.source_turn_id == turn.turn_id
```

For an executable archival and recall flow, run:

```bash
python examples/08_turn_lifecycle_integration.py
```

The example is offline, uses a temporary directory, and prints only ASCII-safe
status output.

## REST routes

All non-public routes use the single-owner `X-API-Key` reference-server header.
This header is service-side authority and must not be embedded in browser code.

| Method | Route | Result |
|---|---|---|
| `POST` | `/api/v1/turns/open` | `201`, `{status, turn}` |
| `POST` | `/api/v1/turns` | `{status, receipt}` |
| `GET` | `/api/v1/turns` | `{status, turns}` |
| `GET` | `/api/v1/turns/{turn_id}` | `{status, turn}` |
| `POST` | `/api/v1/turns/{turn_id}/continuity/evaluate` | `{status, result}` |
| `POST` | `/api/v1/turns/{turn_id}/complete` | `{status, receipt}` |
| `POST` | `/api/v1/turns/{turn_id}/reply-attempts` | `201`, `{status, attempt}` |
| `GET` | `/api/v1/turns/{turn_id}/reply-attempts` | `{status, attempts}` |
| `POST` | `/api/v1/turns/{turn_id}/abandon` | `{status, turn}` |
| `POST` | `/api/v1/archivals` | `200` or `202`, `{receipt}` |
| `GET` | `/api/v1/archivals/{archival_id}` | `{receipt}` |

The REST completion model deliberately accepts only:

- a full continuity result with `shown` or `overridden`; or
- `shown_unreviewed` with a delivery exception.

REST `record_turn` also requires a delivery exception. Validation failures use
the stable error contract described in
[Turn error handling](turn-error-handling.md).

## Related documents

- [Advanced Turn usage](turn-advanced-usage.md)
- [Turn error handling](turn-error-handling.md)
- [REST archival boundary ADR](../adr/0068-map-archival-lifecycle-truthfully-at-the-rest-boundary.md)
- [Executable example](../../examples/08_turn_lifecycle_integration.py)
