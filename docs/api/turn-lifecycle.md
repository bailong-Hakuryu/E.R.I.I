# Turn Lifecycle API Reference

**Version:** 0.5.0a2  
**Status:** Stable  
**Audience:** Host Integration Developers

---

## Overview

The Turn Lifecycle API provides the canonical path for recording and processing chat interactions in E.R.I.I. It ensures that every conversation turn is:

- **Traceable**: Each turn has a durable source record with content fingerprint
- **Relationship-scoped**: Turns belong to exactly one `Agent × User` relationship
- **Process-safe**: Concurrent writes are protected; retries are idempotent
- **Portable**: Turns serialize into MemoryPack format for migration

---

## Architecture

### Two-Phase Recording (Recommended)

```
begin_turn()        ← Opens turn, captures user message
    ↓
recall_structured() ← Fetch prior context (before new reply exists)
    ↓
[Host generates reply]
    ↓
complete_turn()     ← Seals agent reply and processing plan
    ↓
archive_turn()      ← Extract memories asynchronously
```

**Why two-phase?**
- Recall sees only pre-reply context
- Host controls generation and review
- Processing happens after delivery

### One-Shot Recording (Alternative)

```python
record_turn()  # Both messages already shown
    ↓
archive_turn()
```

Use when both messages are already delivered (e.g., importing historical data).

---

## API Reference

### begin_turn()

Opens a new turn and persists the exact user message.

```python
def begin_turn(
    self,
    agent_id: str,
    user_id: str,
    user_message: str,
    *,
    turn_id: Optional[str] = None,
    interaction_context: Sequence[InteractionContextSignal] = (),
) -> TurnRecord
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | str | Yes | Stable agent identifier (not display name) |
| `user_id` | str | Yes | Stable user identifier |
| `user_message` | str | Yes | Exact visible user message content |
| `turn_id` | str | No | Stable turn ID (UUID if omitted) |
| `interaction_context` | Sequence | No | Host-observed context signals |

**Returns:** `TurnRecord` with status `OPEN`

**Raises:**
- `RelationshipNotFoundError` - Relationship not initialized
- `TurnConflictError` - Turn ID already exists with different content
- `ValueError` - Invalid parameters

**Example:**

```python
from erii import ERIIEngine

engine = ERIIEngine(storage_dir="./data")

# Initialize relationship first
engine.initialize_relationship(
    "agent_lumi",
    "user_chen",
    persona_source="...",
    source_format="text/markdown"
)

# Open turn
turn = engine.begin_turn(
    "agent_lumi",
    "user_chen",
    "今天天气真好！",
    turn_id="turn-2026-08-11-001"
)

print(f"Turn opened: {turn.turn_id}")
print(f"Status: {turn.status}")
# Output:
# Turn opened: turn-2026-08-11-001
# Status: TurnStatus.OPEN
```

**Best Practices:**
- ✅ Use stable, application-generated turn IDs
- ✅ Call before generating reply
- ✅ Store turn_id for later completion
- ❌ Don't use display names or temporary IDs
- ❌ Don't open multiple turns for same ID

---

### complete_turn()

Seals the agent reply and marks the turn as completed.

```python
def complete_turn(
    self,
    agent_id: str,
    user_id: str,
    turn_id: str,
    agent_message: str,
    *,
    continuity_assessment: Optional[ContinuityAssessment] = None,
    continuity_result: Optional[ContinuityEvaluationResult] = None,
    delivery_exception: Optional[DeliveryExceptionRecord] = None,
    delivery_disposition: Optional[DeliveryDisposition] = None,
    processing_channels: Optional[
        Sequence[Union[SourceProcessingChannel, str]]
    ] = None,
) -> TurnReceipt
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | str | Yes | Same as begin_turn |
| `user_id` | str | Yes | Same as begin_turn |
| `turn_id` | str | Yes | Same as begin_turn |
| `agent_message` | str | Yes | Exact visible agent reply |
| `continuity_assessment` | ContinuityAssessment | No | Continuity evaluation status |
| `continuity_result` | ContinuityEvaluationResult | No | Detailed continuity data |
| `delivery_exception` | DeliveryExceptionRecord | No | If reply shown unreviewed |
| `delivery_disposition` | DeliveryDisposition | No | How reply was delivered |
| `processing_channels` | Sequence | No | Which processing to enable |

**Returns:** `TurnReceipt` with final status and content fingerprints

**Raises:**
- `TurnNotFoundError` - Turn doesn't exist
- `TurnConflictError` - Turn already completed with different content
- `TurnTerminalConflictError` - Turn in terminal state (completed/abandoned)

**Example:**

