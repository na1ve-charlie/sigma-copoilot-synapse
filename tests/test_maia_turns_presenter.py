from __future__ import annotations

from maia.api import ReplyPlan
from maia.presentation import TurnPresenter


def test_turn_presenter_wraps_plan_without_internal_result_fields() -> None:
    response = TurnPresenter().present(ReplyPlan(message="Available sensors."))

    assert response.model_dump(mode="json") == {
        "plan": {
            "dataset": {},
            "kind": "reply",
            "message": "Available sensors.",
            "data": {},
            "suggestions": [],
            "slot_state_diff": {"changes": []},
        }
    }


def test_turn_presenter_accepts_plain_plan_mapping() -> None:
    response = TurnPresenter().present({"kind": "confirm", "reason": "risk", "message": "ok"})

    assert response.model_dump(mode="json") == {
        "plan": {
            "dataset": {},
            "kind": "confirm",
            "reason": "risk",
            "message": "ok",
            "payload": {},
            "slot_state_diff": {"changes": []},
        }
    }
