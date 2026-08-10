"""
Example: Using E.R.I.I. Performance Optimization Features

This example demonstrates how to use the performance optimization utilities
including query caching, batch loading, and performance monitoring.
"""

from erii import ERIIEngine, SQLiteStorage
from erii.performance import QueryCache, cached_method, PerformanceMonitor
import time


def example_1_basic_query_cache():
    """Example 1: Basic query result caching."""
    print("=" * 60)
    print("Example 1: Basic Query Cache")
    print("=" * 60)

    # Create cache with 1000 entry limit, 5 minute TTL
    cache = QueryCache(max_size=1000, ttl=300)

    # Simulate expensive queries
    def expensive_recall(agent_id: str, user_id: str, query: str) -> str:
        time.sleep(0.1)  # Simulate delay
        return f"Context for {query}"

    # Generate cache key
    key = cache.make_key("recall", "agent1", "user1", "what is my name?")

    # Check cache
    if cache.has(key):
        result = cache.get(key)
        print("✓ Cache hit!")
    else:
        print("✗ Cache miss, executing query...")
        result = expensive_recall("agent1", "user1", "what is my name?")
        cache.set(key, result)
        print("✓ Result cached")

    print(f"Result: {result}")
    print(f"Cache stats: {cache.stats()}")
    print()


def example_2_cached_method_decorator():
    """Example 2: Using @cached_method decorator."""
    print("=" * 60)
    print("Example 2: @cached_method Decorator")
    print("=" * 60)

    class MyEngine:
        def __init__(self):
            self.query_count = 0

        @cached_method(ttl=300, max_size=100)
        def get_user_profile(self, user_id: str) -> dict:
            """Cached method - only computed once per user."""
            self.query_count += 1
            print(f"  → Computing profile for {user_id}...")
            time.sleep(0.05)
            return {"user_id": user_id, "name": f"User {user_id}"}

    engine = MyEngine()

    # First call - cache miss
    print("First call:")
    profile1 = engine.get_user_profile("user123")
    print(f"  Result: {profile1}")
    print(f"  Total queries: {engine.query_count}")

    # Second call - cache hit
    print("\nSecond call (cached):")
    profile2 = engine.get_user_profile("user123")
    print(f"  Result: {profile2}")
    print(f"  Total queries: {engine.query_count}")

    # Different user - cache miss
    print("\nDifferent user:")
    profile3 = engine.get_user_profile("user456")
    print(f"  Result: {profile3}")
    print(f"  Total queries: {engine.query_count}")

    print()


def example_3_performance_monitoring():
    """Example 3: Performance monitoring and metrics."""
    print("=" * 60)
    print("Example 3: Performance Monitoring")
    print("=" * 60)

    monitor = PerformanceMonitor()

    # Simulate various operations
    for i in range(10):
        with monitor.measure("recall"):
            time.sleep(0.01)

    for i in range(5):
        with monitor.measure("remember"):
            time.sleep(0.02)

    # Get statistics
    stats = monitor.stats()

    print("Performance Statistics:")
    print("-" * 60)
    for operation, metrics in stats.items():
        print(f"\n{operation}:")
        print(f"  Count:  {metrics['count']}")
        print(f"  Mean:   {metrics['mean']*1000:.2f}ms")
        print(f"  P50:    {metrics['p50']*1000:.2f}ms")
        print(f"  P95:    {metrics['p95']*1000:.2f}ms")
        print(f"  P99:    {metrics['p99']*1000:.2f}ms")

    print()


