from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from maia.conversation.draft import SelectionDraft, SelectionDraftReducer
from maia.recognition.report import RecognitionReport
from maia.selection.sets import SelectionSet
from maia.tasks import PendingConfirmation, PendingTask, TaskSpec, TaskSpecBuilder


class ConversationSelectionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_selection_set_id: str | None = None
    recent_selection_set_ids: tuple[str, ...] = ()
    pending_selection_draft: SelectionDraft | None = None
    pending_task: TaskSpec | PendingTask | None = None
    pending_confirmation: PendingConfirmation | None = None
    active_task_id: str | None = None
    version: int = 0

    @field_validator("active_selection_set_id", "active_task_id")
    @classmethod
    def _validate_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("state ids must not be blank")
        return value

    @field_validator("recent_selection_set_ids")
    @classmethod
    def _validate_recent(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("recent_selection_set_ids must not contain blank values")
        return tuple(dict.fromkeys(value))

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: int) -> int:
        if value < 0:
            raise ValueError("version must not be negative")
        return value


class PendingSelectionStateStore:
    def save_pending(self, state: ConversationSelectionState, draft: SelectionDraft | None) -> ConversationSelectionState:
        return _update_state(state, pending_selection_draft=draft)

    def resume(
        self,
        state: ConversationSelectionState,
        report: RecognitionReport,
        *,
        reducer: SelectionDraftReducer | None = None,
    ) -> SelectionDraft | None:
        if state.pending_selection_draft is None:
            return None
        return (reducer or SelectionDraftReducer()).apply(state.pending_selection_draft, report)

    def clear_pending(self, state: ConversationSelectionState) -> ConversationSelectionState:
        return self.save_pending(state, None)

    def activate(self, state: ConversationSelectionState, selection_set_id: str) -> ConversationSelectionState:
        if not selection_set_id.strip():
            raise ValueError("selection_set_id must not be blank")
        if state.active_selection_set_id == selection_set_id and state.pending_selection_draft is None:
            return state
        return _update_state(
            state,
            active_selection_set_id=selection_set_id,
            recent_selection_set_ids=_push_recent(
                selection_set_id,
                state.active_selection_set_id,
                state.recent_selection_set_ids,
            ),
            pending_selection_draft=None,
        )


def _push_recent(
    selection_set_id: str,
    active_selection_set_id: str | None,
    recent_selection_set_ids: tuple[str, ...],
) -> tuple[str, ...]:
    items = [selection_set_id]
    if active_selection_set_id is not None:
        items.append(active_selection_set_id)
    items.extend(recent_selection_set_ids)
    return tuple(dict.fromkeys(items))


class ConversationTaskStateStore:
    def save_pending(
        self,
        state: ConversationSelectionState,
        task: TaskSpec | PendingTask | None,
    ) -> ConversationSelectionState:
        return _update_state(state, pending_task=task)

    def resume(
        self,
        state: ConversationSelectionState,
        report: RecognitionReport,
        *,
        builder: TaskSpecBuilder,
        selection_set: SelectionSet | None = None,
    ) -> TaskSpec | PendingTask | None:
        return builder.resume(state.pending_task, report, selection_set=selection_set)

    def clear_pending(self, state: ConversationSelectionState) -> ConversationSelectionState:
        return self.save_pending(state, None)

    def cancel_pending(self, state: ConversationSelectionState) -> ConversationSelectionState:
        return _update_state(
            state,
            pending_selection_draft=None,
            pending_task=None,
            pending_confirmation=None,
        )

    def rebase(
        self,
        state: ConversationSelectionState,
        selection_set: SelectionSet,
        *,
        builder: TaskSpecBuilder,
    ) -> ConversationSelectionState:
        if state.pending_task is None:
            return state
        return _update_state(state, pending_task=builder.rebase(state.pending_task, selection_set))

    def save_confirmation(self, state: ConversationSelectionState, confirmation: PendingConfirmation) -> ConversationSelectionState:
        return _update_state(state, pending_task=None, pending_confirmation=confirmation)

    def cancel_confirmation(self, state: ConversationSelectionState) -> ConversationSelectionState:
        return _update_state(state, pending_task=None, pending_confirmation=None)

    def expire_confirmation(self, state: ConversationSelectionState, *, selection_hash: str | None = None) -> ConversationSelectionState:
        if state.pending_confirmation is None:
            return state
        if selection_hash == state.pending_confirmation.task.selection_hash:
            return state
        return _update_state(
            state,
            pending_task=state.pending_confirmation.task,
            pending_confirmation=None,
        )

    def submit(self, state: ConversationSelectionState, task_id: str) -> ConversationSelectionState:
        if not task_id.strip():
            raise ValueError("task_id must not be blank")
        return _update_state(state, active_task_id=task_id, pending_task=None, pending_confirmation=None)


def _update_state(state: ConversationSelectionState, **changes: object) -> ConversationSelectionState:
    if all(getattr(state, key) == value for key, value in changes.items()):
        return state
    return state.model_copy(update={**changes, "version": state.version + 1})


__all__ = [
    "ConversationSelectionState",
    "ConversationTaskStateStore",
    "PendingSelectionStateStore",
]
