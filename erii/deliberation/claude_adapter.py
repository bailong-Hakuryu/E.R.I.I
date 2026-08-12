"""Reserved Claude Messages seam; no live transport is implemented yet.

This module deliberately performs no SDK construction, credential lookup, or
network I/O.  The offline Claude-shaped fixture remains in ``claude_offline``.
A future live adapter must add strict byte fixtures and explicit opt-in tests
before advertising any provider capability here.
"""

from __future__ import annotations

from importlib.util import find_spec

from erii.deliberation.contracts import (
    ActorDescriptor,
    ProviderErrorCode,
    ProviderResult,
)
from erii.deliberation.schemas import (
    CompactDecisionV1,
    CompactDeliberationRequestV1,
    DeliberationPlanV1,
    ReplyRealizationRequestV1,
    ReplyRealizationV1,
    StagedPlanRequestV1,
)


def check_anthropic_available() -> bool:
    """Return whether the optional SDK can be resolved, without importing it."""
    return find_spec("anthropic") is not None


class ClaudeDeliberationAdapter:
    """Non-operational placeholder that cannot be routed as a live Actor."""

    def __init__(
        self,
        *,
        model_id: str,
        max_tokens: int = 4000,
        temperature: float = 0.0,
    ) -> None:
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        if type(temperature) is not float or not 0.0 <= temperature <= 2.0:
            raise ValueError("temperature must be a float in [0.0, 2.0]")
        self._descriptor = ActorDescriptor(
            provider_kind="anthropic_messages_unavailable",
            adapter_contract="erii-character-deliberation-claude-placeholder/v1",
            adapter_version="0.0.0",
            model_id=model_id,
            supports_compact=False,
            supports_staged=False,
            supports_cancellation=False,
            structured_output_strategy="unavailable",
        )

    @property
    def descriptor(self) -> ActorDescriptor:
        """Return an honest, credential-free capability descriptor."""
        return self._descriptor

    def compact(
        self,
        request: CompactDeliberationRequestV1,
        *,
        timeout: float,
    ) -> ProviderResult[CompactDecisionV1]:
        """Report that the live Compact transport is unavailable."""
        return _unavailable()

    def plan(
        self,
        request: StagedPlanRequestV1,
        *,
        timeout: float,
    ) -> ProviderResult[DeliberationPlanV1]:
        """Report that the live staged-plan transport is unavailable."""
        return _unavailable()

    def realize(
        self,
        request: ReplyRealizationRequestV1,
        *,
        timeout: float,
    ) -> ProviderResult[ReplyRealizationV1]:
        """Report that the live realization transport is unavailable."""
        return _unavailable()


def _unavailable():
    return ProviderResult(
        success=False,
        error_code=ProviderErrorCode.CAPABILITY_UNAVAILABLE,
        error_message=ProviderErrorCode.CAPABILITY_UNAVAILABLE.value,
    )


__all__ = ["ClaudeDeliberationAdapter", "check_anthropic_available"]
