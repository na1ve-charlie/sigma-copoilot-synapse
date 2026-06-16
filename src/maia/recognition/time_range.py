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


class DateTimePrecision(str, Enum):
    YEAR = "YEAR"
    MONTH = "MONTH"
    DAY = "DAY"
    MINUTE = "MINUTE"
    SECOND = "SECOND"


@dataclass(frozen=True)
class PartialDateTime:
    year: int
    month: int | None
    day: int | None
    hour: int | None
    minute: int | None
    second: int | None
    precision: DateTimePrecision


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
_COMPACT_RANGE_RE = re.compile(r"^(?P<start>\d{3,8})\s*[-~]\s*(?P<end>\d{3,8})$")
_SEPARATED_PARTIAL_RE = re.compile(r"^(?:(?P<year>\d{4})[./])?(?P<month>\d{1,2})[./](?P<day>\d{1,2})$")
_ISO_MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{1,2})$")
_CHINESE_PARTIAL_RE = re.compile(
    r"^(?:(?P<year>[\d零〇一二两三四五六七八九十]{4})年)?"
    r"(?:(?P<month>[\d零〇一二两三四五六七八九十]{1,3})月)?"
    r"(?:(?P<day>[\d零〇一二两三四五六七八九十]{1,3})(?:日|号))?$"
)
_TRAILING_TIME_RE = re.compile(r"(?P<time>(?:上午|下午|晚上|中午|凌晨|早上)?\d{1,2}:\d{2}(?::\d{2})?)$")
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
    r"^(?P<prefix>最近|近|过去)?(?P<count>\d+|一|二|两|三|四|五|六|七|八|九|十|十一|十二|十三|十四|十五)(?P<unit>周|天|月|个月|小时)$"
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

    anchor = _anchor(anchor_time)
    normalized_text = _strip_query_scaffold(text)

    partial = _parse_partial_rule_bounds(normalized_text, anchor)
    if partial is not None:
        return render_bounds(partial)

    expr = _parse_rule_expr(normalized_text)
    if expr is not None:
        return render_bounds(normalize_time_range_expr(expr, anchor_time=anchor))

    if normalized_text.endswith("前"):
        return render_bounds(TimeRangeBounds(None, _parse_moment(normalized_text[:-1].strip(), is_end=False)))
    if normalized_text.endswith("后"):
        return render_bounds(TimeRangeBounds(_parse_moment(normalized_text[:-1].strip(), is_end=False), None))

    parts = _RANGE_SPLIT_RE.split(normalized_text, maxsplit=1)
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
        value = _parse_partial_date_text(_required_text(expr.date, "date"), anchor)
        return TimeRangeBounds(_partial_start(value), _partial_end(value))
    if kind is TimeRangeKind.ABSOLUTE_DATE_RANGE:
        start = _parse_partial_date_text(_required_text(expr.start_date, "start_date"), anchor)
        end = _parse_partial_date_text(_required_text(expr.end_date, "end_date"), anchor)
        return TimeRangeBounds(
            _partial_start(start),
            _partial_end(end),
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
_CHINESE_DIGITS = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))


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
    if unit == "月" and match.group("prefix") is None:
        return None
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


