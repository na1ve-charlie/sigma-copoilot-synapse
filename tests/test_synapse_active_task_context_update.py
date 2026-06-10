from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from synapse.api.main import create_app
from synapse.recognition import CandidateCatalog
from synapse.runtime import create_synapse_runtime
from synapse.slots.contracts import SlotRef
from synapse.slots.state import SlotState
from synapse.turns import TurnRequest


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
"""


class FakeRecognizer:
    def __init__(
        self,
        intent_name: str,
        *,
        slot_operations: tuple[Any, ...] = (),
    ) -> None:
        self.intent_name = intent_name
        self.slot_operations = slot_operations

    async def recognize(
        self,
        message: str,
        *,
        resolver: Any | None = None,
    ) -> SimpleNamespace:
        del message, resolver
        intents = (
            [SimpleNamespace(name=self.intent_name)]
            if self.intent_name
            else []
        )
        return SimpleNamespace(
            verdict="clear",
            action_intents=intents,
            slot_operations=self.slot_operations,
        )


class FakeCatalogLoader:
    async def load(self, request: TurnRequest) -> CandidateCatalog:
        del request
        return CandidateCatalog.from_mapping(
            {
                "sensor": ["VibX", "VibY"],
                "sensors": ["VibX", "VibY"],
                "test_segments": ["run-1"],
                "indicator_names": ["RMS"],
            }
        )


def test_runtime_rebuilds_active_task_after_slot_only_context_update(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer("task.nvh.data_observation.batch.one_dim_data")
    client = TestClient(
        create_app(
            turn_handler=create_synapse_runtime(
                recognizer=recognizer,
                slot_state=SlotState.from_values(
                    {
                        SlotRef("nvh.data_observation", "sensors"): ["VibX"],
                        SlotRef("nvh.data_observation", "test_segments"): ["run-1"],
                        SlotRef("nvh.data_observation", "indicator_names"): ["RMS"],
                    }
                ),
                task_config_dir=_task_dir(tmp_path),
                candidate_catalog_loader=FakeCatalogLoader(),
            )
        )
    )

    first = client.post(
        "/turns",
        json={"session_id": "s1", "message": "show"},
    )

    assert first.status_code == 200
    assert first.json()["plan"]["kind"] == "task"
    assert first.json()["plan"]["name"] == "query_one_dim_data"
    assert first.json()["plan"]["params"] == {
        "sensors": ["VibX"],
        "test_segments": ["run-1"],
        "indicator_names": ["RMS"],
    }

    recognizer.intent_name = ""
    recognizer.slot_operations = (
        SimpleNamespace(
            action="switch",
            entity_type="sensor",
            target="VibY",
            slot_valid=True,
        ),
    )
    second = client.post(
        "/turns",
        json={"session_id": "s1", "message": "switch sensor"},
    )

    assert second.status_code == 200
    assert second.json()["plan"]["kind"] == "task"
    assert second.json()["plan"]["name"] == "query_one_dim_data"
    assert second.json()["plan"]["params"] == {
        "sensors": ["VibY"],
        "test_segments": ["run-1"],
        "indicator_names": ["RMS"],
    }
    assert second.json()["plan"]["slot_state_diff"] == {
        "changes": [
            {
                "slot": "sensors",
                "label": "传感器",
                "before": ["VibX"],
                "after": ["VibY"],
            }
        ]
    }


def test_runtime_returns_context_update_without_active_task(
    tmp_path: Path,
) -> None:
    recognizer = FakeRecognizer(
        "",
        slot_operations=(
            SimpleNamespace(
                action="switch",
                entity_type="sensor",
                target="VibY",
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
    assert response.json()["plan"]["projected_slots"] == {"sensors": ["VibY"]}
    assert response.json()["plan"]["slot_state_diff"] == {
        "changes": [
            {
                "slot": "sensors",
                "label": "传感器",
                "before": None,
                "after": ["VibY"],
            }
        ]
    }


def _task_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "data_observation.yaml").write_text(TASK_YAML, encoding="utf-8")
    return path
