from __future__ import annotations

from datetime import datetime

import pytest

from maia.recognition import normalization as normalization_module
from maia.recognition.time_range import (
    TimeRangeExpr,
    TimeRangeKind,
    normalize_time_range,
    normalize_time_range_expr,
    parse_time_range_expr,
    render_bounds,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("NG", "不合格"),
        ("OK", "合格"),
        ("不合格", "不合格"),
        ("合格", "合格"),
        ("未设置界限值", "未设置界限值"),
        ("异常", "异常"),
        ("次异常", "次异常"),
        ("检测失败", "检测失败"),
    ],
)
def test_normalize_entity_target_maps_summary_result_aliases_to_canonical_values(
    raw: str,
    expected: str,
) -> None:
    assert normalization_module.normalize_entity_target("summary_result", raw) == expected


@pytest.mark.parametrize("raw", ["FAIL", "PASS", "不合格品"])
def test_normalize_entity_target_rejects_unsupported_summary_result_aliases(raw: str) -> None:
    with pytest.raises(ValueError, match="unsupported summary_result"):
        normalization_module.normalize_entity_target("summary_result", raw)


@pytest.mark.parametrize("entity_type", ["manual_tagging", "status"])
@pytest.mark.parametrize("raw", ["合格", "不合格", "无效"])
def test_normalize_entity_target_accepts_marking_result_enums(entity_type: str, raw: str) -> None:
    assert normalization_module.normalize_entity_target(entity_type, raw) == raw


@pytest.mark.parametrize("entity_type", ["manual_tagging", "status"])
def test_normalize_entity_target_rejects_unknown_marking_result(entity_type: str) -> None:
    with pytest.raises(ValueError, match=f"unsupported {entity_type}"):
        normalization_module.normalize_entity_target(entity_type, "人工标记")


def test_normalize_entity_target_expands_recent_time_range_to_absolute_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        normalization_module,
        "_now",
        lambda: datetime(2026, 6, 12, 15, 30, 0),
    )

    assert normalization_module.normalize_entity_target("time_range", "最近1周") == (
        "start=2026-06-05 15:30:00; end=2026-06-12 15:30:00"
    )


def test_normalize_entity_target_keeps_date_only_upper_bound_at_midnight() -> None:
    assert normalization_module.normalize_entity_target("time_range", "2026-06-12前") == (
        "end=2026-06-12 00:00:00"
    )


