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
from synapse.planning.plans import (
    ClarifyPlan as SynapseClarifyPlan,
    ConfirmPlan as SynapseConfirmPlan,
    ContextClearPlan as SynapseContextClearPlan,
    ContextUpdatePlan as SynapseContextUpdatePlan,
    Prompt as SynapsePrompt,
    PromptCandidate as SynapsePromptCandidate,
    ReplyPlan as SynapseReplyPlan,
    SlotStateChangeView as SynapseSlotStateChangeView,
    SlotStateDiffView as SynapseSlotStateDiffView,
    TaskPlan as SynapseTaskPlan,
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


def test_turn_plan_dumps_match_current_synapse_public_shape() -> None:
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
    synapse_slot_state_diff = SynapseSlotStateDiffView(
        changes=[
            SynapseSlotStateChangeView(
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
    synapse_plans = [
        SynapseReplyPlan(
            message="Available sensors.",
            slot_state_diff=synapse_slot_state_diff,
        ),
        SynapseClarifyPlan(
            reason="missing_slots",
            message="Please choose a sensor.",
            pending_task="query_frequency_spectrum",
            missing_slots=["sensors"],
            prompts=[
                SynapsePrompt(
                    id="sensors",
                    target="slot",
                    label="Sensor",
                    message="Select one sensor.",
                    required=True,
                    input_type="single_select",
                    candidates=[SynapsePromptCandidate(value="S1", label="Seat 1")],
                )
            ],
            slot_state_diff=synapse_slot_state_diff,
        ),
        SynapseTaskPlan(
            status="ready",
            name="query_frequency_spectrum",
            title="Query frequency spectrum",
            risk_level="low",
            requires_confirmation=False,
            params={"sensors": ["S2"]},
            message="Task is ready.",
            slot_state_diff=synapse_slot_state_diff,
        ),
        SynapseConfirmPlan(
            reason="high_risk_operation",
            message="Confirm deletion.",
            payload={"record_count": 3},
            slot_state_diff=synapse_slot_state_diff,
        ),
        SynapseContextUpdatePlan(
            message="Context updated.",
            projected_slots={"sensors": ["S2"]},
            slot_state_diff=synapse_slot_state_diff,
        ),
        SynapseContextClearPlan(
            message="Context cleared.",
            preserved=["workspace_context"],
            cleared=["sensors"],
            slot_state_diff=synapse_slot_state_diff,
        ),
    ]

    assert [plan.model_dump(mode="json") for plan in maia_plans] == [
        plan.model_dump(mode="json") for plan in synapse_plans
    ]


def test_turn_response_model_validates_g06_public_samples() -> None:
    samples = yaml.safe_load(SAMPLES_PATH.read_text(encoding="utf-8"))

    for case in samples["cases"]:
        response = TurnResponse.model_validate(case["response"])
        assert list(response.model_dump(mode="json")) == ["plan"]
        assert response.plan.kind == case["response"]["plan"]["kind"]
