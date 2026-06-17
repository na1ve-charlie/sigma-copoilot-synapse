"""Public dataset projection for Maia turn plans."""

from __future__ import annotations

from maia.api import PlanDataset, WorkspaceContext
from maia.conversation.draft import SelectionDraft
from maia.integrations.sigma.request_mapper import LegacyRecordRequestMapper
from maia.selection.sets import SelectionSet


class DatasetProjector:
    def __init__(self, request_mapper: LegacyRecordRequestMapper | None = None) -> None:
        self._request_mapper = request_mapper or LegacyRecordRequestMapper()

    def from_selection(
        self,
        selection: SelectionSet,
        *,
        workspace_context: WorkspaceContext | None,
        dataset_name: str | None = None,
    ) -> PlanDataset:
        return PlanDataset(
            selection_set_id=selection.selection_set_id,
            selection_hash=selection.selection_hash,
            dataset_id=selection.dataset_id,
            dataset_name=dataset_name or self.dataset_name(selection),
            record_count=selection.record_count,
            record_ids=list(selection.record_ids or ()),
            selection_params=self._selection_params(
                selection.expression,
                selection.limit,
                workspace_context,
            ),
        )

    def from_draft(
        self,
        draft: SelectionDraft,
        *,
        workspace_context: WorkspaceContext | None,
    ) -> PlanDataset:
        return PlanDataset(
            selection_params=self._selection_params(
                draft.expression,
                draft.limit,
                workspace_context,
            )
        )

    @staticmethod
    def dataset_name(selection: SelectionSet) -> str:
        return f"maia-{selection.selection_hash[:12]}"

    def _selection_params(
        self,
        expression: object,
        limit: int | None,
        workspace_context: WorkspaceContext | None,
    ) -> dict[str, object]:
        params = self._request_mapper.map(
            expression,
            workspace_context=workspace_context,
            rows=limit,
        ).to_http_params()
        params.pop("page", None)
        params.pop("rows", None)
        return params


__all__ = ["DatasetProjector"]
