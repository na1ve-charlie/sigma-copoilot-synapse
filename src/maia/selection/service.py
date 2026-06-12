from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from maia.api import WorkspaceContext
from maia.conversation.draft import SelectionDraft
from maia.selection.compiler import SelectionQueryCompiler
from maia.selection.expression import AllOf, FilterExpression, Not
from maia.selection.query import SelectionQuery
from maia.selection.sets import SelectionLineage, SelectionSet, SelectionSetRepository, SelectionSort


class SelectionSetMaterializer(Protocol):
    async def materialize(
        self,
        selection_set: SelectionSet,
        *,
        records: tuple[Any, ...] = (),
        workspace_context: WorkspaceContext | None,
    ) -> str | None: ...


class SelectionSetService:
    def __init__(
        self,
        repository: SelectionSetRepository,
        compiler: SelectionQueryCompiler,
        *,
        source_version: str,
        expires_in: timedelta | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        materializer: SelectionSetMaterializer | None = None,
    ) -> None:
        if not source_version.strip():
            raise ValueError("source_version must not be blank")
        self._repository = repository
        self._compiler = compiler
        self._source_version = source_version
        self._expires_in = expires_in
        self._id_factory = id_factory or (lambda: f"sel-{uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._materializer = materializer

    async def create_or_derive(
        self,
        draft: SelectionDraft,
        *,
        workspace_context: WorkspaceContext | None,
    ) -> SelectionSet:
        if draft.expression is None:
            raise ValueError("selection draft must include an expression")
        base = self._load_base(draft.base_selection_id)
        compiled = await self._compiler.compile(
            SelectionQuery(
                expression=draft.expression,
                sort=_selection_sorts(draft),
                limit=draft.limit,
            ),
            workspace_context=workspace_context,
        )
        created_at = self._clock()
        selection_set = SelectionSet(
            selection_set_id=self._id_factory(),
            expression=compiled.query.expression,
            sort=compiled.query.sort,
            limit=compiled.query.limit,
            record_count=compiled.record_count,
            record_ids=compiled.record_ids,
            source_version=self._source_version,
            created_at=created_at,
            expires_at=None if self._expires_in is None else created_at + self._expires_in,
            lineage=self._lineage(base, compiled.query.expression, compiled.query.sort, compiled.query.limit, compiled.record_ids),
        )
        if self._materializer is not None:
            dataset_id = await self._materializer.materialize(
                selection_set,
                records=compiled.records,
                workspace_context=workspace_context,
            )
            if dataset_id is not None:
                selection_set = selection_set.model_copy(update={"dataset_id": dataset_id})
        existing = self._repository.find_by_hash(selection_set.selection_hash)
        return existing or self._repository.save(selection_set)

    def _load_base(self, selection_set_id: str | None) -> SelectionSet | None:
        if selection_set_id is None:
            return None
        selection_set = self._repository.get(selection_set_id)
        if selection_set is None:
            raise LookupError(f"unknown base_selection_id: {selection_set_id}")
        return selection_set

    def _lineage(
        self,
        base: SelectionSet | None,
        expression: FilterExpression,
        sort: tuple[SelectionSort, ...],
        limit: int | None,
        record_ids: tuple[str, ...],
    ) -> SelectionLineage:
        if base is None:
            return SelectionLineage(operation="create")
        if expression == base.expression and (sort != base.sort or limit != base.limit):
            return SelectionLineage(operation="limit", parent_selection_set_id=base.selection_set_id)
        if base.record_ids is None:
            return SelectionLineage(operation="replace", parent_selection_set_id=base.selection_set_id)
        base_ids = set(base.record_ids)
        current_ids = set(record_ids)
        if current_ids.issubset(base_ids):
            operation = "exclude" if _is_exclude(base.expression, expression) else "refine"
        elif base_ids.issubset(current_ids):
            operation = "expand"
        else:
            operation = "replace"
        return SelectionLineage(operation=operation, parent_selection_set_id=base.selection_set_id)


def _selection_sorts(draft: SelectionDraft) -> tuple[SelectionSort, ...]:
    return tuple(SelectionSort(field=item.field, direction=item.direction) for item in draft.sort)


def _is_exclude(base: FilterExpression, current: FilterExpression) -> bool:
    base_positive, base_negative = _split_components(base)
    current_positive, current_negative = _split_components(current)
    return (
        base_positive == current_positive
        and len(current_negative) > len(base_negative)
        and all(component in current_negative for component in base_negative)
    )


def _split_components(expression: FilterExpression) -> tuple[tuple[FilterExpression, ...], tuple[FilterExpression, ...]]:
    parts = expression.expressions if isinstance(expression, AllOf) else (expression,)
    positive: list[FilterExpression] = []
    negative: list[FilterExpression] = []
    for part in parts:
        if isinstance(part, Not):
            negative.append(part.expression)
        else:
            positive.append(part)
    return tuple(positive), tuple(negative)


__all__ = ["SelectionSetMaterializer", "SelectionSetService"]
