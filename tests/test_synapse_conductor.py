from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from synapse.api.main import create_app
from synapse.engine import SynapseConductor, TurnContext
from synapse.turns import TurnRequest


def run(coro):
    return asyncio.run(coro)


class RecordingStep:
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        plan: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.calls = calls
        self.plan = plan

    async def run(self, context: TurnContext) -> TurnContext:
        self.calls.append(self.name)
        context = context.with_artifact(self.name, context.message)
        if self.plan is not None:
            return context.with_plan(self.plan)
        return context


class PostPlanStep(RecordingStep):
    run_after_plan = True


def test_conductor_runs_injected_steps_in_order() -> None:
    calls: list[str] = []
    conductor = SynapseConductor(
        [
            RecordingStep("pre_recognition", calls),
            RecordingStep("recognition", calls),
            RecordingStep("slot_resolution", calls),
            RecordingStep("planner", calls, plan={"kind": "reply", "message": "ok"}),
        ]
    )

    response = run(
        conductor.handle_turn(TurnRequest(session_id="s1", message="hello"))
    )

    assert calls == ["pre_recognition", "recognition", "slot_resolution", "planner"]
    assert response.plan == {"kind": "reply", "message": "ok"}


def test_conductor_stops_when_a_step_returns_plan() -> None:
    calls: list[str] = []
    conductor = SynapseConductor(
        [
            RecordingStep("clarify", calls, plan={"kind": "clarify"}),
            RecordingStep("planner", calls, plan={"kind": "reply"}),
        ]
    )

    response = run(
        conductor.handle_turn(TurnRequest(session_id="s1", message="hello"))
    )

    assert calls == ["clarify"]
    assert response.plan == {"kind": "clarify"}


def test_conductor_runs_post_plan_steps_marked_for_after_plan() -> None:
    calls: list[str] = []
    conductor = SynapseConductor(
        [
            RecordingStep("planner", calls, plan={"kind": "reply"}),
            PostPlanStep("post_plan", calls),
        ]
    )

    context = run(conductor.run(TurnRequest(session_id="s1", message="hello")))

    assert calls == ["planner", "post_plan"]
    assert context.plan == {"kind": "reply"}
    assert context.artifacts["post_plan"] == "hello"


def test_conductor_can_be_injected_into_turns_api() -> None:
    conductor = SynapseConductor(
        [RecordingStep("planner", [], plan={"kind": "reply", "message": "ok"})]
    )
    client = TestClient(create_app(turn_handler=conductor))

    response = client.post(
        "/turns",
        json={"session_id": "s1", "message": "hello"},
    )

    assert response.status_code == 200
    assert response.json() == {"plan": {"kind": "reply", "message": "ok"}}


def test_conductor_does_not_import_domain_implementations() -> None:
    source = Path("src/synapse/engine/conductor.py").read_text(encoding="utf-8")

    assert "synapse.domains" not in source
