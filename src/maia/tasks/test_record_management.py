from __future__ import annotations

import re
from typing import Protocol
from uuid import uuid4

from maia.api import (
    ClarifyPlan,
    ConfirmPlan,
    PlanDataset,
    Prompt,
    PromptCandidate,
    ReplyPlan,
    TaskPlan,
    TaskStatus,
)
from maia.conversation.draft import SelectionDraft
from maia.conversation.state import ConversationSelectionState, ConversationTaskStateStore
from maia.integrations.sigma.records import TestRecordSummary
from maia.integrations.sigma.test_record_management import (
    DEFAULT_BACKUP_PATH,
    TestRecordManagementError,
    TestRecordManagementRequest,
)
from maia.selection import InMemorySelectionSetRepository
from maia.selection.sets import SelectionSet
from maia.tasks import ConfirmationService, PendingConfirmation, PendingTask, TaskSpec
from maia.tasks.record_search import RecordSearchHandler
from maia.tasks.router import TaskContext, TaskResult
from maia.tasks.test_record_artifacts import TestRecordArtifactParser


BACKUP_INTENT = "task.nvh.data_backup"
DELETE_INTENT = "task.nvh.data_delete"
TEST_RECORD_MANAGEMENT_INTENT = "task.nvh.test_record_management"
DATA_TYPES_SLOT = "test_record_data_types"
FILE_NAME_SLOT = "test_record_file_name"
OPERATION_SLOT = "test_record_operation"
PENDING_SELECTION_MARKER = f"__task__:{TEST_RECORD_MANAGEMENT_INTENT}"
CONFIRM_VALUES = {"确认", "是", "好的", "执行", "confirm", "yes", "y"}
CANCEL_VALUES = {"取消", "不用", "不要", "cancel", "no", "n"}
_ARTIFACT_PARSER = TestRecordArtifactParser()
DATA_TYPE_OPTIONS = _ARTIFACT_PARSER.options
DATA_EXPORT_TYPES = {"delete": 1, "backup": 2, "backup_delete": 3}


class TestRecordManager(Protocol):
    async def submit(
        self,
        request: TestRecordManagementRequest,
        *,
        workspace_context,
    ) -> dict[str, object]: ...


class UnavailableTestRecordManager:
    async def submit(
        self,
        request: TestRecordManagementRequest,
        *,
        workspace_context,
    ) -> dict[str, object]:
        raise TestRecordManagementError("Test record management client is not configured.")


class TestRecordManagementPolicy:
    def task_for_selection(
        self,
        selection: SelectionSet,
        *,
        operation: str,
        params: dict[str, object],
        task_id: str,
    ) -> TaskSpec:
        operations = (
            (BACKUP_INTENT, DELETE_INTENT)
            if operation == "backup_delete"
            else (BACKUP_INTENT if operation == "backup" else DELETE_INTENT,)
        )
        return TaskSpec(
            task_id=task_id,
            name="+".join(operations),
            title="Test record management",
            operations=operations,
            selection_set_id=selection.selection_set_id,
            selection_hash=selection.selection_hash,
            params={**params, OPERATION_SLOT: operation},
            risk_level=risk_level(operation),
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
            intent=TEST_RECORD_MANAGEMENT_INTENT,
            title=task.title,
            risk_level=task.risk_level,
            requires_confirmation=task.requires_confirmation,
            params=task.params,
            message=message,
        )

    def blocked_plan(self, task: TaskSpec | PendingTask, reason: str, message: str) -> TaskPlan:
        return self.task_plan(task, message).model_copy(update={"status": "blocked", "reason": reason})

    def clarify_data_types(self, *, dataset: PlanDataset) -> ClarifyPlan:
        labels = [label for _, label, _ in DATA_TYPE_OPTIONS]
        return ClarifyPlan(
            reason="missing_slots",
            message="Select test record data types to manage.",
            pending_task=TEST_RECORD_MANAGEMENT_INTENT,
            missing_slots=[DATA_TYPES_SLOT],
            prompts=[
                Prompt(
                    id=DATA_TYPES_SLOT,
                    target="slot",
                    label="data types",
                    message="Select test record data types to manage.",
                    required=True,
                    input_type="multi_select",
                    candidates=[PromptCandidate(value=label, label=label) for label in labels],
                )
            ],
            suggestions=labels,
            dataset=dataset,
        )

    def clarify_file_name(self, *, dataset: PlanDataset, invalid: bool = False) -> ClarifyPlan:
        slot_field = "invalid_slots" if invalid else "missing_slots"
        return ClarifyPlan(
            reason="invalid_slots" if invalid else "missing_slots",
            message="Provide a valid backup file name.",
            pending_task=TEST_RECORD_MANAGEMENT_INTENT,
            prompts=[
                Prompt(
                    id=FILE_NAME_SLOT,
                    target="slot",
                    label="file name",
                    message="Provide a valid backup file name.",
                    required=True,
                    input_type="text",
                )
            ],
            dataset=dataset,
            **{slot_field: [FILE_NAME_SLOT]},
        )


