"""SigMA origin-data export adapter."""

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


ORIGIN_EXPORT_PATH = "/api/storage/originData/OriginExport"
ORIGIN_DATA_INFO_LOOKUP_PATH = "/api/storage/dataGroup/listOriginDataInfoByResultIdList"
_SUCCESS_CODES = frozenset({0, 200, "0", "200"})
OriginExportTransport: TypeAlias = Callable[
    [str, dict[str, str], bytes | None, float],
    tuple[int, str],
]


class OriginExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id_list: tuple[int, ...] = Field(serialization_alias="idList")
    path: str = "D:\\exportOriginFile"
    data_export_type: int = Field(serialization_alias="dataExportType")
    system_no: str = Field(serialization_alias="systemNo")

    @field_validator("id_list")
    @classmethod
    def _require_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("idList must not be empty")
        if any(item < 1 for item in value):
            raise ValueError("idList values must be positive")
        return value

    @field_validator("path", "system_no")
    @classmethod
    def _require_text(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value

    @field_validator("data_export_type")
    @classmethod
    def _validate_export_type(cls, value: int) -> int:
        if value not in {0, 1}:
            raise ValueError("dataExportType must be 0 or 1")
        return value

    def to_body(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)


class OriginExportError(RuntimeError):
    __test__: ClassVar[bool] = False


class OriginExportClient:
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        token_provider: SigmaTokenProvider | None = None,
        timeout: float = 5.0,
        endpoint_path: str = ORIGIN_EXPORT_PATH,
        transport: OriginExportTransport | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url:
            raise ValueError("base_url is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._url = f"{normalized_base_url}{_path(endpoint_path)}"
        self._origin_data_info_url = f"{normalized_base_url}{_path(ORIGIN_DATA_INFO_LOOKUP_PATH)}"
        self._timeout = timeout
        self._transport = transport or _post_with_urllib
        self._token = None if token is None or not token.strip() else token.strip()
        self._token_provider = token_provider

    async def export(
        self,
        request: OriginExportRequest,
        *,
        workspace_context: WorkspaceContext | None,
    ) -> dict[str, object]:
        lang = "zh" if workspace_context is None else workspace_context.lang
        headers = _headers(self._token, self._token_provider)
        origin_data_ids = await self._resolve_origin_data_ids(request.id_list, lang=lang, headers=headers)
        export_request = request.model_copy(update={"id_list": origin_data_ids})
        status_code, payload = await self._request_json(
            url=_url_with_lang(self._url, lang),
            headers=headers,
            body=export_request.to_body(),
            operation="origin export",
            empty_payload={},
        )
        error = _backend_error(payload, status_code)
        if error:
            raise OriginExportError(f"SigMA origin export backend error: {error}")
        return payload if isinstance(payload, dict) else {}

    async def _resolve_origin_data_ids(
        self,
        result_ids: tuple[int, ...],
        *,
        lang: str,
        headers: dict[str, str],
    ) -> tuple[int, ...]:
        status_code, payload = await self._request_json(
            url=_url_with_lang(self._origin_data_info_url, lang),
            headers=headers,
            body=list(result_ids),
            operation="origin export id lookup",
            empty_payload=[],
        )
        error = _lookup_backend_error(payload, status_code)
        if error:
            raise OriginExportError(f"SigMA origin export id lookup backend error: {error}")
        return _origin_data_ids(payload)

    async def _request_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: object,
        operation: str,
        empty_payload: object,
    ) -> tuple[int, object]:
        status_code, text = await asyncio.to_thread(
            self._transport,
            url,
            headers,
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            self._timeout,
        )
        try:
            payload = json.loads(text) if text else empty_payload
        except json.JSONDecodeError as exc:
            raise OriginExportError(f"SigMA {operation} returned invalid JSON") from exc
        return status_code, payload


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
) -> dict[str, str]:
    current = token if token_provider is None else token_provider.get()
    headers = {"Content-Type": "application/json"}
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


def _backend_error(payload: object, status_code: int) -> str | None:
    if not isinstance(payload, Mapping):
        return None if status_code < 400 else "unknown error"
    if status_code < 400 and payload.get("code") in _SUCCESS_CODES:
        return None
    messages = _backend_messages(payload)
    if messages:
        return messages
    code = payload.get("code")
    if code not in (None, *tuple(_SUCCESS_CODES)):
        return f"code {code}"
    return f"HTTP {status_code}" if status_code >= 400 else "unknown error"


def _lookup_backend_error(payload: object, status_code: int) -> str | None:
    if _lookup_rows(payload) is not None:
        return None if status_code < 400 else f"HTTP {status_code}"
    return _backend_error(payload, status_code)


def _origin_data_ids(payload: object) -> tuple[int, ...]:
    rows = _lookup_rows(payload)
    if rows is None:
        raise OriginExportError("SigMA origin export id lookup returned invalid payload")
    ids = tuple(_origin_data_id(item) for item in rows)
    if not ids:
        raise OriginExportError("SigMA origin export id lookup returned no origin data ids")
    return ids


def _lookup_rows(payload: object) -> list[object] | None:
    return _row_list(payload)


def _backend_messages(payload: Mapping[str, object]) -> str | None:
    parts = [_text_token(payload.get("msg")), _text_token(payload.get("message"))]
    data = payload.get("data")
    if isinstance(data, Mapping):
        parts.extend(
            [
                _text_token(data.get("content")),
                _text_token(data.get("operation")),
                _text_token(data.get("name")),
                _text_token(data.get("error")),
                _text_token(data.get("message")),
                _text_token(data.get("msg")),
            ]
        )
    messages = [part for part in parts if part]
    if not messages:
        return None
    return " / ".join(dict.fromkeys(messages))


def _row_list(container: object, *, depth: int = 0) -> list[object] | None:
    if depth > 4:
        return None
    if isinstance(container, list):
        return container
    if not isinstance(container, Mapping):
        return None
    for key in ("rows", "list", "content", "data"):
        rows = _row_list(container.get(key), depth=depth + 1)
        if rows is not None:
            return rows
    return None


def _origin_data_id(item: object) -> int:
    if not isinstance(item, Mapping):
        raise OriginExportError("SigMA origin export id lookup returned invalid rows")
    value = item.get("id")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    raise OriginExportError("SigMA origin export id lookup rows must include a positive id")


def _text_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


__all__ = [
    "ORIGIN_DATA_INFO_LOOKUP_PATH",
    "ORIGIN_EXPORT_PATH",
    "OriginExportClient",
    "OriginExportError",
    "OriginExportRequest",
    "OriginExportTransport",
]
