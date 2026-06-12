from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from themis import IntentDecision, IntentMatch, IntentSlot, RecognitionVerdict

from synapse.api.main import create_app
from synapse.domains.observation.catalog import (
    ObservationCatalog,
    ObservationCatalogEntry,
)
from synapse.domains.observation.resolver_query import (
    ObservationResolverQueryResponder,
)
from synapse.integrations.sigma import SigmaCandidate, SigmaCandidateCatalogLoader
from synapse.integrations.sigma.contracts import SigmaObservationAvailabilityRow
from synapse.recognition import CandidateCatalog
from synapse.runtime import create_synapse_runtime
from synapse.slots.contracts import SlotRef
from synapse.slots.state import SlotState
from synapse.turns import TurnRequest, TurnResponse


TASK_YAML = """
query_one_dim_data:
  intent_names:
    - task.nvh.data_observation.batch.one_dim_data
  title: Query one dim data
  risk_level: low
  requires_confirmation: false
  required_slots:
    - sensors
    - test_segments
    - indicator_names
  optional_slots: []

query_frequency_spectrum:
  intent_names:
    - task.nvh.data_observation.batch.frequency_spectrum
  title: Query frequency spectrum
  risk_level: low
  requires_confirmation: false
  required_slots:
    - sensors
    - test_segments
    - indicator_names
  optional_slots: []

query_order_spectrum:
  intent_names:
    - task.nvh.data_observation.batch.order_spectrum
  title: Query order spectrum
  risk_level: low
  requires_confirmation: false
  required_slots:
    - sensors
    - test_segments
    - indicator_names
  optional_slots: []

query_order_slice:
  intent_names:
    - task.nvh.data_observation.batch.order_slice
  title: Query order slice
  risk_level: low
  requires_confirmation: false
  required_slots:
    - sensors
    - test_segments
    - indicator_names
  optional_slots: []

query_cepstrum:
  intent_names:
    - task.nvh.data_observation.batch.cepstrum
  title: Query cepstrum
  risk_level: low
  requires_confirmation: false
  required_slots:
    - sensors
    - test_segments
    - indicator_names
  optional_slots: []
"""


class FakeRecognizer:
    def __init__(
        self,
        intent_name: str | tuple[str, ...],
        *,
        slot_operations: tuple[Any, ...] = (),
    ) -> None:
        self.intent_name = intent_name
        self.slot_operations = slot_operations
        self.messages: list[str] = []
        self.resolvers: list[Any | None] = []

    @property
    def intent_name(self) -> str | tuple[str, ...]:
        if len(self.intent_names) == 1:
            return self.intent_names[0]
        return self.intent_names

    @intent_name.setter
    def intent_name(self, value: str | tuple[str, ...]) -> None:
        if isinstance(value, tuple):
            self.intent_names = value
            return
        self.intent_names = (value,)

    async def recognize(
        self,
        message: str,
        *,
        resolver: Any | None = None,
    ) -> SimpleNamespace:
        self.messages.append(message)
        self.resolvers.append(resolver)
        return SimpleNamespace(
            verdict="clear",
            action_intents=[
                SimpleNamespace(name=intent_name)
                for intent_name in self.intent_names
            ],
            slot_operations=self.slot_operations,
        )


class FakeThemisRecognizer:
    def __init__(self, decision: IntentDecision) -> None:
        self.decision = decision

    async def recognize(
        self,
        message: str,
        *,
        resolver: Any | None = None,
    ) -> IntentDecision:
        return self.decision


class FakeCatalogLoader:
    def __init__(self) -> None:
        self.workspace_dataset_ids: list[str | None] = []

    async def load(self, request: TurnRequest) -> CandidateCatalog:
        workspace = request.workspace_context
        self.workspace_dataset_ids.append(
            workspace.dataset_id if workspace is not None else None
        )
        return CandidateCatalog.from_mapping(
            {
                "sensor": ["VibX"],
                "sensors": ["VibX"],
                "test_segments": ["run-1"],
                "indicator_names": ["RMS"],
            }
        )


class FakeTurnHandler:
    async def handle_turn(self, request: TurnRequest) -> TurnResponse:
        return TurnResponse(plan={"kind": "reply", "message": request.message})


