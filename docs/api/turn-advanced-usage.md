# Turn Lifecycle Advanced Usage

**Version:** 0.5.0a2  
**Audience:** Experienced Developers

---

## Overview

This guide covers advanced usage patterns for the Turn Lifecycle API, including:
- Concurrent turn processing
- Batch operations
- Custom processing pipelines
- Performance optimization
- Advanced error recovery

---

## Advanced Patterns

### 1. Concurrent Turn Processing

Handle multiple users simultaneously with thread-safe turn recording:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from erii import ERIIEngine
import threading

class ConcurrentTurnProcessor:
    """Thread-safe turn processor for multiple users."""
    
    def __init__(self, engine: ERIIEngine):
        self.engine = engine
        self._locks = {}
        self._locks_lock = threading.Lock()
    
    def _get_relationship_lock(self, agent_id: str, user_id: str):
        """Get or create lock for relationship."""
        key = (agent_id, user_id)
        with self._locks_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]
    
    def process_turn(
        self,
        agent_id: str,
        user_id: str,
        user_message: str,
        agent_message: str,
        turn_id: str
    ):
        """Process turn for one relationship (thread-safe)."""
        # Lock per relationship, not globally
        lock = self._get_relationship_lock(agent_id, user_id)
        
        with lock:
            return self.engine.record_turn(
                agent_id,
                user_id,
                user_message,
                agent_message,
                turn_id=turn_id
            )
    
    def process_batch(self, turns: list):
        """Process multiple turns concurrently."""
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(
                    self.process_turn,
                    **turn_data
                ): turn_data['turn_id']
                for turn_data in turns
            }
            
            results = {}
            for future in as_completed(futures):
                turn_id = futures[future]
                try:
                    results[turn_id] = future.result()
                except Exception as e:
                    results[turn_id] = {"error": str(e)}
            
            return results

# Usage
processor = ConcurrentTurnProcessor(engine)

turns = [
    {
        "agent_id": "agent1",
        "user_id": "user1",
        "user_message": "Hello",
        "agent_message": "Hi!",
        "turn_id": "turn-1"
    },
    {
        "agent_id": "agent1",
        "user_id": "user2",
        "user_message": "Hey",
        "agent_message": "Hey there!",
        "turn_id": "turn-2"
    }
]

results = processor.process_batch(turns)
```

### 2. Streaming Turn Recording

For real-time chat with streaming responses:

```python
class StreamingTurnRecorder:
    """Record turns with streaming agent messages."""
    
    def __init__(self, engine: ERIIEngine):
        self.engine = engine
    
    def record_streaming_turn(
        self,
        agent_id: str,
        user_id: str,
        user_message: str,
        turn_id: str,
        message_generator  # Generator yielding message chunks
    ):
        """Record turn with streaming response."""
        # Open turn immediately
        turn = self.engine.begin_turn(
            agent_id,
            user_id,
            user_message,
            turn_id=turn_id
        )
        
        # Collect streamed message
        agent_message_parts = []
        for chunk in message_generator:
            agent_message_parts.append(chunk)
            yield chunk  # Pass through to client
        
        # Complete turn with full message
        agent_message = "".join(agent_message_parts)
        receipt = self.engine.complete_turn(
            agent_id,
            user_id,
            turn_id,
            agent_message
        )
        
        return receipt

# Usage
recorder = StreamingTurnRecorder(engine)

def llm_stream():
    """Simulate streaming LLM response."""
    for word in ["Hello", " ", "there", "!"]:
        yield word

# Stream to user and record
for chunk in recorder.record_streaming_turn(
    "agent1",
    "user1",
    "Hi",
    "turn-stream-1",
    llm_stream()
):
    print(chunk, end="", flush=True)
