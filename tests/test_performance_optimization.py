"""Correctness tests for the opt-in performance utilities."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import threading
import time
import unittest
from unittest.mock import patch

from erii.performance import BatchLoader, PerformanceMonitor, QueryCache, cached_method


class TestQueryCache(unittest.TestCase):
    """Verify deterministic keys, TTL/LRU behavior, and concurrent access."""

    def setUp(self) -> None:
        self.cache = QueryCache(max_size=3, ttl=1)

    def test_basic_get_set(self) -> None:
        key = self.cache.make_key("test", "arg1", "arg2")
        self.assertFalse(self.cache.has(key))
        self.assertIsNone(self.cache.get(key))

        self.cache.set(key, "result")

        self.assertTrue(self.cache.has(key))
        self.assertEqual(self.cache.get(key), "result")

    def test_cached_none_is_distinct_from_a_miss(self) -> None:
        calls = 0

        class Service:
            @cached_method(ttl=10)
            def optional_value(self, key: str):
                nonlocal calls
                calls += 1
                return None

        service = Service()
        self.assertIsNone(service.optional_value("known-empty"))
        self.assertIsNone(service.optional_value("known-empty"))
        self.assertEqual(calls, 1)

    def test_keys_are_stable_type_sensitive_full_sha256(self) -> None:
        first = self.cache.make_key(
            "recall",
            {"b": [2, 3], "a": 1},
            {"snow", "arcade"},
        )
        reordered = self.cache.make_key(
            "recall",
            {"a": 1, "b": [2, 3]},
            {"arcade", "snow"},
        )

        self.assertEqual(first, reordered)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(self.cache.make_key(1), self.cache.make_key("1"))
        self.assertNotEqual(self.cache.make_key([1]), self.cache.make_key((1,)))

    def test_dataclass_keys_are_supported_without_string_fallback(self) -> None:
        @dataclass(frozen=True)
        class Scope:
            agent_id: str
            user_id: str

        self.assertEqual(
            self.cache.make_key(Scope("agent", "user")),
            self.cache.make_key(Scope("agent", "user")),
        )
        with self.assertRaisesRegex(TypeError, "unsupported query cache key type"):
            self.cache.make_key(object())

    def test_ttl_expiration_uses_monotonic_clock(self) -> None:
        with patch("erii.performance.time.monotonic", return_value=10.0) as clock:
            cache = QueryCache(max_size=3, ttl=1)
            key = cache.make_key("test")
            cache.set(key, "value")
            self.assertTrue(cache.has(key))

            clock.return_value = 11.0
            self.assertFalse(cache.has(key))
            self.assertIsNone(cache.get(key))
            self.assertEqual(cache.size(), 0)

    def test_lru_eviction_and_access_order(self) -> None:
        keys = [self.cache.make_key(f"test-{index}") for index in range(4)]
        for index, key in enumerate(keys[:3]):
            self.cache.set(key, f"value-{index}")

        self.assertEqual(self.cache.get(keys[0]), "value-0")
        self.cache.set(keys[3], "value-3")

        self.assertEqual(self.cache.size(), 3)
        self.assertTrue(self.cache.has(keys[0]))
        self.assertFalse(self.cache.has(keys[1]))
        self.assertTrue(self.cache.has(keys[2]))
        self.assertTrue(self.cache.has(keys[3]))

    def test_clear_and_stats(self) -> None:
        self.cache.set(self.cache.make_key("test"), "value")
        self.assertEqual(
            self.cache.stats(),
            {"size": 1, "max_size": 3, "ttl": 1.0},
        )

        self.cache.clear()
        self.assertEqual(self.cache.size(), 0)

    def test_constructor_rejects_invalid_bounds(self) -> None:
        for value in (0, -1, True):
            with self.subTest(max_size=value):
                with self.assertRaises(ValueError):
                    QueryCache(max_size=value)
        for value in (-1, True):
            with self.subTest(ttl=value):
                with self.assertRaises(ValueError):
                    QueryCache(ttl=value)

    def test_concurrent_cache_operations_preserve_bound_and_values(self) -> None:
        cache = QueryCache(max_size=32, ttl=0)

        def write_and_read(index: int) -> int:
            key = cache.make_key(index)
            cache.set(key, index)
            value = cache.get(key)
            self.assertIsInstance(value, int)
            return value

        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(write_and_read, range(200)))

        self.assertEqual(values, list(range(200)))
        self.assertLessEqual(cache.size(), 32)


class TestBatchLoader(unittest.TestCase):
    """Verify the host-controlled accumulator contract."""

    def test_batch_by_size(self) -> None:
        loader = BatchLoader(window_ms=1000, max_batch_size=3)
        loader.add("item1")
        loader.add("item2")
        self.assertFalse(loader.should_flush())

        loader.add("item3")
        self.assertTrue(loader.should_flush())
        self.assertEqual(loader.pending_count(), 3)
        self.assertEqual(loader.flush(), ["item1", "item2", "item3"])
        self.assertEqual(loader.pending_count(), 0)
        self.assertFalse(loader.should_flush())

    def test_batch_by_time_uses_monotonic_clock(self) -> None:
        with patch("erii.performance.time.monotonic", return_value=20.0) as clock:
            loader = BatchLoader(window_ms=50, max_batch_size=100)
            loader.add("item1")
            self.assertFalse(loader.should_flush())

            clock.return_value = 20.05
            self.assertTrue(loader.should_flush())
            self.assertEqual(loader.flush(), ["item1"])

    def test_concurrent_add_and_atomic_flush(self) -> None:
        loader = BatchLoader(window_ms=1000, max_batch_size=1000)
        expected = {(thread_id, index) for thread_id in range(8) for index in range(25)}

        def add_items(thread_id: int) -> None:
            for index in range(25):
                loader.add((thread_id, index))

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(add_items, range(8)))

        self.assertEqual(loader.pending_count(), 200)
        self.assertEqual(set(loader.flush()), expected)
        self.assertEqual(loader.pending_count(), 0)

    def test_constructor_rejects_invalid_bounds(self) -> None:
        with self.assertRaises(ValueError):
            BatchLoader(window_ms=-1)
        with self.assertRaises(ValueError):
            BatchLoader(max_batch_size=0)


class TestCachedMethod(unittest.TestCase):
    """Verify caching, instance isolation, and one-time population."""

    def test_caching_and_explicit_clear(self) -> None:
        call_count = 0

        class TestClass:
            @cached_method(ttl=10)
            def expensive_method(self, x: int, y: int) -> int:
                nonlocal call_count
                call_count += 1
                return x + y

        obj = TestClass()
        self.assertEqual(obj.expensive_method(1, 2), 3)
        self.assertEqual(obj.expensive_method(1, 2), 3)
        self.assertEqual(call_count, 1)

        obj.expensive_method.cache.clear()
        self.assertEqual(obj.expensive_method(1, 2), 3)
        self.assertEqual(call_count, 2)

    def test_equal_arguments_do_not_cross_instance_boundaries(self) -> None:
        class NamedService:
            def __init__(self, name: str):
                self.name = name
                self.calls = 0

            @cached_method(ttl=10)
            def scoped_value(self, key: str) -> str:
                self.calls += 1
                return f"{self.name}:{key}"

        first = NamedService("first")
        second = NamedService("second")

        self.assertEqual(first.scoped_value("same"), "first:same")
        self.assertEqual(second.scoped_value("same"), "second:same")
        self.assertEqual(first.scoped_value("same"), "first:same")
        self.assertEqual(second.scoped_value("same"), "second:same")
        self.assertEqual((first.calls, second.calls), (1, 1))

    def test_concurrent_miss_computes_once(self) -> None:
        start = threading.Barrier(8)

        class Service:
            def __init__(self) -> None:
                self.calls = 0

            @cached_method(ttl=10)
            def compute(self, key: str) -> str:
                self.calls += 1
                time.sleep(0.01)
                return key.upper()

        service = Service()

        def invoke() -> str:
            start.wait()
            return service.compute("shared")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _index: invoke(), range(8)))

        self.assertEqual(results, ["SHARED"] * 8)
        self.assertEqual(service.calls, 1)


class TestPerformanceMonitor(unittest.TestCase):
    """Verify deterministic statistics and concurrent recording."""

    def test_measure_context_manager(self) -> None:
        monitor = PerformanceMonitor()
        with monitor.measure("test_op"):
            time.sleep(0.01)

        stats = monitor.stats()["test_op"]
        self.assertEqual(stats["count"], 1)
        self.assertGreaterEqual(stats["mean"], 0.01)

    def test_percentiles_use_nearest_rank(self) -> None:
        monitor = PerformanceMonitor()
        for duration in range(1, 101):
            monitor.record("op", float(duration))

        stats = monitor.stats()["op"]
        self.assertEqual(stats["count"], 100)
        self.assertEqual(stats["mean"], 50.5)
        self.assertEqual(stats["p50"], 50.0)
        self.assertEqual(stats["p95"], 95.0)
        self.assertEqual(stats["p99"], 99.0)

    def test_concurrent_records_are_not_lost(self) -> None:
        monitor = PerformanceMonitor()

        def record_batch(_thread_id: int) -> None:
            for _ in range(100):
                monitor.record("recall", 0.001)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(record_batch, range(8)))

        self.assertEqual(monitor.stats()["recall"]["count"], 800)

    def test_reset_and_input_validation(self) -> None:
        monitor = PerformanceMonitor()
        monitor.record("op", 0.0)
        monitor.reset()
        self.assertEqual(monitor.stats(), {})
        with self.assertRaises(ValueError):
            monitor.record("", 0.1)
        with self.assertRaises(ValueError):
            monitor.record("op", -0.1)


if __name__ == "__main__":
    unittest.main()