class TestRecordManagementHandler:
    def __init__(
        self,
        *,
        record_search: RecordSearchHandler,
        selection_repository: InMemorySelectionSetRepository,
        manager: TestRecordManager | None,
        confirmation_service: ConfirmationService | None = None,
        task_id_factory=None,
        policy: TestRecordManagementPolicy | None = None,
    ) -> None:
        self._record_search = record_search
        self._selection_repository = selection_repository
        self._manager = manager or UnavailableTestRecordManager()
        self._confirmation = confirmation_service or ConfirmationService()
        self._task_store = ConversationTaskStateStore()
        self._task_id_factory = task_id_factory or (lambda: f"task-{uuid4().hex}")
        self._policy = policy or TestRecordManagementPolicy()

    def can_handle(self, context: TaskContext) -> bool:
        return (
            is_pending_management_selection(context.state.pending_selection_draft)
            or is_management_task(context.state.pending_task)
            or is_management_confirmation(context.state.pending_confirmation)
            or operation_from_context(context) is not None
        )

    async def handle(self, context: TaskContext) -> TaskResult:
        if is_management_confirmation(context.state.pending_confirmation):
            return await self._handle_confirmation(context, context.state.pending_confirmation)
        if is_management_task(context.state.pending_task):
            return await self._resume_task(context, context.state.pending_task)
        return await self._start_task(context)

    async def _start_task(self, context: TaskContext) -> TaskResult:
        selection_result = await self._record_search.resolve_selection(
            context,
            complete_type_system=False,
        )
        if selection_result.clarify is not None:
            return TaskResult(
                plan=selection_result.clarify,
                state=mark_pending_management_selection(selection_result.state),
            )
        if selection_result.selection is None:
            return TaskResult(
                plan=TaskPlan(
                    status="blocked",
                    name=TEST_RECORD_MANAGEMENT_INTENT,
                    intent=TEST_RECORD_MANAGEMENT_INTENT,
                    title="Test record management",
                    risk_level="high",
                    requires_confirmation=True,
                    message="No records matched the current selection.",
                    reason="empty_selection",
                ),
                state=selection_result.state,
            )
        operation = operation_from_context(context)
        if operation is None:
            return TaskResult(
                plan=self._policy.reply("Test record management operation is not supported."),
                state=context.state,
            )
        task = self._policy.task_for_selection(
            selection_result.selection,
            operation=operation,
            params=initial_params_from_message(context.request.message),
            task_id=self._task_id_factory(),
        )
        return await self._prepare_or_confirm(context, selection_result.state, task)

    async def _resume_task(self, context: TaskContext, task: TaskSpec | PendingTask) -> TaskResult:
        if self._selection_repository.get(task.selection_set_id) is None:
            return TaskResult(
                plan=self._policy.blocked_plan(
                    task,
                    "selection_not_found",
                    "The selected records are no longer available.",
                ),
                state=self._task_store.clear_pending(context.state),
            )
        params = dict(task.params)
        params.update(params_from_prompt_replies(context.request.prompt_replies))
        return await self._prepare_or_confirm(
            context,
            context.state,
            task.model_copy(update={"params": params}),
        )

    async def _prepare_or_confirm(
        self,
        context: TaskContext,
        state: ConversationSelectionState,
        task: TaskSpec | PendingTask,
    ) -> TaskResult:
        selection = self._selection_repository.get(task.selection_set_id)
        if selection is None:
            return TaskResult(
                plan=self._policy.blocked_plan(
                    task,
                    "selection_not_found",
                    "The selected records are no longer available.",
                ),
                state=self._task_store.clear_pending(state),
            )
        records = await self._record_search.records_for_selection(
            selection,
            workspace_context=context.request.workspace_context,
        )
        if not records:
            return TaskResult(
                plan=self._policy.blocked_plan(task, "empty_selection", "No records matched the current selection."),
                state=self._task_store.clear_pending(state),
            )
        dataset = self._record_search.dataset_for(context, state=state, selection=selection)
        operation = operation_param(task.params.get(OPERATION_SLOT))
        data_types = data_types_param(task.params.get(DATA_TYPES_SLOT))
        if not data_types:
            return TaskResult(
                plan=self._policy.clarify_data_types(dataset=dataset),
                state=self._task_store.save_pending(state, pending_task(task, DATA_TYPES_SLOT)),
            )
        file_name = (
            file_name_param(task.params.get(FILE_NAME_SLOT))
            if operation in {"backup", "backup_delete"}
            else None
        )
        if operation in {"backup", "backup_delete"} and file_name is None:
            return TaskResult(
                plan=self._policy.clarify_file_name(dataset=dataset, invalid=FILE_NAME_SLOT in task.params),
                state=self._task_store.save_pending(state, pending_task(task, FILE_NAME_SLOT)),
            )
        try:
            request = management_request(records, operation, data_types, file_name)
        except ValueError as exc:
            return TaskResult(
                plan=self._policy.blocked_plan(task, "invalid_record_id", str(exc)),
                state=self._task_store.clear_pending(state),
            )
        ready_task = task.model_copy(
            update={
                "params": {
                    OPERATION_SLOT: operation,
                    DATA_TYPES_SLOT: data_types,
                    **request.to_body(),
                }
            }
        )
        confirmation = self._confirmation.preview(ready_task, record_count=len(request.result_id_list))
        return TaskResult(
            plan=ConfirmPlan(
                reason=confirmation.reason,
                message="Confirm test record management operation.",
                payload={
                    **confirmation.payload,
                    "operation": TEST_RECORD_MANAGEMENT_INTENT,
                    "params": ready_task.params,
                },
                dataset=dataset,
            ),
            state=self._task_store.save_confirmation(state, confirmation),
        )

    async def _handle_confirmation(self, context: TaskContext, confirmation: PendingConfirmation) -> TaskResult:
        normalized = context.request.message.strip().casefold()
        if normalized in CANCEL_VALUES:
            return TaskResult(
                plan=self._policy.reply("Test record management cancelled."),
                state=self._task_store.cancel_confirmation(context.state),
            )
        if normalized not in CONFIRM_VALUES:
            return TaskResult(
                plan=ConfirmPlan(
                    reason=confirmation.reason,
                    message="Confirm test record management operation.",
                    payload=confirmation.payload,
                ),
                state=context.state,
            )
        try:
            task = self._confirmation.confirm(
                confirmation,
                token=confirmation.token,
                selection_hash=confirmation.task.selection_hash,
            )
            await self._manager.submit(request_from_task(task), workspace_context=context.request.workspace_context)
        except (TestRecordManagementError, ValueError) as exc:
            return TaskResult(
                plan=self._policy.blocked_plan(confirmation.task, "test_record_management_failed", str(exc)),
                state=self._task_store.cancel_confirmation(context.state),
            )
        return TaskResult(
            plan=self._policy.task_plan(
                task,
                "Test record management submitted.",
                status="submitted",
            ),
            state=self._task_store.submit(context.state, task.task_id),
        )


