"""Current DeepSeek V4 transport and privacy contracts."""

from erii_deepseek_continuity import DeepSeekClient
from erii_deepseek_continuity.client import DEEPSEEK_CHAT_COMPLETIONS_URL


def _successful_response(*, usage: object = None) -> dict:
    return {
        "choices": [
            {
                "message": {"content": '{"findings":[]}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {} if usage is None else usage,
    }


def test_low_reasoning_effort_is_forwarded_for_current_v4_contract() -> None:
    captured: dict = {}

    def transport(payload: dict) -> dict:
        captured.update(payload)
        return _successful_response()

    client = DeepSeekClient(
        api_key="test-key",
        reasoning_effort="low",
        transport=transport,
    )

    client.complete([{"role": "user", "content": "test"}])

    assert captured["model"] == "deepseek-v4-flash"
    assert captured["thinking"] == {"type": "enabled"}
    assert captured["reasoning_effort"] == "low"


def test_usage_metadata_is_reduced_to_non_negative_token_counters() -> None:
    client = DeepSeekClient(
        api_key="test-key",
        transport=lambda payload: _successful_response(
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 7,
                "total_tokens": 19,
                "completion_tokens_details": {
                    "reasoning_tokens": 5,
                    "provider_debug": "must not leave the client boundary",
                },
                "provider_debug": "must not leave the client boundary",
                "negative_counter": -1,
            }
        ),
    )

    result = client.complete([{"role": "user", "content": "test"}])

    assert result["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19,
        "completion_tokens_details": {"reasoning_tokens": 5},
    }
    assert client.last_usage == result["usage"]


def test_real_transport_uses_the_current_official_chat_endpoint(monkeypatch) -> None:
    captured: dict = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return _successful_response()

    class Client:
        def __init__(self, *, timeout: float) -> None:
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def post(self, url: str, **kwargs) -> Response:
            captured["url"] = url
            captured["headers"] = kwargs["headers"]
            captured["json"] = kwargs["json"]
            return Response()

    monkeypatch.setattr("erii_deepseek_continuity.client.httpx.Client", Client)
    client = DeepSeekClient(api_key="test-key")

    client.complete([{"role": "user", "content": "test"}])

    assert captured["url"] == DEEPSEEK_CHAT_COMPLETIONS_URL
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
