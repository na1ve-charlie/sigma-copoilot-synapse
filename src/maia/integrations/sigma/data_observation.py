"""SigMA data-observation catalog adapters."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import ClassVar, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from maia.api import WorkspaceContext
from maia.integrations.sigma.token_provider import SigmaTokenProvider


RESULT_EXIST_MAP_PATH = "/api/storage/resultData/getResultExistMap"
LIST_ONE_INDICATORS_PATH = "/api/storage/config/listOneIndicatorsByResult"
LIST_LINE_INDICATORS_PATH = "/api/storage/config/listLineIndicatorsByResult"
LIST_MULTI_LINE_INDICATORS_PATH = "/api/storage/config/listMultiLineIndicatorsByResult"
_SUCCESS_CODES = frozenset({0, 200, "0", "200"})

ObservationAvailabilityTransport: TypeAlias = Callable[
    [str, dict[str, object], dict[str, str], float],
    tuple[int, str],
]
ObservationIndicatorTransport: TypeAlias = Callable[
    [str, dict[str, str], bytes | None, float],
    tuple[int, str],
]


@dataclass(frozen=True)
class ObservationAvailability:
    data_type: str
    sensor: str
    test_name: str


@dataclass(frozen=True)
class ObservationTypeSystem:
    type_: str
    system_no: str

    def to_body(self) -> dict[str, str]:
        return {"type": self.type_, "systemNo": self.system_no}


class ObservationIndicator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    index: str

    @field_validator("name", "index")
    @classmethod
    def _require_text(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value

    def to_param(self) -> dict[str, str]:
        return {"name": self.name, "index": self.index}


class DataObservationCatalogError(RuntimeError):
    __test__: ClassVar[bool] = False


class SigmaDataObservationCatalogClient:
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        token_provider: SigmaTokenProvider | None = None,
        timeout: float = 5.0,
        availability_path: str = RESULT_EXIST_MAP_PATH,
        one_indicator_path: str = LIST_ONE_INDICATORS_PATH,
        line_indicator_path: str = LIST_LINE_INDICATORS_PATH,
        multi_line_indicator_path: str = LIST_MULTI_LINE_INDICATORS_PATH,
        availability_transport: ObservationAvailabilityTransport | None = None,
        indicator_transport: ObservationIndicatorTransport | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url:
            raise ValueError("base_url is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._availability_url = f"{normalized_base_url}{_path(availability_path)}"
        self._one_indicator_url = f"{normalized_base_url}{_path(one_indicator_path)}"
        self._line_indicator_url = f"{normalized_base_url}{_path(line_indicator_path)}"
        self._multi_line_indicator_url = f"{normalized_base_url}{_path(multi_line_indicator_path)}"
        self._timeout = timeout
        self._token = None if token is None or not token.strip() else token.strip()
        self._token_provider = token_provider
        self._availability_transport = availability_transport or _fetch_with_urllib
        self._indicator_transport = indicator_transport or _post_with_urllib

    async def list_availability(self, dataset_id: str) -> tuple[ObservationAvailability, ...]:
        if not dataset_id.strip():
            return ()
        status_code, text = await asyncio.to_thread(
            self._availability_transport,
            self._availability_url,
            {"dataGroupId": dataset_id},
            _headers(self._token, self._token_provider),
            self._timeout,
        )
        payload = _json_payload(text, operation="result availability")
        error = _backend_error(payload, status_code)
        if error:
            raise DataObservationCatalogError(f"SigMA result availability backend error: {error}")
        return _availability_rows(payload)

    async def list_indicators(
        self,
        *,
        data_type: str,
        sensor_list: tuple[str, ...],
        test_name_list: tuple[str, ...],
        type_systems: tuple[ObservationTypeSystem, ...],
        workspace_context: WorkspaceContext | None,
    ) -> tuple[ObservationIndicator, ...]:
        if not data_type.strip() or not sensor_list or not test_name_list or not type_systems:
            return ()
        body: dict[str, object] = {
            "sensorList": list(sensor_list),
            "testNameList": list(test_name_list),
            "typeSystemVOList": [item.to_body() for item in type_systems],
        }
        if data_type != "ONE_D":
            body["dataType"] = data_type
        status_code, text = await asyncio.to_thread(
            self._indicator_transport,
            _url_with_lang(
                self._indicator_url(data_type),
                "zh" if workspace_context is None else workspace_context.lang,
            ),
            _headers(self._token, self._token_provider, include_content_type=True),
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            self._timeout,
        )
        payload = _json_payload(text, operation="observation indicators")
        error = _backend_error(payload, status_code)
        if error:
            raise DataObservationCatalogError(f"SigMA observation indicators backend error: {error}")
        return _indicator_rows(payload)

    def _indicator_url(self, data_type: str) -> str:
        if data_type == "ONE_D":
            return self._one_indicator_url
        if data_type == "TWO_D_OC":
            return self._multi_line_indicator_url
        return self._line_indicator_url


def _availability_rows(payload: object) -> tuple[ObservationAvailability, ...]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        return ()
    rows: list[ObservationAvailability] = []
    for data_type, sensors in data.items():
        if not isinstance(sensors, Mapping):
            continue
        for sensor, tests in sensors.items():
            if not isinstance(tests, list):
                continue
            for test_name in tests:
                row = _availability_row(data_type, sensor, test_name)
                if row is not None:
                    rows.append(row)
    return tuple(rows)


def _availability_row(
    data_type: object,
    sensor: object,
    test_name: object,
) -> ObservationAvailability | None:
    data_type_text = _coerce_text(data_type)
    sensor_text = _coerce_text(sensor)
    test_name_text = _coerce_text(test_name)
    if not data_type_text or not sensor_text or not test_name_text:
        return None
    return ObservationAvailability(data_type_text, sensor_text, test_name_text)


def _indicator_rows(payload: object) -> tuple[ObservationIndicator, ...]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    items = data if isinstance(data, list) else payload if isinstance(payload, list) else ()
    rows: list[ObservationIndicator] = []
    for item in items:
        indicator = _indicator_item(item)
        if indicator is not None and indicator not in rows:
            rows.append(indicator)
    return tuple(rows)


def _indicator_item(value: object) -> ObservationIndicator | None:
    if not isinstance(value, Mapping):
        return None
    name = _first_text(value, ("name", "indicatorName", "label"))
    index = _first_text(value, ("index", "indicatorIndex", "value", "id"))
    if name is None or index is None:
        return None
    return ObservationIndicator(name=name, index=index)


def _first_text(value: Mapping[object, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        text = _coerce_text(value.get(key))
        if text is not None:
            return text
    return None


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


def _json_payload(text: str, *, operation: str) -> object:
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise DataObservationCatalogError(f"SigMA {operation} returned invalid JSON") from exc


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
    "DataObservationCatalogError",
    "LIST_LINE_INDICATORS_PATH",
    "LIST_MULTI_LINE_INDICATORS_PATH",
    "LIST_ONE_INDICATORS_PATH",
    "ObservationAvailability",
    "ObservationAvailabilityTransport",
    "ObservationIndicator",
    "ObservationIndicatorTransport",
    "ObservationTypeSystem",
    "RESULT_EXIST_MAP_PATH",
    "SigmaDataObservationCatalogClient",
]