def is_management_task(task: TaskSpec | PendingTask | None) -> bool:
    return task is not None and any(name in {BACKUP_INTENT, DELETE_INTENT} for name in task.operations)


def is_management_confirmation(confirmation: PendingConfirmation | None) -> bool:
    return confirmation is not None and is_management_task(confirmation.task)


def is_pending_management_selection(draft: SelectionDraft | None) -> bool:
    return draft is not None and PENDING_SELECTION_MARKER in draft.pending_questions


def mark_pending_management_selection(state: ConversationSelectionState) -> ConversationSelectionState:
    draft = state.pending_selection_draft
    if draft is None or PENDING_SELECTION_MARKER in draft.pending_questions:
        return state
    return state.model_copy(
        update={
            "pending_selection_draft": draft.model_copy(
                update={
                    "pending_questions": (
                        *draft.pending_questions,
                        PENDING_SELECTION_MARKER,
                    )
                }
            ),
            "version": state.version + 1,
        }
    )


def pending_task(task: TaskSpec | PendingTask, missing_slot: str) -> PendingTask:
    payload = task.model_dump(mode="python")
    payload.pop("missing_slots", None)
    return PendingTask(**payload, missing_slots=(missing_slot,))


def operation_from_context(context: TaskContext) -> str | None:
    names = {intent.name for intent in context.report.action_intents}
    if BACKUP_INTENT in names and DELETE_INTENT in names:
        return "backup_delete"
    if BACKUP_INTENT in names:
        return "backup"
    if DELETE_INTENT in names:
        return "delete"
    return None


