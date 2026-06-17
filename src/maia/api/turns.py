"""Public Maia /turns request and response contracts."""

from __future__ import annotations

from typing import Any, Literal, Mapping, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


RiskLevel: TypeAlias = Literal["low", "medium", "high"]
TaskStatus: TypeAlias = Literal["ready", "needs_confirmation", "blocked"]
ClarifyReason: TypeAlias = Literal[
    "low_confidence",
    "ambiguous_intent",
    "missing_slots",
    "invalid_slots",
    "ambiguous_slots",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRange(ContractModel):
    start: str | None = None
    end: str | None = None


class TypeSystemContext(ContractModel):
    type: str
    system_no: str

    def to_backend(self) -> dict[str, str]:
        return {"type": self.type, "systemNo": self.system_no}


class ProductContext(ContractModel):
    product_type: str
    product_version: str
    system_no: str


class WorkspaceContext(ContractModel):
    lang: str = "zh"


class PromptReply(ContractModel):
    prompt_id: str
    value: Any


class TurnRequest(ContractModel):
    session_id: str
    message: str
    prompt_replies: list[PromptReply] = Field(default_factory=list, exclude=True)
    workspace_context: WorkspaceContext | None = None


class SlotStateChange(ContractModel):
    slot: str
    label: str | None = None
    before: Any = None
    after: Any = None


class SlotStateDiff(ContractModel):
    changes: list[SlotStateChange] = Field(default_factory=list)


class PromptCandidate(ContractModel):
    value: Any
    label: str
    description: str | None = None
    disabled: bool = False


class Prompt(ContractModel):
    id: str
    target: Literal["slot", "intent", "text"]
    label: str
    message: str
    required: bool
    input_type: Literal["single_select", "multi_select", "text"]
    candidates: list[PromptCandidate] = Field(default_factory=list)


class PlanDataset(ContractModel):
    selection_set_id: str | None = None
    selection_hash: str | None = None
    dataset_id: str | None = None
    dataset_name: str | None = None
    record_count: int | None = None
    record_ids: list[str] = Field(default_factory=list)
    selection_params: dict[str, Any] = Field(default_factory=dict)

    @model_serializer(mode="plain")
    def _serialize(self) -> dict[str, Any]:
        if (
            self.selection_set_id is None
            and self.selection_hash is None
            and self.dataset_id is None
            and self.dataset_name is None
            and self.record_count is None
            and not self.record_ids
            and not self.selection_params
        ):
            return {}
        return {
            "selection_set_id": self.selection_set_id,
            "selection_hash": self.selection_hash,
            "dataset_id": self.dataset_id,
            "dataset_name": self.dataset_name,
            "record_count": self.record_count,
            "record_ids": self.record_ids,
            "selection_params": self.selection_params,
        }


class PlanWithDataset(ContractModel):
    dataset: PlanDataset = Field(default_factory=PlanDataset)


class ReplyPlan(PlanWithDataset):
    kind: Literal["reply"] = "reply"
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)
    slot_state_diff: SlotStateDiff = Field(default_factory=SlotStateDiff)


class ClarifyPlan(PlanWithDataset):
    kind: Literal["clarify"] = "clarify"
    reason: ClarifyReason
    message: str
    pending_task: str | None = None
    missing_slots: list[str] = Field(default_factory=list)
    invalid_slots: list[str] = Field(default_factory=list)
    prompts: list[Prompt] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    slot_state_diff: SlotStateDiff = Field(default_factory=SlotStateDiff)

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


class ConfirmPlan(PlanWithDataset):
    kind: Literal["confirm"] = "confirm"
    reason: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    slot_state_diff: SlotStateDiff = Field(default_factory=SlotStateDiff)


class ContextUpdatePlan(PlanWithDataset):
    kind: Literal["context_update"] = "context_update"
    message: str
    projected_slots: dict[str, Any] = Field(default_factory=dict)
    slot_state_diff: SlotStateDiff = Field(default_factory=SlotStateDiff)


class ContextClearPlan(PlanWithDataset):
    kind: Literal["context_clear"] = "context_clear"
    message: str = "Context cleared."
    preserved: list[str] = Field(default_factory=lambda: ["workspace_context"])
    cleared: list[str] = Field(default_factory=lambda: ["slot_state"])
    slot_state_diff: SlotStateDiff = Field(default_factory=SlotStateDiff)


class TaskPlan(PlanWithDataset):
    kind: Literal["task"] = "task"
    status: TaskStatus
    name: str
    intent: str
    title: str
    risk_level: RiskLevel
    requires_confirmation: bool
    params: dict[str, Any] = Field(default_factory=dict)
    message: str
    reason: str | None = None
    slot_state_diff: SlotStateDiff = Field(default_factory=SlotStateDiff)


TurnPlan: TypeAlias = (
    ReplyPlan | ClarifyPlan | TaskPlan | ConfirmPlan | ContextUpdatePlan | ContextClearPlan
)


class TurnResponse(ContractModel):
    plan: TurnPlan


TurnPlanResponse = TurnResponse
TurnPlanMapping: TypeAlias = Mapping[str, Any]


__all__ = [
    "ClarifyPlan",
    "ClarifyReason",
    "ConfirmPlan",
    "ContextClearPlan",
    "ContextUpdatePlan",
    "PlanDataset",
    "ProductContext",
    "Prompt",
    "PromptCandidate",
    "PromptReply",
    "ReplyPlan",
    "RiskLevel",
    "SlotStateChange",
    "SlotStateDiff",
    "TaskPlan",
    "TaskStatus",
    "TimeRange",
    "TurnPlan",
    "TurnPlanMapping",
    "TurnPlanResponse",
    "TurnRequest",
    "TurnResponse",
    "TypeSystemContext",
    "WorkspaceContext",
]
