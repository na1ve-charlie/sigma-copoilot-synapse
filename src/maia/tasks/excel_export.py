from __future__ import annotations

from uuid import uuid4

from maia.api import ConfirmPlan, TaskPlan
from maia.conversation.state import ConversationTaskStateStore
from maia.integrations.sigma.excel_export import ExcelExportError, SensorListError
from maia.selection import InMemorySelectionSetRepository
from maia.tasks import ConfirmationService, PendingConfirmation, PendingTask, TaskSpec
from maia.tasks.excel_export_policy import (
    CANCEL_VALUES,
    CONFIRM_VALUES,
    EXCEL_DATA_TYPES_SLOT,
    EXCEL_EXPORT_INTENT,
    EXCEL_SCOPE_SLOT,
    SENSOR_ID_LIST_SLOT,
    ExcelExportPolicy,
    ExcelExporter,
    SensorLister,
    UnavailableExcelExporter,
    UnavailableSensorLister,
    data_types_from_message,
    data_types_param,
    export_request,
    initial_params_from_message,
    is_excel_confirmation,
    is_excel_task,
    is_pending_excel_selection,
    mark_pending_excel_selection,
    params_from_prompt_replies,
    pending_task,
    records_for_scope,
    request_from_task,
    scope_from_message,
    scope_param,
    scopes,
    sensors_from_message,
    validate_sensors,
)
from maia.tasks.record_search import RecordSearchHandler
from maia.tasks.router import TaskContext, TaskResult