```

### 3. Turn Replay and Audit

Replay historical conversations for debugging or auditing:

```python
class TurnReplayer:
    """Replay and audit turn history."""
    
    def __init__(self, engine: ERIIEngine):
        self.engine = engine
    
    def replay_conversation(
        self,
        agent_id: str,
        user_id: str,
        start_turn: str = None,
        end_turn: str = None
    ):
        """Replay conversation turns in order."""
        turns = self.engine.list_turns(agent_id, user_id)
        
        # Filter by range if specified
        if start_turn or end_turn:
            start_idx = 0
            end_idx = len(turns)
            
            for i, turn in enumerate(turns):
                if start_turn and turn.turn_id == start_turn:
                    start_idx = i
                if end_turn and turn.turn_id == end_turn:
                    end_idx = i + 1
                    break
            
            turns = turns[start_idx:end_idx]
        
        # Replay
        conversation = []
        for turn in turns:
            conversation.append({
                "turn_id": turn.turn_id,
                "user": turn.transcript.user_message.content,
                "agent": turn.transcript.agent_message.content if turn.transcript.agent_message else None,
                "status": turn.status,
                "timestamp": turn.opened_at
            })
        
        return conversation
    
    def audit_turn_integrity(
        self,
        agent_id: str,
        user_id: str,
        turn_id: str
    ):
        """Verify turn content integrity."""
        import hashlib
        
        turn = self.engine.get_turn(agent_id, user_id, turn_id)
        
        # Verify user message fingerprint
        user_content = turn.transcript.user_message.content
        user_fingerprint = hashlib.sha256(user_content.encode()).hexdigest()
        
        # Verify agent message fingerprint
        if turn.transcript.agent_message:
            agent_content = turn.transcript.agent_message.content
            agent_fingerprint = hashlib.sha256(agent_content.encode()).hexdigest()
        else:
            agent_fingerprint = None
        
        return {
            "turn_id": turn_id,
            "user_message_valid": True,  # Would check against stored fingerprint
            "agent_message_valid": True if agent_fingerprint else None,
            "status": turn.status,
            "tampered": False  # Would detect modifications
        }

# Usage
replayer = TurnReplayer(engine)

# Replay entire conversation
conversation = replayer.replay_conversation("agent1", "user1")
for turn in conversation:
    print(f"[{turn['timestamp']}]")
    print(f"User: {turn['user']}")
    if turn['agent']:
        print(f"Agent: {turn['agent']}")
    print()

# Audit specific turn
audit = replayer.audit_turn_integrity("agent1", "user1", "turn-1")
print(f"Integrity check: {audit}")
```

### 4. Custom Processing Pipelines

Implement custom processing logic for turns:

```python
from dataclasses import dataclass
from typing import Callable, List

@dataclass
class ProcessingStep:
    """A step in the processing pipeline."""
    name: str
    processor: Callable
    enabled: bool = True

class TurnProcessingPipeline:
    """Custom turn processing pipeline."""
    
    def __init__(self, engine: ERIIEngine):
        self.engine = engine
        self.steps: List[ProcessingStep] = []
    
    def add_step(self, name: str, processor: Callable, enabled: bool = True):
        """Add processing step to pipeline."""
        self.steps.append(ProcessingStep(name, processor, enabled))
    
    def process_turn(
        self,
        agent_id: str,
        user_id: str,
        user_message: str,
        agent_message: str,
        turn_id: str
    ):
        """Process turn through pipeline."""
        # Record turn
        receipt = self.engine.record_turn(
            agent_id,
            user_id,
            user_message,
            agent_message,
            turn_id=turn_id
        )
        
        # Run through pipeline
        context = {
            "receipt": receipt,
            "agent_id": agent_id,
            "user_id": user_id,
            "turn_id": turn_id
        }
        
        for step in self.steps:
            if not step.enabled:
                continue
            
            try:
                context = step.processor(self.engine, context)
            except Exception as e:
                print(f"Pipeline step {step.name} failed: {e}")
                context[f"{step.name}_error"] = str(e)
        
        return context

# Define custom processors
def extract_entities(engine, context):
    """Extract entities from turn."""
    # Custom entity extraction logic
    context["entities"] = ["entity1", "entity2"]
    return context

def sentiment_analysis(engine, context):
    """Analyze sentiment."""
    # Custom sentiment analysis
    context["sentiment"] = "positive"
    return context

def custom_memory_tagging(engine, context):
    """Add custom tags to memories."""
    # Custom tagging logic
    context["tags"] = ["important", "personal"]
    return context

# Build pipeline
pipeline = TurnProcessingPipeline(engine)
pipeline.add_step("extract_entities", extract_entities)
pipeline.add_step("sentiment_analysis", sentiment_analysis)
pipeline.add_step("custom_tagging", custom_memory_tagging)

