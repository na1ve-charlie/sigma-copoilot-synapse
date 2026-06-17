from __future__ import annotations

from typing import Protocol

from maia.api import ClarifyPlan, PlanDataset, TaskPlan, WorkspaceContext
from maia.conversation.draft import SelectionDraft, SelectionDraftReducer, SelectionSort
from maia.conversation.references import SelectionReferenceResolutionError, SelectionReferenceResolver
from maia.conversation.state import ConversationSelectionState, PendingSelectionStateStore
from maia.presentation import DatasetProjector
from maia.recognition import RecognitionReport
from maia.selection import InMemorySelectionSetRepository
from maia.selection.compiler import SelectionQueryCompiler
from maia.selection.query import SelectionQuery
from maia.selection.service import SelectionSetService
from maia.selection.sets import SelectionSet
from maia.tasks.record_search_filters import (
    complete_config_version_filter,
    complete_product_type_filter,
    complete_type_system_filter,
    config_version_scope,
    invalidate_product_filters_on_scope_change,
    is_all_product_types_request,
    product_type_scope,
    selection_expression_for_storage,
    selection_expression_from_storage,
    type_system_scope,
)
from maia.tasks.record_search_replies import (
    mark_pending_prompts,
    prompt_replies_allow_all_products,
    resolve_pending_prompt_reply,
)
from maia.tasks.router import TaskContext, TaskResult


class DatasetBindingStore(Protocol):
    def load_dataset_binding(self, session_id: str): ...
    def save_dataset_binding(self, session_id: str, binding): ...


