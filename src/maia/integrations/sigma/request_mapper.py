"""Adapter-local mapper from Maia FilterExpression to legacy SigMA record params."""

from __future__ import annotations

from datetime import date, timedelta
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
    "config_version_in": "config_version_list",
    "indicator_in": "indicator_list",
    "sensor_in": "sensor_id_list",
    "serial_number_in": "serial_number_list",
    "summary_result_in": "summary_result_list",
    "test_segment_in": "test_name_list",
    "type_system_in": "system_no_list",
}
_TEXT_FIELDS = {
    "manual_tag_in": "manual_tagging",
    "product_type_in": "product_type",
}
_BOOL_FIELDS = {
    "archive_status_in": ("archive", {"archived": True}),
    "repeat_serial_in": (
        "only_repeat_serial",
        {"repeated": True, "non_repeated": False, "unique": False},
    ),
}
_ARTIFACT_FIELDS = {
    "colormap": "has_color_map",
    "color_map": "has_color_map",
    "pdf": "has_pdf_report",
    "raw": "has_origin_data",
    "raw_data": "has_origin_data",
    "report": "has_pdf_report",
    "result": "has_result_data",
    "result_data": "has_result_data",
}
_TIME_RANGE_DAYS = {
    "today": 1,
    "last_month": 30,
    "last_week": 7,
}


class LegacyRecordRequestParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    __test__: ClassVar[bool] = False

    data_group_id: str | None = Field(default=None, serialization_alias="dataGroupId")
    lang: str = "zh"
    page: int = 1
    rows: int = 500
    product_type: str | None = Field(default=None, serialization_alias="type")
    config_version_list: tuple[str, ...] = Field(default=(), serialization_alias="versionList")
    system_no_list: tuple[str, ...] = Field(default=(), serialization_alias="systemNoList")
    serial_number_list: tuple[str, ...] = Field(default=(), serialization_alias="serialNumberList")
    summary_result_list: tuple[str, ...] = Field(default=(), serialization_alias="sumList")
    manual_tagging: str | None = Field(default=None, serialization_alias="manualTagging")
    archive: bool | None = None
    sensor_id_list: tuple[str, ...] = Field(default=(), serialization_alias="sensorIdList")
    test_name_list: tuple[str, ...] = Field(default=(), serialization_alias="testNameList")
    indicator_list: tuple[str, ...] = Field(default=(), serialization_alias="indicatorList")
    only_repeat_serial: bool | None = Field(default=None, serialization_alias="onlyRepeatSerial")
    has_pdf_report: bool | None = Field(default=None, serialization_alias="hasPdfReport")
    has_origin_data: bool | None = Field(default=None, serialization_alias="hasOriginData")
    has_result_data: bool | None = Field(default=None, serialization_alias="hasResultData")
    has_color_map: bool | None = Field(default=None, serialization_alias="hasColorMap")
    start_time: str | None = Field(default=None, serialization_alias="startTime")
    end_time: str | None = Field(default=None, serialization_alias="endTime")

    @field_validator("data_group_id", "lang", "product_type", "manual_tagging", "start_time", "end_time")
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
        params: dict[str, object] = {}
        for key, value in payload.items():
            if value == []:
                continue
            if isinstance(value, list):
                params[key] = ",".join(value)
            elif isinstance(value, bool):
                params[key] = str(value).lower()
            else:
                params[key] = value
        return params


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
        updates: dict[str, Any] = {}
        if expression is not None:
            for predicate in _conjunctive_predicates(parse_filter_expression(expression)):
                _apply_predicate(updates, predicate)
        return LegacyRecordRequestParams(
            data_group_id=None if workspace_context is None else workspace_context.dataset_id,
            lang="zh" if workspace_context is None else workspace_context.lang,
            page=self._default_page if page is None else page,
            rows=self._default_rows if rows is None else rows,
            **updates,
        )