class ExcelExportHandler:
    def __init__(
        self,
        *,
        record_search: RecordSearchHandler,
        selection_repository: InMemorySelectionSetRepository,
        exporter: ExcelExporter | None,
        sensor_lister: SensorLister | None,
        confirmation_service: ConfirmationService | None = None,
        task_id_factory=None,
        policy: ExcelExportPolicy | None = None,
    ) -> None:
        self._record_search = record_search
        self._selection_repository = selection_repository
        self._exporter = exporter or UnavailableExcelExporter()
        self._sensor_lister = sensor_lister or UnavailableSensorLister()
        self._confirmation = confirmation_service or ConfirmationService()
        self._task_store = ConversationTaskStateStore()
        self._task_id_factory = task_id_factory or (lambda: f"task-{uuid4().hex}")
        self._policy = policy or ExcelExportPolicy()

    def can_handle(self, context: TaskContext) -> bool:
        return (
            is_pending_excel_selection(context.state.pending_selection_draft)
            or is_excel_task(context.state.pending_task)
            or is_excel_confirmation(context.state.pending_confirmation)
            or _has_excel_action(context)
        )

    async def handle(self, context: TaskContext) -> TaskResult:
        if is_excel_confirmation(context.state.pending_confirmation):
            return await self._handle_confirmation(context, context.state.pending_confirmation)
        if is_excel_task(context.state.pending_task):
            return await self._resume_task(context, context.state.pending_task)
        return await self._start_task(context)

    async def _start_task(self, context: TaskContext) -> TaskResult:
        selection_result = await self._record_search.resolve_selection(
            context,
            complete_type_system=True,
        )
        if selection_result.clarify is not None:
            return TaskResult(
                plan=selection_result.clarify,
                state=mark_pending_excel_selection(selection_result.state),
            )
        if selection_result.selection is None:
            return TaskResult(
                plan=TaskPlan(
                    status="blocked",
                    name=EXCEL_EXPORT_INTENT,
                    intent=EXCEL_EXPORT_INTENT,
                    title="Excel export",
                    risk_level="medium",
                    requires_confirmation=True,
                    message="No records matched the current selection.",
                    reason="empty_selection",
                ),
                state=selection_result.state,
            )
        task = self._policy.task_for_selection(
            selection_result.selection,
            params=initial_params_from_message(context.request.message),
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

        available_scopes = scopes(records)
        dataset = self._record_search.dataset_for(context, state=state, selection=selection)
        if not available_scopes:
            return TaskResult(
                plan=self._policy.blocked_plan(task, "missing_excel_scope", "Selected records do not include type/systemNo."),
                state=self._task_store.clear_pending(state),
            )
        scope = scope_param(task.params.get(EXCEL_SCOPE_SLOT), available_scopes)
        if scope is None:
            scope = scope_from_message(context.request.message, available_scopes)
        if scope is None and len(available_scopes) > 1:
            return TaskResult(
                plan=self._policy.clarify_scope(
                    task=task,
                    scopes=available_scopes,
                    records=records,
                    dataset=dataset,
                    invalid=EXCEL_SCOPE_SLOT in task.params,
                ),
                state=self._task_store.save_pending(state, pending_task(task, EXCEL_SCOPE_SLOT)),
            )
        if scope is None:
            scope = available_scopes[0]

        records = records_for_scope(records, scope)
        params = {**task.params, EXCEL_SCOPE_SLOT: scope.prompt_value()}
        current_task = task.model_copy(update={"params": params})
        try:
            sensor_candidates = await self._sensor_lister.list_sensors(
                type_=scope.type_,
                system_no=scope.system_no,
                workspace_context=context.request.workspace_context,
            )
        except SensorListError as exc:
            return TaskResult(
                plan=self._policy.blocked_plan(current_task, "sensor_list_failed", str(exc)),
                state=self._task_store.clear_pending(state),
            )
        if not sensor_candidates:
            return TaskResult(
                plan=self._policy.blocked_plan(current_task, "empty_sensor_candidates", "No exportable sensors were returned."),
                state=self._task_store.clear_pending(state),
            )

        sensor_value = params.get(SENSOR_ID_LIST_SLOT)
        if sensor_value is None:
            parsed_sensors = sensors_from_message(context.request.message, sensor_candidates)
            sensor_value = parsed_sensors or None
        sensors, invalid_sensors = validate_sensors(sensor_value, sensor_candidates)
        if invalid_sensors or not sensors:
            slot_task = current_task.model_copy(
                update={"params": {**params, **({SENSOR_ID_LIST_SLOT: sensor_value} if sensor_value else {})}}
            )
            return TaskResult(
                plan=self._policy.clarify_sensors(
                    sensors=sensor_candidates,
                    dataset=dataset,
                    invalid=bool(invalid_sensors),
                ),
                state=self._task_store.save_pending(state, pending_task(slot_task, SENSOR_ID_LIST_SLOT)),
            )

        data_types = data_types_param(params.get(EXCEL_DATA_TYPES_SLOT))
        if not data_types:
            data_types = data_types_from_message(context.request.message)
        if not data_types:
            slot_task = current_task.model_copy(update={"params": {**params, SENSOR_ID_LIST_SLOT: sensors}})
            return TaskResult(
                plan=self._policy.clarify_data_types(dataset=dataset),
                state=self._task_store.save_pending(state, pending_task(slot_task, EXCEL_DATA_TYPES_SLOT)),
            )

        try:
            excel_request = export_request(records, scope, sensors, data_types)
        except ValueError as exc:
            return TaskResult(
                plan=self._policy.blocked_plan(current_task, "invalid_record_id", str(exc)),
                state=self._task_store.clear_pending(state),
            )
        ready_task = task.model_copy(
            update={
                "params": {
                    EXCEL_SCOPE_SLOT: scope.prompt_value(),
                    SENSOR_ID_LIST_SLOT: sensors,
                    EXCEL_DATA_TYPES_SLOT: data_types,
                    "type": excel_request.type_,
                    "systemNo": excel_request.system_no,
                    "idList": excel_request.id_list,
                    "sensorIdList": excel_request.sensor_id_list,
                    "oneData": excel_request.one_data,
                    "twoData": excel_request.two_data,
                    "resultData": excel_request.result_data,
                }
            }
        )
        confirmation = self._confirmation.preview(ready_task, record_count=len(excel_request.id_list))
        if not isinstance(confirmation, PendingConfirmation):
            return TaskResult(
                plan=self._policy.task_plan(ready_task, "Excel export is ready."),
                state=self._task_store.clear_pending(state),
            )
        return TaskResult(
            plan=ConfirmPlan(
                reason=confirmation.reason,
                message="Confirm export of the selected Excel data.",
                payload={
                    **confirmation.payload,
                    "operation": EXCEL_EXPORT_INTENT,
                    "params": ready_task.params,
                },
                dataset=dataset,
            ),
            state=self._task_store.save_confirmation(state, confirmation),
        )

    async def _handle_confirmation(
        self,
        context: TaskContext,
        confirmation: PendingConfirmation,
    ) -> TaskResult:
        normalized = context.request.message.strip().casefold()
        if normalized in CANCEL_VALUES:
            return TaskResult(
                plan=self._policy.reply("Excel export cancelled."),
                state=self._task_store.cancel_confirmation(context.state),
            )
        if normalized not in CONFIRM_VALUES:
            return TaskResult(
                plan=ConfirmPlan(
                    reason=confirmation.reason,
                    message="Confirm export of the selected Excel data.",
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
            export_result = await self._exporter.export(
                request_from_task(task),
                workspace_context=context.request.workspace_context,
            )
        except (ExcelExportError, ValueError) as exc:
            return TaskResult(
                plan=self._policy.blocked_plan(confirmation.task, "excel_export_failed", str(exc)),
                state=self._task_store.cancel_confirmation(context.state),
            )
        return TaskResult(
            plan=self._policy.task_plan(
                task,
                "Excel export submitted.",
                status="submitted",
                data=export_result if isinstance(export_result, dict) else {},
            ),
            state=self._task_store.submit(context.state, task.task_id),
        )


def _has_excel_action(context: TaskContext) -> bool:
    return bool(context.report.action_intents) and context.report.action_intents[0].name == EXCEL_EXPORT_INTENT


__all__ = ["ExcelExportHandler"]
