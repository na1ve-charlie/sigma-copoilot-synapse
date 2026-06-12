from __future__ import annotations

from maia.conversation.draft import SelectionDraft, SelectionDraftReducer, SelectionSort
from maia.recognition.report import RecognitionReport, RecognitionSlotOperation


_WEEK_RANGE = "start=2026-06-05 15:30:00; end=2026-06-12 15:30:00"
_MONTH_RANGE = "start=2026-05-13 15:30:00; end=2026-06-12 15:30:00"


def test_reducer_builds_all_of_expression_for_replace_conditions() -> None:
    draft = SelectionDraftReducer().apply(
        None,
        _report(
            _slot_operation("time_range", "replace", _WEEK_RANGE),
            _slot_operation("summary_result", "replace", "不合格"),
        ),
    )

    assert draft.revision == 1
    assert draft.limit is None
    assert draft.sort == ()
    assert draft.expression is not None
    assert draft.expression.model_dump(mode="json") == {
        "kind": "all_of",
        "expressions": [
            {
                "kind": "predicate",
                "name": "tested_at_between",
                "params": {
                    "start": "2026-06-05 15:30:00",
                    "end": "2026-06-12 15:30:00",
                },
            },
            {
                "kind": "predicate",
                "name": "summary_result_in",
                "params": {"values": ["不合格"]},
            },
        ],
    }


def test_reducer_preserves_any_operator_for_same_dimension_values() -> None:
    draft = SelectionDraftReducer().apply(
        None,
        _report(
            _slot_operation(
                "sensor",
                ("replace", "replace"),
                ("Vib1", "Vib2"),
                (True, True),
            ),
            _slot_operation("filter_operator", "replace", "any"),
            _slot_operation("summary_result", "replace", "不合格"),
        ),
    )

    assert draft.expression is not None
    assert draft.expression.model_dump(mode="json") == {
        "kind": "all_of",
        "expressions": [
            {
                "kind": "any_of",
                "expressions": [
                    {
                        "kind": "predicate",
                        "name": "sensor_in",
                        "params": {"values": ["Vib1"]},
                    },
                    {
                        "kind": "predicate",
                        "name": "sensor_in",
                        "params": {"values": ["Vib2"]},
                    },
                ],
            },
            {
                "kind": "predicate",
                "name": "summary_result_in",
                "params": {"values": ["不合格"]},
            },
        ],
    }


def test_reducer_add_and_remove_update_existing_dimension_and_revision() -> None:
    reducer = SelectionDraftReducer()

    first = reducer.apply(None, _report(_slot_operation("sensor", "replace", "Vib1")))
    second = reducer.apply(first, _report(_slot_operation("sensor", "add", "Vib2")))
    third = reducer.apply(second, _report(_slot_operation("sensor", "remove", "Vib1")))

    assert (first.revision, second.revision, third.revision) == (1, 2, 3)
    assert second.expression is not None
    assert second.expression.model_dump(mode="json") == {
        "kind": "predicate",
        "name": "sensor_in",
        "params": {"values": ["Vib1", "Vib2"]},
    }
    assert third.expression is not None
    assert third.expression.model_dump(mode="json") == {
        "kind": "predicate",
        "name": "sensor_in",
        "params": {"values": ["Vib2"]},
    }


def test_reducer_replace_swaps_only_the_target_dimension() -> None:
    reducer = SelectionDraftReducer()
    first = reducer.apply(
        None,
        _report(
            _slot_operation("time_range", "replace", _WEEK_RANGE),
            _slot_operation("summary_result", "replace", "不合格"),
        ),
    )

    second = reducer.apply(
        first,
        _report(_slot_operation("time_range", "replace", _MONTH_RANGE)),
    )

    assert second.expression is not None
    assert second.expression.model_dump(mode="json") == {
        "kind": "all_of",
        "expressions": [
            {
                "kind": "predicate",
                "name": "tested_at_between",
                "params": {
                    "start": "2026-05-13 15:30:00",
                    "end": "2026-06-12 15:30:00",
                },
            },
            {
                "kind": "predicate",
                "name": "summary_result_in",
                "params": {"values": ["不合格"]},
            },
        ],
    }


def test_reducer_appends_not_predicates_for_exclude_operations() -> None:
    reducer = SelectionDraftReducer()
    first = reducer.apply(None, _report(_slot_operation("product_type", "replace", "A")))
    second = reducer.apply(
        first,
        _report(_slot_operation("archive_status", "exclude", "archived")),
    )

    assert second.expression is not None
    assert second.expression.model_dump(mode="json") == {
        "kind": "all_of",
        "expressions": [
            {
                "kind": "predicate",
                "name": "product_type_in",
                "params": {"values": ["A"]},
            },
            {
                "kind": "not",
                "expression": {
                    "kind": "predicate",
                    "name": "archive_status_in",
                    "params": {"values": ["archived"]},
                },
            },
        ],
    }


def test_latest_n_updates_limit_and_default_sort() -> None:
    draft = SelectionDraftReducer().apply(
        None,
        _report(_slot_operation("latest_n", "replace", "5")),
    )

    assert draft.expression is None
    assert draft.limit == 5
    assert draft.sort == (SelectionSort(field="tested_at", direction="desc"),)
    assert draft.revision == 1


def test_clear_resets_expression_and_limit_but_preserves_base_selection_id() -> None:
    reducer = SelectionDraftReducer()
    draft = reducer.apply(None, _report(_slot_operation("summary_result", "replace", "不合格")))
    limited = reducer.apply(draft, _report(_slot_operation("latest_n", "replace", "5")))
    with_base = limited.model_copy(
        update={"base_selection_id": "sel-1", "pending_questions": ("clarify",)},
    )

    cleared = reducer.clear(with_base)

    assert cleared.base_selection_id == "sel-1"
    assert cleared.expression is None
    assert cleared.limit is None
    assert cleared.sort == ()
    assert cleared.pending_questions == ()
    assert cleared.revision == 3


def _report(*slot_operations: RecognitionSlotOperation) -> RecognitionReport:
    return RecognitionReport(
        message="selection turn",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        slot_operations=slot_operations,
    )


def _slot_operation(
    entity_type: str,
    action: str | tuple[str, ...],
    target: str | tuple[str, ...],
    slot_valid: bool | tuple[bool, ...] = True,
) -> RecognitionSlotOperation:
    return RecognitionSlotOperation(
        intent=f"task.nvh.selection.set_{entity_type}",
        score=1.0,
        action=action,
        entity_type=entity_type,
        target=target,
        slot_valid=slot_valid,
    )