def _conjunctive_predicates(expression: FilterExpression) -> tuple[Predicate, ...]:
    if isinstance(expression, Predicate):
        return (expression,)
    if isinstance(expression, AllOf):
        return tuple(
            predicate
            for child in expression.expressions
            for predicate in _conjunctive_predicates(child)
        )
    if isinstance(expression, AnyOf):
        raise ValueError("AnyOf legacy query branching is handled in G15")
    raise ValueError("Not legacy query branching is handled in G15")


def _apply_predicate(target: dict[str, Any], predicate: Predicate) -> None:
    if predicate.name in _LIST_FIELDS:
        field_name = _LIST_FIELDS[predicate.name]
        target[field_name] = _merge_texts(target.get(field_name, ()), _values(predicate))
        return
    if predicate.name in _TEXT_FIELDS:
        _set_once(target, _TEXT_FIELDS[predicate.name], _single_value(predicate))
        return
    if predicate.name == "tested_at_between":
        start = predicate.params.get("start")
        end = predicate.params.get("end")
        if start is None and end is None:
            raise ValueError(f"{predicate.name} requires params.start or params.end")
        if start is not None:
            _set_once(target, "start_time", _required_param(predicate, "start"))
        if end is not None:
            _set_once(target, "end_time", _required_param(predicate, "end"))
        return
    if predicate.name == "time_range_in":
        start, end = _time_range_bounds(predicate)
        _set_once(target, "start_time", start)
        _set_once(target, "end_time", end)
        return
    if predicate.name == "data_kind_in":
        for value in _values(predicate):
            _set_once(target, _artifact_field(value), True)
        return
    if predicate.name == "artifact_availability_in":
        if _single_value(predicate) != "available":
            raise ValueError(f"unsupported values for {predicate.name}: {_values(predicate)}")
        return
    if predicate.name in _BOOL_FIELDS:
        field_name, mapping = _BOOL_FIELDS[predicate.name]
        value = _single_value(predicate)
        if value not in mapping:
            raise ValueError(f"unsupported values for {predicate.name}: {(value,)}")
        _set_once(target, field_name, mapping[value])
        return
    raise ValueError(f"unsupported legacy record predicate: {predicate.name}")


def _values(predicate: Predicate) -> tuple[str, ...]:
    raw = predicate.params.get("values")
    if raw is None:
        raise ValueError(f"{predicate.name} requires params.values")
    values = raw if isinstance(raw, tuple) else (raw,)
    return tuple(str(value) for value in values)


def _single_value(predicate: Predicate) -> str:
    values = _values(predicate)
    if len(values) != 1:
        raise ValueError(f"unsupported values for {predicate.name}: {values}")
    return values[0]


def _required_param(predicate: Predicate, key: str) -> str:
    value = predicate.params.get(key)
    if value is None or not str(value).strip():
        raise ValueError(f"{predicate.name} requires params.{key}")
    return str(value)


def _merge_texts(current: tuple[str, ...], incoming: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*current, *incoming)))


def _artifact_field(value: str) -> str:
    key = value.strip().lower()
    if key not in _ARTIFACT_FIELDS:
        raise ValueError(f"unsupported values for data_kind_in: {(value,)}")
    return _ARTIFACT_FIELDS[key]


def _time_range_bounds(predicate: Predicate) -> tuple[str, str]:
    token = _single_value(predicate).strip().lower()
    if token not in _TIME_RANGE_DAYS:
        raise ValueError(f"unsupported values for {predicate.name}: {(token,)}")

    end = _today()
    start = end - timedelta(days=_TIME_RANGE_DAYS[token] - 1)
    return start.isoformat(), end.isoformat()


def _today() -> date:
    return date.today()


def _set_once(target: dict[str, Any], key: str, value: Any) -> None:
    existing = target.get(key)
    if existing not in (None, value):
        raise ValueError(f"conflicting values for {key}")
    target[key] = value


__all__ = ["LegacyRecordRequestMapper", "LegacyRecordRequestParams"]
