from __future__ import annotations

from uuid import uuid4

from maia.api import ConfirmPlan, TaskPlan
from maia.conversation.state import ConversationTaskStateStore
from maia.integrations.sigma.origin_export import OriginExportError
from maia.selection import InMemorySelectionSetRepository
from maia.tasks import ConfirmationService, PendingConfirmation, PendingTask, TaskSpec
from maia.tasks.origin_data_export_policy import (
    CANCEL_VALUES,
    CONFIRM_VALUES,
    DEFAULT_EXPORT_PATH,
    FORMAT_VALUES,
    ORIGIN_DATA_EXPORT_INTENT,
    ORIGIN_DATA_FORMAT_SLOT,
    OriginDataExportPolicy,
    OriginExporter,
    SYSTEM_NO_SLOT,
    UnavailableOriginExporter,
    export_request,
    format_from_message,
    format_param,
    is_origin_confirmation,
    is_origin_task,
    is_pending_origin_selection,
    mark_pending_origin_selection,
    params_from_prompt_replies,
    pending_task,
    request_from_task,
    system_param,
    system_from_message,
    systems,
)
from maia.tasks.record_search import RecordSearchHandler
from maia.tasks.router import TaskContext, TaskResult


class OriginDataExportHandler:
    def __init__(
        self,
        *,
        record_search: RecordSearchHandler,
        selection_repository: InMemorySelectionSetRepository,
        exporter: OriginExporter | None,
        confirmation_service: ConfirmationService | None = None,
        task_id_factory=None,
        policy: OriginDataExportPolicy | None = None,
    ) -> None:
        self._record_search = record_search
        self._selection_repository = selection_repository
        self._exporter = exporter or UnavailableOriginExporter()
        self._confirmation = confirmation_service or ConfirmationService()
        self._task_store = ConversationTaskStateStore()
        self._task_id_factory = task_id_factory or (lambda: f"task-{uuid4().hex}")
        self._policy = policy or OriginDataExportPolicy()

    def can_handle(self, context: TaskContext) -> bool:
        return (
            is_pending_origin_selection(context.state.pending_selection_draft)
            or is_origin_task(context.state.pending_task)
            or is_origin_confirmation(context.state.pending_confirmation)
            or _has_origin_action(context)
        )

    async def handle(self, context: TaskContext) -> TaskResult:
        if is_origin_confirmation(context.state.pending_confirmation):
            return await self._handle_confirmation(context, context.state.pending_confirmation)
        if is_origin_task(context.state.pending_task):
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
                state=mark_pending_origin_selection(selection_result.state),
            )
        if selection_result.selection is None:
            return TaskResult(
                plan=TaskPlan(
                    status="blocked",
                    name=ORIGIN_DATA_EXPORT_INTENT,
                    intent=ORIGIN_DATA_EXPORT_INTENT,
                    title="Origin data export",
                    risk_level="medium",
                    requires_confirmation=True,
                    message="No records matched the current selection.",
                    reason="empty_selection",
                ),
                state=selection_result.state,
            )
        format_value = format_from_message(context.request.message)
        task = self._policy.task_for_selection(
            selection_result.selection,
            params={ORIGIN_DATA_FORMAT_SLOT: format_value} if format_value is not None else {},
            task_id=self._task_id_factory(),
        )
        return await self._prepare_or_confirm(context, selection_result.state, task)

    async def _resume_task(self, context: TaskContext, task: TaskSpec | PendingTask) -> TaskResult:
        selection = self._selection_repository.get(task.selection_set_id)
        if selection is None:
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
        state,
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

        format_value = format_param(task.params.get(ORIGIN_DATA_FORMAT_SLOT))
        if format_value is None:
            dataset = self._record_search.dataset_for(context, state=state, selection=selection)
            return TaskResult(
                plan=self._policy.clarify_format(task=task, dataset=dataset),
                state=self._task_store.save_pending(state, pending_task(task, ORIGIN_DATA_FORMAT_SLOT)),
            )

        system_no = system_param(task.params.get(SYSTEM_NO_SLOT))
        available_systems = systems(records)
        if not available_systems:
            return TaskResult(
                plan=self._policy.blocked_plan(task, "missing_system_no", "Selected records do not include systemNo."),
                state=self._task_store.clear_pending(state),
            )
        if system_no is None:
            system_no = system_from_message(context.request.message, available_systems)
        if system_no is None and len(available_systems) > 1:
            return self._clarify_system(context, state, task, records)
        if system_no is None:
            system_no = available_systems[0]
        if system_no not in available_systems:
            return self._clarify_system(context, state, task, records, invalid=True)

        try:
            origin_request = export_request(records, format_value, system_no)
        except ValueError as exc:
            return TaskResult(
                plan=self._policy.blocked_plan(task, "invalid_record_id", str(exc)),
                state=self._task_store.clear_pending(state),
            )
        ready_task = task.model_copy(
            update={
                "params": {
                    ORIGIN_DATA_FORMAT_SLOT: format_value,
                    SYSTEM_NO_SLOT: system_no,
                    "path": DEFAULT_EXPORT_PATH,
                    "idList": origin_request.id_list,
                    "dataExportType": FORMAT_VALUES[format_value],
                }
            }
        )
        confirmation = self._confirmation.preview(ready_task, record_count=len(origin_request.id_list))
        if not isinstance(confirmation, PendingConfirmation):
            return TaskResult(
                plan=self._policy.task_plan(ready_task, "Origin data export is ready."),
                state=self._task_store.clear_pending(state),
            )
        return TaskResult(
            plan=ConfirmPlan(
                reason=confirmation.reason,
                message="Confirm export of the selected origin data.",
                payload={
                    **confirmation.payload,
                    "operation": ORIGIN_DATA_EXPORT_INTENT,
                    "params": ready_task.params,
                },
                dataset=self._record_search.dataset_for(context, state=state, selection=selection),
            ),
            state=self._task_store.save_confirmation(state, confirmation),
        )

    def _clarify_system(
        self,
        context: TaskContext,
        state,
        task: TaskSpec | PendingTask,
        records,
        *,
        invalid: bool = False,
    ) -> TaskResult:
        selection = self._selection_repository.get(task.selection_set_id)
        dataset = self._record_search.dataset_for(context, state=state, selection=selection)
        return TaskResult(
            plan=self._policy.clarify_system(task=task, records=records, dataset=dataset, invalid=invalid),
            state=self._task_store.save_pending(state, pending_task(task, SYSTEM_NO_SLOT)),
        )

    async def _handle_confirmation(
        self,
        context: TaskContext,
        confirmation: PendingConfirmation,
    ) -> TaskResult:
        normalized = context.request.message.strip().casefold()
        if normalized in CANCEL_VALUES:
            return TaskResult(
                plan=self._policy.reply("Origin data export cancelled."),
                state=self._task_store.cancel_confirmation(context.state),
            )
        if normalized not in CONFIRM_VALUES:
            return TaskResult(
                plan=ConfirmPlan(
                    reason=confirmation.reason,
                    message="Confirm export of the selected origin data.",
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
            request = request_from_task(task)
            await self._exporter.export(request, workspace_context=context.request.workspace_context)
        except (OriginExportError, ValueError) as exc:
            return TaskResult(
                plan=self._policy.blocked_plan(confirmation.task, "origin_export_failed", str(exc)),
                state=self._task_store.cancel_confirmation(context.state),
            )
        return TaskResult(
            plan=self._policy.task_plan(
                task,
                "Origin data export submitted.",
                status="submitted",
            ),
            state=self._task_store.submit(context.state, task.task_id),
        )


def _has_origin_action(context: TaskContext) -> bool:
    return bool(context.report.action_intents) and context.report.action_intents[0].name == ORIGIN_DATA_EXPORT_INTENT


__all__ = ["OriginDataExportHandler"]
