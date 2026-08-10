# Turn Lifecycle Error Handling Guide

**Version:** 0.5.0a2  
**Audience:** Host Integration Developers

---

## Overview

This guide covers error handling patterns for the Turn Lifecycle API, including:
- Common error scenarios and recovery strategies
- Retry logic and idempotency
- Debugging techniques
- Production best practices

---

## Error Hierarchy

```
Exception
└── TurnLifecycleError (base for all turn-related errors)
    ├── TurnNotFoundError
    ├── TurnConflictError
    └── TurnTerminalConflictError
```

All Turn Lifecycle errors inherit from `TurnLifecycleError`, allowing you to catch them collectively if needed.

---

## Common Error Scenarios

### 1. TurnNotFoundError

**When it occurs:**
- Attempting to access a turn that doesn't exist
- Wrong turn_id, agent_id, or user_id
- Turn exists in different relationship

**Example:**

```python
from erii import ERIIEngine, TurnNotFoundError

engine = ERIIEngine(storage_dir="./data")

try:
    turn = engine.get_turn(
        "agent_lumi",
        "user_chen",
        "nonexistent-turn-id"
    )
except TurnNotFoundError as e:
    print(f"Turn not found: {e}")
    # Recovery options:
    # 1. Check if turn_id is correct
    # 2. List existing turns to find the right one
    # 3. Create a new turn if this was meant to be new
```

**Recovery Strategies:**

**Strategy 1: Verify Turn Exists**
```python
def get_turn_safely(engine, agent_id, user_id, turn_id):
    """Get turn with existence check."""
    try:
        return engine.get_turn(agent_id, user_id, turn_id)
    except TurnNotFoundError:
        # List all turns to help debug
        all_turns = engine.list_turns(agent_id, user_id)
        print(f"Available turns: {[t.turn_id for t in all_turns]}")
        return None
```

**Strategy 2: Create if Not Found**
```python
def get_or_create_turn(engine, agent_id, user_id, turn_id, user_message):
    """Get existing turn or create new one."""
    try:
        return engine.get_turn(agent_id, user_id, turn_id)
    except TurnNotFoundError:
        # Turn doesn't exist, create it
        return engine.begin_turn(
            agent_id,
            user_id,
            user_message,
            turn_id=turn_id
        )
```

---

### 2. TurnConflictError

**When it occurs:**
- Turn ID already exists with different content
- Retry with modified message
- Race condition with multiple writers

**Example:**

```python
from erii import TurnConflictError

try:
    # First attempt
    engine.begin_turn("agent", "user", "Hello", turn_id="turn-1")
    
    # Retry with different content (ERROR!)
    engine.begin_turn("agent", "user", "Hi there", turn_id="turn-1")
    
except TurnConflictError as e:
    print(f"Content mismatch: {e}")
    # The turn already exists with different content
```

**Recovery Strategies:**

**Strategy 1: Check for Idempotent Retry**
```python
def begin_turn_idempotent(engine, agent_id, user_id, user_message, turn_id):
    """Begin turn with idempotent retry handling."""
    try:
        return engine.begin_turn(
            agent_id,
            user_id,
            user_message,
            turn_id=turn_id
        )
    except TurnConflictError:
        # Check if this is a retry with same content
        existing_turn = engine.get_turn(agent_id, user_id, turn_id)
        if existing_turn.transcript.user_message.content == user_message:
            # Same content, this is an idempotent retry
            return existing_turn
        else:
            # Different content, real conflict
            raise
```

**Strategy 2: Use New Turn ID**
```python
import uuid

def begin_turn_with_fallback(engine, agent_id, user_id, user_message, turn_id):
    """Try turn_id, fall back to new UUID on conflict."""
    try:
        return engine.begin_turn(
            agent_id,
            user_id,
            user_message,
            turn_id=turn_id
        )
    except TurnConflictError:
        # Conflict - use new ID
        new_turn_id = f"{turn_id}-retry-{uuid.uuid4().hex[:8]}"
        return engine.begin_turn(
            agent_id,
            user_id,
            user_message,
            turn_id=new_turn_id
        )
```

---

### 3. TurnTerminalConflictError

**When it occurs:**
- Attempting to modify a completed turn
- Attempting to complete an abandoned turn
- Turn already in terminal state (COMPLETED or ABANDONED)

**Example:**

```python
from erii import TurnTerminalConflictError

# Complete turn
engine.complete_turn("agent", "user", "turn-1", "Reply")

# Try to complete again (ERROR!)
try:
    engine.complete_turn("agent", "user", "turn-1", "Different reply")
except TurnTerminalConflictError as e:
    print(f"Turn already terminal: {e}")
    # Cannot modify terminal turns
```

**Recovery Strategies:**

