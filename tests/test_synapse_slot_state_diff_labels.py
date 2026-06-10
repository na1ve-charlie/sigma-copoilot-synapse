from __future__ import annotations

import asyncio
from types import SimpleNamespace

from synapse.planning.planner import PlanningContext
from synapse.planning.tasks import TaskCatalog, TaskPlanBuilder
from synapse.slots.contracts import SlotRef
from synapse.slots.state import SlotState
from synapse.turns import TurnRequest


SENSOR = SlotRef("observation", "sensors")
SEGMENT = SlotRef("observation", "test_segments")
INDICATOR = SlotRef("observation", "indicator_names")
INTENT = "task.nvh.data_observation.batch.one_dim_data"


def catalog() -> TaskCatalog:
    return TaskCatalog.from_mapping(
        {
            "query_one_dim_data": {
                "intent_names": (INTENT,),
                "title": "Query one dim data",
                "risk_level": "low",
                "requires_confirmation": False,
                "required_slots": ("sensors", "test_segments", "indicator_names"),
                "optional_slots": (),
            }
        }
    )


def test_context_update_diff_includes_chinese_labels() -> None:
    state = SlotState.from_values({SENSOR: ["Vib1"]})

    plan = asyncio.run(
        TaskPlanBuilder(TaskCatalog.from_mapping({})).build(
            PlanningContext(
                request=TurnRequest(session_id="s1", message="switch sensor"),
                decision=SimpleNamespace(action_intents=()),
                slot_state=state,
                slot_state_diff=SlotState().diff(state),
            )
        )
    )

    assert plan.kind == "context_update"
    assert plan.model_dump(mode="json")["slot_state_diff"] == {
        "changes": [
            {
                "slot": "sensors",
                "label": "传感器",
                "before": None,
                "after": ["Vib1"],
            }
        ]
    }


def test_task_plan_diff_includes_chinese_labels_for_autofilled_slot() -> None:
    before = SlotState.from_values(
        {
            SENSOR: ["Vib1"],
            SEGMENT: ["runup"],
        }
    )
    after = SlotState.from_values(
        {
            SENSOR: ["Vib1"],
            SEGMENT: ["runup"],
            INDICATOR: ["RMS"],
        }
    )

    plan = asyncio.run(
        TaskPlanBuilder(catalog()).build(
            PlanningContext(
                request=TurnRequest(session_id="s1", message="show"),
                decision=SimpleNamespace(action_intents=({"name": INTENT},)),
                slot_state=after,
                slot_state_diff=before.diff(after),
            )
        )
    )

    assert plan.kind == "task"
    assert plan.model_dump(mode="json")["slot_state_diff"] == {
        "changes": [
            {
                "slot": "indicator_names",
                "label": "指标名",
                "before": None,
                "after": ["RMS"],
            }
        ]
    }


def test_empty_slot_state_diff_remains_empty() -> None:
    state = SlotState.from_values(
        {
            SENSOR: ["Vib1"],
            SEGMENT: ["runup"],
            INDICATOR: ["RMS"],
        }
    )

    plan = asyncio.run(
        TaskPlanBuilder(catalog()).build(
            PlanningContext(
                request=TurnRequest(session_id="s1", message="show"),
                decision=SimpleNamespace(action_intents=({"name": INTENT},)),
                slot_state=state,
            )
        )
    )

    assert plan.kind == "task"
    assert plan.model_dump(mode="json")["slot_state_diff"] == {"changes": []}
