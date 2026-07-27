"""Example 03: Embedded SQLite Storage Driver Usage.

Demonstrates using zero-config embedded SQLite database as storage backend.
"""

from erii import ERIIEngine, SQLiteStorage

def main():
    # Instantiate SQLiteStorage driver with custom DB file path
    db_driver = SQLiteStorage(db_path="./example_memory.db")

    engine = ERIIEngine(
        storage_driver=db_driver
    )

    agent_id = "agent_sqlite"
    user_id = "user_test"

    engine.set_core_memory(agent_id, user_id, "User prefers relational SQLite persistence.")

    engine.remember(
        agent_id=agent_id,
        user_id=user_id,
        user_message="Hello, test storing memory in SQLite database.",
        bot_reply="SQLite database is initialized and working smoothly!"
    )
    engine.process_pending()

    context = engine.recall(agent_id, user_id, query="sqlite test")

    print("\n--- Recalled Context from SQLite ---")
    print(context)

    engine.close()

if __name__ == "__main__":
    main()
