from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Protocol

from maia.api import (
    ClarifyPlan,
    PlanDataset,
    Prompt,
    PromptCandidate,
    ReplyPlan,
    TaskPlan,
    TaskStatus,
)
from maia.conversation.draft import SelectionDraft
from maia.conversation.state import ConversationSelectionState
from maia.integrations.sigma.origin_export import OriginExportError, OriginExportRequest
from maia.integrations.sigma.records import TestRecordSummary
from maia.selection.sets import SelectionSet
from maia.tasks import PendingConfirmation, PendingTask, TaskSpec
from maia.tasks.slot_value_resolution import MessageSlotResolver, SlotCandidate, SlotCandidateSet


ORIGIN_DATA_EXPORT_INTENT = "task.nvh.origin_data_export"
ORIGIN_DATA_FORMAT_SLOT = "origin_data_format"
SYSTEM_NO_SLOT = "system_no"
DEFAULT_EXPORT_PATH = "D:\\exportOriginFile"
PENDING_SELECTION_MARKER = f"__task__:{ORIGIN_DATA_EXPORT_INTENT}"
FORMAT_VALUES = {"H5": 0, "TDMS": 1}
_SLOT_RESOLVER = MessageSlotResolver()
CONFIRM_VALUES = {"\u786e\u8ba4", "\u662f", "\u597d\u7684", "\u6267\u884c", "confirm", "yes", "y"}
CANCEL_VALUES = {"\u53d6\u6d88", "\u4e0d\u7528", "\u4e0d\u8981", "cancel", "no", "n"}


class OriginExporter(Protocol):
    async def export(self, request: OriginExportRequest, *, workspace_context) -> dict[str, object]: ...


class UnavailableOriginExporter:
    async def export(self, request: OriginExportRequest, *, workspace_context) -> dict[str, object]:
        raise OriginExportError("Origin export client is not configured.")


class OriginDataExportPolicy:
    def task_for_selection(
        self,
        selection: SelectionSet,
        *,
        params: dict[str, object],
        task_id: str,
    ) -> TaskSpec:
        return TaskSpec(
            task_id=task_id,
            name=ORIGIN_DATA_EXPORT_INTENT,
            title="Origin data export",
            operations=(ORIGIN_DATA_EXPORT_INTENT,),
            selection_set_id=selection.selection_set_id,
            selection_hash=selection.selection_hash,
            params=params,
            risk_level="medium",
            requires_confirmation=True,
        )

    def reply(self, message: str) -> ReplyPlan:
        return ReplyPlan(message=message)

    def task_plan(
        self,
        task: TaskSpec | PendingTask,
        message: str,
        *,
        status: TaskStatus = "ready",
    ) -> TaskPlan:
        return TaskPlan(
            status=status,
            name=task.name,
            intent=ORIGIN_DATA_EXPORT_INTENT,
            title=task.title,
            risk_level=task.risk_level,
            requires_confirmation=task.requires_confirmation,
            params=task.params,
            message=message,
        )

    def blocked_plan(self, task: TaskSpec | PendingTask, reason: str, message: str) -> TaskPlan:
        return TaskPlan(
            status="blocked",
            name=task.name,
            intent=ORIGIN_DATA_EXPORT_INTENT,
            title=task.title,
            risk_level=task.risk_level,
            requires_confirmation=task.requires_confirmation,
            params=task.params,
            message=message,
            reason=reason,
        )

    def clarify_format(self, *, task: TaskSpec | PendingTask, dataset: PlanDataset) -> ClarifyPlan:
        prompt = Prompt(
            id=ORIGIN_DATA_FORMAT_SLOT,
            target="slot",
            label="origin data format",
            message="Select the origin data export format.",
            required=True,
            input_type="single_select",
            candidates=[PromptCandidate(value=value, label=value) for value in FORMAT_VALUES],
        )
        return ClarifyPlan(
            reason="missing_slots",
            message="Select the origin data export format.",
            pending_task=ORIGIN_DATA_EXPORT_INTENT,
            missing_slots=[ORIGIN_DATA_FORMAT_SLOT],
            prompts=[prompt],
            suggestions=list(FORMAT_VALUES),
            dataset=dataset,
        )

    def clarify_system(
        self,
        *,
        task: TaskSpec | PendingTask,
        records: tuple[TestRecordSummary, ...],
        dataset: PlanDataset,
        invalid: bool = False,
    ) -> ClarifyPlan:
        counts = system_counts(records)
        prompt = Prompt(
            id=SYSTEM_NO_SLOT,
            target="slot",
            label="system no",
            message="Select the detection system.",
            required=True,
            input_type="single_select",
            candidates=[
                PromptCandidate(value=system_no, label=system_no, description=f"{count} records")
                for system_no, count in counts.items()
            ],
        )
        slot_field = "invalid_slots" if invalid else "missing_slots"
        return ClarifyPlan(
            reason="invalid_slots" if invalid else "ambiguous_slots",
            message="Select the detection system to export.",
            pending_task=ORIGIN_DATA_EXPORT_INTENT,
            prompts=[prompt],
            suggestions=list(counts),
            dataset=dataset,
            **{slot_field: [SYSTEM_NO_SLOT]},
        )


