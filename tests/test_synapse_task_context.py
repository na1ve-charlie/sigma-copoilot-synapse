from __future__ import annotations

import asyncio

from synapse.engine import TurnContext
from synapse.session.task_context import (
    ACTIVE_TASK_ARTIFACT,
    ACTIVE_TASK_NAME_ARTIFACT,
    PENDING_TASK_ARTIFACT,
    InMemoryTaskContextStore,
    TaskContextCommitterStep,
    TaskContextLoaderStep,
    TaskContextState,
)
from synapse.turns import TurnRequest


def run(coro):
    return asyncio.run(coro)


def _context(session_id: str = "s1") -> TurnContext:
    return TurnContext.from_request(TurnRequest(session_id=session_id, message="show"))


def test_task_context_step_persists_pending_task_for_session() -> None:
    store = InMemoryTaskContextStore()
    step = TaskContextCommitterStep(store)

    updated = run(
        step.run(
            _context().with_plan(
                {
                    "kind": "clarify",
                    "pending_task": "query_frequency_spectrum",
                }
            )
        )
    )

    assert store.states["s1"] == TaskContextState(
        pending_task_name="query_frequency_spectrum"
    )
    assert updated.artifacts[PENDING_TASK_ARTIFACT] == "query_frequency_spectrum"


def test_task_context_step_persists_active_task_for_session() -> None:
    store = InMemoryTaskContextStore()
    step = TaskContextCommitterStep(store)

    updated = run(
        step.run(
            _context().with_plan(
                {
                    "kind": "task",
                    "status": "ready",
                    "name": "query_order_slice",
                }
            )
        )
    )

    assert store.states["s1"] == TaskContextState(active_task_name="query_order_slice")
    assert updated.artifacts[ACTIVE_TASK_NAME_ARTIFACT] == "query_order_slice"
    assert updated.artifacts[ACTIVE_TASK_ARTIFACT] == {"name": "query_order_slice"}


def test_task_context_step_preserves_active_task_when_pending_task_is_created() -> None:
    store = InMemoryTaskContextStore(
        {
            "s1": TaskContextState(active_task_name="query_one_dim_data"),
        }
    )
    step = TaskContextCommitterStep(store)

    updated = run(
        step.run(
            _context().with_plan(
                {
                    "kind": "clarify",
                    "pending_task": "query_frequency_spectrum",
                }
            )
        )
    )

    assert store.states["s1"] == TaskContextState(
        pending_task_name="query_frequency_spectrum",
        active_task_name="query_one_dim_data",
    )
    assert updated.artifacts[PENDING_TASK_ARTIFACT] == "query_frequency_spectrum"
    assert updated.artifacts[ACTIVE_TASK_NAME_ARTIFACT] == "query_one_dim_data"


def test_task_context_step_clears_pending_when_task_becomes_ready() -> None:
    store = InMemoryTaskContextStore(
        {
            "s1": TaskContextState(pending_task_name="query_frequency_spectrum"),
        }
    )
    step = TaskContextCommitterStep(store)

    run(
        step.run(
            _context().with_plan(
                {
                    "kind": "task",
                    "status": "ready",
                    "name": "query_frequency_spectrum",
                }
            )
        )
    )

    assert store.states["s1"] == TaskContextState(
        active_task_name="query_frequency_spectrum"
    )


def test_task_context_step_preserves_active_task_on_reply_plan() -> None:
    store = InMemoryTaskContextStore(
        {
            "s1": TaskContextState(active_task_name="query_frequency_spectrum"),
        }
    )
    step = TaskContextCommitterStep(store)

    updated = run(
        step.run(
            _context().with_plan(
                {
                    "kind": "reply",
                    "message": "当前可用候选如下。",
                }
            )
        )
    )

    assert store.states["s1"] == TaskContextState(
        active_task_name="query_frequency_spectrum"
    )
    assert updated.artifacts[ACTIVE_TASK_NAME_ARTIFACT] == "query_frequency_spectrum"
    assert updated.artifacts[ACTIVE_TASK_ARTIFACT] == {
        "name": "query_frequency_spectrum"
    }


def test_task_context_loader_projects_pending_and_active_artifacts() -> None:
    store = InMemoryTaskContextStore(
        {
            "s1": TaskContextState(
                pending_task_name="query_frequency_spectrum",
                active_task_name="query_order_slice",
            )
        }
    )
    updated = run(TaskContextLoaderStep(store).run(_context()))

    assert updated.artifacts[PENDING_TASK_ARTIFACT] == "query_frequency_spectrum"
    assert updated.artifacts[ACTIVE_TASK_NAME_ARTIFACT] == "query_order_slice"
    assert updated.artifacts[ACTIVE_TASK_ARTIFACT] == {"name": "query_order_slice"}


def test_task_context_step_clears_session_task_context_on_context_clear() -> None:
    store = InMemoryTaskContextStore(
        {
            "s1": TaskContextState(
                pending_task_name="query_frequency_spectrum",
                active_task_name="query_order_slice",
            )
        }
    )
    step = TaskContextCommitterStep(store)

    updated = run(step.run(_context().with_plan({"kind": "context_clear"})))

    assert store.states["s1"] == TaskContextState()
    assert PENDING_TASK_ARTIFACT not in updated.artifacts
    assert ACTIVE_TASK_ARTIFACT not in updated.artifacts
