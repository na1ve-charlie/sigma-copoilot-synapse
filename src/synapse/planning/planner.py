from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from synapse.engine import TurnContext
from synapse.planning.plans import Plan
from synapse.slots.state import SlotState, SlotStateDiff
from synapse.turns import TurnRequest


DECISION_ARTIFACT = "intent_decision"
SLOT_STATE_ARTIFACT = "slot_state"
SLOT_STATE_DIFF_ARTIFACT = "slot_state_diff"


@dataclass(frozen=True, slots=True)
class PlanningContext:
    """Input available to an application-level plan builder."""

    request: TurnRequest
    decision: Any
    slot_state: SlotState
    slot_state_diff: SlotStateDiff = field(
        default_factory=lambda: SlotState().diff(SlotState())
    )
    artifacts: Mapping[str, Any] = field(default_factory=dict)


class PlanBuilder(Protocol):
    async def build(self, context: PlanningContext) -> Plan:
        ...


class PlanningStep:
    """Conductor adapter for application-level planning."""

    def __init__(self, builder: PlanBuilder) -> None:
        self._builder = builder

    async def run(self, context: TurnContext) -> TurnContext:
        planning = PlanningContext(
            request=context.request,
            decision=_required_artifact(context, DECISION_ARTIFACT),
            slot_state=_required_slot_state(context, SLOT_STATE_ARTIFACT),
            slot_state_diff=_slot_state_diff(context),
            artifacts=context.artifacts,
        )
        plan = await self._builder.build(planning)
        return context.with_plan(plan.model_dump(mode="json"))


def _required_artifact(context: TurnContext, key: str) -> Any:
    if key not in context.artifacts:
        raise KeyError(f"missing required planning artifact: {key}")
    return context.artifacts[key]


def _required_slot_state(context: TurnContext, key: str) -> SlotState:
    value = _required_artifact(context, key)
    if not isinstance(value, SlotState):
        raise TypeError(f"planning artifact {key} must be SlotState")
    return value


def _slot_state_diff(context: TurnContext) -> SlotStateDiff:
    value = context.artifacts.get(SLOT_STATE_DIFF_ARTIFACT)
    if value is None:
        return SlotState().diff(SlotState())
    if not isinstance(value, SlotStateDiff):
        raise TypeError(
            f"planning artifact {SLOT_STATE_DIFF_ARTIFACT} must be SlotStateDiff"
        )
    return value
