"""Renderer seam for immutable structured recall results."""

from __future__ import annotations

from typing import Protocol

from erii.models.recall import RecallAudience, RecallResult


class RecallRenderError(ValueError):
    """Base error raised when a complete result cannot be rendered safely."""


class RecallAudienceMismatchError(RecallRenderError):
    """Raised when a renderer is asked to consume a different audience."""


class RecallRenderBudgetError(RecallRenderError):
    """Raised instead of truncating a complete structured recall result."""

    def __init__(self, required_cost: int, max_output_cost: int) -> None:
        self.required_cost = required_cost
        self.max_output_cost = max_output_cost
        super().__init__(
            "rendered recall exceeds output budget "
            f"(required={required_cost}, max={max_output_cost})"
        )


class RecallRenderer(Protocol):
    """One-method adapter Interface for deterministic result formatting."""

    audience: RecallAudience

    def render(self, result: RecallResult) -> str:
        """Renders every selected semantic projection or raises an explicit error."""


def require_matching_audience(result: RecallResult, audience: RecallAudience) -> None:
    """Rejects attempts to use rendering as an audience-filtering operation."""

    if result.audience != audience:
        raise RecallAudienceMismatchError(
            f"renderer audience {audience.value!r} does not match "
            f"recall audience {result.audience.value!r}"
        )
