from __future__ import annotations

import asyncio
from types import SimpleNamespace

from synapse.planning.planner import PlanningContext
from synapse.planning.tasks import TaskCatalog, TaskPlanBuilder
from synapse.recognition import CANDIDATE_CATALOG_ARTIFACT, CandidateCatalog
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


def ctx(state: SlotState) -> PlanningContext:
    return PlanningContext(
        request=TurnRequest(session_id="s1", message="show"),
        decision=SimpleNamespace(action_intents=({"name": INTENT},)),
        slot_state=state,
    )


def build(state: SlotState):
    builder = TaskPlanBuilder(
        catalog(),
        candidates={"test_segments": ("runup",), "indicator_names": ("RMS",)},
    )
    return asyncio.run(builder.build(ctx(state)))


def test_task_builder_returns_clarify_for_missing_required_slots() -> None:
    plan = build(SlotState.from_values({SENSOR: ["VibX"]}))

    assert plan.kind == "clarify"
    assert plan.message == "缺少必填任务参数。"
    assert plan.missing_slots == ["test_segments", "indicator_names"]
    assert [prompt.model_dump(mode="json") for prompt in plan.prompts] == [
        {
            "id": "test_segments",
            "target": "slot",
            "label": "测试段",
            "message": "请选择测试段。",
            "required": True,
            "input_type": "multi_select",
            "candidates": [
                {
                    "value": "runup",
                    "label": "runup",
                    "description": None,
                    "disabled": False,
                }
            ],
        },
        {
            "id": "indicator_names",
            "target": "slot",
            "label": "指标名",
            "message": "请选择指标名。",
            "required": True,
            "input_type": "multi_select",
            "candidates": [
                {
                    "value": "RMS",
                    "label": "RMS",
                    "description": None,
                    "disabled": False,
                }
            ],
        },
    ]


def test_task_builder_uses_candidate_catalog_artifact_for_prompts() -> None:
    builder = TaskPlanBuilder(catalog())
    state = SlotState.from_values({SENSOR: ["VibX"]})
    planning = ctx(state)
    planning = PlanningContext(
        request=planning.request,
        decision=planning.decision,
        slot_state=planning.slot_state,
        artifacts={
            CANDIDATE_CATALOG_ARTIFACT: CandidateCatalog.from_mapping(
                {
                    "test_segments": ["runup"],
                    "indicator_names": [{"value": "RMS", "label": "RMS"}],
                }
            )
        },
    )

    plan = asyncio.run(builder.build(planning))

    assert plan.kind == "clarify"
    assert [item.value for item in plan.prompts[0].candidates] == ["runup"]
    assert [item.value for item in plan.prompts[1].candidates] == ["RMS"]


def test_task_builder_returns_task_when_required_slots_are_ready() -> None:
    plan = build(
        SlotState.from_values(
            {
                SENSOR: ["VibX"],
                SEGMENT: ["runup"],
                INDICATOR: ["RMS"],
            }
        )
    )

    assert plan.kind == "task"
    assert plan.status == "ready"
    assert plan.message == "任务已就绪：Query one dim data"
    assert plan.params == {
        "sensors": ["VibX"],
        "test_segments": ["runup"],
        "indicator_names": ["RMS"],
    }


def test_task_builder_merges_ready_task_params_from_generic_provider() -> None:
    class StaticTaskParamProvider:
        def params_for(self, task, context):
            assert task.name == "query_one_dim_data"
            assert context.slot_state.values[SENSOR] == ["VibX"]
            return {"extra_param": "injected"}

    plan = asyncio.run(
        TaskPlanBuilder(
            catalog(),
            task_param_providers=(StaticTaskParamProvider(),),
        ).build(
            ctx(
                SlotState.from_values(
                    {
                        SENSOR: ["VibX"],
                        SEGMENT: ["runup"],
                        INDICATOR: ["RMS"],
                    }
                )
            )
        )
    )

    assert plan.kind == "task"
    assert plan.message == "任务已就绪：Query one dim data"
    assert plan.params == {
        "sensors": ["VibX"],
        "test_segments": ["runup"],
        "indicator_names": ["RMS"],
        "extra_param": "injected",
    }


def test_task_builder_returns_context_update_from_committed_slot_diff() -> None:
    state = SlotState.from_values({SENSOR: ["VibX"]})
    plan = asyncio.run(
        TaskPlanBuilder(catalog()).build(
            PlanningContext(
                request=TurnRequest(session_id="s1", message="switch"),
                decision=SimpleNamespace(action_intents=()),
                slot_state=state,
                slot_state_diff=SlotState().diff(state),
            )
        )
    )

    assert plan.kind == "context_update"
    assert plan.model_dump(mode="json") == {
        "kind": "context_update",
        "message": "已更新当前上下文。",
        "projected_slots": {"sensors": ["VibX"]},
        "slot_state_diff": {
            "changes": [
                {
                    "slot": "sensors",
                    "label": "传感器",
                    "before": None,
                    "after": ["VibX"],
                }
            ]
        },
    }


