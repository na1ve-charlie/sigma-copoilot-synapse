from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
import json
import re
from typing import Any, Protocol


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_FORMAT = "%Y-%m-%d"


class TimeRangeKind(str, Enum):
    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    DAY_BEFORE_YESTERDAY = "DAY_BEFORE_YESTERDAY"
    RELATIVE_DAY = "RELATIVE_DAY"
    CURRENT_CALENDAR_WEEK = "CURRENT_CALENDAR_WEEK"
    PREVIOUS_CALENDAR_WEEK = "PREVIOUS_CALENDAR_WEEK"
    CALENDAR_WEEKDAY = "CALENDAR_WEEKDAY"
    CURRENT_CALENDAR_MONTH = "CURRENT_CALENDAR_MONTH"
    PREVIOUS_CALENDAR_MONTH = "PREVIOUS_CALENDAR_MONTH"
    CURRENT_CALENDAR_QUARTER = "CURRENT_CALENDAR_QUARTER"
    PREVIOUS_CALENDAR_QUARTER = "PREVIOUS_CALENDAR_QUARTER"
    CURRENT_CALENDAR_YEAR = "CURRENT_CALENDAR_YEAR"
    PREVIOUS_CALENDAR_YEAR = "PREVIOUS_CALENDAR_YEAR"
    PERIOD_TO_NOW = "PERIOD_TO_NOW"
    RECENT_ROLLING_DAYS = "RECENT_ROLLING_DAYS"
    RECENT_ROLLING_WEEKS = "RECENT_ROLLING_WEEKS"
    RECENT_ROLLING_MONTHS = "RECENT_ROLLING_MONTHS"
    RECENT_ROLLING_HOURS = "RECENT_ROLLING_HOURS"
    ABSOLUTE_DATE = "ABSOLUTE_DATE"
    ABSOLUTE_DATE_RANGE = "ABSOLUTE_DATE_RANGE"
    AFTER_DATETIME = "AFTER_DATETIME"
    BEFORE_DATETIME = "BEFORE_DATETIME"
    DATETIME_RANGE = "DATETIME_RANGE"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


@dataclass(frozen=True)
class TimeRangeExpr:
    kind: TimeRangeKind
    source_text: str = ""
    count: int | None = None
    unit: str | None = None
    date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    date_ref: str | None = None
    time: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    period: str | None = None
    confidence: float | None = None
    offset_days: int | None = None
    week_offset: int | None = None
    weekday: int | None = None


@dataclass(frozen=True)
class TimeRangeBounds:
    start: datetime | None
    end: datetime | None


class TimeRangeExtractor(Protocol):
    async def extract_time_range(self, *, message: str, target: str) -> TimeRangeExpr | Mapping[str, Any] | str:
        ...


