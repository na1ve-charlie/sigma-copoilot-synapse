from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any


_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_DATE_FORMAT = "%Y-%m-%d"
_CANONICAL_SUMMARY_RESULTS = (
    "不合格",
    "合格",
    "未设置界限值",
    "异常",
    "次异常",
    "检测失败",
)
_SUMMARY_RESULT_ALIASES = {
    "ng": "不合格",
    "fail": "不合格",
    "不合格品": "不合格",
    "ok": "合格",
    "pass": "合格",
}
_RANGE_SPLIT_RE = re.compile(r"\s*(?:到|至)\s*")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?$")
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RECENT_RE = re.compile(r"^最近(?P<count>\d+|一)(?P<unit>周|天|月|个月|小时)$")
_CANONICAL_RE = re.compile(
    r"^(?:start=(?P<start>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))?"
    r"(?:; )?"
    r"(?:end=(?P<end>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))?$"
)


def normalize_entity_target(entity_type: str, target: Any) -> Any:
    if isinstance(target, tuple):
        return tuple(normalize_entity_target(entity_type, item) for item in target)
    if entity_type == "summary_result":
        return _normalize_summary_result(target)
    if entity_type == "time_range":
        return normalize_time_range(target)
    if entity_type == "latest_n":
        return str(_normalize_positive_int(target))
    return target


def normalize_time_range(target: Any) -> str:
    if not isinstance(target, str):
        raise ValueError("time_range target must be text")
    text = target.strip()
    if not text:
        raise ValueError("time_range target must not be blank")

    canonical = _parse_canonical(text)
    if canonical is not None:
        return _render_bounds(*canonical)
    if text.endswith("前"):
        return _render_bounds(None, _parse_moment(text[:-1].strip(), is_end=False))
    if text.endswith("后"):
        return _render_bounds(_parse_moment(text[:-1].strip(), is_end=False), None)

    parts = _RANGE_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        return _render_bounds(
            _parse_moment(parts[0], is_end=False),
            _parse_moment(parts[1], is_end=True),
        )

    relative = _parse_relative(text)
    if relative is not None:
        return _render_bounds(*relative)
    raise ValueError(f"unsupported time_range target: {target}")


def time_range_params(target: str) -> dict[str, str]:
    match = _CANONICAL_RE.fullmatch(target.strip())
    if match is None:
        raise ValueError(f"invalid canonical time_range target: {target}")
    result = {key: value for key, value in match.groupdict().items() if value is not None}
    if not result:
        raise ValueError(f"invalid canonical time_range target: {target}")
    return result


def _normalize_summary_result(target: Any) -> str:
    value = str(target).strip()
    if not value:
        raise ValueError("summary_result target must not be blank")
    canonical = _SUMMARY_RESULT_ALIASES.get(value.casefold(), value)
    if canonical not in _CANONICAL_SUMMARY_RESULTS:
        raise ValueError(f"unsupported summary_result target: {target}")
    return canonical


def _normalize_positive_int(target: Any) -> int:
    value = int(str(target).strip())
    if value < 1:
        raise ValueError("latest_n target must be positive")
    return value


def _parse_canonical(text: str) -> tuple[datetime | None, datetime | None] | None:
    match = _CANONICAL_RE.fullmatch(text)
    if match is None:
        return None
    start = _parse_datetime(match.group("start")) if match.group("start") else None
    end = _parse_datetime(match.group("end")) if match.group("end") else None
    return start, end


def _parse_relative(text: str) -> tuple[datetime, datetime] | None:
    now = _now()
    if text == "今天":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), now
    if text == "昨天":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start.replace(hour=23, minute=59, second=59)

    match = _RECENT_RE.fullmatch(text)
    if match is None:
        return None
    count = 1 if match.group("count") == "一" else int(match.group("count"))
    unit = match.group("unit")
    if unit == "周":
        delta = timedelta(days=7 * count)
    elif unit == "天":
        delta = timedelta(days=count)
    elif unit in {"月", "个月"}:
        delta = timedelta(days=30 * count)
    else:
        delta = timedelta(hours=count)
    return now - delta, now


def _parse_moment(text: str, *, is_end: bool) -> datetime:
    normalized = text.strip()
    if not _DATETIME_RE.fullmatch(normalized):
        raise ValueError(f"unsupported datetime target: {text}")
    if _DATE_ONLY_RE.fullmatch(normalized):
        day = datetime.strptime(normalized, _DATE_FORMAT)
        return day.replace(hour=23, minute=59, second=59) if is_end else day
    if len(normalized) == 16:
        normalized = f"{normalized}:00"
    return _parse_datetime(normalized)


def _parse_datetime(text: str) -> datetime:
    return datetime.strptime(text, _TIME_FORMAT)


def _render_bounds(start: datetime | None, end: datetime | None) -> str:
    parts: list[str] = []
    if start is not None:
        parts.append(f"start={start.strftime(_TIME_FORMAT)}")
    if end is not None:
        parts.append(f"end={end.strftime(_TIME_FORMAT)}")
    if not parts:
        raise ValueError("time_range must include start or end")
    return "; ".join(parts)


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


__all__ = ["normalize_entity_target", "normalize_time_range", "time_range_params"]