class FakeObservationSigmaGateway:
    def __init__(
        self,
        *,
        availability_rows: tuple[SigmaObservationAvailabilityRow, ...] = (),
        indicators: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]],
            tuple[SigmaCandidate, ...],
        ] | None = None,
        domains_by_action: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._availability_rows = availability_rows
        self._indicators = indicators or {}
        self._domains_by_action = domains_by_action or {}

    async def list_sensors(self, query):
        return tuple(
            SigmaCandidate(row.sensor)
            for row in _dedupe_availability(self._availability_rows, "sensor")
        )

    async def list_test_segments(self, query):
        return tuple(
            SigmaCandidate(row.test_segment)
            for row in _dedupe_availability(self._availability_rows, "test_segment")
        )

    async def list_indicator_names(self, query):
        values = {}
        for candidates in self._indicators.values():
            for item in candidates:
                existing = values.get(item.value)
                if existing is None:
                    values[item.value] = item
                    continue
                merged = []
                for source in (
                    existing.metadata.get("data_types"),
                    item.metadata.get("data_types"),
                ):
                    if isinstance(source, str):
                        merged.append(source)
                    elif isinstance(source, tuple):
                        merged.extend(source)
                    elif isinstance(source, list):
                        merged.extend(str(value) for value in source)
                values[item.value] = SigmaCandidate(
                    item.value,
                    label=item.label or existing.label,
                    metadata={"data_types": tuple(dict.fromkeys(merged))},
                )
        return tuple(values.values())

    async def list_observation_availability(self, query):
        return self._availability_rows

    async def list_observation_indicator_names(
        self,
        query,
        *,
        domain: str,
        sensors: tuple[str, ...],
        test_segments: tuple[str, ...],
    ):
        return self._indicators[(domain, sensors, test_segments)]

    def domains_for_action(self, action_name: str) -> tuple[str, ...]:
        return self._domains_by_action.get(action_name, ())


def run(coro):
    return asyncio.run(coro)


def test_default_app_uses_synapse_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        "synapse.api.main.create_synapse_runtime",
        lambda: FakeTurnHandler(),
    )

    response = TestClient(create_app()).post(
        "/turns",
        json={"session_id": "s1", "message": "hello"},
    )

    assert response.status_code == 200
    assert response.json()["plan"] == {"kind": "reply", "message": "hello"}


def test_default_runtime_orders_catalog_before_recognition(tmp_path: Path) -> None:
    runtime = create_synapse_runtime(
        recognizer=FakeRecognizer("task.nvh.data_observation.batch.one_dim_data"),
        task_config_dir=_task_dir(tmp_path),
    )

    step_names = [type(step).__name__ for step in runtime._steps[:4]]

    assert step_names == [
        "TaskContextLoaderStep",
        "CandidateCatalogStep",
        "PreRecognitionStep",
        "ThemisRecognitionStep",
    ]
    assert type(runtime._steps[1]._loader).__name__ == "SigmaCandidateCatalogLoader"


def test_default_runtime_accepts_fake_dependencies_end_to_end(tmp_path: Path) -> None:
    task_dir = _task_dir(tmp_path)
    recognizer = FakeRecognizer("task.nvh.data_observation.batch.one_dim_data")
    catalog_loader = FakeCatalogLoader()
    slot_state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "sensors"): ["VibX"],
            SlotRef("nvh.data_observation", "test_segments"): ["run-1"],
            SlotRef("nvh.data_observation", "indicator_names"): ["RMS"],
        }
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=slot_state,
                task_config_dir=task_dir,
                candidate_catalog_loader=catalog_loader,
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "鏌ヤ竴缁存暟鎹?",
        },
    )

    assert response.status_code == 200
    assert catalog_loader.workspace_dataset_ids == ["1152"]
    assert run(recognizer.resolvers[0].resolve("sensor")) == [{"value": "VibX"}]
    assert recognizer.messages == ["鏌ヤ竴缁存暟鎹?"]
    assert response.json()["plan"] == {
        "kind": "task",
        "status": "ready",
        "name": "query_one_dim_data",
        "title": "Query one dim data",
        "risk_level": "low",
        "requires_confirmation": False,
        "params": {
            "sensors": ["VibX"],
            "test_segments": ["run-1"],
            "indicator_names": ["RMS"],
        },
        "message": "浠诲姟宸插氨缁細Query one dim data",
        "reason": None,
        "slot_state_diff": {"changes": []},
    }


