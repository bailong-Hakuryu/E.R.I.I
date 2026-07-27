"""Custom Callable LLM Adapter for E.R.I.I. Engine.

Allows 1-line integration with any custom function, framework, or model endpoint.
Follows Google Python Style Guide.
"""

from typing import Callable
from erii.adapters.base import BaseLLMAdapter


class CallableLLMAdapter(BaseLLMAdapter):
    """Adapter that wraps any Python callable function into a BaseLLMAdapter."""

    def __init__(self, fn: Callable[[str], str]) -> None:
        """Initializes CallableLLMAdapter.

        Args:
            fn: Callable object accepting prompt str and returning str response.

        Raises:
            TypeError: If provided argument is not callable.
        """
        if not callable(fn):
            raise TypeError("CallableLLMAdapter requires a callable function.")
        self._fn = fn

    def generate(self, prompt: str) -> str:
        """Generates LLM response by invoking the underlying callable.

        Args:
            prompt: Text prompt string.

        Returns:
            Generated text string response.
        """
        result = self._fn(prompt)
        if not isinstance(result, str):
            return str(result)
        return result
