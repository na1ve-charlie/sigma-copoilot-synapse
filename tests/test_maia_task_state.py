from __future__ import annotations

from datetime import UTC, datetime

from maia.conversation.draft import SelectionDraftReducer
from maia.recognition.report import RecognitionReport
from maia.selection import SelectionLineage, SelectionSet
from maia.selection.expression import iter_predicates


def test_pending_task_state_resumes_alongside_pending_selection() -> None:
    from maia.conversation.state import (
        ConversationSelectionState,
        ConversationTaskStateStore,
        PendingSelectionStateStore,
    )
    from maia.tasks import TaskSpec, TaskSpecBuilder

    reducer = SelectionDraftReducer()
    selection_store = PendingSelectionStateStore()
    task_store = ConversationTaskStateStore()
    builder = TaskSpecBuilder(id_factory=iter(("task-1",)).__next__)
    selection = _selection_set("sel-1", ("r-1", "r-2"))

    draft = reducer.apply(
        None,
        _report(
            operations=[
                {"action": "replace", "entity_type": "product_type", "target": "A"},
            ]
        ),
    )
    pending_task = builder.build(_report(actions=["task.nvh.origin_data_export"]), selection)
    state = selection_store.save_pending(ConversationSelectionState(), draft)
    state = task_store.save_pending(state, pending_task)

    follow_up = _report(
        operations=[
            {"action": "add", "entity_type": "summary_result", "target": "FAIL"},
        ]
    )

    resumed_draft = selection_store.resume(state, follow_up, reducer=reducer)
    resumed_task = task_store.resume(state, follow_up, builder=builder)

    assert resumed_draft is not None
    assert tuple(predicate.name for predicate in iter_predicates(resumed_draft.expression)) == (
        "product_type_in",
        "summary_result_in",
    )
    assert isinstance(resumed_task, TaskSpec)
    assert resumed_task.params == {}
    assert resumed_task.selection_hash == selection.selection_hash


def test_pending_task_state_rebases_saved_task_to_new_selection() -> None:
    from maia.conversation.state import ConversationSelectionState, ConversationTaskStateStore
    from maia.tasks import TaskSpec, TaskSpecBuilder

    task_store = ConversationTaskStateStore()
    builder = TaskSpecBuilder(id_factory=iter(("task-1",)).__next__)
    pending_task = builder.build(_report(actions=["task.nvh.origin_data_export"]), _selection_set("sel-1", ("r-1", "r-2")))
    state = task_store.save_pending(ConversationSelectionState(), pending_task)

    rebased = task_store.rebase(
        state,
        _selection_set("sel-2", ("r-2",)),
        builder=builder,
    )

    assert isinstance(rebased.pending_task, TaskSpec)
    assert rebased.pending_task.selection_set_id == "sel-2"
    assert rebased.pending_task.selection_hash == _selection_set("sel-2", ("r-2",)).selection_hash
    assert rebased.version == 2


def test_cancel_pending_clears_selection_and_task_state() -> None:
    from maia.conversation.state import (
        ConversationSelectionState,
        ConversationTaskStateStore,
        PendingSelectionStateStore,
    )
    from maia.tasks import TaskSpecBuilder

    selection = _selection_set("sel-1", ("r-1",))
    draft = SelectionDraftReducer().apply(
        None,
        _report(
            operations=[
                {"action": "replace", "entity_type": "product_type", "target": "A"},
            ]
        ),
    )
    task = TaskSpecBuilder(id_factory=iter(("task-1",)).__next__).build(
        _report(actions=["task.nvh.origin_data_export"]),
        selection,
    )
    state = PendingSelectionStateStore().save_pending(ConversationSelectionState(), draft)
    state = ConversationTaskStateStore().save_pending(state, task)

    cancelled = ConversationTaskStateStore().cancel_pending(state)

    assert cancelled.pending_selection_draft is None
    assert cancelled.pending_task is None
    assert cancelled.pending_confirmation is None
    assert cancelled.version == state.version + 1


def _report(
    *,
    actions: list[str] | None = None,
    operations: list[dict[str, object]] | None = None,
) -> RecognitionReport:
    return RecognitionReport(
        message="补充条件",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        action_intents=[
            {"name": name, "score": 0.95}
            for name in actions or []
        ],
        slot_operations=[
            {
                "intent": "task.nvh.record_search",
                "score": 0.93,
                "slot_valid": True,
                **operation,
            }
            for operation in operations or []
        ],
    )


def _selection_set(selection_set_id: str, record_ids: tuple[str, ...]) -> SelectionSet:
    return SelectionSet(
        selection_set_id=selection_set_id,
        expression={"kind": "predicate", "name": "product_type_in", "params": {"values": ["A"]}},
        record_count=len(record_ids),
        record_ids=record_ids,
        source_version="sigma-fixture-v1",
        created_at=datetime(2026, 6, 11, 9, 0, tzinfo=UTC),
        lineage=SelectionLineage(operation="create"),
    )