```python
# After generating reply
receipt = engine.complete_turn(
    "agent_lumi",
    "user_chen",
    "turn-2026-08-11-001",
    "是啊！我们可以出去走走。",
    delivery_disposition=DeliveryDisposition.SHOWN,
    processing_channels=[
        SourceProcessingChannel.MEMORY_EXTRACTION,
        SourceProcessingChannel.RELATIONSHIP_EVENT_EXTRACTION
    ]
)

print(f"Turn completed: {receipt.turn_id}")
print(f"User message fingerprint: {receipt.user_message_fingerprint}")
print(f"Agent message fingerprint: {receipt.agent_message_fingerprint}")
```

**Best Practices:**
- ✅ Only seal messages actually shown to user
- ✅ Specify processing channels explicitly
- ✅ Include continuity assessment if available
- ❌ Don't seal draft/rejected replies
- ❌ Don't modify content after sealing

---

### abandon_turn()

Abandons an open turn without recording an agent reply.

```python
def abandon_turn(
    self,
    agent_id: str,
    user_id: str,
    turn_id: str,
) -> TurnReceipt
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | str | Yes | Same as begin_turn |
| `user_id` | str | Yes | Same as begin_turn |
| `turn_id` | str | Yes | Turn to abandon |

**Returns:** `TurnReceipt` with status `ABANDONED`

**Raises:**
- `TurnNotFoundError` - Turn doesn't exist
- `TurnTerminalConflictError` - Turn already in terminal state

**Example:**

```python
try:
    # Try to generate reply
    reply = generate_reply(user_message)
except Exception as e:
    # Generation failed, abandon turn
    receipt = engine.abandon_turn(
        "agent_lumi",
        "user_chen",
        "turn-2026-08-11-001"
    )
    print(f"Turn abandoned: {receipt.turn_id}")
```

**Use Cases:**
- Generation failures
- User cancellation
- Safety filter rejection
- Connection loss before reply

---

### record_turn()

One-shot recording when both messages are already delivered.

```python
def record_turn(
    self,
    agent_id: str,
    user_id: str,
    user_message: str,
    agent_message: str,
    *,
    turn_id: Optional[str] = None,
    interaction_context: Sequence[InteractionContextSignal] = (),
    continuity_assessment: Optional[ContinuityAssessment] = None,
    continuity_result: Optional[ContinuityEvaluationResult] = None,
    delivery_exception: Optional[DeliveryExceptionRecord] = None,
    delivery_disposition: Optional[DeliveryDisposition] = None,
    processing_channels: Optional[
        Sequence[Union[SourceProcessingChannel, str]]
    ] = None,
) -> TurnReceipt
```

**Parameters:** Combination of begin_turn + complete_turn

**Returns:** `TurnReceipt` with status `COMPLETED`

**Example:**

```python
# Import historical conversation
receipt = engine.record_turn(
    "agent_lumi",
    "user_chen",
    user_message="你好！",
    agent_message="你好！很高兴认识你。",
    turn_id="historical-turn-001",
    delivery_disposition=DeliveryDisposition.SHOWN
)
```

**Use Cases:**
- Importing historical data
- External chat systems integration
- Batch recording

---

### get_turn()

Retrieves a single turn by ID.

```python
def get_turn(
    self,
    agent_id: str,
    user_id: str,
    turn_id: str,
) -> TurnRecord
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | str | Yes | Agent identifier |
| `user_id` | str | Yes | User identifier |
| `turn_id` | str | Yes | Turn identifier |

**Returns:** `TurnRecord`

**Raises:**
- `TurnNotFoundError` - Turn doesn't exist in this relationship

**Example:**

```python
turn = engine.get_turn(
    "agent_lumi",
    "user_chen",
    "turn-2026-08-11-001"
)

print(f"Status: {turn.status}")
print(f"User: {turn.transcript.user_message.content}")
if turn.transcript.agent_message:
    print(f"Agent: {turn.transcript.agent_message.content}")
```

---

### list_turns()

Lists all turns for a relationship.

```python
def list_turns(
    self,
    agent_id: str,
    user_id: str,
    *,
    status: Optional[Union[TurnStatus, str]] = None,
) -> List[TurnRecord]
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | str | Yes | Agent identifier |
| `user_id` | str | Yes | User identifier |
| `status` | TurnStatus | No | Filter by status |

**Returns:** List of `TurnRecord` in opening order

**Example:**

```python
from erii import TurnStatus

# All turns
all_turns = engine.list_turns("agent_lumi", "user_chen")
print(f"Total turns: {len(all_turns)}")

# Only completed turns
completed = engine.list_turns(
    "agent_lumi",
    "user_chen",
    status=TurnStatus.COMPLETED
)
print(f"Completed: {len(completed)}")

