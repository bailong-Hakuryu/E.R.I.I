"""Test DeepSeek client API failure handling."""

import sys
sys.path.insert(0, 'D:/bate/erii')
sys.path.insert(0, 'D:/bate/erii/experiments/deepseek-continuity-review/src')

from erii_deepseek_continuity import DeepSeekClient, DeepSeekAPIError
import httpx


def test_timeout_error():
    """Test timeout error handling."""
    print("Test: Timeout error...")

    def timeout_transport(payload):
        raise httpx.TimeoutException("Request timeout")

    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=True,
        transport=timeout_transport,
    )

    try:
        client.complete([{"role": "user", "content": "test"}])
        assert False, "Should have raised DeepSeekAPIError"
    except DeepSeekAPIError as e:
        assert str(e) == "deepseek_timeout"
        # Verify no sensitive info in exception
        assert "fake-key" not in str(e)
        assert "TimeoutException" not in str(e)
    print("OK")


def test_http_404_error():
    """Test HTTP 404 error handling."""
    print("Test: HTTP 404 error...")

    def http_404_transport(payload):
        response = httpx.Response(
            status_code=404,
            json={"error": "Not found"},
        )
        raise httpx.HTTPStatusError(
            "404 Not Found",
            request=httpx.Request("POST", "https://api.deepseek.com"),
            response=response,
        )

    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=True,
        transport=http_404_transport,
    )

    try:
        client.complete([{"role": "user", "content": "test"}])
        assert False, "Should have raised DeepSeekAPIError"
    except DeepSeekAPIError as e:
        assert str(e) == "deepseek_http_404"
        # Verify no sensitive info leaked
        assert "fake-key" not in str(e)
        assert "Not found" not in str(e)  # Response body not included
    print("OK")


def test_http_500_error():
    """Test HTTP 500 error handling."""
    print("Test: HTTP 500 error...")

    def http_500_transport(payload):
        response = httpx.Response(
            status_code=500,
            json={"error": "Internal server error"},
        )
        raise httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=httpx.Request("POST", "https://api.deepseek.com"),
            response=response,
        )

    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=True,
        transport=http_500_transport,
    )

    try:
        client.complete([{"role": "user", "content": "test"}])
        assert False, "Should have raised DeepSeekAPIError"
    except DeepSeekAPIError as e:
        assert str(e) == "deepseek_http_500"
    print("OK")


def test_request_error():
    """Test generic request error handling."""
    print("Test: Request error...")

    def request_error_transport(payload):
        raise httpx.RequestError("Connection failed")

    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=True,
        transport=request_error_transport,
    )

    try:
        client.complete([{"role": "user", "content": "test"}])
        assert False, "Should have raised DeepSeekAPIError"
    except DeepSeekAPIError as e:
        assert str(e) == "deepseek_transport_error"
        assert "Connection failed" not in str(e)
    print("OK")


def test_invalid_response_structure():
    """Test invalid response structure handling."""
    print("Test: Invalid response structure...")

    def invalid_structure_transport(payload):
        return {"invalid": "structure"}

    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=True,
        transport=invalid_structure_transport,
    )

    try:
        client.complete([{"role": "user", "content": "test"}])
        assert False, "Should have raised DeepSeekAPIError"
    except DeepSeekAPIError as e:
        assert str(e) == "deepseek_invalid_response_structure"
    print("OK")


def test_no_exception_chain_leakage():
    """Test that exception chain doesn't leak sensitive info."""
    print("Test: No exception chain leakage...")

    def leak_attempt_transport(payload):
        # Try to leak API key through exception
        raise httpx.HTTPStatusError(
            f"Auth failed with key: {payload}",
            request=httpx.Request("POST", "https://api.deepseek.com"),
            response=httpx.Response(401, json={"error": "Unauthorized"}),
        )

    client = DeepSeekClient(
        api_key="secret-api-key-12345",
        thinking_enabled=True,
        transport=leak_attempt_transport,
    )

    try:
        client.complete([{"role": "user", "content": "test"}])
        assert False, "Should have raised DeepSeekAPIError"
    except DeepSeekAPIError as e:
        # Check that API key is not in exception or its chain
        error_str = str(e)
        assert "secret-api-key" not in error_str

        # Check __cause__ and __context__
        if e.__cause__:
            assert "secret-api-key" not in str(e.__cause__)
        if e.__context__:
            assert "secret-api-key" not in str(e.__context__)

    print("OK")


def test_thinking_enabled_explicitly_sent():
    """Test that thinking=enabled is explicitly sent."""
    print("Test: Thinking enabled explicitly sent...")

    captured_payload = {}

    def capture_transport(payload):
        captured_payload.update(payload)
        return {
            "choices": [{
                "message": {"content": "test"},
                "finish_reason": "stop",
            }],
            "usage": {},
        }

    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=True,
        reasoning_effort="high",
        transport=capture_transport,
    )

    client.complete([{"role": "user", "content": "test"}])

    assert "thinking" in captured_payload
    assert captured_payload["thinking"]["type"] == "enabled"
    assert "reasoning_effort" in captured_payload
    assert captured_payload["reasoning_effort"] == "high"
    print("OK")


def test_thinking_disabled_explicitly_sent():
    """Test that thinking=disabled is explicitly sent."""
    print("Test: Thinking disabled explicitly sent...")

    captured_payload = {}

    def capture_transport(payload):
        captured_payload.update(payload)
        return {
            "choices": [{
                "message": {"content": "test"},
                "finish_reason": "stop",
            }],
            "usage": {},
        }

    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=False,
        transport=capture_transport,
    )

    client.complete([{"role": "user", "content": "test"}])

    assert "thinking" in captured_payload
    assert captured_payload["thinking"]["type"] == "disabled"
    assert "reasoning_effort" not in captured_payload
    print("OK")


if __name__ == "__main__":
    tests = [
        test_timeout_error,
        test_http_404_error,
        test_http_500_error,
        test_request_error,
        test_invalid_response_structure,
        test_no_exception_chain_leakage,
        test_thinking_enabled_explicitly_sent,
        test_thinking_disabled_explicitly_sent,
    ]

    failed = 0
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    if failed > 0:
        sys.exit(1)
