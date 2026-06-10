from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


RiskLevel: TypeAlias = Literal["low", "medium", "high"]
TaskStatus: TypeAlias = Literal["ready", "needs_confirmation", "blocked"]
ClarifyReason: TypeAlias = Literal[
    "low_confidence",
    "ambiguous_intent",
    "missing_slots",
    "invalid_slots",
    "ambiguous_slots",
]


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlotStateChangeView(PlanModel):
    slot: str
    label: str
    before: Any = None
    after: Any = None


class SlotStateDiffView(PlanModel):
    changes: list[SlotStateChangeView] = Field(default_factory=list)


class PromptCandidate(PlanModel):
    value: Any
    label: str
    description: str | None = None
    disabled: bool = False


class Prompt(PlanModel):
    id: str
    target: Literal["slot", "intent", "text"]
    label: str
    message: str
    required: bool
    input_type: Literal["single_select", "multi_select", "text"]
    candidates: list[PromptCandidate] = Field(default_factory=list)


class ReplyPlan(PlanModel):
    kind: Literal["reply"] = "reply"
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)
    slot_state_diff: SlotStateDiffView = Field(default_factory=SlotStateDiffView)


class ClarifyPlan(PlanModel):
    kind: Literal["clarify"] = "clarify"
    reason: ClarifyReason
    message: str
    pending_task: str | None = None
    missing_slots: list[str] = Field(default_factory=list)
    invalid_slots: list[str] = Field(default_factory=list)
    prompts: list[Prompt] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    slot_state_diff: SlotStateDiffView = Field(default_factory=SlotStateDiffView)

    @model_validator(mode="after")
    def _requires_prompts_for_unready_slots(self) -> "ClarifyPlan":
        prompt_ids = {prompt.id for prompt in self.prompts if prompt.target == "slot"}
        slots = (*self.missing_slots, *self.invalid_slots)
        missing_prompts = [slot for slot in slots if slot not in prompt_ids]
        if missing_prompts:
            joined = ", ".join(missing_prompts)
            raise ValueError(f"clarify slots require prompts: {joined}")
        empty_candidates = [
            prompt.id
            for prompt in self.prompts
            if prompt.target == "slot" and prompt.id in slots and not prompt.candidates
        ]
        if empty_candidates:
            joined = ", ".join(empty_candidates)
            raise ValueError(f"clarify slot prompts require candidates: {joined}")
        return self


class ConfirmPlan(PlanModel):
    kind: Literal["confirm"] = "confirm"
    reason: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    slot_state_diff: SlotStateDiffView = Field(default_factory=SlotStateDiffView)


class ContextUpdatePlan(PlanModel):
    kind: Literal["context_update"] = "context_update"
    message: str
    projected_slots: dict[str, Any] = Field(default_factory=dict)
    slot_state_diff: SlotStateDiffView = Field(default_factory=SlotStateDiffView)


class ContextClearPlan(PlanModel):
    kind: Literal["context_clear"] = "context_clear"
    message: str = "已清空当前上下文。"
    preserved: list[str] = Field(default_factory=lambda: ["workspace_context"])
    cleared: list[str] = Field(default_factory=lambda: ["slot_state"])
    slot_state_diff: SlotStateDiffView = Field(default_factory=SlotStateDiffView)


class TaskPlan(PlanModel):
    kind: Literal["task"] = "task"
    status: TaskStatus
    name: str
    title: str
    risk_level: RiskLevel
    requires_confirmation: bool
    params: dict[str, Any] = Field(default_factory=dict)
    message: str
    reason: str | None = None
    slot_state_diff: SlotStateDiffView = Field(default_factory=SlotStateDiffView)


Plan: TypeAlias = (
    ReplyPlan | ClarifyPlan | ConfirmPlan | ContextUpdatePlan | ContextClearPlan | TaskPlan
)
