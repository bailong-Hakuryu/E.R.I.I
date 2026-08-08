"""DeepSeek API Client (experimental).

Key constraints:
- Explicitly sends thinking={"type": "enabled"} or "disabled"
- reasoning_effort as top-level parameter
- Raw reasoning never enters return values
- API key never enters logs/exceptions
- Does not preserve exception chains
"""

import time
from typing import Any, Callable, Mapping

import httpx


DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
_USAGE_COUNTER_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


class DeepSeekClient:
    """DeepSeek API client for continuity review experiment."""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        thinking_enabled: bool = True,
        reasoning_effort: str = "high",
        timeout_seconds: float = 45.0,
        max_tokens: int = 4096,
        transport: Callable[[dict], dict] | None = None,
    ):
        """
        Initialize DeepSeek client.

        Args:
            api_key: DeepSeek API key
            model: Model name (deepseek-v4-flash or deepseek-v4-pro)
            thinking_enabled: Whether to enable thinking mode
            reasoning_effort: Effort level (low/high/max) for thinking mode
            timeout_seconds: Request timeout
            max_tokens: Maximum tokens for completion
            transport: Optional fake transport for testing
        """
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("api_key_must_be_non_empty")
        if not isinstance(model, str) or not model:
            raise ValueError("model_must_be_non_empty")
        if not isinstance(thinking_enabled, bool):
            raise ValueError("thinking_enabled_must_be_boolean")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds_must_be_positive")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
            raise ValueError("max_tokens_must_be_positive")
        if reasoning_effort not in {"low", "high", "max"}:
            raise ValueError("unsupported_reasoning_effort")
        if transport is not None and not callable(transport):
            raise ValueError("transport_must_be_callable")

        self._api_key = api_key
        self._model = model
        self._thinking_enabled = thinking_enabled
        self._reasoning_effort = reasoning_effort
        self._timeout_seconds = timeout_seconds
        self._max_tokens = max_tokens
        self._transport = transport
        self._last_usage: Mapping[str, Any] = {}

    @property
    def last_usage(self) -> Mapping[str, Any]:
        """Return only the sanitized token counters from the latest call."""
        return self._last_usage

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
                "usage": {
                    "prompt_tokens": int,
                    "completion_tokens": int,
                    "total_tokens": int,
                    "completion_tokens_details": {"reasoning_tokens": int},
                },
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
            "max_tokens": self._max_tokens,
        }

        # Add reasoning_effort as top-level parameter (only when enabled)
        if self._thinking_enabled:
            payload["reasoning_effort"] = self._reasoning_effort

        # Call API with error handling
        start_time = time.monotonic()
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
            raise error from None

        latency_ms = int((time.monotonic() - start_time) * 1000)

        # Extract key fields, discard reasoning_content
        try:
            choice = response_data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise DeepSeekAPIError("deepseek_invalid_response_structure") from None

        if not isinstance(choice, Mapping) or not isinstance(message, Mapping):
            raise DeepSeekAPIError("deepseek_invalid_response_structure") from None
        usage = response_data.get("usage", {})
        self._last_usage = _sanitized_usage(usage)
        if choice.get("finish_reason") != "stop":
            raise DeepSeekAPIError("deepseek_incomplete_response") from None

        result = {
            "content": message.get("content", ""),
            "reasoning_present": "reasoning_content" in message,
            "finish_reason": choice.get("finish_reason"),
            "usage": self._last_usage,
            "latency_ms": latency_ms,
        }

        return result

    def _call_real_api(self, payload: dict) -> dict:
        """Real HTTP call (does not preserve original request/response)."""

        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.post(
                DEEPSEEK_CHAT_COMPLETIONS_URL,
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


def _sanitized_usage(value: object) -> Mapping[str, Any]:
    """Retain only non-negative token counters from provider usage metadata."""
    if not isinstance(value, Mapping):
        return {}
    counters: dict[str, Any] = {
        field: counter
        for field in _USAGE_COUNTER_FIELDS
        if isinstance((counter := value.get(field)), int)
        and not isinstance(counter, bool)
        and counter >= 0
    }
    completion_details = value.get("completion_tokens_details")
    if isinstance(completion_details, Mapping):
        reasoning_tokens = completion_details.get("reasoning_tokens")
        if (
            isinstance(reasoning_tokens, int)
            and not isinstance(reasoning_tokens, bool)
            and reasoning_tokens >= 0
        ):
            counters["completion_tokens_details"] = {
                "reasoning_tokens": reasoning_tokens,
            }
    return counters
