from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol
from uuid import uuid4

from maia.conversation.draft import SelectionDraft
from maia.conversation.state import ConversationSelectionState, ConversationTaskStateStore
from maia.integrations.sigma.data_observation import (
    DataObservationCatalogError,
    ObservationAvailability,
    ObservationIndicator,
    ObservationTypeSystem,
)
from maia.integrations.sigma.records import TestRecordSummary
from maia.recognition.report import RecognitionReport, RecognitionSlotOperation
from maia.selection import InMemorySelectionSetRepository
from maia.selection.sets import SelectionSet
from maia.tasks import PendingTask, TaskSpec
from maia.tasks.data_observation_matcher import ObservationMatcher
from maia.tasks.data_observation_models import (
    DATA_OBSERVATION_INTENT,
    DATA_TYPE_SLOT,
    INDICATOR_SLOT,
    PENDING_SELECTION_MARKER,
    SENSOR_LIST_SLOT,
    TEST_NAME_LIST_SLOT,
    ObservationResolution,
    ObservationWideRow,
)
from maia.tasks.data_observation_policy import DataObservationPolicy
from maia.tasks.record_search import RecordSearchHandler
from maia.tasks.router import TaskContext, TaskResult

_OBSERVATION_PARAM_ENTITY_TYPES = frozenset(
    {
        DATA_TYPE_SLOT,
        "data_type",
        "domain",
        INDICATOR_SLOT,
        SENSOR_LIST_SLOT,
        "sensor",
        TEST_NAME_LIST_SLOT,
        "testName",
        "test_name",
        "test_segment",
    }
)


class ObservationCatalog(Protocol):
    async def list_availability(self, dataset_id: str) -> tuple[ObservationAvailability, ...]: ...

    async def list_indicators(
        self,
        *,
        data_type: str,
        sensor_list: tuple[str, ...],
        test_name_list: tuple[str, ...],
        type_systems: tuple[ObservationTypeSystem, ...],
        workspace_context,
    ) -> tuple[ObservationIndicator, ...]: ...


class UnavailableObservationCatalog:
    async def list_availability(self, dataset_id: str) -> tuple[ObservationAvailability, ...]:
        del dataset_id
        raise DataObservationCatalogError("Data observation catalog client is not configured.")

    async def list_indicators(
        self,
        *,
        data_type: str,
        sensor_list: tuple[str, ...],
        test_name_list: tuple[str, ...],
        type_systems: tuple[ObservationTypeSystem, ...],
        workspace_context,
    ) -> tuple[ObservationIndicator, ...]:
        del data_type, sensor_list, test_name_list, type_systems, workspace_context
        raise DataObservationCatalogError("Data observation catalog client is not configured.")


class ObservationCatalogCache:
    def __init__(self, catalog: ObservationCatalog | None) -> None:
        self._catalog = catalog or UnavailableObservationCatalog()
        self._items: dict[tuple[str, str, str, str], tuple[ObservationWideRow, ...]] = {}

    async def load(
        self,
        *,
        session_id: str,
        selection: SelectionSet,
        dataset_id: str,
        type_systems: tuple[ObservationTypeSystem, ...],
        workspace_context,
    ) -> tuple[ObservationWideRow, ...]:
        key = (session_id, selection.selection_set_id, selection.selection_hash, dataset_id)
        if key in self._items:
            return self._items[key]
        availability = await self._catalog.list_availability(dataset_id)
        rows: list[ObservationWideRow] = []
        for item in availability:
            indicators = await self._catalog.list_indicators(
                data_type=item.data_type,
                sensor_list=(item.sensor,),
                test_name_list=(item.test_name,),
                type_systems=type_systems,
                workspace_context=workspace_context,
            )
            rows.extend(
                ObservationWideRow(
                    data_type=item.data_type,
                    sensor=item.sensor,
                    test_name=item.test_name,
                    indicator=indicator,
                )
                for indicator in indicators
            )
        self._items[key] = tuple(rows)
        return self._items[key]