def test_task_builder_rebuilds_active_task_from_committed_slot_diff() -> None:
    before = SlotState.from_values(
        {
            SENSOR: ["VibX"],
            SEGMENT: ["runup"],
            INDICATOR: ["RMS"],
        }
    )
    after = SlotState.from_values(
        {
            SENSOR: ["VibY"],
            SEGMENT: ["runup"],
            INDICATOR: ["RMS"],
        }
    )
    plan = asyncio.run(
        TaskPlanBuilder(catalog()).build(
            PlanningContext(
                request=TurnRequest(session_id="s1", message="switch"),
                decision=SimpleNamespace(action_intents=()),
                slot_state=after,
                slot_state_diff=before.diff(after),
                artifacts={"active_task": {"name": "query_one_dim_data"}},
            )
        )
    )

    assert plan.kind == "task"
    assert plan.name == "query_one_dim_data"
    assert plan.status == "ready"
    assert plan.params == {
        "sensors": ["VibY"],
        "test_segments": ["runup"],
        "indicator_names": ["RMS"],
    }
    assert plan.model_dump(mode="json")["slot_state_diff"] == {
        "changes": [
            {
                "slot": "sensors",
                "label": "传感器",
                "before": ["VibX"],
                "after": ["VibY"],
            }
        ]
    }


def test_task_builder_returns_context_clear_for_clear_context_intent() -> None:
    before = SlotState.from_values({SENSOR: ["VibX"]})
    after = SlotState()
    plan = asyncio.run(
        TaskPlanBuilder(catalog()).build(
            PlanningContext(
                request=TurnRequest(session_id="s1", message="clear"),
                decision=SimpleNamespace(
                    action_intents=(
                        {"name": "task.nvh.context_management.clear_context"},
                    )
                ),
                slot_state=after,
                slot_state_diff=before.diff(after),
            )
        )
    )

    assert plan.kind == "context_clear"
    assert plan.message == "已清空当前上下文。"
    assert plan.slot_state_diff.changes[0].slot == "sensors"


def test_task_builder_replies_to_resolver_query_from_catalog() -> None:
    plan = asyncio.run(
        TaskPlanBuilder(catalog()).build(
            PlanningContext(
                request=TurnRequest(session_id="s1", message="list"),
                decision=SimpleNamespace(
                    action_intents=(
                        {"name": "inquiry.nvh.resolver_query.sensors"},
                    )
                ),
                slot_state=SlotState(),
                artifacts={
                    CANDIDATE_CATALOG_ARTIFACT: CandidateCatalog.from_mapping(
                        {"sensors": ["VibX"]}
                    )
                },
            )
        )
    )

    assert plan.kind == "reply"
    assert plan.message == "当前可用传感器如下。"
    assert plan.data == {"slot_name": "sensors", "candidates": ["VibX"]}
    assert plan.suggestions == ["VibX"]


def test_task_builder_replies_with_current_context() -> None:
    plan = asyncio.run(
        TaskPlanBuilder(catalog()).build(
            PlanningContext(
                request=TurnRequest(session_id="s1", message="current"),
                decision=SimpleNamespace(
                    action_intents=(
                        {"name": "inquiry.nvh.context_management.current"},
                    )
                ),
                slot_state=SlotState.from_values({SENSOR: ["VibX"]}),
            )
        )
    )

    assert plan.kind == "reply"
    assert plan.message == "当前上下文如下。"
    assert plan.data == {"slots": {"sensors": ["VibX"]}}


def test_task_builder_replies_in_chinese_when_no_task_matches() -> None:
    plan = asyncio.run(
        TaskPlanBuilder(catalog()).build(
            PlanningContext(
                request=TurnRequest(session_id="s1", message="noop"),
                decision=SimpleNamespace(action_intents=()),
                slot_state=SlotState(),
            )
        )
    )

    assert plan.kind == "reply"
    assert plan.message == "未匹配到可执行任务。"


def test_task_catalog_loads_yaml(tmp_path) -> None:
    path = tmp_path / "tasks.yaml"
    path.write_text(
        """
query_one_dim_data:
  intent_names:
    - task.nvh.data_observation.batch.one_dim_data
  title: Query one dim data
  risk_level: low
  requires_confirmation: false
  required_slots:
    - sensors
  optional_slots: []
""",
        encoding="utf-8",
    )

    task = TaskCatalog.from_yaml(path).match([INTENT])

    assert task is not None
    assert task.name == "query_one_dim_data"


def test_task_catalog_loads_directory(tmp_path) -> None:
    first = tmp_path / "first.yaml"
    first.write_text(
        """
query_one_dim_data:
  intent_names:
    - task.nvh.data_observation.batch.one_dim_data
  title: Query one dim data
  risk_level: low
  requires_confirmation: false
  required_slots: []
  optional_slots: []
""",
        encoding="utf-8",
    )
    second = tmp_path / "second.yaml"
    second.write_text(
        """
query_frequency_spectrum:
  intent_names:
    - task.nvh.data_observation.batch.frequency_spectrum
  title: Query frequency spectrum
  risk_level: low
  requires_confirmation: false
  required_slots: []
  optional_slots: []
""",
        encoding="utf-8",
    )

    task = TaskCatalog.from_directory(tmp_path).match(
        ["task.nvh.data_observation.batch.frequency_spectrum"]
    )

    assert task is not None
    assert task.name == "query_frequency_spectrum"
