from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from maia.api import TurnRequest
from maia.integrations.sigma.excel_export import ExcelExportRequest
from maia.integrations.sigma.product_catalog import ProductConfig
from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary
from maia.recognition import RecognitionReport
from maia.selection.expression import AllOf, Predicate, parse_filter_expression


def test_excel_export_clarifies_sensors_first() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("46704", product_type="dm0608", config_version="5", system_no="7s-SNF1001"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.excel_export"],
                    operations=[{"action": "replace", "entity_type": "product_type", "target": "dm0608"}],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        sensor_list_client=_SensorLister(("Torque", "sensor01", "sensor02")),
        excel_export_client=_ExcelExporter(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "export dm0608 Excel all data")))

    assert response.plan.kind == "clarify"
    assert response.plan.reason == "missing_slots"
    assert response.plan.missing_slots == ["sensor_id_list"]
    assert response.plan.prompts[0].input_type == "multi_select"
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == [
        "Torque",
        "sensor01",
        "sensor02",
    ]


def test_excel_export_clarifies_data_types_after_sensor_reply() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("46704", product_type="dm0608", config_version="5", system_no="7s-SNF1001"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.excel_export"],
                    operations=[{"action": "replace", "entity_type": "product_type", "target": "dm0608"}],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        sensor_list_client=_SensorLister(("Torque", "sensor01", "sensor02")),
        excel_export_client=_ExcelExporter(),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "export dm0608 Excel")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "sensor_id_list", "value": ["sensor01", "Torque"]}],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert first.plan.missing_slots == ["sensor_id_list"]
    assert second.plan.kind == "clarify"
    assert second.plan.reason == "missing_slots"
    assert second.plan.missing_slots == ["excel_data_types"]
    assert second.plan.prompts[0].input_type == "multi_select"
    assert [candidate.value for candidate in second.plan.prompts[0].candidates] == [
        "\u4e00\u7ef4\u6570\u636e",
        "\u4e8c\u7ef4\u6570\u636e",
        "\u7ed3\u679c\u6570\u636e",
    ]


def test_excel_export_confirms_and_posts_payload_after_prompt_replies() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("46704", product_type="dm0608", config_version="5", system_no="7s-SNF1001"),
        _record("46703", product_type="dm0608", config_version="5", system_no="7s-SNF1001"),
    )
    export_response = {
        "msg": None,
        "code": 200,
        "data": [
            "http://SN1033:8081/api/storage/baseFilePath/exports/20260624101641-OneData.xlsx",
            "http://SN1033:8081/api/storage/baseFilePath/exports/20260624101641-TwoData.xlsx",
            "http://SN1033:8081/api/storage/baseFilePath/exports/20260624101641-resultData.xlsx",
        ],
    }
    exporter = _ExcelExporter(response=export_response)
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.excel_export"],
                    operations=[{"action": "replace", "entity_type": "product_type", "target": "dm0608"}],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        sensor_list_client=_SensorLister(("Torque", "sensor01", "sensor02")),
        excel_export_client=exporter,
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "export dm0608 Excel")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "sensor_id_list", "value": ["sensor02", "sensor01", "Torque"]}],
            )
        )
    )
    third = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[
                    {
                        "prompt_id": "excel_data_types",
                        "value": ["\u4e00\u7ef4\u6570\u636e", "\u4e8c\u7ef4\u6570\u636e", "\u7ed3\u679c\u6570\u636e"],
                    }
                ],
            )
        )
    )
    fourth = asyncio.run(handler.handle_turn(_request("s1", "\u786e\u8ba4")))

    assert first.plan.kind == "clarify"
    assert second.plan.kind == "clarify"
    assert third.plan.kind == "confirm"
    assert third.plan.reason == "medium_risk_operation"
    assert fourth.plan.kind == "task"
    assert fourth.plan.status == "submitted"
    assert fourth.plan.data == export_response
    assert exporter.requests[0].to_body() == {
        "type": "dm0608_5",
        "systemNo": "7s-SNF1001",
        "idList": [46704, 46703],
        "sensorIdList": ["sensor02", "sensor01", "Torque"],
        "oneData": 1,
        "twoData": 1,
        "resultData": 1,
    }


def test_excel_export_parses_all_sensors_and_all_data_from_message() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("46704", product_type="dm0608", config_version="5", system_no="7s-SNF1001"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.excel_export"],
                    operations=[{"action": "replace", "entity_type": "product_type", "target": "dm0608"}],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        sensor_list_client=_SensorLister(("Torque", "sensor01", "sensor02")),
        excel_export_client=_ExcelExporter(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "export all sensors all data Excel")))

    assert response.plan.kind == "confirm"
    assert response.plan.payload["params"]["sensorIdList"] == ("Torque", "sensor01", "sensor02")
    assert response.plan.payload["params"]["oneData"] == 1
    assert response.plan.payload["params"]["twoData"] == 1
    assert response.plan.payload["params"]["resultData"] == 1


