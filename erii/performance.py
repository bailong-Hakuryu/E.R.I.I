"""
Performance optimization utilities for E.R.I.I.

Provides caching, batching, and query optimization features.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple
from functools import lru_cache
import hashlib
import json
import time


class QueryCache:
    """
    LRU cache for query results with TTL support.

    Features:
    - LRU eviction policy
    - Time-to-live (TTL) expiration
    - Cache key generation from query parameters
    - Memory-efficient storage

    Example:
        cache = QueryCache(max_size=1000, ttl=300)

        # Cache a recall result
        key = cache.make_key("recall", agent_id, user_id, query)
        if cache.has(key):
            return cache.get(key)

        result = expensive_query()
        cache.set(key, result)
    """

    def __init__(self, max_size: int = 1000, ttl: int = 300):
        """
        Initialize query cache.

        Args:
            max_size: Maximum number of entries to cache
            ttl: Time-to-live in seconds (0 = no expiration)
        """
        self.max_size = max_size
        self.ttl = ttl
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._access_order: List[str] = []

    def make_key(self, *args: Any) -> str:
        """
        Generate cache key from query parameters.

        Args:
            *args: Query parameters

        Returns:
            Cache key (hex digest)
        """
        # Serialize args to JSON for consistent hashing
        serialized = json.dumps(args, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]

    def has(self, key: str) -> bool:
        """
        Check if key exists and is not expired.

        Args:
            key: Cache key

        Returns:
            True if valid entry exists
        """
        if key not in self._cache:
            return False

        if self.ttl > 0:
            _, timestamp = self._cache[key]
            if time.time() - timestamp > self.ttl:
                # Expired, remove
                del self._cache[key]
                if key in self._access_order:
                    self._access_order.remove(key)
                return False

        return True

    def get(self, key: str) -> Optional[Any]:
        """
        Get cached value.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        if not self.has(key):
            return None

        # Update access order (LRU)
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

        value, _ = self._cache[key]
        return value

    def set(self, key: str, value: Any) -> None:
        """
        Set cache value.

        Args:
            key: Cache key
            value: Value to cache
        """
        # Evict if at max size
        if len(self._cache) >= self.max_size and key not in self._cache:
            self._evict_lru()

        self._cache[key] = (value, time.time())

        # Update access order
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

    def _evict_lru(self) -> None:
        """Evict least recently used entry."""
        if self._access_order:
            lru_key = self._access_order.pop(0)
            if lru_key in self._cache:
                del self._cache[lru_key]

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()
        self._access_order.clear()

    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)

    def stats(self) -> Dict[str, int]:
        """
        Get cache statistics.

        Returns:
            Dict with size, max_size, and ttl
        """
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "ttl": self.ttl,
        }


class BatchLoader:
    """
    Batch multiple queries together for better performance.

    Features:
    - Accumulate requests over a time window
    - Execute in batch to reduce overhead
    - Distribute results back to callers

    Example:
        loader = BatchLoader(window_ms=10)

        async def load_many(ids):
            return await loader.load_batch(ids, fetch_function)
    """

    def __init__(self, window_ms: int = 10, max_batch_size: int = 100):
        """
        Initialize batch loader.

        Args:
            window_ms: Time window to accumulate requests (milliseconds)
            max_batch_size: Maximum batch size
        """
        self.window_ms = window_ms
        self.max_batch_size = max_batch_size
        self._pending: List[Any] = []
        self._last_flush = time.time()

    def should_flush(self) -> bool:
        """Check if batch should be flushed."""
        if len(self._pending) >= self.max_batch_size:
            return True

        if self._pending and (time.time() - self._last_flush) * 1000 >= self.window_ms:
            return True

        return False

    def add(self, item: Any) -> None:
        """Add item to batch."""
        self._pending.append(item)

    def flush(self) -> List[Any]:
        """
        Flush pending batch.

        Returns:
            List of pending items
        """
        items = self._pending
        self._pending = []
        self._last_flush = time.time()
        return items


def cached_method(ttl: int = 300, max_size: int = 128):
    """
    Decorator for caching method results.

    Args:
        ttl: Time-to-live in seconds
        max_size: Maximum cache size

    Example:
        class MyClass:
            @cached_method(ttl=300)
            def expensive_operation(self, arg1, arg2):
                # Expensive computation
                return result
    """
    cache = QueryCache(max_size=max_size, ttl=ttl)

    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            # Generate cache key from function name and args
            key = cache.make_key(func.__name__, args[1:], tuple(sorted(kwargs.items())))

            if cache.has(key):
                return cache.get(key)

            result = func(*args, **kwargs)
            cache.set(key, result)
            return result

        wrapper.cache = cache  # Expose cache for inspection/clearing
        return wrapper

    return decorator


class PerformanceMonitor:
    """
    Monitor query performance and collect metrics.

    Example:
        monitor = PerformanceMonitor()

        with monitor.measure("recall"):
            result = engine.recall(...)

        stats = monitor.stats()
    """

    def __init__(self):
        self._metrics: Dict[str, List[float]] = {}

    def measure(self, operation: str):
        """
        Context manager for measuring operation duration.

        Args:
            operation: Operation name
        """
        class _Timer:
            def __init__(self, monitor, op):
                self.monitor = monitor
                self.op = op
                self.start = None

            def __enter__(self):
                self.start = time.perf_counter()
                return self

            def __exit__(self, *args):
                duration = time.perf_counter() - self.start
                self.monitor.record(self.op, duration)

        return _Timer(self, operation)

    def record(self, operation: str, duration: float) -> None:
        """Record operation duration."""
        if operation not in self._metrics:
            self._metrics[operation] = []
        self._metrics[operation].append(duration)

    def stats(self) -> Dict[str, Dict[str, float]]:
        """
        Get performance statistics.

        Returns:
            Dict mapping operation to stats (count, mean, p50, p95, p99)
        """
        stats = {}

        for op, durations in self._metrics.items():
            if not durations:
                continue

            sorted_durations = sorted(durations)
            count = len(durations)

            stats[op] = {
                "count": count,
                "mean": sum(durations) / count,
                "p50": sorted_durations[int(count * 0.5)],
                "p95": sorted_durations[int(count * 0.95)],
                "p99": sorted_durations[int(count * 0.99)],
            }

        return stats

    def reset(self) -> None:
        """Reset all metrics."""
        self._metrics.clear()
