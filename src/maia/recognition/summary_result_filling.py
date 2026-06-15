from __future__ import annotations

import re
from dataclasses import dataclass

from maia.recognition.normalization import SUMMARY_RESULT_ALIASES
from maia.recognition.report import RecognitionReport, RecognitionSlotOperation


_SUMMARY_RESULT_INTENT = "task.nvh.selection.set_summary_result"
_TEXT_PATTERNS = (
    ("未设置界限值", "未设置界限值"),
    ("界限值未设置", "未设置界限值"),
    ("检测失败", "检测失败"),
    ("次异常", "次异常"),
    ("不合格", "不合格"),
    ("合格", "合格"),
    ("异常", "异常"),
)
_ALIAS_PATTERNS = tuple((alias, canonical) for alias, canonical in SUMMARY_RESULT_ALIASES.items())


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    value: str


def fill_summary_result_slots(report: RecognitionReport) -> RecognitionReport:
    values = _extract_summary_results(report.message)
    if not values:
        return report

    operations = tuple(
        operation
        for operation in report.slot_operations
        if operation.entity_type != "summary_result" and not _is_invalid_indicator(operation)
    )
    summary_operation = RecognitionSlotOperation(
        intent=_SUMMARY_RESULT_INTENT,
        score=1.0,
        action="replace",
        entity_type="summary_result",
        target=values[0] if len(values) == 1 else values,
        slot_valid=True if len(values) == 1 else tuple(True for _ in values),
    )
    return report.model_copy(update={"slot_operations": (*operations, summary_operation)})


def _extract_summary_results(message: str) -> tuple[str, ...]:
    selected: list[_Match] = []
    for match in sorted(_candidate_matches(message), key=lambda item: (item.start, -(item.end - item.start))):
        if any(_overlaps(match, existing) for existing in selected):
            continue
        selected.append(match)

    values: list[str] = []
    for match in sorted(selected, key=lambda item: item.start):
        if match.value not in values:
            values.append(match.value)
    return tuple(values)


def _candidate_matches(message: str) -> tuple[_Match, ...]:
    matches: list[_Match] = []
    for raw, canonical in _TEXT_PATTERNS:
        start = message.find(raw)
        while start != -1:
            matches.append(_Match(start, start + len(raw), canonical))
            start = message.find(raw, start + 1)

    for raw, canonical in _ALIAS_PATTERNS:
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(raw)}(?![A-Za-z0-9])", re.IGNORECASE)
        matches.extend(_Match(item.start(), item.end(), canonical) for item in pattern.finditer(message))
    return tuple(matches)


def _overlaps(left: _Match, right: _Match) -> bool:
    return left.start < right.end and right.start < left.end


def _is_invalid_indicator(operation: RecognitionSlotOperation) -> bool:
    return operation.entity_type == "indicator" and not _all_valid(operation.slot_valid)


def _all_valid(value: bool | tuple[bool, ...]) -> bool:
    return all(value) if isinstance(value, tuple) else bool(value)


__all__ = ["fill_summary_result_slots"]