# Process turn
result = pipeline.process_turn(
    "agent1",
    "user1",
    "I love coffee",
    "Great! I'll remember that.",
    "turn-pipeline-1"
)

print(f"Entities: {result.get('entities')}")
print(f"Sentiment: {result.get('sentiment')}")
```

### 5. Turn Deduplication

Prevent duplicate turn recording:

```python
import hashlib

class DeduplicatingTurnRecorder:
    """Turn recorder with deduplication."""
    
    def __init__(self, engine: ERIIEngine):
        self.engine = engine
        self.seen_fingerprints = set()
    
    def _make_fingerprint(
        self,
        agent_id: str,
        user_id: str,
        user_message: str,
        agent_message: str
    ) -> str:
        """Create content fingerprint for deduplication."""
        content = f"{agent_id}|{user_id}|{user_message}|{agent_message}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def record_if_unique(
        self,
        agent_id: str,
        user_id: str,
        user_message: str,
        agent_message: str,
        turn_id: str = None
    ):
        """Record turn only if content is unique."""
        fingerprint = self._make_fingerprint(
            agent_id,
            user_id,
            user_message,
            agent_message
        )
        
        if fingerprint in self.seen_fingerprints:
            return {
                "status": "DUPLICATE",
                "fingerprint": fingerprint
            }
        
        # Record turn
        receipt = self.engine.record_turn(
            agent_id,
            user_id,
            user_message,
            agent_message,
            turn_id=turn_id
        )
        
        # Mark as seen
        self.seen_fingerprints.add(fingerprint)
        
        return {
            "status": "RECORDED",
            "receipt": receipt,
            "fingerprint": fingerprint
        }

# Usage
dedup_recorder = DeduplicatingTurnRecorder(engine)

# First recording
result1 = dedup_recorder.record_if_unique(
    "agent1", "user1", "Hello", "Hi!", "turn-1"
)
print(result1["status"])  # RECORDED

# Duplicate attempt
result2 = dedup_recorder.record_if_unique(
    "agent1", "user1", "Hello", "Hi!", "turn-2"
)
print(result2["status"])  # DUPLICATE
```

---

## Performance Optimization

### 1. Batch Turn Archival

Archive multiple turns efficiently:

```python
def batch_archive_turns(
    engine: ERIIEngine,
    agent_id: str,
    user_id: str,
    turn_ids: List[str]
):
    """Archive multiple turns in batch."""
    submissions = []
    
    for turn_id in turn_ids:
        try:
            submission = engine.archive_turn(agent_id, user_id, turn_id)
            submissions.append(submission)
        except Exception as e:
            print(f"Failed to archive {turn_id}: {e}")
    
    return submissions

# Usage
turn_ids = ["turn-1", "turn-2", "turn-3"]
submissions = batch_archive_turns(engine, "agent1", "user1", turn_ids)
print(f"Archived {len(submissions)} turns")
```

### 2. Lazy Turn Loading

Load turn data only when needed:

```python
class LazyTurn:
    """Lazy-loaded turn wrapper."""
    
    def __init__(
        self,
        engine: ERIIEngine,
        agent_id: str,
        user_id: str,
        turn_id: str
    ):
        self.engine = engine
        self.agent_id = agent_id
        self.user_id = user_id
        self.turn_id = turn_id
        self._turn = None
    
    @property
    def turn(self):
        """Load turn on first access."""
        if self._turn is None:
            self._turn = self.engine.get_turn(
                self.agent_id,
                self.user_id,
                self.turn_id
            )
        return self._turn
    
    @property
    def user_message(self):
        return self.turn.transcript.user_message.content
    
    @property
    def agent_message(self):
        if self.turn.transcript.agent_message:
            return self.turn.transcript.agent_message.content
        return None

# Usage - turn data not loaded until accessed
lazy_turn = LazyTurn(engine, "agent1", "user1", "turn-1")
# ... do other work ...
print(lazy_turn.user_message)  # Now turn is loaded
```

### 3. Turn Caching

Cache frequently accessed turns:

```python
from functools import lru_cache