def _strip_query_scaffold(text: str) -> str:
    value = re.sub(r"\s+", "", text.strip())
    for prefix in ("我想查看", "我想看", "我要查看", "我要看", "想查看", "想看", "查看", "查询", "看"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    for suffix in ("的测试记录", "测试记录", "的测试数据", "测试数据"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value


def _parse_partial_rule_bounds(text: str, anchor: datetime) -> TimeRangeBounds | None:
    if not text:
        return None
    since = re.fullmatch(r"(?:从)?(?P<value>.+?)(?:以来|起|开始)", text)
    if since is not None:
        try:
            value = _parse_partial_datetime(since["value"], anchor)
        except ValueError:
            if _looks_like_partial_datetime(since["value"]):
                raise
            return None
        return TimeRangeBounds(_partial_start(value), anchor)

    for suffix in ("之前", "以前", "前"):
        if text.endswith(suffix):
            value = _parse_partial_datetime(text[: -len(suffix)], anchor)
            return TimeRangeBounds(None, _partial_start(value))
    for suffix in ("之后", "以后", "后"):
        if text.endswith(suffix):
            value = _parse_partial_datetime(text[: -len(suffix)], anchor)
            return TimeRangeBounds(_partial_after_start(value), None)

    compact = _COMPACT_RANGE_RE.fullmatch(text)
    parts = (compact["start"], compact["end"]) if compact is not None else None
    if parts is None:
        split = _RANGE_SPLIT_RE.split(text, maxsplit=1)
        parts = (split[0], split[1]) if len(split) == 2 else None
    if parts is not None:
        start, end = parts
        if end in {"现在", "当前"}:
            end_time = anchor
        elif end == "今天":
            end_time = _end_of_day(anchor.date())
        else:
            end_time = _partial_end(_parse_partial_datetime(end, anchor))
        return TimeRangeBounds(
            _partial_start(_parse_partial_datetime(start, anchor)),
            end_time,
        )

    if _parse_rule_expr(text) is not None:
        return None

    try:
        value = _parse_partial_datetime(text, anchor)
    except ValueError:
        if _looks_like_partial_datetime(text):
            raise
        return None
    return TimeRangeBounds(_partial_start(value), _partial_end(value))


def _parse_partial_datetime(text: str, anchor: datetime) -> PartialDateTime:
    value = text.strip()
    if not value:
        raise ValueError("partial datetime must not be blank")
    relative = _parse_relative_partial(value, anchor)
    if relative is not None:
        return relative
    value, parsed_time, precision = _split_trailing_time(value)
    if not value and parsed_time is not None:
        return _partial(anchor.year, anchor.month, anchor.day, parsed_time, precision)
    relative = _parse_relative_partial(value, anchor)
    if relative is not None:
        if parsed_time is None:
            return relative
        return _partial(relative.year, relative.month, relative.day, parsed_time, precision)
    if "月" in value and not value.endswith(("月", "日", "号")):
        value = f"{value}号"

    if _DATE_ONLY_RE.fullmatch(value):
        day = datetime.strptime(value, DATE_FORMAT).date()
        return _partial(day.year, day.month, day.day, parsed_time, precision)
    month = _ISO_MONTH_RE.fullmatch(value)
    if month is not None:
        return _partial(int(month["year"]), int(month["month"]), None, parsed_time, precision)

    separated = _SEPARATED_PARTIAL_RE.fullmatch(value)
    if separated is not None:
        year = int(separated["year"] or anchor.year)
        return _partial(year, int(separated["month"]), int(separated["day"]), parsed_time, precision)

    if value.isdigit():
        return _parse_compact_digits(value, anchor, parsed_time, precision)

    chinese = _CHINESE_PARTIAL_RE.fullmatch(value)
    if chinese is not None and any(chinese.groupdict().values()):
        year = _parse_year_token(chinese["year"]) if chinese["year"] else anchor.year
        day_value = _parse_number_token(chinese["day"]) if chinese["day"] else None
        month_value = (
            _parse_number_token(chinese["month"])
            if chinese["month"]
            else anchor.month if day_value is not None else None
        )
        return _partial(year, month_value, day_value, parsed_time, precision)

    raise ValueError(f"unsupported partial datetime: {text}")


def _parse_partial_date_text(text: str, anchor: datetime) -> PartialDateTime:
    try:
        return _parse_partial_datetime(text, anchor)
    except ValueError:
        value = _parse_date(text, anchor)
        return _partial(value.year, value.month, value.day, None, None)


def _parse_relative_partial(text: str, anchor: datetime) -> PartialDateTime | None:
    day_offset = {"今天": 0, "今日": 0, "昨天": -1, "前天": -2, "大前天": -3}.get(text)
    if day_offset is not None:
        target = anchor.date() + timedelta(days=day_offset)
        return _partial(target.year, target.month, target.day, None, None)
    month_offset = {
        "月初": 0,
        "本月初": 0,
        "这个月初": 0,
        "本月": 0,
        "这个月": 0,
        "上月": -1,
        "上个月": -1,
        "上月初": -1,
        "上个月初": -1,
    }.get(text)
    if month_offset is not None:
        target = _shift_month(datetime(anchor.year, anchor.month, 1), month_offset)
        return _partial(target.year, target.month, 1 if text.endswith("初") else None, None, None)
    year_offset = {"今年": 0, "去年": -1}.get(text)
    if year_offset is not None:
        return PartialDateTime(anchor.year + year_offset, None, None, None, None, None, DateTimePrecision.YEAR)
    return None


def _split_trailing_time(text: str) -> tuple[str, time | None, DateTimePrecision | None]:
    normalized = text.replace(" ", "")
    match = _TRAILING_TIME_RE.search(normalized)
    if match is None:
        return normalized, None, None
    raw_time = match["time"]
    precision = DateTimePrecision.SECOND if raw_time.count(":") == 2 else DateTimePrecision.MINUTE
    return normalized[: match.start()], _parse_time(raw_time, time.min), precision


def _parse_compact_digits(
    value: str,
    anchor: datetime,
    parsed_time: time | None,
    precision: DateTimePrecision | None,
) -> PartialDateTime:
    if len(value) == 8:
        return _partial(int(value[:4]), int(value[4:6]), int(value[6:8]), parsed_time, precision)
    if len(value) == 6:
        return _partial(int(value[:4]), int(value[4:6]), None, parsed_time, precision)
    if len(value) == 4:
        return _partial(anchor.year, int(value[:2]), int(value[2:4]), parsed_time, precision)
    if len(value) == 3:
        return _partial(anchor.year, int(value[:1]), int(value[1:3]), parsed_time, precision)
    raise ValueError(f"unsupported compact date: {value}")


def _partial(
    year: int,
    month: int | None,
    day: int | None,
    parsed_time: time | None,
    time_precision: DateTimePrecision | None,
) -> PartialDateTime:
    if parsed_time is not None and (month is None or day is None):
        raise ValueError("time requires day precision")
    if month is None:
        return PartialDateTime(year, None, None, None, None, None, DateTimePrecision.YEAR)
    if day is None:
        date(year, month, 1)
        return PartialDateTime(year, month, None, None, None, None, DateTimePrecision.MONTH)
    date(year, month, day)
    if parsed_time is None:
        return PartialDateTime(year, month, day, None, None, None, DateTimePrecision.DAY)
    return PartialDateTime(
        year,
        month,
        day,
        parsed_time.hour,
        parsed_time.minute,
        parsed_time.second,
        time_precision or DateTimePrecision.SECOND,
    )


def _partial_start(value: PartialDateTime) -> datetime:
    if value.precision is DateTimePrecision.YEAR:
        return datetime(value.year, 1, 1)
    if value.month is None:
        raise ValueError("partial datetime month is required")
    if value.precision is DateTimePrecision.MONTH:
        return datetime(value.year, value.month, 1)
    if value.day is None:
        raise ValueError("partial datetime day is required")
    if value.precision is DateTimePrecision.DAY:
        return datetime(value.year, value.month, value.day)
    return datetime(
        value.year,
        value.month,
        value.day,
        value.hour or 0,
        value.minute or 0,
        value.second or 0,
    )


def _partial_end(value: PartialDateTime) -> datetime:
    start = _partial_start(value)
    if value.precision is DateTimePrecision.YEAR:
        return datetime(value.year + 1, 1, 1) - timedelta(seconds=1)
    if value.precision is DateTimePrecision.MONTH:
        return _shift_month(start, 1) - timedelta(seconds=1)
    if value.precision is DateTimePrecision.DAY:
        return _end_of_day(start.date())
    if value.precision is DateTimePrecision.MINUTE:
        return start + timedelta(seconds=59)
    return start


def _partial_after_start(value: PartialDateTime) -> datetime:
    if value.precision is DateTimePrecision.YEAR:
        return datetime(value.year + 1, 1, 1)
    if value.precision is DateTimePrecision.MONTH:
        return _shift_month(_partial_start(value), 1)
    return _partial_start(value)


def _looks_like_partial_datetime(text: str) -> bool:
    if not text or text.startswith(("最近", "近", "过去")) or "个" in text:
        return False
    return (
        text.isdigit()
        or bool(re.search(r"[年月日号]", text))
        or bool(re.fullmatch(r"\d{1,4}[./]\d{1,2}(?:[./]\d{1,2})?", text))
        or bool(re.fullmatch(r"\d{4}-\d{1,2}(?:-\d{1,2})?", text))
    )


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
    required_date = _required_text(date_text, "date")
    try:
        partial = _parse_partial_datetime(required_date, anchor)
    except ValueError:
        return _combine(_parse_date(required_date, anchor), _parse_time(expr.time, default))
    if expr.time is None and partial.precision in {DateTimePrecision.MINUTE, DateTimePrecision.SECOND}:
        return _partial_start(partial)
    return _combine(_partial_start(partial).date(), _parse_time(expr.time, default))


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
    try:
        return _partial_start(_parse_partial_datetime(value, anchor)).date()
    except ValueError as exc:
        raise ValueError(f"unsupported date: {text}") from exc


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


def _parse_year_token(text: str) -> int:
    if text.isdigit():
        return int(text)
    if len(text) == 4 and all(char in _CHINESE_DIGITS for char in text):
        return int("".join(str(_CHINESE_DIGITS[char]) for char in text))
    raise ValueError(f"unsupported year token: {text}")


def _parse_number_token(text: str) -> int:
    if text.isdigit():
        return int(text)
    if text in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[text]
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + _parse_number_token(text[1:])
    if text.endswith("十"):
        return _parse_number_token(text[:-1]) * 10
    if "十" in text:
        tens, ones = text.split("十", maxsplit=1)
        return _parse_number_token(tens) * 10 + _parse_number_token(ones)
    raise ValueError(f"unsupported number token: {text}")


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
