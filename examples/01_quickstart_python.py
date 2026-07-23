"""Example 01: Quickstart Python Integration with E.R.I.I. Engine.

Demonstrates basic usage of memory recording and selective recall prompt injection.
"""

from erii import ERIIEngine, ERIIConfig, SQLiteStorage

def main():
    print("=== E.R.I.I. Quickstart Example ===")

    # 1. Initialize E.R.I.I. Engine
    config = ERIIConfig(storage_dir="./example_memory")
    engine = ERIIEngine(config=config)

    # 2. Set core persona memory for agent
    agent_id = "assistant_alice"
    user_id = "user_bob"
    
    engine.set_core_memory(
        agent_id=agent_id,
        user_id=user_id,
        content="Bob is a senior software engineer who prefers clean architecture and concise responses."
    )

    # 3. Record conversation turn
    print("\nRecording conversation turn...")
    engine.remember(
        agent_id=agent_id,
        user_id=user_id,
        user_message="I love brewing Earl Grey black tea with lavender on quiet rainy afternoons.",
        bot_reply="That sounds incredibly relaxing! Earl Grey with lavender is a wonderful blend."
    )

    # 4. Recall prompt context
    print("\nRecalling relevant memory context...")
    context = engine.recall(
        agent_id=agent_id,
        user_id=user_id,
        query="What kind of tea do I like?"
    )

    print("\n--- Formatted Prompt Context for LLM System Prompt ---")
    print(context)
    print("-----------------------------------------------------")

    engine.close()

if __name__ == "__main__":
    main()