def is_origin_task(task: TaskSpec | PendingTask | None) -> bool:
    return task is not None and ORIGIN_DATA_EXPORT_INTENT in task.operations


def is_origin_confirmation(confirmation: PendingConfirmation | None) -> bool:
    return confirmation is not None and ORIGIN_DATA_EXPORT_INTENT in confirmation.task.operations


def is_pending_origin_selection(draft: SelectionDraft | None) -> bool:
    return draft is not None and PENDING_SELECTION_MARKER in draft.pending_questions


def mark_pending_origin_selection(state: ConversationSelectionState) -> ConversationSelectionState:
    draft = state.pending_selection_draft
    if draft is None or PENDING_SELECTION_MARKER in draft.pending_questions:
        return state
    return state.model_copy(
        update={
            "pending_selection_draft": draft.model_copy(
                update={"pending_questions": (*draft.pending_questions, PENDING_SELECTION_MARKER)}
            ),
            "version": state.version + 1,
        }
    )


def params_from_prompt_replies(replies: Iterable[object]) -> dict[str, str]:
    params: dict[str, str] = {}
    for reply in replies:
        if getattr(reply, "prompt_id", None) == ORIGIN_DATA_FORMAT_SLOT:
            value = format_param(getattr(reply, "value", None))
            if value is not None:
                params[ORIGIN_DATA_FORMAT_SLOT] = value
        if getattr(reply, "prompt_id", None) == SYSTEM_NO_SLOT:
            value = system_param(getattr(reply, "value", None))
            if value is not None:
                params[SYSTEM_NO_SLOT] = value
    return params


def pending_task(task: TaskSpec | PendingTask, missing_slot: str) -> PendingTask:
    payload = task.model_dump(mode="python")
    payload.pop("missing_slots", None)
    return PendingTask(**payload, missing_slots=(missing_slot,))


def format_from_message(message: str) -> str | None:
    return _SLOT_RESOLVER.resolve_message(message, _format_candidate_set()).first


def format_param(value: object) -> str | None:
    return _SLOT_RESOLVER.resolve_value(value, _format_candidate_set()).first


def system_param(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def system_from_message(message: str, candidates: tuple[str, ...]) -> str | None:
    result = _SLOT_RESOLVER.resolve_message(message, _system_candidate_set(candidates))
    return result.first


def systems(records: tuple[TestRecordSummary, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(record.system_no for record in records if record.system_no))


def system_counts(records: tuple[TestRecordSummary, ...]) -> Counter[str]:
    return Counter(record.system_no for record in records if record.system_no)


def _format_candidate_set() -> SlotCandidateSet:
    return SlotCandidateSet(
        slot=ORIGIN_DATA_FORMAT_SLOT,
        candidates=tuple(SlotCandidate(value=value, label=value) for value in FORMAT_VALUES),
    )


def _system_candidate_set(candidates: tuple[str, ...]) -> SlotCandidateSet:
    return SlotCandidateSet(
        slot=SYSTEM_NO_SLOT,
        candidates=tuple(SlotCandidate(value=value, label=value) for value in candidates),
    )


def export_request(
    records: tuple[TestRecordSummary, ...],
    format_value: str,
    system_no: str,
) -> OriginExportRequest:
    return OriginExportRequest(
        id_list=tuple(record_id(record) for record in records if record.system_no == system_no),
        path=DEFAULT_EXPORT_PATH,
        data_export_type=FORMAT_VALUES[format_value],
        system_no=system_no,
    )


def request_from_task(task: TaskSpec) -> OriginExportRequest:
    return OriginExportRequest(
        id_list=tuple(int(value) for value in task.params["idList"]),
        path=str(task.params["path"]),
        data_export_type=int(task.params["dataExportType"]),
        system_no=str(task.params[SYSTEM_NO_SLOT]),
    )


def record_id(record: TestRecordSummary) -> int:
    text = record.record_id.strip()
    if not text.isdigit():
        raise ValueError(f"record_id must be numeric for origin export: {record.record_id}")
    return int(text)


__all__ = [
    "CANCEL_VALUES",
    "CONFIRM_VALUES",
    "DEFAULT_EXPORT_PATH",
    "FORMAT_VALUES",
    "ORIGIN_DATA_EXPORT_INTENT",
    "ORIGIN_DATA_FORMAT_SLOT",
    "OriginDataExportPolicy",
    "OriginExporter",
    "SYSTEM_NO_SLOT",
    "UnavailableOriginExporter",
    "export_request",
    "format_from_message",
    "format_param",
    "is_origin_confirmation",
    "is_origin_task",
    "is_pending_origin_selection",
    "mark_pending_origin_selection",
    "params_from_prompt_replies",
    "pending_task",
    "request_from_task",
    "system_param",
    "system_from_message",
    "systems",
]
