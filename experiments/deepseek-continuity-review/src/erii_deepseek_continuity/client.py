"""DeepSeek API Client (experimental).

Key constraints:
- Explicitly sends thinking={"type": "enabled"} or "disabled"
- reasoning_effort as top-level parameter
- Raw reasoning never enters return values
- API key never enters logs/exceptions
- Does not preserve exception chains
"""

import httpx
import time
from typing import Any, Mapping, Callable


class DeepSeekClient:
    """DeepSeek API client for continuity review experiment."""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        thinking_enabled: bool = True,
        reasoning_effort: str = "high",
        timeout_seconds: float = 45.0,
        transport: Callable[[dict], dict] | None = None,
    ):
        """
        Initialize DeepSeek client.

        Args:
            api_key: DeepSeek API key
            model: Model name (deepseek-v4-flash or deepseek-v4-pro)
            thinking_enabled: Whether to enable thinking mode
            reasoning_effort: Effort level (high/max) for thinking mode
            timeout_seconds: Request timeout
            transport: Optional fake transport for testing
        """
        self._api_key = api_key
        self._model = model
        self._thinking_enabled = thinking_enabled
        self._reasoning_effort = reasoning_effort
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def complete(
        self,
        messages: list[dict],
    ) -> Mapping[str, Any]:
        """
        Call DeepSeek API.

        Returns:
            {
                "content": str,              # Model output (JSON string)
                "reasoning_present": bool,   # Whether thinking exists (not content)
                "finish_reason": str,
                "usage": {"prompt_tokens": int, "completion_tokens": int},
                "latency_ms": int,
            }

        Raw reasoning never enters return value.
        """

        # Build payload with explicit thinking switch
        payload = {
            "model": self._model,
            "messages": messages,
            "thinking": {
                "type": "enabled" if self._thinking_enabled else "disabled"
            },
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
        }

        # Add reasoning_effort as top-level parameter (only when enabled)
        if self._thinking_enabled:
            payload["reasoning_effort"] = self._reasoning_effort

        # Call API with error handling
        start_time = time.time()
        error = None

        try:
            if self._transport:
                # Fake transport for testing
                response_data = self._transport(payload)
            else:
                # Real HTTP call
                response_data = self._call_real_api(payload)

        except httpx.TimeoutException:
            error = DeepSeekAPIError("deepseek_timeout")
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            error = DeepSeekAPIError(f"deepseek_http_{status_code}")
        except httpx.RequestError:
            error = DeepSeekAPIError("deepseek_transport_error")
        except Exception:
            error = DeepSeekAPIError("deepseek_unknown_error")

        # Raise outside except block to avoid automatic exception chaining
        if error is not None:
            raise error

        latency_ms = int((time.time() - start_time) * 1000)

        # Extract key fields, discard reasoning_content
        try:
            choice = response_data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError):
            raise DeepSeekAPIError("deepseek_invalid_response_structure")

        return {
            "content": message.get("content", ""),
            "reasoning_present": "reasoning_content" in message,
            "finish_reason": choice.get("finish_reason"),
            "usage": response_data.get("usage", {}),
            "latency_ms": latency_ms,
        }

    def _call_real_api(self, payload: dict) -> dict:
        """Real HTTP call (does not preserve original request/response)."""

        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return response.json()


class DeepSeekAPIError(Exception):
    """DeepSeek API error (contains no sensitive information)."""
    pass
