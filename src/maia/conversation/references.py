from __future__ import annotations

from maia.conversation.state import ConversationSelectionState
from maia.recognition.report import RecognitionReport
from maia.selection.sets import SelectionSet, SelectionSetRepository


class SelectionReferenceResolutionError(LookupError):
    pass


class SelectionReferenceResolver:
    def __init__(self, repository: SelectionSetRepository) -> None:
        self._repository = repository

    def resolve_report(
        self,
        report: RecognitionReport,
        state: ConversationSelectionState,
    ) -> SelectionSet | None:
        references = tuple(
            dict.fromkeys(
                str(operation.target)
                for operation in report.slot_operations
                if operation.entity_type == "selection_reference" and operation.slot_valid
            )
        )
        if not references:
            return None
        if len(references) != 1:
            raise SelectionReferenceResolutionError("multiple selection references are not supported yet")
        return self.resolve(references[0], state)

    def resolve(
        self,
        reference: str,
        state: ConversationSelectionState,
    ) -> SelectionSet:
        if reference == "active_selection":
            return self._resolve_by_id(state.active_selection_set_id, "active selection")
        if reference == "recent_selection":
            return self._resolve_by_id(_recent_id(state, 0), "recent selection")
        if reference.startswith("recent_selection:"):
            return self._resolve_by_id(_recent_id(state, int(reference.split(":", maxsplit=1)[1])), "recent selection")
        selection = self._repository.get(reference) or self._repository.find_by_hash(reference)
        if selection is None:
            raise SelectionReferenceResolutionError(f"unknown selection reference: {reference}")
        return selection

    def _resolve_by_id(self, selection_set_id: str | None, label: str) -> SelectionSet:
        if selection_set_id is None:
            raise SelectionReferenceResolutionError(f"{label} is not available")
        selection = self._repository.get(selection_set_id)
        if selection is None:
            raise SelectionReferenceResolutionError(f"{label} could not be loaded: {selection_set_id}")
        return selection


def _recent_id(state: ConversationSelectionState, index: int) -> str | None:
    if index < 0:
        raise SelectionReferenceResolutionError("recent selection index must not be negative")
    if index >= len(state.recent_selection_set_ids):
        return None
    return state.recent_selection_set_ids[index]
__all__ = ["SelectionReferenceResolutionError", "SelectionReferenceResolver"]
