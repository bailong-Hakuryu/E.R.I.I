"""Example 02: 1-Line Custom Callable LLM Adapter Integration.

Demonstrates how to pass any custom LLM function into E.R.I.I. Engine.
"""

import json
from erii import ERIIEngine

def my_custom_llm_function(prompt: str) -> str:
    """Custom LLM function wrapper (e.g. OpenAI, Ollama, vLLM, custom HTTP API)."""
    print(f"\n[Custom LLM Invoked with Prompt Length: {len(prompt)} chars]")
    # Return structured JSON memory extraction result
    return json.dumps({
        "timeline_entry": "I learned about Bob's preference for dark mode IDE themes.",
        "impressions": [
            {
                "type": "preference",
                "content": "Prefers dark mode IDE themes for programming",
                "base_importance": 0.8,
                "emotional_score": 0.2,
                "tags": ["ide", "theme", "preference"]
            }
        ]
    })

def main():
    # Pass custom function directly to llm parameter!
    engine = ERIIEngine(
        storage_dir="./example_custom_llm_memory",
        llm=my_custom_llm_function
    )

    agent_id = "agent_coder"
    user_id = "user_dev"

    engine.remember(
        agent_id=agent_id,
        user_id=user_id,
        user_message="I always set my editor to dark mode with solarized dark theme.",
        bot_reply="Dark mode is essential for night coding sessions!"
    )

    # Wait briefly for background archival
    import time
    time.sleep(1)

    context = engine.recall(
        agent_id=agent_id,
        user_id=user_id,
        query="editor theme preference"
    )

    print("\n--- Recalled Context ---")
    print(context)

    engine.close()

if __name__ == "__main__":
    main()