def test_normalize_entity_target_normalizes_latest_n_to_positive_integer_text() -> None:
    assert normalization_module.normalize_entity_target("latest_n", " 05 ") == "5"


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (
            TimeRangeExpr(TimeRangeKind.DAY_BEFORE_YESTERDAY),
            "start=2026-06-14 00:00:00; end=2026-06-14 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.RELATIVE_DAY, offset_days=-3),
            "start=2026-06-13 00:00:00; end=2026-06-13 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.CALENDAR_WEEKDAY, week_offset=-1, weekday=6),
            "start=2026-06-13 00:00:00; end=2026-06-13 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.CALENDAR_WEEKDAY, week_offset=-1, weekday=7),
            "start=2026-06-14 00:00:00; end=2026-06-14 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.CALENDAR_WEEKDAY, week_offset=0, weekday=3),
            "start=2026-06-17 00:00:00; end=2026-06-17 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.CURRENT_CALENDAR_WEEK),
            "start=2026-06-15 00:00:00; end=2026-06-21 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.PREVIOUS_CALENDAR_WEEK),
            "start=2026-06-08 00:00:00; end=2026-06-14 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.CURRENT_CALENDAR_MONTH),
            "start=2026-06-01 00:00:00; end=2026-06-30 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.PREVIOUS_CALENDAR_MONTH),
            "start=2026-05-01 00:00:00; end=2026-05-31 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.CURRENT_CALENDAR_QUARTER),
            "start=2026-04-01 00:00:00; end=2026-06-30 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.PREVIOUS_CALENDAR_QUARTER),
            "start=2026-01-01 00:00:00; end=2026-03-31 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.CURRENT_CALENDAR_YEAR),
            "start=2026-01-01 00:00:00; end=2026-12-31 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.PREVIOUS_CALENDAR_YEAR),
            "start=2025-01-01 00:00:00; end=2025-12-31 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.PERIOD_TO_NOW, period="year"),
            "start=2026-01-01 00:00:00; end=2026-06-16 13:20:30",
        ),
        (
            TimeRangeExpr(TimeRangeKind.PERIOD_TO_NOW, period="month"),
            "start=2026-06-01 00:00:00; end=2026-06-16 13:20:30",
        ),
        (
            TimeRangeExpr(TimeRangeKind.RECENT_ROLLING_DAYS, count=2),
            "start=2026-06-14 13:20:30; end=2026-06-16 13:20:30",
        ),
        (
            TimeRangeExpr(TimeRangeKind.RECENT_ROLLING_DAYS, count=15, source_text="半个月"),
            "start=2026-06-01 13:20:30; end=2026-06-16 13:20:30",
        ),
        (
            TimeRangeExpr(TimeRangeKind.RECENT_ROLLING_DAYS, count=15, source_text="十五天"),
            "start=2026-06-01 13:20:30; end=2026-06-16 13:20:30",
        ),
        (
            TimeRangeExpr(TimeRangeKind.AFTER_DATETIME, date_ref="YESTERDAY", time="16:14:00"),
            "start=2026-06-15 16:14:00",
        ),
        (
            TimeRangeExpr(TimeRangeKind.ABSOLUTE_DATE, date="2026年6月1日"),
            "start=2026-06-01 00:00:00; end=2026-06-01 23:59:59",
        ),
        (
            TimeRangeExpr(TimeRangeKind.ABSOLUTE_DATE_RANGE, start_date="6月1日", end_date="6月15日"),
            "start=2026-06-01 00:00:00; end=2026-06-15 23:59:59",
        ),
    ],
)
def test_time_range_expr_normalizer_outputs_second_precision_bounds(
    expr: TimeRangeExpr,
    expected: str,
) -> None:
    bounds = normalize_time_range_expr(
        expr,
        anchor_time=datetime(2026, 6, 16, 13, 20, 30),
    )

    assert render_bounds(bounds) == expected


@pytest.mark.parametrize(
    "expr",
    [
        TimeRangeExpr(TimeRangeKind.AMBIGUOUS, source_text="前几天"),
        TimeRangeExpr(TimeRangeKind.UNSUPPORTED, source_text="端午之后"),
    ],
)
def test_time_range_expr_normalizer_rejects_ambiguous_and_unsupported(expr: TimeRangeExpr) -> None:
    with pytest.raises(ValueError):
        normalize_time_range_expr(expr, anchor_time=datetime(2026, 6, 16, 13, 20, 30))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("大前天", "start=2026-06-13 00:00:00; end=2026-06-13 23:59:59"),
        ("上周六", "start=2026-06-13 00:00:00; end=2026-06-13 23:59:59"),
        ("上周日", "start=2026-06-14 00:00:00; end=2026-06-14 23:59:59"),
        ("本周三", "start=2026-06-17 00:00:00; end=2026-06-17 23:59:59"),
        ("半个月", "start=2026-06-01 13:20:30; end=2026-06-16 13:20:30"),
        ("十五天", "start=2026-06-01 13:20:30; end=2026-06-16 13:20:30"),
        ("今年以来", "start=2026-01-01 00:00:00; end=2026-06-16 13:20:30"),
        ("月初到现在", "start=2026-06-01 00:00:00; end=2026-06-16 13:20:30"),
    ],
)
def test_normalize_time_range_covers_common_explicit_time_expressions(
    raw: str,
    expected: str,
) -> None:
    assert normalize_time_range(raw, anchor_time=datetime(2026, 6, 16, 13, 20, 30)) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "RELATIVE_DAY", "source_text": "大前天", "offset_days": -3, "confidence": 0.74},
        {"kind": "RELATIVE_DAY", "source_text": "大前天", "offset_days": -3, "count": 0},
        {"kind": "DAY_BEFORE_YESTERDAY", "source_text": "大前天"},
        {"kind": "PREVIOUS_CALENDAR_WEEK", "source_text": "上周六"},
        {"kind": "RELATIVE_DAY", "source_text": "大前天", "offset_days": -3, "unexpected": "x"},
        {"kind": "RELATIVE_DAY", "source_text": "", "offset_days": -3},
    ],
)
def test_parse_time_range_expr_rejects_low_confidence_and_inconsistent_llm_payloads(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        parse_time_range_expr(payload)
