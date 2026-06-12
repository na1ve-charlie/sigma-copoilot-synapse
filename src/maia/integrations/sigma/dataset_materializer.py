"""SigMA SelectionSet dataset materialization."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, ClassVar, TypeAlias

from maia.api import WorkspaceContext
from maia.integrations.sigma.token_provider import SigmaTokenProvider
from maia.selection.service import SelectionSetMaterializer
from maia.selection.sets import SelectionSet


SAVE_DATASET_PATH = "/api/storage/dataGroup/saveDataGroup"
REPLACE_DATASET_RECORDS_PATH = "/api/storage/dataGroup/saveSelectedResult"
_SUCCESS_CODES = frozenset({0, 200, "0", "200"})
DatasetMaterializerTransport: TypeAlias = Callable[
    [str, dict[str, str], bytes | None, float],
    tuple[int, str],
]


class DatasetMaterializerError(RuntimeError):
    __test__: ClassVar[bool] = False


class SigmaSelectionSetMaterializer(SelectionSetMaterializer):
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        token_provider: SigmaTokenProvider | None = None,
        timeout: float = 5.0,
        save_dataset_path: str = SAVE_DATASET_PATH,
        replace_records_path: str = REPLACE_DATASET_RECORDS_PATH,
        transport: DatasetMaterializerTransport | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url:
            raise ValueError("base_url is required")
        self._save_dataset_url = f"{normalized_base_url}{_path(save_dataset_path)}"
        self._replace_records_url = f"{normalized_base_url}{_path(replace_records_path)}"
        self._timeout = timeout
        self._transport = transport or _post_with_urllib
        self._token = None if token is None or not token.strip() else token.strip()
        self._token_provider = token_provider

    async def materialize(
        self,
        selection_set: SelectionSet,
        *,
        workspace_context: WorkspaceContext | None,
    ) -> str | None:
        if not selection_set.record_ids:
            return None
        lang = "zh" if workspace_context is None else workspace_context.lang
        dataset_id = _extract_dataset_id(
            await self._request(
                self._save_dataset_url,
                {"lang": lang, "name": f"maia-{selection_set.selection_set_id}"},
            )
        )
        await self._request(
            self._replace_records_url,
            {
                "lang": lang,
                "dataGroupId": dataset_id,
                "recordIdList": list(selection_set.record_ids),
            },
        )
        return dataset_id

    async def _request(self, url: str, body: dict[str, object]) -> dict[str, object]:
        status_code, text = await asyncio.to_thread(
            self._transport,
            url,
            _headers(self._token, self._token_provider),
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            self._timeout,
        )
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise DatasetMaterializerError("SigMA dataset materializer returned invalid JSON") from exc
        error = _backend_error(payload, status_code)
        if error:
            raise DatasetMaterializerError(f"SigMA dataset materializer backend error: {error}")
        return payload if isinstance(payload, dict) else {}


def _extract_dataset_id(payload: Mapping[str, object]) -> str:
    data = payload.get("data")
    if isinstance(data, Mapping):
        for key in ("dataGroupId", "datasetId", "id"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
    if data not in (None, ""):
        return str(data)
    raise DatasetMaterializerError("SigMA dataset materializer response did not include dataset id")


def _path(value: str) -> str:
    return value if value.startswith("/") else f"/{value}"


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
    "DatasetMaterializerError",
    "DatasetMaterializerTransport",
    "REPLACE_DATASET_RECORDS_PATH",
    "SAVE_DATASET_PATH",
    "SigmaSelectionSetMaterializer",
]
