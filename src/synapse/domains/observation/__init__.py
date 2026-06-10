"""Observation domain support."""

from synapse.domains.observation.pre_recognition import ObservationPreProcessor
from synapse.domains.observation.slots import ObservationSlotResolutionPipeline

__all__ = ["ObservationPreProcessor", "ObservationSlotResolutionPipeline"]
