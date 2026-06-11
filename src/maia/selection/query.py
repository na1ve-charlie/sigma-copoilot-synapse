"""SelectionQuery models for compiled Maia selection queries."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, computed_field, field_validator

from maia.integrations.sigma.records import TestRecordSummary
from maia.selection.expression import FilterExpression, parse_filter_expression
from maia.selection.sets import SelectionSort


class SelectionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expression: FilterExpression
    sort: tuple[SelectionSort, ...] = ()
    limit: int | None = None

    @field_validator("expression", mode="before")
    @classmethod
    def _parse_expression(cls, value: object) -> FilterExpression:
        return parse_filter_expression(value)

    @field_validator("limit")
    @classmethod
    def _validate_limit(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("limit must be positive")
        return value


class CompiledSelectionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: SelectionQuery
    records: tuple[TestRecordSummary, ...] = ()

    @field_validator("query", mode="before")
    @classmethod
    def _parse_query(cls, value: object) -> SelectionQuery:
        return parse_selection_query(value)

    @computed_field(return_type=tuple[str, ...])
    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(record.record_id for record in self.records)

    @computed_field(return_type=int)
    @property
    def record_count(self) -> int:
        return len(self.records)


_SELECTION_QUERY_ADAPTER = TypeAdapter(SelectionQuery)


def parse_selection_query(
    value: SelectionQuery | Mapping[str, object],
) -> SelectionQuery:
    if isinstance(value, SelectionQuery):
        return value
    return _SELECTION_QUERY_ADAPTER.validate_python(value)


__all__ = [
    "CompiledSelectionQuery",
    "SelectionQuery",
    "parse_selection_query",
]
