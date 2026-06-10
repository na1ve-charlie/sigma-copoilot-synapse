"""Observation slot resolution policy."""

from __future__ import annotations

from collections import defaultdict

from synapse.engine import TurnContext
from synapse.slots.contracts import SlotOperation, SlotRef
from synapse.slots.resolution import SlotResolutionPipeline, ThemisSlotResolutionPipeline


OBSERVATION_DOMAIN_ID = "nvh.data_observation"
OBSERVATION_MULTI_SLOTS = frozenset(
    {
        "sensors",
        "test_segments",
        "indicator_names",
    }
)
OBSERVATION_SLOT_NAMES = OBSERVATION_MULTI_SLOTS | {"data_types"}


class ObservationSlotResolutionPipeline:
    """Apply observation slot schema semantics after generic Themis resolution."""

    def __init__(self, inner: SlotResolutionPipeline | None = None) -> None:
        self._inner = inner or ThemisSlotResolutionPipeline()

    async def resolve(self, context: TurnContext) -> tuple[SlotOperation, ...]:
        operations = await self._inner.resolve(context)
        operations = _scope_observation_slots(operations)
        return _coalesce_multi_replaces(operations)


def _scope_observation_slots(
    operations: tuple[SlotOperation, ...],
) -> tuple[SlotOperation, ...]:
    return tuple(_scope_observation_slot(operation) for operation in operations)


def _scope_observation_slot(operation: SlotOperation) -> SlotOperation:
    if operation.ref.name not in OBSERVATION_SLOT_NAMES:
        return operation
    return SlotOperation(
        ref=SlotRef(OBSERVATION_DOMAIN_ID, operation.ref.name),
        kind=operation.kind,
        value=operation.value,
        source=operation.source,
    )


def _coalesce_multi_replaces(
    operations: tuple[SlotOperation, ...],
) -> tuple[SlotOperation, ...]:
    replace_refs = _all_replace_multi_refs(operations)
    if not replace_refs:
        return operations

    values: dict[SlotRef, list[object]] = defaultdict(list)
    emitted: set[SlotRef] = set()
    result = []
    for operation in operations:
        if operation.ref not in replace_refs:
            result.append(operation)
            continue

        if operation.value not in values[operation.ref]:
            values[operation.ref].append(operation.value)
        if operation.ref in emitted:
            continue

        emitted.add(operation.ref)
        result.append(
            SlotOperation.replace(
                operation.ref,
                values[operation.ref],
                source=operation.source,
            )
        )
    return tuple(result)


def _all_replace_multi_refs(
    operations: tuple[SlotOperation, ...],
) -> set[SlotRef]:
    by_ref: dict[SlotRef, list[SlotOperation]] = defaultdict(list)
    for operation in operations:
        if operation.ref.name in OBSERVATION_MULTI_SLOTS:
            by_ref[operation.ref].append(operation)
    return {
        ref
        for ref, ref_operations in by_ref.items()
        if ref_operations and all(item.kind == "replace" for item in ref_operations)
    }
