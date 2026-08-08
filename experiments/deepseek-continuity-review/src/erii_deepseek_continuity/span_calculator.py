"""Span calculator: quote → span deterministic computation.

Key features:
- Model returns reply_quote + occurrence
- Adapter deterministically calculates reply_start/reply_end
- Fails closed on: quote not found, duplicate without occurrence
- Never silently corrects model's quote
- Supports Unicode/emoji
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SpanResult:
    """Span calculation result."""
    reply_start: int
    reply_end: int
    reply_quote: str


def calculate_span(
    proposed_reply: str,
    reply_quote: str,
    occurrence: int = 0,
) -> SpanResult:
    """
    Deterministically calculate reply span.

    Args:
        proposed_reply: Full reply text
        reply_quote: Exact quote from reply (from model)
        occurrence: Which occurrence if quote appears multiple times (0-indexed)

    Returns:
        SpanResult with start/end positions

    Raises:
        SpanCalculationError: If quote not found, duplicate without occurrence specified,
                              or occurrence out of range
    """

    if not reply_quote:
        raise SpanCalculationError("reply_quote cannot be empty")

    if not proposed_reply:
        raise SpanCalculationError("proposed_reply cannot be empty")

    # Find all occurrences
    occurrences = []
    start = 0
    while True:
        pos = proposed_reply.find(reply_quote, start)
        if pos == -1:
            break
        occurrences.append(pos)
        start = pos + 1

    # Validate occurrences
    if not occurrences:
        raise SpanCalculationError("reply_quote not found in proposed_reply")

    if len(occurrences) > 1 and occurrence is None:
        raise SpanCalculationError(
            f"reply_quote appears {len(occurrences)} times, "
            f"must specify occurrence"
        )

    if occurrence is None:
        occurrence = 0

    if occurrence < 0 or occurrence >= len(occurrences):
        raise SpanCalculationError(
            f"occurrence {occurrence} out of range "
            f"(quote appears {len(occurrences)} times)"
        )

    # Calculate span
    reply_start = occurrences[occurrence]
    reply_end = reply_start + len(reply_quote)

    return SpanResult(
        reply_start=reply_start,
        reply_end=reply_end,
        reply_quote=reply_quote,
    )


class SpanCalculationError(Exception):
    """Span calculation failed (contains no sensitive info)."""
    pass
