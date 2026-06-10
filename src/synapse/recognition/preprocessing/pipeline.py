"""Pipeline adapters for pre-recognition processing."""

from __future__ import annotations

from collections.abc import Sequence

from synapse.engine import TurnContext
from synapse.recognition.candidates import CANDIDATE_CATALOG_ARTIFACT
from synapse.recognition.preprocessing.arbiter import PreRecognitionArbiter
from synapse.recognition.preprocessing.contracts import (
    PreRecognitionContext,
    PreRecognitionEffect,
    PreRecognitionProcessor,
    PreRecognitionResult,
)


class PreRecognitionProcessorPipeline:
    """Runs a sorted list of processors against one context."""

    def __init__(self, processors: Sequence[PreRecognitionProcessor] = ()) -> None:
        self._processors = tuple(
            sorted(processors, key=lambda item: (item.priority, item.domain_id))
        )

    def run(self, context: PreRecognitionContext) -> tuple[PreRecognitionEffect, ...]:
        return tuple(
            processor.propose(context)
            for processor in self._processors
            if processor.matches(context)
        )


class PreRecognitionPipeline:
    """Runs global processors, then domain processors, then merges proposals."""

    def __init__(
        self,
        *,
        global_pipeline: PreRecognitionProcessorPipeline,
        domain_pipeline: PreRecognitionProcessorPipeline,
        arbiter: PreRecognitionArbiter,
    ) -> None:
        self._global_pipeline = global_pipeline
        self._domain_pipeline = domain_pipeline
        self._arbiter = arbiter

    def run(self, context: PreRecognitionContext) -> PreRecognitionResult:
        global_effects = self._global_pipeline.run(context)
        global_result = self._arbiter.merge(context, global_effects)

        domain_context = context.with_message(global_result.message)
        domain_effects = self._domain_pipeline.run(domain_context)

        return self._arbiter.merge(
            domain_context,
            (*global_effects, *domain_effects),
        )


class PreRecognitionStep:
    """Conductor step adapter for pre-recognition."""

    artifact_key = "pre_recognition"

    def __init__(self, pipeline: PreRecognitionPipeline) -> None:
        self._pipeline = pipeline

    async def run(self, context: TurnContext) -> TurnContext:
        pre_context = PreRecognitionContext(
            request=context.request,
            message=context.message,
            artifacts=context.artifacts,
            diagnostics=context.diagnostics,
        )

        result = self._pipeline.run(pre_context)

        updated = context.with_message(result.message).with_artifact(
            self.artifact_key,
            result,
        )
        if result.candidate_catalog is not None:
            updated = updated.with_artifact(
                CANDIDATE_CATALOG_ARTIFACT,
                result.candidate_catalog,
            )
        return updated
