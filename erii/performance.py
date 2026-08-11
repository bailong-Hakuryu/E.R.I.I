"""Small, opt-in performance utilities for E.R.I.I.

The helpers in this module are deliberately independent from :class:`ERIIEngine`.
Hosts may compose them around their own read paths, but the kernel does not enable
them implicitly.  In particular, callers remain responsible for invalidating
cached reads after writes.
"""

from collections.abc import Callable, Mapping, Set as AbstractSet
from dataclasses import fields, is_dataclass
from enum import Enum
from functools import wraps
import hashlib
import json
import math
import threading
import time
import weakref
from typing import Any, Optional


def _type_name(value: object) -> str:
    """Return a stable, fully qualified type name for a cache-key value."""
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def _canonical_cache_value(value: Any, active_ids: set[int]) -> Any:
    """Convert supported values to a typed, deterministic JSON structure.

    ``str(value)`` is intentionally not used as a fallback: unrelated objects can
    have the same string representation and some representations contain process-
    specific addresses.  Hosts can instead pass primitive values, containers,
    enums, or dataclass instances.
    """
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if math.isnan(value):
            encoded = "nan"
        elif math.isinf(value):
            encoded = "+inf" if value > 0 else "-inf"
        else:
            encoded = value.hex()
        return ["float", encoded]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, Enum):
        return ["enum", _type_name(value), value.name]

    object_id = id(value)
    if object_id in active_ids:
        raise ValueError("cyclic values cannot be used in a query cache key")

    active_ids.add(object_id)
    try:
        if isinstance(value, tuple):
            return ["tuple", [_canonical_cache_value(item, active_ids) for item in value]]
        if isinstance(value, list):
            return ["list", [_canonical_cache_value(item, active_ids) for item in value]]
        if isinstance(value, Mapping):
            encoded_items = [
                (
                    _canonical_cache_value(key, active_ids),
                    _canonical_cache_value(item, active_ids),
                )
                for key, item in value.items()
            ]
            encoded_items.sort(
                key=lambda pair: json.dumps(
                    pair[0],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            return ["mapping", encoded_items]
        if isinstance(value, AbstractSet):
            encoded_items = [
                _canonical_cache_value(item, active_ids) for item in value
            ]
            encoded_items.sort(
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            return ["set", _type_name(value), encoded_items]
        if is_dataclass(value) and not isinstance(value, type):
            encoded_fields = [
                [field.name, _canonical_cache_value(getattr(value, field.name), active_ids)]
                for field in fields(value)
            ]
            return ["dataclass", _type_name(value), encoded_fields]
    finally:
        active_ids.remove(object_id)

    raise TypeError(
        f"unsupported query cache key type: {_type_name(value)}; "
        "use primitives, containers, enums, or dataclass instances"
    )


class QueryCache:
    """Thread-safe LRU cache for query results with monotonic TTL expiry.

    This is an opt-in host utility, not an engine-global cache.  Cache keys use a
    full SHA-256 digest over a typed canonical representation, so values such as
    ``1`` and ``"1"`` cannot alias and mapping order does not change the key.
    """

    def __init__(self, max_size: int = 1000, ttl: float = 300):
        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size <= 0:
            raise ValueError("max_size must be a positive integer")
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl < 0:
            raise ValueError("ttl must be a non-negative number")
        self.max_size = max_size
        self.ttl = float(ttl)
        self._cache: dict[str, tuple[Any, float]] = {}
        self._access_order: list[str] = []
        self._lock = threading.RLock()

    def make_key(self, *args: Any) -> str:
        """Return a deterministic, type-sensitive SHA-256 cache key."""
        canonical = _canonical_cache_value(args, set())
        serialized = json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _remove_locked(self, key: str) -> None:
        self._cache.pop(key, None)
        try:
            self._access_order.remove(key)
        except ValueError:
            pass

    def _has_locked(self, key: str, now: float) -> bool:
        entry = self._cache.get(key)
        if entry is None:
            return False
        if self.ttl > 0 and now - entry[1] >= self.ttl:
            self._remove_locked(key)
            return False
        return True

    def _purge_expired_locked(self, now: float) -> None:
        if self.ttl <= 0:
            return
        expired = [
            key
            for key, (_, inserted_at) in self._cache.items()
            if now - inserted_at >= self.ttl
        ]
        for key in expired:
            self._remove_locked(key)

    def has(self, key: str) -> bool:
        """Return whether ``key`` has a live entry without changing LRU order."""
        with self._lock:
            return self._has_locked(key, time.monotonic())

    def get(self, key: str) -> Optional[Any]:
        """Return a live cached value, or ``None`` for a missing/expired key."""
        found, value = self._lookup(key)
        return value if found else None

    def _lookup(self, key: str) -> tuple[bool, Any]:
        """Atomically return entry presence and value, including cached ``None``."""
        with self._lock:
            if not self._has_locked(key, time.monotonic()):
                return False, None
            self._access_order.remove(key)
            self._access_order.append(key)
            return True, self._cache[key][0]

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key`` and evict least-recently-used entries."""
        with self._lock:
            if (
                isinstance(self.max_size, bool)
                or not isinstance(self.max_size, int)
                or self.max_size <= 0
            ):
                raise ValueError("max_size must remain a positive integer")
            self._purge_expired_locked(time.monotonic())
            if key not in self._cache:
                while len(self._cache) >= self.max_size:
                    self._evict_lru_locked()
            self._cache[key] = (value, time.monotonic())
            try:
                self._access_order.remove(key)
            except ValueError:
                pass
            self._access_order.append(key)

    def _evict_lru_locked(self) -> None:
        if self._access_order:
            self._cache.pop(self._access_order.pop(0), None)

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            self._access_order.clear()

    def size(self) -> int:
        """Return the number of live cache entries."""
        with self._lock:
            self._purge_expired_locked(time.monotonic())
            return len(self._cache)

    def stats(self) -> dict[str, int | float]:
        """Return current live size and configured bounds."""
        with self._lock:
            self._purge_expired_locked(time.monotonic())
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "ttl": self.ttl,
            }


class BatchLoader:
    """Thread-safe accumulator for host-controlled batch execution.

    The loader does not run a scheduler or call a backend.  A host calls
    :meth:`add`, checks :meth:`should_flush`, then passes the list returned by
    :meth:`flush` to its own batch function::

        loader.add(item)
        if loader.should_flush():
            results = fetch_many(loader.flush())
    """

    def __init__(self, window_ms: float = 10, max_batch_size: int = 100):
        if isinstance(window_ms, bool) or not isinstance(window_ms, (int, float)):
            raise ValueError("window_ms must be a non-negative number")
        if window_ms < 0:
            raise ValueError("window_ms must be a non-negative number")
        if (
            isinstance(max_batch_size, bool)
            or not isinstance(max_batch_size, int)
            or max_batch_size <= 0
        ):
            raise ValueError("max_batch_size must be a positive integer")
        self.window_ms = float(window_ms)
        self.max_batch_size = max_batch_size
        self._pending: list[Any] = []
        self._batch_started_at: Optional[float] = None
        self._lock = threading.RLock()

    def should_flush(self) -> bool:
        """Return whether size or elapsed-time bounds require a flush."""
        with self._lock:
            if len(self._pending) >= self.max_batch_size:
                return True
            return bool(
                self._pending
                and self._batch_started_at is not None
                and (time.monotonic() - self._batch_started_at) * 1000
                >= self.window_ms
            )

    def add(self, item: Any) -> None:
        """Add one item; the host remains responsible for calling ``flush``."""
        with self._lock:
            if not self._pending:
                self._batch_started_at = time.monotonic()
            self._pending.append(item)

    def flush(self) -> list[Any]:
        """Atomically remove and return all currently pending items."""
        with self._lock:
            items = self._pending
            self._pending = []
            self._batch_started_at = None
            return items

    def pending_count(self) -> int:
        """Return the current number of pending items."""
        with self._lock:
            return len(self._pending)


def cached_method(ttl: float = 300, max_size: int = 128) -> Callable:
    """Cache a method without allowing results to cross instance boundaries.

    The decorator keeps one bounded cache for the decorated method but assigns a
    never-reused marker to every live instance.  The marker is part of the key, so
    equal arguments on two engines, tenants, or service instances remain isolated.
    Calls and cache population are serialized to prevent duplicate computation for
    the same uncached call.  Decorated instances must support weak references.

    The cache remains available as ``instance.method.cache`` for explicit host
    invalidation after writes.
    """
    cache = QueryCache(max_size=max_size, ttl=ttl)
    invocation_lock = threading.RLock()
    marker_lock = threading.RLock()
    instance_markers: dict[int, tuple[weakref.ReferenceType[Any], int]] = {}
    next_marker = 0

    def instance_marker(instance: object) -> int:
        nonlocal next_marker
        instance_id = id(instance)
        with marker_lock:
            existing = instance_markers.get(instance_id)
            if existing is not None and existing[0]() is instance:
                return existing[1]
            try:
                reference = weakref.ref(
                    instance,
                    lambda ref, key=instance_id: _discard_instance_marker(key, ref),
                )
            except TypeError as exc:
                raise TypeError(
                    "@cached_method requires instances that support weak references"
                ) from exc
            next_marker += 1
            instance_markers[instance_id] = (reference, next_marker)
            return next_marker

    def _discard_instance_marker(
        instance_id: int,
        reference: weakref.ReferenceType[Any],
    ) -> None:
        with marker_lock:
            existing = instance_markers.get(instance_id)
            if existing is not None and existing[0] is reference:
                instance_markers.pop(instance_id, None)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not args:
                raise TypeError("@cached_method can only decorate instance methods")
            marker = instance_marker(args[0])
            key = cache.make_key(
                func.__module__,
                func.__qualname__,
                marker,
                args[1:],
                kwargs,
            )
            with invocation_lock:
                found, value = cache._lookup(key)
                if found:
                    return value
                result = func(*args, **kwargs)
                cache.set(key, result)
                return result

        setattr(wrapper, "cache", cache)
        return wrapper

    return decorator


class PerformanceMonitor:
    """Thread-safe duration collector for opt-in host instrumentation."""

    def __init__(self) -> None:
        self._metrics: dict[str, list[float]] = {}
        self._lock = threading.RLock()

    def measure(self, operation: str) -> Any:
        """Return a context manager that records one operation duration."""
        monitor = self

        class _Timer:
            def __enter__(self) -> "_Timer":
                self.start = time.perf_counter()
                return self

            def __exit__(self, *_args: object) -> None:
                monitor.record(operation, time.perf_counter() - self.start)

        return _Timer()

    def record(self, operation: str, duration: float) -> None:
        """Record a non-negative operation duration in seconds."""
        if not operation:
            raise ValueError("operation must not be empty")
        if duration < 0 or not math.isfinite(duration):
            raise ValueError("duration must be a finite non-negative number")
        with self._lock:
            self._metrics.setdefault(operation, []).append(duration)

    @staticmethod
    def _percentile(sorted_values: list[float], percentile: float) -> float:
        index = max(0, math.ceil(percentile * len(sorted_values)) - 1)
        return sorted_values[index]

    def stats(self) -> dict[str, dict[str, int | float]]:
        """Return a consistent snapshot with count, mean, p50, p95, and p99."""
        with self._lock:
            snapshot = {
                operation: list(durations)
                for operation, durations in self._metrics.items()
                if durations
            }

        result: dict[str, dict[str, int | float]] = {}
        for operation, durations in snapshot.items():
            sorted_durations = sorted(durations)
            count = len(sorted_durations)
            result[operation] = {
                "count": count,
                "mean": sum(sorted_durations) / count,
                "p50": self._percentile(sorted_durations, 0.50),
                "p95": self._percentile(sorted_durations, 0.95),
                "p99": self._percentile(sorted_durations, 0.99),
            }
        return result

    def reset(self) -> None:
        """Atomically clear all collected metrics."""
        with self._lock:
            self._metrics.clear()
