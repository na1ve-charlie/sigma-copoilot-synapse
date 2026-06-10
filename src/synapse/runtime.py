"""Runtime factory for the default Synapse turn handler."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from themis import ResolverProvider

from synapse.domains.observation import (
    ObservationPreProcessor,
    ObservationSlotResolutionPipeline,
)
from synapse.domains.observation.autofill import ObservationAutofillPolicy
from synapse.domains.observation.indicator_inference import (
    ObservationIndicatorInferenceStep,
)
from synapse.domains.observation.pending_request import (
    InMemoryPendingObservationRequestStore,
    PendingObservationRequestCommitterStep,
    PendingObservationRequestLoaderStep,
)
from synapse.domains.observation.resolver_query import (
    ObservationResolverQueryResponder,
)
from synapse.domains.observation.resolver_query_normalization import (
    ObservationResolverQueryNormalizationStep,
)
from synapse.domains.observation.sigma_catalog import (
    SigmaObservationCatalogSource,
)
from synapse.domains.observation.task_params import ObservationTaskParamProvider
from synapse.engine import SynapseConductor
from synapse.integrations.sigma import SigmaCandidateCatalogLoader
from synapse.planning.planner import PlanningStep
from synapse.planning.resolver_query import ResolverQueryHandler
from synapse.planning.tasks import TaskCatalog, TaskPlanBuilder
from synapse.recognition import CandidateCatalogLoader, CandidateCatalogStep
from synapse.recognition.themis import (
    DEFAULT_INTENT_CONFIG_DIR,
    DEFAULT_THEMIS_CONFIG,
    IntentRecognizer,
    LazyThemisRecognizer,
    ThemisRecognitionStep,
)
from synapse.recognition.preprocessing.arbiter import PreRecognitionArbiter
from synapse.recognition.preprocessing.pipeline import (
    PreRecognitionPipeline,
    PreRecognitionProcessorPipeline,
    PreRecognitionStep,
)
from synapse.slots.committer import SlotCommitterStep
from synapse.slots.resolution import SlotResolutionStep
from synapse.session.task_context import (
    InMemoryTaskContextStore,
    TaskContextCommitterStep,
    TaskContextLoaderStep,
)
from synapse.slots.state import SlotState
from synapse.slots.validation import SlotValidationStep


DEFAULT_TASK_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "tasks"


def create_synapse_runtime(
    *,
    recognizer: IntentRecognizer | None = None,
    themis_config_path: str | Path = DEFAULT_THEMIS_CONFIG,
    themis_resolver: ResolverProvider | None = None,
    intent_config_dir: str | Path = DEFAULT_INTENT_CONFIG_DIR,
    intent_config_paths: Sequence[str | Path] | None = None,
    slot_state: SlotState | None = None,
    task_catalog: TaskCatalog | None = None,
    task_config_dir: str | Path = DEFAULT_TASK_CONFIG_DIR,
    task_config_paths: Sequence[str | Path] | None = None,
    candidates: Mapping[str, Sequence[Any]] | None = None,
    candidate_catalog_loader: CandidateCatalogLoader | None = None,
    resolver_query_handler: ResolverQueryHandler | None = None,
) -> SynapseConductor:
    """Create the default Synapse conductor with injectable runtime dependencies."""

    catalog = task_catalog or _load_task_catalog(task_config_dir, task_config_paths)
    loader = _candidate_catalog_loader(candidate_catalog_loader)
    observation_catalog_source = _observation_catalog_source(loader)
    data_types_by_task_name = _data_types_by_task_name(loader, catalog)
    task_context_store = InMemoryTaskContextStore()
    pending_observation_store = InMemoryPendingObservationRequestStore()
    recognizer = recognizer or LazyThemisRecognizer(
        config_path=themis_config_path,
        intent_config_dir=intent_config_dir,
        intent_config_paths=intent_config_paths,
    )
    observation_indicator_step = _observation_indicator_inference_step(
        observation_catalog_source,
        catalog,
        data_types_by_task_name,
    )
    steps = [
        TaskContextLoaderStep(task_context_store),
        PendingObservationRequestLoaderStep(pending_observation_store),
        CandidateCatalogStep(loader),
        _pre_recognition_step(),
        ThemisRecognitionStep(recognizer),
    ]
    if observation_indicator_step is not None:
        steps.append(observation_indicator_step)
    if observation_catalog_source is not None:
        steps.append(ObservationResolverQueryNormalizationStep())
    steps.extend(
        [
            SlotResolutionStep(ObservationSlotResolutionPipeline()),
            SlotValidationStep(),
            SlotCommitterStep(
                slot_state,
                post_commit_policy=_observation_autofill_policy(
                    observation_catalog_source,
                    catalog,
                    data_types_by_task_name,
                ),
            ),
            PlanningStep(
                TaskPlanBuilder(
                    catalog,
                    candidates=candidates,
                    resolver_query_handler=(
                        resolver_query_handler
                        or _resolver_query_handler(observation_catalog_source, catalog)
                    ),
                    task_param_providers=_task_param_providers(data_types_by_task_name),
                )
            ),
            TaskContextCommitterStep(task_context_store),
            PendingObservationRequestCommitterStep(pending_observation_store),
        ]
    )
    return SynapseConductor(
        steps
    )


def _load_task_catalog(
    task_config_dir: str | Path,
    task_config_paths: Sequence[str | Path] | None,
) -> TaskCatalog:
    if task_config_paths is not None:
        return TaskCatalog.from_yamls(task_config_paths)
    return TaskCatalog.from_directory(task_config_dir)


def _candidate_catalog_loader(
    loader: CandidateCatalogLoader | None,
) -> CandidateCatalogLoader:
    return loader or SigmaCandidateCatalogLoader.from_yaml()


def _pre_recognition_step() -> PreRecognitionStep:
    return PreRecognitionStep(
        PreRecognitionPipeline(
            global_pipeline=PreRecognitionProcessorPipeline(),
            domain_pipeline=PreRecognitionProcessorPipeline(
                [ObservationPreProcessor()]
            ),
            arbiter=PreRecognitionArbiter(),
        )
    )


def _resolver_query_handler(
    catalog_source: SigmaObservationCatalogSource | None,
    catalog: TaskCatalog,
) -> ResolverQueryHandler | None:
    if catalog_source is None:
        return None
    return ObservationResolverQueryResponder(
        catalog_source=catalog_source,
        action_name_by_intent={
            intent_name: task.name
            for task in catalog._tasks
            for intent_name in task.intent_names
        },
    )


def _observation_indicator_inference_step(
    catalog_source: SigmaObservationCatalogSource | None,
    catalog: TaskCatalog,
    data_types_by_task_name: Mapping[str, tuple[str, ...]],
) -> ObservationIndicatorInferenceStep | None:
    if catalog_source is None:
        return None
    data_types_by_task_intent = {
        intent_name: data_types_by_task_name.get(task.name, ())
        for task in catalog._tasks
        for intent_name in task.intent_names
    }
    task_intent_by_data_type = {
        data_type: intent_name
        for task in catalog._tasks
        for intent_name in task.intent_names[:1]
        for data_type in data_types_by_task_name.get(task.name, ())
    }
    return ObservationIndicatorInferenceStep(
        task_intent_by_data_type=task_intent_by_data_type,
        data_types_by_task_intent=data_types_by_task_intent,
    )


def _task_param_providers(
    data_types_by_task_name: Mapping[str, Sequence[str]],
) -> tuple[ObservationTaskParamProvider, ...]:
    if not data_types_by_task_name:
        return ()
    return (
        ObservationTaskParamProvider(
            data_types_by_task_name=data_types_by_task_name
        ),
    )


def _data_types_by_task_name(
    loader: CandidateCatalogLoader,
    catalog: TaskCatalog,
) -> dict[str, tuple[str, ...]]:
    if not isinstance(loader, SigmaCandidateCatalogLoader):
        return {}
    return {
        task.name: tuple(loader.gateway.domains_for_action(task.name))
        for task in catalog._tasks
    }


def _observation_catalog_source(
    loader: CandidateCatalogLoader,
) -> SigmaObservationCatalogSource | None:
    if not isinstance(loader, SigmaCandidateCatalogLoader):
        return None
    return SigmaObservationCatalogSource(loader.gateway)


def _observation_autofill_policy(
    catalog_source: SigmaObservationCatalogSource | None,
    catalog: TaskCatalog,
    data_types_by_task_name: Mapping[str, Sequence[str]],
) -> ObservationAutofillPolicy | None:
    if catalog_source is None:
        return None
    return ObservationAutofillPolicy(
        catalog_source=catalog_source,
        action_name_by_intent={
            intent_name: task.name
            for task in catalog._tasks
            for intent_name in task.intent_names
        },
        data_types_by_task_name=data_types_by_task_name,
    )
