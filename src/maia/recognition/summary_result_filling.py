from __future__ import annotations

import re
from dataclasses import dataclass

from maia.recognition.normalization import MARKING_RESULT_VALUES, SUMMARY_RESULT_ALIASES
from maia.recognition.report import RecognitionReport, RecognitionSlotOperation


_SUMMARY_RESULT_INTENT = "task.nvh.selection.set_summary_result"
_MARKING_RESULT_INTENTS = {
    "manual_tagging": "task.nvh.selection.set_manual_tagging",
    "status": "task.nvh.selection.set_status",
}
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
_MARKING_PATTERNS = tuple((value, value) for value in MARKING_RESULT_VALUES)
_SCOPED_RESULT_KEYWORDS = {
    "manual_tagging": (
        "人工标记总结果",
        "人工标记总状态",
        "人工标记结果",
        "人工标记状态",
        "人工标记",
    ),
    "status": (
        "标记测试段结果",
        "标记测试段状态",
        "测试段结果",
        "测试段状态",
    ),
}
_SCOPED_RESULT_ENTITIES = frozenset((*_MARKING_RESULT_INTENTS, "manual_tag", "summary_result"))


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    value: str


def fill_summary_result_slots(report: RecognitionReport) -> RecognitionReport:
    scoped_operations = _scoped_result_operations(report.message)
    if scoped_operations:
        operations = tuple(
            operation
            for operation in report.slot_operations
            if operation.entity_type not in _SCOPED_RESULT_ENTITIES
            and not _is_invalid_indicator(operation)
        )
        return report.model_copy(update={"slot_operations": (*operations, *scoped_operations)})

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


def _scoped_result_operations(message: str) -> tuple[RecognitionSlotOperation, ...]:
    operations: list[RecognitionSlotOperation] = []
    for entity_type, keywords in _SCOPED_RESULT_KEYWORDS.items():
        values = _extract_scoped_values(message, keywords)
        if not values:
            continue
        operations.append(
            RecognitionSlotOperation(
                intent=_MARKING_RESULT_INTENTS[entity_type],
                score=1.0,
                action="replace",
                entity_type=entity_type,
                target=values[0] if len(values) == 1 else values,
                slot_valid=True if len(values) == 1 else tuple(True for _ in values),
            )
        )
    return tuple(operations)


def _extract_scoped_values(message: str, keywords: tuple[str, ...]) -> tuple[str, ...]:
    matches = _candidate_matches(message, patterns=_MARKING_PATTERNS, aliases=())
    values: list[str] = []
    for keyword in sorted(keywords, key=len, reverse=True):
        start = message.find(keyword)
        while start != -1:
            keyword_end = start + len(keyword)
            scoped_match = next((match for match in matches if match.start >= keyword_end), None)
            if scoped_match is not None and scoped_match.value not in values:
                values.append(scoped_match.value)
            start = message.find(keyword, start + 1)
    return tuple(values)


def _extract_summary_results(message: str) -> tuple[str, ...]:
    selected: list[_Match] = []
    for match in sorted(
        _candidate_matches(message, patterns=_TEXT_PATTERNS, aliases=_ALIAS_PATTERNS),
        key=lambda item: (item.start, -(item.end - item.start)),
    ):
        if any(_overlaps(match, existing) for existing in selected):
            continue
        selected.append(match)

    values: list[str] = []
    for match in sorted(selected, key=lambda item: item.start):
        if match.value not in values:
            values.append(match.value)
    return tuple(values)


def _candidate_matches(
    message: str,
    *,
    patterns: tuple[tuple[str, str], ...],
    aliases: tuple[tuple[str, str], ...],
) -> tuple[_Match, ...]:
    matches: list[_Match] = []
    for raw, canonical in patterns:
        start = message.find(raw)
        while start != -1:
            matches.append(_Match(start, start + len(raw), canonical))
            start = message.find(raw, start + 1)

    for raw, canonical in aliases:
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(raw)}(?![A-Za-z0-9])", re.IGNORECASE)
        matches.extend(_Match(item.start(), item.end(), canonical) for item in pattern.finditer(message))
    return tuple(sorted(matches, key=lambda item: (item.start, -(item.end - item.start))))


def _overlaps(left: _Match, right: _Match) -> bool:
    return left.start < right.end and right.start < left.end


def _is_invalid_indicator(operation: RecognitionSlotOperation) -> bool:
    return operation.entity_type == "indicator" and not _all_valid(operation.slot_valid)


def _all_valid(value: bool | tuple[bool, ...]) -> bool:
    return all(value) if isinstance(value, tuple) else bool(value)


__all__ = ["fill_summary_result_slots"]
