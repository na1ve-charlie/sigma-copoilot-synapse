from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from maia.conversation.draft import SelectionDraft, SelectionDraftReducer
from maia.recognition.report import RecognitionReport


class ConversationSelectionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_selection_set_id: str | None = None
    recent_selection_set_ids: tuple[str, ...] = ()
    pending_selection_draft: SelectionDraft | None = None
    version: int = 0

    @field_validator("active_selection_set_id")
    @classmethod
    def _validate_active(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("active_selection_set_id must not be blank")
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
        if state.pending_selection_draft == draft:
            return state
        return state.model_copy(update={"pending_selection_draft": draft, "version": state.version + 1})

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
        return state.model_copy(
            update={
                "active_selection_set_id": selection_set_id,
                "recent_selection_set_ids": _push_recent(
                    selection_set_id,
                    state.active_selection_set_id,
                    state.recent_selection_set_ids,
                ),
                "pending_selection_draft": None,
                "version": state.version + 1,
            }
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


__all__ = ["ConversationSelectionState", "PendingSelectionStateStore"]
