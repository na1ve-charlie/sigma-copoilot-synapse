from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from maia.api import TurnRequest
from maia.integrations.sigma.audio_generation import NgAudioGenerationRequest
from maia.integrations.sigma.product_catalog import ProductConfig
from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary
from maia.recognition import RecognitionReport
from maia.selection.expression import AllOf, Predicate, parse_filter_expression


def test_audio_generation_filters_ng_records_then_confirms_and_submits() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("46467", product_type="A", summary_result="FAIL"),
        _record("46478", product_type="A", summary_result="PASS"),
        _record("46477", product_type="A", summary_result="NG"),
        _record("46472", product_type="A", summary_result="不合格"),
    )
    generator = _AudioGenerator()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.audio.generate"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        audio_generation_client=generator,
        source_version="sigma-fixture-v1",
    )

    first = asyncio.run(handler.handle_turn(_request("s1", "生成 A 的 NG 音频")))
    second = asyncio.run(handler.handle_turn(_request("s1", "确认")))

    assert first.plan.kind == "confirm"
    assert first.plan.reason == "medium_risk_operation"
    assert first.plan.payload["operation"] == "task.nvh.audio.generate"
    assert first.plan.payload["params"]["resultIds"] == [46467, 46477, 46472]
    assert first.plan.payload["record_count"] == 3
    assert second.plan.kind == "task"
    assert second.plan.status == "submitted"
    assert generator.requests[0].to_body() == [46467, 46477, 46472]


def test_audio_generation_replies_when_selection_has_no_ng_records() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("46478", product_type="A", summary_result="PASS"),
        _record("46479", product_type="A", summary_result="合格"),
    )
    generator = _AudioGenerator()
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.audio.generate"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        audio_generation_client=generator,
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "生成 A 的 NG 音频")))

    assert response.plan.kind == "reply"
    assert response.plan.message == "当前测试记录中不包含不合格测试件。"
    assert generator.requests == []


def test_audio_generation_blocks_non_numeric_ng_record_id() -> None:
    from maia.runtime import create_maia_runtime

    records = (_record("bad-id", product_type="A", summary_result="FAIL"),)
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                _report(
                    actions=["task.nvh.audio.generate"],
                    operations=[
                        {"action": "replace", "entity_type": "product_type", "target": "A"},
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        audio_generation_client=_AudioGenerator(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "生成 A 的 NG 音频")))

    assert response.plan.kind == "task"
    assert response.plan.status == "blocked"
    assert response.plan.reason == "invalid_record_id"


