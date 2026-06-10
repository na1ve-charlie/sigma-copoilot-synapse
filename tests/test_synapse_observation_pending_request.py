from __future__ import annotations

import asyncio

from synapse.domains.observation.pending_request import (
    CLEAR_PENDING_OBSERVATION_REQUEST_ARTIFACT,
    PENDING_OBSERVATION_REQUEST_ARTIFACT,
    InMemoryPendingObservationRequestStore,
    PendingObservationRequest,
    PendingObservationRequestCommitterStep,
    PendingObservationRequestLoaderStep,
)
from synapse.engine import TurnContext
from synapse.turns import TurnRequest


def test_pending_observation_request_is_isolated_by_session_and_cleared() -> None:
    request = PendingObservationRequest(
        indicator_name="48阶",
        candidate_data_types=("ONE_D", "TWO_D_OC"),
        intent_name="task.nvh.data_observation.indicator_query.list",
        score=0.95,
    )
    store = InMemoryPendingObservationRequestStore()
    committer = PendingObservationRequestCommitterStep(store)
    loader = PendingObservationRequestLoaderStep(store)

    run(
        committer.run(
            _context("s1")
            .with_artifact(PENDING_OBSERVATION_REQUEST_ARTIFACT, request)
            .with_plan({"kind": "clarify"})
        )
    )

    assert run(loader.run(_context("s1"))).artifacts[
        PENDING_OBSERVATION_REQUEST_ARTIFACT
    ] == request
    assert PENDING_OBSERVATION_REQUEST_ARTIFACT not in run(
        loader.run(_context("s2"))
    ).artifacts

    run(
        committer.run(
            _context("s1")
            .with_artifact(CLEAR_PENDING_OBSERVATION_REQUEST_ARTIFACT, True)
            .with_plan({"kind": "task", "status": "ready"})
        )
    )

    assert PENDING_OBSERVATION_REQUEST_ARTIFACT not in run(
        loader.run(_context("s1"))
    ).artifacts


def test_context_clear_removes_pending_observation_request() -> None:
    request = PendingObservationRequest(
        indicator_name="48阶",
        candidate_data_types=("ONE_D", "TWO_D_OC"),
        intent_name="task.nvh.data_observation.indicator_query.list",
        score=0.95,
    )
    store = InMemoryPendingObservationRequestStore({"s1": request})

    run(
        PendingObservationRequestCommitterStep(store).run(
            _context("s1").with_plan({"kind": "context_clear"})
        )
    )

    assert store.get("s1") is None


def _context(session_id: str) -> TurnContext:
    return TurnContext.from_request(TurnRequest(session_id=session_id, message="show"))


def run(coro):
    return asyncio.run(coro)