class DataObservationHandler:
    def __init__(
        self,
        *,
        record_search: RecordSearchHandler,
        selection_repository: InMemorySelectionSetRepository,
        catalog: ObservationCatalog | None,
        task_id_factory=None,
        policy: DataObservationPolicy | None = None,
    ) -> None:
        self._record_search = record_search
        self._selection_repository = selection_repository
        self._catalog_cache = ObservationCatalogCache(catalog)
        self._task_store = ConversationTaskStateStore()
        self._task_id_factory = task_id_factory or (lambda: f"task-{uuid4().hex}")
        self._policy = policy or DataObservationPolicy()

    def can_handle(self, context: TaskContext) -> bool:
        return (
            is_pending_observation_selection(context.state.pending_selection_draft)
            or is_observation_task(context.state.pending_task)
            or _has_observation_action(context)
        )

    async def handle(self, context: TaskContext) -> TaskResult:
        if is_observation_task(context.state.pending_task):
            return await self._resume_task(context, context.state.pending_task)
        return await self._start_task(context)

    async def _start_task(self, context: TaskContext) -> TaskResult:
        selection_result = await self._record_search.resolve_selection(
            _selection_context(context),
            complete_type_system=False,
        )
        if selection_result.clarify is not None:
            return TaskResult(
                plan=selection_result.clarify,
                state=mark_pending_observation_selection(selection_result.state),
            )
        if selection_result.selection is None:
            return TaskResult(
                plan=self._policy.blocked_plan(None, "empty_selection", "No records matched the current selection."),
                state=selection_result.state,
            )
        task = self._policy.task_for_selection(
            selection_result.selection,
            task_id=self._task_id_factory(),
        )
        return await self._prepare(context, selection_result.state, task)

    async def _resume_task(self, context: TaskContext, task: TaskSpec | PendingTask) -> TaskResult:
        params = dict(task.params)
        params.update(params_from_prompt_replies(context.request.prompt_replies))
        return await self._prepare(
            context,
            context.state,
            task.model_copy(update={"params": params}),
        )

    async def _prepare(
        self,
        context: TaskContext,
        state: ConversationSelectionState,
        task: TaskSpec | PendingTask,
    ) -> TaskResult:
        selection = self._selection_repository.get(task.selection_set_id)
        dataset = self._record_search.dataset_for(context, state=state, selection=selection)
        if selection is None:
            return TaskResult(
                plan=self._policy.blocked_plan(
                    task,
                    "selection_not_found",
                    "The selected records are no longer available.",
                    dataset=dataset,
                ),
                state=self._task_store.clear_pending(state),
            )
        if selection.dataset_id is None:
            return TaskResult(
                plan=self._policy.blocked_plan(
                    task,
                    "dataset_missing",
                    "No materialized dataset is available for data observation.",
                    dataset=dataset,
                ),
                state=self._task_store.clear_pending(state),
            )
        rows = await self._wide_rows(context, state, task, selection, dataset)
        if isinstance(rows, TaskResult):
            return rows
        if not rows:
            return TaskResult(
                plan=self._policy.blocked_plan(
                    task,
                    "empty_observation_catalog",
                    "No observation data is available for the selected dataset.",
                    dataset=dataset,
                ),
                state=self._task_store.clear_pending(state),
            )

        resolution = ObservationMatcher(rows).resolve(
            message=context.request.message,
            params=task.params,
            include_message=not context.request.prompt_replies,
        )
        current_task = task.model_copy(update={"params": resolution.params})
        if resolution.missing_slots or resolution.invalid_slots:
            return TaskResult(
                plan=self._policy.clarify(resolution=resolution, dataset=dataset),
                state=self._task_store.save_pending(state, pending_task(current_task, resolution)),
            )
        return TaskResult(
            plan=self._policy.task_plan(current_task, dataset),
            state=self._task_store.clear_pending(state),
        )

    async def _wide_rows(
        self,
        context: TaskContext,
        state: ConversationSelectionState,
        task: TaskSpec | PendingTask,
        selection: SelectionSet,
        dataset,
    ) -> tuple[ObservationWideRow, ...] | TaskResult:
        records = await self._record_search.records_for_selection(
            selection,
            workspace_context=context.request.workspace_context,
        )
        type_systems = _type_systems(records)
        if not type_systems:
            return TaskResult(
                plan=self._policy.blocked_plan(
                    task,
                    "missing_type_system",
                    "Selected records do not include product/config/system information.",
                    dataset=dataset,
                ),
                state=self._task_store.clear_pending(state),
            )
        try:
            return await self._catalog_cache.load(
                session_id=context.request.session_id,
                selection=selection,
                dataset_id=str(selection.dataset_id),
                type_systems=type_systems,
                workspace_context=context.request.workspace_context,
            )
        except DataObservationCatalogError as exc:
            return TaskResult(
                plan=self._policy.blocked_plan(task, "catalog_failed", str(exc), dataset=dataset),
                state=self._task_store.clear_pending(state),
            )


