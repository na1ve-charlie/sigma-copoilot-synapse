from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from maia.api import WorkspaceContext
from maia.integrations.sigma import request_mapper as request_mapper_module
from maia.integrations.sigma.request_mapper import (
    LegacyRecordRequestMapper,
    LegacyRecordRequestParams,
)


WORKSPACE_CONTEXT_PATH = Path("configs/maia/testdata/sigma/offline_1152.workspace_context.json")


def test_request_mapper_projects_dataset_scope_page_defaults_and_supported_filters() -> None:
    mapper = LegacyRecordRequestMapper()

    request = mapper.map(
        {
            "kind": "all_of",
            "expressions": [
                {"kind": "predicate", "name": "product_type_in", "params": {"values": ["dm0518"]}},
                {
                    "kind": "predicate",
                    "name": "tested_at_between",
                    "params": {"start": "2026-05-01", "end": "2026-05-31"},
                },
                {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["不合格"]}},
                {"kind": "predicate", "name": "sensor_in", "params": {"values": ["Vib1"]}},
                {"kind": "predicate", "name": "test_segment_in", "params": {"values": ["TS-01"]}},
                {"kind": "predicate", "name": "indicator_in", "params": {"values": ["RMS"]}},
            ],
        },
        workspace_context=_workspace_context(),
    )

    assert isinstance(request, LegacyRecordRequestParams)
    assert request.to_http_params() == {
        "lang": "zh",
        "page": 1,
        "rows": 500,
        "archive": "false",
        "keepLast": "false",
        "onlyRepeatSerial": "false",
        "type": "dm0518",
        "startTime": "2026-05-01",
        "endTime": "2026-05-31",
        "sumList": "不合格",
        "sensorIdList": "Vib1",
        "testNameList": "TS-01",
        "indicatorList": "RMS",
    }


def test_request_mapper_normalizes_boolean_and_text_qualifiers() -> None:
    mapper = LegacyRecordRequestMapper(default_page=2, default_rows=100)

    request = mapper.map(
        {
            "kind": "all_of",
            "expressions": [
                {"kind": "predicate", "name": "config_version_in", "params": {"values": ["A12"]}},
                {"kind": "predicate", "name": "type_system_in", "params": {"values": ["SYS-01"]}},
                {"kind": "predicate", "name": "serial_number_in", "params": {"values": ["SN1001"]}},
                {"kind": "predicate", "name": "manual_tagging_in", "params": {"values": ["不合格"]}},
                {"kind": "predicate", "name": "remark_in", "params": {"values": ["异响"]}},
                {"kind": "predicate", "name": "test_section_in", "params": {"values": ["高速段"]}},
                {"kind": "predicate", "name": "status_in", "params": {"values": ["无效"]}},
                {"kind": "predicate", "name": "archive_status_in", "params": {"values": ["archived"]}},
                {"kind": "predicate", "name": "data_kind_in", "params": {"values": ["raw"]}},
                {"kind": "predicate", "name": "artifact_availability_in", "params": {"values": ["available"]}},
                {"kind": "predicate", "name": "repeat_serial_in", "params": {"values": ["repeated"]}},
            ],
        },
        workspace_context=_workspace_context(),
    )

    assert request.to_http_params() == {
        "lang": "zh",
        "page": 2,
        "rows": 100,
        "archive": "true",
        "keepLast": "false",
        "onlyRepeatSerial": "true",
        "versionList": "A12",
        "systemNoList": "SYS-01",
        "serialNo": "SN1001",
        "manualTagging": "不合格",
        "remark": "异响",
        "status": "无效",
        "testSection": "高速段",
        "hasOriginData": "true",
    }


def test_request_mapper_does_not_infer_unconfirmed_negative_semantics() -> None:
    mapper = LegacyRecordRequestMapper()

    with pytest.raises(ValueError, match="archive_status_in"):
        mapper.map(
            {
                "kind": "predicate",
                "name": "archive_status_in",
                "params": {"values": ["active"]},
            },
            workspace_context=_workspace_context(),
        )

    with pytest.raises(ValueError, match="artifact_availability_in"):
        mapper.map(
            {
                "kind": "all_of",
                "expressions": [
                    {
                        "kind": "predicate",
                        "name": "data_kind_in",
                        "params": {"values": ["raw"]},
                    },
                    {
                        "kind": "predicate",
                        "name": "artifact_availability_in",
                        "params": {"values": ["unavailable"]},
                    },
                ],
            },
            workspace_context=_workspace_context(),
        )


@pytest.mark.parametrize("predicate_name", ["manual_tagging_in", "status_in"])
def test_request_mapper_rejects_unknown_marking_result_values(predicate_name: str) -> None:
    mapper = LegacyRecordRequestMapper()

    with pytest.raises(ValueError, match=predicate_name):
        mapper.map(
            {
                "kind": "predicate",
                "name": predicate_name,
                "params": {"values": ["人工标记"]},
            },
            workspace_context=_workspace_context(),
        )


def test_request_mapper_allows_missing_dataset_scope_and_requires_positive_pagination() -> None:
    mapper = LegacyRecordRequestMapper()

    request = mapper.map(
        {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["不合格"]}},
        workspace_context=WorkspaceContext(),
    )

    assert request.to_http_params() == {
        "lang": "zh",
        "page": 1,
        "rows": 500,
        "archive": "false",
        "keepLast": "false",
        "onlyRepeatSerial": "false",
        "sumList": "不合格",
    }

    with pytest.raises(ValueError, match="page"):
        mapper.map(
            {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["不合格"]}},
            workspace_context=_workspace_context(),
            page=0,
        )


