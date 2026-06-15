"""Compile FilterExpression trees into paged legacy record queries and set operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from maia.api import WorkspaceContext
from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary
from maia.selection.expression import AllOf, AnyOf, FilterExpression, Not, Predicate, parse_filter_expression
from maia.selection.query import CompiledSelectionQuery, SelectionQuery, parse_selection_query
from maia.selection.sets import SelectionSort

ALL_RECORDS_PREDICATE_NAME = "all_records"


class SelectionRecordClient(Protocol):
    async def list_records(
        self,
        expression: FilterExpression | Mapping[str, object] | None,
        *,
        workspace_context: WorkspaceContext | None,
        page: int | None = None,
        rows: int | None = None,
    ) -> TestRecordPage: ...


class SelectionQueryCompileError(ValueError):
    pass


class SelectionQueryCompiler:
    def __init__(
        self,
        record_client: SelectionRecordClient,
        *,
        page_size: int = 500,
    ) -> None:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        self._record_client = record_client
        self._page_size = page_size

    async def compile(
        self,
        query: SelectionQuery | Mapping[str, object],
        *,
        workspace_context: WorkspaceContext | None,
    ) -> CompiledSelectionQuery:
        parsed = parse_selection_query(query)
        fetch_limit = _pushdown_fetch_limit(parsed)
        can_pushdown, pushdown_expression = _limited_pushdown_expression(parsed.expression)
        if fetch_limit is not None and can_pushdown:
            records = await self._fetch_records(
                pushdown_expression,
                workspace_context=workspace_context,
                max_records=fetch_limit,
            )
        else:
            records = await self.records_for_expression(
                parsed.expression,
                workspace_context=workspace_context,
            )
        ordered = _sort_records(records, parsed.sort)
        limited = ordered if parsed.limit is None else ordered[: parsed.limit]
        return CompiledSelectionQuery(query=parsed, records=limited)

    async def records_for_expression(
        self,
        expression: FilterExpression | Mapping[str, object] | None,
        *,
        workspace_context: WorkspaceContext | None,
    ) -> tuple[TestRecordSummary, ...]:
        if expression is None:
            return await self._fetch_dataset_scope(workspace_context=workspace_context)
        return await self._evaluate(expression, workspace_context=workspace_context)

    async def _evaluate(
        self,
        expression: FilterExpression | Mapping[str, object],
        *,
        workspace_context: WorkspaceContext | None,
    ) -> tuple[TestRecordSummary, ...]:
        parsed = parse_filter_expression(expression)
        if isinstance(parsed, Predicate) and parsed.name == ALL_RECORDS_PREDICATE_NAME:
            return await self._fetch_dataset_scope(workspace_context=workspace_context)
        if isinstance(parsed, Predicate):
            return await self._fetch_pushdown_records(parsed, workspace_context=workspace_context)
        if isinstance(parsed, AnyOf):
            combined: tuple[TestRecordSummary, ...] = ()
            for child in parsed.expressions:
                combined = _union_records(
                    combined,
                    await self._evaluate(child, workspace_context=workspace_context),
                )
            return combined
        if isinstance(parsed, Not):
            return _difference_records(
                await self._fetch_dataset_scope(workspace_context=workspace_context),
                await self._evaluate(parsed.expression, workspace_context=workspace_context),
            )

        positive = [child for child in parsed.expressions if not isinstance(child, Not)]
        negative = [child.expression for child in parsed.expressions if isinstance(child, Not)]
        if positive:
            merged_positive = _merged_pushdown_expression(positive)
            if merged_positive is not None:
                current = await self._fetch_pushdown_records(
                    merged_positive,
                    workspace_context=workspace_context,
                )
            else:
                current = await self._evaluate(positive[0], workspace_context=workspace_context)
                for child in positive[1:]:
                    current = _intersect_records(
                        current,
                        await self._evaluate(child, workspace_context=workspace_context),
                    )
        else:
            current = await self._fetch_dataset_scope(workspace_context=workspace_context)
        for child in negative:
            current = _difference_records(
                current,
                await self._evaluate(child, workspace_context=workspace_context),
            )
        return current

    async def _fetch_pushdown_records(
        self,
        expression: FilterExpression,
        *,
        workspace_context: WorkspaceContext | None,
    ) -> tuple[TestRecordSummary, ...]:
        try:
            return await self._fetch_records(
                expression,
                workspace_context=workspace_context,
            )
        except ValueError as exc:
            raise SelectionQueryCompileError(
                f"predicate cannot be queried or record-level validated yet: {self._describe(expression)}"
            ) from exc

    async def _fetch_dataset_scope(
        self,
        *,
        workspace_context: WorkspaceContext | None,
    ) -> tuple[TestRecordSummary, ...]:
        return await self._fetch_records(None, workspace_context=workspace_context)

    async def _fetch_records(
        self,
        expression: FilterExpression | None,
        *,
        workspace_context: WorkspaceContext | None,
        max_records: int | None = None,
    ) -> tuple[TestRecordSummary, ...]:
        records: tuple[TestRecordSummary, ...] = ()
        page = 1
        rows = _rows_for_limit(self._page_size, max_records)
        while True:
            result = await self._record_client.list_records(
                expression,
                workspace_context=workspace_context,
                page=page,
                rows=rows,
            )
            records = _union_records(records, result.records)
            if (
                not result.records
                or (max_records is not None and len(records) >= max_records)
                or len(records) >= result.total
                or result.returned_count < rows
            ):
                return records
            page += 1

    @staticmethod
    def _describe(expression: FilterExpression) -> str:
        return expression.name if isinstance(expression, Predicate) else expression.kind


def _merged_pushdown_expression(
    expressions: list[FilterExpression],
) -> FilterExpression | None:
    if not expressions or not all(_is_pushdown_compatible(expression) for expression in expressions):
        return None
    if len(expressions) == 1:
        return expressions[0]
    return AllOf(expressions=tuple(expressions))


def _is_pushdown_compatible(expression: FilterExpression) -> bool:
    if isinstance(expression, Predicate):
        return True
    if isinstance(expression, AllOf):
        return all(_is_pushdown_compatible(child) for child in expression.expressions)
    return False


def _pushdown_fetch_limit(query: SelectionQuery) -> int | None:
    if query.limit is None or len(query.sort) != 1:
        return None
    sort = query.sort[0]
    if sort.field != "tested_at" or sort.direction != "desc":
        return None
    return query.limit


def _limited_pushdown_expression(expression: FilterExpression) -> tuple[bool, FilterExpression | None]:
    parsed = parse_filter_expression(expression)
    if isinstance(parsed, Predicate) and parsed.name == ALL_RECORDS_PREDICATE_NAME:
        return True, None
    if _is_pushdown_compatible(parsed):
        return True, parsed
    return False, None


def _rows_for_limit(page_size: int, max_records: int | None) -> int:
    if max_records is None or max_records > page_size:
        return page_size
    return max_records


def _union_records(
    left: tuple[TestRecordSummary, ...],
    right: tuple[TestRecordSummary, ...],
) -> tuple[TestRecordSummary, ...]:
    merged = list(left)
    seen = {record.record_id for record in left}
    for record in right:
        if record.record_id not in seen:
            merged.append(record)
            seen.add(record.record_id)
    return tuple(merged)


def _intersect_records(
    left: tuple[TestRecordSummary, ...],
    right: tuple[TestRecordSummary, ...],
) -> tuple[TestRecordSummary, ...]:
    right_ids = {record.record_id for record in right}
    return tuple(record for record in left if record.record_id in right_ids)


def _difference_records(
    left: tuple[TestRecordSummary, ...],
    right: tuple[TestRecordSummary, ...],
) -> tuple[TestRecordSummary, ...]:
    right_ids = {record.record_id for record in right}
    return tuple(record for record in left if record.record_id not in right_ids)


def _sort_records(
    records: tuple[TestRecordSummary, ...],
    sort: tuple[SelectionSort, ...],
) -> tuple[TestRecordSummary, ...]:
    ordered = list(records)
    for item in reversed(sort):
        if item.field not in TestRecordSummary.model_fields:
            raise SelectionQueryCompileError(f"unsupported sort field: {item.field}")
        present = [record for record in ordered if getattr(record, item.field) is not None]
        missing = [record for record in ordered if getattr(record, item.field) is None]
        present.sort(
            key=lambda record: getattr(record, item.field),
            reverse=item.direction == "desc",
        )
        ordered = [*present, *missing]
    return tuple(ordered)


__all__ = [
    "ALL_RECORDS_PREDICATE_NAME",
    "SelectionQueryCompileError",
    "SelectionQueryCompiler",
    "SelectionRecordClient",
]
