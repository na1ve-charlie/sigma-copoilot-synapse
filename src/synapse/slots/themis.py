"""Adapt Themis slot operations into Synapse slot state operations."""

from __future__ import annotations

from typing import Any

from synapse.recognition import CandidateCatalog
from synapse.slots.contracts import SlotOperation, SlotRef


_ENTITY_TYPE_BY_INTENT_SUFFIX = {
    ".switch_sensor": "sensor",
    ".switch_test_segment": "test_segment",
    ".switch_indicator": "indicator",
    ".switch_data_type": "data_type",
}
_SLOT_NAMES_BY_TARGET_PRIORITY = (
    "sensors",
    "test_segments",
    "indicator_names",
    "data_types",
)


def operations_from_decision(
    decision: Any,
    *,
    catalog: CandidateCatalog | None = None,
    domain_id: str = "recognition",
) -> tuple[SlotOperation, ...]:
    """Convert public Themis slot operations to internal slot operations."""

    operations = []
    for operation in getattr(decision, "slot_operations", ()) or ():
        for action, target, valid in zip(
            _as_list(getattr(operation, "action", None)),
            _as_list(getattr(operation, "target", None)),
            _as_list(getattr(operation, "slot_valid", True)),
        ):
            if valid is False:
                continue
            slot_operation = _slot_operation(
                str(action),
                _slot_ref(_entity_type(operation), target, catalog, domain_id),
                target,
            )
            if slot_operation is not None:
                operations.append(slot_operation)
    return tuple(operations)


def _slot_operation(
    action: str,
    ref: SlotRef,
    target: Any,
) -> SlotOperation | None:
    if action in {"replace", "switch"}:
        return SlotOperation.replace(ref, target, source="themis")
    if action == "add":
        return SlotOperation.add(ref, target, source="themis")
    if action == "remove":
        return SlotOperation.remove(ref, target, source="themis")
    if action == "clear":
        return SlotOperation.clear(ref, source="themis")
    return None


def _slot_ref(
    entity_type: Any,
    target: Any,
    catalog: CandidateCatalog | None,
    domain_id: str,
) -> SlotRef:
    entity = str(entity_type)
    return SlotRef(domain_id, _slot_name(entity, target, catalog))


def _entity_type(operation: Any) -> Any:
    value = getattr(operation, "entity_type", "")
    if value:
        return value
    for intent_name in _as_list(getattr(operation, "intent", "")):
        intent = str(intent_name)
        for suffix, entity_type in _ENTITY_TYPE_BY_INTENT_SUFFIX.items():
            if intent.endswith(suffix):
                return entity_type
    return value


def _slot_name(
    entity_type: str,
    target: Any,
    catalog: CandidateCatalog | None,
) -> str:
    for candidate in _slot_name_candidates(entity_type):
        if catalog is not None and _candidate_matches(catalog, candidate, target):
            return candidate
    matched = _slot_name_for_target(catalog, target)
    if matched is not None:
        return matched
    for candidate in _slot_name_candidates(entity_type):
        if catalog is not None and catalog.candidates_for_entity(candidate):
            return candidate
    return entity_type


def _slot_name_candidates(entity_type: str) -> tuple[str, ...]:
    return (f"{entity_type}s", f"{entity_type}_names", entity_type)


def _slot_name_for_target(
    catalog: CandidateCatalog | None,
    target: Any,
) -> str | None:
    if catalog is None:
        return None
    for slot_name in _SLOT_NAMES_BY_TARGET_PRIORITY:
        if _candidate_matches(catalog, slot_name, target):
            return slot_name
    return None


def _candidate_matches(
    catalog: CandidateCatalog,
    entity_type: str,
    target: Any,
) -> bool:
    target_text = str(target)
    return any(
        target_text in {item.value, item.label}
        for item in catalog.candidates_for_entity(entity_type)
    )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]
