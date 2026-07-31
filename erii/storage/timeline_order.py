"""Canonical chronological ordering for Timeline artifacts."""

from datetime import datetime, timezone
import re
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from erii.models.archival import TimelineEntry


_ISO_FRACTION = re.compile(
    r"^(?P<seconds>.+[T ]\d{2}:\d{2}:\d{2})"
    r"\.(?P<fraction>\d+)"
    r"(?P<offset>[+-]\d{2}(?::?\d{2})?)?$"
)


def timeline_timestamp_sort_key(timestamp: Optional[str]) -> str:
    """Returns a UTC-normalized key whose lexical order is chronological.

    Legacy timestamps without an offset are interpreted as UTC, matching the
    historical storage convention. Missing or unparseable legacy values sort
    before known instants without being rewritten into invented timestamps.
    """
    if timestamp is None:
        return ""
    value = str(timestamp).strip()
    if not value:
        return ""
    if value.endswith(("Z", "z")):
        value = f"{value[:-1]}+00:00"
    match = _ISO_FRACTION.fullmatch(value)
    if match is not None:
        fraction = match.group("fraction")[:6].ljust(6, "0")
        value = (
            f"{match.group('seconds')}.{fraction}"
            f"{match.group('offset') or ''}"
        )
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        instant = parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return ""
    return instant.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def timeline_entry_order_key(entry: "TimelineEntry") -> Tuple[str, str]:
    """Orders Timeline entries by instant and stable artifact identity."""
    timestamp = entry.recorded_at or entry.legacy_timestamp
    return timeline_timestamp_sort_key(timestamp), entry.timeline_entry_id


__all__ = ["timeline_entry_order_key", "timeline_timestamp_sort_key"]
