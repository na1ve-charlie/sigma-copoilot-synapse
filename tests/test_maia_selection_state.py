from __future__ import annotations

from maia.conversation.draft import SelectionDraftReducer
from maia.recognition.report import RecognitionReport
from maia.selection.expression import iter_predicates


def test_pending_selection_state_resumes_saved_draft_and_clears_on_activation() -> None:
    from maia.conversation.state import ConversationSelectionState, PendingSelectionStateStore

    store = PendingSelectionStateStore()
    reducer = SelectionDraftReducer()

    first = reducer.apply(
        None,
        _report(
            "查找 A 型号",
            [{"action": "replace", "entity_type": "product_type", "target": "A"}],
        ),
    )
    saved = store.save_pending(ConversationSelectionState(), first)

    resumed = store.resume(
        saved,
        _report(
            "只看不合格",
            [{"action": "add", "entity_type": "summary_result", "target": "FAIL"}],
        ),
        reducer=reducer,
    )
    activated = store.activate(
        saved.model_copy(update={"pending_selection_draft": resumed}),
        "sel-2",
    )

    assert saved.pending_selection_draft == first
    assert saved.version == 1
    assert resumed is not None
    assert resumed.revision == 2
    assert tuple(predicate.name for predicate in iter_predicates(resumed.expression)) == (
        "product_type_in",
        "summary_result_in",
    )
    assert activated.active_selection_set_id == "sel-2"
    assert activated.recent_selection_set_ids == ("sel-2",)
    assert activated.pending_selection_draft is None
    assert activated.version == 2


def test_activate_promotes_previous_active_selection_into_recent_history() -> None:
    from maia.conversation.state import ConversationSelectionState, PendingSelectionStateStore

    state = ConversationSelectionState(
        active_selection_set_id="sel-1",
        recent_selection_set_ids=("sel-0", "sel-1"),
    )

    updated = PendingSelectionStateStore().activate(state, "sel-2")

    assert updated.active_selection_set_id == "sel-2"
    assert updated.recent_selection_set_ids == ("sel-2", "sel-1", "sel-0")
    assert updated.version == 1


def _report(message: str, operations: list[dict[str, object]]) -> RecognitionReport:
    return RecognitionReport(
        message=message,
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        slot_operations=[
            {
                "intent": "task.nvh.record_search",
                "score": 0.95,
                "slot_valid": True,
                **operation,
            }
            for operation in operations
        ],
    )
