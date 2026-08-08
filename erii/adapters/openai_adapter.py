"""OpenAI-compatible LLM Adapter for E.R.I.I. Engine.

Supports both official `openai` SDK and standard library HTTP fallback.
API keys must be provided via environment variables for security.
Follows Google Python Style Guide.
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from erii.adapters.base import BaseLLMAdapter
from erii.security.credential_manager import CredentialManager

logger = logging.getLogger("erii")


class OpenAIAdapter(BaseLLMAdapter):
    """Adapter for OpenAI, Ollama, vLLM, and OpenAI-compatible API endpoints.

    Security: API keys must be provided via environment variables (OPENAI_API_KEY)
    or custom environment variables. Direct key strings are deprecated.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
        max_tokens: int = 1000,
        timeout: int = 30,
        api_key_env: Optional[str] = None,
    ) -> None:
        """Initializes OpenAIAdapter.

        Args:
            api_key: DEPRECATED. API secret key. For security, use api_key_env instead.
                If provided, must be a valid key (not placeholder). If None, loads from
                environment variable.
            base_url: Base endpoint URL.
            model: Model identifier.
            temperature: Sampling temperature.
            max_tokens: Maximum response tokens.
            timeout: HTTP request timeout in seconds.
            api_key_env: Custom environment variable name for API key.
                If None, uses OPENAI_API_KEY.

        Raises:
            CredentialError: If API key cannot be loaded or is invalid.
        """
        # Load API key securely from environment
        if api_key is None:
            # Secure path: load from environment
            self.api_key = CredentialManager.get_api_key(
                provider="openai",
                env_var=api_key_env,
                required=True
            )
            logger.info(
                "Loaded OpenAI API key from environment (fingerprint: %s)",
                CredentialManager.get_key_fingerprint(self.api_key)
            )
        else:
            # Legacy path: accept direct key but warn
            if api_key == "sk-placeholder":
                # Reject placeholder keys
                logger.error("Placeholder API key detected. Use environment variables.")
                raise ValueError(
                    "Placeholder API key 'sk-placeholder' is not valid. "
                    "Set OPENAI_API_KEY environment variable or use api_key_env parameter."
                )
            logger.warning(
                "Direct API key parameter is deprecated for security. "
                "Use environment variables instead (OPENAI_API_KEY or api_key_env parameter)."
            )
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
            # Log error without exposing API key
            logger.error(
                "OpenAI API request failed for model %s: %s",
                self.model,
                str(e)
            )
            raise RuntimeError(f"OpenAI API call error: {str(e)}") from e
        except Exception as e:
            logger.error(
                "Unexpected error in OpenAIAdapter (model: %s): %s",
                self.model,
                str(e)
            )
            raise RuntimeError(f"Unexpected OpenAIAdapter error: {str(e)}") from e
