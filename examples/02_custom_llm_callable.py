"""Example 02: Custom Callable LLM Host Integration.

Demonstrates how to pass any custom LLM function into E.R.I.I. Engine.
"""

import json
from erii import ERIIConfig, ERIIEngine


if __package__:
    from ._shared import (
        CallableJSONMemoryExtractor,
        record_and_archive_visible_exchange,
    )
else:
    from _shared import (  # type: ignore[import-not-found]
        CallableJSONMemoryExtractor,
        record_and_archive_visible_exchange,
    )


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
    # The same callable can serve normal generation and an explicit host extractor.
    config = ERIIConfig(
        storage_dir="./example_custom_llm_memory",
        async_archival=False,
    )
    extractor = CallableJSONMemoryExtractor(my_custom_llm_function)

    agent_id = "agent_coder"
    user_id = "user_dev"

    with ERIIEngine(
        config=config,
        llm=my_custom_llm_function,
        memory_extractor=extractor,
    ) as engine:
        engine.initialize_relationship(
            agent_id,
            user_id,
            "Coder is a practical assistant who enjoys helping with software tools.",
        )
        record_and_archive_visible_exchange(
            engine,
            agent_id=agent_id,
            user_id=user_id,
            user_message=(
                "I always set my editor to dark mode with solarized dark theme."
            ),
            agent_message="Dark mode is essential for night coding sessions!",
            turn_id="custom-llm-dark-mode-turn",
            actor_id="examples.custom-llm-host",
        )

        context = engine.recall(
            agent_id=agent_id,
            user_id=user_id,
            query="editor theme preference",
        )

        print("\n--- Recalled Context ---")
        print(context)


if __name__ == "__main__":
    main()
