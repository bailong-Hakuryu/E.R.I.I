"""Tests for performance optimization utilities."""

import time
import unittest

from erii.performance import QueryCache, BatchLoader, cached_method, PerformanceMonitor


class TestQueryCache(unittest.TestCase):
    """Test QueryCache functionality."""

    def setUp(self):
        self.cache = QueryCache(max_size=3, ttl=1)

    def test_basic_get_set(self):
        """Test basic cache operations."""
        key = self.cache.make_key("test", "arg1", "arg2")

        # Initially not in cache
        self.assertFalse(self.cache.has(key))
        self.assertIsNone(self.cache.get(key))

        # Set value
        self.cache.set(key, "result")

        # Now in cache
        self.assertTrue(self.cache.has(key))
        self.assertEqual(self.cache.get(key), "result")

    def test_ttl_expiration(self):
        """Test TTL expiration."""
        key = self.cache.make_key("test")
        self.cache.set(key, "value")

        # Should exist immediately
        self.assertTrue(self.cache.has(key))

        # Wait for expiration
        time.sleep(1.1)

        # Should be expired
        self.assertFalse(self.cache.has(key))
        self.assertIsNone(self.cache.get(key))

    def test_lru_eviction(self):
        """Test LRU eviction policy."""
        # Add 3 entries (max size)
        key1 = self.cache.make_key("test1")
        key2 = self.cache.make_key("test2")
        key3 = self.cache.make_key("test3")

        self.cache.set(key1, "value1")
        self.cache.set(key2, "value2")
        self.cache.set(key3, "value3")

        self.assertEqual(self.cache.size(), 3)

        # Add 4th entry, should evict key1 (LRU)
        key4 = self.cache.make_key("test4")
        self.cache.set(key4, "value4")

        self.assertEqual(self.cache.size(), 3)
        self.assertFalse(self.cache.has(key1))
        self.assertTrue(self.cache.has(key2))
        self.assertTrue(self.cache.has(key3))
        self.assertTrue(self.cache.has(key4))

    def test_lru_access_updates(self):
        """Test that access updates LRU order."""
        key1 = self.cache.make_key("test1")
        key2 = self.cache.make_key("test2")
        key3 = self.cache.make_key("test3")

        self.cache.set(key1, "value1")
        self.cache.set(key2, "value2")
        self.cache.set(key3, "value3")

        # Access key1 to make it most recently used
        self.cache.get(key1)

        # Add 4th entry, should evict key2 (now LRU)
        key4 = self.cache.make_key("test4")
        self.cache.set(key4, "value4")

        self.assertTrue(self.cache.has(key1))
        self.assertFalse(self.cache.has(key2))
        self.assertTrue(self.cache.has(key3))
        self.assertTrue(self.cache.has(key4))

    def test_clear(self):
        """Test cache clearing."""
        self.cache.set(self.cache.make_key("test"), "value")
        self.assertEqual(self.cache.size(), 1)

        self.cache.clear()
        self.assertEqual(self.cache.size(), 0)

    def test_stats(self):
        """Test cache statistics."""
        stats = self.cache.stats()
        self.assertEqual(stats["size"], 0)
        self.assertEqual(stats["max_size"], 3)
        self.assertEqual(stats["ttl"], 1)


class TestBatchLoader(unittest.TestCase):
    """Test BatchLoader functionality."""

    def test_batch_by_size(self):
        """Test batching by max size."""
        loader = BatchLoader(window_ms=1000, max_batch_size=3)

        loader.add("item1")
        loader.add("item2")
        self.assertFalse(loader.should_flush())

        loader.add("item3")
        self.assertTrue(loader.should_flush())

        items = loader.flush()
        self.assertEqual(len(items), 3)
        self.assertFalse(loader.should_flush())

    def test_batch_by_time(self):
        """Test batching by time window."""
        loader = BatchLoader(window_ms=50, max_batch_size=100)

        loader.add("item1")
        self.assertFalse(loader.should_flush())

        # Wait for time window
        time.sleep(0.06)

        self.assertTrue(loader.should_flush())
        items = loader.flush()
        self.assertEqual(len(items), 1)


class TestCachedMethod(unittest.TestCase):
    """Test cached_method decorator."""

    def test_caching(self):
        """Test method result caching."""
        call_count = 0

        class TestClass:
            @cached_method(ttl=10)
            def expensive_method(self, x, y):
                nonlocal call_count
                call_count += 1
                return x + y

        obj = TestClass()

        # First call
        result1 = obj.expensive_method(1, 2)
        self.assertEqual(result1, 3)
        self.assertEqual(call_count, 1)

        # Second call with same args (should use cache)
        result2 = obj.expensive_method(1, 2)
        self.assertEqual(result2, 3)
        self.assertEqual(call_count, 1)  # Not incremented

        # Different args (should call again)
        result3 = obj.expensive_method(2, 3)
        self.assertEqual(result3, 5)
        self.assertEqual(call_count, 2)

    def test_cache_clearing(self):
        """Test clearing method cache."""
        class TestClass:
            @cached_method(ttl=10)
            def method(self, x):
                return x * 2

        obj = TestClass()
        obj.method(5)

        # Clear cache
        obj.method.cache.clear()
        self.assertEqual(obj.method.cache.size(), 0)


class TestPerformanceMonitor(unittest.TestCase):
    """Test PerformanceMonitor functionality."""

    def test_measure_context_manager(self):
        """Test measuring operation duration."""
        monitor = PerformanceMonitor()

        with monitor.measure("test_op"):
            time.sleep(0.01)

        stats = monitor.stats()
        self.assertIn("test_op", stats)
        self.assertEqual(stats["test_op"]["count"], 1)
        self.assertGreater(stats["test_op"]["mean"], 0.01)

    def test_multiple_measurements(self):
        """Test collecting multiple measurements."""
        monitor = PerformanceMonitor()

        for _ in range(10):
            with monitor.measure("op"):
                time.sleep(0.001)

        stats = monitor.stats()
        self.assertEqual(stats["op"]["count"], 10)
        self.assertGreater(stats["op"]["p50"], 0)
        self.assertGreater(stats["op"]["p95"], 0)
        self.assertGreater(stats["op"]["p99"], 0)

    def test_reset(self):
        """Test resetting metrics."""
        monitor = PerformanceMonitor()

        with monitor.measure("op"):
            pass

        self.assertEqual(len(monitor.stats()), 1)

        monitor.reset()
        self.assertEqual(len(monitor.stats()), 0)


if __name__ == "__main__":
    unittest.main()
