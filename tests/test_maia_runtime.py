from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from maia.api import TurnRequest, WorkspaceContext
from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary
from maia.recognition import RecognitionReport


def test_runtime_returns_reply_plan_without_workspace_context_and_materializes_dataset() -> None:
    from maia.runtime import create_maia_runtime

    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(),
        product_catalog=_ProductCatalog([_config("A", "1", "SYS-1")]),
        selection_materializer=_Materializer(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "find A records")))

    assert response.plan.kind == "reply"
    assert response.plan.message == "Found 3 records."
    assert response.plan.data["dataset_id"] == "dataset-1"
    assert response.plan.data["record_count"] == 3
    assert response.plan.data["record_ids"] == ["r-1", "r-2", "r-3"]
    assert response.plan.data["selection_set_id"].startswith("sel-")


def test_runtime_derives_follow_up_record_search_from_active_selection() -> None:
    from maia.runtime import create_maia_runtime

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
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "add", "entity_type": "summary_result", "target": "不合格"},
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(),
        product_catalog=_ProductCatalog([_config("A", "1", "SYS-1")]),
        selection_materializer=_Materializer(),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find A records")))
    second = asyncio.run(handler.handle_turn(_request("s1", "only failing")))

    assert first.plan.data["dataset_id"] == "dataset-1"
    assert second.plan.data["dataset_id"] == "dataset-2"
    assert second.plan.data["selection_set_id"] != first.plan.data["selection_set_id"]
    assert second.plan.data["record_count"] == 1
    assert second.plan.data["record_ids"] == ["r-2"]


def test_runtime_resolves_active_selection_reference_to_same_selection() -> None:
    from maia.runtime import create_maia_runtime

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
                    actions=["task.nvh.record_search"],
                    operations=[
                        {
                            "action": "replace",
                            "entity_type": "selection_reference",
                            "target": "active_selection",
                        },
                    ],
                ),
            ]
        ),
        record_client=_RecordClient(),
        product_catalog=_ProductCatalog([_config("A", "1", "SYS-1")]),
        selection_materializer=_Materializer(),
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "find A records")))
    second = asyncio.run(handler.handle_turn(_request("s1", "show those records")))

    assert second.plan.data["selection_set_id"] == first.plan.data["selection_set_id"]
    assert second.plan.data["dataset_id"] == "dataset-1"
    assert second.plan.data["record_ids"] == first.plan.data["record_ids"]


def test_runtime_treats_selection_only_report_as_record_search_request() -> None:
    from maia.runtime import create_maia_runtime

    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=[],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(),
        product_catalog=_ProductCatalog([_config("A", "1", "SYS-1")]),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "only A records")))

    assert response.plan.kind == "reply"
    assert response.plan.data["record_count"] == 3
    assert response.plan.data["record_ids"] == ["r-1", "r-2", "r-3"]


def test_runtime_clarifies_missing_product_type_with_recent_candidates() -> None:
    from maia.runtime import create_maia_runtime

    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "summary_result", "target": "不合格"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(),
        product_catalog=_ProductCatalog(
            [
                _config("B", "1", "SYS-2", day=12),
                _config("A", "1", "SYS-1", day=11),
            ]
        ),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "show failing records")))

    assert response.plan.kind == "clarify"
    assert response.plan.reason == "missing_slots"
    assert response.plan.missing_slots == ["product_type"]
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == ["B", "A"]


def test_runtime_auto_fills_unique_version_and_clarifies_system_only_on_conflict() -> None:
    from maia.runtime import create_maia_runtime

    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(),
        product_catalog=_ProductCatalog(
            [
                _config("A", "1", "SYS-1"),
                _config("A", "1", "SYS-2"),
            ]
        ),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "find A records")))

    assert response.plan.kind == "clarify"
    assert response.plan.missing_slots == ["type_system"]
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == [
        "SYS-1",
        "SYS-2",
    ]


def test_runtime_clarifies_config_version_when_product_has_multiple_versions() -> None:
    from maia.runtime import create_maia_runtime

    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(),
        product_catalog=_ProductCatalog(
            [
                _config("A", "1", "SYS-1"),
                _config("A", "2", "SYS-2"),
            ]
        ),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "find A records")))

    assert response.plan.kind == "clarify"
    assert response.plan.missing_slots == ["config_version"]
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == ["1", "2"]


