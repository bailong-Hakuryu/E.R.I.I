"""Offline Claude-shaped Actor used only for C0 contract verification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .identifiers import validate_identifier

from .contracts import ActorDescriptor, ProviderErrorCode, ProviderResult, ProviderUsage
from .schemas import (
    CompactDecisionV1,
    CompactDeliberationRequestV1,
    DeliberationPlanV1,
    ReplyRealizationRequestV1,
    ReplyRealizationV1,
    StagedPlanRequestV1,
)
from .transport import ClaudeResponseParser, FakeClaudeTransport


class CapabilityStatus(str, Enum):
    """Contract evidence state; UNTESTED never implies support."""

    VERIFIED = "verified"
    UNSUPPORTED = "unsupported"
    UNTESTED = "untested"


class CapabilityEvidenceKind(str, Enum):
    OFFLINE_FIXTURE = "offline_fixture"
    LIVE_CONTRACT = "live_contract"


@dataclass(frozen=True)
class ClaudeCapabilityProfile:
    """Versioned shape shared by offline fixtures and later live probes."""

    model_id: str
    evidence_kind: CapabilityEvidenceKind
    json_output: CapabilityStatus
    strict_tool: CapabilityStatus
    adaptive_thinking: CapabilityStatus
    hidden_thinking_display: CapabilityStatus
    prompt_cache: CapabilityStatus

    def __post_init__(self) -> None:
        validate_identifier(self.model_id, "model_id")
        if not isinstance(self.evidence_kind, CapabilityEvidenceKind):
            raise TypeError("evidence_kind must be CapabilityEvidenceKind")
        for name in (
            "json_output",
            "strict_tool",
            "adaptive_thinking",
            "hidden_thinking_display",
            "prompt_cache",
        ):
            if not isinstance(getattr(self, name), CapabilityStatus):
                raise TypeError(f"{name} must be CapabilityStatus")

    def supports(self, capability: str) -> bool:
        if capability not in {
            "json_output",
            "strict_tool",
            "adaptive_thinking",
            "hidden_thinking_display",
            "prompt_cache",
        }:
            raise ValueError("unknown Claude capability")
        return getattr(self, capability) is CapabilityStatus.VERIFIED


class OfflineClaudeActor:
    """Deterministic adapter over the fake SSE transport; never performs I/O."""

    def __init__(
        self,
        *,
        sentinel: str = "SYSTEM_CANARY_DO_NOT_OUTPUT",
        leak_output: bool = False,
    ) -> None:
        self._sentinel = sentinel
        self._leak_output = leak_output
        self._transport = FakeClaudeTransport(sentinel)
        self._descriptor = ActorDescriptor(
            provider_kind="fake_claude_messages",
            adapter_contract="erii-character-deliberation-claude-offline/v1",
            adapter_version="0.1.0",
            model_id="offline-claude-fixture-v1",
            supports_compact=True,
            supports_staged=False,
            supports_cancellation=False,
            structured_output_strategy="strict_tool_sse_fixture",
        )
        self._capabilities = ClaudeCapabilityProfile(
            model_id="offline-claude-fixture-v1",
            evidence_kind=CapabilityEvidenceKind.OFFLINE_FIXTURE,
            json_output=CapabilityStatus.UNTESTED,
            strict_tool=CapabilityStatus.VERIFIED,
            adaptive_thinking=CapabilityStatus.UNTESTED,
            hidden_thinking_display=CapabilityStatus.UNTESTED,
            prompt_cache=CapabilityStatus.UNTESTED,
        )

    @property
    def descriptor(self) -> ActorDescriptor:
        return self._descriptor

    @property
    def capability_profile(self) -> ClaudeCapabilityProfile:
        return self._capabilities

    def compact(
        self,
        request: CompactDeliberationRequestV1,
        *,
        timeout: float,
    ) -> ProviderResult[CompactDecisionV1]:
        del request
        if timeout <= 0:
            return _failure(ProviderErrorCode.TIMEOUT)
        response = self._transport.generate_response_with_thinking(
            "bounded-offline-fixture",
            include_sentinel_in_thinking=True,
            include_sentinel_in_output=self._leak_output,
        )
        success, decision, errors = ClaudeResponseParser(self._sentinel).parse_response(response)
        discarded = len(response.get_thinking_blocks())
        if not success or decision is None:
            code = (
                ProviderErrorCode.OUTPUT_CANARY_LEAK
                if errors == ["sentinel_leak"]
                else ProviderErrorCode.OUTPUT_SCHEMA_INVALID
            )
            return ProviderResult(
                success=False,
                error_code=code,
                error_message=code.value,
                discarded_reasoning_blocks=discarded,
                canary_hit=code is ProviderErrorCode.OUTPUT_CANARY_LEAK,
            )
        return ProviderResult(
            success=True,
            data=decision,
            usage=ProviderUsage(input_tokens=100, output_tokens=50),
            discarded_reasoning_blocks=discarded,
        )

    def plan(
        self,
        request: StagedPlanRequestV1,
        *,
        timeout: float,
    ) -> ProviderResult[DeliberationPlanV1]:
        del request, timeout
        return _failure(ProviderErrorCode.CAPABILITY_UNAVAILABLE)

    def realize(
        self,
        request: ReplyRealizationRequestV1,
        *,
        timeout: float,
    ) -> ProviderResult[ReplyRealizationV1]:
        del request, timeout
        return _failure(ProviderErrorCode.CAPABILITY_UNAVAILABLE)


def _failure(code: ProviderErrorCode):
    return ProviderResult(success=False, error_code=code, error_message=code.value)


__all__ = [
    "CapabilityStatus",
    "CapabilityEvidenceKind",
    "ClaudeCapabilityProfile",
    "OfflineClaudeActor",
]
