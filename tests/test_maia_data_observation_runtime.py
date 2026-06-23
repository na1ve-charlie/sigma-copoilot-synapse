from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from maia.api import PromptReply, TurnRequest
from maia.conversation.state import ConversationSelectionState
from maia.integrations.sigma.data_observation import (
    ObservationAvailability,
    ObservationIndicator,
    ObservationTypeSystem,
)
from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary
from maia.recognition.report import RecognitionReport
from maia.runtime import ConversationStateRepository, create_maia_runtime
from maia.selection import InMemorySelectionSetRepository, SelectionLineage, SelectionSet
from maia.tasks.data_observation import DATA_OBSERVATION_INTENT


def test_data_observation_runtime_returns_ready_params_from_message() -> None:
    handler = _runtime(
        message="我要查看 Vib1、Spd-rDL 的阶次谱",
        catalog=_Catalog(
            availability=(
                ObservationAvailability("TWO_D_OS", "Vib1", "Spd-rDL"),
                ObservationAvailability("TWO_D_OS", "Vib2", "Spd-rCH"),
            ),
            indicators={("TWO_D_OS", "Vib1", "Spd-rDL"): (ObservationIndicator(name="阶次谱", index="os-index"),)},
        ),
    )

    response = asyncio.run(handler.handle_turn(_request("s1", "我要查看 Vib1、Spd-rDL 的阶次谱")))

    assert response.plan.kind == "task"
    assert response.plan.intent == DATA_OBSERVATION_INTENT
    assert response.plan.params == {
        "dataType": "TWO_D_OS",
        "sensorList": ("Vib1",),
        "testNameList": ("Spd-rDL",),
        "indicator": {"name": "阶次谱", "index": "os-index"},
    }


def test_data_observation_runtime_prompts_and_reuses_catalog_cache() -> None:
    catalog = _Catalog(
        availability=(
            ObservationAvailability("TWO_D_CEP", "Vib2", "B"),
            ObservationAvailability("TWO_D_CEP", "Vib1", "A"),
        ),
        indicators={
            ("TWO_D_CEP", "Vib2", "B"): (ObservationIndicator(name="倒谱", index="cep-index"),),
            ("TWO_D_CEP", "Vib1", "A"): (ObservationIndicator(name="倒谱", index="cep-index"),),
        },
    )
    handler = _runtime(message="我要查看倒谱", catalog=catalog)

    first = asyncio.run(handler.handle_turn(_request("s1", "我要查看倒谱")))
    second = asyncio.run(
        handler.handle_turn(
            _request(
                "s1",
                "",
                replies=[
                    PromptReply(prompt_id="sensorList", value=["Vib1"]),
                    PromptReply(prompt_id="testNameList", value=["A"]),
                ],
            )
        )
    )

    assert first.plan.kind == "clarify"
    assert first.plan.missing_slots == ["sensorList", "testNameList"]
    assert first.plan.prompts[0].input_type == "multi_select"
    assert [candidate.value for candidate in first.plan.prompts[0].candidates] == ["Vib2", "Vib1"]
    assert second.plan.kind == "task"
    assert second.plan.params == {
        "dataType": "TWO_D_CEP",
        "sensorList": ("Vib1",),
        "testNameList": ("A",),
        "indicator": {"name": "倒谱", "index": "cep-index"},
    }
    assert catalog.availability_calls == ["1226"]


