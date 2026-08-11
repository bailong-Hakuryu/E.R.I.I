"""Offline examples for the opt-in E.R.I.I. performance utilities.

Nothing in this file enables caching inside ``ERIIEngine``.  The host owns cache
scope and invalidation.  The integrated example uses a temporary on-disk SQLite
database and explicitly archives a completed Source Turn before recall, so it
runs consistently on Windows as well as POSIX systems.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import time

from erii import (
    ArchivalArtifactsDecision,
    ArchivalStatus,
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    MemoryCandidate,
    MemoryType,
    SQLiteStorage,
)
from erii.performance import BatchLoader, PerformanceMonitor, QueryCache, cached_method


def _delivery_exception() -> dict[str, object]:
    return {
        "exception_record_version": "delivery-exception-record/v1",
        "disposition": "shown_unreviewed",
        "actor_kind": "host_policy",
        "actor_id": "examples.performance-host",
        "reason_code": "preexisting_visible_exchange",
        "decided_at": "2026-08-01T00:00:00+00:00",
        "reply_attempt_number": None,
    }


class _ExampleMemoryExtractor:
    """Deterministic offline host adapter that emits one MemoryNode."""

    descriptor = ExtractorDescriptor(
        extractor_id="examples.performance-memory",
        extractor_version="1",
        extraction_schema_version="2",
    )

    def extract(self, request):
        message = request.transcript.user_message
        evidence = (
            {
                "citation_version": "archival-evidence-citation/v1",
                "kind": "message_span",
                "source_id": message.message_id,
                "source_revision": request.source_revision,
                "quote": message.content,
                "start": 0,
                "end": len(message.content),
            },
        )
        return ArchivalArtifactsDecision(
            memories=(
                MemoryCandidate(
                    node_type=MemoryType.PREFERENCE,
                    content=message.content,
                    tags=("offline-example",),
                    evidence=evidence,
                ),
            )
        )


def example_1_basic_query_cache() -> None:
    """Cache one simulated host query under an explicit relationship scope."""
    print("=" * 60)
    print("Example 1: Basic Query Cache")
    print("=" * 60)
    cache = QueryCache(max_size=1000, ttl=300)
    key = cache.make_key("recall", "agent1", "user1", "what is my name?")

    if cache.has(key):
        result = cache.get(key)
        print("[HIT] Cached result")
    else:
        print("[MISS] Running simulated host query")
        time.sleep(0.01)
        result = "Context for: what is my name?"
        cache.set(key, result)

    print(f"Result: {result}")
    print(f"Cache stats: {cache.stats()}")
    print()


def example_2_cached_method_isolation() -> None:
    """Show that equal calls on two service instances never share results."""
    print("=" * 60)
    print("Example 2: @cached_method Instance Isolation")
    print("=" * 60)

    class ProfileService:
        def __init__(self, scope: str) -> None:
            self.scope = scope
            self.query_count = 0

        @cached_method(ttl=300, max_size=100)
        def get_user_profile(self, user_id: str) -> dict[str, str]:
            self.query_count += 1
            return {"scope": self.scope, "user_id": user_id}

    first = ProfileService("relationship-a")
    second = ProfileService("relationship-b")
    print(f"First call:  {first.get_user_profile('same-user')}")
    print(f"First cached: {first.get_user_profile('same-user')}")
    print(f"Second call: {second.get_user_profile('same-user')}")
    assert first.query_count == 1
    assert second.query_count == 1
    print("[PASS] Equal arguments remained isolated by service instance")
    print()


def example_3_host_controlled_batching() -> None:
    """Accumulate items, then let the host invoke its own batch function."""
    print("=" * 60)
    print("Example 3: Host-Controlled Batching")
    print("=" * 60)
    loader = BatchLoader(window_ms=1000, max_batch_size=3)
    for item in ("memory-a", "memory-b", "memory-c"):
        loader.add(item)

    if loader.should_flush():
        batch = loader.flush()
        results = [item.upper() for item in batch]
        print(f"Flushed {len(batch)} items: {results}")
    assert loader.pending_count() == 0
    print()


def example_4_performance_monitoring() -> None:
    """Collect observed durations without claiming an environment-wide target."""
    print("=" * 60)
    print("Example 4: Performance Monitoring")
    print("=" * 60)
    monitor = PerformanceMonitor()
    for _ in range(5):
        with monitor.measure("simulated-read"):
            time.sleep(0.002)

    metrics = monitor.stats()["simulated-read"]
    print(f"Observed calls: {metrics['count']}")
    print(f"Observed mean:  {metrics['mean'] * 1000:.2f} ms")
    print(f"Observed p95:   {metrics['p95'] * 1000:.2f} ms")
    print()


def example_5_integrated_persisted_recall() -> None:
    """Cache recall only after verifying one synchronously archived node."""
    print("=" * 60)
    print("Example 5: Persisted E.R.I.I. Recall with Host Cache")
    print("=" * 60)

    with TemporaryDirectory() as directory:
        storage = SQLiteStorage(str(Path(directory) / "performance-example.db"))
        engine = ERIIEngine(
            storage_driver=storage,
            memory_extractor=_ExampleMemoryExtractor(),
            config=ERIIConfig(async_archival=False),
        )
        try:
            engine.initialize_relationship(
                "agent1",
                "user1",
                "A character who remembers shared preferences accurately.",
            )
            source = engine.record_turn(
                "agent1",
                "user1",
                "The user likes coffee.",
                "I will remember that.",
                turn_id="performance-example-turn",
                delivery_exception=_delivery_exception(),
            )
            receipt = engine.archive_turn(
                "agent1",
                "user1",
                source.source_turn_id,
                idempotency_key="archive-performance-example-turn",
            )
            assert receipt.status == ArchivalStatus.COMPLETED
            nodes = storage.load_nodes("agent1", "user1")
            assert len(nodes) == 1
            assert nodes[0].source_turn_id == source.source_turn_id
            print("[PASS] Verified one persisted MemoryNode")

            recall_cache = QueryCache(max_size=50, ttl=300)
            monitor = PerformanceMonitor()

            def cached_recall(query: str) -> str:
                key = recall_cache.make_key("recall", "agent1", "user1", query)
                if recall_cache.has(key):
                    print("[HIT] Host recall cache")
                    cached = recall_cache.get(key)
                    assert isinstance(cached, str)
                    return cached
                print("[MISS] E.R.I.I. persisted recall")
                with monitor.measure("erii-recall"):
                    context = engine.recall("agent1", "user1", query)
                recall_cache.set(key, context)
                return context

            first = cached_recall("What does the user like?")
            second = cached_recall("What does the user like?")
            assert first == second
            assert "likes coffee" in first
            assert monitor.stats()["erii-recall"]["count"] == 1
            print(f"Cache stats: {recall_cache.stats()}")
        finally:
            engine.close()
    print()


def example_6_explicit_invalidation() -> None:
    """Demonstrate the host's responsibility to invalidate after writes."""
    print("=" * 60)
    print("Example 6: Explicit Cache Invalidation")
    print("=" * 60)
    cache = QueryCache(max_size=10, ttl=300)
    cache.set(cache.make_key("profile", "agent1", "user1"), {"revision": 1})
    print(f"Before write: {cache.stats()}")
    cache.clear()
    print(f"After write invalidation: {cache.stats()}")
    assert cache.size() == 0
    print()


def main() -> None:
    print("\n" + "=" * 60)
    print("E.R.I.I. Opt-In Performance Utility Examples")
    print("=" * 60 + "\n")
    example_1_basic_query_cache()
    example_2_cached_method_isolation()
    example_3_host_controlled_batching()
    example_4_performance_monitoring()
    example_5_integrated_persisted_recall()
    example_6_explicit_invalidation()
    print("=" * 60)
    print("All offline examples completed and verified.")
    print("=" * 60)


if __name__ == "__main__":
    main()