**Strategy 1: Check Status First**
```python
from erii import TurnStatus

def complete_if_open(engine, agent_id, user_id, turn_id, agent_message):
    """Complete turn only if still open."""
    turn = engine.get_turn(agent_id, user_id, turn_id)
    
    if turn.status == TurnStatus.OPEN:
        return engine.complete_turn(
            agent_id,
            user_id,
            turn_id,
            agent_message
        )
    elif turn.status == TurnStatus.COMPLETED:
        # Already completed, check if same content
        if turn.transcript.agent_message.content == agent_message:
            return turn  # Idempotent retry
        else:
            raise ValueError("Turn completed with different content")
    else:
        raise ValueError(f"Turn in unexpected state: {turn.status}")
```

**Strategy 2: Create New Turn**
```python
def complete_or_create_new(engine, agent_id, user_id, turn_id, user_msg, agent_msg):
    """Complete turn or create new one if terminal."""
    try:
        return engine.complete_turn(
            agent_id,
            user_id,
            turn_id,
            agent_msg
        )
    except TurnTerminalConflictError:
        # Turn is terminal, create new turn
        new_turn_id = f"turn-{uuid.uuid4()}"
        new_turn = engine.begin_turn(
            agent_id,
            user_id,
            user_msg,
            turn_id=new_turn_id
        )
        return engine.complete_turn(
            agent_id,
            user_id,
            new_turn_id,
            agent_msg
        )
```

---

## Retry Patterns

### Exponential Backoff

For transient failures (network, temporary locks):

```python
import time

def complete_turn_with_retry(
    engine,
    agent_id,
    user_id,
    turn_id,
    agent_message,
    max_retries=3,
    base_delay=0.1
):
    """Complete turn with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return engine.complete_turn(
                agent_id,
                user_id,
                turn_id,
                agent_message
            )
        except TurnConflictError:
            # Conflict - check if idempotent retry
            turn = engine.get_turn(agent_id, user_id, turn_id)
            if turn.transcript.agent_message and \
               turn.transcript.agent_message.content == agent_message:
                return turn  # Same content, success
            else:
                raise  # Real conflict, don't retry
        except Exception as e:
            if attempt == max_retries - 1:
                raise  # Last attempt, give up
            
            # Exponential backoff
            delay = base_delay * (2 ** attempt)
            time.sleep(delay)
    
    raise RuntimeError("Max retries exceeded")
```

### Circuit Breaker

For protecting against cascading failures:

```python
class TurnRecordingCircuitBreaker:
    """Circuit breaker for turn recording operations."""
    
    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.last_failure_time = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    
    def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection."""
        if self.state == "OPEN":
            # Check if timeout expired
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "HALF_OPEN"
            else:
                raise RuntimeError("Circuit breaker OPEN")
        
        try:
            result = func(*args, **kwargs)
            # Success
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
                self.failure_count = 0
            return result
        
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
            
            raise

# Usage
breaker = TurnRecordingCircuitBreaker()

try:
    receipt = breaker.call(
        engine.complete_turn,
        agent_id,
        user_id,
        turn_id,
        agent_message
    )
except RuntimeError as e:
    print(f"Circuit breaker triggered: {e}")
```

---

## Debugging Techniques

### 1. Enable Detailed Logging

```python
import logging

# Enable E.R.I.I. debug logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("erii")
logger.setLevel(logging.DEBUG)

# Now turn operations will log details
engine.begin_turn(...)
```

### 2. Inspect Turn State

```python
def debug_turn(engine, agent_id, user_id, turn_id):
    """Print detailed turn information for debugging."""
    try:
        turn = engine.get_turn(agent_id, user_id, turn_id)
        print(f"Turn ID: {turn.turn_id}")
        print(f"Status: {turn.status}")
        print(f"Opened at: {turn.opened_at}")
        print(f"Completed at: {turn.completed_at}")
        print(f"User message: {turn.transcript.user_message.content}")
        if turn.transcript.agent_message:
            print(f"Agent message: {turn.transcript.agent_message.content}")
        print(f"Context snapshot: {turn.context_snapshot is not None}")
    except TurnNotFoundError:
        print(f"Turn {turn_id} not found")
        # List available turns
        all_turns = engine.list_turns(agent_id, user_id)
        print(f"Available turns: {[t.turn_id for t in all_turns]}")
```

### 3. Validate Fingerprints

```python
import hashlib

def verify_content_fingerprint(content: str, expected_fingerprint: str):
    """Verify content matches expected fingerprint."""
    actual = hashlib.sha256(content.encode('utf-8')).hexdigest()
    if actual != expected_fingerprint:
        raise ValueError(
            f"Content fingerprint mismatch!\n"
            f"Expected: {expected_fingerprint}\n"
            f"Actual: {actual}\n"
            f"Content may have been modified"
        )
    return True

# After completing turn
receipt = engine.complete_turn(...)
verify_content_fingerprint(user_message, receipt.user_message_fingerprint)
verify_content_fingerprint(agent_message, receipt.agent_message_fingerprint)
```

---

## Production Best Practices

### 1. Centralized Error Handling

