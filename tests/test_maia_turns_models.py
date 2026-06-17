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
from synapse.turns import TurnRequest as SynapseTurnRequest


SAMPLES_PATH = Path("configs/maia/turns_response_samples.yaml")


def test_turn_request_dump_keeps_only_lang_in_maia_workspace_context() -> None:
    payload = {
        "session_id": "sess-001",
        "message": "show S1",
        "workspace_context": {"lang": "zh"},
    }

    maia = TurnRequest(**payload)
    synapse = SynapseTurnRequest(**payload)

    assert maia.model_dump(mode="json") == payload
    assert synapse.workspace_context is not None
    assert synapse.workspace_context.lang == "zh"


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
            pending_task="query_frequency_spectrum",
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
            name="query_frequency_spectrum",
            intent="task.nvh.data_observation.batch.frequency_spectrum",
            title="Query frequency spectrum",
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
    assert dumped[2]["intent"] == "task.nvh.data_observation.batch.frequency_spectrum"


def test_turn_response_model_validates_g06_public_samples() -> None:
    samples = yaml.safe_load(SAMPLES_PATH.read_text(encoding="utf-8"))

    for case in samples["cases"]:
        response = TurnResponse.model_validate(case["response"])
        assert list(response.model_dump(mode="json")) == ["plan"]
        assert response.plan.kind == case["response"]["plan"]["kind"]
