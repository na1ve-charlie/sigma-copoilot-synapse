from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, field_validator

from maia.recognition.report import RecognitionReport, RecognitionSlotOperation
from maia.selection.expression import AllOf, AnyOf, FilterExpression, Not, Predicate


Scalar: TypeAlias = str | int | float | bool
_PREDICATE_NAMES = {
    "archive_status": "archive_status_in",
    "artifact_availability": "artifact_availability_in",
    "config_version": "config_version_in",
    "data_kind": "data_kind_in",
    "indicator": "indicator_in",
    "manual_tag": "manual_tag_in",
    "product_type": "product_type_in",
    "repeat_serial": "repeat_serial_in",
    "sensor": "sensor_in",
    "serial_number": "serial_number_in",
    "summary_result": "summary_result_in",
    "test_segment": "test_segment_in",
    "time_range": "time_range_in",
    "type_system": "type_system_in",
}
_ENTITY_TYPES = {name: entity for entity, name in _PREDICATE_NAMES.items()}


class SelectionSort(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: str
    direction: Literal["asc", "desc"] = "asc"


class SelectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_selection_id: str | None = None
    expression: FilterExpression | None = None
    sort: tuple[SelectionSort, ...] = ()
    limit: int | None = None
    pending_questions: tuple[str, ...] = ()
    revision: int = 0

    @field_validator("limit")
    @classmethod
    def _validate_limit(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("limit must be positive")
        return value


@dataclass(frozen=True)
class _Batch:
    entity_type: str
    action: str
    targets: tuple[Scalar, ...]


class SelectionDraftReducer:
    def apply(self, draft: SelectionDraft | None, report: RecognitionReport) -> SelectionDraft:
        current = draft or SelectionDraft()
        batches, operator = _collect_batches(report.slot_operations)
        if not batches:
            return current

        order, positive, negatives = _split_expression(current.expression)
        limit, sort, changed = current.limit, current.sort, False
        for batch in batches:
            if batch.entity_type == "latest_n":
                next_limit = None if batch.action in {"remove", "clear"} else int(batch.targets[-1])
                next_sort = () if next_limit is None else (SelectionSort(field="tested_at", direction="desc"),)
                changed |= next_limit != limit or next_sort != sort
                limit, sort = next_limit, next_sort
                continue
            changed |= _apply_batch(order, positive, negatives, batch, operator)

        if not changed:
            return current
        return current.model_copy(
            update={
                "expression": _build_expression(order, positive, negatives),
                "sort": sort,
                "limit": limit,
                "pending_questions": (),
                "revision": current.revision + 1,
            }
        )

    def clear(self, draft: SelectionDraft | None) -> SelectionDraft:
        current = draft or SelectionDraft()
        if current.expression is None and current.limit is None and not current.sort:
            return current
        return current.model_copy(
            update={"expression": None, "sort": (), "limit": None, "pending_questions": (), "revision": current.revision + 1}
        )


def _collect_batches(slot_operations: Sequence[RecognitionSlotOperation]) -> tuple[list[_Batch], str | None]:
    batches: list[_Batch] = []
    operator: str | None = None
    for slot_operation in slot_operations:
        for entity_type, action, target in _flatten_slot_operation(slot_operation):
            if entity_type == "filter_operator":
                operator = str(target)
                continue
            if entity_type == "selection_reference":
                continue
            if batches and batches[-1].entity_type == entity_type and batches[-1].action == action:
                batches[-1] = _Batch(entity_type, action, batches[-1].targets + (target,))
            else:
                batches.append(_Batch(entity_type, action, (target,)))
    return batches, operator


def _flatten_slot_operation(slot_operation: RecognitionSlotOperation) -> tuple[tuple[str, str, Scalar], ...]:
    targets = _as_tuple(slot_operation.target)
    size = max(len(_as_tuple(slot_operation.action)), len(targets), len(_as_tuple(slot_operation.slot_valid)))
    actions = _broadcast(_as_tuple(slot_operation.action), size)
    values = _broadcast(targets, size)
    valids = _broadcast(_as_tuple(slot_operation.slot_valid), size)
    rows: list[tuple[str, str, Scalar]] = []
    for action, target, valid in zip(actions, values, valids, strict=True):
        if not valid:
            raise ValueError(f"slot operation for {slot_operation.entity_type} is invalid")
        rows.append((slot_operation.entity_type, str(action), target))
    return tuple(rows)


def _apply_batch(
    order: list[str],
    positive: dict[str, FilterExpression],
    negatives: list[Not],
    batch: _Batch,
    operator: str | None,
) -> bool:
    entity_type, targets = batch.entity_type, _unique(batch.targets)
    if entity_type not in _PREDICATE_NAMES:
        raise ValueError(f"unsupported selection entity_type: {entity_type}")

    action = "exclude" if operator == "not" and batch.action in {"add", "replace"} else batch.action
    if action == "clear":
        removed = positive.pop(entity_type, None) is not None
        order[:] = [name for name in order if name != entity_type]
        negatives[:], negative_changed = _prune_negatives(negatives, entity_type, drop_all=True)
        return removed or negative_changed
    if action == "replace":
        previous = positive.get(entity_type)
        positive[entity_type] = _component(entity_type, targets, operator)
        if entity_type not in order:
            order.append(entity_type)
        negatives[:], negative_changed = _prune_negatives(negatives, entity_type, drop_all=True)
        return previous != positive[entity_type] or negative_changed
    if action == "add":
        negatives[:], negative_changed = _prune_negatives(negatives, entity_type, targets=targets)
        previous = positive.get(entity_type)
        merged = _unique((() if previous is None else _targets(previous)) + targets)
        positive[entity_type] = _component(
            entity_type,
            merged,
            "any" if isinstance(previous, AnyOf) else "all" if isinstance(previous, AllOf) else operator,
        )
        if entity_type not in order:
            order.append(entity_type)
        return previous != positive[entity_type] or negative_changed
    if action == "remove":
        previous = positive.get(entity_type)
        if previous is not None:
            remaining = tuple(value for value in _targets(previous) if value not in set(targets))
            if remaining:
                positive[entity_type] = _component(
                    entity_type,
                    remaining,
                    "any" if isinstance(previous, AnyOf) else "all" if isinstance(previous, AllOf) else None,
                )
            else:
                positive.pop(entity_type)
                order[:] = [name for name in order if name != entity_type]
        negatives[:], negative_changed = _prune_negatives(negatives, entity_type, targets=targets)
        return previous != positive.get(entity_type) or negative_changed
    if action == "exclude":
        negatives.append(Not(expression=_component(entity_type, targets)))
        return True
    raise ValueError(f"unsupported selection action: {batch.action}")


def _split_expression(
    expression: FilterExpression | None,
) -> tuple[list[str], dict[str, FilterExpression], list[Not]]:
    order: list[str] = []
    positive: dict[str, FilterExpression] = {}
    negatives: list[Not] = []
    for component in (() if expression is None else expression.expressions if isinstance(expression, AllOf) else (expression,)):
        if isinstance(component, Not):
            negatives.append(component)
        else:
            entity_type = _entity_type(component)
            order.append(entity_type)
            positive[entity_type] = component
    return order, positive, negatives


def _build_expression(
    order: list[str],
    positive: dict[str, FilterExpression],
    negatives: list[Not],
) -> FilterExpression | None:
    components = [positive[name] for name in order if name in positive] + negatives
    return None if not components else components[0] if len(components) == 1 else AllOf(expressions=tuple(components))


def _entity_type(expression: FilterExpression) -> str:
    if isinstance(expression, Predicate):
        return _ENTITY_TYPES[expression.name]
    entity_types = {_entity_type(child) for child in expression.expressions}
    if len(entity_types) != 1:
        raise ValueError("selection expression groups must use a single entity type")
    return next(iter(entity_types))


def _prune_negatives(
    negatives: list[Not],
    entity_type: str,
    targets: tuple[Scalar, ...] = (),
    *,
    drop_all: bool = False,
) -> tuple[list[Not], bool]:
    kept: list[Not] = []
    changed = False
    for component in negatives:
        if _entity_type(component.expression) != entity_type:
            kept.append(component)
            continue
        if drop_all:
            changed = True
            continue
        remaining = tuple(value for value in _targets(component.expression) if value not in set(targets))
        if remaining:
            next_component = Not(expression=_component(entity_type, remaining))
            changed |= next_component != component
            kept.append(next_component)
        else:
            changed = True
    return kept, changed


def _component(entity_type: str, targets: tuple[Scalar, ...], operator: str | None = None) -> FilterExpression:
    if operator == "any" and len(targets) > 1:
        return AnyOf(expressions=tuple(_predicate(entity_type, (target,)) for target in targets))
    if operator == "all" and len(targets) > 1:
        return AllOf(expressions=tuple(_predicate(entity_type, (target,)) for target in targets))
    return _predicate(entity_type, targets)


def _predicate(entity_type: str, targets: tuple[Scalar, ...]) -> Predicate:
    return Predicate(name=_PREDICATE_NAMES[entity_type], params={"values": targets})


def _targets(expression: FilterExpression) -> tuple[Scalar, ...]:
    if isinstance(expression, Predicate):
        values = expression.params["values"]
        return values if isinstance(values, tuple) else (values,)
    return tuple(value for child in expression.expressions for value in _targets(child))


def _as_tuple(value: object) -> tuple[object, ...]:
    return value if isinstance(value, tuple) else (value,)


def _broadcast(values: tuple[object, ...], size: int) -> tuple[object, ...]:
    if len(values) == size:
        return values
    if len(values) == 1:
        return values * size
    raise ValueError("slot operation arrays must align")


def _unique(values: tuple[Scalar, ...]) -> tuple[Scalar, ...]:
    return tuple(dict.fromkeys(values))


__all__ = ["SelectionDraft", "SelectionDraftReducer", "SelectionSort"]