def test_data_observation_runtime_continues_after_selection_when_observation_is_intent() -> None:
    report = RecognitionReport(
        message="我想查看最近一个月的NG件的频谱",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        intents=[
            {
                "name": DATA_OBSERVATION_INTENT,
                "score": 1.0,
                "slots": {"entity_type": "indicator", "target": "频谱", "slot_valid": True},
            }
        ],
        slot_operations=[
            {
                "intent": "task.nvh.selection.set_time_range",
                "score": 1.0,
                "action": "replace",
                "entity_type": "time_range",
                "target": "start=2026-05-23 00:00:00; end=2026-06-23 23:59:59",
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
            {
                "intent": "task.nvh.selection.set_indicator",
                "score": 0.0,
                "action": "",
                "entity_type": "indicator",
                "target": "",
                "slot_valid": False,
            },
        ],
    )
    catalog = _Catalog(
        availability=(ObservationAvailability("TWO_D_FS", "Vib1", "Spd-rDL"),),
        indicators={
            ("TWO_D_FS", "Vib1", "Spd-rDL"): (ObservationIndicator(name="频谱", index="fs-index"),)
        },
    )
    handler = _runtime(message=report.message, catalog=catalog, report=report)

    response = asyncio.run(handler.handle_turn(_request("s1", report.message)))

    assert response.plan.kind == "clarify"
    assert response.plan.pending_task == DATA_OBSERVATION_INTENT
    assert response.plan.missing_slots == ["sensorList", "testNameList"]
    assert catalog.availability_calls == ["1226"]


def test_data_observation_runtime_handles_observation_slot_operation() -> None:
    report = RecognitionReport(
        message="我想查看频谱",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        slot_operations=[
            {
                "intent": DATA_OBSERVATION_INTENT,
                "score": 1.0,
                "action": "replace",
                "entity_type": "indicator",
                "target": "频谱",
                "slot_valid": True,
            }
        ],
    )
    catalog = _Catalog(
        availability=(ObservationAvailability("TWO_D_FS", "Vib1", "Spd-rDL"),),
        indicators={
            ("TWO_D_FS", "Vib1", "Spd-rDL"): (ObservationIndicator(name="频谱", index="fs-index"),)
        },
    )
    handler = _runtime(message=report.message, catalog=catalog, report=report)

    response = asyncio.run(handler.handle_turn(_request("s1", report.message)))

    assert response.plan.kind == "clarify"
    assert response.plan.pending_task == DATA_OBSERVATION_INTENT
    assert response.plan.missing_slots == ["sensorList", "testNameList"]
    assert catalog.availability_calls == ["1226"]


class _Catalog:
    def __init__(
        self,
        *,
        availability: tuple[ObservationAvailability, ...],
        indicators: dict[tuple[str, str, str], tuple[ObservationIndicator, ...]],
    ) -> None:
        self._availability = availability
        self._indicators = indicators
        self.availability_calls: list[str] = []
        self.indicator_calls: list[tuple[str, tuple[str, ...], tuple[str, ...], tuple[ObservationTypeSystem, ...]]] = []

    async def list_availability(self, dataset_id: str) -> tuple[ObservationAvailability, ...]:
        self.availability_calls.append(dataset_id)
        return self._availability

    async def list_indicators(
        self,
        *,
        data_type: str,
        sensor_list: tuple[str, ...],
        test_name_list: tuple[str, ...],
        type_systems: tuple[ObservationTypeSystem, ...],
        workspace_context,
    ) -> tuple[ObservationIndicator, ...]:
        del workspace_context
        self.indicator_calls.append((data_type, sensor_list, test_name_list, type_systems))
        return self._indicators.get((data_type, sensor_list[0], test_name_list[0]), ())


class _Recognizer:
    def __init__(self, report: RecognitionReport) -> None:
        self._report = report

    async def recognize(self, message: str, *, resolver=None, include_diagnostics: bool = False):
        del message, resolver, include_diagnostics
        return self._report


class _RecordClient:
    def __init__(self, records: tuple[TestRecordSummary, ...]) -> None:
        self._records = records

    async def list_records(self, expression, *, workspace_context, page=None, rows=None):
        del expression, workspace_context, page, rows
        return TestRecordPage(total=len(self._records), records=self._records)


class _Materializer:
    async def materialize(
        self,
        selection_set,
        *,
        records=(),
        workspace_context,
        dataset_id=None,
        dataset_name=None,
    ):
        del selection_set, records, workspace_context, dataset_name
        return dataset_id or "1226"


def _runtime(*, message: str, catalog: _Catalog, report: RecognitionReport | None = None):
    records = (
        TestRecordSummary(
            record_id="46704",
            tested_at=datetime(2026, 6, 11, 9, 30, tzinfo=UTC),
            product_type="byd0601",
            config_version="7",
            system_no="7s-SNF1001",
            serial_number="SN-46704",
        ),
    )
    selection_repository = InMemorySelectionSetRepository()
    selection_repository.save(
        SelectionSet(
            selection_set_id="sel-1",
            expression={"kind": "predicate", "name": "product_type_in", "params": {"values": ["byd0601"]}},
            record_count=1,
            record_ids=("46704",),
            dataset_id="1226",
            source_version="sigma-fixture-v1",
            created_at=datetime(2026, 6, 11, 9, 30, tzinfo=UTC),
            lineage=SelectionLineage(operation="create"),
        )
    )
    state_repository = ConversationStateRepository()
    state_repository.save(
        "s1",
        ConversationSelectionState(active_selection_set_id="sel-1", recent_selection_set_ids=("sel-1",)),
    )
    return create_maia_runtime(
        recognizer=_Recognizer(
            report
            or RecognitionReport(
                message=message,
                verdict="clear",
                requires_confirmation=False,
                degraded=False,
                action_intents=[{"name": DATA_OBSERVATION_INTENT, "score": 0.98}],
            )
        ),
        record_client=_RecordClient(records),
        selection_repository=selection_repository,
        state_repository=state_repository,
        data_observation_catalog=catalog,
        selection_materializer=_Materializer(),
        source_version="sigma-fixture-v1",
    )


def _request(
    session_id: str,
    message: str,
    replies: list[PromptReply] | None = None,
) -> TurnRequest:
    return TurnRequest(session_id=session_id, message=message, prompt_replies=replies or [])
