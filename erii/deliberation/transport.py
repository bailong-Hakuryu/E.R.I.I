"""Offline Claude-shaped transport fixtures and strict response parsing.

This module performs no network I/O.  It models the important Messages API
boundary: streaming events are accumulated to a complete message, raw thinking
blocks are discarded, and exactly one final structured result is decoded into
the provider-neutral domain schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import unicodedata
from typing import Any, Iterable

from .schemas import CompactDecisionV1
from .strict_codec import StrictCanonicalCodec


class ContentBlockType(str, Enum):
    THINKING = "thinking"
    REDACTED_THINKING = "redacted_thinking"
    SIGNATURE = "signature"
    TEXT = "text"
    TOOL_USE = "tool_use"


@dataclass(frozen=True)
class ContentBlock:
    type: ContentBlockType
    content: str = field(repr=False)
    index: int

    def contains_sentinel(self, sentinel: str) -> bool:
        return _canonical_probe(sentinel) in _canonical_probe(self.content)


@dataclass(frozen=True)
class FakeClaudeResponse:
    content_blocks: tuple[ContentBlock, ...]
    stop_reason: str
    usage: dict[str, int] = field(repr=False)
    message_stopped: bool = True

    def get_thinking_blocks(self) -> list[ContentBlock]:
        private = {
            ContentBlockType.THINKING,
            ContentBlockType.REDACTED_THINKING,
            ContentBlockType.SIGNATURE,
        }
        return [block for block in self.content_blocks if block.type in private]

    def get_text_blocks(self) -> list[ContentBlock]:
        return [block for block in self.content_blocks if block.type is ContentBlockType.TEXT]

    def get_tool_use_blocks(self) -> list[ContentBlock]:
        return [block for block in self.content_blocks if block.type is ContentBlockType.TOOL_USE]


class SSEEventType(str, Enum):
    MESSAGE_START = "message_start"
    CONTENT_BLOCK_START = "content_block_start"
    CONTENT_BLOCK_DELTA = "content_block_delta"
    CONTENT_BLOCK_STOP = "content_block_stop"
    MESSAGE_DELTA = "message_delta"
    MESSAGE_STOP = "message_stop"
    ERROR = "error"


@dataclass(frozen=True)
class FakeClaudeSSEEvent:
    event_type: SSEEventType
    index: int | None = None
    block_type: ContentBlockType | None = None
    delta: str = field(default="", repr=False)
    stop_reason: str | None = None
    usage: dict[str, int] | None = field(default=None, repr=False)


class ClaudeSSEAccumulator:
    """Fail-closed accumulator; partial streams never become results."""

    def accumulate(self, events: Iterable[FakeClaudeSSEEvent]) -> FakeClaudeResponse:
        started = False
        stopped = False
        open_blocks: dict[int, tuple[ContentBlockType, list[str]]] = {}
        completed: list[ContentBlock] = []
        stop_reason: str | None = None
        usage: dict[str, int] = {}

        for event in events:
            if stopped:
                raise ValueError("sse_event_after_message_stop")
            if event.event_type is SSEEventType.ERROR:
                raise ValueError("sse_provider_error")
            if event.event_type is SSEEventType.MESSAGE_START:
                if started:
                    raise ValueError("sse_duplicate_message_start")
                started = True
                continue
            if not started:
                raise ValueError("sse_message_start_required")
            if event.event_type is SSEEventType.CONTENT_BLOCK_START:
                if event.index is None or event.block_type is None or event.index in open_blocks:
                    raise ValueError("sse_invalid_block_start")
                open_blocks[event.index] = (event.block_type, [])
            elif event.event_type is SSEEventType.CONTENT_BLOCK_DELTA:
                if event.index not in open_blocks:
                    raise ValueError("sse_delta_without_open_block")
                open_blocks[event.index][1].append(event.delta)
            elif event.event_type is SSEEventType.CONTENT_BLOCK_STOP:
                if event.index not in open_blocks:
                    raise ValueError("sse_stop_without_open_block")
                block_type, chunks = open_blocks.pop(event.index)
                completed.append(ContentBlock(block_type, "".join(chunks), event.index))
            elif event.event_type is SSEEventType.MESSAGE_DELTA:
                if event.stop_reason is not None:
                    stop_reason = event.stop_reason
                if event.usage is not None:
                    usage = dict(event.usage)
            elif event.event_type is SSEEventType.MESSAGE_STOP:
                if open_blocks:
                    raise ValueError("sse_message_stopped_with_open_block")
                stopped = True

        if not started or not stopped:
            raise ValueError("sse_incomplete_message")
        if stop_reason is None:
            raise ValueError("sse_missing_stop_reason")
        completed.sort(key=lambda block: block.index)
        if len({block.index for block in completed}) != len(completed):
            raise ValueError("sse_duplicate_block_index")
        return FakeClaudeResponse(tuple(completed), stop_reason, usage, message_stopped=True)


class FakeClaudeTransport:
    """Deterministic offline Messages API fixture."""

    def __init__(self, sentinel: str = "SYSTEM_CANARY_DO_NOT_OUTPUT") -> None:
        if not _canonical_probe(sentinel):
            raise ValueError("sentinel must contain visible characters")
        self.sentinel = sentinel

    def stream_response_with_thinking(
        self,
        prompt: str,
        include_sentinel_in_thinking: bool = False,
        include_sentinel_in_output: bool = False,
    ) -> tuple[FakeClaudeSSEEvent, ...]:
        del prompt  # untrusted prompt text is not echoed into transport diagnostics
        thinking = "private provider reasoning fixture"
        if include_sentinel_in_thinking:
            thinking += f" {self.sentinel}"
        payload = _canonical_decision_payload(
            output_suffix=f" {self.sentinel}" if include_sentinel_in_output else ""
        )
        blocks = (
            (0, ContentBlockType.THINKING, thinking),
            (1, ContentBlockType.TOOL_USE, StrictCanonicalCodec.serialize(payload)),
        )
        events: list[FakeClaudeSSEEvent] = [
            FakeClaudeSSEEvent(SSEEventType.MESSAGE_START)
        ]
        for index, block_type, content in blocks:
            events.extend(
                (
                    FakeClaudeSSEEvent(
                        SSEEventType.CONTENT_BLOCK_START,
                        index=index,
                        block_type=block_type,
                    ),
                    FakeClaudeSSEEvent(
                        SSEEventType.CONTENT_BLOCK_DELTA,
                        index=index,
                        delta=content,
                    ),
                    FakeClaudeSSEEvent(SSEEventType.CONTENT_BLOCK_STOP, index=index),
                )
            )
        events.extend(
            (
                FakeClaudeSSEEvent(
                    SSEEventType.MESSAGE_DELTA,
                    stop_reason="tool_use",
                    usage={"input_tokens": 100, "output_tokens": 50},
                ),
                FakeClaudeSSEEvent(SSEEventType.MESSAGE_STOP),
            )
        )
        return tuple(events)

    def generate_response_with_thinking(
        self,
        prompt: str,
        include_sentinel_in_thinking: bool = False,
        include_sentinel_in_output: bool = False,
    ) -> FakeClaudeResponse:
        events = self.stream_response_with_thinking(
            prompt,
            include_sentinel_in_thinking,
            include_sentinel_in_output,
        )
        return ClaudeSSEAccumulator().accumulate(events)


class ClaudeResponseParser:
    """Whitelist parser for one complete strict-tool structured result."""

    def __init__(self, sentinel: str) -> None:
        normalized = _canonical_probe(sentinel)
        if not normalized:
            raise ValueError("sentinel must contain visible characters")
        self.sentinel = sentinel
        self._normalized_sentinel = normalized

    def parse_response(
        self,
        response: FakeClaudeResponse,
    ) -> tuple[bool, CompactDecisionV1 | None, list[str]]:
        if not response.message_stopped:
            return False, None, ["incomplete_message"]
        if response.stop_reason != "tool_use":
            return False, None, ["unsupported_stop_reason"]
        tool_blocks = response.get_tool_use_blocks()
        if len(tool_blocks) != 1:
            return False, None, ["exactly_one_tool_result_required"]
        if response.get_text_blocks():
            return False, None, ["ambiguous_final_output"]

        content = tool_blocks[0].content
        if self._contains_sentinel_text(content):
            return False, None, ["sentinel_leak"]
        try:
            decision = StrictCanonicalCodec.decode_as(content, CompactDecisionV1)
        except ValueError:
            return False, None, ["invalid_structured_result"]
        if self._contains_sentinel_recursive(decision.model_dump(mode="json")):
            return False, None, ["sentinel_leak"]
        return True, decision, []

    def _contains_sentinel_text(self, text: str) -> bool:
        return self._normalized_sentinel in _canonical_probe(text)

    def _contains_sentinel_recursive(self, obj: Any) -> bool:
        if isinstance(obj, str):
            return self._contains_sentinel_text(obj)
        if isinstance(obj, dict):
            return any(self._contains_sentinel_recursive(value) for value in obj.values())
        if isinstance(obj, (list, tuple)):
            return any(self._contains_sentinel_recursive(value) for value in obj)
        return False


class TransportTestScenarios:
    @staticmethod
    def clean_response(prompt: str) -> FakeClaudeResponse:
        return FakeClaudeTransport().generate_response_with_thinking(
            prompt,
            include_sentinel_in_thinking=True,
        )

    @staticmethod
    def leaked_response(prompt: str) -> FakeClaudeResponse:
        return FakeClaudeTransport().generate_response_with_thinking(
            prompt,
            include_sentinel_in_thinking=True,
            include_sentinel_in_output=True,
        )

    @staticmethod
    def no_thinking_response(prompt: str) -> FakeClaudeResponse:
        del prompt
        payload = StrictCanonicalCodec.serialize(_canonical_decision_payload())
        return FakeClaudeResponse(
            content_blocks=(ContentBlock(ContentBlockType.TOOL_USE, payload, 0),),
            stop_reason="tool_use",
            usage={"input_tokens": 50, "output_tokens": 30},
        )


def _canonical_probe(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        char
        for char in normalized
        if unicodedata.category(char)[0] not in {"C", "M", "Z"}
    )


def _canonical_decision_payload(output_suffix: str = "") -> dict[str, Any]:
    return {
        "decision_version": "erii-compact-decision/v1",
        "result_kind": "candidate",
        "frame": {
            "frame_version": "erii-deliberation-frame/v1",
            "result_kind": "candidate",
            "situation_appraisals": [],
            "psychological_candidates": [],
            "competing_impulses": [],
            "tensions": [],
            "affect_candidates": [],
            "self_interpretation": {
                "awareness": "unformed",
                "bounded_summary": "The character has not settled on one interpretation.",
            },
            "behavioral_intent": {
                "kind": "acknowledge",
                "bounded_summary": "Acknowledge the message without inventing motives.",
            },
            "communication_strategy": {
                "expression_relation": "direct",
                "disclosure": "direct",
                "interpersonal_posture": "open",
                "tone_goal": "character_native",
            },
            "uncertainties": [],
            "residue_proposals": [],
        },
        "interior_scene": {
            "scene_version": "erii-character-interior-scene/v1",
            "voice_mode": "character_native",
            "perspective": "first_person",
            "narrative_budget": "standard",
            "text": f"I want to answer in my own voice.{output_suffix}",
            "semantic_anchor_ids": [],
            "factual_echo_refs": [],
            "projection_eligibility": "not_assessed",
        },
        "reply_candidate": {
            "parts": [
                {
                    "part_id": "reply-1",
                    "kind": "text",
                    "exact_utf8": f"I understand.{output_suffix}",
                }
            ],
            "delivery_mode": "sequential",
        },
        "router_signal": "none",
    }


__all__ = [
    "ContentBlockType",
    "ContentBlock",
    "FakeClaudeResponse",
    "SSEEventType",
    "FakeClaudeSSEEvent",
    "ClaudeSSEAccumulator",
    "FakeClaudeTransport",
    "ClaudeResponseParser",
    "TransportTestScenarios",
]
