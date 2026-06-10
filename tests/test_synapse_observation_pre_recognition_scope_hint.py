from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from synapse.recognition import CandidateCatalog, CandidateItem
from synapse.runtime import create_synapse_runtime
from synapse.turns import TurnRequest


class FakeCatalogLoader:
    async def load(self, request: TurnRequest) -> CandidateCatalog:
        return CandidateCatalog.from_mapping(
            {
                "indicator": [
                    CandidateItem("Peak", metadata={"data_types": ["TWO_D_FS"]}),
                ],
                "indicator_names": [
                    CandidateItem("Peak", metadata={"data_types": ["TWO_D_FS"]}),
                ],
                "data_type": [
                    CandidateItem("TWO_D_FS", label="frequency spectrum"),
                ],
                "data_types": [
                    CandidateItem("TWO_D_FS", label="frequency spectrum"),
                ],
            }
        )


class RecordingRecognizer:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def recognize(
        self,
        message: str,
        *,
        resolver: Any | None = None,
    ) -> Any:
        self.messages.append(message)
        return SimpleNamespace(verdict="low")


def test_unique_indicator_message_gets_data_type_hint_before_recognition(
    tmp_path,
) -> None:
    recognizer = RecordingRecognizer()
    runtime = create_synapse_runtime(
        recognizer=recognizer,
        candidate_catalog_loader=FakeCatalogLoader(),
        task_config_dir=_task_dir(tmp_path),
    )

    asyncio.run(
        runtime.handle_turn(TurnRequest(session_id="s1", message="show Peak"))
    )

    assert recognizer.messages == ["TWO_D_FS show Peak"]


def _task_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "tasks.yaml").write_text(
        "noop:\n"
        "  intent_names: [noop]\n"
        "  title: Noop\n"
        "  risk_level: low\n"
        "  requires_confirmation: false\n"
        "  required_slots: []\n"
        "  optional_slots: []\n",
        encoding="utf-8",
    )
    return path