# Only open turns
open_turns = engine.list_turns(
    "agent_lumi",
    "user_chen",
    status=TurnStatus.OPEN
)
print(f"Open: {len(open_turns)}")
```

---

### archive_turn()

Asynchronously extracts memories from a completed turn.

```python
def archive_turn(
    self,
    agent_id: str,
    user_id: str,
    turn_id: str,
    *,
    accepted_relationship_events: Sequence[RelationshipEvent] = (),
    enable_persona_context: bool = True,
) -> ArchivalSubmission
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | str | Yes | Agent identifier |
| `user_id` | str | Yes | User identifier |
| `turn_id` | str | Yes | Completed turn ID |
| `accepted_relationship_events` | Sequence | No | Pre-adjudicated events |
| `enable_persona_context` | bool | No | Include persona in recall |

**Returns:** `ArchivalSubmission` with task ID

**Raises:**
- `ArchivalSubmissionError` - Turn not archivable

**Example:**

```python
# Submit for archival
submission = engine.archive_turn(
    "agent_lumi",
    "user_chen",
    "turn-2026-08-11-001"
)

print(f"Archival task: {submission.task_id}")
print(f"State: {submission.state}")

# Wait for completion (if synchronous config)
# Task processes in background by default
```

**Processing:**
- Extracts memory nodes
- Updates recall index
- Processes relationship events
- Runs asynchronously by default

---

### process_relationship_turn()

Extracts and adjudicates relationship events from a turn.

```python
def process_relationship_turn(
    self,
    agent_id: str,
    user_id: str,
    turn_id: str,
    *,
    force_reextraction: bool = False,
) -> RelationshipTurnProcessingResult
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agent_id` | str | Yes | Agent identifier |
| `user_id` | str | Yes | User identifier |
| `turn_id` | str | Yes | Turn to process |
| `force_reextraction` | bool | No | Re-run extraction |

**Returns:** `RelationshipTurnProcessingResult`

**Example:**

```python
result = engine.process_relationship_turn(
    "agent_lumi",
    "user_chen",
    "turn-2026-08-11-001"
)

print(f"Accepted events: {len(result.accepted_events)}")
for event in result.accepted_events:
    print(f"  - {event.event_type}: {event.description}")
```

---

## Data Models

### TurnRecord

```python
@dataclass
class TurnRecord:
    turn_id: str
    status: TurnStatus
    transcript: TurnTranscript
    context_snapshot: Optional[TurnContextSnapshot]
    opened_at: str
    completed_at: Optional[str]
```

### TurnStatus

```python
class TurnStatus(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
```

### TurnReceipt

```python
@dataclass
class TurnReceipt:
    turn_id: str
    status: TurnStatus
    user_message_fingerprint: str
    agent_message_fingerprint: Optional[str]
    processing_plan: ProcessingPlan
```

---

## Error Handling

### Exception Hierarchy

```
TurnLifecycleError (base)
├── TurnNotFoundError
├── TurnConflictError
└── TurnTerminalConflictError
```

### Common Errors

#### TurnNotFoundError

**Cause:** Turn ID doesn't exist in this relationship

**Example:**
```python
try:
    turn = engine.get_turn("agent", "user", "nonexistent-turn")
except TurnNotFoundError as e:
    print(f"Turn not found: {e}")
    # Handle: create new turn or use different ID
```

#### TurnConflictError

**Cause:** Turn ID exists with different content

**Example:**
```python
try:
    engine.begin_turn("agent", "user", "Hello", turn_id="turn-1")
    engine.begin_turn("agent", "user", "Hi", turn_id="turn-1")  # Different content!
except TurnConflictError as e:
    print(f"Content mismatch: {e}")
    # Handle: use new turn_id or check for retry
```

**Recovery:** Check if this is a retry with same content, or use new ID.

#### TurnTerminalConflictError

**Cause:** Turn already completed or abandoned

**Example:**
```python
try:
    engine.complete_turn("agent", "user", "turn-1", "Reply")
    engine.complete_turn("agent", "user", "turn-1", "Another reply")  # Already terminal!
except TurnTerminalConflictError as e:
    print(f"Turn already sealed: {e}")
    # Handle: this is not retryable, use new turn
```

**Recovery:** Cannot modify terminal turns. Create new turn if needed.

---

## Best Practices

### 1. Use Stable IDs

```python
# ✅ Good: Application-controlled ID
turn_id = f"turn-{conversation_id}-{sequence_number}"

# ❌ Bad: Temporary or display-based ID
turn_id = f"turn-{datetime.now()}"  # Not stable across retries
```

