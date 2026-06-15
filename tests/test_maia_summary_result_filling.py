from __future__ import annotations

import pytest

from maia.recognition.report import RecognitionReport, RecognitionSlotOperation
from maia.recognition.summary_result_filling import fill_summary_result_slots


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("我想查看次异常测试件的测试记录", "次异常"),
        ("我想查看检测失败测试件的测试记录", "检测失败"),
        ("我想查看界限值未设置的测试记录", "未设置界限值"),
    ],
)
def test_summary_result_filling_extracts_single_canonical_value(
    message: str,
    expected: str,
) -> None:
    report = fill_summary_result_slots(_report(message))

    assert report.slot_operations[-1].entity_type == "summary_result"
    assert report.slot_operations[-1].action == "replace"
    assert report.slot_operations[-1].target == expected
    assert report.slot_operations[-1].slot_valid is True


def test_summary_result_filling_extracts_multiple_values_in_message_order() -> None:
    report = fill_summary_result_slots(_report("我想查看次异常和不合格测试件的测试记录"))

    assert report.slot_operations[-1].target == ("次异常", "不合格")
    assert report.slot_operations[-1].slot_valid == (True, True)


def test_summary_result_filling_uses_longest_non_overlapping_match() -> None:
    report = fill_summary_result_slots(_report("我想查看次异常测试件和NG件的测试记录"))

    assert report.slot_operations[-1].target == ("次异常", "不合格")


def test_summary_result_filling_overwrites_existing_summary_result_operation() -> None:
    report = fill_summary_result_slots(
        _report(
            "我想查看不合格测试件的测试记录",
            _slot_operation("summary_result", "add", ("异常", "不合格"), (True, True)),
        )
    )

    assert len(report.slot_operations) == 1
    assert report.slot_operations[0].entity_type == "summary_result"
    assert report.slot_operations[0].action == "replace"
    assert report.slot_operations[0].target == "不合格"


def test_summary_result_filling_drops_invalid_indicator_operation_when_summary_matches() -> None:
    report = fill_summary_result_slots(
        _report(
            "我想查看界限值未设置的测试记录",
            _slot_operation("indicator", "replace", "界限值", False),
        )
    )

    assert [operation.entity_type for operation in report.slot_operations] == ["summary_result"]
    assert report.slot_operations[0].target == "未设置界限值"


def test_summary_result_filling_keeps_non_summary_report_unchanged() -> None:
    original = _report("我想查看RMS指标", _slot_operation("indicator", "replace", "RMS", True))

    report = fill_summary_result_slots(original)

    assert report is original


def _report(
    message: str,
    *slot_operations: RecognitionSlotOperation,
) -> RecognitionReport:
    return RecognitionReport(
        message=message,
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        slot_operations=slot_operations,
    )


def _slot_operation(
    entity_type: str,
    action: str | tuple[str, ...],
    target: str | tuple[str, ...],
    slot_valid: bool | tuple[bool, ...],
) -> RecognitionSlotOperation:
    return RecognitionSlotOperation(
        intent=f"task.nvh.selection.set_{entity_type}",
        score=0.9,
        action=action,
        entity_type=entity_type,
        target=target,
        slot_valid=slot_valid,
    )
