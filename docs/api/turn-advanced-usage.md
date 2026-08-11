# Turn Lifecycle Advanced Usage

This guide describes behavior implemented by the current Python engine and
reference REST server. It does not introduce a separate pipeline, cache, replay
service, or background scheduler.

Read [Turn Lifecycle API Reference](turn-lifecycle.md) first.

## 1. Stable identities and idempotency

Use a durable host identity for every visible interaction. It should survive an
HTTP retry, process restart, and queue redelivery.

```python
turn_id = f"HOST_CONVERSATION_ID:{HOST_MESSAGE_ID}"
turn = engine.begin_turn(
    "AGENT",
    "USER",
    visible_user_message,
    turn_id=turn_id,
)
```

The identity rules are operation-specific:

| Operation | Identical retry | Changed retry |
|---|---|---|
| `begin_turn` | returns the existing open record | `TurnConflictError` |
| `complete_turn` | returns the existing receipt | `TurnTerminalConflictError` |
| `abandon_turn` | returns the abandoned record | `TurnTerminalConflictError` |
| `record_reply_attempt_failure` | returns the same attempt | reply-attempt conflict |
| `record_turn` | resolves through the same Turn identity | Turn conflict |
| `archive_turn` | resolves to the same archival identity | `ArchivalConflictError` |

An identical completion includes the reply, review record, delivery disposition,
delivery exception, and processing channels. Changing any of these after the
terminal write is a conflict rather than an update.

Do not respond to a conflict by silently minting another ID. First determine
whether the host sent a duplicate delivery or a genuinely different interaction.

## 2. Delivery truth

Continuity quality and delivery truth are separate facts. The Turn record must
describe what happened, not what the host hoped would happen.

### Reviewed ordinary delivery

```python
result = engine.evaluate_reply_continuity(
    "AGENT",
    "USER",
    turn.turn_id,
    proposed_reply,
    persona_context_refs=persona_refs,
    relationship_context_refs=relationship_refs,
)

# Display exactly proposed_reply, then seal it.
receipt = engine.complete_turn(
    "AGENT",
    "USER",
    turn.turn_id,
    proposed_reply,
    continuity_result=result,
    delivery_disposition="shown",
)
```

The evaluator result is not a free-form score. Its binding is verified against
the Turn opening baseline and the exact user/reply text.

### Reviewed but explicitly overridden delivery

Use `overridden` only when a full continuity result exists and the host still
made an explicit exceptional delivery decision. Supply a compatible
`DeliveryExceptionRecord`.

### Unreviewed visible delivery

If no evaluator is configured, or evaluation was not requested, record that
truth explicitly:

```python
exception_record = {
    "exception_record_version": "delivery-exception-record/v1",
    "disposition": "shown_unreviewed",
    "actor_kind": "host_policy",
    "actor_id": "HOST_COMPONENT/v1",
    "reason_code": "availability_fallback",
    "decided_at": "TIMESTAMP",
    "reply_attempt_number": None,
}

engine.complete_turn(
    "AGENT",
    "USER",
    turn.turn_id,
    displayed_reply,
    delivery_disposition="shown_unreviewed",
    delivery_exception=exception_record,
)
```

This branch is not evidence that the reply was continuous. It is evidence that
the reply was visible and its review status is known.

### Already-visible history

`record_turn()` is reserved for messages displayed before ingestion. Its
exception reason must be `preexisting_visible_exchange`.

## 3. Multiple generation attempts

Keep draft text in the host's transient generation layer. Record only safe
failure metadata in E.R.I.I.:

```python
engine.record_reply_attempt_failure(
    "AGENT",
    "USER",
    turn.turn_id,
    attempt_number=1,
    stage="generation",
    capability_descriptor="PROVIDER/MODEL_VERSION",
    failure_classification="temporary_provider_error",
)
```

The three accepted stages are:

- `generation`
- `continuity_evaluation`
- `delivery_preparation`

Do not place draft text, stack traces, provider credentials, or raw provider
responses in `failure_classification` or `capability_descriptor`.

## 4. Host-observed interaction context

`begin_turn()` can capture current situational signals such as activity or
location when the host actually observed them. Public callers may submit only
signals whose source is `host_observed`.

```python
turn = engine.begin_turn(
    "AGENT",
    "USER",
    visible_user_message,
    turn_id="HOST_TURN_ID",
    interaction_context=[
        {
            "signal_id": "activity-gaming",
            "source": "host_observed",
            "signal_type": "activity",
            "value": "gaming",
        }
    ],
)
```

Public callers cannot label a signal `core_derived` or `evaluator_inferred`, or
set internal relationship/Turn/producer scope fields. Contextual voice
activation is derived later against the frozen Turn baseline.

## 5. Processing channels are declarations

The implemented channel values are:

```text
memory_archival
relationship_adjudication
```

