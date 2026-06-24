from __future__ import annotations

import pytest

from maia.tasks.excel_export_data_types import ExcelExportDataTypeParser
from maia.tasks.excel_export_policy import data_flags


@pytest.mark.parametrize(
    "message",
    [
        "我想导出一维数据、二维数据以及结果数据到Excel中",
        "我想导出Mic2的一维指标、二维指标以及结果数据到Excel中",
        "我想导出Mic1的一维数据、二维数据以及结果数据到Excel中",
        "我想导出一维指标、二维数据以及结果数据到Excel中",
    ],
)
def test_excel_data_type_parser_recognizes_data_and_indicator_terms(message: str) -> None:
    selected = ExcelExportDataTypeParser().parse_message(message)

    assert selected == ("one_data", "two_data", "result_data")
    assert data_flags(selected) == {
        "oneData": 1,
        "twoData": 1,
        "resultData": 1,
    }


def test_excel_data_type_parser_deduplicates_synonyms() -> None:
    selected = ExcelExportDataTypeParser().parse_message(
        "导出一维数据和一维指标、二维数据和二维指标到Excel"
    )

    assert selected == ("one_data", "two_data")


def test_excel_data_type_parser_accepts_prompt_and_backend_values() -> None:
    selected = ExcelExportDataTypeParser().parse_value(
        ["一维指标", "twoData", "结果数据", "一维数据"]
    )

    assert selected == ("one_data", "two_data", "result_data")
