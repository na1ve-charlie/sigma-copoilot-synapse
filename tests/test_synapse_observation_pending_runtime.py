from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from themis import IntentDecision, IntentMatch, IntentSlot, RecognitionVerdict

from synapse.api.main import create_app
from synapse.integrations.sigma import SigmaCandidate, SigmaCandidateCatalogLoader
from synapse.integrations.sigma.contracts import SigmaObservationAvailabilityRow
from synapse.runtime import create_synapse_runtime
from synapse.slots.contracts import SlotRef
from synapse.slots.state import SlotState


TASK_YAML = """
query_one_dim_data:
  intent_names: [task.nvh.data_observation.batch.one_dim_data]
  title: Query one dim data
  risk_level: low
  requires_confirmation: false
  required_slots: [sensors, test_segments, indicator_names]
  optional_slots: []
query_order_slice:
  intent_names: [task.nvh.data_observation.batch.order_slice]
  title: Query order slice
  risk_level: low
  requires_confirmation: false
  required_slots: [sensors, test_segments, indicator_names]
  optional_slots: []
"""


class MutableRecognizer:
    def __init__(self) -> None:
        self.decision = _indicator_decision()

    async def recognize(self, message, *, resolver=None):
        del message, resolver
        return self.decision


class ObservationGateway:
    def __init__(
        self,
        availability_rows: tuple[SigmaObservationAvailabilityRow, ...] = (),
    ) -> None:
        self._availability_rows = availability_rows

    async def list_sensors(self, query):
        del query
        return tuple(
            SigmaCandidate(value)
            for value in dict.fromkeys(row.sensor for row in self._availability_rows)
        )

    async def list_test_segments(self, query):
        del query
        return tuple(
            SigmaCandidate(value)
            for value in dict.fromkeys(
                row.test_segment for row in self._availability_rows
            )
        )

    async def list_indicator_names(self, query):
        del query
        return (
            SigmaCandidate(
                "48阶",
                metadata={
                    "data_types": ("ONE_D", "TWO_D_OC"),
                    "indexes_by_data_type": {
                        "ONE_D": "one-d-48",
                        "TWO_D_OC": "order-cut-48",
                    },
                },
            ),
        )

    async def list_observation_availability(self, query):
        del query
        return self._availability_rows

    async def list_observation_indicator_names(
        self,
        query,
        *,
        domain,
        sensors,
        test_segments,
    ):
        del query, sensors, test_segments
        if domain not in {"ONE_D", "TWO_D_OC"}:
            return ()
        return (
            SigmaCandidate(
                "48阶",
                metadata={
                    "data_types": (domain,),
                    "indexes_by_data_type": {
                        domain: (
                            "one-d-48"
                            if domain == "ONE_D"
                            else "order-cut-48"
                        )
                    },
                },
            ),
        )

    def domains_for_action(self, action_name):
        return {
            "query_one_dim_data": ("ONE_D",),
            "query_order_slice": ("TWO_D_OC",),
        }.get(action_name, ())


def test_data_type_follow_up_restores_indicator_and_builds_ready_task(
    tmp_path: Path,
) -> None:
    recognizer = MutableRecognizer()
    gateway = ObservationGateway(
        (SigmaObservationAvailabilityRow("TWO_D_OC", "Vib1", "runup"),)
    )
    client = _client(
        tmp_path,
        recognizer,
        gateway,
        slot_state=SlotState.from_values(
            {
                SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
                SlotRef("nvh.data_observation", "test_segments"): ["runup"],
            }
        ),
    )

    first = client.post("/turns", json={"session_id": "s1", "message": "show 48阶"})

    assert first.json()["plan"]["pending_task"] is None
    assert [
        (item["value"], item["label"])
        for item in first.json()["plan"]["prompts"][0]["candidates"]
    ] == [("ONE_D", "一维数据"), ("TWO_D_OC", "阶次切片")]

    recognizer.decision = _data_type_decision()
    second = client.post(
        "/turns",
        json={"session_id": "s1", "message": "TWO_D_OC"},
    )

    assert second.json()["plan"]["kind"] == "task"
    assert second.json()["plan"]["name"] == "query_order_slice"
    assert second.json()["plan"]["params"] == {
        "data_types": "TWO_D_OC",
        "sensors": ["Vib1"],
        "test_segments": ["runup"],
        "indicator_names": [
            {"name": "48阶", "index": "order-cut-48"},
        ],
    }


def test_data_type_follow_up_sets_pending_task_when_required_slots_are_missing(
    tmp_path: Path,
) -> None:
    recognizer = MutableRecognizer()
    client = _client(
        tmp_path,
        recognizer,
        ObservationGateway(),
        candidates={
            "sensors": ["Vib1", "Vib2"],
            "test_segments": ["runup", "coast"],
        },
    )

    first = client.post("/turns", json={"session_id": "s2", "message": "show 48阶"})
    assert first.json()["plan"]["kind"] == "clarify"

    recognizer.decision = _data_type_decision()
    second = client.post(
        "/turns",
        json={"session_id": "s2", "message": "TWO_D_OC"},
    )

    assert second.json()["plan"]["kind"] == "clarify"
    assert second.json()["plan"]["pending_task"] == "query_order_slice"
    assert set(second.json()["plan"]["missing_slots"]) == {
        "sensors",
        "test_segments",
    }


def _client(
    tmp_path: Path,
    recognizer: MutableRecognizer,
    gateway: ObservationGateway,
    *,
    slot_state: SlotState | None = None,
    candidates=None,
) -> TestClient:
    task_dir = tmp_path / "tasks"
    task_dir.mkdir()
    (task_dir / "observation.yaml").write_text(TASK_YAML, encoding="utf-8")
    runtime = create_synapse_runtime(
        recognizer=recognizer,
        slot_state=slot_state,
        task_config_dir=task_dir,
        candidates=candidates,
        candidate_catalog_loader=SigmaCandidateCatalogLoader(gateway),
    )
    return TestClient(create_app(turn_handler=runtime))


def _indicator_decision() -> IntentDecision:
    return IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.data_observation.indicator_query.list",
                score=0.95,
                slots=IntentSlot(entity_type="indicator", target="48阶"),
            ),
        ),
    )


def _data_type_decision() -> IntentDecision:
    return IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=(
            IntentMatch(
                name="task.nvh.context_management.switch_data_type",
                score=0.98,
                slots=IntentSlot(
                    action="replace",
                    entity_type="data_type",
                    target="TWO_D_OC",
                    slot_valid=True,
                ),
            ),
        ),
    )
