from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Protocol

from maia.api import ClarifyPlan, PlanDataset, ReplyPlan, TaskPlan, TurnRequest, TurnResponse
from maia.conversation.draft import SelectionDraft, SelectionDraftReducer, SelectionSort
from maia.conversation.references import SelectionReferenceResolutionError, SelectionReferenceResolver
from maia.conversation.state import ConversationSelectionState, PendingSelectionStateStore
from maia.integrations.sigma import (
    MutableSigmaTokenProvider,
    SigmaProductCatalogClient,
    SigmaSelectionSetMaterializer,
    SigmaTokenProvider,
    TestRecordClient,
)
from maia.integrations.sigma.product_catalog import ProductConfig
from maia.presentation import DatasetProjector, present_turn
from maia.recognition import RecognitionReport, build_maia_recognizer_from_config
from maia.recognition.normalization import (
    MARKING_RESULT_VALUES,
    SUMMARY_RESULT_ALIASES,
    SUMMARY_RESULT_VALUES,
)
from maia.runtime_product_filters import (
    complete_config_version_filter,
    complete_product_type_filter,
    complete_type_system_filter,
    config_version_scope,
    distinct_values,
    invalidate_product_filters_on_scope_change,
    is_all_product_types_request,
    product_type_scope,
    selection_expression_for_storage,
    selection_expression_from_storage,
    type_system_scope,
)
from maia.runtime_slot_replies import (
    mark_pending_prompts,
    prompt_replies_allow_all_products,
    resolve_pending_prompt_reply,
)
from maia.selection import InMemorySelectionSetRepository
from maia.selection.compiler import SelectionQueryCompiler
from maia.selection.query import SelectionQuery
from maia.selection.service import SelectionSetMaterializer, SelectionSetService
from maia.selection.sets import SelectionSet

_SUMMARY_RESULT_RESOLVER_VALUES = (*SUMMARY_RESULT_VALUES, *(alias.upper() for alias in SUMMARY_RESULT_ALIASES))
_MARKING_RESULT_RESOLVER_VALUES = MARKING_RESULT_VALUES


@dataclass(frozen=True)
class SessionDatasetBinding:
    dataset_id: str
    dataset_name: str


class Recognizer(Protocol):
    async def recognize(
        self,
        message: str,
        *,
        resolver: Any | None = None,
        include_diagnostics: bool = False,
    ) -> RecognitionReport: ...


class ProductCatalog(Protocol):
    async def list_configs(self, *, lang: str = "zh") -> tuple[ProductConfig, ...]: ...


class ConversationStateRepository:
    def __init__(self) -> None:
        self._items: dict[str, ConversationSelectionState] = {}
        self._dataset_bindings: dict[str, SessionDatasetBinding] = {}

    def load(self, session_id: str) -> ConversationSelectionState:
        return self._items.get(session_id, ConversationSelectionState())

    def save(
        self,
        session_id: str,
        state: ConversationSelectionState,
    ) -> ConversationSelectionState:
        self._items[session_id] = state
        return state

    def load_dataset_binding(self, session_id: str) -> SessionDatasetBinding | None:
        return self._dataset_bindings.get(session_id)

    def save_dataset_binding(
        self,
        session_id: str,
        binding: SessionDatasetBinding,
    ) -> SessionDatasetBinding:
        if not binding.dataset_id.strip() or not binding.dataset_name.strip():
            raise ValueError("dataset binding must not contain blank values")
        self._dataset_bindings[session_id] = binding
        return binding