def test_runtime_commits_recognized_slots_before_planning(tmp_path: Path) -> None:
    recognizer = FakeRecognizer(
        "task.nvh.data_observation.batch.one_dim_data",
        slot_operations=(
            SimpleNamespace(
                action="add",
                entity_type="sensor",
                target="VibX",
                slot_valid=True,
            ),
            SimpleNamespace(
                action="add",
                entity_type="test_segment",
                target="run-1",
                slot_valid=True,
            ),
            SimpleNamespace(
                action="add",
                entity_type="indicator",
                target="RMS",
                slot_valid=True,
            ),
        ),
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "show"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["params"] == {
        "sensors": ["VibX"],
        "test_segments": ["run-1"],
        "indicator_names": ["RMS"],
    }


def test_runtime_returns_context_update_for_slot_only_turn(tmp_path: Path) -> None:
    recognizer = FakeRecognizer(
        "",
        slot_operations=(
            SimpleNamespace(
                action="add",
                entity_type="sensor",
                target="VibX",
                slot_valid=True,
            ),
        ),
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "switch sensor"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "context_update"
    assert response.json()["plan"]["projected_slots"] == {"sensors": ["VibX"]}


def test_runtime_applies_observation_switch_as_multi_slot_replace(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer(
        "",
        slot_operations=(
            SimpleNamespace(
                action="switch",
                entity_type="sensor",
                target="VibX",
                slot_valid=True,
            ),
        ),
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "switch sensor"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["projected_slots"] == {"sensors": ["VibX"]}


def test_runtime_uses_committed_slots_across_turns_for_action_plan(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer(
        "",
        slot_operations=(
            SimpleNamespace(
                action="switch",
                entity_type="sensor",
                target="VibX",
                slot_valid=True,
            ),
        ),
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )

    first = client.post(
        "/turns",
        json={"session_id": "s1", "message": "switch sensor"},
    )
    assert first.json()["plan"]["kind"] == "context_update"

    recognizer.intent_name = "task.nvh.data_observation.batch.one_dim_data"
    recognizer.slot_operations = (
        SimpleNamespace(
            action="add",
            entity_type="test_segment",
            target="run-1",
            slot_valid=True,
        ),
        SimpleNamespace(
            action="add",
            entity_type="indicator",
            target="RMS",
            slot_valid=True,
        ),
    )
    second = client.post(
        "/turns",
        json={"session_id": "s1", "message": "show data"},
    )

    assert second.status_code == 200
    assert second.json()["plan"]["kind"] == "task"
    assert second.json()["plan"]["params"] == {
        "sensors": ["VibX"],
        "test_segments": ["run-1"],
        "indicator_names": ["RMS"],
    }


def test_runtime_uses_committed_slot_updates_for_same_turn_action(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer(
        "task.nvh.data_observation.batch.frequency_spectrum",
        slot_operations=(
            SimpleNamespace(
                action="switch",
                entity_type="sensor",
                target="VibX",
                slot_valid=True,
            ),
            SimpleNamespace(
                action="switch",
                entity_type="test_segment",
                target="run-1",
                slot_valid=True,
            ),
            SimpleNamespace(
                action="switch",
                entity_type="indicator",
                target="RMS",
                slot_valid=True,
            ),
        ),
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "message": "鍒囨崲鍒?VibX run-1 鐪嬮璋?",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["name"] == "query_frequency_spectrum"
    assert response.json()["plan"]["params"] == {
        "sensors": ["VibX"],
        "test_segments": ["run-1"],
        "indicator_names": ["RMS"],
    }


def test_runtime_same_turn_observation_updates_override_existing_domain_slots(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer(
        "task.nvh.data_observation.batch.frequency_spectrum",
        slot_operations=(
            SimpleNamespace(
                action="switch",
                entity_type="data_type",
                target="TWO_D_FS",
                slot_valid=True,
            ),
            SimpleNamespace(
                action="switch",
                entity_type="sensor",
                target="sensor01",
                slot_valid=True,
            ),
            SimpleNamespace(
                action="switch",
                entity_type="test_segment",
                target="Spd-rDL",
                slot_valid=True,
            ),
        ),
    )
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("TWO_D_FS", "sensor01", "Spd-rDL"),
            SigmaObservationAvailabilityRow("TWO_D_OS", "Vib1", "old-segment"),
        ),
        indicators={
            ("TWO_D_FS", ("sensor01",), ("Spd-rDL",)): (
                SigmaCandidate("Spectrum"),
            ),
            ("TWO_D_FS", ("Vib1",), ("old-segment",)): (
                SigmaCandidate("Order Spectrum"),
            ),
        },
        domains_by_action={"query_frequency_spectrum": ("TWO_D_FS",)},
    )
    slot_state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "data_types"): "TWO_D_OS",
            SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
            SlotRef("nvh.data_observation", "test_segments"): ["old-segment"],
            SlotRef("nvh.data_observation", "indicator_names"): ["Order Spectrum"],
        }
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=slot_state,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "show spectrum sensor01 Spd-rDL",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["params"] == {
        "data_types": "TWO_D_FS",
        "sensors": ["sensor01"],
        "test_segments": ["Spd-rDL"],
        "indicator_names": ["Spectrum"],
    }
    assert response.json()["plan"]["slot_state_diff"] == {
        "changes": [
            {"slot": "data_types", "before": "TWO_D_OS", "after": "TWO_D_FS"},
            {
                "slot": "indicator_names",
                "before": ["Order Spectrum"],
                "after": ["Spectrum"],
            },
            {"slot": "sensors", "before": ["Vib1"], "after": ["sensor01"]},
            {
                "slot": "test_segments",
                "before": ["old-segment"],
                "after": ["Spd-rDL"],
            },
        ]
    }


def test_runtime_clear_context_clears_committed_slot_state(tmp_path: Path) -> None:
    recognizer = FakeRecognizer(
        "",
        slot_operations=(
            SimpleNamespace(
                action="switch",
                entity_type="sensor",
                target="VibX",
                slot_valid=True,
            ),
        ),
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )
    client.post(
        "/turns",
        json={"session_id": "s1", "message": "switch sensor"},
    )

    recognizer.intent_name = "task.nvh.context_management.clear_context"
    recognizer.slot_operations = ()
    cleared = client.post(
        "/turns",
        json={"session_id": "s1", "message": "clear context"},
    )

    assert cleared.status_code == 200
    assert cleared.json()["plan"]["kind"] == "context_clear"

    recognizer.intent_name = "task.nvh.data_observation.batch.one_dim_data"
    recognizer.slot_operations = (
        SimpleNamespace(
            action="add",
            entity_type="test_segment",
            target="run-1",
            slot_valid=True,
        ),
        SimpleNamespace(
            action="add",
            entity_type="indicator",
            target="RMS",
            slot_valid=True,
        ),
    )
    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "show data"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "clarify"
    assert response.json()["plan"]["missing_slots"] == ["sensors"]


def test_runtime_clarifies_invalid_slot_before_commit(tmp_path: Path) -> None:
    recognizer = FakeRecognizer(
        "",
        slot_operations=(
            SimpleNamespace(
                action="add",
                entity_type="sensor",
                target="missing_sensor",
                slot_valid=True,
            ),
        ),
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "switch sensor"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "clarify"
    assert response.json()["plan"]["invalid_slots"] == ["sensors"]
    candidates = response.json()["plan"]["prompts"][0]["candidates"]
    assert [item["value"] for item in candidates] == ["VibX"]


def test_runtime_replies_to_resolver_query_from_candidate_catalog(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("inquiry.nvh.resolver_query.sensors")
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "有哪些传感器"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "reply"
    assert response.json()["plan"]["data"] == {
        "slot_name": "sensors",
        "candidates": ["VibX"],
    }


def test_runtime_keeps_resolver_query_with_entity_type_as_action_intent(
    tmp_path: Path,
) -> None:
    recognizer = FakeThemisRecognizer(
        IntentDecision(
            verdict=RecognitionVerdict.CLEAR,
            intents=(
                IntentMatch(
                    name="inquiry.nvh.resolver_query.sensors",
                    score=0.95,
                    slots=IntentSlot(entity_type="sensor"),
                ),
            ),
        )
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "which sensors"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["data"] == {
        "slot_name": "sensors",
        "candidates": ["VibX"],
    }


def test_runtime_uses_sigma_observation_source_for_sensor_query_by_default(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("inquiry.nvh.resolver_query.sensors")
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
            SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib1", "coast"),
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib2", "runup"),
        )
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "workspace_context": {"dataset_id": "1152"}, "message": "which sensors"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["message"] == "当前可用候选如下。"
    assert response.json()["plan"]["data"] == {
        "entries": [{"sensors": "Vib1"}, {"sensors": "Vib2"}],
        "facets": [
            {
                "slot_name": "sensors",
                "label": "传感器",
                "candidates": ["Vib1", "Vib2"],
                "selected": [],
            }
        ],
    }


def test_runtime_uses_sigma_observation_action_scope_for_indicator_query_by_default(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer(
        (
            "inquiry.nvh.resolver_query.indicators",
            "task.nvh.data_observation.batch.frequency_spectrum",
        )
    )
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
            SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib1", "runup"),
        ),
        indicators={
            ("TWO_D_FS", ("Vib1",), ("runup",)): (SigmaCandidate("Spectrum"),),
            ("TWO_D_CEP", ("Vib1",), ("runup",)): (SigmaCandidate("Cepstrum"),),
        },
        domains_by_action={"query_frequency_spectrum": ("TWO_D_FS",)},
    )
    slot_state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
            SlotRef("nvh.data_observation", "test_segments"): ["runup"],
        }
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=slot_state,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "workspace_context": {"dataset_id": "1152"}, "message": "which indicators"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["message"] == "当前可用候选如下。"
    assert response.json()["plan"]["data"] == {
        "entries": [{"indicator_names": "Spectrum"}],
        "facets": [
            {
                "slot_name": "indicator_names",
                "label": "指标",
                "candidates": ["Spectrum"],
                "selected": [],
                "candidate_domains": {"Spectrum": ["TWO_D_FS"]},
            },
        ],
    }


def test_runtime_replies_to_observation_sensor_query_with_entries_and_facets(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("inquiry.nvh.resolver_query.sensors")
    responder = ObservationResolverQueryResponder(
        catalog=_observation_catalog(
            ObservationCatalogEntry(sensors="Vib1"),
            ObservationCatalogEntry(sensors="Vib1"),
            ObservationCatalogEntry(sensors="Vib2"),
        )
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
                resolver_query_handler=responder,
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "which sensors"},
    )

    assert response.status_code == 200
    assert response.json()["plan"] == {
        "kind": "reply",
        "message": "当前可用候选如下。",
        "data": {
            "entries": [{"sensors": "Vib1"}, {"sensors": "Vib2"}],
            "facets": [
                {
                    "slot_name": "sensors",
                    "label": "传感器",
                    "candidates": ["Vib1", "Vib2"],
                    "selected": [],
                },
            ],
        },
        "suggestions": [],
        "slot_state_diff": {"changes": []},
    }


def test_runtime_replies_to_observation_multi_field_query_with_tuple_dedupe(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer(
        (
            "inquiry.nvh.resolver_query.sensors",
            "inquiry.nvh.resolver_query.test_segments",
        )
    )
    responder = ObservationResolverQueryResponder(
        catalog=_observation_catalog(
            ObservationCatalogEntry(sensors="Vib1", test_segments="runup"),
            ObservationCatalogEntry(sensors="Vib1", test_segments="runup"),
            ObservationCatalogEntry(sensors="Vib2", test_segments="coast"),
        )
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
                resolver_query_handler=responder,
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "which sensors and segments"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["message"] == "当前可用候选如下。"
    assert response.json()["plan"]["data"] == {
        "entries": [
            {"sensors": "Vib1", "test_segments": "runup"},
            {"sensors": "Vib2", "test_segments": "coast"},
        ],
        "facets": [
            {
                "slot_name": "sensors",
                "label": "传感器",
                "candidates": ["Vib1", "Vib2"],
                "selected": [],
            },
            {
                "slot_name": "test_segments",
                "label": "测试段",
                "candidates": ["runup", "coast"],
                "selected": [],
            },
        ],
    }


def test_runtime_replies_to_observation_indicator_query_with_entries_and_facets(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("inquiry.nvh.resolver_query.indicators")
    responder = ObservationResolverQueryResponder(
        catalog=_observation_catalog(
            ObservationCatalogEntry(indicator_names="RMS"),
            ObservationCatalogEntry(indicator_names="Spectrum"),
            ObservationCatalogEntry(indicator_names="RMS"),
        )
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
                resolver_query_handler=responder,
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "which indicators"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["message"] == "当前可用候选如下。"
    assert response.json()["plan"]["data"] == {
        "entries": [
            {"indicator_names": "RMS"},
            {"indicator_names": "Spectrum"},
        ],
        "facets": [
            {
                "slot_name": "indicator_names",
                "label": "指标",
                "candidates": ["RMS", "Spectrum"],
                "selected": [],
                "candidate_domains": {"RMS": [], "Spectrum": []},
            },
        ],
    }


def test_runtime_routes_scoped_indicator_list_intent_to_observation_resolver_query(
    tmp_path: Path,
) -> None:
    recognizer = FakeThemisRecognizer(
        IntentDecision(
            verdict=RecognitionVerdict.CLEAR,
            intents=(
                IntentMatch(
                    name="task.nvh.data_observation.indicator_query.list",
                    score=1.0,
                    slots=IntentSlot(
                        entity_type="data_type",
                        target="ONE_D",
                        slot_valid=True,
                    ),
                ),
            ),
        )
    )
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("ONE_D", "Vib1", "runup"),
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
        ),
        indicators={
            ("ONE_D", ("Vib1",), ("runup",)): (SigmaCandidate("RMS"),),
            ("TWO_D_FS", ("Vib1",), ("runup",)): (SigmaCandidate("Spectrum"),),
        },
    )
    slot_state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
            SlotRef("nvh.data_observation", "test_segments"): ["runup"],
        }
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=slot_state,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "一维指标有什么指标",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "reply"
    assert response.json()["plan"]["message"] == "当前可用候选如下。"
    assert response.json()["plan"]["data"] == {
        "entries": [{"indicator_names": "RMS"}],
        "facets": [
            {
                "slot_name": "indicator_names",
                "label": "指标",
                "candidates": ["RMS"],
                "selected": [],
                "candidate_domains": {"RMS": ["ONE_D"]},
            },
        ],
    }


def test_runtime_infers_task_from_unique_indicator_domain_without_data_type_slot(
    tmp_path: Path,
) -> None:
    recognizer = FakeThemisRecognizer(
        IntentDecision(
            verdict=RecognitionVerdict.CLEAR,
            intents=(
                IntentMatch(
                    name="task.nvh.data_observation.indicator_query.list",
                    score=0.95,
                    slots=IntentSlot(entity_type="indicator", target="RMS"),
                ),
            ),
        )
    )
    gateway = FakeObservationSigmaGateway(
        indicators={
            ("ONE_D", ("Vib1",), ("runup",)): (
                SigmaCandidate("RMS", metadata={"data_types": ("ONE_D",)}),
            ),
        },
        domains_by_action={"query_one_dim_data": ("ONE_D",)},
    )
    slot_state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
            SlotRef("nvh.data_observation", "test_segments"): ["runup"],
        }
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=slot_state,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "workspace_context": {"dataset_id": "1152"}, "message": "show RMS"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["name"] == "query_one_dim_data"
    assert response.json()["plan"]["params"] == {
        "data_types": "ONE_D",
        "sensors": ["Vib1"],
        "test_segments": ["runup"],
        "indicator_names": ["RMS"],
    }


def test_runtime_clarifies_shared_indicator_with_data_type_candidates(
    tmp_path: Path,
) -> None:
    recognizer = FakeThemisRecognizer(
        IntentDecision(
            verdict=RecognitionVerdict.CLEAR,
            intents=(
                IntentMatch(
                    name="task.nvh.data_observation.indicator_query.list",
                    score=0.95,
                    slots=IntentSlot(entity_type="indicator", target="48阶"),
                ),
            ),
        )
    )
    gateway = FakeObservationSigmaGateway(
        indicators={
            ("ONE_D", ("Vib1",), ("runup",)): (
                SigmaCandidate("48阶", metadata={"data_types": ("ONE_D",)}),
            ),
            ("TWO_D_OC", ("Vib1",), ("runup",)): (
                SigmaCandidate("48阶", metadata={"data_types": ("TWO_D_OC",)}),
            ),
        },
        domains_by_action={
            "query_one_dim_data": ("ONE_D",),
            "query_order_slice": ("TWO_D_OC",),
        },
    )
    slot_state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
            SlotRef("nvh.data_observation", "test_segments"): ["runup"],
        }
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=slot_state,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "workspace_context": {"dataset_id": "1152"}, "message": "show 48阶"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "clarify"
    assert response.json()["plan"]["reason"] == "ambiguous_slots"
    assert response.json()["plan"]["missing_slots"] == ["data_types"]
    assert response.json()["plan"]["prompts"][0]["id"] == "data_types"
    assert [item["value"] for item in response.json()["plan"]["prompts"][0]["candidates"]] == [
        "ONE_D",
        "TWO_D_OC",
    ]


def test_runtime_prefers_explicit_observation_scope_over_indicator_conflict(
    tmp_path: Path,
) -> None:
    recognizer = FakeThemisRecognizer(
        IntentDecision(
            verdict=RecognitionVerdict.CLEAR,
            intents=(
                IntentMatch(
                    name="task.nvh.data_observation.batch.order_slice",
                    score=0.95,
                    slots=IntentSlot(entity_type="data_type", target="TWO_D_OC"),
                ),
                IntentMatch(
                    name="task.nvh.data_observation.indicator_query.list",
                    score=0.95,
                    slots=IntentSlot(entity_type="indicator", target="48阶"),
                ),
            ),
        )
    )
    gateway = FakeObservationSigmaGateway(
        indicators={
            ("ONE_D", ("Vib1",), ("runup",)): (
                SigmaCandidate("48阶", metadata={"data_types": ("ONE_D",)}),
            ),
            ("TWO_D_OC", ("Vib1",), ("runup",)): (
                SigmaCandidate("48阶", metadata={"data_types": ("TWO_D_OC",)}),
            ),
        },
        domains_by_action={
            "query_one_dim_data": ("ONE_D",),
            "query_order_slice": ("TWO_D_OC",),
        },
    )
    slot_state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
            SlotRef("nvh.data_observation", "test_segments"): ["runup"],
        }
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=slot_state,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "workspace_context": {"dataset_id": "1152"}, "message": "show 48阶 in order slice"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["name"] == "query_order_slice"
    assert response.json()["plan"]["params"] == {
        "data_types": "TWO_D_OC",
        "sensors": ["Vib1"],
        "test_segments": ["runup"],
        "indicator_names": ["48阶"],
    }


def test_runtime_projects_selected_observation_data_type_into_task_params(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("task.nvh.data_observation.batch.frequency_spectrum")
    gateway = FakeObservationSigmaGateway(
        domains_by_action={"query_frequency_spectrum": ("TWO_D_FS",)}
    )
    slot_state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "data_types"): "TWO_D_FS",
            SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
            SlotRef("nvh.data_observation", "test_segments"): ["runup"],
            SlotRef("nvh.data_observation", "indicator_names"): ["Spectrum"],
        }
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=slot_state,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "show spectrum",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["name"] == "query_frequency_spectrum"
    assert response.json()["plan"]["params"] == {
        "data_types": "TWO_D_FS",
        "sensors": ["Vib1"],
        "test_segments": ["runup"],
        "indicator_names": ["Spectrum"],
    }


def test_runtime_autofills_frequency_spectrum_defaults_from_action_scope(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("task.nvh.data_observation.batch.frequency_spectrum")
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib2", "runup"),
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib9", "coast"),
        ),
        indicators={
            ("TWO_D_FS", ("Vib2",), ("runup",)): (
                SigmaCandidate("Peak"),
                SigmaCandidate("频谱"),
            ),
        },
        domains_by_action={"query_frequency_spectrum": ("TWO_D_FS",)},
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "show spectrum",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["name"] == "query_frequency_spectrum"
    assert response.json()["plan"]["params"] == {
        "data_types": "TWO_D_FS",
        "sensors": ["Vib2"],
        "test_segments": ["runup"],
        "indicator_names": ["频谱"],
    }


def test_runtime_autofills_first_scoped_indicator_when_domain_alias_is_missing(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("task.nvh.data_observation.batch.frequency_spectrum")
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib2", "runup"),
        ),
        indicators={
            ("TWO_D_FS", ("Vib2",), ("runup",)): (
                SigmaCandidate("Peak"),
                SigmaCandidate("RMS"),
            ),
        },
        domains_by_action={"query_frequency_spectrum": ("TWO_D_FS",)},
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "show spectrum",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["params"] == {
        "data_types": "TWO_D_FS",
        "sensors": ["Vib2"],
        "test_segments": ["runup"],
        "indicator_names": ["Peak"],
    }


def test_runtime_autofills_order_spectrum_indicator_from_domain_alias(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("task.nvh.data_observation.batch.order_spectrum")
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("TWO_D_OS", "Vib3", "coast"),
        ),
        indicators={
            ("TWO_D_OS", ("Vib3",), ("coast",)): (
                SigmaCandidate("12Order"),
                SigmaCandidate("阶次谱"),
            ),
        },
        domains_by_action={"query_order_spectrum": ("TWO_D_OS",)},
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "show order spectrum",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["name"] == "query_order_spectrum"
    assert response.json()["plan"]["params"] == {
        "data_types": "TWO_D_OS",
        "sensors": ["Vib3"],
        "test_segments": ["coast"],
        "indicator_names": ["阶次谱"],
    }


def test_runtime_autofills_first_available_data_type_when_action_domain_is_unresolved(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("task.nvh.data_observation.batch.cepstrum")
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib7", "runup"),
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib7", "runup"),
        ),
        indicators={
            ("TWO_D_CEP", ("Vib7",), ("runup",)): (SigmaCandidate("倒频谱"),),
        },
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "show cepstrum",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["name"] == "query_cepstrum"
    assert response.json()["plan"]["params"] == {
        "data_types": "TWO_D_CEP",
        "sensors": ["Vib7"],
        "test_segments": ["runup"],
        "indicator_names": ["倒频谱"],
    }


def test_runtime_autofills_cepstrum_indicator_from_domain_alias(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("task.nvh.data_observation.batch.cepstrum")
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib7", "runup"),
        ),
        indicators={
            ("TWO_D_CEP", ("Vib7",), ("runup",)): (
                SigmaCandidate("倒频谱"),
                SigmaCandidate("倒阶次谱"),
            ),
        },
        domains_by_action={"query_cepstrum": ("TWO_D_CEP",)},
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "show cepstrum",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["name"] == "query_cepstrum"
    assert response.json()["plan"]["params"] == {
        "data_types": "TWO_D_CEP",
        "sensors": ["Vib7"],
        "test_segments": ["runup"],
        "indicator_names": ["倒阶次谱"],
    }


def test_runtime_keeps_explicit_cepstrum_indicator_over_default_alias_priority(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer(
        "task.nvh.data_observation.batch.cepstrum",
        slot_operations=(
            SimpleNamespace(
                action="switch",
                entity_type="indicator",
                target="倒频谱",
                slot_valid=True,
            ),
        ),
    )
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib7", "runup"),
        ),
        indicators={
            ("TWO_D_CEP", ("Vib7",), ("runup",)): (
                SigmaCandidate("倒频谱"),
                SigmaCandidate("倒阶次谱"),
            ),
        },
        domains_by_action={"query_cepstrum": ("TWO_D_CEP",)},
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "show explicit cepstrum indicator",
        },
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "task"
    assert response.json()["plan"]["name"] == "query_cepstrum"
    assert response.json()["plan"]["params"] == {
        "data_types": "TWO_D_CEP",
        "sensors": ["Vib7"],
        "test_segments": ["runup"],
        "indicator_names": ["倒频谱"],
    }


def test_runtime_uses_active_task_scope_for_follow_up_indicator_query(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("task.nvh.data_observation.batch.frequency_spectrum")
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("ONE_D", "Vib1", "runup"),
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib2", "runup"),
        ),
        indicators={
            ("ONE_D", ("Vib1",), ("runup",)): (SigmaCandidate("RMS"),),
            ("TWO_D_FS", ("Vib2",), ("runup",)): (SigmaCandidate("Spectrum"),),
        },
        domains_by_action={
            "query_one_dim_data": ("ONE_D",),
            "query_frequency_spectrum": ("TWO_D_FS",),
        },
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    first = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "show spectrum",
        },
    )

    assert first.status_code == 200
    assert first.json()["plan"]["kind"] == "task"
    assert first.json()["plan"]["name"] == "query_frequency_spectrum"

    recognizer.intent_name = "inquiry.nvh.resolver_query.indicators"
    second = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "which indicators",
        },
    )

    assert second.status_code == 200
    assert second.json()["plan"]["kind"] == "reply"
    assert second.json()["plan"]["message"] == "当前可用候选如下。"
    assert second.json()["plan"]["data"] == {
        "entries": [{"indicator_names": "Spectrum"}],
        "facets": [
            {
                "slot_name": "indicator_names",
                "label": "指标",
                "candidates": ["Spectrum"],
                "selected": ["Spectrum"],
                "candidate_domains": {"Spectrum": ["TWO_D_FS"]},
            },
        ],
    }


