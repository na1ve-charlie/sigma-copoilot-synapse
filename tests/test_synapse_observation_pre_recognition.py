from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from synapse.engine import TurnContext
from synapse.recognition import (
    CANDIDATE_CATALOG_ARTIFACT,
    CandidateCatalog,
    CandidateItem,
)
from synapse.recognition.themis import ThemisRecognitionStep
from synapse.runtime import create_synapse_runtime
from synapse.turns import TurnRequest


class FakeCatalogLoader:
    async def load(self, request: TurnRequest) -> CandidateCatalog:
        return _catalog()


class FakeRecognizer:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.resolvers: list[Any | None] = []

    async def recognize(
        self,
        message: str,
        *,
        resolver: Any | None = None,
    ) -> Any:
        self.messages.append(message)
        self.resolvers.append(resolver)
        return SimpleNamespace(verdict="low")


def run(coro):
    return asyncio.run(coro)


def test_observation_preprocessor_normalizes_and_filters_candidates(tmp_path) -> None:
    recognizer = FakeRecognizer()
    runtime = create_synapse_runtime(
        recognizer=recognizer,
        candidate_catalog_loader=FakeCatalogLoader(),
        task_config_dir=_task_dir(tmp_path),
    )

    response = run(
        runtime.handle_turn(
            TurnRequest(
                session_id="s1",
                message="show frequency spectrum Seat X",
            )
        )
    )

    assert response.plan["kind"] == "reply"
    assert recognizer.messages == ["show frequency spectrum VibX"]
    assert run(recognizer.resolvers[0].resolve("sensor")) == [
        {"value": "VibX", "label": "Seat X"}
    ]
    assert run(recognizer.resolvers[0].resolve("data_type")) == [
        {"value": "TWO_D_FS", "label": "frequency spectrum"}
    ]
    assert run(recognizer.resolvers[0].resolve("indicator")) == [
        {"value": "Peak"}
    ]


def test_themis_uses_unfiltered_catalog_without_observation_match() -> None:
    recognizer = FakeRecognizer()
    context = TurnContext.from_request(
        TurnRequest(session_id="s1", message="show data")
    ).with_artifact(CANDIDATE_CATALOG_ARTIFACT, _catalog())

    run(ThemisRecognitionStep(recognizer).run(context))

    assert run(recognizer.resolvers[0].resolve("sensor")) == [
        {"value": "VibX", "label": "Seat X"},
        {"value": "VibY"},
    ]


def _catalog() -> CandidateCatalog:
    return CandidateCatalog.from_mapping(
        {
            "sensor": [
                CandidateItem("VibX", label="Seat X"),
                CandidateItem("VibY"),
            ],
            "sensors": [
                CandidateItem("VibX", label="Seat X"),
                CandidateItem("VibY"),
            ],
            "data_type": [
                CandidateItem(
                    "TWO_D_FS",
                    label="frequency spectrum",
                    metadata={"aliases": ("频谱",)},
                ),
                CandidateItem("TWO_D_TD", label="time domain"),
            ],
            "data_types": [
                CandidateItem(
                    "TWO_D_FS",
                    label="frequency spectrum",
                    metadata={"aliases": ("频谱",)},
                ),
                CandidateItem("TWO_D_TD", label="time domain"),
            ],
            "indicator": [
                CandidateItem(
                    "Peak",
                    metadata={"data_types": ["TWO_D_FS"], "sensors": ["VibX"]},
                ),
                CandidateItem(
                    "RMS",
                    metadata={"data_types": ["TWO_D_TD"], "sensors": ["VibY"]},
                ),
            ],
            "indicator_names": [
                CandidateItem(
                    "Peak",
                    metadata={"data_types": ["TWO_D_FS"], "sensors": ["VibX"]},
                ),
                CandidateItem(
                    "RMS",
                    metadata={"data_types": ["TWO_D_TD"], "sensors": ["VibY"]},
                ),
            ],
        }
    )


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


def test_observation_preprocessor_uses_candidate_metadata_aliases(tmp_path) -> None:
    recognizer = FakeRecognizer()
    runtime = create_synapse_runtime(
        recognizer=recognizer,
        candidate_catalog_loader=FakeCatalogLoader(),
        task_config_dir=_task_dir(tmp_path),
    )

    run(
        runtime.handle_turn(
            TurnRequest(
                session_id="s1",
                message="切换到 VibX 看频谱",
            )
        )
    )

    assert recognizer.messages == ["切换到 VibX 看频谱"]
