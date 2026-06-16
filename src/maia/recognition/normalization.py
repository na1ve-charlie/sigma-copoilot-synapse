from __future__ import annotations

from collections.abc import MutableMapping
from datetime import datetime
from typing import Any

from maia.recognition.time_range import (
    TimeRangeExtractor,
    normalize_time_range as _normalize_time_range,
    normalize_time_range_with_extractor,
    time_range_params,
)

SUMMARY_RESULT_VALUES = (
    "不合格",
    "合格",
    "未设置界限值",
    "异常",
    "次异常",
    "检测失败",
)
SUMMARY_RESULT_ALIASES = {
    "ng": "不合格",
    "ok": "合格",
}
MARKING_RESULT_VALUES = (
    "合格",
    "不合格",
    "无效",
)
_MARKING_RESULT_ENTITY_TYPES = {"manual_tagging", "status"}
_SELF_VALIDATING_ENTITY_TYPES = {
    "summary_result",
    "time_range",
    "latest_n",
    *_MARKING_RESULT_ENTITY_TYPES,
}


def normalize_entity_target(entity_type: str, target: Any) -> Any:
    if isinstance(target, tuple):
        return tuple(normalize_entity_target(entity_type, item) for item in target)
    if entity_type == "summary_result":
        return _normalize_summary_result(target)
    if entity_type in _MARKING_RESULT_ENTITY_TYPES:
        return _normalize_marking_result(entity_type, target)
    if entity_type == "time_range":
        return normalize_time_range(target)
    if entity_type == "latest_n":
        return str(_normalize_positive_int(target))
    return target


def normalize_slot_value(entity_type: str, target: Any, slot_valid: Any) -> tuple[Any, Any]:
    if isinstance(target, tuple) or isinstance(slot_valid, tuple):
        targets = _as_tuple(target)
        valids = _as_tuple(slot_valid)
        size = max(len(targets), len(valids))
        normalized_targets: list[Any] = []
        normalized_valids: list[bool] = []
        for item, valid in zip(_broadcast(targets, size), _broadcast(valids, size), strict=True):
            normalized_target, normalized_valid = normalize_slot_value(entity_type, item, valid)
            normalized_targets.append(normalized_target)
            normalized_valids.append(bool(normalized_valid))
        return tuple(normalized_targets), tuple(normalized_valids)

    try:
        normalized_target = normalize_entity_target(entity_type, target)
    except ValueError:
        if entity_type in _SELF_VALIDATING_ENTITY_TYPES:
            return target, False
        return target, bool(slot_valid)

    if entity_type in _SELF_VALIDATING_ENTITY_TYPES:
        return normalized_target, True
    return normalized_target, bool(slot_valid)


async def normalize_slot_value_with_time_range_extractor(
    entity_type: str,
    target: Any,
    slot_valid: Any,
    *,
    message: str,
    time_range_extractor: TimeRangeExtractor | None = None,
    time_range_cache: MutableMapping[str, tuple[Any, bool]] | None = None,
) -> tuple[Any, Any]:
    if isinstance(target, tuple) or isinstance(slot_valid, tuple):
        targets = _as_tuple(target)
        valids = _as_tuple(slot_valid)
        size = max(len(targets), len(valids))
        normalized_targets: list[Any] = []
        normalized_valids: list[bool] = []
        for item, valid in zip(_broadcast(targets, size), _broadcast(valids, size), strict=True):
            normalized_target, normalized_valid = await normalize_slot_value_with_time_range_extractor(
                entity_type,
                item,
                valid,
                message=message,
                time_range_extractor=time_range_extractor,
                time_range_cache=time_range_cache,
            )
            normalized_targets.append(normalized_target)
            normalized_valids.append(bool(normalized_valid))
        return tuple(normalized_targets), tuple(normalized_valids)

    if entity_type != "time_range":
        return normalize_slot_value(entity_type, target, slot_valid)

    cache_key = str(target)
    if time_range_cache is not None and cache_key in time_range_cache:
        return time_range_cache[cache_key]

    try:
        normalized = await normalize_time_range_with_extractor(
            target,
            message=message,
            extractor=time_range_extractor,
            anchor_time=_now(),
        )
    except ValueError:
        result = (target, False)
    else:
        result = (normalized, True)
    if time_range_cache is not None:
        time_range_cache[cache_key] = result
    return result


def normalize_time_range(target: Any) -> str:
    return _normalize_time_range(target, anchor_time=_now())


def _normalize_summary_result(target: Any) -> str:
    value = str(target).strip()
    if not value:
        raise ValueError("summary_result target must not be blank")
    canonical = SUMMARY_RESULT_ALIASES.get(value.casefold(), value)
    if canonical not in SUMMARY_RESULT_VALUES:
        raise ValueError(f"unsupported summary_result target: {target}")
    return canonical


def _normalize_marking_result(entity_type: str, target: Any) -> str:
    value = str(target).strip()
    if not value:
        raise ValueError(f"{entity_type} target must not be blank")
    if value not in MARKING_RESULT_VALUES:
        raise ValueError(f"unsupported {entity_type} target: {target}")
    return value


def _normalize_positive_int(target: Any) -> int:
    value = int(str(target).strip())
    if value < 1:
        raise ValueError("latest_n target must be positive")
    return value


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _as_tuple(value: Any) -> tuple[Any, ...]:
    return value if isinstance(value, tuple) else (value,)


def _broadcast(values: tuple[Any, ...], size: int) -> tuple[Any, ...]:
    if len(values) == size:
        return values
    if len(values) == 1:
        return values * size
    raise ValueError("slot operation arrays must align")


__all__ = [
    "normalize_entity_target",
    "normalize_slot_value",
    "normalize_slot_value_with_time_range_extractor",
    "normalize_time_range",
    "SUMMARY_RESULT_ALIASES",
    "SUMMARY_RESULT_VALUES",
    "time_range_params",
]
