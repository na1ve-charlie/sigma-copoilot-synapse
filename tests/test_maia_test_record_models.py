from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary


WORKSPACE_CONTEXT_PATH = Path("configs/maia/sigma/offline_1152.workspace_context.json")


def test_test_record_summary_accepts_canonical_record_fields() -> None:
    workspace_context = json.loads(WORKSPACE_CONTEXT_PATH.read_text(encoding="utf-8"))
    product = workspace_context["products"][0]

    summary = TestRecordSummary(
        record_id="rec-001",
        tested_at="2026-06-11T09:30:00Z",
        product_type=product["product_type"],
        config_version=product["product_version"],
        system_no=product["system_no"],
        serial_number="SNF1001",
        summary_result="FAIL",
        manual_tags=["异响", "异响", "复测"],
        archive_status="active",
        available_artifacts=["raw_data", "report", "raw_data"],
        repeat_serial=True,
    )

    assert summary.tested_at == datetime(2026, 6, 11, 9, 30, tzinfo=UTC)
    assert summary.manual_tags == ("异响", "复测")
    assert summary.available_artifacts == ("raw_data", "report")
    assert summary.model_dump(mode="json")["record_id"] == "rec-001"


def test_test_record_summary_rejects_blank_text_and_unknown_artifacts() -> None:
    with pytest.raises(ValidationError, match="record_id"):
        _summary(record_id="  ")

    with pytest.raises(ValidationError, match="summary_result"):
        _summary(summary_result="  ")

    with pytest.raises(ValidationError, match="manual_tags"):
        _summary(manual_tags=("异响", " "))

    with pytest.raises(ValidationError, match="available_artifacts"):
        _summary(available_artifacts=("raw_data", "unknown"))


def test_test_record_page_preserves_order_and_exposes_record_ids() -> None:
    older = _summary(record_id="rec-001")
    newer = _summary(record_id="rec-002", summary_result="PASS")

    page = TestRecordPage(total=5, records=[newer, older])

    assert page.total == 5
    assert page.returned_count == 2
    assert page.record_ids == ("rec-002", "rec-001")
    assert page.records == (newer, older)


def test_test_record_page_rejects_invalid_totals_and_duplicate_ids() -> None:
    with pytest.raises(ValidationError, match="total"):
        TestRecordPage(total=-1, records=())

    with pytest.raises(ValidationError, match="total"):
        TestRecordPage(total=1, records=[_summary("rec-001"), _summary("rec-002")])

    with pytest.raises(ValidationError, match="duplicate"):
        TestRecordPage(total=2, records=[_summary("rec-001"), _summary("rec-001")])


def _summary(
    record_id: str = "rec-001",
    *,
    tested_at: object = datetime(2026, 6, 11, 9, 30, tzinfo=UTC),
    product_type: str = "hzzxkj-0527",
    config_version: str = "4",
    system_no: str = "7s-SNF1001",
    serial_number: str = "SNF1001",
    summary_result: str = "FAIL",
    manual_tags: tuple[str, ...] = ("异响",),
    archive_status: str = "active",
    available_artifacts: tuple[str, ...] = ("raw_data", "result_data"),
    repeat_serial: bool | None = False,
) -> TestRecordSummary:
    return TestRecordSummary(
        record_id=record_id,
        tested_at=tested_at,
        product_type=product_type,
        config_version=config_version,
        system_no=system_no,
        serial_number=serial_number,
        summary_result=summary_result,
        manual_tags=manual_tags,
        archive_status=archive_status,
        available_artifacts=available_artifacts,
        repeat_serial=repeat_serial,
    )
