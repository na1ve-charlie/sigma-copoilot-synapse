"""Internal SigMA test-record models used by Maia query adapters."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, computed_field, field_validator, model_validator


ArtifactKind: TypeAlias = Literal[
    "raw_data",
    "result_data",
    "report",
    "audio",
    "colormap",
]


class SigmaRecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TestRecordSummary(SigmaRecordModel):
    __test__: ClassVar[bool] = False

    record_id: str
    tested_at: datetime | None = None
    product_type: str | None = None
    config_version: str | None = None
    system_no: str | None = None
    serial_number: str | None = None
    summary_result: str | None = None
    manual_tags: tuple[str, ...] = ()
    archive_status: str | None = None
    available_artifacts: tuple[ArtifactKind, ...] = ()
    repeat_serial: bool | None = None

    @field_validator(
        "record_id",
        "product_type",
        "config_version",
        "system_no",
        "serial_number",
        "summary_result",
        "archive_status",
    )
    @classmethod
    def _require_text(cls, value: str | None, info) -> str | None:
        if value is not None and not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value

    @field_validator("manual_tags")
    @classmethod
    def _validate_manual_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="manual_tags")

    @field_validator("available_artifacts")
    @classmethod
    def _dedupe_artifacts(
        cls,
        value: tuple[ArtifactKind, ...],
    ) -> tuple[ArtifactKind, ...]:
        return tuple(dict.fromkeys(value))


class TestRecordPage(SigmaRecordModel):
    __test__: ClassVar[bool] = False

    total: int
    records: tuple[TestRecordSummary, ...] = ()

    @field_validator("total")
    @classmethod
    def _validate_total(cls, value: int) -> int:
        if value < 0:
            raise ValueError("total must not be negative")
        return value

    @model_validator(mode="after")
    def _validate_records(self) -> Self:
        if self.total < len(self.records):
            raise ValueError("total must be greater than or equal to returned records")
        if len(self.record_ids) != len(set(self.record_ids)):
            raise ValueError("duplicate record_id values are not allowed")
        return self

    @computed_field(return_type=int)
    @property
    def returned_count(self) -> int:
        return len(self.records)

    @computed_field(return_type=tuple[str, ...])
    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(record.record_id for record in self.records)


def _normalize_text_tuple(
    values: tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        if not value.strip():
            raise ValueError(f"{field_name} must not contain blank values")
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


__all__ = ["ArtifactKind", "TestRecordPage", "TestRecordSummary"]
