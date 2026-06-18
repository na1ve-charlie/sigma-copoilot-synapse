"""SigMA test-record management adapter."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import ClassVar, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from maia.api import WorkspaceContext
from maia.integrations.sigma.token_provider import SigmaTokenProvider


TEST_RECORD_MANAGEMENT_PATH = "/api/storage/dataFile/exportData"
DEFAULT_BACKUP_PATH = "D:/数据备份/"
_SUCCESS_CODES = frozenset({0, 200, "0", "200"})
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
TestRecordManagementTransport: TypeAlias = Callable[
    [str, dict[str, str], bytes | None, float],
    tuple[int, str],
]


class TestRecordManagementRequest(BaseModel):
    __test__: ClassVar[bool] = False

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    result_id_list: tuple[int, ...] = Field(serialization_alias="resultIdList")
    color_map: bool = Field(serialization_alias="colorMap")
    origin_data: bool = Field(serialization_alias="originData")
    result_data: bool = Field(serialization_alias="resultData")
    data_export_type: int = Field(serialization_alias="dataExportType")
    file_path: str = Field(default=DEFAULT_BACKUP_PATH, serialization_alias="filePath")
    file_name: str | None = Field(default=None, serialization_alias="fileName")

    @field_validator("result_id_list")
    @classmethod
    def _require_ids(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("resultIdList must not be empty")
        if any(item < 1 for item in value):
            raise ValueError("resultIdList values must be positive")
        return value

    @field_validator("file_path")
    @classmethod
    def _require_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("filePath must not be blank")
        return value

    @field_validator("data_export_type")
    @classmethod
    def _validate_export_type(cls, value: int) -> int:
        if value not in {1, 2, 3}:
            raise ValueError("dataExportType must be 1, 2, or 3")
        return value

    @model_validator(mode="after")
    def _validate_payload(self) -> "TestRecordManagementRequest":
        if not (self.color_map or self.origin_data or self.result_data):
            raise ValueError("at least one data type must be selected")
        if self.data_export_type in {2, 3} and not _valid_file_name(self.file_name):
            raise ValueError("fileName is required and must be a valid Windows file name")
        if self.data_export_type == 1 and self.file_name is not None:
            raise ValueError("fileName is only supported for backup operations")
        return self

    def to_body(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class TestRecordManagementError(RuntimeError):
    __test__: ClassVar[bool] = False


class TestRecordManagementClient:
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        token_provider: SigmaTokenProvider | None = None,
        timeout: float = 5.0,
        endpoint_path: str = TEST_RECORD_MANAGEMENT_PATH,
        transport: TestRecordManagementTransport | None = None,
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

    async def submit(
        self,
        request: TestRecordManagementRequest,
        *,
        workspace_context: WorkspaceContext | None,
    ) -> dict[str, object]:
        lang = "zh" if workspace_context is None else workspace_context.lang
        status_code, text = await asyncio.to_thread(
            self._transport,
            _url_with_lang(self._url, lang),
            _headers(self._token, self._token_provider),
            json.dumps(request.to_body(), ensure_ascii=False).encode("utf-8"),
            self._timeout,
        )
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise TestRecordManagementError("SigMA test record management returned invalid JSON") from exc
        error = _backend_error(payload, status_code)
        if error:
            raise TestRecordManagementError(f"SigMA test record management backend error: {error}")
        return payload if isinstance(payload, dict) else {}


def _valid_file_name(value: str | None) -> bool:
    if value is None:
        return False
    if not value.strip() or value.endswith((" ", ".")):
        return False
    text = value.strip()
    stem = text.split(".", 1)[0].upper()
    return stem not in _WINDOWS_RESERVED_NAMES and not any(
        char in '<>:"/\\|?*' or ord(char) < 32
        for char in text
    )


def _path(value: str) -> str:
    return value if value.startswith("/") else f"/{value}"


def _url_with_lang(url: str, lang: str) -> str:
    if "lang" in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query):
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}lang={urllib.parse.quote(lang)}"


def _headers(token: str | None, token_provider: SigmaTokenProvider | None) -> dict[str, str]:
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
    "DEFAULT_BACKUP_PATH",
    "TEST_RECORD_MANAGEMENT_PATH",
    "TestRecordManagementClient",
    "TestRecordManagementError",
    "TestRecordManagementRequest",
    "TestRecordManagementTransport",
]
