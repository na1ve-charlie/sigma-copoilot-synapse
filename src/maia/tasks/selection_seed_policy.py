from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from maia.conversation.draft import SelectionDraft, SelectionSort
from maia.recognition import RecognitionReport
from maia.selection import AllOf, AnyOf, FilterExpression, Not, Predicate, SelectionSet
from maia.tasks.record_search_filters import (
    invalidate_product_filters_on_scope_change,
    selection_expression_from_storage,
)


SelectionSeedMode = Literal[
    "pending",
    "explicit_reference",
    "active_task",
    "new_query",
]


@dataclass(frozen=True)
class SelectionSeed:
    mode: SelectionSeedMode
    draft: SelectionDraft | None
    base: SelectionSet | None


class SelectionSeedPolicy:
    """Selects the state inherited by a new selection turn."""

    def select(
        self,
        report: RecognitionReport,
        *,
        pending_draft: SelectionDraft | None = None,
        pending_base: SelectionSet | None = None,
        referenced_selection: SelectionSet | None = None,
        active_selection: SelectionSet | None = None,
        is_prompt_reply: bool = False,
    ) -> SelectionSeed:
        if is_prompt_reply:
            return SelectionSeed("pending", pending_draft, pending_base)
        if referenced_selection is not None:
            return SelectionSeed(
                "explicit_reference",
                _draft_from_selection(referenced_selection),
                referenced_selection,
            )
        if _reuses_active_selection(report):
            return SelectionSeed(
                "active_task",
                _draft_from_selection(active_selection),
                active_selection,
            )
        return SelectionSeed(
            "new_query",
            _product_type_seed(active_selection),
            None,
        )

    def apply_scope_reset(
        self,
        seed: SelectionSeed,
        report: RecognitionReport,
        *,
        clear_product_type: bool,
    ) -> SelectionDraft | None:
        if seed.mode == "explicit_reference" and not clear_product_type:
            return seed.draft
        return invalidate_product_filters_on_scope_change(
            seed.draft,
            report,
            clear_product_type=clear_product_type,
        )


def _reuses_active_selection(report: RecognitionReport) -> bool:
    action_names = {intent.name for intent in report.action_intents}
    has_new_filters = any(
        operation.entity_type != "selection_reference"
        for operation in report.slot_operations
    )
    return (
        "task.nvh.record_search" not in action_names
        and not has_new_filters
    )


def _draft_from_selection(selection: SelectionSet | None) -> SelectionDraft | None:
    if selection is None:
        return None
    return SelectionDraft(
        base_selection_id=selection.selection_set_id,
        expression=selection_expression_from_storage(selection.expression),
        sort=tuple(
            SelectionSort(field=item.field, direction=item.direction)
            for item in selection.sort
        ),
        limit=selection.limit,
    )


def _product_type_seed(selection: SelectionSet | None) -> SelectionDraft | None:
    if selection is None:
        return None
    values = tuple(dict.fromkeys(_positive_product_types(selection.expression)))
    if len(values) != 1:
        return None
    return SelectionDraft(
        expression=Predicate(
            name="product_type_in",
            params={"values": values},
        )
    )


def _positive_product_types(expression: FilterExpression) -> tuple[str, ...]:
    if isinstance(expression, Predicate):
        if expression.name != "product_type_in":
            return ()
        raw = expression.params.get("values")
        values = raw if isinstance(raw, tuple) else (raw,)
        return tuple(str(value) for value in values if value not in (None, ""))
    if isinstance(expression, Not):
        return ()
    if isinstance(expression, (AllOf, AnyOf)):
        return tuple(
            value
            for child in expression.expressions
            for value in _positive_product_types(child)
        )
    return ()


__all__ = ["SelectionSeed", "SelectionSeedMode", "SelectionSeedPolicy"]
