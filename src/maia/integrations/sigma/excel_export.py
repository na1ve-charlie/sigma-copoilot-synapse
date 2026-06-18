"""SigMA Excel export and sensor-list adapters."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import ClassVar, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from maia.api import WorkspaceContext
from maia.integrations.sigma.token_provider import SigmaTokenProvider


EXCEL_EXPORT_PATH = "/api/storage/singleStationReport/export"
SENSOR_LIST_PATH = "/api/storage/config/sensor-list"
_SUCCESS_CODES = frozenset({0, 200, "0", "200"})
ExcelExportTransport: TypeAlias = Callable[
    [str, dict[str, str], bytes | None, float],
    tuple[int, str],
]
SensorListTransport: TypeAlias = Callable[
    [str, dict[str, object], dict[str, str], float],
    tuple[int, str],
]


class ExcelExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    type_: str = Field(serialization_alias="type")
    system_no: str = Field(serialization_alias="systemNo")
    id_list: tuple[int, ...] = Field(serialization_alias="idList")
    sensor_id_list: tuple[str, ...] = Field(serialization_alias="sensorIdList")
    one_data: int = Field(serialization_alias="oneData")
    two_data: int = Field(serialization_alias="twoData")
    result_data: int = Field(serialization_alias="resultData")

    @field_validator("type_", "system_no")
    @classmethod
    def _require_text(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value

    @field_validator("id_list")
    @classmethod
    def _require_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("idList must not be empty")
        if any(item < 1 for item in value):
            raise ValueError("idList values must be positive")
        return value

    @field_validator("sensor_id_list")
    @classmethod
    def _require_sensors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("sensorIdList must not be empty")
        return normalized

    @field_validator("one_data", "two_data", "result_data")
    @classmethod
    def _validate_flag(cls, value: int, info) -> int:
        if value not in {0, 1}:
            raise ValueError(f"{info.field_name} must be 0 or 1")
        return value

    def to_body(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)


class ExcelExportError(RuntimeError):
    __test__: ClassVar[bool] = False


class SensorListError(RuntimeError):
    __test__: ClassVar[bool] = False


class ExcelExportClient:
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        token_provider: SigmaTokenProvider | None = None,
        timeout: float = 5.0,
        endpoint_path: str = EXCEL_EXPORT_PATH,
        transport: ExcelExportTransport | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url:
            raise ValueError("base_url is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._url = f"{normalized_base_url}{_path(endpoint_path)}"
        self._timeout = timeout
        self._transport = transport or _post_with_urllib
        self._token = None if token is None or not token.strip() else token.strip()
        self._token_provider = token_provider

    async def export(
        self,
        request: ExcelExportRequest,
        *,
        workspace_context: WorkspaceContext | None,
    ) -> dict[str, object]:
        status_code, text = await asyncio.to_thread(
            self._transport,
            _url_with_lang(
                self._url,
                "zh" if workspace_context is None else workspace_context.lang,
            ),
            _headers(self._token, self._token_provider, include_content_type=True),
            json.dumps(request.to_body(), ensure_ascii=False).encode("utf-8"),
            self._timeout,
        )
        payload = _json_payload(text, operation="excel export", error_type=ExcelExportError)
        error = _backend_error(payload, status_code)
        if error:
            raise ExcelExportError(f"SigMA excel export backend error: {error}")
        return payload if isinstance(payload, dict) else {}


class SensorListClient:
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        token_provider: SigmaTokenProvider | None = None,
        timeout: float = 5.0,
        endpoint_path: str = SENSOR_LIST_PATH,
        transport: SensorListTransport | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url:
            raise ValueError("base_url is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._url = f"{normalized_base_url}{_path(endpoint_path)}"
        self._timeout = timeout
        self._transport = transport or _fetch_with_urllib
        self._token = None if token is None or not token.strip() else token.strip()
        self._token_provider = token_provider

    async def list_sensors(
        self,
        *,
        type_: str,
        system_no: str,
        workspace_context: WorkspaceContext | None,
    ) -> tuple[str, ...]:
        lang = "zh" if workspace_context is None else workspace_context.lang
        status_code, text = await asyncio.to_thread(
            self._transport,
            self._url,
            {"type": type_, "systemNo": system_no, "lang": lang},
            _headers(self._token, self._token_provider),
            self._timeout,
        )
        payload = _json_payload(text, operation="sensor list", error_type=SensorListError)
        error = _backend_error(payload, status_code)
        if error:
            raise SensorListError(f"SigMA sensor list backend error: {error}")
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, list):
            return ()
        return tuple(dict.fromkeys(text for value in data if (text := _coerce_text(value))))


def _path(value: str) -> str:
    return value if value.startswith("/") else f"/{value}"


def _url_with_lang(url: str, lang: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    if "lang" in query:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}lang={urllib.parse.quote(lang)}"


def _headers(
    token: str | None,
    token_provider: SigmaTokenProvider | None,
    *,
    include_content_type: bool = False,
) -> dict[str, str]:
    current = token if token_provider is None else token_provider.get()
    headers = {"Content-Type": "application/json"} if include_content_type else {}
    if current is not None:
        headers["Token"] = current
    return headers


def _post_with_urllib(
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, str]:
    request = urllib.request.Request(url, method="POST", headers=headers, data=body)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _fetch_with_urllib(
    url: str,
    params: dict[str, object],
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, str]:
    query = urllib.parse.urlencode(params, doseq=True)
    request_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(request_url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _json_payload(text: str, *, operation: str, error_type):
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise error_type(f"SigMA {operation} returned invalid JSON") from exc


def _backend_error(payload: object, status_code: int) -> str | None:
    if not isinstance(payload, Mapping):
        return None if status_code < 400 else "unknown error"
    if status_code < 400 and payload.get("code") in _SUCCESS_CODES:
        return None
    for key in ("msg", "message"):
        value = _coerce_text(payload.get(key))
        if value is not None:
            return value
    return f"HTTP {status_code}" if status_code >= 400 else "unknown error"


def _coerce_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "EXCEL_EXPORT_PATH",
    "SENSOR_LIST_PATH",
    "ExcelExportClient",
    "ExcelExportError",
    "ExcelExportRequest",
    "ExcelExportTransport",
    "SensorListClient",
    "SensorListError",
    "SensorListTransport",
]