class CachedTurnAccess:
    """Turn access with caching."""
    
    def __init__(self, engine: ERIIEngine, cache_size: int = 128):
        self.engine = engine
        # Create cached version of get_turn
        self._get_turn_cached = lru_cache(maxsize=cache_size)(
            self._get_turn_impl
        )
    
    def _get_turn_impl(self, agent_id: str, user_id: str, turn_id: str):
        """Actual turn retrieval (cacheable)."""
        return self.engine.get_turn(agent_id, user_id, turn_id)
    
    def get_turn(self, agent_id: str, user_id: str, turn_id: str):
        """Get turn with caching."""
        return self._get_turn_cached(agent_id, user_id, turn_id)
    
    def clear_cache(self):
        """Clear turn cache."""
        self._get_turn_cached.cache_clear()
    
    def cache_info(self):
        """Get cache statistics."""
        return self._get_turn_cached.cache_info()

# Usage
cached_access = CachedTurnAccess(engine, cache_size=256)

# First access - cache miss
turn1 = cached_access.get_turn("agent1", "user1", "turn-1")

# Second access - cache hit
turn2 = cached_access.get_turn("agent1", "user1", "turn-1")

# Check cache stats
print(cached_access.cache_info())
# CacheInfo(hits=1, misses=1, maxsize=256, currsize=1)
```

---

## Advanced Error Recovery

### Automatic Retry with State Machine

```python
from enum import Enum

class TurnState(Enum):
    PENDING = "pending"
    OPENING = "opening"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"

class StatefulTurnRecorder:
    """Turn recorder with state machine and automatic recovery."""
    
    def __init__(self, engine: ERIIEngine):
        self.engine = engine
        self.state = TurnState.PENDING
        self.context = {}
    
    def record_with_recovery(
        self,
        agent_id: str,
        user_id: str,
        user_message: str,
        agent_message: str,
        turn_id: str
    ):
        """Record turn with automatic state recovery."""
        self.context = {
            "agent_id": agent_id,
            "user_id": user_id,
            "user_message": user_message,
            "agent_message": agent_message,
            "turn_id": turn_id
        }
        
        # State machine
        while self.state != TurnState.COMPLETED:
            if self.state == TurnState.PENDING:
                self._transition_to_opening()
            elif self.state == TurnState.OPENING:
                self._transition_to_completing()
            elif self.state == TurnState.COMPLETING:
                self._transition_to_completed()
            elif self.state == TurnState.FAILED:
                # Recovery logic
                self._attempt_recovery()
                break
            else:
                break
        
        return self.state == TurnState.COMPLETED
    
    def _transition_to_opening(self):
        """Transition: PENDING → OPENING."""
        try:
            self.state = TurnState.OPENING
            turn = self.engine.begin_turn(
                self.context["agent_id"],
                self.context["user_id"],
                self.context["user_message"],
                turn_id=self.context["turn_id"]
            )
            self.context["turn"] = turn
        except Exception as e:
            self.state = TurnState.FAILED
            self.context["error"] = e
    
    def _transition_to_completing(self):
        """Transition: OPENING → COMPLETING."""
        try:
            self.state = TurnState.COMPLETING
            receipt = self.engine.complete_turn(
                self.context["agent_id"],
                self.context["user_id"],
                self.context["turn_id"],
                self.context["agent_message"]
            )
            self.context["receipt"] = receipt
        except Exception as e:
            self.state = TurnState.FAILED
            self.context["error"] = e
    
    def _transition_to_completed(self):
        """Transition: COMPLETING → COMPLETED."""
        self.state = TurnState.COMPLETED
    
    def _attempt_recovery(self):
        """Attempt to recover from failure."""
        # Check if turn exists and its state
        try:
            turn = self.engine.get_turn(
                self.context["agent_id"],
                self.context["user_id"],
                self.context["turn_id"]
            )
            
            if turn.status == TurnStatus.OPEN:
                # Resume from completing
                self.state = TurnState.COMPLETING
            elif turn.status == TurnStatus.COMPLETED:
                # Already completed, we're done
                self.state = TurnState.COMPLETED
        except TurnNotFoundError:
            # Turn doesn't exist, restart
            self.state = TurnState.PENDING
```

---

## See Also

- [Turn Lifecycle API Reference](turn-lifecycle.md)
- [Turn Error Handling Guide](turn-error-handling.md)
- [Performance Optimization Guide](../performance.md)

---

**Last Updated:** 2026-08-11  
**Status:** Complete