### 2. Separate Recall and Generation

```python
# ✅ Good: Recall before new content exists
turn = engine.begin_turn(agent_id, user_id, user_message)
context = engine.recall_structured(...)  # Prior context only
reply = generate_with_context(user_message, context)
engine.complete_turn(agent_id, user_id, turn.turn_id, reply)

# ❌ Bad: Recall after completion
turn = engine.record_turn(...)  # New content already exists
context = engine.recall_structured(...)  # Sees its own turn!
```

### 3. Handle Retries Idempotently

```python
def record_safely(agent_id, user_id, turn_id, user_msg, agent_msg):
    try:
        return engine.complete_turn(agent_id, user_id, turn_id, agent_msg)
    except TurnConflictError as e:
        # Check if this is a retry with same content
        turn = engine.get_turn(agent_id, user_id, turn_id)
        if turn.transcript.agent_message.content == agent_msg:
            return turn  # Idempotent retry
        else:
            raise  # Different content, real conflict
```

### 4. Seal Only Shown Messages

```python
# ✅ Good: Only seal what user actually saw
if safety_check_passed and user_saw_reply:
    engine.complete_turn(..., delivery_disposition=DeliveryDisposition.SHOWN)

# ❌ Bad: Seal rejected/draft content
draft = generate_reply()
if not safety_check(draft):
    engine.complete_turn(..., agent_message=draft)  # Don't seal bad content!
```

### 5. Specify Processing Channels

```python
# ✅ Good: Explicit channels
engine.complete_turn(
    ...,
    processing_channels=[
        SourceProcessingChannel.MEMORY_EXTRACTION,
        SourceProcessingChannel.RELATIONSHIP_EVENT_EXTRACTION
    ]
)

# ⚠️ Okay: Default channels (memory only)
engine.complete_turn(...)  # Uses defaults
```

---

## Performance Considerations

### Concurrent Access

Turn recording is **concurrency-safe**:
- Multiple `begin_turn()` with same ID → first wins, others get TurnConflictError
- Multiple `complete_turn()` with same ID → first wins, others get TurnTerminalConflictError
- Exactly-one-winner guarantee

### Batch Operations

For importing historical data:

```python
# Process in batches
for batch in chunks(historical_turns, size=100):
    with engine.storage.batch_mode():  # If supported
        for turn_data in batch:
            engine.record_turn(...)
```

### Async Processing

Archival runs asynchronously by default:

```python
# Non-blocking
submission = engine.archive_turn(...)
# Returns immediately, processes in background

# Blocking (if needed)
engine = ERIIEngine(
    storage_dir="...",
    config=ERIIConfig(async_archival=False)
)
submission = engine.archive_turn(...)  # Blocks until complete
```

---

## Migration and Portability

### Export Turns

```python
# Export entire relationship
pack = engine.export_memory("agent_lumi", "user_chen")

# pack.turns contains all turn transcripts with fingerprints
for turn in pack.turns:
    print(f"{turn.turn_id}: {turn.user_message_fingerprint}")
```

### Import Turns

```python
# Import into new relationship
engine.import_memory(pack, agent_id="agent_lumi", user_id="new_user")
# All turns restored with source provenance
```

---

## Troubleshooting

### Turn Not Archiving

**Symptom:** `archive_turn()` succeeds but no memories extracted

**Causes:**
1. Turn not completed
2. Processing channels not enabled
3. Archival worker not running

**Check:**
```python
turn = engine.get_turn(agent_id, user_id, turn_id)
print(f"Status: {turn.status}")  # Must be COMPLETED

# Check archival status
from erii import ArchivalStatus
status = engine.storage.get_archival_status(turn_id)
print(f"Archival: {status}")
```

### Duplicate Turn IDs

**Symptom:** TurnConflictError on different machines

**Cause:** Turn ID not globally unique

**Fix:**
```python
import uuid

# Include machine/session ID
turn_id = f"turn-{machine_id}-{uuid.uuid4()}"
```

### Missing Context

**Symptom:** Recall returns empty context after recording turns

**Cause:** Archival not completed yet

**Fix:**
```python
# Wait for archival (development only)
engine = ERIIEngine(
    storage_dir="...",
    config=ERIIConfig(async_archival=False)
)

# Or check archival status
submission = engine.archive_turn(...)
# Poll submission.state until COMPLETED
```

---

## See Also

- [Host Integration Guide](../host-integration.md)
- [Recall API Reference](recall-api.md)
- [Relationship API Reference](relationship-api.md)
- [Memory Pack Format](../memorypack-format.md)

---

**Last Updated:** 2026-08-11  
**API Stability:** Golden (v0.5.0+)
