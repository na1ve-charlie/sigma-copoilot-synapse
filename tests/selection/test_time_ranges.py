"""Tests for TimeRange encoding / decoding (Task 05).

Covers:
- Three relative tokens (today, last_7_days, current_month)
- Absolute ISO interval (two-sided, start-only, end-only)
- Timezone rejection (naive datetime)
- Reversed interval rejection
- Unknown relative token rejection
- encode / decode round-trip
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from synapse.selection.time_ranges import (
    TimeRangeCriteria,
    encode_time_range,
    parse_time_range,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_TZ8 = timezone(timedelta(hours=8))

_NOW = datetime(2026, 6, 10, 15, 30, 0, tzinfo=_UTC)


def _naive(dt_list: list[int]) -> datetime:
    """Shorthand for naive datetime construction."""
    return datetime(*dt_list)


def _aware(dt_list: list[int], tz: timezone = _UTC) -> datetime:
    return datetime(*dt_list, tzinfo=tz)


# ======================================================================
# Relative tokens
# ======================================================================


class TestRelativeTokens:
    def test_today(self) -> None:
        cr = parse_time_range("relative:today", now=_NOW)
        assert cr.start == _aware([2026, 6, 10, 0, 0, 0])
        assert cr.end == _aware([2026, 6, 10, 23, 59, 59, 999999])

    def test_today_tz8(self) -> None:
        now = datetime(2026, 6, 10, 23, 0, 0, tzinfo=_TZ8)
        cr = parse_time_range("relative:today", now=now)
        assert cr.start == _aware([2026, 6, 10, 0, 0, 0], tz=_TZ8)
        assert cr.end == _aware([2026, 6, 10, 23, 59, 59, 999999], tz=_TZ8)

    def test_last_7_days(self) -> None:
        cr = parse_time_range("relative:last_7_days", now=_NOW)
        # today 2026-06-10, 7 days means Jun 04 – Jun 10
        assert cr.start == _aware([2026, 6, 4, 0, 0, 0])
        assert cr.end == _aware([2026, 6, 10, 23, 59, 59, 999999])

    def test_last_7_days_edge(self) -> None:
        # 7 days ago from Jun 4 is May 29
        now = _aware([2026, 6, 4, 10, 0, 0])
        cr = parse_time_range("relative:last_7_days", now=now)
        assert cr.start == _aware([2026, 5, 29, 0, 0, 0])
        assert cr.end == _aware([2026, 6, 4, 23, 59, 59, 999999])

    def test_current_month(self) -> None:
        cr = parse_time_range("relative:current_month", now=_NOW)
        assert cr.start == _aware([2026, 6, 1, 0, 0, 0])
        # end is *now*, not end of month
        assert cr.end == _NOW

    def test_current_month_first_day(self) -> None:
        now = _aware([2026, 6, 1, 0, 0, 1])
        cr = parse_time_range("relative:current_month", now=now)
        assert cr.start == _aware([2026, 6, 1, 0, 0, 0])
        assert cr.end == now


# ======================================================================
# Absolute ISO interval — two-sided
# ======================================================================


class TestAbsoluteIsoInterval:
    def test_full_interval_utc(self) -> None:
        cr = parse_time_range(
            "2026-06-01T00:00:00+00:00/2026-06-10T23:59:59+00:00",
            now=_NOW,
        )
        assert cr.start == _aware([2026, 6, 1, 0, 0, 0])
        assert cr.end == _aware([2026, 6, 10, 23, 59, 59])

    def test_full_interval_tz8(self) -> None:
        cr = parse_time_range(
            "2026-06-01T00:00:00+08:00/2026-06-10T23:59:59+08:00",
            now=_NOW,
        )
        assert cr.start == _aware([2026, 6, 1, 0, 0, 0], tz=_TZ8)
        assert cr.end == _aware([2026, 6, 10, 23, 59, 59], tz=_TZ8)

    def test_full_interval_spaces(self) -> None:
        cr = parse_time_range(
            " 2026-06-01T00:00:00+00:00 / 2026-06-10T23:59:59+00:00 ",
            now=_NOW,
        )
        assert cr.start == _aware([2026, 6, 1, 0, 0, 0])

    def test_start_only(self) -> None:
        cr = parse_time_range(
            "2026-06-01T00:00:00+00:00/",
            now=_NOW,
        )
        assert cr.start == _aware([2026, 6, 1, 0, 0, 0])
        assert cr.end is None

    def test_end_only(self) -> None:
        cr = parse_time_range(
            "/2026-06-10T23:59:59+00:00",
            now=_NOW,
        )
        assert cr.start is None
        assert cr.end == _aware([2026, 6, 10, 23, 59, 59])


# ======================================================================
# Timezone rejection
# ======================================================================


class TestTimezoneRejection:
    def test_naive_start_in_interval(self) -> None:
        with pytest.raises(ValueError, match="must be timezone-aware"):
            parse_time_range(
                "2026-06-01T00:00:00/2026-06-10T23:59:59+00:00",
                now=_NOW,
            )

    def test_naive_end_in_interval(self) -> None:
        with pytest.raises(ValueError, match="must be timezone-aware"):
            parse_time_range(
                "2026-06-01T00:00:00+00:00/2026-06-10T23:59:59",
                now=_NOW,
            )

    def test_naive_now_rejected(self) -> None:
        naive_now = datetime(2026, 6, 10, 15, 30, 0)
        with pytest.raises(ValueError, match="must be timezone-aware"):
            parse_time_range("relative:today", now=naive_now)

    def test_naive_start_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be timezone-aware"):
            parse_time_range("2026-06-01T00:00:00/", now=_NOW)


# ======================================================================
# Reversed interval rejection
# ======================================================================


class TestReversedIntervalRejection:
    def test_start_after_end(self) -> None:
        with pytest.raises(ValueError, match="must not be later"):
            parse_time_range(
                "2026-06-10T00:00:00+00:00/2026-06-01T00:00:00+00:00",
                now=_NOW,
            )


# ======================================================================
# Invalid token / format rejection
# ======================================================================


class TestInvalidTokenRejection:
    def test_unknown_relative_token(self) -> None:
        with pytest.raises(ValueError, match="Unsupported relative token"):
            parse_time_range("relative:yesterday", now=_NOW)

    def test_garbage_string(self) -> None:
        with pytest.raises(ValueError, match="Expected ISO interval"):
            parse_time_range("not-a-time-range", now=_NOW)

    def test_no_slash(self) -> None:
        with pytest.raises(ValueError, match="Expected ISO interval"):
            parse_time_range("2026-06-01T00:00:00+00:00", now=_NOW)


# ======================================================================
# encode_time_range
# ======================================================================


class TestEncodeTimeRange:
    def test_encode_full(self) -> None:
        cr = TimeRangeCriteria(
            start=_aware([2026, 6, 1, 0, 0, 0]),
            end=_aware([2026, 6, 10, 23, 59, 59]),
        )
        out = encode_time_range(cr)
        assert out == "2026-06-01T00:00:00+00:00/2026-06-10T23:59:59+00:00"

    def test_encode_start_only(self) -> None:
        cr = TimeRangeCriteria(start=_aware([2026, 6, 1]), end=None)
        assert encode_time_range(cr) == "2026-06-01T00:00:00+00:00/"

    def test_encode_end_only(self) -> None:
        cr = TimeRangeCriteria(start=None, end=_aware([2026, 6, 10]))
        assert encode_time_range(cr) == "/2026-06-10T00:00:00+00:00"


# ======================================================================
# Round-trip
# ======================================================================


class TestRoundTrip:
    def test_relative_today(self) -> None:
        original = parse_time_range("relative:today", now=_NOW)
        encoded = encode_time_range(original)
        restored = parse_time_range(encoded, now=_NOW)
        assert restored == original

    def test_absolute_full(self) -> None:
        s = "2026-06-01T00:00:00+00:00/2026-06-10T23:59:59+00:00"
        original = parse_time_range(s, now=_NOW)
        assert encode_time_range(original) == s

    def test_absolute_start_only(self) -> None:
        s = "2026-06-01T00:00:00+00:00/"
        original = parse_time_range(s, now=_NOW)
        assert encode_time_range(original) == s

    def test_absolute_end_only(self) -> None:
        s = "/2026-06-10T23:59:59+00:00"
        original = parse_time_range(s, now=_NOW)
        assert encode_time_range(original) == s


# ======================================================================
# TimeRangeCriteria direct construction
# ======================================================================


class TestTimeRangeCriteriaDirect:
    def test_both_none_rejected(self) -> None:
        with pytest.raises(ValueError, match="requires at least one"):
            TimeRangeCriteria(start=None, end=None)

    def test_naive_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be timezone-aware"):
            TimeRangeCriteria(start=datetime(2026, 6, 1), end=None)

    def test_naive_end_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be timezone-aware"):
            TimeRangeCriteria(start=None, end=datetime(2026, 6, 1))

    def test_reversed_both_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be later"):
            TimeRangeCriteria(
                start=_aware([2026, 6, 10]),
                end=_aware([2026, 6, 1]),
            )

    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        cr = TimeRangeCriteria(start=_aware([2026, 6, 1]), end=None)
        with pytest.raises(FrozenInstanceError):
            cr.start = _aware([2026, 7, 1])  # type: ignore[misc]

    def test_equality(self) -> None:
        a = TimeRangeCriteria(start=_aware([2026, 6, 1]), end=None)
        b = TimeRangeCriteria(start=_aware([2026, 6, 1]), end=None)
        assert a == b
        assert a != TimeRangeCriteria(start=_aware([2026, 7, 1]), end=None)
