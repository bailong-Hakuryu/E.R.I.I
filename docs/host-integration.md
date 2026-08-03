# Host Integration: One Canonical Chat Path

This guide defines the recommended integration path for a host that already
owns chat generation and delivery. It deliberately avoids the deprecated
`remember()` pipeline and transient relationship adjudication.

## The canonical path

For new real-chat integrations, **the only recommended path** is:

```text
record_turn() → archive_turn() / process_relationship_turn() → recall_structured() → export_memory()
```

Here `record_turn()` is the one-shot form of canonical **Turn Recording** when
both messages have already been shown. A live host that still controls
delivery should use the stronger two-phase form:

```text
begin_turn()
  → recall prior relationship context
  → host generates, evaluates, and displays the final reply
  → complete_turn()
  → archive_turn() and/or process_relationship_turn()
  → later recall_structured()
  → export_memory() when portability is needed
```

Both forms write the same Turn Record and Source Transcript ledger. The
shorthand describes the durable flow across turns: accept the exact visible
exchange, derive selected artifacts, recall them in a later turn, and keep an
export path.

## Responsibilities at each boundary

| Boundary | Kernel responsibility | Host responsibility |
| --- | --- | --- |
| Relationship setup | Bind an immutable Character Blueprint to one independent `Agent × User` relationship | Supply stable internal IDs and authorized persona source text |
| Turn Recording | Preserve exact visible User/Agent text, stable identity, terminal state, and processing plan | Call before/after delivery at the correct point; never save an undisplayed draft as the final reply |
| Memory archival | Validate message-level citations and atomically publish Timeline/Memory artifacts | Inject a versioned `MemoryExtractorV1`; explicitly call `archive_turn()` and `process_pending()` or `drain()` |
| Relationship processing | Freeze extraction, verify evidence, adjudicate candidates, and append accepted events | Inject a versioned `RelationshipEventExtractorV1`; inspect the returned run outcome |
| Recall | Enforce relationship scope, audience, authority tier, budget, and provenance projection | Choose `agent_private` or `public`; place rendered data below host policy |
| Export | Produce a relationship-scoped MemoryPack | Protect, retain, transfer, and delete exported files according to product policy |

The memory and relationship channels are independent. A completed archival
receipt does not imply that a Relationship Event was accepted, and an accepted
Relationship Event does not imply that a retrievable MemoryNode exists.

## 1. Initialize one relationship

Initialize once for each `Agent × User`. Reusing the same character source does
not merge the two relationships.

```python
from erii import ERIIEngine


engine = ERIIEngine(
    storage_dir="./erii-data",
    memory_extractor=my_memory_extractor,
    relationship_event_extractor=my_relationship_extractor,
)
engine.initialize_relationship(
    "agent_lumi",
    "user_chen",
    persona_source=persona_markdown,
    source_format="text/markdown",
    source_name="lumi.md",
)
```

Use stable application IDs, not display names, paths, or model-generated
strings.

## 2. Record the exact delivered turn

### Live delivery: two phases

Open the Turn before generation:

```python
opened = engine.begin_turn(
    "agent_lumi",
    "user_chen",
    user_text,
    turn_id=stable_turn_id,
)
```

Recall only history that existed before the new reply:

```python
from erii import RecallOptions, RecallRequest


prior = engine.recall_structured(
    RecallRequest(
        agent_id="agent_lumi",
        user_id="user_chen",
        query=user_text,
        audience="agent_private",
        options=RecallOptions(
            persona_delivery="full",
            reinforce=False,
        ),
    )
)
long_term_context = engine.render_recall(prior)
```

The host generates and displays a final reply using its own model and delivery
policy. Complete the Turn with that exact text. A normal `shown` completion
requires the full Turn-bound result from `evaluate_reply_continuity()`. If no
evaluator exists, record the truth as `shown_unreviewed` with a stable
`DeliveryExceptionRecord`; do not pretend that review succeeded.

```python
source = engine.complete_turn(
    "agent_lumi",
    "user_chen",
    opened.turn_id,
    exact_visible_reply,
    continuity_assessment=turn_bound_assessment,
    delivery_disposition="shown",
)
```

