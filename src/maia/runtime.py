from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any, Protocol

from maia.api import ClarifyPlan, Prompt, PromptCandidate, ReplyPlan, TurnRequest, TurnResponse
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
from maia.selection import InMemorySelectionSetRepository
from maia.selection.compiler import SelectionQueryCompiler
from maia.selection.expression import AllOf, AnyOf, FilterExpression, Not, Predicate
from maia.selection.service import SelectionSetMaterializer, SelectionSetService
from maia.selection.sets import SelectionSet

_PRODUCT_PREDICATES = {
    "product_type_in": "product_type",
    "config_version_in": "config_version",
    "type_system_in": "type_system",
}
_SUMMARY_RESULT_VALUES = (
    "不合格",
    "合格",
    "未设置界限值",
    "异常",
    "次异常",
    "检测失败",
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

    def save(self, session_id: str, state: ConversationSelectionState) -> ConversationSelectionState:
        self._items[session_id] = state
        return state


class MaiaTurnHandler:
    def __init__(
        self,
        *,
        recognizer: Recognizer,
        state_repository: ConversationStateRepository,
        selection_service: SelectionSetService,
        selection_repository: InMemorySelectionSetRepository,
        product_catalog: ProductCatalog | None = None,
    ) -> None:
        self._recognizer = recognizer
        self._state_repository = state_repository
        self._selection_service = selection_service
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
            return present_turn(ClarifyPlan(reason="low_confidence", message="I could not identify a supported Maia request."))
        if report.requires_confirmation or report.verdict == "ambiguous":
            return present_turn(ClarifyPlan(reason="ambiguous_intent", message="Please clarify which request you want Maia to run."))
        if not _is_record_search(report):
            return present_turn(ReplyPlan(message="Maia currently supports record search only."))

        try:
            base = self._resolve_base_selection(report, state)
            draft = self._build_draft(report, state, base)
        except (SelectionReferenceResolutionError, ValueError) as exc:
            return present_turn(ClarifyPlan(reason="ambiguous_slots", message=str(exc)))
        if self._product_catalog is not None:
            draft, clarify = _complete_product_filters(draft, product_configs, reducer=self._draft_reducer)
            if clarify is not None:
                self._state_repository.save(
                    request.session_id,
                    self._selection_store.save_pending(state, draft),
                )
                return present_turn(clarify)
        if draft.expression is None:
            return present_turn(ClarifyPlan(reason="ambiguous_slots", message="Please add at least one supported search filter."))

        selection = await self._selection_service.create_or_derive(draft, workspace_context=request.workspace_context)
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
            resumed = self._selection_store.resume(state, report, reducer=self._draft_reducer)
            return resumed or state.pending_selection_draft
        return self._draft_reducer.apply(_draft_from_selection(base), report)


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
    return MaiaTurnHandler(
        recognizer=recognizer or build_maia_recognizer_from_config(),
        state_repository=state_repository or ConversationStateRepository(),
        selection_repository=selection_repository,
        product_catalog=product_catalog,
        selection_service=SelectionSetService(
            selection_repository,
            SelectionQueryCompiler(record_client),
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
        expression=selection.expression,
        sort=tuple(SelectionSort(field=item.field, direction=item.direction) for item in selection.sort),
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


def _complete_product_filters(
    draft: SelectionDraft,
    product_configs: tuple[ProductConfig, ...],
    *,
    reducer: SelectionDraftReducer,
) -> tuple[SelectionDraft, ClarifyPlan | None]:
    if draft.expression is None or not product_configs:
        return draft, None
    product_types = _distinct(item.product_type for item in product_configs)
    selected_type = _single_selected_value(draft.expression, "product_type")
    if selected_type is None:
        return draft, _clarify_missing("product_type", product_types)
    if selected_type not in product_types:
        return draft, _clarify_invalid("product_type", product_types)

    type_scoped = tuple(item for item in product_configs if item.product_type == selected_type)
    versions = _distinct(item.config_version for item in type_scoped)
    selected_version = _single_selected_value(draft.expression, "config_version")
    if selected_version is None:
        if len(versions) != 1:
            return draft, _clarify_missing("config_version", versions)
        draft = _apply_auto_slots(draft, reducer=reducer, config_version=versions[0])
        selected_version = versions[0]
    elif selected_version not in versions:
        return draft, _clarify_invalid("config_version", versions)

    version_scoped = tuple(
        item
        for item in type_scoped
        if item.config_version == selected_version
    )
    systems = _distinct(item.type_system for item in version_scoped)
    selected_system = _single_selected_value(draft.expression, "type_system")
    if selected_system is None:
        if len(systems) > 1:
            return draft, _clarify_missing("type_system", systems)
        if len(systems) == 1:
            draft = _apply_auto_slots(draft, reducer=reducer, type_system=systems[0])
        return draft, None
    if selected_system not in systems:
        return draft, _clarify_invalid("type_system", systems)
    return draft, None


def _single_selected_value(expression: FilterExpression, entity_type: str) -> str | None:
    values = _selected_values(expression, entity_type)
    return values[0] if len(values) == 1 else None


def _selected_values(expression: FilterExpression, entity_type: str) -> tuple[str, ...]:
    result: list[str] = []
    for predicate in _predicates(expression):
        if _PRODUCT_PREDICATES.get(predicate.name) != entity_type:
            continue
        raw = predicate.params.get("values")
        values = raw if isinstance(raw, tuple) else (raw,)
        result.extend(str(value) for value in values if value not in (None, ""))
    return _distinct(result)


def _predicates(expression: FilterExpression) -> Iterable[Predicate]:
    if isinstance(expression, Predicate):
        yield expression
        return
    if isinstance(expression, Not):
        return
    children = expression.expressions if isinstance(expression, (AllOf, AnyOf)) else ()
    for child in children:
        yield from _predicates(child)


def _apply_auto_slots(
    draft: SelectionDraft,
    *,
    reducer: SelectionDraftReducer,
    **updates: str,
) -> SelectionDraft:
    return reducer.apply(
        draft,
        RecognitionReport(
            message="auto-fill product filters",
            verdict="clear",
            requires_confirmation=False,
            degraded=False,
            slot_operations=tuple(
                {
                    "intent": f"task.nvh.selection.set_{entity_type}",
                    "score": 1.0,
                    "action": "replace",
                    "entity_type": entity_type,
                    "target": target,
                    "slot_valid": True,
                }
                for entity_type, target in updates.items()
            ),
        ),
    )


def _clarify_missing(slot: str, values: tuple[str, ...]) -> ClarifyPlan:
    return ClarifyPlan(
        reason="missing_slots",
        message=f"Please choose {slot.replace('_', ' ')} before searching records.",
        missing_slots=[slot],
        prompts=[_slot_prompt(slot, values)],
        suggestions=list(values),
    )


def _clarify_invalid(slot: str, values: tuple[str, ...]) -> ClarifyPlan:
    return ClarifyPlan(
        reason="invalid_slots",
        message=f"Please choose a valid {slot.replace('_', ' ')}.",
        invalid_slots=[slot],
        prompts=[_slot_prompt(slot, values)],
        suggestions=list(values),
    )


def _slot_prompt(slot: str, values: tuple[str, ...]) -> Prompt:
    return Prompt(
        id=slot,
        target="slot",
        label=slot.replace("_", " "),
        message=f"Choose {slot.replace('_', ' ')}.",
        required=True,
        input_type="single_select",
        candidates=[PromptCandidate(value=value, label=value) for value in values],
    )


def _distinct(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


class _TurnResolver:
    def __init__(self, product_configs: tuple[ProductConfig, ...]) -> None:
        self._values = {
            "product_type": _distinct(item.product_type for item in product_configs),
            "config_version": _distinct(item.config_version for item in product_configs),
            "type_system": _distinct(item.type_system for item in product_configs),
            "summary_result": _SUMMARY_RESULT_VALUES,
        }

    async def resolve(self, entity_type: str, context: dict[str, object] | None = None) -> list[str]:
        del context
        return list(self._values.get(entity_type, ()))


__all__ = ["ConversationStateRepository", "MaiaTurnHandler", "create_maia_runtime"]