def test_audio_generation_handles_llm_ng_target_as_terminal_action() -> None:
    from maia.runtime import create_maia_runtime

    records = (
        _record("46467", product_type="A", summary_result="不合格"),
        _record("46478", product_type="A", summary_result="PASS"),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                RecognitionReport(
                    message="帮我生成 NG 音频",
                    verdict="clear",
                    requires_confirmation=False,
                    degraded=False,
                    intents=[
                        {
                            "name": "task.nvh.audio.generate",
                            "score": 1.0,
                            "slots": {"target": "NG", "slot_valid": True},
                        }
                    ],
                    slot_operations=[
                        {
                            "intent": "task.nvh.audio.generate",
                            "score": 1.0,
                            "action": "",
                            "entity_type": "",
                            "target": "NG",
                            "slot_valid": True,
                        },
                        {
                            "intent": "task.nvh.selection.set_summary_result",
                            "score": 1.0,
                            "action": "replace",
                            "entity_type": "summary_result",
                            "target": "不合格",
                            "slot_valid": True,
                        },
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        audio_generation_client=_AudioGenerator(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "帮我生成 NG 音频")))

    assert response.plan.kind == "confirm"
    assert response.plan.payload["operation"] == "task.nvh.audio.generate"
    assert response.plan.payload["params"]["resultIds"] == [46467]


def test_audio_generation_uses_active_selection_without_reasking_config_version() -> None:
    from maia.conversation.state import ConversationSelectionState
    from maia.runtime import ConversationStateRepository, create_maia_runtime
    from maia.selection import InMemorySelectionSetRepository, SelectionLineage, SelectionSet

    records = (
        _record("46467", product_type="A", summary_result="不合格", config_version="2"),
        _record("46477", product_type="A", summary_result="不合格", config_version="1"),
        _record("46478", product_type="A", summary_result="PASS", config_version="0"),
    )
    selection_repository = InMemorySelectionSetRepository()
    state_repository = ConversationStateRepository()
    selection = SelectionSet(
        selection_set_id="sel-active",
        expression={
            "kind": "all_of",
            "expressions": [
                {
                    "kind": "predicate",
                    "name": "product_type_in",
                    "params": {"values": ["A"]},
                },
                {
                    "kind": "predicate",
                    "name": "config_version_in",
                    "params": {"values": ["2", "1", "0"]},
                },
                {
                    "kind": "predicate",
                    "name": "summary_result_in",
                    "params": {"values": ["不合格"]},
                },
            ],
        },
        record_count=3,
        record_ids=("46467", "46477", "46478"),
        source_version="sigma-fixture-v1",
        created_at=datetime(2026, 6, 11, 9, 30, tzinfo=UTC),
        lineage=SelectionLineage(operation="create"),
    )
    selection_repository.save(selection)
    state_repository.save(
        "s1",
        ConversationSelectionState(
            active_selection_set_id=selection.selection_set_id,
            recent_selection_set_ids=(selection.selection_set_id,),
        ),
    )
    handler = create_maia_runtime(
        recognizer=_SequenceRecognizer(
            [
                RecognitionReport(
                    message="帮我生成 NG 音频",
                    verdict="clear",
                    requires_confirmation=False,
                    degraded=False,
                    action_intents=[
                        {"name": "task.nvh.audio.generate", "score": 0.9767}
                    ],
                    slot_operations=[
                        {
                            "intent": "task.nvh.selection.set_summary_result",
                            "score": 1.0,
                            "action": "replace",
                            "entity_type": "summary_result",
                            "target": "不合格",
                            "slot_valid": True,
                        },
                    ],
                )
            ]
        ),
        record_client=_RecordClient(records),
        product_catalog=_ProductCatalog(_configs_from_records(records)),
        selection_repository=selection_repository,
        state_repository=state_repository,
        audio_generation_client=_AudioGenerator(),
        source_version="sigma-fixture-v1",
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "帮我生成 NG 音频")))

    assert response.plan.kind == "confirm"
    assert response.plan.payload["operation"] == "task.nvh.audio.generate"
    assert response.plan.payload["params"]["resultIds"] == [46467, 46477]


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


class _AudioGenerator:
    def __init__(self) -> None:
        self.requests: list[NgAudioGenerationRequest] = []

    async def generate(self, request: NgAudioGenerationRequest, *, workspace_context):
        del workspace_context
        self.requests.append(request)
        return {"code": 200, "data": None}


def _report(*, actions: list[str], operations: list[dict[str, object]]) -> RecognitionReport:
    return RecognitionReport(
        message="generate audio",
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


def _request(session_id: str, message: str) -> TurnRequest:
    return TurnRequest(session_id=session_id, message=message)


def _record(
    record_id: str,
    *,
    product_type: str,
    summary_result: str,
    config_version: str = "1",
    system_no: str = "SYS-1",
) -> TestRecordSummary:
    return TestRecordSummary(
        record_id=record_id,
        tested_at=datetime(2026, 6, 11, 9, 30, tzinfo=UTC),
        product_type=product_type,
        config_version=config_version,
        system_no=system_no,
        serial_number=f"SN-{record_id}",
        summary_result=summary_result,
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
        if parsed.name == "summary_result_in":
            return record.summary_result in normalized
        if parsed.name == "all_records":
            return True
    if isinstance(parsed, AllOf):
        return all(_matches(record, child) for child in parsed.expressions)
    raise AssertionError(f"unsupported expression: {parsed}")
