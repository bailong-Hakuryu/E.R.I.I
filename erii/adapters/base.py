"""Base LLM Adapter protocol interface for E.R.I.I. Engine.

Follows Google Python Style Guide.
"""

from abc import ABC, abstractmethod


class BaseLLMAdapter(ABC):
    """Abstract protocol for LLM provider adapters."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Generates text response from LLM provider for given prompt.

        Args:
            prompt: Text prompt string.

        Returns:
            Generated text string response.

        Raises:
            RuntimeError: If LLM call fails.
        """
        pass
