from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from maia.api import TurnRequest
from maia.integrations.sigma.product_catalog import ProductConfig
from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary
from maia.integrations.sigma.test_record_management import TestRecordManagementRequest
from maia.recognition import RecognitionReport
from maia.selection.expression import AllOf, Predicate, parse_filter_expression


def test_test_record_management_backup_clarifies_slots_then_confirms_and_submits() -> None:
    from maia.runtime import create_maia_runtime

    records = (_record("46704", product_type="A"), _record("46703", product_type="A"))
    manager = _Manager()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [_report(actions=["task.nvh.data_backup"], operations=[{"action": "replace", "entity_type": "product_type", "target": "A"}])]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        test_record_management_client=manager,
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "备份 A 数据")))
    second = asyncio.run(
        handler.handle_turn(
            _request("s1", "", prompt_replies=[{"prompt_id": "test_record_data_types", "value": ["彩图", "结果数据"]}])
        )
    )
    third = asyncio.run(
        handler.handle_turn(_request("s1", "", prompt_replies=[{"prompt_id": "test_record_file_name", "value": "backup-001"}]))
    )
    fourth = asyncio.run(handler.handle_turn(_request("s1", "确认")))

    assert first.plan.kind == "clarify"
    assert first.plan.missing_slots == ["test_record_data_types"]
    assert first.plan.prompts[0].input_type == "multi_select"
    assert second.plan.kind == "clarify"
    assert second.plan.missing_slots == ["test_record_file_name"]
    assert second.plan.prompts[0].input_type == "text"
    assert second.plan.prompts[0].candidates == []
    assert third.plan.kind == "confirm"
    assert third.plan.reason == "medium_risk_operation"
    assert fourth.plan.kind == "task"
    assert fourth.plan.status == "submitted"
    assert manager.requests[0].to_body() == {
        "resultIdList": [46704, 46703],
        "colorMap": True,
        "originData": False,
        "resultData": True,
        "dataExportType": 2,
        "filePath": "D:/数据备份/",
        "fileName": "backup-001",
    }


def test_test_record_management_delete_confirms_high_risk_without_file_name() -> None:
    from maia.runtime import create_maia_runtime

    records = (_record("46704", product_type="A"),)
    manager = _Manager()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [_report(actions=["task.nvh.data_delete"], operations=[{"action": "replace", "entity_type": "product_type", "target": "A"}])]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        test_record_management_client=manager,
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "删除 A 的原始数据")))
    second = asyncio.run(handler.handle_turn(_request("s1", "确认")))

    assert first.plan.kind == "confirm"
    assert first.plan.reason == "high_risk_operation"
    assert first.plan.payload["params"]["dataExportType"] == 1
    assert "fileName" not in first.plan.payload["params"]
    assert second.plan.kind == "task"
    assert manager.requests[0].to_body() == {
        "resultIdList": [46704],
        "colorMap": False,
        "originData": True,
        "resultData": False,
        "dataExportType": 1,
        "filePath": "D:/数据备份/",
    }


def test_test_record_management_delete_and_backup_normalizes_to_combined_operation() -> None:
    from maia.runtime import create_maia_runtime

    records = (_record("46704", product_type="A"),)
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.data_delete", "task.nvh.data_backup"],
                    operations=[{"action": "replace", "entity_type": "product_type", "target": "A"}],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        test_record_management_client=_Manager(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "删除并备份 A 的全部数据 文件名为backup-001")))

    assert response.plan.kind == "confirm"
    assert response.plan.reason == "high_risk_operation"
    assert response.plan.payload["params"]["dataExportType"] == 3
    assert response.plan.payload["params"]["fileName"] == "backup-001"


def test_test_record_management_parses_artifacts_adjacent_to_combined_action() -> None:
    from maia.runtime import create_maia_runtime

    records = (_record("46704", product_type="A"),)
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.data_backup", "task.nvh.data_delete"],
                    operations=[{"action": "replace", "entity_type": "product_type", "target": "A"}],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        test_record_management_client=_Manager(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(
        handler.handle_turn(
            _request("s1", "帮我备份并删除彩图、原始数据 文件名为backup-001")
        )
    )

    assert response.plan.kind == "confirm"
    assert response.plan.payload["params"]["colorMap"] is True
    assert response.plan.payload["params"]["originData"] is True
    assert response.plan.payload["params"]["resultData"] is False
    assert response.plan.payload["params"]["dataExportType"] == 3


def test_test_record_management_clarifies_invalid_file_name() -> None:
    from maia.runtime import create_maia_runtime

    records = (_record("46704", product_type="A"),)
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [_report(actions=["task.nvh.data_backup"], operations=[{"action": "replace", "entity_type": "product_type", "target": "A"}])]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        test_record_management_client=_Manager(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "备份 A 的原始数据 文件名为bad:name")))

    assert response.plan.kind == "clarify"
    assert response.plan.reason == "invalid_slots"
    assert response.plan.invalid_slots == ["test_record_file_name"]


