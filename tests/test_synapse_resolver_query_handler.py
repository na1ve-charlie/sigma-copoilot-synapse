import asyncio
from pathlib import Path
from types import SimpleNamespace

from synapse.planning.planner import PlanningContext
from synapse.planning.plans import ReplyPlan
from synapse.planning.tasks import TaskCatalog, TaskPlanBuilder
from synapse.recognition import CANDIDATE_CATALOG_ARTIFACT, CandidateCatalog
from synapse.slots.contracts import SlotRef
from synapse.slots.state import SlotState
from synapse.turns import TurnRequest


TASK_INTENT = "task.example.ready"
QUERY_INTENT = "inquiry.nvh.resolver_query.sensors"


class Handler:
    def __init__(self, plan):
        self.plan = plan
        self.calls = []

    async def build(self, context, intent_names):
        self.calls.append((context, intent_names))
        return self.plan


def test_task_builder_delegates_resolver_query_to_injected_handler() -> None:
    handler = Handler(ReplyPlan(message="delegated", data={"handled": True}))

    plan = build((QUERY_INTENT,), handler=handler)

    assert plan.kind == "reply"
    assert plan.data == {"handled": True}
    assert [item[1] for item in handler.calls] == [(QUERY_INTENT,)]


def test_resolver_query_handler_none_does_not_block_task_planning() -> None:
    handler = Handler(None)
    state = SlotState.from_values({SlotRef("example", "required_slot"): "ready"})

    plan = build((QUERY_INTENT, TASK_INTENT), state=state, handler=handler)

    assert plan.kind == "task"
    assert plan.name == "ready_task"
    assert [item[1] for item in handler.calls] == [(QUERY_INTENT, TASK_INTENT)]


def test_default_resolver_query_handler_keeps_single_entity_behavior() -> None:
    artifacts = {
        CANDIDATE_CATALOG_ARTIFACT: CandidateCatalog.from_mapping(
            {"sensors": ["VibX"]}
        )
    }

    plan = build(
        (QUERY_INTENT,),
        artifacts=artifacts,
    )

    assert plan.kind == "reply"
    assert plan.data == {"slot_name": "sensors", "candidates": ["VibX"]}
    assert plan.suggestions == ["VibX"]


def test_planner_sources_do_not_embed_observation_constants() -> None:
    forbidden = ("sensors", "test_segments", "indicator_names", "TWO_D_FS", "TWO_D_OC")
    paths = (Path("src/synapse/planning/tasks.py"), Path("src/synapse/planning/resolver_query.py"))

    assert not {
        token
        for path in paths
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    }


def build(
    intents,
    *,
    state=None,
    artifacts=None,
    handler=None,
):
    return asyncio.run(
        TaskPlanBuilder(catalog(), resolver_query_handler=handler).build(
            PlanningContext(
                request=TurnRequest(session_id="s1", message="query"),
                decision=SimpleNamespace(
                    action_intents=({"name": name} for name in intents)
                ),
                slot_state=state or SlotState(),
                artifacts=artifacts or {},
            )
        )
    )


def catalog() -> TaskCatalog:
    return TaskCatalog.from_mapping(
        {
            "ready_task": {
                "intent_names": (TASK_INTENT,),
                "risk_level": "low",
                "title": "Ready task",
                "requires_confirmation": False,
                "required_slots": ("required_slot",),
                "optional_slots": (),
            }
        }
    )
