"""Example 03: Embedded SQLite Storage Driver Usage.

Demonstrates using zero-config embedded SQLite database as storage backend.
"""

from erii import ERIIConfig, ERIIEngine, SQLiteStorage


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
    # Instantiate SQLiteStorage driver with custom DB file path
    db_driver = SQLiteStorage(db_path="./example_memory.db")
    config = ERIIConfig(
        storage_dir="./example_sqlite_runtime",
        async_archival=False,
    )
    extractor = DeterministicMemoryExtractor(
        timeline_content="We verified E.R.I.I. memory persistence with SQLite.",
        memory_content="The user prefers relational SQLite persistence.",
        tags=("sqlite", "storage", "persistence"),
    )

    agent_id = "agent_sqlite"
    user_id = "user_test"

    with ERIIEngine(
        storage_driver=db_driver,
        config=config,
        memory_extractor=extractor,
    ) as engine:
        engine.initialize_relationship(
            agent_id,
            user_id,
            "A practical assistant that explains durable storage clearly.",
        )
        engine.set_core_memory(
            agent_id,
            user_id,
            "User prefers relational SQLite persistence.",
        )

        record_and_archive_visible_exchange(
            engine,
            agent_id=agent_id,
            user_id=user_id,
            user_message="Hello, test storing memory in SQLite database.",
            agent_message="SQLite database is initialized and working smoothly!",
            turn_id="sqlite-storage-test-turn",
            actor_id="examples.sqlite-host",
        )

        context = engine.recall(agent_id, user_id, query="sqlite test")

        print("\n--- Recalled Context from SQLite ---")
        print(context)


if __name__ == "__main__":
    main()