class MaiaTurnHandler:
    def __init__(
        self,
        *,
        recognizer: Recognizer,
        state_repository: ConversationStateRepository,
        selection_service: SelectionSetService,
        selection_compiler: SelectionQueryCompiler,
        selection_repository: InMemorySelectionSetRepository,
        product_catalog: ProductCatalog | None = None,
    ) -> None:
        self._recognizer = recognizer
        self._state_repository = state_repository
        self._selection_service = selection_service
        self._selection_compiler = selection_compiler
        self._selection_repository = selection_repository
        self._product_catalog = product_catalog
        self._draft_reducer = SelectionDraftReducer()
        self._selection_store = PendingSelectionStateStore()
        self._reference_resolver = SelectionReferenceResolver(selection_repository)
        self._dataset_projector = DatasetProjector()

    async def handle_turn(self, request: TurnRequest) -> TurnResponse:
        state = self._state_repository.load(request.session_id)
        product_configs = await self._product_configs(request)
        try:
            report = await self._recognize_or_apply_prompt_replies(
                request,
                state,
                product_configs,
            )
        except ValueError as exc:
            return present_turn(
                self._with_dataset(
                    ClarifyPlan(reason="ambiguous_slots", message=str(exc)),
                    request,
                    state,
                )
            )
        if report.verdict == "low":
            return present_turn(
                self._with_dataset(
                    ClarifyPlan(
                        reason="low_confidence",
                        message="I could not identify a supported Maia request.",
                    ),
                    request,
                    state,
                )
            )
        if report.requires_confirmation or report.verdict == "ambiguous":
            return present_turn(
                self._with_dataset(
                    ClarifyPlan(
                        reason="ambiguous_intent",
                        message="Please clarify which request you want Maia to run.",
                    ),
                    request,
                    state,
                )
            )
        if not _is_record_search(report):
            return present_turn(
                self._with_dataset(
                    ReplyPlan(message="Maia currently supports record search only."),
                    request,
                    state,
                )
            )

        clear_product_type = is_all_product_types_request(
            request.message
        ) or prompt_replies_allow_all_products(request.prompt_replies)
        try:
            base = self._resolve_base_selection(report, state)
            draft = self._build_draft(
                report,
                state,
                base,
                clear_product_type=clear_product_type,
            )
        except (SelectionReferenceResolutionError, ValueError) as exc:
            return present_turn(
                self._with_dataset(
                    ClarifyPlan(reason="ambiguous_slots", message=str(exc)),
                    request,
                    state,
                )
            )

        draft, clarify = await self._complete_product_filters(
            request,
            draft,
            allow_all_products=clear_product_type,
        )
        if clarify is not None:
            draft = mark_pending_prompts(draft, clarify)
            self._state_repository.save(
                request.session_id,
                self._selection_store.save_pending(state, draft),
            )
            return present_turn(
                self._with_dataset(clarify, request, state, draft=draft)
            )

        selection = await self._materialize_selection(
            request,
            state,
            draft,
            base=base,
        )
        self._state_repository.save(
            request.session_id,
            self._selection_store.activate(state, selection.selection_set_id),
        )
        dataset = self._dataset_for(request, state, selection=selection)
        return present_turn(_record_search_task_plan(selection, dataset))

    async def _materialize_selection(
        self,
        request: TurnRequest,
        state: ConversationSelectionState,
        draft: SelectionDraft,
        *,
        base: SelectionSet | None,
    ) -> SelectionSet:
        if (
            base is not None
            and base.selection_set_id == state.active_selection_set_id
            and _draft_matches_selection(draft, base)
        ):
            return base
        binding = self._state_repository.load_dataset_binding(request.session_id)
        selection = await self._selection_service.create_or_derive(
            draft.model_copy(
                update={"expression": selection_expression_for_storage(draft.expression)}
            ),
            workspace_context=request.workspace_context,
            materialized_dataset_id=None if binding is None else binding.dataset_id,
            materialized_dataset_name=None if binding is None else binding.dataset_name,
        )
        if selection.dataset_id is not None:
            dataset_name = (
                self._dataset_projector.dataset_name(selection)
                if binding is None
                else binding.dataset_name
            )
            self._state_repository.save_dataset_binding(
                request.session_id,
                SessionDatasetBinding(
                    dataset_id=selection.dataset_id,
                    dataset_name=dataset_name,
                ),
            )
        return selection

    async def _product_configs(self, request: TurnRequest) -> tuple[ProductConfig, ...]:
        if self._product_catalog is None:
            return ()
        return await self._product_catalog.list_configs(
            lang="zh" if request.workspace_context is None else request.workspace_context.lang
        )

    async def _recognize_or_apply_prompt_replies(
        self,
        request: TurnRequest,
        state: ConversationSelectionState,
        product_configs: tuple[ProductConfig, ...],
    ) -> RecognitionReport:
        empty_report = RecognitionReport(
            message=request.message,
            verdict="clear",
            requires_confirmation=False,
            degraded=False,
        )
        if request.prompt_replies:
            return resolve_pending_prompt_reply(
                state.pending_selection_draft,
                request.message,
                empty_report,
                prompt_replies=request.prompt_replies,
            )
        report = await self._recognizer.recognize(
            request.message,
            resolver=_TurnResolver(product_configs),
            include_diagnostics=False,
        )
        return report

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
        clear_product_type: bool,
    ) -> SelectionDraft:
        current = (
            state.pending_selection_draft
            if state.pending_selection_draft is not None
            else _draft_from_selection(base)
        )
        current = invalidate_product_filters_on_scope_change(
            current,
            report,
            clear_product_type=clear_product_type,
        )
        return self._draft_reducer.apply(current, report)

    async def _complete_product_filters(
        self,
        request: TurnRequest,
        draft: SelectionDraft,
        *,
        allow_all_products: bool,
    ) -> tuple[SelectionDraft, ClarifyPlan | None]:
        product_records = await self._records_for_scope(
            draft,
            product_type_scope(draft.expression),
            workspace_context=request.workspace_context,
        )
        draft, clarify, product_type = complete_product_type_filter(
            draft,
            product_records,
            reducer=self._draft_reducer,
            allow_all_products=allow_all_products,
        )
        if clarify is not None or product_type is None:
            return draft, clarify

        version_records = await self._records_for_scope(
            draft,
            config_version_scope(draft.expression),
            workspace_context=request.workspace_context,
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
            workspace_context=request.workspace_context,
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
        plan: Any,
        request: TurnRequest,
        state: ConversationSelectionState,
        *,
        draft: SelectionDraft | None = None,
        selection: SelectionSet | None = None,
    ) -> Any:
        return plan.model_copy(
            update={"dataset": self._dataset_for(request, state, draft=draft, selection=selection)}
        )

    def _dataset_for(
        self,
        request: TurnRequest,
        state: ConversationSelectionState,
        *,
        draft: SelectionDraft | None = None,
        selection: SelectionSet | None = None,
    ) -> PlanDataset:
        if selection is not None:
            return self._dataset_projector.from_selection(
                selection,
                workspace_context=request.workspace_context,
                dataset_name=self._dataset_name_for_session(request.session_id, selection),
            )
        if draft is not None:
            return self._dataset_projector.from_draft(
                draft,
                workspace_context=request.workspace_context,
            )
        if state.active_selection_set_id is None:
            return PlanDataset()
        active = self._selection_repository.get(state.active_selection_set_id)
        if active is None:
            return PlanDataset()
        return self._dataset_projector.from_selection(
            active,
            workspace_context=request.workspace_context,
            dataset_name=self._dataset_name_for_session(request.session_id, active),
        )

    def _dataset_name_for_session(
        self,
        session_id: str,
        selection: SelectionSet,
    ) -> str:
        binding = self._state_repository.load_dataset_binding(session_id)
        return (
            binding.dataset_name
            if binding is not None and selection.dataset_id == binding.dataset_id
            else self._dataset_projector.dataset_name(selection)
        )


