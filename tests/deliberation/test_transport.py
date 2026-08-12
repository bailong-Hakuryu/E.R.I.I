"""Offline Claude transport, SSE, parser, and thinking-isolation tests."""

import pytest

from erii.deliberation.schemas import CompactDecisionV1
from erii.deliberation.transport import (
    ClaudeResponseParser,
    ClaudeSSEAccumulator,
    ContentBlock,
    ContentBlockType,
    FakeClaudeResponse,
    FakeClaudeSSEEvent,
    FakeClaudeTransport,
    SSEEventType,
    TransportTestScenarios,
)


def test_transport_produces_complete_sse_message() -> None:
    transport = FakeClaudeTransport()
    events = transport.stream_response_with_thinking(
        "untrusted prompt",
        include_sentinel_in_thinking=True,
    )
    response = ClaudeSSEAccumulator().accumulate(events)
    assert response.message_stopped
    assert response.stop_reason == "tool_use"
    assert len(response.get_thinking_blocks()) == 1
    assert len(response.get_tool_use_blocks()) == 1


def test_incomplete_sse_never_becomes_response() -> None:
    events = FakeClaudeTransport().stream_response_with_thinking("prompt")
    with pytest.raises(ValueError, match="incomplete"):
        ClaudeSSEAccumulator().accumulate(events[:-1])


def test_sse_error_fails_closed() -> None:
    events = (
        FakeClaudeSSEEvent(SSEEventType.MESSAGE_START),
        FakeClaudeSSEEvent(SSEEventType.ERROR),
    )
    with pytest.raises(ValueError, match="provider_error"):
        ClaudeSSEAccumulator().accumulate(events)


def test_clean_response_returns_domain_decision() -> None:
    response = TransportTestScenarios.clean_response("prompt")
    success, result, errors = ClaudeResponseParser(
        "SYSTEM_CANARY_DO_NOT_OUTPUT"
    ).parse_response(response)
    assert success
    assert errors == []
    assert isinstance(result, CompactDecisionV1)
    assert result.reply_candidate.parts[0].exact_utf8 == "I understand."


def test_raw_thinking_is_not_present_in_domain_result_or_repr() -> None:
    response = TransportTestScenarios.clean_response("prompt")
    raw = response.get_thinking_blocks()[0].content
    success, result, errors = ClaudeResponseParser(
        "SYSTEM_CANARY_DO_NOT_OUTPUT"
    ).parse_response(response)
    assert success and result is not None and not errors
    assert raw not in result.model_dump_json()
    assert raw not in repr(result)
    assert "SYSTEM_CANARY_DO_NOT_OUTPUT" not in result.model_dump_json()


def test_sentinel_in_final_result_fails_closed() -> None:
    response = TransportTestScenarios.leaked_response("prompt")
    success, result, errors = ClaudeResponseParser(
        "SYSTEM_CANARY_DO_NOT_OUTPUT"
    ).parse_response(response)
    assert not success
    assert result is None
    assert errors == ["sentinel_leak"]


def test_zero_width_sentinel_variant_fails_closed() -> None:
    parser = ClaudeResponseParser("SYSTEM_CANARY")
    bypass = "SYSTEM\u200b_CANARY"
    assert parser._contains_sentinel_recursive({"nested": [bypass]})


def test_multiple_tool_results_fail_closed() -> None:
    valid = TransportTestScenarios.no_thinking_response("prompt")
    block = valid.get_tool_use_blocks()[0]
    response = FakeClaudeResponse(
        content_blocks=(block, ContentBlock(ContentBlockType.TOOL_USE, block.content, 1)),
        stop_reason="tool_use",
        usage={},
    )
    success, result, errors = ClaudeResponseParser("CANARY").parse_response(response)
    assert not success
    assert result is None
    assert errors == ["exactly_one_tool_result_required"]


@pytest.mark.parametrize("stop_reason", ["end_turn", "max_tokens", "refusal"])
def test_non_tool_stop_reasons_fail_closed(stop_reason: str) -> None:
    valid = TransportTestScenarios.no_thinking_response("prompt")
    response = FakeClaudeResponse(valid.content_blocks, stop_reason, {})
    success, result, errors = ClaudeResponseParser("CANARY").parse_response(response)
    assert not success
    assert result is None
    assert errors == ["unsupported_stop_reason"]


def test_text_and_tool_result_is_ambiguous() -> None:
    valid = TransportTestScenarios.no_thinking_response("prompt")
    response = FakeClaudeResponse(
        content_blocks=(
            ContentBlock(ContentBlockType.TEXT, "extra final", 0),
            ContentBlock(ContentBlockType.TOOL_USE, valid.get_tool_use_blocks()[0].content, 1),
        ),
        stop_reason="tool_use",
        usage={},
    )
    success, result, errors = ClaudeResponseParser("CANARY").parse_response(response)
    assert not success
    assert result is None
    assert errors == ["ambiguous_final_output"]


def test_no_tool_result_fails_closed() -> None:
    response = FakeClaudeResponse(
        content_blocks=(ContentBlock(ContentBlockType.THINKING, "private", 0),),
        stop_reason="tool_use",
        usage={},
    )
    success, result, errors = ClaudeResponseParser("CANARY").parse_response(response)
    assert not success
    assert result is None
    assert errors == ["exactly_one_tool_result_required"]


def test_response_repr_hides_raw_content_and_usage() -> None:
    response = TransportTestScenarios.clean_response("prompt")
    rendered = repr(response)
    assert "private provider reasoning" not in rendered
    assert "input_tokens" not in rendered
