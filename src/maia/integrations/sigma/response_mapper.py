"""Adapter-local mapper from legacy SigMA record envelopes to Maia models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from pydantic import ValidationError

from maia.integrations.sigma.records import ArtifactKind, TestRecordPage, TestRecordSummary


_MISSING = object()
_SUCCESS_CODES = frozenset({0, 200, "0", "200"})
_DATA_TOTAL_ALIASES = ("total",)
_DATA_ROWS_ALIASES = ("list", "rows")
_ROW_ALIASES = {
    "record_id": ("recordId", "reportId", "id"),
    "tested_at": ("testedAt", "testTime", "createdAt"),
    "product_type": ("productType",),
    "legacy_type": ("type",),
    "config_version": ("configVersion", "version"),
    "system_no": ("systemNo", "system"),
    "serial_number": ("serialNumber", "serialNo"),
    "summary_result": ("summaryResult", "sum"),
    "manual_tags": ("manualTagList", "manualTags", "manualTagging"),
    "archive_status": ("archiveStatus",),
    "repeat_serial": ("repeatSerial",),
}
_ARTIFACT_LIST_ALIASES = ("availableArtifacts", "artifactKinds", "dataKinds")
_ARTIFACT_FLAG_ALIASES: dict[ArtifactKind, tuple[str, ...]] = {
    "raw_data": ("rawDataAvailable", "rawData", "originData"),
    "result_data": ("resultDataAvailable", "resultData"),
    "report": ("reportAvailable", "report"),
    "audio": ("audioAvailable", "audio", "ngaudio"),
    "colormap": ("colormapAvailable", "colorMapAvailable", "colormap", "colorMap"),
}
_ARTIFACT_NAME_ALIASES: dict[str, ArtifactKind] = {
    "raw": "raw_data",
    "rawdata": "raw_data",
    "result": "result_data",
    "resultdata": "result_data",
    "report": "report",
    "audio": "audio",
    "colormap": "colormap",
}


class LegacyRecordResponseMapper:
    __test__: ClassVar[bool] = False

    def map(self, payload: Mapping[str, object] | dict[str, object]) -> TestRecordPage:
        body = _mapping(payload, "payload")
        code = body.get("code")
        if code not in _SUCCESS_CODES:
            msg = _optional_text(body.get("msg"))
            raise ValueError(f"legacy record query failed with code {code}: {msg or 'unknown error'}")

        data = _mapping(_required_value(body, "data", "payload.data"), "payload.data")
        rows = _row_list(
            _required_alias_value(data, _DATA_ROWS_ALIASES, "payload.data.list"),
            "payload.data.list",
        )
        return TestRecordPage(
            total=_int_value(
                _required_alias_value(data, _DATA_TOTAL_ALIASES, "payload.data.total"),
                "payload.data.total",
            ),
            records=tuple(self._map_row(row, index) for index, row in enumerate(rows)),
        )

    def _map_row(self, row: Mapping[str, object], index: int) -> TestRecordSummary:
        product_type, config_version = _product_identity(row)
        try:
            return TestRecordSummary(
                record_id=_required_text(_lookup(row, _ROW_ALIASES["record_id"]), "record id"),
                tested_at=_optional_datetime(_lookup(row, _ROW_ALIASES["tested_at"])),
                product_type=product_type,
                config_version=config_version,
                system_no=_optional_text(_lookup(row, _ROW_ALIASES["system_no"])),
                serial_number=_optional_text(_lookup(row, _ROW_ALIASES["serial_number"])),
                summary_result=_optional_text(_lookup(row, _ROW_ALIASES["summary_result"])),
                manual_tags=_text_tuple(_lookup(row, _ROW_ALIASES["manual_tags"]), field_name="manual_tags"),
                archive_status=_optional_text(_lookup(row, _ROW_ALIASES["archive_status"])),
                available_artifacts=_available_artifacts(row),
                repeat_serial=_optional_bool(_lookup(row, _ROW_ALIASES["repeat_serial"])),
            )
        except ValidationError as exc:
            raise ValueError(f"invalid legacy record row at index {index}: {exc}") from exc


def _product_identity(row: Mapping[str, object]) -> tuple[str | None, str | None]:
    product_type = _optional_text(_lookup(row, _ROW_ALIASES["product_type"]))
    config_version = _optional_text(_lookup(row, _ROW_ALIASES["config_version"]))
    if product_type is not None:
        return product_type, config_version

    legacy_type = _optional_text(_lookup(row, _ROW_ALIASES["legacy_type"]))
    if legacy_type is None:
        return None, config_version

    split_product_type, split_version = _split_product_type_and_version(legacy_type)
    return split_product_type, config_version or split_version


def _split_product_type_and_version(value: str) -> tuple[str, str | None]:
    product_type, separator, version = value.rpartition("_")
    if not separator:
        return value, None
    normalized_product_type = product_type.strip()
    normalized_version = version.strip()
    if not normalized_product_type or not normalized_version:
        return value, None
    return normalized_product_type, normalized_version


def _mapping(value: Any, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    return value


def _required_value(source: Mapping[str, object], key: str, path: str) -> object:
    if key not in source:
        raise ValueError(f"{path} is required")
    return source[key]


def _required_alias_value(
    source: Mapping[str, object],
    aliases: tuple[str, ...],
    path: str,
) -> object:
    value = _lookup(source, aliases)
    if value is _MISSING:
        raise ValueError(f"{path} is required")
    return value


def _row_list(value: object, path: str) -> tuple[Mapping[str, object], ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        rows = []
        for index, item in enumerate(value):
            rows.append(_mapping(item, f"{path}[{index}]"))
        return tuple(rows)
    raise ValueError(f"{path} must be an array")


def _lookup(source: Mapping[str, object], aliases: tuple[str, ...]) -> object:
    for alias in aliases:
        if alias in source:
            return source[alias]
    return _MISSING


def _required_text(value: object, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is _MISSING or value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_datetime(value: object) -> str | None:
    return _optional_text(value)


def _text_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if value is _MISSING or value is None:
        return ()
    items = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else (value,)
    normalized: list[str] = []
    for item in items:
        text = _optional_text(item)
        if text is None:
            continue
        if text not in normalized:
            normalized.append(text)
    return tuple(normalized)


def _available_artifacts(row: Mapping[str, object]) -> tuple[ArtifactKind, ...]:
    explicit = _lookup(row, _ARTIFACT_LIST_ALIASES)
    if explicit is not _MISSING and explicit is not None:
        values = explicit if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes, bytearray)) else (explicit,)
        return tuple(dict.fromkeys(_artifact_name(value) for value in values))

    derived: list[ArtifactKind] = []
    for artifact, aliases in _ARTIFACT_FLAG_ALIASES.items():
        if _optional_bool(_lookup(row, aliases)) is True:
            derived.append(artifact)
    return tuple(derived)


def _artifact_name(value: object) -> ArtifactKind:
    text = _required_text(value, "artifact kind").replace("-", "_")
    key = text.casefold().replace("_", "")
    if key not in _ARTIFACT_NAME_ALIASES:
        raise ValueError(f"unsupported artifact kind: {text}")
    return _ARTIFACT_NAME_ALIASES[key]


def _optional_bool(value: object) -> bool | None:
    if value is _MISSING or value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    raise ValueError(f"invalid boolean value: {value}")


def _int_value(value: object, path: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError(f"{path} must be an integer")


__all__ = ["LegacyRecordResponseMapper"]
