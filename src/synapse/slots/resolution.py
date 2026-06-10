"""Slot resolution pipeline for converting recognition output into operations."""

from __future__ import annotations

from typing import Protocol

from synapse.engine import TurnContext
from synapse.recognition import CANDIDATE_CATALOG_ARTIFACT, CandidateCatalog
from synapse.slots.contracts import SlotOperation, SlotRef
from synapse.slots.themis import operations_from_decision


SLOT_OPERATIONS_ARTIFACT = "slot_operations"
CLEAR_CONTEXT_INTENT = "task.nvh.context_management.clear_context"
CLEAR_ALL_SLOT_REF = SlotRef("context", "*")


class SlotResolutionPipeline(Protocol):
    async def resolve(self, context: TurnContext) -> tuple[SlotOperation, ...]:
        ...


class ThemisSlotResolutionPipeline:
    """Resolve public Themis slot operations into Synapse slot operations."""

    def __init__(self, *, decision_artifact: str = "intent_decision") -> None:
        self._decision_artifact = decision_artifact

    async def resolve(self, context: TurnContext) -> tuple[SlotOperation, ...]:
        decision = context.artifacts.get(self._decision_artifact)
        if _has_intent(decision, CLEAR_CONTEXT_INTENT):
            return (SlotOperation.clear(CLEAR_ALL_SLOT_REF, source="themis"),)
        catalog = context.artifacts.get(CANDIDATE_CATALOG_ARTIFACT)
        return operations_from_decision(
            decision,
            catalog=catalog if isinstance(catalog, CandidateCatalog) else None,
        )


class SlotResolutionStep:
    """Inject resolved slot operations into the turn context."""

    def __init__(self, pipeline: SlotResolutionPipeline | None = None) -> None:
        self._pipeline = pipeline or ThemisSlotResolutionPipeline()

    async def run(self, context: TurnContext) -> TurnContext:
        operations = await self._pipeline.resolve(context)
        return context.with_artifact(SLOT_OPERATIONS_ARTIFACT, operations)


def _has_intent(decision: object, intent_name: str) -> bool:
    for intent in getattr(decision, "action_intents", ()) or ():
        if getattr(intent, "name", None) == intent_name:
            return True
        if isinstance(intent, dict) and intent.get("name") == intent_name:
            return True
    return False
