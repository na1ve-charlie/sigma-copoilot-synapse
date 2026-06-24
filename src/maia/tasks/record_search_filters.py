from __future__ import annotations

from collections.abc import Iterable

from maia.api import ClarifyPlan, Prompt, PromptCandidate
from maia.conversation.draft import SelectionDraft, SelectionDraftReducer
from maia.integrations.sigma.records import TestRecordSummary
from maia.recognition import RecognitionReport
from maia.selection.compiler import ALL_RECORDS_PREDICATE_NAME
from maia.selection.expression import AllOf, AnyOf, FilterExpression, Not, Predicate


_PRODUCT_PREDICATES = {
    "product_type_in": "product_type",
    "config_version_in": "config_version",
    "type_system_in": "type_system",
}
_PRODUCT_FILTER_SLOTS = frozenset({"product_type", "config_version", "type_system"})
_DERIVED_PRODUCT_FILTER_SLOTS = frozenset({"config_version", "type_system"})
_SCOPE_NEUTRAL_SLOTS = frozenset({"filter_operator", "selection_reference"})
_ALL_PRODUCT_TYPE_ALIASES = frozenset(
    {
        "__ALL_PRODUCTS__",
        "\u5168\u90e8\u4ea7\u54c1",
        "\u6240\u6709\u4ea7\u54c1",
        "\u4ea7\u54c1\u4e0d\u9650",
        "\u578b\u53f7\u4e0d\u9650",
        "\u5168\u90e8\u578b\u53f7",
        "\u6240\u6709\u578b\u53f7",
        "\u4e0d\u9650\u578b\u53f7",
    }
)
_SLOT_LABELS = {
    "config_version": "\u914d\u7f6e\u5e8f\u53f7",
    "product_type": "\u4ea7\u54c1\u578b\u53f7",
    "type_system": "\u68c0\u6d4b\u7cfb\u7edf",
}

ALL_PRODUCTS_VALUE = "__ALL_PRODUCTS__"


def selection_expression_for_storage(expression: FilterExpression | None) -> FilterExpression:
    if expression is not None:
        return expression
    return Predicate(name=ALL_RECORDS_PREDICATE_NAME, params={})


def selection_expression_from_storage(
    expression: FilterExpression | None,
) -> FilterExpression | None:
    if (
        isinstance(expression, Predicate)
        and expression.name == ALL_RECORDS_PREDICATE_NAME
        and not expression.params
    ):
        return None
    return expression


def product_type_scope(expression: FilterExpression | None) -> FilterExpression | None:
    return _strip_scope_filters(expression, excluded_slots={"product_type"})


def config_version_scope(expression: FilterExpression | None) -> FilterExpression | None:
    return _strip_scope_filters(
        expression,
        excluded_slots={"config_version", "type_system"},
    )


def type_system_scope(expression: FilterExpression | None) -> FilterExpression | None:
    return _strip_scope_filters(expression, excluded_slots={"type_system"})


def invalidate_product_filters_on_scope_change(
    draft: SelectionDraft | None,
    report: RecognitionReport,
    *,
    clear_product_type: bool = False,
) -> SelectionDraft | None:
    if draft is None or (not clear_product_type and not _changes_record_scope(report)):
        return draft
    excluded_slots = (
        _PRODUCT_FILTER_SLOTS if clear_product_type else _DERIVED_PRODUCT_FILTER_SLOTS
    )
    expression = _strip_scope_filters(
        draft.expression,
        excluded_slots=set(excluded_slots),
    )
    pending_questions = tuple(
        question for question in draft.pending_questions if question not in excluded_slots
    )
    if expression == draft.expression and pending_questions == draft.pending_questions:
        return draft
    return draft.model_copy(
        update={
            "expression": expression,
            "pending_questions": pending_questions,
            "revision": draft.revision + 1,
        }
    )


