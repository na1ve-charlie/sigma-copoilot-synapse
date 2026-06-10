"""Generic slot validation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from synapse.engine import TurnContext
from synapse.planning.display import slot_label, slot_prompt_message
from synapse.planning.plans import ClarifyPlan, Prompt, PromptCandidate
from synapse.recognition import CANDIDATE_CATALOG_ARTIFACT, CandidateCatalog
from synapse.slots.contracts import SlotOperation, SlotRef
from synapse.slots.resolution import SLOT_OPERATIONS_ARTIFACT


SLOT_VALIDATION_ARTIFACT = "slot_validation"


@dataclass(frozen=True, slots=True)
class SlotValidationIssue:
    ref: SlotRef
    value: object
    reason: str
    candidates: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SlotValidationResult:
    issues: tuple[SlotValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues


class SlotValidator(Protocol):
    async def validate(
        self,
        operations: tuple[SlotOperation, ...],
        context: TurnContext,
    ) -> SlotValidationResult:
        ...


class GenericSlotValidator:
    """Validate resolved operations against request-scoped candidates when known."""

    async def validate(
        self,
        operations: tuple[SlotOperation, ...],
        context: TurnContext,
    ) -> SlotValidationResult:
        catalog = context.artifacts.get(CANDIDATE_CATALOG_ARTIFACT)
        if not isinstance(catalog, CandidateCatalog):
            return SlotValidationResult()

        issues = []
        for operation in operations:
            if operation.kind == "clear" or operation.ref.name not in catalog.by_entity:
                continue
            allowed = _candidate_values(catalog, operation.ref.name)
            for value in _operation_values(operation.value):
                if str(value) not in allowed:
                    issues.append(
                        SlotValidationIssue(
                            ref=operation.ref,
                            value=value,
                            reason="candidate_not_found",
                            candidates=tuple(sorted(allowed)),
                        )
                    )
        return SlotValidationResult(tuple(issues))


class SlotValidationStep:
    """Attach validation result before slot commit."""

    def __init__(
        self,
        validator: SlotValidator | None = None,
        clarify_builder: "SlotClarifyBuilder | None" = None,
    ) -> None:
        self._validator = validator or GenericSlotValidator()
        self._clarify_builder = clarify_builder or SlotClarifyBuilder()

    async def run(self, context: TurnContext) -> TurnContext:
        operations = context.artifacts.get(SLOT_OPERATIONS_ARTIFACT, ())
        if not isinstance(operations, tuple):
            raise TypeError("slot operations artifact must be a tuple")
        result = await self._validator.validate(operations, context)
        if not result.valid:
            plan = self._clarify_builder.build(result)
            return context.with_plan(plan.model_dump(mode="json"))
        return context.with_artifact(SLOT_VALIDATION_ARTIFACT, result)


class SlotClarifyBuilder:
    """Build a clarify plan for invalid resolved slot values."""

    def build(self, result: SlotValidationResult) -> ClarifyPlan:
        invalid_slots = _dedupe([issue.ref.name for issue in result.issues])
        return ClarifyPlan(
            reason="invalid_slots",
            message="当前参数值无效。",
            invalid_slots=invalid_slots,
            prompts=[
                Prompt(
                    id=slot,
                    target="slot",
                    label=slot_label(slot),
                    message=slot_prompt_message(slot),
                    required=True,
                    input_type="multi_select",
                    candidates=[
                        PromptCandidate(value=value, label=value)
                        for value in _candidates_for_slot(result, slot)
                    ],
                )
                for slot in invalid_slots
            ],
        )


def _candidate_values(catalog: CandidateCatalog, entity_type: str) -> set[str]:
    values = set()
    for item in catalog.candidates_for_entity(entity_type):
        values.add(item.value)
        if item.label:
            values.add(item.label)
    return values


def _operation_values(value: object) -> tuple[object, ...]:
    if isinstance(value, list | tuple):
        return tuple(value)
    return (value,)


def _candidates_for_slot(
    result: SlotValidationResult,
    slot_name: str,
) -> tuple[str, ...]:
    for issue in result.issues:
        if issue.ref.name == slot_name:
            return issue.candidates
    return ()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