class LLMTimeRangeExtractor:
    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def extract_time_range(self, *, message: str, target: str) -> TimeRangeExpr:
        raw = await self._llm.chat(
            [
                {"role": "system", "content": _EXTRACTOR_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"message": message, "target": target},
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        return parse_time_range_expr(raw)


_EXTRACTOR_PROMPT = """\
You convert Chinese natural-language time range text into JSON.
Output only JSON with allowed fields:
kind, source_text, count, unit, date, start_date, end_date, date_ref,
time, start_time, end_time, period, confidence, offset_days, week_offset, weekday.
Never output final start/end datetimes.
Omit irrelevant fields. Do not output empty string fields or count=0.
Allowed kind values:
TODAY, YESTERDAY, DAY_BEFORE_YESTERDAY, RELATIVE_DAY,
CURRENT_CALENDAR_WEEK, PREVIOUS_CALENDAR_WEEK, CALENDAR_WEEKDAY,
CURRENT_CALENDAR_MONTH, PREVIOUS_CALENDAR_MONTH,
CURRENT_CALENDAR_QUARTER, PREVIOUS_CALENDAR_QUARTER, CURRENT_CALENDAR_YEAR,
PREVIOUS_CALENDAR_YEAR, PERIOD_TO_NOW, RECENT_ROLLING_DAYS,
RECENT_ROLLING_WEEKS, RECENT_ROLLING_MONTHS, RECENT_ROLLING_HOURS,
ABSOLUTE_DATE, ABSOLUTE_DATE_RANGE, AFTER_DATETIME, BEFORE_DATETIME,
DATETIME_RANGE, AMBIGUOUS, UNSUPPORTED, LOW_CONFIDENCE.
Use RELATIVE_DAY with offset_days=-3 for 大前天. Do not map 大前天 to DAY_BEFORE_YESTERDAY.
Use CALENDAR_WEEKDAY with week_offset and weekday for 上周六, 上周日, 本周三.
weekday uses Monday=1 and Sunday=7. week_offset uses this week=0, previous week=-1.
Use AMBIGUOUS for vague phrases like 前几天, 最近一段时间, 两三个星期.
Use UNSUPPORTED for holidays, lunar dates, workdays, or complex time periods.
If confidence is below 0.75, use LOW_CONFIDENCE.
"""

_RANGE_SPLIT_RE = re.compile(r"\s*(?:到|至)\s*")
_CANONICAL_RE = re.compile(
    r"^(?:start=(?P<start>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))?"
    r"(?:; )?"
    r"(?:end=(?P<end>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))?$"
)
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?$")
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_DATE_RE = re.compile(r"^(?P<year>\d{4})/(?P<month>\d{1,2})/(?P<day>\d{1,2})$")
_CHINESE_DATE_RE = re.compile(r"^(?:(?P<year>\d{4})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})日?$")
_TIME_RE = re.compile(r"^(?P<prefix>上午|下午|晚上|中午|凌晨|早上)?(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?$")
_RECENT_RE = re.compile(
    r"^(?:最近|近|过去)?(?P<count>\d+|一|二|两|三|四|五|六|七|八|九|十|十一|十二|十三|十四|十五)(?P<unit>周|天|月|个月|小时)$"
)
_WEEKDAY_RE = re.compile(r"^(?P<week>本周|这周|上周)(?P<weekday>一|二|三|四|五|六|日|天)$")
_ALLOWED_EXPR_KEYS = set(TimeRangeExpr.__dataclass_fields__)
_MIN_CONFIDENCE = 0.75


async def normalize_time_range_with_extractor(
    target: Any,
    *,
    message: str,
    extractor: TimeRangeExtractor | None,
    anchor_time: datetime | None = None,
) -> str:
    try:
        return normalize_time_range(target, anchor_time=anchor_time)
    except ValueError:
        if extractor is None or not isinstance(target, str) or not target.strip():
            raise
    extracted = await extractor.extract_time_range(message=message, target=target.strip())
    expr = parse_time_range_expr(extracted)
    return render_bounds(normalize_time_range_expr(expr, anchor_time=anchor_time))


def normalize_time_range(target: Any, *, anchor_time: datetime | None = None) -> str:
    if not isinstance(target, str):
        raise ValueError("time_range target must be text")
    text = target.strip()
    if not text:
        raise ValueError("time_range target must not be blank")

    canonical = _parse_canonical(text)
    if canonical is not None:
        return render_bounds(canonical)

    expr = _parse_rule_expr(text)
    if expr is not None:
        return render_bounds(normalize_time_range_expr(expr, anchor_time=anchor_time))

    if text.endswith("前"):
        return render_bounds(TimeRangeBounds(None, _parse_moment(text[:-1].strip(), is_end=False)))
    if text.endswith("后"):
        return render_bounds(TimeRangeBounds(_parse_moment(text[:-1].strip(), is_end=False), None))

    parts = _RANGE_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        return render_bounds(
            TimeRangeBounds(
                _parse_moment(parts[0], is_end=False),
                _parse_moment(parts[1], is_end=True),
            )
        )

    raise ValueError(f"unsupported time_range target: {target}")


def normalize_time_range_expr(
    expr: TimeRangeExpr,
    *,
    anchor_time: datetime | None = None,
) -> TimeRangeBounds:
    anchor = _anchor(anchor_time)
    kind = expr.kind
    if kind is TimeRangeKind.AMBIGUOUS:
        raise ValueError("ambiguous time_range expression")
    if kind is TimeRangeKind.UNSUPPORTED:
        raise ValueError("unsupported time_range expression")
    if kind is TimeRangeKind.LOW_CONFIDENCE:
        raise ValueError("low confidence time_range expression")
    _validate_expr(expr)
    if kind is TimeRangeKind.TODAY:
        return TimeRangeBounds(_start_of_day(anchor.date()), anchor)
    if kind is TimeRangeKind.YESTERDAY:
        return _day_bounds(anchor.date() - timedelta(days=1))
    if kind is TimeRangeKind.DAY_BEFORE_YESTERDAY:
        return _day_bounds(anchor.date() - timedelta(days=2))
    if kind is TimeRangeKind.RELATIVE_DAY:
        return _day_bounds(anchor.date() + timedelta(days=_required_offset_days(expr)))
    if kind is TimeRangeKind.CALENDAR_WEEKDAY:
        return _day_bounds(_calendar_weekday(anchor, expr))
    if kind in _PERIOD_KINDS:
        return _period_bounds(kind, anchor)
    if kind is TimeRangeKind.PERIOD_TO_NOW:
        return TimeRangeBounds(_period_start(_required_text(expr.period, "period"), anchor), anchor)
    if kind in _ROLLING_KINDS:
        return TimeRangeBounds(anchor - _rolling_delta(kind, _required_count(expr)), anchor)
    if kind is TimeRangeKind.ABSOLUTE_DATE:
        return _day_bounds(_parse_date(_required_text(expr.date, "date"), anchor))
    if kind is TimeRangeKind.ABSOLUTE_DATE_RANGE:
        return TimeRangeBounds(
            _start_of_day(_parse_date(_required_text(expr.start_date, "start_date"), anchor)),
            _end_of_day(_parse_date(_required_text(expr.end_date, "end_date"), anchor)),
        )
    if kind is TimeRangeKind.AFTER_DATETIME:
        return TimeRangeBounds(_expr_datetime(expr, anchor, default=time.min), None)
    if kind is TimeRangeKind.BEFORE_DATETIME:
        return TimeRangeBounds(None, _expr_datetime(expr, anchor, default=time.min))
    if kind is TimeRangeKind.DATETIME_RANGE:
        return TimeRangeBounds(
            _combine(_parse_date(_required_text(expr.start_date, "start_date"), anchor), _parse_time(expr.start_time, time.min)),
            _combine(_parse_date(_required_text(expr.end_date, "end_date"), anchor), _parse_time(expr.end_time, time.max.replace(microsecond=0))),
        )
    raise ValueError(f"unsupported time_range kind: {kind}")


def parse_time_range_expr(payload: TimeRangeExpr | Mapping[str, Any] | str) -> TimeRangeExpr:
    if isinstance(payload, TimeRangeExpr):
        return payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("time_range extractor returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("time_range expression must be a mapping")
    extra = set(payload) - _ALLOWED_EXPR_KEYS
    if extra:
        raise ValueError(f"unsupported time_range expression fields: {sorted(extra)}")
    _reject_empty_string_fields(payload)
    kind = TimeRangeKind(_required_text(payload.get("kind"), "kind"))
    expr = TimeRangeExpr(
        kind=kind,
        source_text=_optional_text(payload.get("source_text")) or "",
        count=_optional_count(payload.get("count")),
        unit=_optional_text(payload.get("unit")),
        date=_optional_text(payload.get("date")),
        start_date=_optional_text(payload.get("start_date")),
        end_date=_optional_text(payload.get("end_date")),
        date_ref=_optional_text(payload.get("date_ref")),
        time=_optional_text(payload.get("time")),
        start_time=_optional_text(payload.get("start_time")),
        end_time=_optional_text(payload.get("end_time")),
        period=_optional_text(payload.get("period")),
        confidence=_optional_confidence(payload.get("confidence")),
        offset_days=_optional_int(payload.get("offset_days"), "offset_days"),
        week_offset=_optional_int(payload.get("week_offset"), "week_offset"),
        weekday=_optional_int(payload.get("weekday"), "weekday"),
    )
    _validate_expr(expr)
    return expr


def time_range_params(target: str) -> dict[str, str]:
    match = _CANONICAL_RE.fullmatch(target.strip())
    if match is None:
        raise ValueError(f"invalid canonical time_range target: {target}")
    result = {key: value for key, value in match.groupdict().items() if value is not None}
    if not result:
        raise ValueError(f"invalid canonical time_range target: {target}")
    return result


def render_bounds(bounds: TimeRangeBounds) -> str:
    parts: list[str] = []
    if bounds.start is not None:
        parts.append(f"start={bounds.start.strftime(TIME_FORMAT)}")
    if bounds.end is not None:
        parts.append(f"end={bounds.end.strftime(TIME_FORMAT)}")
    if not parts:
        raise ValueError("time_range must include start or end")
    return "; ".join(parts)


_PERIOD_KINDS = {
    TimeRangeKind.CURRENT_CALENDAR_WEEK,
    TimeRangeKind.PREVIOUS_CALENDAR_WEEK,
    TimeRangeKind.CURRENT_CALENDAR_MONTH,
    TimeRangeKind.PREVIOUS_CALENDAR_MONTH,
    TimeRangeKind.CURRENT_CALENDAR_QUARTER,
    TimeRangeKind.PREVIOUS_CALENDAR_QUARTER,
    TimeRangeKind.CURRENT_CALENDAR_YEAR,
    TimeRangeKind.PREVIOUS_CALENDAR_YEAR,
}
_ROLLING_KINDS = {
    TimeRangeKind.RECENT_ROLLING_DAYS,
    TimeRangeKind.RECENT_ROLLING_WEEKS,
    TimeRangeKind.RECENT_ROLLING_MONTHS,
    TimeRangeKind.RECENT_ROLLING_HOURS,
}
_DATE_REF_ALIASES = {
    "今天": TimeRangeKind.TODAY.value,
    "昨天": TimeRangeKind.YESTERDAY.value,
    "前天": TimeRangeKind.DAY_BEFORE_YESTERDAY.value,
}
_CALENDAR_PERIOD_TEXTS = {
    "本周": TimeRangeKind.CURRENT_CALENDAR_WEEK,
    "这周": TimeRangeKind.CURRENT_CALENDAR_WEEK,
    "上周": TimeRangeKind.PREVIOUS_CALENDAR_WEEK,
    "本月": TimeRangeKind.CURRENT_CALENDAR_MONTH,
    "这个月": TimeRangeKind.CURRENT_CALENDAR_MONTH,
    "上月": TimeRangeKind.PREVIOUS_CALENDAR_MONTH,
    "上个月": TimeRangeKind.PREVIOUS_CALENDAR_MONTH,
    "本季度": TimeRangeKind.CURRENT_CALENDAR_QUARTER,
    "这个季度": TimeRangeKind.CURRENT_CALENDAR_QUARTER,
    "上季度": TimeRangeKind.PREVIOUS_CALENDAR_QUARTER,
    "上个季度": TimeRangeKind.PREVIOUS_CALENDAR_QUARTER,
    "今年": TimeRangeKind.CURRENT_CALENDAR_YEAR,
    "去年": TimeRangeKind.PREVIOUS_CALENDAR_YEAR,
}
_PERIOD_ALIASES = {
    "week": "week",
    "month": "month",
    "quarter": "quarter",
    "year": "year",
    "本周": "week",
    "本月": "month",
    "本季度": "quarter",
    "今年": "year",
    TimeRangeKind.CURRENT_CALENDAR_WEEK.value: "week",
    TimeRangeKind.CURRENT_CALENDAR_MONTH.value: "month",
    TimeRangeKind.CURRENT_CALENDAR_QUARTER.value: "quarter",
    TimeRangeKind.CURRENT_CALENDAR_YEAR.value: "year",
}
_WEEKDAY_VALUES = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 7,
    "天": 7,
}
_CHINESE_COUNTS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
}


def _parse_rule_expr(text: str) -> TimeRangeExpr | None:
    if text == "今天":
        return TimeRangeExpr(TimeRangeKind.TODAY, source_text=text)
    if text == "昨天":
        return TimeRangeExpr(TimeRangeKind.YESTERDAY, source_text=text)
    if text == "前天":
        return TimeRangeExpr(TimeRangeKind.DAY_BEFORE_YESTERDAY, source_text=text)
    if text == "大前天":
        return TimeRangeExpr(TimeRangeKind.RELATIVE_DAY, source_text=text, offset_days=-3)
    if text in _CALENDAR_PERIOD_TEXTS:
        return TimeRangeExpr(_CALENDAR_PERIOD_TEXTS[text], source_text=text)
    if text in {"今年以来", "本年以来"}:
        return TimeRangeExpr(TimeRangeKind.PERIOD_TO_NOW, source_text=text, period="year")
    if text in {"月初到现在", "本月以来"}:
        return TimeRangeExpr(TimeRangeKind.PERIOD_TO_NOW, source_text=text, period="month")
    if text == "本季度以来":
        return TimeRangeExpr(TimeRangeKind.PERIOD_TO_NOW, source_text=text, period="quarter")
    if text == "半个月":
        return TimeRangeExpr(TimeRangeKind.RECENT_ROLLING_DAYS, source_text=text, count=15)
    weekday = _WEEKDAY_RE.fullmatch(text)
    if weekday is not None:
        return TimeRangeExpr(
            TimeRangeKind.CALENDAR_WEEKDAY,
            source_text=text,
            week_offset=-1 if weekday["week"] == "上周" else 0,
            weekday=_WEEKDAY_VALUES[weekday["weekday"]],
        )
    match = _RECENT_RE.fullmatch(text)
    if match is None:
        return None
    count = _parse_count(match.group("count"))
    unit = match.group("unit")
    kind = {
        "周": TimeRangeKind.RECENT_ROLLING_WEEKS,
        "天": TimeRangeKind.RECENT_ROLLING_DAYS,
        "月": TimeRangeKind.RECENT_ROLLING_MONTHS,
        "个月": TimeRangeKind.RECENT_ROLLING_MONTHS,
        "小时": TimeRangeKind.RECENT_ROLLING_HOURS,
    }[unit]
    return TimeRangeExpr(kind, source_text=text, count=count, unit=unit)


def _parse_canonical(text: str) -> TimeRangeBounds | None:
    match = _CANONICAL_RE.fullmatch(text)
    if match is None:
        return None
    start = _parse_datetime(match.group("start")) if match.group("start") else None
    end = _parse_datetime(match.group("end")) if match.group("end") else None
    return TimeRangeBounds(start, end)


def _parse_moment(text: str, *, is_end: bool) -> datetime:
    normalized = text.strip()
    if not _DATETIME_RE.fullmatch(normalized):
        raise ValueError(f"unsupported datetime target: {text}")
    if _DATE_ONLY_RE.fullmatch(normalized):
        day = datetime.strptime(normalized, DATE_FORMAT)
        return day.replace(hour=23, minute=59, second=59) if is_end else day
    if len(normalized) == 16:
        normalized = f"{normalized}:00"
    return _parse_datetime(normalized)


def _parse_datetime(text: str) -> datetime:
    return datetime.strptime(text, TIME_FORMAT)


def _anchor(anchor_time: datetime | None) -> datetime:
    return (anchor_time or datetime.now()).replace(microsecond=0)


def _day_bounds(value: date) -> TimeRangeBounds:
    return TimeRangeBounds(_start_of_day(value), _end_of_day(value))


def _start_of_day(value: date) -> datetime:
    return datetime.combine(value, time.min)


def _end_of_day(value: date) -> datetime:
    return datetime.combine(value, time.max.replace(microsecond=0))


def _period_bounds(kind: TimeRangeKind, anchor: datetime) -> TimeRangeBounds:
    if kind.value.startswith("CURRENT"):
        start = _period_start(_period_name(kind), anchor)
    else:
        current = _period_start(_period_name(kind), anchor)
        start = _previous_period_start(_period_name(kind), current)
    return TimeRangeBounds(start, _period_end(_period_name(kind), start))


def _calendar_weekday(anchor: datetime, expr: TimeRangeExpr) -> date:
    week_offset = _required_week_offset(expr)
    weekday = _required_weekday(expr)
    week_start = _period_start("week", anchor)
    return (week_start + timedelta(weeks=week_offset, days=weekday - 1)).date()


def _period_name(kind: TimeRangeKind) -> str:
    for period in ("WEEK", "MONTH", "QUARTER", "YEAR"):
        if period in kind.value:
            return period.lower()
    raise ValueError(f"unsupported calendar period: {kind}")


def _period_start(period: str, anchor: datetime) -> datetime:
    period = _period_key(period)
    if period == "week":
        return _start_of_day(anchor.date() - timedelta(days=anchor.weekday()))
    if period == "month":
        return datetime(anchor.year, anchor.month, 1)
    if period == "quarter":
        return datetime(anchor.year, ((anchor.month - 1) // 3) * 3 + 1, 1)
    if period == "year":
        return datetime(anchor.year, 1, 1)
    raise ValueError(f"unsupported period: {period}")


def _previous_period_start(period: str, current_start: datetime) -> datetime:
    if period == "week":
        return current_start - timedelta(days=7)
    if period == "month":
        return _shift_month(current_start, -1)
    if period == "quarter":
        return _shift_month(current_start, -3)
    if period == "year":
        return datetime(current_start.year - 1, 1, 1)
    raise ValueError(f"unsupported period: {period}")


def _period_end(period: str, start: datetime) -> datetime:
    if period == "week":
        next_start = start + timedelta(days=7)
    elif period == "month":
        next_start = _shift_month(start, 1)
    elif period == "quarter":
        next_start = _shift_month(start, 3)
    elif period == "year":
        next_start = datetime(start.year + 1, 1, 1)
    else:
        raise ValueError(f"unsupported period: {period}")
    return next_start - timedelta(seconds=1)


def _shift_month(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + months
    return datetime(month_index // 12, month_index % 12 + 1, 1)


def _rolling_delta(kind: TimeRangeKind, count: int) -> timedelta:
    if kind is TimeRangeKind.RECENT_ROLLING_DAYS:
        return timedelta(days=count)
    if kind is TimeRangeKind.RECENT_ROLLING_WEEKS:
        return timedelta(days=7 * count)
    if kind is TimeRangeKind.RECENT_ROLLING_MONTHS:
        return timedelta(days=30 * count)
    if kind is TimeRangeKind.RECENT_ROLLING_HOURS:
        return timedelta(hours=count)
    raise ValueError(f"unsupported rolling kind: {kind}")


def _expr_datetime(expr: TimeRangeExpr, anchor: datetime, *, default: time) -> datetime:
    date_text = expr.date or expr.date_ref
    return _combine(_parse_date(_required_text(date_text, "date"), anchor), _parse_time(expr.time, default))


def _parse_date(text: str, anchor: datetime) -> date:
    value = text.strip()
    ref = _DATE_REF_ALIASES.get(value, value.upper())
    if ref == TimeRangeKind.TODAY.value:
        return anchor.date()
    if ref == TimeRangeKind.YESTERDAY.value:
        return anchor.date() - timedelta(days=1)
    if ref == TimeRangeKind.DAY_BEFORE_YESTERDAY.value:
        return anchor.date() - timedelta(days=2)
    if _DATE_ONLY_RE.fullmatch(value):
        return datetime.strptime(value, DATE_FORMAT).date()
    slash = _SLASH_DATE_RE.fullmatch(value)
    if slash is not None:
        return date(int(slash["year"]), int(slash["month"]), int(slash["day"]))
    chinese = _CHINESE_DATE_RE.fullmatch(value)
    if chinese is not None:
        year = int(chinese["year"] or anchor.year)
        return date(year, int(chinese["month"]), int(chinese["day"]))
    raise ValueError(f"unsupported date: {text}")


def _parse_time(text_value: str | None, default: time) -> time:
    if text_value is None:
        return default
    match = _TIME_RE.fullmatch(text_value.strip())
    if match is None:
        raise ValueError(f"unsupported time: {text_value}")
    hour = int(match["hour"])
    prefix = match["prefix"]
    if prefix in {"下午", "晚上"} and hour < 12:
        hour += 12
    if prefix == "凌晨" and hour == 12:
        hour = 0
    return time(hour, int(match["minute"]), int(match["second"] or 0))


def _combine(date_value: date, time_value: time) -> datetime:
    return datetime.combine(date_value, time_value)


def _validate_expr(expr: TimeRangeExpr) -> None:
    if expr.kind is TimeRangeKind.LOW_CONFIDENCE:
        raise ValueError("low confidence time_range expression")
    if expr.confidence is not None and expr.confidence < _MIN_CONFIDENCE:
        raise ValueError("low confidence time_range expression")
    if expr.kind is TimeRangeKind.DAY_BEFORE_YESTERDAY and _mentions_three_days_ago(expr):
        raise ValueError("DAY_BEFORE_YESTERDAY conflicts with 大前天")
    if expr.kind in {TimeRangeKind.CURRENT_CALENDAR_WEEK, TimeRangeKind.PREVIOUS_CALENDAR_WEEK}:
        if _mentions_specific_weekday(expr.source_text):
            raise ValueError("calendar week kind conflicts with specific weekday source_text")
    if expr.kind is TimeRangeKind.RELATIVE_DAY:
        _required_offset_days(expr)
    if expr.kind is TimeRangeKind.CALENDAR_WEEKDAY:
        _required_week_offset(expr)
        _required_weekday(expr)


def _mentions_three_days_ago(expr: TimeRangeExpr) -> bool:
    return any(
        "大前天" in value
        for value in (
            expr.source_text,
            expr.date or "",
            expr.start_date or "",
            expr.end_date or "",
            expr.date_ref or "",
        )
    )


def _mentions_specific_weekday(source_text: str) -> bool:
    return re.search(r"(?:本周|这周|上周)[一二三四五六日天]|(?:星期|礼拜)[一二三四五六日天]", source_text) is not None


def _period_key(period: str) -> str:
    key = period.strip()
    if key in _PERIOD_ALIASES:
        return _PERIOD_ALIASES[key]
    lower = key.lower()
    if lower in _PERIOD_ALIASES:
        return _PERIOD_ALIASES[lower]
    raise ValueError(f"unsupported period: {period}")


def _required_count(expr: TimeRangeExpr) -> int:
    if expr.count is None or expr.count < 1:
        raise ValueError("time_range count must be positive")
    return expr.count


def _required_offset_days(expr: TimeRangeExpr) -> int:
    if expr.offset_days is None:
        raise ValueError("time_range offset_days is required")
    return expr.offset_days


def _required_week_offset(expr: TimeRangeExpr) -> int:
    if expr.week_offset is None:
        raise ValueError("time_range week_offset is required")
    return expr.week_offset


def _required_weekday(expr: TimeRangeExpr) -> int:
    if expr.weekday is None or expr.weekday < 1 or expr.weekday > 7:
        raise ValueError("time_range weekday must be 1 through 7")
    return expr.weekday


def _required_text(value: Any, name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"time_range {name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _optional_count(value: Any) -> int | None:
    if value is None:
        return None
    count = int(value)
    if count < 1:
        raise ValueError("time_range count must be positive")
    return count


def _optional_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"time_range {name} must not be blank")
    return int(value)


def _optional_confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        raise ValueError("time_range confidence must not be blank")
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        raise ValueError("time_range confidence must be between 0 and 1")
    return confidence


def _reject_empty_string_fields(payload: Mapping[str, Any]) -> None:
    for key, value in payload.items():
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"time_range field must not be blank: {key}")


def _parse_count(text: str) -> int:
    if text.isdigit():
        return int(text)
    if text in _CHINESE_COUNTS:
        return _CHINESE_COUNTS[text]
    raise ValueError(f"unsupported time_range count: {text}")


__all__ = [
    "LLMTimeRangeExtractor",
    "TimeRangeBounds",
    "TimeRangeExpr",
    "TimeRangeExtractor",
    "TimeRangeKind",
    "normalize_time_range",
    "normalize_time_range_expr",
    "normalize_time_range_with_extractor",
    "parse_time_range_expr",
    "render_bounds",
    "time_range_params",
]