If no reply was shown, call `abandon_turn()` instead. Keep retryable failures
open and retry with the same identity.

### Already-visible exchange: one shot

If both visible messages already exist, use `record_turn()`:

```python
source = engine.record_turn(
    "agent_lumi",
    "user_chen",
    user_text,
    exact_visible_reply,
    turn_id=stable_turn_id,
    delivery_exception=preexisting_exchange_exception,
)
```

This is necessarily a `shown_unreviewed` preexisting exchange. Post-hoc
recording cannot fabricate a pre-delivery continuity review.

## 3. Derive long-term artifacts explicitly

Submit the completed Source Turn to the configured memory extractor:

```python
submission = engine.archive_turn(
    "agent_lumi",
    "user_chen",
    source.source_turn_id,
    idempotency_key=f"archive:{source.source_turn_id}",
)
engine.process_pending(max_tasks=20)
archival = engine.get_archival_receipt(
    "agent_lumi",
    "user_chen",
    submission.archival_id,
)
```

Or use `ERIIConfig(async_archival=False)` when the request should process
inline. Engine construction never starts a hidden archival worker.

Run relationship processing separately:

```python
relationship_run = engine.process_relationship_turn(
    "agent_lumi",
    "user_chen",
    source.source_turn_id,
)
print(relationship_run.status, relationship_run.outcome)
```

Check the archival receipt and relationship-run outcome. A method returning
without raising is not proof that it created a useful artifact or accepted
every candidate.

If the visible reply was `overridden` or `shown_unreviewed`, the transcript
still records what happened, but Agent-side evidence remains quarantined from
ordinary automatic memory and relationship authority. This rule depends on
delivery disposition, not whether the reply was kind, angry, rejecting, or
painful.

## 4. Recall on a later turn

Use `recall_structured()` as the primary read interface:

```python
result = engine.recall_structured(
    RecallRequest(
        agent_id="agent_lumi",
        user_id="user_chen",
        query=next_user_message,
        audience="agent_private",
        options=RecallOptions(
            persona_delivery="full",
            reinforce=False,
        ),
    )
)
prompt_context = engine.render_recall(result)
```

Use a fresh `audience="public"` request for user-visible inspection. Never
render Agent-private context and then attempt to sanitize it with string
replacement.

The structured result is the source of truth for scope, authority tier,
omissions, and provenance. Rendering is a deterministic presentation step; it
does not write history.

## 5. Preserve portability

Export the exact relationship:

```python
engine.export_memory(
    "agent_lumi",
    "user_chen",
    export_path="./exports/lumi-user-chen.erii",
)
```

MemoryPacks that carry Source Turns and modern provenance remain bound to their
original Agent, User, and relationship identities. `overwrite=True` is not
permission to remap a private relationship to another user.

MemoryPack is not the same as a verified Lifecycle Backup. Use
`DataLifecycleCoordinator.inspect() → plan() → execute()` for backup, restore,
upgrade, fresh import, erasure, and rebuild operations.

## Shutdown and failure handling

Call `drain()` only when the host wants to process a bounded snapshot of
accepted archival work. Then close explicitly:

```python
drain_report = engine.drain(timeout=5.0)
shutdown_report = engine.close(timeout=1.0)
```

`close()` does not silently drain reliable archival. Persist IDs and receipts
so process restarts can resume the same operation rather than create a new
history.

## Not part of this path

- `remember()` and transient `adjudicate_relationship_candidates()` are
  deprecated compatibility interfaces, not starting points for new hosts.
- `record_relationship_event()` is an Advanced trusted-host correction/import
  interface, not a substitute for processing model output.
- The reference REST service is a protocol example, not a complete
  multi-tenant product backend.
- DeepSeek, raw model thinking, Character Deliberation, Relationship
  Consequence, and Narrative Tension are not required by this v0.4 path.
- Authentication, per-user authorization, TLS, encryption, quotas, and tenant
  isolation remain product-host responsibilities.

See [API Stability](api-stability.md) before adopting additional surfaces, and
[Data Lifecycle](data-lifecycle.md) before changing real stored data.