def complete_product_type_filter(
    draft: SelectionDraft,
    records: tuple[TestRecordSummary, ...],
    *,
    reducer: SelectionDraftReducer,
    allow_all_products: bool = False,
) -> tuple[SelectionDraft, ClarifyPlan | None, str | None]:
    selected_type = _single_selected_value(draft.expression, "product_type")
    product_types = _ordered_record_values(records, "product_type")
    if selected_type is None:
        if allow_all_products or not product_types:
            return draft, None, None
        if len(product_types) == 1:
            selected_type = product_types[0]
            draft = _apply_auto_slots(
                draft,
                reducer=reducer,
                product_type=selected_type,
            )
            return draft, None, selected_type
        return draft, _clarify_missing_product_type(product_types), None
    if product_types and selected_type not in product_types:
        return draft, _clarify_invalid_product_type(product_types), None
    return draft, None, selected_type


def complete_config_version_filter(
    draft: SelectionDraft,
    records: tuple[TestRecordSummary, ...],
    *,
    reducer: SelectionDraftReducer,
    product_type: str,
) -> tuple[SelectionDraft, ClarifyPlan | None, tuple[str, ...]]:
    scoped_records = _filter_records(records, product_type=product_type)
    versions = _ordered_record_values(scoped_records, "config_version")
    selected_versions = _selected_values(draft.expression, "config_version")
    if not selected_versions:
        if not versions:
            return draft, None, ()
        if len(versions) == 1:
            selected_versions = (versions[0],)
            draft = _apply_auto_slots(
                draft,
                reducer=reducer,
                config_version=versions[0],
            )
            return draft, None, selected_versions
        return draft, _clarify_missing(
            "config_version",
            versions,
            scope=_slot_scope(product_type=product_type),
        ), ()
    if versions and not set(selected_versions).issubset(versions):
        return draft, _clarify_invalid(
            "config_version",
            versions,
            scope=_slot_scope(product_type=product_type),
        ), ()
    return draft, None, selected_versions


def complete_type_system_filter(
    draft: SelectionDraft,
    records: tuple[TestRecordSummary, ...],
    *,
    reducer: SelectionDraftReducer,
    product_type: str,
    config_versions: tuple[str, ...],
) -> tuple[SelectionDraft, ClarifyPlan | None]:
    scoped_records = _filter_records(
        records,
        product_type=product_type,
        config_versions=config_versions,
    )
    systems = _ordered_record_values(scoped_records, "system_no")
    selected_systems = _selected_values(draft.expression, "type_system")
    if not selected_systems:
        if len(systems) > 1:
            return draft, _clarify_missing(
                "type_system",
                systems,
                scope=_slot_scope(
                    product_type=product_type,
                    config_versions=config_versions,
                ),
            )
        if len(systems) == 1:
            draft = _apply_auto_slots(
                draft,
                reducer=reducer,
                type_system=systems[0],
            )
        return draft, None
    if systems and not set(selected_systems).issubset(systems):
        return draft, _clarify_invalid(
            "type_system",
            systems,
            scope=_slot_scope(
                product_type=product_type,
                config_versions=config_versions,
            ),
        )
    return draft, None