def create_maia_runtime(
    *,
    recognizer: Recognizer | None = None,
    record_client: object | None = None,
    state_repository: ConversationStateRepository | None = None,
    selection_repository: InMemorySelectionSetRepository | None = None,
    product_catalog: ProductCatalog | None = None,
    selection_materializer: SelectionSetMaterializer | None = None,
    token_provider: SigmaTokenProvider | None = None,
    source_version: str = "sigma-legacy-v1",
    base_url: str | None = None,
    token: str | None = None,
) -> MaiaTurnHandler:
    selection_repository = selection_repository or InMemorySelectionSetRepository()
    sigma_base_url = base_url or os.getenv("SIGMA_BASE_URL", "http://192.168.0.65:8081")
    sigma_token_provider = token_provider or MutableSigmaTokenProvider(token or os.getenv("SIGMA_TOKEN"))
    if record_client is None:
        record_client = TestRecordClient(
            base_url=sigma_base_url,
            token_provider=sigma_token_provider,
        )
        product_catalog = product_catalog or SigmaProductCatalogClient(
            base_url=sigma_base_url,
            token_provider=sigma_token_provider,
        )
        selection_materializer = selection_materializer or SigmaSelectionSetMaterializer(
            base_url=sigma_base_url,
            token_provider=sigma_token_provider,
        )
    selection_compiler = SelectionQueryCompiler(record_client)
    return MaiaTurnHandler(
        recognizer=recognizer or build_maia_recognizer_from_config(),
        state_repository=state_repository or ConversationStateRepository(),
        selection_repository=selection_repository,
        selection_compiler=selection_compiler,
        product_catalog=product_catalog,
        selection_service=SelectionSetService(
            selection_repository,
            selection_compiler,
            source_version=source_version,
            materializer=selection_materializer,
        ),
    )


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


class _TurnResolver:
    def __init__(self, product_configs: tuple[ProductConfig, ...]) -> None:
        self._values = {
            "product_type": distinct_values(item.product_type for item in product_configs),
            "config_version": distinct_values(item.config_version for item in product_configs),
            "type_system": distinct_values(item.type_system for item in product_configs),
            "manual_tagging": _MARKING_RESULT_RESOLVER_VALUES,
            "summary_result": _SUMMARY_RESULT_RESOLVER_VALUES,
            "status": _MARKING_RESULT_RESOLVER_VALUES,
        }

    async def resolve(
        self,
        entity_type: str,
        context: dict[str, object] | None = None,
    ) -> list[str]:
        del context
        return list(self._values.get(entity_type, ()))


__all__ = ["ConversationStateRepository", "MaiaTurnHandler", "create_maia_runtime"]
