from __future__ import annotations

import os
from typing import Any, Protocol

from maia.api import ClarifyPlan, ReplyPlan, TurnRequest, TurnResponse
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
from maia.presentation import present_turn
from maia.recognition import RecognitionReport, build_maia_recognizer_from_config
from maia.runtime_product_filters import (
    complete_config_version_filter,
    complete_product_type_filter,
    complete_type_system_filter,
    config_version_scope,
    distinct_values,
    is_all_product_types_request,
    product_type_scope,
    selection_expression_for_storage,
    selection_expression_from_storage,
    type_system_scope,
)
from maia.selection import InMemorySelectionSetRepository
from maia.selection.compiler import SelectionQueryCompiler
from maia.selection.service import SelectionSetMaterializer, SelectionSetService
from maia.selection.sets import SelectionSet

_SUMMARY_RESULT_VALUES = (
    "\u4e0d\u5408\u683c",
    "\u5408\u683c",
    "\u672a\u8bbe\u7f6e\u754c\u9650\u503c",
    "\u5f02\u5e38",
    "\u6b21\u5f02\u5e38",
    "\u68c0\u6d4b\u5931\u8d25",
    "NG",
    "OK",
)


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

    def load(self, session_id: str) -> ConversationSelectionState:
        return self._items.get(session_id, ConversationSelectionState())

    def save(
        self,
        session_id: str,
        state: ConversationSelectionState,
    ) -> ConversationSelectionState:
        self._items[session_id] = state
        return state


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

    async def handle_turn(self, request: TurnRequest) -> TurnResponse:
        state = self._state_repository.load(request.session_id)
        product_configs = await self._product_configs(request)
        report = await self._recognizer.recognize(
            request.message,
            resolver=_TurnResolver(product_configs),
            include_diagnostics=False,
        )
        if report.verdict == "low":
            return present_turn(
                ClarifyPlan(
                    reason="low_confidence",
                    message="I could not identify a supported Maia request.",
                )
            )
        if report.requires_confirmation or report.verdict == "ambiguous":
            return present_turn(
                ClarifyPlan(
                    reason="ambiguous_intent",
                    message="Please clarify which request you want Maia to run.",
                )
            )
        if not _is_record_search(report):
            return present_turn(ReplyPlan(message="Maia currently supports record search only."))

        try:
            base = self._resolve_base_selection(report, state)
            draft = self._build_draft(report, state, base)
        except (SelectionReferenceResolutionError, ValueError) as exc:
            return present_turn(ClarifyPlan(reason="ambiguous_slots", message=str(exc)))

        draft, clarify = await self._complete_product_filters(
            request,
            draft,
            allow_all_products=is_all_product_types_request(request.message),
        )
        if clarify is not None:
            self._state_repository.save(
                request.session_id,
                self._selection_store.save_pending(state, draft),
            )
            return present_turn(clarify)

        selection = await self._selection_service.create_or_derive(
            draft.model_copy(
                update={"expression": selection_expression_for_storage(draft.expression)}
            ),
            workspace_context=request.workspace_context,
        )
        self._state_repository.save(
            request.session_id,
            self._selection_store.activate(state, selection.selection_set_id),
        )
        return present_turn(_reply_plan(selection))

    async def _product_configs(self, request: TurnRequest) -> tuple[ProductConfig, ...]:
        if self._product_catalog is None:
            return ()
        return await self._product_catalog.list_configs(
            lang="zh" if request.workspace_context is None else request.workspace_context.lang
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
    ) -> SelectionDraft:
        if state.pending_selection_draft is not None:
            resumed = self._selection_store.resume(
                state,
                report,
                reducer=self._draft_reducer,
            )
            return resumed or state.pending_selection_draft
        return self._draft_reducer.apply(_draft_from_selection(base), report)

    async def _complete_product_filters(
        self,
        request: TurnRequest,
        draft: SelectionDraft,
        *,
        allow_all_products: bool,
    ) -> tuple[SelectionDraft, ClarifyPlan | None]:
        product_records = await self._selection_compiler.records_for_expression(
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

        version_records = await self._selection_compiler.records_for_expression(
            config_version_scope(draft.expression),
            workspace_context=request.workspace_context,
        )
        draft, clarify, config_version = complete_config_version_filter(
            draft,
            version_records,
            reducer=self._draft_reducer,
            product_type=product_type,
        )
        if clarify is not None or config_version is None:
            return draft, clarify

        system_records = await self._selection_compiler.records_for_expression(
            type_system_scope(draft.expression),
            workspace_context=request.workspace_context,
        )
        return complete_type_system_filter(
            draft,
            system_records,
            reducer=self._draft_reducer,
            product_type=product_type,
            config_version=config_version,
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


def _reply_plan(selection: SelectionSet) -> ReplyPlan:
    data = {
        "selection_set_id": selection.selection_set_id,
        "selection_hash": selection.selection_hash,
        "record_count": selection.record_count,
        "record_ids": list(selection.record_ids or ()),
    }
    if selection.dataset_id is not None:
        data["dataset_id"] = selection.dataset_id
    return ReplyPlan(
        message=f"Found {selection.record_count} records.",
        data=data,
    )


class _TurnResolver:
    def __init__(self, product_configs: tuple[ProductConfig, ...]) -> None:
        self._values = {
            "product_type": distinct_values(item.product_type for item in product_configs),
            "config_version": distinct_values(item.config_version for item in product_configs),
            "type_system": distinct_values(item.type_system for item in product_configs),
            "summary_result": _SUMMARY_RESULT_VALUES,
        }

    async def resolve(
        self,
        entity_type: str,
        context: dict[str, object] | None = None,
    ) -> list[str]:
        del context
        return list(self._values.get(entity_type, ()))


__all__ = ["ConversationStateRepository", "MaiaTurnHandler", "create_maia_runtime"]
