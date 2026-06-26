from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from maia.api import TurnRequest
from maia.integrations.sigma.origin_export import OriginExportRequest
from maia.integrations.sigma.product_catalog import ProductConfig
from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary
from maia.recognition import RecognitionReport
from maia.selection import InMemorySelectionSetRepository
from maia.selection.expression import AllOf, Predicate, parse_filter_expression


def test_origin_data_export_clarifies_format_then_confirms_and_exports() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("29181", product_type="A", system_no="SYS-1"),
        _record("29182", product_type="A", system_no="SYS-1"),
    )
    exporter = _OriginExporter()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.origin_data_export"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        origin_export_client=exporter,
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "export A origin data")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                prompt_replies=[{"prompt_id": "origin_data_format", "value": "TDMS"}],
            )
        )
    )
    third = asyncio.run(handler.handle_turn(_request("s1", "\u786e\u8ba4")))

    assert first.plan.kind == "clarify"
    assert first.plan.reason == "missing_slots"
    assert first.plan.missing_slots == ["origin_data_format"]
    assert first.plan.prompts[0].input_type == "single_select"
    assert [candidate.value for candidate in first.plan.prompts[0].candidates] == ["H5", "TDMS"]
    assert second.plan.kind == "confirm"
    assert second.plan.reason == "medium_risk_operation"
    assert third.plan.kind == "task"
    assert third.plan.status == "submitted"
    assert exporter.requests[0].to_body() == {
        "idList": [29181, 29182],
        "path": "D:\\exportOriginFile",
        "dataExportType": 1,
        "systemNo": "SYS-1",
    }


def test_origin_data_export_prompt_cancel_clears_pending_task() -> None:
    from maia.runtime import ConversationStateRepository, create_maia_runtime

    records = (_record("29181", product_type="A", system_no="SYS-1"),)
    state_repository = ConversationStateRepository()
    exporter = _OriginExporter()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.origin_data_export"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        origin_export_client=exporter,
        state_repository=state_repository,
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "export A origin data")))
    cancelled = asyncio.run(handler.handle_turn(_request("s1", "取消")))
    state = state_repository.load("s1")

    assert first.plan.kind == "clarify"
    assert first.plan.pending_task == "task.nvh.origin_data_export"
    assert cancelled.plan.kind == "reply"
    assert cancelled.plan.message == "Pending task cancelled."
    assert state.pending_selection_draft is None
    assert state.pending_task is None
    assert state.pending_confirmation is None
    assert exporter.requests == []


def test_origin_data_export_clarifies_system_no_after_format_reply() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("29181", product_type="A", system_no="SYS-1"),
        _record("29182", product_type="A", system_no="SYS-2"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.origin_data_export"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        origin_export_client=_OriginExporter(),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "export A origin data TDMS")))

    assert first.plan.kind == "clarify"
    assert first.plan.reason == "ambiguous_slots"
    assert first.plan.missing_slots == ["system_no"]
    assert first.plan.prompts[0].id == "system_no"
    assert first.plan.prompts[0].input_type == "single_select"
    assert [candidate.value for candidate in first.plan.prompts[0].candidates] == [
        "SYS-1",
        "SYS-2",
    ]


def test_origin_data_export_resolves_h5_format_from_message() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("29181", product_type="A", system_no="SYS-1"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.origin_data_export"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        origin_export_client=_OriginExporter(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "导出到H5原始数据")))

    assert response.plan.kind == "confirm"
    assert response.plan.payload["params"]["origin_data_format"] == "H5"
    assert response.plan.payload["params"]["dataExportType"] == 0


def test_origin_data_export_resolves_system_no_from_message_before_prompting() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("29181", product_type="A", system_no="SYS-1"),
        _record("29182", product_type="A", system_no="SYS-2"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.origin_data_export"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        origin_export_client=_OriginExporter(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "export SYS-2 origin data TDMS")))

    assert response.plan.kind == "confirm"
    assert response.plan.payload["params"]["system_no"] == "SYS-2"


def test_origin_data_export_uses_primary_action_when_embedding_returns_excel_candidate() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("29181", product_type="A", system_no="SYS-1"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.origin_data_export", "task.nvh.excel_export"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        origin_export_client=_OriginExporter(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "export A origin data TDMS")))

    assert response.plan.kind == "confirm"
    assert response.plan.payload["operation"] == "task.nvh.origin_data_export"


def test_origin_data_export_without_filters_reuses_active_selection() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("29181", product_type="A", system_no="SYS-1"),
        _record("29182", product_type="B", system_no="SYS-2"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                ),
                _report(actions=["task.nvh.origin_data_export"], operations=[]),
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        origin_export_client=_OriginExporter(),
        source_version="sigma-fixture-v1",
    )

    selected = asyncio.run(handler.handle_turn(_request("s1", "find A records")))
    exported = asyncio.run(handler.handle_turn(_request("s1", "export current origin data TDMS")))

    assert exported.plan.kind == "confirm"
    assert exported.plan.dataset.selection_set_id == selected.plan.dataset.selection_set_id
    assert exported.plan.dataset.record_ids == ["29181"]


def test_origin_data_export_with_new_product_creates_independent_selection() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("29181", product_type="A", system_no="SYS-1"),
        _record("29182", product_type="B", system_no="SYS-2"),
    )
    selection_repository = InMemorySelectionSetRepository()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                ),
                _report(
                    actions=["task.nvh.origin_data_export"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "B"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(records),
        selection_repository=selection_repository,
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        origin_export_client=_OriginExporter(),
        source_version="sigma-fixture-v1",
    )

    selected = asyncio.run(handler.handle_turn(_request("s1", "find A records")))
    exported = asyncio.run(handler.handle_turn(_request("s1", "export B origin data TDMS")))
    export_selection = selection_repository.get(exported.plan.dataset.selection_set_id)

    assert exported.plan.kind == "confirm"
    assert exported.plan.dataset.selection_set_id != selected.plan.dataset.selection_set_id
    assert exported.plan.dataset.record_ids == ["29182"]
    assert export_selection is not None
    assert export_selection.derived_operation == "create"
    assert export_selection.parent_selection_set_id is None


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


class _OriginExporter:
    def __init__(self) -> None:
        self.requests: list[OriginExportRequest] = []

    async def export(self, request: OriginExportRequest, *, workspace_context):
        del workspace_context
        self.requests.append(request)
        return {"code": 200, "data": True}


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


def _record(record_id: str, *, product_type: str, system_no: str) -> TestRecordSummary:
    return TestRecordSummary(
        record_id=record_id,
        tested_at=datetime(2026, 6, 11, 9, 30, tzinfo=UTC),
        product_type=product_type,
        config_version="1",
        system_no=system_no,
        serial_number=f"SN-{record_id}",
        summary_result="PASS",
        available_artifacts=("raw_data",),
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
