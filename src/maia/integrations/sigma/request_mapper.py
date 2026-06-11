"""Adapter-local mapper from Maia FilterExpression to legacy SigMA record params."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from maia.api import WorkspaceContext
from maia.selection.expression import (
    AllOf,
    AnyOf,
    FilterExpression,
    Not,
    Predicate,
    parse_filter_expression,
)


_LIST_FIELDS = {
    "archive_status_in": "archive_status_list",
    "config_version_in": "config_version_list",
    "data_kind_in": "data_kind_list",
    "indicator_in": "indicator_name_list",
    "manual_tag_in": "manual_tag_list",
    "product_type_in": "product_type_list",
    "sensor_in": "sensor_list",
    "serial_number_in": "serial_number_list",
    "summary_result_in": "summary_result_list",
    "test_segment_in": "test_name_list",
    "type_system_in": "system_no_list",
}
_BOOL_FIELDS = {
    "artifact_availability_in": ("artifact_available", {"available": True, "unavailable": False, "missing": False}),
    "repeat_serial_in": ("repeat_serial", {"repeated": True, "non_repeated": False, "unique": False}),
}


class LegacyRecordRequestParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    __test__: ClassVar[bool] = False

    data_group_id: str = Field(serialization_alias="dataGroupId")
    lang: str = "zh"
    page: int = 1
    rows: int = 500
    product_type_list: tuple[str, ...] = Field(default=(), serialization_alias="productTypeList")
    config_version_list: tuple[str, ...] = Field(default=(), serialization_alias="configVersionList")
    system_no_list: tuple[str, ...] = Field(default=(), serialization_alias="systemNoList")
    serial_number_list: tuple[str, ...] = Field(default=(), serialization_alias="serialNumberList")
    summary_result_list: tuple[str, ...] = Field(default=(), serialization_alias="summaryResultList")
    manual_tag_list: tuple[str, ...] = Field(default=(), serialization_alias="manualTagList")
    archive_status_list: tuple[str, ...] = Field(default=(), serialization_alias="archiveStatusList")
    data_kind_list: tuple[str, ...] = Field(default=(), serialization_alias="dataKindList")
    sensor_list: tuple[str, ...] = Field(default=(), serialization_alias="sensorList")
    test_name_list: tuple[str, ...] = Field(default=(), serialization_alias="testNameList")
    indicator_name_list: tuple[str, ...] = Field(default=(), serialization_alias="indicatorNameList")
    artifact_available: bool | None = Field(default=None, serialization_alias="artifactAvailable")
    repeat_serial: bool | None = Field(default=None, serialization_alias="repeatSerial")
    tested_at_start: str | None = Field(default=None, serialization_alias="testedAtStart")
    tested_at_end: str | None = Field(default=None, serialization_alias="testedAtEnd")

    @field_validator("data_group_id", "lang", "tested_at_start", "tested_at_end")
    @classmethod
    def _require_text(cls, value: str | None, info) -> str | None:
        if value is not None and not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value

    @field_validator("page", "rows")
    @classmethod
    def _require_positive(cls, value: int, info) -> int:
        if value < 1:
            raise ValueError(f"{info.field_name} must be positive")
        return value

    @field_validator(*tuple(_LIST_FIELDS.values()))
    @classmethod
    def _normalize_lists(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        result: list[str] = []
        for item in value:
            if not item.strip():
                raise ValueError(f"{info.field_name} must not contain blank values")
            if item not in result:
                result.append(item)
        return tuple(result)

    def to_http_params(self) -> dict[str, object]:
        payload = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        return {key: value for key, value in payload.items() if value != []}


class LegacyRecordRequestMapper:
    def __init__(self, *, default_page: int = 1, default_rows: int = 500) -> None:
        self._default_page = default_page
        self._default_rows = default_rows

    def map(
        self,
        expression: FilterExpression | dict[str, object] | None,
        *,
        workspace_context: WorkspaceContext | None,
        page: int | None = None,
        rows: int | None = None,
    ) -> LegacyRecordRequestParams:
        if workspace_context is None or not workspace_context.dataset_id:
            raise ValueError("workspace_context.dataset_id is required for legacy record queries")

        updates: dict[str, Any] = {}
        if expression is not None:
            for predicate in _conjunctive_predicates(parse_filter_expression(expression)):
                _apply_predicate(updates, predicate)
        return LegacyRecordRequestParams(
            data_group_id=workspace_context.dataset_id,
            lang=workspace_context.lang,
            page=self._default_page if page is None else page,
            rows=self._default_rows if rows is None else rows,
            **updates,
        )


def _conjunctive_predicates(expression: FilterExpression) -> tuple[Predicate, ...]:
    if isinstance(expression, Predicate):
        return (expression,)
    if isinstance(expression, AllOf):
        return tuple(predicate for child in expression.expressions for predicate in _conjunctive_predicates(child))
    if isinstance(expression, AnyOf):
        raise ValueError("AnyOf legacy query branching is handled in G15")
    raise ValueError("Not legacy query branching is handled in G15")


def _apply_predicate(target: dict[str, Any], predicate: Predicate) -> None:
    if predicate.name in _LIST_FIELDS:
        field_name = _LIST_FIELDS[predicate.name]
        target[field_name] = _merge_texts(target.get(field_name, ()), _values(predicate))
        return
    if predicate.name == "tested_at_between":
        start = _required_param(predicate, "start")
        end = _required_param(predicate, "end")
        _set_once(target, "tested_at_start", start)
        _set_once(target, "tested_at_end", end)
        return
    if predicate.name in _BOOL_FIELDS:
        field_name, mapping = _BOOL_FIELDS[predicate.name]
        values = _values(predicate)
        if len(values) != 1 or values[0] not in mapping:
            raise ValueError(f"unsupported values for {predicate.name}: {values}")
        _set_once(target, field_name, mapping[values[0]])
        return
    raise ValueError(f"unsupported legacy record predicate: {predicate.name}")


def _values(predicate: Predicate) -> tuple[str, ...]:
    raw = predicate.params.get("values")
    if raw is None:
        raise ValueError(f"{predicate.name} requires params.values")
    values = raw if isinstance(raw, tuple) else (raw,)
    return tuple(str(value) for value in values)


def _required_param(predicate: Predicate, key: str) -> str:
    value = predicate.params.get(key)
    if value is None or not str(value).strip():
        raise ValueError(f"{predicate.name} requires params.{key}")
    return str(value)


def _merge_texts(current: tuple[str, ...], incoming: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*current, *incoming)))


def _set_once(target: dict[str, Any], key: str, value: Any) -> None:
    existing = target.get(key)
    if existing not in (None, value):
        raise ValueError(f"conflicting values for {key}")
    target[key] = value


__all__ = ["LegacyRecordRequestMapper", "LegacyRecordRequestParams"]
