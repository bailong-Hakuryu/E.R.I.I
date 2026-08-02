"""Example 01: Quickstart Python Integration with E.R.I.I. Engine.

Demonstrates basic usage of memory recording and selective recall prompt injection.
"""

from erii import ERIIEngine, ERIIConfig


if __package__:
    from ._shared import (
        DeterministicMemoryExtractor,
        record_and_archive_visible_exchange,
    )
else:
    from _shared import (  # type: ignore[import-not-found]
        DeterministicMemoryExtractor,
        record_and_archive_visible_exchange,
    )


def main():
    print("=== E.R.I.I. Quickstart Example ===")

    # 1. Initialize E.R.I.I. with explicit, synchronous archival.
    config = ERIIConfig(
        storage_dir="./example_memory",
        async_archival=False,
    )
    extractor = DeterministicMemoryExtractor(
        timeline_content="Bob shared his rainy-afternoon tea ritual with Alice.",
        memory_content="Bob enjoys Earl Grey tea with lavender on rainy afternoons.",
        tags=("tea", "earl-grey", "lavender"),
    )

    agent_id = "assistant_alice"
    user_id = "user_bob"

    with ERIIEngine(config=config, memory_extractor=extractor) as engine:
        # 2. Establish the immutable persona and this isolated relationship.
        engine.initialize_relationship(
            agent_id,
            user_id,
            "Alice is a helpful assistant who values clean architecture and concise replies.",
        )
        engine.set_core_memory(
            agent_id=agent_id,
            user_id=user_id,
            content=(
                "Bob is a senior software engineer who prefers clean architecture "
                "and concise responses."
            ),
        )

        # 3. Record the already-visible exchange, then archive it inline.
        print("\nRecording and archiving conversation turn...")
        record_and_archive_visible_exchange(
            engine,
            agent_id=agent_id,
            user_id=user_id,
            user_message=(
                "I love brewing Earl Grey black tea with lavender on quiet "
                "rainy afternoons."
            ),
            agent_message=(
                "That sounds incredibly relaxing! Earl Grey with lavender is "
                "a wonderful blend."
            ),
            turn_id="quickstart-tea-turn",
            actor_id="examples.quickstart-host",
        )

        # 4. Recall prompt context.
        print("\nRecalling relevant memory context...")
        context = engine.recall(
            agent_id=agent_id,
            user_id=user_id,
            query="What kind of tea do I like?",
        )

        print("\n--- Formatted Prompt Context for LLM System Prompt ---")
        print(context)
        print("-----------------------------------------------------")


if __name__ == "__main__":
    main()
