from __future__ import annotations

import json
from pathlib import Path

import pytest

from maia.api import WorkspaceContext
from maia.integrations.sigma.request_mapper import (
    LegacyRecordRequestMapper,
    LegacyRecordRequestParams,
)


WORKSPACE_CONTEXT_PATH = Path("configs/maia/sigma/offline_1152.workspace_context.json")


def test_request_mapper_projects_dataset_scope_page_defaults_and_supported_filters() -> None:
    mapper = LegacyRecordRequestMapper()

    request = mapper.map(
        {
            "kind": "all_of",
            "expressions": [
                {"kind": "predicate", "name": "product_type_in", "params": {"values": ["dm0518"]}},
                {"kind": "predicate", "name": "tested_at_between", "params": {"start": "2026-05-01", "end": "2026-05-31"}},
                {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}},
                {"kind": "predicate", "name": "sensor_in", "params": {"values": ["Vib1"]}},
                {"kind": "predicate", "name": "test_segment_in", "params": {"values": ["TS-01"]}},
                {"kind": "predicate", "name": "indicator_in", "params": {"values": ["RMS"]}},
            ],
        },
        workspace_context=_workspace_context(),
    )

    assert isinstance(request, LegacyRecordRequestParams)
    assert request.to_http_params() == {
        "dataGroupId": "1152",
        "lang": "zh",
        "page": 1,
        "rows": 500,
        "productTypeList": ["dm0518"],
        "testedAtStart": "2026-05-01",
        "testedAtEnd": "2026-05-31",
        "summaryResultList": ["FAIL"],
        "sensorList": ["Vib1"],
        "testNameList": ["TS-01"],
        "indicatorNameList": ["RMS"],
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
                {"kind": "predicate", "name": "manual_tag_in", "params": {"values": ["异响"]}},
                {"kind": "predicate", "name": "archive_status_in", "params": {"values": ["archived"]}},
                {"kind": "predicate", "name": "data_kind_in", "params": {"values": ["raw"]}},
                {"kind": "predicate", "name": "artifact_availability_in", "params": {"values": ["available"]}},
                {"kind": "predicate", "name": "repeat_serial_in", "params": {"values": ["repeated"]}},
            ],
        },
        workspace_context=_workspace_context(),
    )

    assert request.to_http_params() == {
        "dataGroupId": "1152",
        "lang": "zh",
        "page": 2,
        "rows": 100,
        "configVersionList": ["A12"],
        "systemNoList": ["SYS-01"],
        "serialNumberList": ["SN1001"],
        "manualTagList": ["异响"],
        "archiveStatusList": ["archived"],
        "dataKindList": ["raw"],
        "artifactAvailable": True,
        "repeatSerial": True,
    }


def test_request_mapper_requires_dataset_scope_and_positive_pagination() -> None:
    mapper = LegacyRecordRequestMapper()

    with pytest.raises(ValueError, match="dataset_id"):
        mapper.map(
            {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}},
            workspace_context=WorkspaceContext(),
        )

    with pytest.raises(ValueError, match="page"):
        mapper.map(
            {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}},
            workspace_context=_workspace_context(),
            page=0,
        )


def test_request_mapper_rejects_branching_and_record_level_predicates_reserved_for_g15() -> None:
    mapper = LegacyRecordRequestMapper()

    with pytest.raises(ValueError, match="AnyOf"):
        mapper.map(
            {
                "kind": "any_of",
                "expressions": [
                    {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}},
                    {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["PASS"]}},
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

    with pytest.raises(ValueError, match="time_range_in"):
        mapper.map(
            {
                "kind": "predicate",
                "name": "time_range_in",
                "params": {"values": ["最近一周"]},
            },
            workspace_context=_workspace_context(),
        )


def _workspace_context() -> WorkspaceContext:
    return WorkspaceContext.model_validate(
        json.loads(WORKSPACE_CONTEXT_PATH.read_text(encoding="utf-8"))
    )