def test_request_mapper_allows_dataset_scoped_query_without_extra_predicates() -> None:
    mapper = LegacyRecordRequestMapper(default_page=3, default_rows=10)

    request = mapper.map(
        None,
        workspace_context=_workspace_context(),
    )

    assert request.to_http_params() == {
        "lang": "zh",
        "page": 3,
        "rows": 10,
        "archive": "false",
        "keepLast": "false",
        "onlyRepeatSerial": "false",
    }


def test_request_mapper_rejects_branching_and_record_level_predicates_reserved_for_g15() -> None:
    mapper = LegacyRecordRequestMapper()

    with pytest.raises(ValueError, match="AnyOf"):
        mapper.map(
            {
                "kind": "any_of",
                "expressions": [
                    {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["不合格"]}},
                    {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["合格"]}},
                ],
            },
            workspace_context=_workspace_context(),
        )

    with pytest.raises(ValueError, match="Not"):
        mapper.map(
            {
                "kind": "not",
                "expression": {
                    "kind": "predicate",
                    "name": "test_segment_in",
                    "params": {"values": ["TS-03"]},
                },
            },
            workspace_context=_workspace_context(),
        )

    with pytest.raises(ValueError, match="indicator_failed"):
        mapper.map(
            {
                "kind": "predicate",
                "name": "indicator_failed",
                "params": {"sensor": "Vib1", "segment": "TS-01", "indicator": "RMS"},
            },
            workspace_context=_workspace_context(),
        )


def test_request_mapper_maps_relative_time_range_shortcuts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapper = LegacyRecordRequestMapper()
    monkeypatch.setattr(request_mapper_module, "_today", lambda: date(2026, 6, 11))

    week = mapper.map(
        {
            "kind": "predicate",
            "name": "time_range_in",
            "params": {"values": ["last_week"]},
        },
        workspace_context=_workspace_context(),
    )
    month = mapper.map(
        {
            "kind": "predicate",
            "name": "time_range_in",
            "params": {"values": ["last_month"]},
        },
        workspace_context=_workspace_context(),
    )

    assert week.to_http_params()["archive"] == "false"
    assert week.to_http_params()["keepLast"] == "false"
    assert week.to_http_params()["onlyRepeatSerial"] == "false"
    assert week.to_http_params()["startTime"] == "2026-06-05"
    assert week.to_http_params()["endTime"] == "2026-06-11"
    assert month.to_http_params()["startTime"] == "2026-05-13"
    assert month.to_http_params()["endTime"] == "2026-06-11"


def test_request_mapper_supports_single_sided_tested_at_between() -> None:
    mapper = LegacyRecordRequestMapper()

    request = mapper.map(
        {
            "kind": "predicate",
            "name": "tested_at_between",
            "params": {"end": "2026-06-12 00:00:00"},
        },
        workspace_context=None,
    )

    assert request.to_http_params() == {
        "lang": "zh",
        "page": 1,
        "rows": 500,
        "archive": "false",
        "keepLast": "false",
        "onlyRepeatSerial": "false",
        "endTime": "2026-06-12 00:00:00",
    }


def test_request_mapper_outputs_canonical_summary_result_lists() -> None:
    mapper = LegacyRecordRequestMapper()

    multi = mapper.map(
        {
            "kind": "predicate",
            "name": "summary_result_in",
            "params": {"values": ["次异常", "不合格"]},
        },
        workspace_context=None,
    )
    single = mapper.map(
        {
            "kind": "predicate",
            "name": "summary_result_in",
            "params": {"values": ["检测失败"]},
        },
        workspace_context=None,
    )

    assert multi.to_http_params()["sumList"] == "次异常,不合格"
    assert single.to_http_params()["sumList"] == "检测失败"

def test_request_mapper_keeps_product_type_after_scope_rebuild() -> None:
    mapper = LegacyRecordRequestMapper()

    request = mapper.map(
        {
            "kind": "all_of",
            "expressions": [
                {
                    "kind": "predicate",
                    "name": "product_type_in",
                    "params": {"values": ["测试"]},
                },
                {
                    "kind": "predicate",
                    "name": "summary_result_in",
                    "params": {"values": ["合格"]},
                },
            ],
        },
        workspace_context=None,
    )

    params = request.to_http_params()
    assert params["type"] == "测试"
    assert params["sumList"] == "合格"
    assert "versionList" not in params
    assert "systemNoList" not in params


def test_request_mapper_omits_product_params_after_all_products_scope_rebuild() -> None:
    mapper = LegacyRecordRequestMapper()

    request = mapper.map(
        {
            "kind": "predicate",
            "name": "summary_result_in",
            "params": {"values": ["合格"]},
        },
        workspace_context=None,
    )

    params = request.to_http_params()
    assert params["sumList"] == "合格"
    assert "type" not in params
    assert "versionList" not in params
    assert "systemNoList" not in params


def _workspace_context() -> WorkspaceContext:
    return WorkspaceContext.model_validate(
        json.loads(WORKSPACE_CONTEXT_PATH.read_text(encoding="utf-8"))
    )
