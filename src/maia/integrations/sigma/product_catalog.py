"""SigMA product configuration lookup for Maia record filters."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, ClassVar, TypeAlias

from pydantic import BaseModel, ConfigDict

from maia.integrations.sigma.token_provider import SigmaTokenProvider


LIST_PRODUCT_CONFIGS_OPERATION = "list_product_configs"
LIST_PRODUCT_SYSTEMS_OPERATION = "list_product_systems"
LIST_PRODUCT_VERSIONS_OPERATION = "list_product_versions"
PRODUCT_CONFIGS_PATH = "/api/storage/type"
PRODUCT_SYSTEMS_PATH = "/api/storage/type/listSystemNos"
PRODUCT_VERSIONS_PATH = "/api/storage/type/listVersions"
_SUCCESS_CODES = frozenset({0, 200, "0", "200"})
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
ProductCatalogTransport: TypeAlias = Callable[
    [str, dict[str, object], dict[str, str], float],
    tuple[int, str],
]


class ProductCatalogError(RuntimeError):
    __test__: ClassVar[bool] = False


class ProductConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_type: str
    config_version: str
    type_system: str
    update_time: datetime | None = None
    version_name: str | None = None


class SigmaProductCatalogClient:
    __test__: ClassVar[bool] = False

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        token_provider: SigmaTokenProvider | None = None,
        timeout: float = 5.0,
        endpoint_path: str = PRODUCT_CONFIGS_PATH,
        systems_path: str = PRODUCT_SYSTEMS_PATH,
        versions_path: str = PRODUCT_VERSIONS_PATH,
        transport: ProductCatalogTransport | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url:
            raise ValueError("base_url is required")
        self._path = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
        self._systems_path = systems_path if systems_path.startswith("/") else f"/{systems_path}"
        self._versions_path = versions_path if versions_path.startswith("/") else f"/{versions_path}"
        self._url = f"{normalized_base_url}{self._path}"
        self._systems_url = f"{normalized_base_url}{self._systems_path}"
        self._versions_url = f"{normalized_base_url}{self._versions_path}"
        self._timeout = timeout
        self._token = None if token is None or not token.strip() else token.strip()
        self._token_provider = token_provider
        self._transport = transport or _fetch_with_urllib

    async def list_configs(self, *, lang: str = "zh") -> tuple[ProductConfig, ...]:
        params = {"page": 1, "rows": 99999, "lang": lang}
        payload = await self._request_payload(
            self._url,
            params,
            operation=LIST_PRODUCT_CONFIGS_OPERATION,
        )
        rows = payload.get("data", {}).get("rows", ())
        if not isinstance(rows, list):
            return ()
        return tuple(
            sorted(
                (_product_config(row) for row in rows if isinstance(row, Mapping)),
                key=_sort_key,
            )
        )

    async def list_versions(self, product_type: str, *, lang: str = "zh") -> tuple[str, ...]:
        normalized_type = product_type.strip()
        if not normalized_type:
            return ()
        payload = await self._request_payload(
            self._versions_url,
            {"typeList": normalized_type, "lang": lang},
            operation=LIST_PRODUCT_VERSIONS_OPERATION,
        )
        data = payload.get("data")
        if not isinstance(data, list):
            return ()
        return _distinct(_coerce_text(value) for value in data)

    async def list_systems(self, product_type: str, *, lang: str = "zh") -> tuple[str, ...]:
        normalized_type = product_type.strip()
        if not normalized_type:
            return ()
        payload = await self._request_payload(
            self._systems_url,
            {"typeList": normalized_type, "lang": lang},
            operation=LIST_PRODUCT_SYSTEMS_OPERATION,
        )
        data = payload.get("data")
        if not isinstance(data, list):
            return ()
        return _distinct(_coerce_text(value) for value in data)

    async def _request_payload(
        self,
        url: str,
        params: dict[str, object],
        *,
        operation: str,
    ) -> dict[str, object]:
        status_code, body = await asyncio.to_thread(
            self._transport,
            url,
            params,
            _headers(self._token, self._token_provider),
            self._timeout,
        )
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise ProductCatalogError(
                f"SigMA {operation} returned invalid JSON"
            ) from exc
        error = _backend_error(payload, status_code)
        if error:
            raise ProductCatalogError(
                f"SigMA {operation} backend error: {error}"
            )
        return payload if isinstance(payload, dict) else {}


def _product_config(row: Mapping[str, Any]) -> ProductConfig:
    product_type = _coerce_text(row.get("type")) or _coerce_text(row.get("name"))
    config_version = _coerce_text(row.get("version"))
    if config_version is None:
        config_version = _coerce_text(row.get("versionName"))
    type_system = _coerce_text(row.get("systemNo"))
    if not product_type or not config_version or not type_system:
        raise ProductCatalogError("SigMA product config rows must include type, version, and systemNo")
    return ProductConfig(
        product_type=product_type,
        config_version=config_version,
        type_system=type_system,
        update_time=_parse_time(row.get("updateTime")),
        version_name=_optional_text(row.get("versionName")),
    )


def _sort_key(item: ProductConfig) -> tuple[float, str, str, str]:
    timestamp = 0.0 if item.update_time is None else -item.update_time.timestamp()
    return (timestamp, item.product_type, item.config_version, item.type_system)


def _parse_time(value: Any) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    return datetime.strptime(text, _TIME_FORMAT)


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _distinct(values: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


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


def _headers(
    token: str | None,
    token_provider: SigmaTokenProvider | None,
) -> dict[str, str]:
    current = token if token_provider is None else token_provider.get()
    return {"Token": current} if current else {}


def _backend_error(payload: object, status_code: int) -> str | None:
    if not isinstance(payload, Mapping):
        return None if status_code < 400 else "unknown error"
    if status_code < 400 and payload.get("code") in _SUCCESS_CODES:
        return None
    for key in ("msg", "message"):
        value = _optional_text(payload.get(key))
        if value is not None:
            return value
    return f"HTTP {status_code}" if status_code >= 400 else "unknown error"


__all__ = [
    "LIST_PRODUCT_CONFIGS_OPERATION",
    "LIST_PRODUCT_SYSTEMS_OPERATION",
    "LIST_PRODUCT_VERSIONS_OPERATION",
    "PRODUCT_CONFIGS_PATH",
    "PRODUCT_SYSTEMS_PATH",
    "PRODUCT_VERSIONS_PATH",
    "ProductCatalogError",
    "ProductConfig",
    "ProductCatalogTransport",
    "SigmaProductCatalogClient",
]
