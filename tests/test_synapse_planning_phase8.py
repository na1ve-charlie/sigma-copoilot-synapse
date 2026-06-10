from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from synapse.engine import SynapseConductor, TurnContext
from synapse.planning.plans import (
    ClarifyPlan,
    Prompt,
    PromptCandidate,
    TaskPlan,
)
from synapse.planning.planner import PlanningContext, PlanningStep
from synapse.slots.contracts import SlotRef
from synapse.slots.state import SlotState
from synapse.turns import TurnRequest


SENSOR = SlotRef("observation", "sensor")


def req(message: str = "show spectrum") -> TurnRequest:
    return TurnRequest(session_id="s1", message=message)


@dataclass
class FakeBuilder:
    seen: list[PlanningContext] = field(default_factory=list)

    async def build(self, context: PlanningContext) -> TaskPlan:
        self.seen.append(context)
        return TaskPlan(
            status="ready",
            name="query_one_dim_data",
            title="Query one dim data",
            risk_level="low",
            requires_confirmation=False,
            params={"sensor": context.slot_state.get(SENSOR)},
            message=f"ready for {context.request.session_id}",
        )


class SeedArtifactsStep:
    async def run(self, context: TurnContext) -> TurnContext:
        return (
            context.with_artifact("intent_decision", {"verdict": "clear"})
            .with_artifact("slot_state", SlotState.from_values({SENSOR: "S1"}))
        )


def test_planning_step_consumes_committed_slot_state() -> None:
    builder = FakeBuilder()
    conductor = SynapseConductor([SeedArtifactsStep(), PlanningStep(builder)])

    response = asyncio.run(conductor.handle_turn(req()))

    assert response.plan["kind"] == "task"
    assert response.plan["params"] == {"sensor": "S1"}
    assert response.plan["slot_state_diff"] == {"changes": []}
    assert builder.seen[0].request == req()
    assert builder.seen[0].decision == {"verdict": "clear"}
    assert builder.seen[0].slot_state.get(SENSOR) == "S1"


def test_planning_step_requires_committed_slot_state() -> None:
    builder = FakeBuilder()
    context = TurnContext.from_request(req()).with_artifact(
        "intent_decision",
        {"verdict": "clear"},
    )

    with pytest.raises(KeyError, match="slot_state"):
        asyncio.run(PlanningStep(builder).run(context))


def test_clarify_plan_requires_prompts_for_missing_and_invalid_slots() -> None:
    sensor_prompt = Prompt(
        id="sensors",
        target="slot",
        label="Sensors",
        message="Select available sensors.",
        required=True,
        input_type="multi_select",
        candidates=[PromptCandidate(value="VibX", label="VibX")],
    )

    plan = ClarifyPlan(
        reason="invalid_slots",
        message="Invalid parameter.",
        invalid_slots=["sensors"],
        prompts=[sensor_prompt],
    )

    assert plan.model_dump(mode="json")["slot_state_diff"] == {"changes": []}

    with pytest.raises(ValueError, match="clarify slots require prompts"):
        ClarifyPlan(
            reason="missing_slots",
            message="Select sensors.",
            missing_slots=["sensors"],
        )
