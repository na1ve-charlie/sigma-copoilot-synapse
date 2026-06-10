"""Parse Themis string targets into absolute time ranges (Task 05).

Supports:
- Absolute ISO 8601 intervals: ``start/end``
- Relative tokens: ``relative:today``, ``relative:last_7_days``,
  ``relative:current_month``
- Single-sided: ``start/`` (after start), ``/end`` (before end)

No LLM, no third-party date libraries, no implicit timezone guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimeRangeCriteria:
    """Absolute time range produced by parsing.

    ``start`` may be ``None`` when only an upper bound is given (``/end``).
    ``end`` may be ``None`` when only a lower bound is given (``start/``).

    When both are present they MUST satisfy ``start <= end`` and both MUST
    carry timezone information.
    """

    start: datetime | None
    end: datetime | None

    def __post_init__(self) -> None:
        if self.start is None and self.end is None:
            raise ValueError(
                "TimeRangeCriteria requires at least one of start or end"
            )
        if self.start is not None and self.start.tzinfo is None:
            raise ValueError("TimeRangeCriteria.start must be timezone-aware")
        if self.end is not None and self.end.tzinfo is None:
            raise ValueError("TimeRangeCriteria.end must be timezone-aware")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError(
                f"TimeRangeCriteria.start ({self.start.isoformat()}) "
                f"must not be later than end ({self.end.isoformat()})"
            )


# ---------------------------------------------------------------------------
# Relative token resolution
# ---------------------------------------------------------------------------

# All resolution must receive an explicit *now* (never wall-clock).


def _resolve_relative(token: str, *, now: datetime) -> TimeRangeCriteria:
    """Resolve a ``relative:<token>`` string.

    Supported tokens: ``today``, ``last_7_days``, ``current_month``.
    """
    if now.tzinfo is None:
        raise ValueError("*now* must be timezone-aware")
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if token == "today":
        return TimeRangeCriteria(
            start=day,
            end=day + timedelta(days=1) - timedelta(microseconds=1),
        )

    if token == "last_7_days":
        return TimeRangeCriteria(
            start=day - timedelta(days=6),  # today counts as day 1
            end=day + timedelta(days=1) - timedelta(microseconds=1),
        )

    if token == "current_month":
        first_of_month = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return TimeRangeCriteria(
            start=first_of_month,
            end=now,
        )

    raise ValueError(f"Unsupported relative token: relative:{token!r}")


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def parse_time_range(value: str, *, now: datetime) -> TimeRangeCriteria:
    """Convert a Themis string *value* into a ``TimeRangeCriteria``.

    Required format:

    * ``relative:<token>`` — relative time expression
    * ``<start_iso>/<end_iso>`` — absolute closed interval
    * ``<start_iso>/`` — lower bound only
    * ``/<end_iso>`` — upper bound only

    Parameters
    ----------
    value:
        The raw string as emitted by Themis (e.g. ``"relative:last_7_days"``
        or ``"2026-06-01T00:00:00+08:00/2026-06-10T23:59:59+08:00"``).
    now:
        The reference instant for resolving relative tokens.  MUST be
        timezone-aware.
    """
    if now.tzinfo is None:
        raise ValueError("*now* must be timezone-aware")

    # Relative token branch
    if value.startswith("relative:"):
        token = value[len("relative:"):]
        return _resolve_relative(token, now=now)

    # Absolute interval(s)
    parts = value.split("/", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Expected ISO interval 'start/end', 'start/', or '/end', "
            f"got {value!r}"
        )

    start_raw, end_raw = parts[0].strip(), parts[1].strip()

    start: datetime | None = None
    end: datetime | None = None

    if start_raw:
        start = datetime.fromisoformat(start_raw)
        if start.tzinfo is None:
            raise ValueError("Absolute time-range start must be timezone-aware")

    if end_raw:
        end = datetime.fromisoformat(end_raw)
        if end.tzinfo is None:
            raise ValueError("Absolute time-range end must be timezone-aware")

    return TimeRangeCriteria(start=start, end=end)


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------


def encode_time_range(criteria: TimeRangeCriteria) -> str:
    """Produce the canonical string representation of *criteria*.

    Full interval → ``start/end``, lower-only → ``start/``,
    upper-only → ``/end``.
    """
    start_str = criteria.start.isoformat() if criteria.start is not None else ""
    end_str = criteria.end.isoformat() if criteria.end is not None else ""
    return f"{start_str}/{end_str}"
