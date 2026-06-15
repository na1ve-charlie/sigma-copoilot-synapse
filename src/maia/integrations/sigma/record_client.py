"""HTTP adapter for Maia test-record queries against legacy SigMA endpoints."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import ClassVar, TypeAlias

from maia.api import WorkspaceContext
from maia.integrations.sigma.request_mapper import LegacyRecordRequestMapper
from maia.integrations.sigma.records import TestRecordPage
from maia.integrations.sigma.response_mapper import LegacyRecordResponseMapper
from maia.integrations.sigma.token_provider import SigmaTokenProvider
from maia.selection.expression import FilterExpression


LIST_TEST_RECORDS_OPERATION = "list_test_records"
LEGACY_RECORDS_PATH = "/api/storage/singleStationReport/listReportByMulti"
_SUCCESS_CODES = frozenset({0, 200, "0", "200"})
RecordTransport: TypeAlias = Callable[
    [str, dict[str, object], dict[str, str], float],
    tuple[int, str],
]


class TestRecordClientError(RuntimeError):
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        path: str,
        request_params: Mapping[str, object],
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.path = path
        self.request_params = dict(request_params)
        self.status_code = status_code


class TestRecordClient:
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        token_provider: SigmaTokenProvider | None = None,
        timeout: float = 5.0,
        endpoint_path: str = LEGACY_RECORDS_PATH,
        transport: RecordTransport | None = None,
        request_mapper: LegacyRecordRequestMapper | None = None,
        response_mapper: LegacyRecordResponseMapper | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url:
            raise ValueError("base_url is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        self._path = _normalize_path(endpoint_path)
        self._url = f"{normalized_base_url}{self._path}"
        self._timeout = timeout
        self._token = _optional_token(token)
        self._token_provider = token_provider
        self._transport = transport or _fetch_with_urllib
        self._request_mapper = request_mapper or LegacyRecordRequestMapper()
        self._response_mapper = response_mapper or LegacyRecordResponseMapper()

    async def list_records(
        self,
        expression: FilterExpression | dict[str, object] | None,
        *,
        workspace_context: WorkspaceContext | None,
        page: int | None = None,
        rows: int | None = None,
    ) -> TestRecordPage:
        request = self._request_mapper.map(
            expression,
            workspace_context=workspace_context,
            page=page,
            rows=rows,
        )
        request_params = request.to_http_params()
        print(request_params)
        payload, status_code = await self._request_payload(request_params)
        try:
            return self._response_mapper.map(payload)
        except ValueError as exc:
            raise TestRecordClientError(
                _status_message(
                    f"SigMA list_test_records response mapping failed: {exc}",
                    status_code,
                ),
                operation=LIST_TEST_RECORDS_OPERATION,
                path=self._path,
                request_params=request_params,
                status_code=status_code,
            ) from exc

    async def _request_payload(
        self,
        request_params: dict[str, object],
        *,
        allow_refresh: bool = True,
    ) -> tuple[dict[str, object], int]:
        try:
            status_code, body = await asyncio.to_thread(
                self._transport,
                self._url,
                request_params,
                self._headers(),
                self._timeout,
            )
        except Exception as exc:
            raise TestRecordClientError(
                "SigMA list_test_records request failed",
                operation=LIST_TEST_RECORDS_OPERATION,
                path=self._path,
                request_params=request_params,
            ) from exc

        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise TestRecordClientError(
                _status_message(
                    "SigMA list_test_records returned invalid JSON",
                    status_code,
                ),
                operation=LIST_TEST_RECORDS_OPERATION,
                path=self._path,
                request_params=request_params,
                status_code=status_code,
            ) from exc

        refreshed_token = _refresh_token(payload)
        if allow_refresh and refreshed_token:
            if self._token_provider is None:
                self._token = refreshed_token
            else:
                self._token_provider.set(refreshed_token)
            return await self._request_payload(request_params, allow_refresh=False)

        backend_error = _backend_error(payload, status_code)
        if backend_error:
            raise TestRecordClientError(
                _status_message(
                    f"SigMA list_test_records backend error: {backend_error}",
                    status_code,
                ),
                operation=LIST_TEST_RECORDS_OPERATION,
                path=self._path,
                request_params=request_params,
                status_code=status_code,
            )

        return payload, status_code

    def _headers(self) -> dict[str, str]:
        token = self._token if self._token_provider is None else self._token_provider.get()
        return {"Token": token} if token else {}


def _fetch_with_urllib(
    url: str,
    params: dict[str, object],
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, str]:
    query = urllib.parse.urlencode(params, doseq=True)
    request_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(
        request_url,
        method="GET",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _normalize_path(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("endpoint_path is required")
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _optional_token(value: str | None) -> str | None:
    if value is None:
        return None
    token = value.strip()
    return token or None


def _status_message(message: str, status_code: int) -> str:
    return f"{message} (HTTP {status_code})" if status_code >= 400 else message


def _refresh_token(payload: object) -> str | None:
    if not isinstance(payload, Mapping) or payload.get("code") != 1001:
        return None
    return _optional_token(payload.get("data"))


def _backend_error(payload: object, status_code: int) -> str | None:
    if not isinstance(payload, Mapping):
        return None if status_code < 400 else "unknown error"

    code = payload.get("code")
    if status_code < 400 and code in _SUCCESS_CODES:
        return None

    parts = [_optional_token(payload.get("msg")), _optional_token(payload.get("message"))]
    data = payload.get("data")
    if isinstance(data, Mapping):
        parts.extend(
            [
                _optional_token(data.get("content")),
                _optional_token(data.get("operation")),
                _optional_token(data.get("name")),
                _optional_token(data.get("error")),
                _optional_token(data.get("message")),
            ]
        )

    messages = [part for part in parts if part]
    if messages:
        return " / ".join(dict.fromkeys(messages))
    if code not in (None, *tuple(_SUCCESS_CODES)):
        return f"code {code}"
    return "unknown error" if status_code >= 400 else None


__all__ = [
    "LEGACY_RECORDS_PATH",
    "LIST_TEST_RECORDS_OPERATION",
    "RecordTransport",
    "TestRecordClient",
    "TestRecordClientError",
]