def test_runtime_uses_active_task_scope_for_follow_up_sensor_query(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("task.nvh.data_observation.batch.frequency_spectrum")
    gateway = FakeObservationSigmaGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("ONE_D", "Vib1", "runup"),
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib2", "runup"),
        ),
        domains_by_action={
            "query_one_dim_data": ("ONE_D",),
            "query_frequency_spectrum": ("TWO_D_FS",),
        },
    )
    slot_state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "sensors"): ["Vib2"],
            SlotRef("nvh.data_observation", "test_segments"): ["runup"],
            SlotRef("nvh.data_observation", "indicator_names"): ["Spectrum"],
        }
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=slot_state,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
            )
        )
    )

    first = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "show spectrum",
        },
    )

    assert first.status_code == 200
    assert first.json()["plan"]["kind"] == "task"
    assert first.json()["plan"]["name"] == "query_frequency_spectrum"
    assert first.json()["plan"]["params"]["data_types"] == "TWO_D_FS"

    recognizer.intent_name = "inquiry.nvh.resolver_query.sensors"
    second = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "workspace_context": {"dataset_id": "1152"},
            "message": "which sensors",
        },
    )

    assert second.status_code == 200
    assert second.json()["plan"]["kind"] == "reply"
    assert second.json()["plan"]["message"] == "当前可用候选如下。"
    assert second.json()["plan"]["data"] == {
        "entries": [{"sensors": "Vib2"}],
        "facets": [
            {
                "slot_name": "sensors",
                "label": "传感器",
                "candidates": ["Vib2"],
                "selected": ["Vib2"],
            }
        ],
    }


