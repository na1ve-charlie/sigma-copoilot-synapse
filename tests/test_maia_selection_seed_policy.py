from __future__ import annotations

from datetime import UTC, datetime

from maia.conversation.draft import SelectionDraft
from maia.recognition.report import RecognitionReport
from maia.selection import SelectionLineage, SelectionSet, SelectionSort


def test_new_query_seed_keeps_only_single_product_type() -> None:
    from maia.tasks.selection_seed_policy import SelectionSeedPolicy

    active = _selection_set()

    seed = SelectionSeedPolicy().select(
        _report(
            actions=("task.nvh.record_search",),
            operations=(
                {
                    "action": "replace",
                    "entity_type": "summary_result",
                    "target": "PASS",
                },
            ),
        ),
        active_selection=active,
    )

    assert seed.mode == "new_query"
    assert seed.base is None
    assert seed.draft is not None
    assert seed.draft.base_selection_id is None
    assert seed.draft.sort == ()
    assert seed.draft.limit is None
    assert seed.draft.expression.model_dump(mode="json") == {
        "kind": "predicate",
        "name": "product_type_in",
        "params": {"values": ["A"]},
    }


def test_explicit_reference_seed_preserves_complete_selection() -> None:
    from maia.tasks.selection_seed_policy import SelectionSeedPolicy

    referenced = _selection_set()

    seed = SelectionSeedPolicy().select(
        _report(
            actions=("task.nvh.record_search",),
            operations=(
                {
                    "action": "replace",
                    "entity_type": "selection_reference",
                    "target": "active_selection",
                },
            ),
        ),
        referenced_selection=referenced,
        active_selection=referenced,
    )

    assert seed.mode == "explicit_reference"
    assert seed.base == referenced
    assert seed.draft is not None
    assert seed.draft.base_selection_id == referenced.selection_set_id
    assert seed.draft.expression == referenced.expression
    assert seed.draft.sort[0].field == "tested_at"
    assert seed.draft.limit == 10


def test_terminal_task_without_new_filters_reuses_active_selection() -> None:
    from maia.tasks.selection_seed_policy import SelectionSeedPolicy

    active = _selection_set()

    seed = SelectionSeedPolicy().select(
        _report(actions=("task.nvh.origin_data_export",)),
        active_selection=active,
    )

    assert seed.mode == "active_task"
    assert seed.base == active
    assert seed.draft is not None
    assert seed.draft.base_selection_id == active.selection_set_id
    assert seed.draft.expression == active.expression
    assert seed.draft.limit == active.limit


def test_stripped_terminal_report_without_filters_reuses_active_selection() -> None:
    from maia.tasks.selection_seed_policy import SelectionSeedPolicy

    active = _selection_set()

    seed = SelectionSeedPolicy().select(
        _report(actions=()),
        active_selection=active,
    )

    assert seed.mode == "active_task"
    assert seed.base == active
    assert seed.draft is not None
    assert seed.draft.expression == active.expression


def test_prompt_reply_continues_pending_draft() -> None:
    from maia.tasks.selection_seed_policy import SelectionSeedPolicy

    active = _selection_set()
    pending = SelectionDraft(
        expression={
            "kind": "predicate",
            "name": "product_type_in",
            "params": {"values": ["B"]},
        },
        pending_questions=("config_version",),
        revision=3,
    )

    seed = SelectionSeedPolicy().select(
        _report(actions=("task.nvh.record_search",)),
        pending_draft=pending,
        referenced_selection=active,
        active_selection=active,
        is_prompt_reply=True,
    )

    assert seed.mode == "pending"
    assert seed.draft == pending
    assert seed.base is None


def _selection_set() -> SelectionSet:
    return SelectionSet(
        selection_set_id="sel-active",
        expression={
            "kind": "all_of",
            "expressions": [
                {
                    "kind": "predicate",
                    "name": "product_type_in",
                    "params": {"values": ["A"]},
                },
                {
                    "kind": "predicate",
                    "name": "config_version_in",
                    "params": {"values": ["1"]},
                },
                {
                    "kind": "predicate",
                    "name": "summary_result_in",
                    "params": {"values": ["FAIL"]},
                },
            ],
        },
        sort=(SelectionSort(field="tested_at", direction="desc"),),
        limit=10,
        record_count=1,
        record_ids=("r-1",),
        source_version="sigma-fixture-v1",
        created_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
        lineage=SelectionLineage(operation="create"),
    )


def _report(
    *,
    actions: tuple[str, ...],
    operations: tuple[dict[str, object], ...] = (),
) -> RecognitionReport:
    return RecognitionReport(
        message="selection turn",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        action_intents=tuple({"name": name, "score": 0.95} for name in actions),
        slot_operations=tuple(
            {
                "intent": "task.nvh.record_search",
                "score": 0.93,
                "slot_valid": True,
                **operation,
            }
            for operation in operations
        ),
    )
