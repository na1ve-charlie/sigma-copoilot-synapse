from __future__ import annotations

import asyncio

from synapse.engine import TurnContext
from synapse.recognition import (
    CANDIDATE_CATALOG_ARTIFACT,
    CandidateCatalog,
    CandidateCatalogStep,
    CandidateItem,
)
from synapse.turns import TurnRequest, WorkspaceContext


def run(coro):
    return asyncio.run(coro)


def test_candidate_catalog_returns_candidates_by_entity_type() -> None:
    catalog = CandidateCatalog.from_mapping(
        {
            "sensor": [
                CandidateItem(value="VibX", label="Seat X"),
                {"value": "VibY", "label": "Seat Y"},
            ],
            "indicator": ["RMS"],
        }
    )

    assert catalog.candidates_for_entity("sensor") == [
        CandidateItem(value="VibX", label="Seat X"),
        CandidateItem(value="VibY", label="Seat Y"),
    ]
    assert catalog.candidates_for_entity("indicator") == [
        CandidateItem(value="RMS")
    ]
    assert catalog.candidates_for_entity("unknown") == []


def test_candidate_catalog_themis_resolver_outputs_value_label_payloads() -> None:
    resolver = CandidateCatalog.from_mapping(
        {
            "sensor": [
                CandidateItem(value="VibX", label="Seat X"),
                CandidateItem(value="VibY"),
            ],
        }
    ).as_themis_resolver()

    assert run(resolver.resolve("sensor")) == [
        {"value": "VibX", "label": "Seat X"},
        {"value": "VibY"},
    ]
    assert run(resolver.resolve("test_segment")) == []


def test_candidate_catalog_step_loads_from_request_workspace_context() -> None:
    class FakeCatalogLoader:
        def __init__(self) -> None:
            self.workspace_contexts: list[WorkspaceContext | None] = []

        async def load(self, request: TurnRequest) -> CandidateCatalog:
            self.workspace_contexts.append(request.workspace_context)
            return CandidateCatalog.from_mapping({"sensor": ["VibX"]})

    loader = FakeCatalogLoader()
    context = TurnContext.from_request(
        TurnRequest(
            session_id="s1",
            message="show data",
            workspace_context={"dataset_id": "1152"},
        )
    )

    result = run(CandidateCatalogStep(loader).run(context))

    assert loader.workspace_contexts[0] is not None
    assert loader.workspace_contexts[0].dataset_id == "1152"
    catalog = result.artifacts[CANDIDATE_CATALOG_ARTIFACT]
    assert catalog.candidates_for_entity("sensor") == [CandidateItem("VibX")]