class RecordSearchHandler:
    def __init__(
        self,
        *,
        selection_service: SelectionSetService,
        selection_compiler: SelectionQueryCompiler,
        selection_repository: InMemorySelectionSetRepository,
        dataset_binding_store: DatasetBindingStore,
    ) -> None:
        self._selection_service = selection_service
        self._selection_compiler = selection_compiler
        self._selection_repository = selection_repository
        self._dataset_binding_store = dataset_binding_store
        self._draft_reducer = SelectionDraftReducer()
        self._selection_store = PendingSelectionStateStore()
        self._reference_resolver = SelectionReferenceResolver(selection_repository)
        self._dataset_projector = DatasetProjector()

    def can_handle(self, context: TaskContext) -> bool:
        return bool(context.request.prompt_replies) or _is_record_search(context.report)

    async def handle(self, context: TaskContext) -> TaskResult:
        try:
            report = self._report_for_context(context)
            base = self._resolve_base_selection(report, context.state)
            draft = self._build_draft(report, context.state, base, request=context.request)
        except (SelectionReferenceResolutionError, ValueError) as exc:
            return TaskResult(
                plan=self._with_dataset(
                    ClarifyPlan(reason="ambiguous_slots", message=str(exc)),
                    context,
                ),
                state=context.state,
            )

        draft, clarify = await self._complete_product_filters(context, draft)
        if clarify is not None:
            pending_draft = mark_pending_prompts(draft, clarify)
            return TaskResult(
                plan=self._with_dataset(clarify, context, draft=pending_draft),
                state=self._selection_store.save_pending(context.state, pending_draft),
            )

        selection = await self.materialize_selection(context, draft, base=base)
        next_state = self._selection_store.activate(context.state, selection.selection_set_id)
        return TaskResult(
            plan=_record_search_task_plan(
                selection,
                self.dataset_for(context, state=next_state, selection=selection),
            ),
            state=next_state,
        )

    async def materialize_selection(
        self,
        context: TaskContext,
        draft: SelectionDraft,
        *,
        base: SelectionSet | None,
    ) -> SelectionSet:
        if (
            base is not None
            and base.selection_set_id == context.state.active_selection_set_id
            and _draft_matches_selection(draft, base)
        ):
            return base
        binding = self._dataset_binding_store.load_dataset_binding(context.request.session_id)
        selection = await self._selection_service.create_or_derive(
            draft.model_copy(
                update={"expression": selection_expression_for_storage(draft.expression)}
            ),
            workspace_context=context.request.workspace_context,
            materialized_dataset_id=None if binding is None else binding.dataset_id,
            materialized_dataset_name=None if binding is None else binding.dataset_name,
        )
        if selection.dataset_id is not None:
            dataset_name = (
                self._dataset_projector.dataset_name(selection)
                if binding is None
                else binding.dataset_name
            )
            self._dataset_binding_store.save_dataset_binding(
                context.request.session_id,
                type(binding)(selection.dataset_id, dataset_name)
                if binding is not None
                else _DatasetBinding(selection.dataset_id, dataset_name),
            )
        return selection

    def dataset_for(
        self,
        context: TaskContext,
        *,
        state: ConversationSelectionState | None = None,
        draft: SelectionDraft | None = None,
        selection: SelectionSet | None = None,
    ) -> PlanDataset:
        current_state = context.state if state is None else state
        if selection is not None:
            return self._dataset_projector.from_selection(
                selection,
                workspace_context=context.request.workspace_context,
                dataset_name=self._dataset_name_for_session(context.request.session_id, selection),
            )
        if draft is not None:
            return self._dataset_projector.from_draft(
                draft,
                workspace_context=context.request.workspace_context,
            )
        if current_state.active_selection_set_id is None:
            return PlanDataset()
        active = self._selection_repository.get(current_state.active_selection_set_id)
        if active is None:
            return PlanDataset()
        return self._dataset_projector.from_selection(
            active,
            workspace_context=context.request.workspace_context,
            dataset_name=self._dataset_name_for_session(context.request.session_id, active),
        )

    async def records_for_selection(
        self,
        selection: SelectionSet,
        *,
        workspace_context: WorkspaceContext | None,
    ):
        return (
            await self._selection_compiler.compile(
                SelectionQuery(
                    expression=selection.expression,
                    sort=tuple(item.model_dump(mode="python") for item in selection.sort),
                    limit=selection.limit,
                ),
                workspace_context=workspace_context,
            )
        ).records

    def _report_for_context(self, context: TaskContext) -> RecognitionReport:
        if not context.request.prompt_replies:
            return context.report
        return resolve_pending_prompt_reply(
            context.state.pending_selection_draft,
            context.request.message,
            context.report,
            prompt_replies=context.request.prompt_replies,
        )

    def _resolve_base_selection(
        self,
        report: RecognitionReport,
        state: ConversationSelectionState,
    ) -> SelectionSet | None:
        referenced = self._reference_resolver.resolve_report(report, state)
        if referenced is not None:
            return referenced
        if state.active_selection_set_id is None:
            return None
        return self._selection_repository.get(state.active_selection_set_id)

    def _build_draft(
        self,
        report: RecognitionReport,
        state: ConversationSelectionState,
        base: SelectionSet | None,
        *,
        request,
    ) -> SelectionDraft:
        current = (
            state.pending_selection_draft
            if state.pending_selection_draft is not None
            else _draft_from_selection(base)
        )
        clear_product_type = is_all_product_types_request(
            request.message
        ) or prompt_replies_allow_all_products(request.prompt_replies)
        current = invalidate_product_filters_on_scope_change(
            current,
            report,
            clear_product_type=clear_product_type,
        )
        return self._draft_reducer.apply(current, report)

    async def _complete_product_filters(
        self,
        context: TaskContext,
        draft: SelectionDraft,
    ) -> tuple[SelectionDraft, ClarifyPlan | None]:
        product_records = await self._records_for_scope(
            draft,
            product_type_scope(draft.expression),
            workspace_context=context.request.workspace_context,
        )
        draft, clarify, product_type = complete_product_type_filter(
            draft,
            product_records,
            reducer=self._draft_reducer,
            allow_all_products=prompt_replies_allow_all_products(context.request.prompt_replies)
            or is_all_product_types_request(context.request.message),
        )
        if clarify is not None or product_type is None:
            return draft, clarify

        version_records = await self._records_for_scope(
            draft,
            config_version_scope(draft.expression),
            workspace_context=context.request.workspace_context,
        )
        draft, clarify, config_versions = complete_config_version_filter(
            draft,
            version_records,
            reducer=self._draft_reducer,
            product_type=product_type,
        )
        if clarify is not None or not config_versions:
            return draft, clarify

        system_records = await self._records_for_scope(
            draft,
            type_system_scope(draft.expression),
            workspace_context=context.request.workspace_context,
        )
        return complete_type_system_filter(
            draft,
            system_records,
            reducer=self._draft_reducer,
            product_type=product_type,
            config_versions=config_versions,
        )

    async def _records_for_scope(
        self,
        draft: SelectionDraft,
        expression,
        *,
        workspace_context,
    ):
        scoped_draft = draft.model_copy(
            update={"expression": selection_expression_for_storage(expression)}
        )
        return (
            await self._selection_compiler.compile(
                SelectionQuery(
                    expression=scoped_draft.expression,
                    sort=tuple(item.model_dump(mode="python") for item in scoped_draft.sort),
                    limit=scoped_draft.limit,
                ),
                workspace_context=workspace_context,
            )
        ).records

    def _with_dataset(
        self,
        plan,
        context: TaskContext,
        *,
        draft: SelectionDraft | None = None,
        selection: SelectionSet | None = None,
    ):
        return plan.model_copy(
            update={"dataset": self.dataset_for(context, draft=draft, selection=selection)}
        )

    def _dataset_name_for_session(
        self,
        session_id: str,
        selection: SelectionSet,
    ) -> str:
        binding = self._dataset_binding_store.load_dataset_binding(session_id)
        return (
            binding.dataset_name
            if binding is not None and selection.dataset_id == binding.dataset_id
            else self._dataset_projector.dataset_name(selection)
        )