def test_excel_export_parses_named_sensor_and_one_dimensional_data_from_message() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("46704", product_type="dm0608", config_version="5", system_no="7s-SNF1001"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.excel_export"],
                    operations=[{"action": "replace", "entity_type": "product_type", "target": "dm0608"}],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        sensor_list_client=_SensorLister(("Mic1", "Mic2")),
        excel_export_client=_ExcelExporter(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "帮我导出Mic1的一维数据到Excel中")))

    assert response.plan.kind == "confirm"
    assert response.plan.payload["params"]["sensorIdList"] == ("Mic1",)
    assert response.plan.payload["params"]["oneData"] == 1
    assert response.plan.payload["params"]["twoData"] == 0
    assert response.plan.payload["params"]["resultData"] == 0


def test_excel_export_clarifies_single_scope_for_multiple_product_config_systems() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("46704", product_type="dm0608", config_version="5", system_no="SYS-1"),
        _record("46703", product_type="dm0608", config_version="6", system_no="SYS-2"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.excel_export"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "dm0608"},
                        {
                            "action": "replace",
                            "entity_type": "config_version",
                            "target": ("5", "6"),
                            "slot_valid": (True, True),
                        },
                        {
                            "action": "replace",
                            "entity_type": "type_system",
                            "target": ("SYS-1", "SYS-2"),
                            "slot_valid": (True, True),
                        },
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        sensor_list_client=_SensorLister(("Torque",)),
        excel_export_client=_ExcelExporter(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "export dm0608 Excel all sensors all data")))

    assert response.plan.kind == "clarify"
    assert response.plan.reason == "ambiguous_slots"
    assert response.plan.missing_slots == ["excel_scope"]
    assert response.plan.prompts[0].input_type == "single_select"
    assert [candidate.label for candidate in response.plan.prompts[0].candidates] == [
        "dm0608 / 5 / SYS-1",
        "dm0608 / 6 / SYS-2",
    ]


def test_excel_export_resolves_scope_from_message_before_prompting() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("46704", product_type="dm0608", config_version="5", system_no="SYS-1"),
        _record("46703", product_type="dm0608", config_version="6", system_no="SYS-2"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.excel_export"],
                    operations=[{"action": "replace", "entity_type": "product_type", "target": "dm0608"}],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        sensor_list_client=_SensorLister(("Mic1",)),
        excel_export_client=_ExcelExporter(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "export SYS-2 all sensors all data Excel")))

    assert response.plan.kind == "confirm"
    assert response.plan.payload["params"]["systemNo"] == "SYS-2"


def test_excel_export_blocks_non_numeric_record_id() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("bad-id", product_type="dm0608", config_version="5", system_no="7s-SNF1001"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.excel_export"],
                    operations=[{"action": "replace", "entity_type": "product_type", "target": "dm0608"}],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        sensor_list_client=_SensorLister(("Torque",)),
        excel_export_client=_ExcelExporter(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "export all sensors all data Excel")))

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

    async def list_records(
        self,
        expression,
        *,
        workspace_context,
        page: int | None = None,
        rows: int | None = None,
    ) -> TestRecordPage:
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


class _SensorLister:
    def __init__(self, sensors: tuple[str, ...]) -> None:
        self.sensors = sensors
        self.calls: list[tuple[str, str]] = []

    async def list_sensors(self, *, type_: str, system_no: str, workspace_context) -> tuple[str, ...]:
        del workspace_context
        self.calls.append((type_, system_no))
        return self.sensors


class _ExcelExporter:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.requests: list[ExcelExportRequest] = []
        self.response = response or {"code": 200, "data": True}

    async def export(self, request: ExcelExportRequest, *, workspace_context):
        del workspace_context
        self.requests.append(request)
        return self.response


def _report(
    *,
    actions: list[str],
    operations: list[dict[str, object]],
) -> RecognitionReport:
    return RecognitionReport(
        message="export",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        action_intents=[{"name": name, "score": 0.95} for name in actions],
        slot_operations=[
            {
                "intent": "task.nvh.record_search",
                "score": 0.93,
                "slot_valid": True,
                **operation,
            }
            for operation in operations
        ],
    )


def _request(
    session_id: str,
    message: str,
    prompt_replies: list[dict[str, object]] | None = None,
) -> TurnRequest:
    return TurnRequest(
        session_id=session_id,
        message=message,
        prompt_replies=[] if prompt_replies is None else prompt_replies,
    )


def _record(
    record_id: str,
    *,
    product_type: str,
    config_version: str,
    system_no: str,
) -> TestRecordSummary:
    return TestRecordSummary(
        record_id=record_id,
        tested_at=datetime(2026, 6, 11, 9, 30, tzinfo=UTC),
        product_type=product_type,
        config_version=config_version,
        system_no=system_no,
        serial_number=f"SN-{record_id}",
        summary_result="PASS",
        available_artifacts=("result_data",),
    )


def _configs_from_records(records: tuple[TestRecordSummary, ...]) -> tuple[ProductConfig, ...]:
    configs: list[ProductConfig] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        key = (record.product_type or "", record.config_version or "", record.system_no or "")
        if "" in key or key in seen:
            continue
        seen.add(key)
        configs.append(
            ProductConfig(
                product_type=key[0],
                config_version=key[1],
                type_system=key[2],
                update_time=record.tested_at,
            )
        )
    return tuple(configs)


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