def initial_params_from_message(message: str) -> dict[str, object]:
    params: dict[str, object] = {}
    if data_types := data_types_from_message(message):
        params[DATA_TYPES_SLOT] = data_types
    if file_name := file_name_from_message(message):
        params[FILE_NAME_SLOT] = file_name
    return params


def params_from_prompt_replies(replies) -> dict[str, object]:
    params: dict[str, object] = {}
    for reply in replies:
        if getattr(reply, "prompt_id", None) == DATA_TYPES_SLOT and (
            data_types := data_types_param(getattr(reply, "value", None))
        ):
            params[DATA_TYPES_SLOT] = data_types
        if getattr(reply, "prompt_id", None) == FILE_NAME_SLOT:
            params[FILE_NAME_SLOT] = str(getattr(reply, "value", "")).strip()
    return params


def data_types_from_message(message: str) -> tuple[str, ...]:
    return _ARTIFACT_PARSER.parse_message(message)


def data_types_param(value: object) -> tuple[str, ...]:
    return _ARTIFACT_PARSER.parse_value(value)


def file_name_from_message(message: str) -> str | None:
    match = re.search(r"(?:文件名|命名为|叫做|叫)\s*[:：为]?\s*([^\s，,。；;]+)", message)
    return match.group(1).strip() if match else None


def file_name_param(value: object) -> str | None:
    text = None if value is None else str(value).strip()
    try:
        TestRecordManagementRequest(
            result_id_list=(1,),
            color_map=True,
            origin_data=False,
            result_data=False,
            data_export_type=2,
            file_name=text,
        )
    except ValueError:
        return None
    return text


def operation_param(value: object) -> str:
    text = str(value or "").strip()
    return text if text in DATA_EXPORT_TYPES else "backup_delete"


def risk_level(operation: str):
    return "medium" if operation == "backup" else "high"


def management_request(
    records: tuple[TestRecordSummary, ...],
    operation: str,
    data_types: tuple[str, ...],
    file_name: str | None,
) -> TestRecordManagementRequest:
    selected = set(data_types)
    return TestRecordManagementRequest(
        result_id_list=tuple(record_id(record) for record in records),
        color_map="color_map" in selected,
        origin_data="origin_data" in selected,
        result_data="result_data" in selected,
        data_export_type=DATA_EXPORT_TYPES[operation],
        file_path=DEFAULT_BACKUP_PATH,
        file_name=file_name,
    )


def request_from_task(task: TaskSpec) -> TestRecordManagementRequest:
    return TestRecordManagementRequest(
        result_id_list=tuple(int(value) for value in task.params["resultIdList"]),
        color_map=bool(task.params["colorMap"]),
        origin_data=bool(task.params["originData"]),
        result_data=bool(task.params["resultData"]),
        data_export_type=int(task.params["dataExportType"]),
        file_path=str(task.params["filePath"]),
        file_name=None if "fileName" not in task.params else str(task.params["fileName"]),
    )


def record_id(record: TestRecordSummary) -> int:
    text = record.record_id.strip()
    if not text.isdigit():
        raise ValueError(f"record_id must be numeric for test record management: {record.record_id}")
    return int(text)


__all__ = ["TestRecordManagementHandler"]
