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
        status_code, text = await asyncio.to_thread(
            self._transport,
            _url_with_lang(
                self._url,
                "zh" if workspace_context is None else workspace_context.lang,
            ),
            _headers(self._token, self._token_provider),
            json.dumps(request.to_body(), ensure_ascii=False).encode("utf-8"),
            self._timeout,
        )
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise OriginExportError("SigMA origin export returned invalid JSON") from exc
        error = _backend_error(payload, status_code)
        if error:
            raise OriginExportError(f"SigMA origin export backend error: {error}")
        return payload if isinstance(payload, dict) else {}


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
    for key in ("msg", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"HTTP {status_code}" if status_code >= 400 else "unknown error"


__all__ = [
    "ORIGIN_EXPORT_PATH",
    "OriginExportClient",
    "OriginExportError",
    "OriginExportRequest",
    "OriginExportTransport",
]