```python
class TurnManager:
    """Centralized turn management with error handling."""
    
    def __init__(self, engine):
        self.engine = engine
        self.logger = logging.getLogger(__name__)
    
    def record_conversation(
        self,
        agent_id: str,
        user_id: str,
        user_message: str,
        agent_message: str,
        turn_id: str = None
    ):
        """Record conversation with comprehensive error handling."""
        turn_id = turn_id or f"turn-{uuid.uuid4()}"
        
        try:
            # Phase 1: Begin turn
            turn = self.engine.begin_turn(
                agent_id,
                user_id,
                user_message,
                turn_id=turn_id
            )
            self.logger.info(f"Turn opened: {turn_id}")
            
            # Phase 2: Complete turn
            receipt = self.engine.complete_turn(
                agent_id,
                user_id,
                turn_id,
                agent_message
            )
            self.logger.info(f"Turn completed: {turn_id}")
            
            # Phase 3: Archive
            submission = self.engine.archive_turn(
                agent_id,
                user_id,
                turn_id
            )
            self.logger.info(f"Turn archived: {turn_id}")
            
            return receipt
            
        except TurnConflictError as e:
            self.logger.warning(f"Turn conflict: {e}")
            # Handle idempotent retry
            turn = self.engine.get_turn(agent_id, user_id, turn_id)
            if turn.status == TurnStatus.COMPLETED:
                return turn
            raise
            
        except TurnTerminalConflictError as e:
            self.logger.error(f"Terminal conflict: {e}")
            raise
            
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}", exc_info=True)
            # Attempt to abandon turn if still open
            try:
                self.engine.abandon_turn(agent_id, user_id, turn_id)
                self.logger.info(f"Turn abandoned: {turn_id}")
            except:
                pass
            raise
```

### 2. Monitoring and Alerts

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class TurnMetrics:
    """Turn operation metrics."""
    total_attempts: int = 0
    successful_completions: int = 0
    conflicts: int = 0
    terminal_conflicts: int = 0
    not_found_errors: int = 0
    abandonments: int = 0
    
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.successful_completions / self.total_attempts

class MonitoredTurnManager(TurnManager):
    """Turn manager with metrics collection."""
    
    def __init__(self, engine):
        super().__init__(engine)
        self.metrics = TurnMetrics()
    
    def record_conversation(self, *args, **kwargs):
        """Record with metrics."""
        self.metrics.total_attempts += 1
        
        try:
            result = super().record_conversation(*args, **kwargs)
            self.metrics.successful_completions += 1
            return result
        except TurnConflictError:
            self.metrics.conflicts += 1
            raise
        except TurnTerminalConflictError:
            self.metrics.terminal_conflicts += 1
            raise
        except TurnNotFoundError:
            self.metrics.not_found_errors += 1
            raise
    
    def get_health_status(self):
        """Check if turn recording is healthy."""
        success_rate = self.metrics.success_rate()
        
        if success_rate < 0.9:
            return "UNHEALTHY", f"Success rate: {success_rate:.2%}"
        elif success_rate < 0.95:
            return "DEGRADED", f"Success rate: {success_rate:.2%}"
        else:
            return "HEALTHY", f"Success rate: {success_rate:.2%}"
```

### 3. Graceful Degradation

```python
def record_with_fallback(engine, agent_id, user_id, user_msg, agent_msg):
    """Record turn with fallback to basic logging."""
    try:
        # Try full turn recording
        return engine.record_turn(
            agent_id,
            user_id,
            user_msg,
            agent_msg
        )
    except Exception as e:
        # Full recording failed, fallback to basic log
        logger.error(f"Turn recording failed: {e}")
        
        # Log to file for later recovery
        with open("failed_turns.jsonl", "a") as f:
            import json
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "agent_id": agent_id,
                "user_id": user_id,
                "user_message": user_msg,
                "agent_message": agent_msg,
                "error": str(e)
            }) + "\n")
        
        # Return minimal receipt
        return {
            "status": "LOGGED_OFFLINE",
            "turn_id": f"offline-{uuid.uuid4()}"
        }
```

---

## Error Recovery Checklist

When encountering errors in production:

### Immediate Actions
- [ ] Check error logs for full stack trace
- [ ] Verify relationship is initialized
- [ ] Confirm turn_id format and uniqueness
- [ ] Check database connectivity
- [ ] Verify sufficient disk space

### Investigation
- [ ] List existing turns for the relationship
- [ ] Check turn status if turn exists
- [ ] Verify content fingerprints
- [ ] Review recent code changes
- [ ] Check for concurrent operations

### Prevention
- [ ] Implement retry logic with backoff
- [ ] Add circuit breaker for cascading failures
- [ ] Monitor success rates
- [ ] Set up alerting thresholds
- [ ] Document recovery procedures

---

## See Also

- [Turn Lifecycle API Reference](turn-lifecycle.md)
- [Host Integration Guide](../host-integration.md)

---

**Last Updated:** 2026-08-11  
**Status:** Complete
