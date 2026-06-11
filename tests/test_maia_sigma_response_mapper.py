from __future__ import annotations

from maia.integrations.sigma.records import TestRecordPage
from maia.integrations.sigma.response_mapper import LegacyRecordResponseMapper

import pytest


def test_response_mapper_projects_legacy_success_envelope_into_record_page() -> None:
    mapper = LegacyRecordResponseMapper()

    page = mapper.map(
        {
            "code": 0,
            "msg": "ok",
            "data": {
                "total": 2,
                "list": [
                    {
                        "recordId": "rec-001",
                        "testedAt": "2026-05-03T10:11:12Z",
                        "productType": "dm0518",
                        "configVersion": "A12",
                        "systemNo": "SYS-01",
                        "serialNumber": "SN1001",
                        "summaryResult": "FAIL",
                        "manualTagList": ["异响", "异响"],
                        "archiveStatus": "archived",
                        "repeatSerial": True,
                        "rawDataAvailable": True,
                        "resultDataAvailable": False,
                        "reportAvailable": True,
                        "audioAvailable": True,
                    }
                ],
            },
        }
    )

    assert isinstance(page, TestRecordPage)
    assert page.total == 2
    assert page.returned_count == 1
    assert page.record_ids == ("rec-001",)
    assert page.records[0].tested_at.isoformat() == "2026-05-03T10:11:12+00:00"
    assert page.records[0].manual_tags == ("异响",)
    assert page.records[0].available_artifacts == ("raw_data", "report", "audio")


def test_response_mapper_accepts_alias_fields_and_explicit_artifact_lists() -> None:
    mapper = LegacyRecordResponseMapper()

    page = mapper.map(
        {
            "code": "200",
            "msg": "ok",
            "data": {
                "total": "1",
                "list": [
                    {
                        "reportId": "rec-002",
                        "testTime": "2026-05-04T08:00:00+08:00",
                        "productType": "dm0518",
                        "configVersion": "A13",
                        "systemNo": "SYS-02",
                        "serialNumber": "SN1002",
                        "summaryResult": "PASS",
                        "manualTags": ["复测", "复测"],
                        "archiveStatus": "active",
                        "repeatSerial": "0",
                        "availableArtifacts": ["resultData", "colormap", "resultData"],
                    }
                ],
            },
        }
    )

    record = page.records[0]
    assert record.record_id == "rec-002"
    assert record.repeat_serial is False
    assert record.manual_tags == ("复测",)
    assert record.available_artifacts == ("result_data", "colormap")


def test_response_mapper_surfaces_legacy_backend_failures() -> None:
    mapper = LegacyRecordResponseMapper()

    with pytest.raises(ValueError, match="downstream failed"):
        mapper.map(
            {
                "code": 500,
                "msg": "downstream failed",
                "data": {"total": 0, "list": []},
            }
        )


def test_response_mapper_rejects_rows_without_record_identity() -> None:
    mapper = LegacyRecordResponseMapper()

    with pytest.raises(ValueError, match="record id"):
        mapper.map(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "total": 1,
                    "list": [
                        {
                            "productType": "dm0518",
                            "summaryResult": "FAIL",
                        }
                    ],
                },
            }
        )
