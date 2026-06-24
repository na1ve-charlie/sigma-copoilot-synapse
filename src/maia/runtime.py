from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Protocol

from maia.api import ClarifyPlan, ReplyPlan, TurnRequest, TurnResponse
from maia.conversation.state import ConversationSelectionState, ConversationTaskStateStore
from maia.integrations.sigma import (
    ExcelExportClient,
    MutableSigmaTokenProvider,
    NgAudioGenerationClient,
    OriginExportClient,
    SensorListClient,
    SigmaDataObservationCatalogClient,
    SigmaProductCatalogClient,
    SigmaSelectionSetMaterializer,
    SigmaTokenProvider,
    TestRecordManagementClient,
    TestRecordClient,
)
from maia.integrations.sigma.product_catalog import ProductConfig
from maia.presentation import present_turn
from maia.recognition import RecognitionReport, build_maia_recognizer_from_config
from maia.recognition.normalization import (
    MARKING_RESULT_VALUES,
    SUMMARY_RESULT_ALIASES,
    SUMMARY_RESULT_VALUES,
)
from maia.selection import InMemorySelectionSetRepository
from maia.selection.compiler import SelectionQueryCompiler
from maia.selection.service import SelectionSetMaterializer, SelectionSetService
from maia.tasks.audio_generation import AudioGenerationHandler
from maia.tasks.data_observation import DataObservationHandler
from maia.tasks.origin_data_export import OriginDataExportHandler
from maia.tasks.excel_export import ExcelExportHandler
from maia.tasks.record_search import RecordSearchHandler
from maia.tasks.record_search_filters import distinct_values
from maia.tasks.router import TaskContext, TaskRouter
from maia.tasks.test_record_management import TestRecordManagementHandler

_SUMMARY_RESULT_RESOLVER_VALUES = (*SUMMARY_RESULT_VALUES, *(alias.upper() for alias in SUMMARY_RESULT_ALIASES))
_MARKING_RESULT_RESOLVER_VALUES = MARKING_RESULT_VALUES
_PENDING_CANCEL_VALUES = frozenset(
    {"\u53d6\u6d88", "\u4e0d\u7528", "\u4e0d\u8981", "cancel", "no", "n"}
)


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
        origin_export_client: object | None = None,
        excel_export_client: object | None = None,
        audio_generation_client: object | None = None,
        data_observation_catalog: object | None = None,
        sensor_list_client: object | None = None,
        test_record_management_client: object | None = None,
    ) -> None:
        self._recognizer = recognizer
        self._state_repository = state_repository
        self._selection_service = selection_service
        self._selection_compiler = selection_compiler
        self._selection_repository = selection_repository
        self._product_catalog = product_catalog
        record_search_handler = RecordSearchHandler(
            selection_service=selection_service,
            selection_compiler=selection_compiler,
            selection_repository=selection_repository,
            dataset_binding_store=state_repository,
        )
        self._task_router = TaskRouter(
            (
                ExcelExportHandler(
                    record_search=record_search_handler,
                    selection_repository=selection_repository,
                    exporter=excel_export_client,
                    sensor_lister=sensor_list_client,
                ),
                OriginDataExportHandler(
                    record_search=record_search_handler,
                    selection_repository=selection_repository,
                    exporter=origin_export_client,
                ),
                AudioGenerationHandler(
                    record_search=record_search_handler,
                    selection_repository=selection_repository,
                    generator=audio_generation_client,
                ),
                TestRecordManagementHandler(
                    record_search=record_search_handler,
                    selection_repository=selection_repository,
                    manager=test_record_management_client,
                ),
                DataObservationHandler(
                    record_search=record_search_handler,
                    selection_repository=selection_repository,
                    catalog=data_observation_catalog,
                ),
                record_search_handler,
            )
        )

    async def handle_turn(self, request: TurnRequest) -> TurnResponse:
        state = self._state_repository.load(request.session_id)
        if _is_pending_prompt_cancellation(request, state):
            state = ConversationTaskStateStore().cancel_pending(state)
            self._state_repository.save(request.session_id, state)
            return present_turn(ReplyPlan(message="Pending task cancelled."))
        product_configs = await self._product_configs(request)
        report = (
            _empty_report(request)
            if request.prompt_replies or state.pending_confirmation is not None
            else await self._recognize(request, product_configs)
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

        result = await self._task_router.handle(TaskContext(request, report, state))
        self._state_repository.save(request.session_id, result.state)
        return present_turn(result.plan)

    async def _product_configs(self, request: TurnRequest) -> tuple[ProductConfig, ...]:
        if self._product_catalog is None:
            return ()
        return await self._product_catalog.list_configs(
            lang="zh" if request.workspace_context is None else request.workspace_context.lang
        )

    async def _recognize(
        self,
        request: TurnRequest,
        product_configs: tuple[ProductConfig, ...],
    ) -> RecognitionReport:
        report = await self._recognizer.recognize(
            request.message,
            resolver=_TurnResolver(product_configs),
            include_diagnostics=False,
        )
        return report


def create_maia_runtime(
    *,
    recognizer: Recognizer | None = None,
    record_client: object | None = None,
    state_repository: ConversationStateRepository | None = None,
    selection_repository: InMemorySelectionSetRepository | None = None,
    product_catalog: ProductCatalog | None = None,
    selection_materializer: SelectionSetMaterializer | None = None,
    origin_export_client: object | None = None,
    excel_export_client: object | None = None,
    audio_generation_client: object | None = None,
    data_observation_catalog: object | None = None,
    sensor_list_client: object | None = None,
    test_record_management_client: object | None = None,
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
    origin_export_client = origin_export_client or OriginExportClient(
        base_url=sigma_base_url,
        token_provider=sigma_token_provider,
    )
    excel_export_client = excel_export_client or ExcelExportClient(
        base_url=sigma_base_url,
        token_provider=sigma_token_provider,
    )
    audio_generation_client = audio_generation_client or NgAudioGenerationClient(
        base_url=sigma_base_url,
        token_provider=sigma_token_provider,
    )
    data_observation_catalog = data_observation_catalog or SigmaDataObservationCatalogClient(
        base_url=sigma_base_url,
        token_provider=sigma_token_provider,
    )
    sensor_list_client = sensor_list_client or SensorListClient(
        base_url=sigma_base_url,
        token_provider=sigma_token_provider,
    )
    test_record_management_client = test_record_management_client or TestRecordManagementClient(
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
        origin_export_client=origin_export_client,
        excel_export_client=excel_export_client,
        audio_generation_client=audio_generation_client,
        data_observation_catalog=data_observation_catalog,
        sensor_list_client=sensor_list_client,
        test_record_management_client=test_record_management_client,
        selection_service=SelectionSetService(
            selection_repository,
            selection_compiler,
            source_version=source_version,
            materializer=selection_materializer,
        ),
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


def _empty_report(request: TurnRequest) -> RecognitionReport:
    return RecognitionReport(
        message=request.message,
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
    )


def _is_pending_prompt_cancellation(
    request: TurnRequest,
    state: ConversationSelectionState,
) -> bool:
    if state.pending_confirmation is not None:
        return False
    if state.pending_selection_draft is None and state.pending_task is None:
        return False
    return request.message.strip().casefold() in _PENDING_CANCEL_VALUES


__all__ = ["ConversationStateRepository", "MaiaTurnHandler", "create_maia_runtime"]