class _DatasetBinding:
    def __init__(self, dataset_id: str, dataset_name: str) -> None:
        self.dataset_id = dataset_id
        self.dataset_name = dataset_name


def _is_record_search(report: RecognitionReport) -> bool:
    if report.action_intents:
        return all(intent.name == "task.nvh.record_search" for intent in report.action_intents)
    return bool(report.slot_operations) and all(
        _is_selection_intent(operation.intent) for operation in report.slot_operations
    )


def _is_selection_intent(intent: str | tuple[str, ...]) -> bool:
    names = intent if isinstance(intent, tuple) else (intent,)
    return all(
        name == "task.nvh.record_search" or name.startswith("task.nvh.selection.")
        for name in names
    )


def _draft_from_selection(selection: SelectionSet | None) -> SelectionDraft | None:
    if selection is None:
        return None
    return SelectionDraft(
        base_selection_id=selection.selection_set_id,
        expression=selection_expression_from_storage(selection.expression),
        sort=tuple(
            SelectionSort(field=item.field, direction=item.direction)
            for item in selection.sort
        ),
        limit=selection.limit,
    )


def _draft_matches_selection(draft: SelectionDraft, selection: SelectionSet) -> bool:
    expression = selection_expression_for_storage(draft.expression)
    draft_sort = tuple((item.field, item.direction) for item in draft.sort)
    selection_sort = tuple((item.field, item.direction) for item in selection.sort)
    return (
        expression == selection.expression
        and draft_sort == selection_sort
        and draft.limit == selection.limit
    )


def _record_search_task_plan(selection: SelectionSet, dataset: PlanDataset) -> TaskPlan:
    return TaskPlan(
        status="ready",
        name="task.nvh.record_search",
        intent="task.nvh.record_search",
        title="Record search",
        risk_level="low",
        requires_confirmation=False,
        params={},
        message=f"Found {selection.record_count} records.",
        dataset=dataset,
    )


__all__ = ["RecordSearchHandler"]