def is_observation_task(task: TaskSpec | PendingTask | None) -> bool:
    return task is not None and DATA_OBSERVATION_INTENT in task.operations


def is_pending_observation_selection(draft: SelectionDraft | None) -> bool:
    return draft is not None and PENDING_SELECTION_MARKER in draft.pending_questions


def mark_pending_observation_selection(state: ConversationSelectionState) -> ConversationSelectionState:
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


def pending_task(task: TaskSpec | PendingTask, resolution: ObservationResolution) -> PendingTask:
    payload = task.model_dump(mode="python")
    payload.pop("missing_slots", None)
    return PendingTask(
        **payload,
        missing_slots=(*resolution.missing_slots, *resolution.invalid_slots),
    )


def params_from_prompt_replies(replies: Iterable[object]) -> dict[str, object]:
    params: dict[str, object] = {}
    for reply in replies:
        prompt_id = getattr(reply, "prompt_id", None)
        value = getattr(reply, "value", None)
        if prompt_id in {DATA_TYPE_SLOT, SENSOR_LIST_SLOT, TEST_NAME_LIST_SLOT, INDICATOR_SLOT}:
            params[prompt_id] = value
    return params


def _selection_context(context: TaskContext) -> TaskContext:
    report = _selection_report(context.report)
    if report is context.report:
        return context
    return TaskContext(context.request, report, context.state)


def _selection_report(report: RecognitionReport) -> RecognitionReport:
    operations = tuple(
        operation
        for operation in report.slot_operations
        if not _is_observation_task_param_operation(operation)
        and not _is_blank_invalid_operation(operation)
    )
    if operations == report.slot_operations:
        return report
    return report.model_copy(update={"slot_operations": operations})


def _has_observation_action(context: TaskContext) -> bool:
    report = context.report
    return (
        any(intent.name == DATA_OBSERVATION_INTENT and intent.score > 0 for intent in report.action_intents)
        or any(intent.name == DATA_OBSERVATION_INTENT and intent.score > 0 for intent in report.intents)
        or any(
            _operation_has_intent(operation, DATA_OBSERVATION_INTENT)
            and _has_positive_score(operation.score)
            for operation in report.slot_operations
        )
    )


def _is_observation_task_param_operation(operation: RecognitionSlotOperation) -> bool:
    return (
        _operation_has_intent(operation, DATA_OBSERVATION_INTENT)
        or operation.entity_type in _OBSERVATION_PARAM_ENTITY_TYPES
    )


def _is_blank_invalid_operation(operation: RecognitionSlotOperation) -> bool:
    return not _any_valid(operation.slot_valid) and all(
        _is_blank_value(target) for target in _as_tuple(operation.target)
    )


def _operation_has_intent(operation: RecognitionSlotOperation, name: str) -> bool:
    return name in _as_tuple(operation.intent)


def _has_positive_score(score: float | tuple[float, ...]) -> bool:
    return any(item > 0 for item in _as_tuple(score))


def _any_valid(value: bool | tuple[bool, ...]) -> bool:
    return any(_as_tuple(value))


def _is_blank_value(value: object) -> bool:
    return str(value).strip() == ""


def _as_tuple(value: object) -> tuple[object, ...]:
    return value if isinstance(value, tuple) else (value,)


def _type_systems(records: tuple[TestRecordSummary, ...]) -> tuple[ObservationTypeSystem, ...]:
    items: list[ObservationTypeSystem] = []
    keys: set[tuple[str, str]] = set()
    for record in records:
        if not record.product_type or not record.config_version or not record.system_no:
            continue
        item = ObservationTypeSystem(
            type_=f"{record.product_type}_{record.config_version}",
            system_no=record.system_no,
        )
        key = (item.type_, item.system_no)
        if key not in keys:
            keys.add(key)
            items.append(item)
    return tuple(items)


__all__ = [
    "DATA_OBSERVATION_INTENT",
    "DATA_TYPE_SLOT",
    "INDICATOR_SLOT",
    "SENSOR_LIST_SLOT",
    "TEST_NAME_LIST_SLOT",
    "DataObservationHandler",
    "ObservationMatcher",
    "ObservationResolution",
    "ObservationWideRow",
]
