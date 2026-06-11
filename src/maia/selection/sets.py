"""SelectionSet models and repository for the Maia selection domain."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, computed_field, field_validator, model_validator

from maia.selection.expression import FilterExpression, parse_filter_expression


class SelectionSort(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    direction: Literal["asc", "desc"] = "asc"

    @field_validator("field")
    @classmethod
    def _require_field(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sort.field must not be blank")
        return value


class SelectionLineage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["create", "refine", "expand", "exclude", "replace", "limit"]
    parent_selection_set_id: str | None = None

    @model_validator(mode="after")
    def _validate_parent(self) -> Self:
        has_parent = bool(self.parent_selection_set_id and self.parent_selection_set_id.strip())
        if self.operation == "create" and has_parent:
            raise ValueError("parent_selection_set_id must be empty for create")
        if self.operation != "create" and not has_parent:
            raise ValueError("parent_selection_set_id is required for derived operations")
        return self


class SelectionSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selection_set_id: str
    expression: FilterExpression
    sort: tuple[SelectionSort, ...] = ()
    limit: int | None = None
    record_count: int
    record_ids: tuple[str, ...] | None = None
    snapshot_ref: str | None = None
    source_version: str
    created_at: datetime
    expires_at: datetime | None = None
    lineage: SelectionLineage

    @field_validator("selection_set_id", "source_version")
    @classmethod
    def _require_text(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value

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

    @field_validator("record_count")
    @classmethod
    def _validate_record_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("record_count must not be negative")
        return value

    @field_validator("record_ids")
    @classmethod
    def _validate_record_ids(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        if any(not item.strip() for item in value):
            raise ValueError("record_ids must not contain blank values")
        return value

    @field_validator("snapshot_ref")
    @classmethod
    def _validate_snapshot_ref(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("snapshot_ref must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_sources(self) -> Self:
        has_record_ids = self.record_ids is not None
        has_snapshot_ref = self.snapshot_ref is not None
        if has_record_ids == has_snapshot_ref:
            raise ValueError("exactly one record source must be provided")
        if self.record_ids is not None and self.record_count != len(self.record_ids):
            raise ValueError("record_count must match record_ids length")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        return self

    @computed_field(return_type=str)
    @property
    def selection_hash(self) -> str:
        payload = {
            "expression": self.expression.model_dump(mode="json"),
            "sort": [item.model_dump(mode="json") for item in self.sort],
            "limit": self.limit,
            "record_count": self.record_count,
            "record_ids": list(self.record_ids) if self.record_ids is not None else None,
            "snapshot_ref": self.snapshot_ref,
            "source_version": self.source_version,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @computed_field(return_type=str)
    @property
    def derived_operation(self) -> str:
        return self.lineage.operation

    @computed_field(return_type=str | None)
    @property
    def parent_selection_set_id(self) -> str | None:
        return self.lineage.parent_selection_set_id


class SelectionSetRepository(Protocol):
    def save(self, selection_set: SelectionSet) -> SelectionSet: ...
    def get(self, selection_set_id: str) -> SelectionSet | None: ...
    def find_by_hash(self, selection_hash: str) -> SelectionSet | None: ...
    def list_recent(self, *, limit: int | None = None) -> tuple[SelectionSet, ...]: ...


class InMemorySelectionSetRepository:
    """Simple in-memory repository used by the early Maia execution goals."""

    def __init__(self) -> None:
        self._items: dict[str, SelectionSet] = {}

    def save(self, selection_set: SelectionSet) -> SelectionSet:
        existing = self._items.get(selection_set.selection_set_id)
        if existing is not None and existing != selection_set:
            raise ValueError(
                f"selection_set_id already exists: {selection_set.selection_set_id}"
            )
        self._items[selection_set.selection_set_id] = selection_set
        return selection_set

    def get(self, selection_set_id: str) -> SelectionSet | None:
        return self._items.get(selection_set_id)

    def find_by_hash(self, selection_hash: str) -> SelectionSet | None:
        for item in reversed(tuple(self._items.values())):
            if item.selection_hash == selection_hash:
                return item
        return None

    def list_recent(self, *, limit: int | None = None) -> tuple[SelectionSet, ...]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        items = tuple(
            sorted(
                self._items.values(),
                key=lambda item: (item.created_at, item.selection_set_id),
                reverse=True,
            )
        )
        return items if limit is None else items[:limit]


__all__ = [
    "InMemorySelectionSetRepository",
    "SelectionLineage",
    "SelectionSet",
    "SelectionSetRepository",
    "SelectionSort",
]
