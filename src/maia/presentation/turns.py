"""Turn presenter for the stable Maia public response wrapper."""

from __future__ import annotations

from pydantic import TypeAdapter

from maia.api import TurnPlan, TurnPlanMapping, TurnResponse


_TURN_PLAN_ADAPTER = TypeAdapter(TurnPlan)


class TurnPresenter:
    def present(self, plan: TurnPlan | TurnPlanMapping) -> TurnResponse:
        normalized = plan if isinstance(plan, dict) else plan.model_dump(mode="python")
        return TurnResponse(plan=_TURN_PLAN_ADAPTER.validate_python(normalized))


def present_turn(plan: TurnPlan | TurnPlanMapping) -> TurnResponse:
    return TurnPresenter().present(plan)


__all__ = ["TurnPresenter", "present_turn"]
