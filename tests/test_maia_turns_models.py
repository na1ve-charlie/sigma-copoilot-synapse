from __future__ import annotations

from pathlib import Path

import yaml

from maia.api import (
    ClarifyPlan,
    ConfirmPlan,
    ContextClearPlan,
    ContextUpdatePlan,
    Prompt,
    PromptCandidate,
    ReplyPlan,
    SlotStateChange,
    SlotStateDiff,
    TaskPlan,
    TurnRequest,
    TurnResponse,
)


SAMPLES_PATH = Path("configs/maia/testdata/turns_response_samples.yaml")


def test_turn_request_dump_keeps_only_lang_in_maia_workspace_context() -> None:
    payload = {
        "session_id": "sess-001",
        "message": "show S1",
        "workspace_context": {"lang": "zh"},
    }

    maia = TurnRequest(**payload)

    assert maia.model_dump(mode="json") == payload
    assert maia.workspace_context is not None
    assert maia.workspace_context.lang == "zh"


def test_turn_plan_dumps_include_public_dataset_shape() -> None:
    slot_state_diff = SlotStateDiff(
        changes=[
            SlotStateChange(
                slot="sensors",
                label="Sensor",
                before=["S1"],
                after=["S2"],
            )
        ]
    )

    maia_plans = [
        ReplyPlan(message="Available sensors.", slot_state_diff=slot_state_diff),
        ClarifyPlan(
            reason="missing_slots",
            message="Please choose a sensor.",
            pending_task="task.nvh.data_observation.view_indicator_result",
            missing_slots=["sensors"],
            prompts=[
                Prompt(
                    id="sensors",
                    target="slot",
                    label="Sensor",
                    message="Select one sensor.",
                    required=True,
                    input_type="single_select",
                    candidates=[PromptCandidate(value="S1", label="Seat 1")],
                )
            ],
            slot_state_diff=slot_state_diff,
        ),
        TaskPlan(
            status="ready",
            name="task.nvh.data_observation.view_indicator_result",
            intent="task.nvh.data_observation.view_indicator_result",
            title="View indicator result",
            risk_level="low",
            requires_confirmation=False,
            params={"sensors": ["S2"]},
            message="Task is ready.",
            slot_state_diff=slot_state_diff,
        ),
        ConfirmPlan(
            reason="high_risk_operation",
            message="Confirm deletion.",
            payload={"record_count": 3},
            slot_state_diff=slot_state_diff,
        ),
        ContextUpdatePlan(
            message="Context updated.",
            projected_slots={"sensors": ["S2"]},
            slot_state_diff=slot_state_diff,
        ),
        ContextClearPlan(
            message="Context cleared.",
            preserved=["workspace_context"],
            cleared=["sensors"],
            slot_state_diff=slot_state_diff,
        ),
    ]

    dumped = [plan.model_dump(mode="json") for plan in maia_plans]

    assert [item["kind"] for item in dumped] == [
        "reply",
        "clarify",
        "task",
        "confirm",
        "context_update",
        "context_clear",
    ]
    assert all(item["dataset"] == {} for item in dumped)
    assert dumped[2]["intent"] == "task.nvh.data_observation.view_indicator_result"
    assert "data" not in dumped[2]


def test_turn_response_model_validates_g06_public_samples() -> None:
    samples = yaml.safe_load(SAMPLES_PATH.read_text(encoding="utf-8"))

    for case in samples["cases"]:
        response = TurnResponse.model_validate(case["response"])
        assert list(response.model_dump(mode="json")) == ["plan"]
        assert response.plan.kind == case["response"]["plan"]["kind"]


def test_clarify_plan_allows_text_slot_prompt_without_candidates() -> None:
    plan = ClarifyPlan(
        reason="missing_slots",
        message="Provide a file name.",
        missing_slots=["file_name"],
        prompts=[
            Prompt(
                id="file_name",
                target="slot",
                label="file name",
                message="Provide a file name.",
                required=True,
                input_type="text",
            )
        ],
    )

    assert plan.prompts[0].candidates == []


def test_task_plan_accepts_submitted_status() -> None:
    plan = TaskPlan(
        status="submitted",
        name="task.nvh.excel_export",
        intent="task.nvh.excel_export",
        title="Excel export",
        risk_level="medium",
        requires_confirmation=True,
        message="Excel export submitted.",
        data={"code": 200, "data": ["http://example.com/report.xlsx"]},
    )

    assert plan.status == "submitted"
    assert plan.data["code"] == 200
    assert plan.model_dump(mode="json")["data"]["code"] == 200