They are recorded in the Source Turn processing plan. They do not themselves
replace the explicit processing calls.

When `processing_channels=None`, the engine includes only capabilities actually
configured on that engine:

- memory extractor -> `memory_archival`
- relationship-event extractor -> `relationship_adjudication`

Pass an empty list to declare that no downstream channel was requested.

## 6. Reliable archival lifecycle

### Inline mode

```python
config = ERIIConfig(storage_dir="DATA_DIR", async_archival=False)
engine = ERIIEngine(
    config=config,
    memory_extractor=extractor,
)
receipt = engine.archive_turn(
    "AGENT",
    "USER",
    turn_id,
    idempotency_key="HOST_ARCHIVAL_KEY",
)
```

Inline mode attempts extraction and commit before returning. A successful
no-memory decision is still a completed archival outcome.

### Deferred mode

```python
config = ERIIConfig(storage_dir="DATA_DIR", async_archival=True)
engine = ERIIEngine(config=config, memory_extractor=extractor)

receipt = engine.archive_turn(
    "AGENT",
    "USER",
    turn_id,
    idempotency_key="HOST_ARCHIVAL_KEY",
)

# Host-controlled processing loop.
processed = engine.process_pending(max_tasks=10)
latest = engine.get_archival_receipt(
    "AGENT",
    "USER",
    receipt.archival_id,
)
```

The host decides when to call `process_pending()`. Engine construction does not
start a reliable-archival thread. Always close the engine or use it as a context
manager so leases and queue resources receive cooperative shutdown.

### Important configuration detail

If an explicit `ERIIConfig` is supplied, its `storage_dir` is authoritative.
Set it directly instead of assuming the separate constructor argument will
override it:

```python
config = ERIIConfig(storage_dir="DATA_DIR", async_archival=False)
engine = ERIIEngine(config=config, memory_extractor=extractor)
```

## 7. Relationship processing

Relationship processing is independent from memory archival and from any
provider brand.

```python
run = engine.process_relationship_turn(
    "AGENT",
    "USER",
    completed_turn_id,
    processing_mode="normal",
)
```

It requires a configured `relationship_event_extractor`. The returned
`RelationshipProcessingRun` freezes:

- the Source Turn ID and revision;
- processing mode and extractor descriptor;
- the extraction decision;
- adjudication base and resulting decision/event IDs;
- reflection outcome/failure IDs when reflection is configured;
- timestamps, status, outcome, and a safe failure code.

For an intentional append-only review of historical input:

```python
run = engine.process_relationship_turn(
    "AGENT",
    "USER",
    completed_turn_id,
    processing_mode="historical_reprocessing",
    reprocessing_id="HOST_REPROCESSING_ID",
)
```

There is no `force_reextraction` argument. Relationship processing is
synchronous in the current public method and does not return an archival task.

## 8. Concurrency boundaries

Hosts may receive duplicate and concurrent deliveries. Use these controls:

1. Generate a stable Turn ID before calling E.R.I.I.
2. Keep one immutable visible payload for that identity.
3. Treat an identical terminal result as success.
4. Treat a changed terminal result as an application-level conflict.
5. Use a stable archival idempotency key derived from the host operation, not a
   random value created on every retry.
6. Keep every call scoped with the same `agent_id` and `user_id`.

Do not add an application cache that omits Agent/User/relationship identity from
its key. Such a cache can break relationship isolation even when storage is
correctly scoped.

## 9. REST orchestration

The reference server accepts `X-API-Key`, not a bearer token. It is a
single-owner service key rather than a per-user identity.

```http
POST /api/v1/turns/open HTTP/1.1
X-API-Key: <ERII_API_KEY>
Content-Type: application/json

{
  "agent_id": "AGENT",
  "user_id": "USER",
  "turn_id": "HOST_TURN_ID",
  "user_message": "VISIBLE_USER_MESSAGE"
}
```

The server does not currently expose `process_relationship_turn()` as a Turn
processing route. The available relationship REST endpoints accept already
extracted/adjudication-bound data and are documented by the generated OpenAPI
schema.

Never put the owner key in browser-delivered JavaScript. A browser or mobile
client should call a host backend that holds the key and enforces end-user
authorization.

## 10. Audit and portability

`get_turn()` returns the durable source transcript and its delivery/review truth.
`SourceTurnReceipt` intentionally omits transcript content. Archival receipts
also expose operational status without embedding the transcript.

MemoryPack export carries durable Turn and processing records supported by the
current pack schema. Import still enforces relationship identity and source
provenance; it is not a way to rewrite an existing relationship silently.

## Related documents

- [Turn Lifecycle API Reference](turn-lifecycle.md)
- [Turn error handling](turn-error-handling.md)
- [REST archival boundary ADR](../adr/0068-map-archival-lifecycle-truthfully-at-the-rest-boundary.md)