def distinct_values(values: Iterable[str | None]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def is_all_product_types_request(message: str) -> bool:
    text = message.strip()
    return text in _ALL_PRODUCT_TYPE_ALIASES or any(
        alias in text for alias in _ALL_PRODUCT_TYPE_ALIASES if alias != ALL_PRODUCTS_VALUE
    )


def _changes_record_scope(report: RecognitionReport) -> bool:
    for operation in report.slot_operations:
        entity_type = operation.entity_type
        if entity_type in _PRODUCT_FILTER_SLOTS or entity_type in _SCOPE_NEUTRAL_SLOTS:
            continue
        return True
    return False


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
    return distinct_values(result)


def _predicates(expression: FilterExpression) -> Iterable[Predicate]:
    if isinstance(expression, Predicate):
        yield expression
        return
    if isinstance(expression, Not):
        return
    children = expression.expressions if isinstance(expression, (AllOf, AnyOf)) else ()
    for child in children:
        yield from _predicates(child)


def _strip_scope_filters(
    expression: FilterExpression | None,
    *,
    excluded_slots: set[str],
) -> FilterExpression | None:
    if expression is None:
        return None
    if isinstance(expression, Predicate):
        return None if _PRODUCT_PREDICATES.get(expression.name) in excluded_slots else expression
    if isinstance(expression, Not):
        child = _strip_scope_filters(expression.expression, excluded_slots=excluded_slots)
        return None if child is None else Not(expression=child)

    children = tuple(
        child
        for item in expression.expressions
        if (child := _strip_scope_filters(item, excluded_slots=excluded_slots)) is not None
    )
    if not children:
        return None
    if len(children) == 1:
        return children[0]
    if isinstance(expression, AnyOf):
        return AnyOf(expressions=children)
    return AllOf(expressions=children)


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


def _clarify_missing(
    slot: str,
    values: tuple[str, ...],
    *,
    scope: str | None = None,
) -> ClarifyPlan:
    label = _slot_label(slot)
    return ClarifyPlan(
        reason="missing_slots",
        message=_slot_plan_message(label, scope=scope, valid=True),
        missing_slots=[slot],
        prompts=[_slot_prompt(slot, values, scope=scope)],
        suggestions=list(values),
    )


def _clarify_missing_product_type(product_types: tuple[str, ...]) -> ClarifyPlan:
    values = product_types
    message = _product_type_context_message(product_types)
    prompt = Prompt(
        id="product_type",
        target="slot",
        label="product type",
        message="\u9009\u62e9\u4ea7\u54c1\u578b\u53f7\u3002",
        required=True,
        input_type="single_select",
        candidates=[
            *(PromptCandidate(value=value, label=value) for value in values),
            PromptCandidate(
                value=ALL_PRODUCTS_VALUE,
                label="\u5168\u90e8\u4ea7\u54c1",
                description="\u4e0d\u4f20 type \u53c2\u6570\uff0c\u6309\u5176\u4ed6\u7b5b\u9009\u6761\u4ef6\u7ee7\u7eed\u3002",
            ),
        ],
    )
    return ClarifyPlan(
        reason="missing_slots",
        message=message,
        missing_slots=["product_type"],
        prompts=[prompt],
        suggestions=[*values, "\u5168\u90e8\u4ea7\u54c1"],
    )


def _clarify_invalid(
    slot: str,
    values: tuple[str, ...],
    *,
    scope: str | None = None,
) -> ClarifyPlan:
    label = _slot_label(slot)
    return ClarifyPlan(
        reason="invalid_slots",
        message=_slot_plan_message(label, scope=scope, valid=False),
        invalid_slots=[slot],
        prompts=[_slot_prompt(slot, values, scope=scope)],
        suggestions=list(values),
    )


def _clarify_invalid_product_type(product_types: tuple[str, ...]) -> ClarifyPlan:
    values = product_types
    prompt = Prompt(
        id="product_type",
        target="slot",
        label="product type",
        message="\u9009\u62e9\u4ea7\u54c1\u578b\u53f7\u3002",
        required=True,
        input_type="single_select",
        candidates=[
            *(PromptCandidate(value=value, label=value) for value in values),
            PromptCandidate(
                value=ALL_PRODUCTS_VALUE,
                label="\u5168\u90e8\u4ea7\u54c1",
                description="\u4e0d\u4f20 type \u53c2\u6570\uff0c\u6309\u5176\u4ed6\u7b5b\u9009\u6761\u4ef6\u7ee7\u7eed\u3002",
            ),
        ],
    )
    return ClarifyPlan(
        reason="invalid_slots",
        message=_product_type_context_message(product_types),
        invalid_slots=["product_type"],
        prompts=[prompt],
        suggestions=[*values, "\u5168\u90e8\u4ea7\u54c1"],
    )


def _product_type_context_message(product_types: tuple[str, ...]) -> str:
    preview = "\u3001".join(product_types[:3])
    count = len(product_types)
    if count > len(product_types[:3]):
        return (
            f"\u5f53\u524d\u7b5b\u9009\u7684\u6d4b\u8bd5\u8bb0\u5f55\u6309\u6d4b\u8bd5\u65f6\u95f4\u5012\u5e8f"
            f"\u6db5\u76d6\u4e86{preview}\u7b49 {count} \u4e2a\u4ea7\u54c1\u578b\u53f7\uff0c"
            f"\u8bf7\u9009\u62e9\u4f60\u8981\u89c2\u5bdf\u7684\u4ea7\u54c1\u578b\u53f7\u3002"
        )
    return (
        f"\u5f53\u524d\u7b5b\u9009\u7684\u6d4b\u8bd5\u8bb0\u5f55\u6309\u6d4b\u8bd5\u65f6\u95f4\u5012\u5e8f"
        f"\u6db5\u76d6\u4e86{preview}\uff0c\u8bf7\u9009\u62e9\u4f60\u8981\u89c2\u5bdf\u7684\u4ea7\u54c1\u578b\u53f7\u3002"
    )


def _slot_prompt(
    slot: str,
    values: tuple[str, ...],
    *,
    scope: str | None = None,
) -> Prompt:
    label = _slot_label(slot)
    return Prompt(
        id=slot,
        target="slot",
        label=slot.replace("_", " "),
        message=_slot_prompt_message(label, scope=scope),
        required=True,
        input_type="multi_select" if slot in {"config_version", "type_system"} else "single_select",
        candidates=[PromptCandidate(value=value, label=value) for value in values],
    )


def _slot_plan_message(label: str, *, scope: str | None, valid: bool) -> str:
    prefix = f"\u5f53\u524d\u5df2\u9009\u62e9{scope}\uff0c" if scope else ""
    qualifier = "" if valid else "\u6709\u6548\u7684"
    return f"{prefix}\u8bf7\u9009\u62e9{qualifier}{label}\u3002"


def _slot_prompt_message(label: str, *, scope: str | None) -> str:
    if scope:
        return f"\u4e3a{scope} \u9009\u62e9{label}\u3002"
    return f"\u9009\u62e9{label}\u3002"


def _slot_scope(
    *,
    product_type: str,
    config_versions: tuple[str, ...] = (),
) -> str:
    parts = [f"\u4ea7\u54c1\u578b\u53f7 {product_type}"]
    if config_versions:
        versions = "\u3001".join(config_versions)
        parts.append(f"\u914d\u7f6e\u5e8f\u53f7 {versions}")
    return "\u3001".join(parts)


def _slot_label(slot: str) -> str:
    return _SLOT_LABELS.get(slot, slot.replace("_", " "))


def _ordered_record_values(
    records: tuple[TestRecordSummary, ...],
    field_name: str,
) -> tuple[str, ...]:
    values: list[str] = []
    for record in sorted(records, key=_record_sort_key, reverse=True):
        value = getattr(record, field_name)
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _record_sort_key(record: TestRecordSummary) -> tuple[float, str]:
    timestamp = 0.0 if record.tested_at is None else record.tested_at.timestamp()
    return (timestamp, record.record_id)


def _filter_records(
    records: tuple[TestRecordSummary, ...],
    *,
    product_type: str,
    config_versions: tuple[str, ...] = (),
) -> tuple[TestRecordSummary, ...]:
    return tuple(
        record
        for record in records
        if record.product_type == product_type
        and (not config_versions or record.config_version in config_versions)
    )


__all__ = [
    "ALL_PRODUCTS_VALUE",
    "complete_config_version_filter",
    "complete_product_type_filter",
    "complete_type_system_filter",
    "config_version_scope",
    "distinct_values",
    "invalidate_product_filters_on_scope_change",
    "is_all_product_types_request",
    "product_type_scope",
    "selection_expression_for_storage",
    "selection_expression_from_storage",
    "type_system_scope",
]