def example_4_integrated_caching():
    """Example 4: Integrated caching in E.R.I.I. engine."""
    print("=" * 60)
    print("Example 4: Integrated Caching with E.R.I.I.")
    print("=" * 60)

    # Create engine with caching
    storage = SQLiteStorage(db_path=":memory:")
    engine = ERIIEngine(storage_driver=storage)

    # Create cache and monitor
    recall_cache = QueryCache(max_size=500, ttl=300)
    monitor = PerformanceMonitor()

    def cached_recall(agent_id: str, user_id: str, query: str) -> str:
        """Cached recall wrapper."""
        key = recall_cache.make_key("recall", agent_id, user_id, query)

        if recall_cache.has(key):
            print("  ✓ Cache hit")
            return recall_cache.get(key)

        print("  ✗ Cache miss, querying database...")
        with monitor.measure("recall"):
            result = engine.recall(agent_id, user_id, query)

        recall_cache.set(key, result)
        return result

    # Setup
    engine.set_core_memory("agent1", "user1", "User likes coffee")
    engine.remember("agent1", "user1", "I love coffee", "Great!")

    # Query 1 - cache miss
    print("Query 1:")
    result1 = cached_recall("agent1", "user1", "what do I like?")
    print(f"  Length: {len(result1)} chars")

    # Query 2 - cache hit
    print("\nQuery 2 (same query):")
    result2 = cached_recall("agent1", "user1", "what do I like?")
    print(f"  Length: {len(result2)} chars")

    # Statistics
    print(f"\nCache stats: {recall_cache.stats()}")
    print("\nPerformance stats:")
    for op, metrics in monitor.stats().items():
        print(f"  {op}: {metrics['count']} calls, {metrics['mean']*1000:.2f}ms avg")

    engine.close()
    print()


def example_5_cache_warming():
    """Example 5: Cache warming strategy."""
    print("=" * 60)
    print("Example 5: Cache Warming")
    print("=" * 60)

    cache = QueryCache(max_size=1000, ttl=600)

    # Common queries to pre-warm
    common_queries = [
        ("agent1", "user1", "what is my name?"),
        ("agent1", "user1", "what do I like?"),
        ("agent1", "user2", "what did we discuss?"),
    ]

    print("Pre-warming cache with common queries...")
    for agent_id, user_id, query in common_queries:
        key = cache.make_key("recall", agent_id, user_id, query)
        # Simulate loading result
        cache.set(key, f"Pre-warmed result for: {query}")
        print(f"  ✓ Cached: {query}")

    print(f"\nCache warmed: {cache.size()} entries")
    print()


def example_6_adaptive_caching():
    """Example 6: Adaptive cache sizing based on hit rate."""
    print("=" * 60)
    print("Example 6: Adaptive Caching")
    print("=" * 60)

    class AdaptiveCache:
        """Cache that adjusts size based on hit rate."""

        def __init__(self):
            self.cache = QueryCache(max_size=100, ttl=300)
            self.hits = 0
            self.misses = 0

        def get(self, key: str):
            if self.cache.has(key):
                self.hits += 1
                return self.cache.get(key)
            else:
                self.misses += 1
                return None

        def set(self, key: str, value):
            self.cache.set(key, value)

        def hit_rate(self) -> float:
            total = self.hits + self.misses
            return self.hits / total if total > 0 else 0

        def adapt(self):
            """Adjust cache size based on hit rate."""
            rate = self.hit_rate()
            if rate > 0.8 and self.cache.max_size < 1000:
                # High hit rate, increase cache
                self.cache.max_size = min(self.cache.max_size * 2, 1000)
                print(f"  ↑ Increased cache size to {self.cache.max_size}")
            elif rate < 0.3 and self.cache.max_size > 50:
                # Low hit rate, decrease cache
                self.cache.max_size = max(self.cache.max_size // 2, 50)
                print(f"  ↓ Decreased cache size to {self.cache.max_size}")

    adaptive = AdaptiveCache()

    # Simulate queries
    print("Simulating queries...")
    for i in range(100):
        key = f"query_{i % 20}"  # 20 unique queries
        result = adaptive.get(key)
        if result is None:
            adaptive.set(key, f"result_{i}")

        if (i + 1) % 20 == 0:
            print(f"\nAfter {i+1} queries:")
            print(f"  Hit rate: {adaptive.hit_rate():.2%}")
            adaptive.adapt()

    print()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("E.R.I.I. Performance Optimization Examples")
    print("=" * 60 + "\n")

    example_1_basic_query_cache()
    example_2_cached_method_decorator()
    example_3_performance_monitoring()
    example_4_integrated_caching()
    example_5_cache_warming()
    example_6_adaptive_caching()

    print("=" * 60)
    print("All examples completed!")
    print("=" * 60)
