from __future__ import annotations

import pytest

from maia.integrations.sigma.records import TestRecordSummary
from maia.tasks.test_record_artifacts import TestRecordArtifactParser
from maia.tasks.test_record_management import management_request


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("帮我删除这批数据的彩图", ("color_map",)),
        ("帮我删除这批数据的结果数据", ("result_data",)),
        ("帮我删除这批数据的原始数据", ("origin_data",)),
        ("帮我删除这批数据的彩图、结果数据", ("color_map", "result_data")),
        ("帮我删除这批数据的彩图、原始数据", ("color_map", "origin_data")),
        ("帮我删除这批数据的原始数据、结果数据", ("origin_data", "result_data")),
        (
            "帮我删除这批数据的原始数据、彩图、结果数据",
            ("origin_data", "color_map", "result_data"),
        ),
        ("帮我备份并删除彩图、原始数据", ("color_map", "origin_data")),
    ],
)
def test_record_artifact_parser_recognizes_single_and_combined_artifacts(
    message: str,
    expected: tuple[str, ...],
) -> None:
    assert TestRecordArtifactParser().parse_message(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "我想导出时域波形",
        "我想导出工况数据",
        "我想导出时域波形和工况数据",
        "帮我备份时域波形和工况数据",
        "帮我删除这批数据的时域波形和工况数据",
    ],
)
def test_record_artifact_parser_maps_origin_data_business_aliases(message: str) -> None:
    assert TestRecordArtifactParser().parse_message(message) == ("origin_data",)


def test_backup_delete_request_sets_color_map_and_origin_data() -> None:
    artifacts = TestRecordArtifactParser().parse_message("帮我备份并删除彩图、原始数据")

    request = management_request(
        (TestRecordSummary(record_id="46704"),),
        "backup_delete",
        artifacts,
        "backup-001",
    )

    assert request.to_body() == {
        "resultIdList": [46704],
        "colorMap": True,
        "originData": True,
        "resultData": False,
        "dataExportType": 3,
        "filePath": "D:/数据备份/",
        "fileName": "backup-001",
    }


def test_record_artifact_parser_accepts_prompt_and_backend_values() -> None:
    selected = TestRecordArtifactParser().parse_value(
        ["彩图", "originData", "result data", "时域波形"]
    )

    assert selected == ("color_map", "origin_data", "result_data")
