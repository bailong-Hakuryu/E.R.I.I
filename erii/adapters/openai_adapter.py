"""OpenAI-compatible LLM Adapter for E.R.I.I. Engine.

Supports both official `openai` SDK and standard library HTTP fallback.
Follows Google Python Style Guide.
"""

import json
import logging
import urllib.error
import urllib.request

from erii.adapters.base import BaseLLMAdapter

logger = logging.getLogger("erii")


class OpenAIAdapter(BaseLLMAdapter):
    """Adapter for OpenAI, Ollama, vLLM, and OpenAI-compatible API endpoints."""

    def __init__(
        self,
        api_key: str = "sk-placeholder",
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
        max_tokens: int = 1000,
        timeout: int = 30,
    ) -> None:
        """Initializes OpenAIAdapter.

        Args:
            api_key: API secret key.
            base_url: Base endpoint URL.
            model: Model identifier.
            temperature: Sampling temperature.
            max_tokens: Maximum response tokens.
            timeout: HTTP request timeout in seconds.
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def generate(self, prompt: str) -> str:
        """Generates response via OpenAI API format.

        Args:
            prompt: Text prompt string.

        Returns:
            Generated response content string.

        Raises:
            RuntimeError: If request fails.
        """
        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a precise JSON memory extraction tool. Output strictly valid JSON without extra markdown formatting.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
                choices = body.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
                return ""
        except urllib.error.URLError as e:
            logger.error("OpenAI API request failed: %s", str(e))
            raise RuntimeError(f"OpenAI API call error: {str(e)}") from e
        except Exception as e:
            logger.error("Unexpected error in OpenAIAdapter: %s", str(e))
            raise RuntimeError(f"Unexpected OpenAIAdapter error: {str(e)}") from e