def test_runtime_marks_invalid_product_combination() -> None:
    from maia.runtime import create_maia_runtime

    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.record_search"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                        {"action": "replace", "entity_type": "config_version", "target": "9"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(),
        product_catalog=_ProductCatalog([_config("A", "1", "SYS-1")]),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "find A version 9 records")))

    assert response.plan.kind == "clarify"
    assert response.plan.reason == "invalid_slots"
    assert response.plan.invalid_slots == ["config_version"]
    assert [candidate.value for candidate in response.plan.prompts[0].candidates] == ["1"]


class _SequenceRecognizer:
    def __init__(self, reports: list[RecognitionReport]) -> None:
        self._reports = iter(reports)

    async def recognize(self, message: str, *, resolver=None, include_diagnostics: bool = False) -> RecognitionReport:
        del message, resolver, include_diagnostics
        return next(self._reports)


class _RecordClient:
    def __init__(self) -> None:
        shared = _page(["r-1", "r-2", "r-3"])
        self._pages = {
            _key({"kind": "predicate", "name": "product_type_in", "params": {"values": ["A"]}}): shared,
            _key({"kind": "predicate", "name": "config_version_in", "params": {"values": ["1"]}}): shared,
            _key({"kind": "predicate", "name": "type_system_in", "params": {"values": ["SYS-1"]}}): shared,
            _key({"kind": "predicate", "name": "summary_result_in", "params": {"values": ["不合格"]}}): _page(["r-2", "r-4"]),
        }

    async def list_records(
        self,
        expression,
        *,
        workspace_context: WorkspaceContext | None,
        page: int | None = None,
        rows: int | None = None,
    ) -> TestRecordPage:
        del workspace_context, page, rows
        key = "<all>" if expression is None else _key(expression)
        if key not in self._pages:
            raise ValueError(f"unsupported query branch: {key}")
        return self._pages[key]


class _ProductCatalog:
    def __init__(self, configs: list[ProductConfig]) -> None:
        self._configs = tuple(configs)

    async def list_configs(self, *, lang: str = "zh") -> tuple[ProductConfig, ...]:
        del lang
        return self._configs


class _Materializer:
    def __init__(self) -> None:
        self._counter = 0

    async def materialize(self, selection_set, *, workspace_context) -> str:
        del selection_set, workspace_context
        self._counter += 1
        return f"dataset-{self._counter}"


def _config(
    product_type: str,
    config_version: str,
    type_system: str,
    *,
    day: int = 11,
) -> ProductConfig:
    from maia.integrations.sigma.product_catalog import ProductConfig

    return ProductConfig(
        product_type=product_type,
        config_version=config_version,
        type_system=type_system,
        update_time=datetime(2026, 6, day, 9, 30),
    )


def _report(
    *,
    actions: list[str],
    operations: list[dict[str, object]],
) -> RecognitionReport:
    return RecognitionReport(
        message="search",
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
    workspace_context: WorkspaceContext | None = None,
) -> TurnRequest:
    return TurnRequest(
        session_id=session_id,
        message=message,
        workspace_context=workspace_context,
    )


def _page(record_ids: list[str]) -> TestRecordPage:
    return TestRecordPage(total=len(record_ids), records=tuple(_record(record_id) for record_id in record_ids))


def _record(record_id: str) -> TestRecordSummary:
    day = int(record_id.split("-")[1])
    return TestRecordSummary(
        record_id=record_id,
        tested_at=datetime(2026, 6, day, 9, 30, tzinfo=UTC),
        product_type="A" if day < 4 else "B",
        config_version="1",
        system_no="SYS-1",
        serial_number=f"SN-{record_id}",
        summary_result="不合格" if record_id in {"r-2", "r-4"} else "合格",
        available_artifacts=("raw_data",),
    )


def _key(expression: object) -> str:
    payload = expression.model_dump(mode="json") if hasattr(expression, "model_dump") else expression
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