def test_test_record_management_blocks_non_numeric_record_id() -> None:
    from maia.runtime import create_maia_runtime

    records = (_record("bad-id", product_type="A"),)
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [_report(actions=["task.nvh.data_delete"], operations=[{"action": "replace", "entity_type": "product_type", "target": "A"}])]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        test_record_management_client=_Manager(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "删除 A 的原始数据")))

    assert response.plan.kind == "task"
    assert response.plan.status == "blocked"
    assert response.plan.reason == "invalid_record_id"


class _SequenceRecognizer:
    def __init__(self, reports: list[RecognitionReport]) -> None:
        self._reports = list(reports)

    async def recognize(self, message: str, **kwargs) -> RecognitionReport:
        del message, kwargs
        if not self._reports:
            raise AssertionError("recognizer should not be called")
        return self._reports.pop(0)


class _RecordClient:
    def __init__(self, records: tuple[TestRecordSummary, ...]) -> None:
        self._records = records

    async def list_records(self, expression, *, workspace_context, page: int | None = None, rows: int | None = None) -> TestRecordPage:
        del workspace_context
        filtered = tuple(record for record in self._records if _matches(record, expression))
        row_count = rows or len(filtered) or 1
        page_number = page or 1
        start = (page_number - 1) * row_count
        end = start + row_count
        return TestRecordPage(total=len(filtered), records=filtered[start:end])


class _ProductCatalog:
    def __init__(self, configs: tuple[ProductConfig, ...]) -> None:
        self._configs = configs

    async def list_configs(self, *, lang: str = "zh") -> tuple[ProductConfig, ...]:
        del lang
        return self._configs


class _Manager:
    def __init__(self) -> None:
        self.requests: list[TestRecordManagementRequest] = []

    async def submit(self, request: TestRecordManagementRequest, *, workspace_context):
        del workspace_context
        self.requests.append(request)
        return {"code": 200, "data": True}


def _report(*, actions: list[str], operations: list[dict[str, object]]) -> RecognitionReport:
    return RecognitionReport(
        message="manage records",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        action_intents=[{"name": name, "score": 0.95} for name in actions],
        slot_operations=[{"intent": "task.nvh.record_search", "score": 0.93, "slot_valid": True, **operation} for operation in operations],
    )


def _request(session_id: str, message: str, prompt_replies: list[dict[str, object]] | None = None) -> TurnRequest:
    return TurnRequest(session_id=session_id, message=message, prompt_replies=[] if prompt_replies is None else prompt_replies)


def _record(record_id: str, *, product_type: str) -> TestRecordSummary:
    return TestRecordSummary(
        record_id=record_id,
        tested_at=datetime(2026, 6, 11, 9, 30, tzinfo=UTC),
        product_type=product_type,
        config_version="1",
        system_no="SYS-1",
        serial_number=f"SN-{record_id}",
        summary_result="PASS",
        available_artifacts=("raw_data", "result_data", "colormap"),
    )


def _configs_from_records(records: tuple[TestRecordSummary, ...]) -> tuple[ProductConfig, ...]:
    return tuple(
        ProductConfig(product_type=record.product_type or "", config_version=record.config_version or "", type_system=record.system_no or "", update_time=record.tested_at)
        for record in records
    )


def _matches(record: TestRecordSummary, expression) -> bool:
    if expression is None:
        return True
    parsed = parse_filter_expression(expression)
    if isinstance(parsed, Predicate):
        values = parsed.params.get("values")
        normalized = values if isinstance(values, tuple) else (values,)
        if parsed.name == "product_type_in":
            return record.product_type in normalized
        if parsed.name == "config_version_in":
            return record.config_version in normalized
        if parsed.name == "type_system_in":
            return record.system_no in normalized
        if parsed.name == "all_records":
            return True
    if isinstance(parsed, AllOf):
        return all(_matches(record, child) for child in parsed.expressions)
    raise AssertionError(f"unsupported expression: {parsed}")