def test_runtime_observation_resolver_query_keeps_state_and_does_not_advance_task(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer(
        (
            "inquiry.nvh.resolver_query.sensors",
            "task.nvh.data_observation.batch.one_dim_data",
        )
    )
    responder = ObservationResolverQueryResponder(
        catalog=_observation_catalog(ObservationCatalogEntry(sensors="Vib1"))
    )
    slot_state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "sensors"): ["VibX"],
            SlotRef("nvh.data_observation", "test_segments"): ["run-1"],
            SlotRef("nvh.data_observation", "indicator_names"): ["RMS"],
        }
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=slot_state,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
                resolver_query_handler=responder,
            )
        )
    )

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "which sensors"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "reply"
    assert response.json()["plan"]["message"] == "当前可用候选如下。"
    assert response.json()["plan"]["data"] == {
        "entries": [{"sensors": "Vib1"}],
        "facets": [
            {
                "slot_name": "sensors",
                "label": "传感器",
                "candidates": ["Vib1"],
                "selected": ["VibX"],
            }
        ],
    }
    assert response.json()["plan"]["slot_state_diff"] == {"changes": []}
    assert "name" not in response.json()["plan"]
    assert "status" not in response.json()["plan"]
    assert set(response.json()["plan"]["data"]) == {"entries", "facets"}

    recognizer.intent_name = "inquiry.nvh.context_management.current"
    current = client.post(
        "/turns",
        json={"session_id": "s1", "message": "current context"},
    )

    assert current.status_code == 200
    assert current.json()["plan"]["data"] == {
        "slots": {
            "sensors": ["VibX"],
            "test_segments": ["run-1"],
            "indicator_names": ["RMS"],
        }
    }


def test_runtime_replies_with_current_committed_context(tmp_path: Path) -> None:
    recognizer = FakeRecognizer(
        "",
        slot_operations=(
            SimpleNamespace(
                action="switch",
                entity_type="sensor",
                target="VibX",
                slot_valid=True,
            ),
        ),
    )
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )
    client.post(
        "/turns",
        json={"session_id": "s1", "message": "switch sensor"},
    )

    recognizer.intent_name = "inquiry.nvh.context_management.current"
    recognizer.slot_operations = ()
    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "当前上下文是什么"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "reply"
    assert response.json()["plan"]["data"] == {"slots": {"sensors": ["VibX"]}}


def _task_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "data_observation.yaml").write_text(TASK_YAML, encoding="utf-8")
    return path


def _observation_catalog(
    *entries: ObservationCatalogEntry,
) -> ObservationCatalog:
    return ObservationCatalog(entries)


def _dedupe_availability(
    rows: tuple[SigmaObservationAvailabilityRow, ...],
    field_name: str,
) -> list[SigmaObservationAvailabilityRow]:
    seen: set[str] = set()
    result = []
    for row in rows:
        value = getattr(row, field_name)
        if value in seen:
            continue
        seen.add(value)
        result.append(row)
    return result




