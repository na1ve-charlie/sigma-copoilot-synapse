"""HTTP SigMA gateway implementation."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from synapse.integrations.sigma.contracts import (
    SigmaCandidate,
    SigmaGatewayError,
    SigmaObservationAvailabilityRow,
    SigmaQuery,
)
from synapse.turns import WorkspaceContext


DEFAULT_RESOLVER_CONFIG = Path("configs/resolvers/nvh.yaml")
DOMAIN_ENDPOINTS = (
    "ONE_D",
    "TWO_D_TD",
    "TWO_D_FS",
    "TWO_D_OS",
    "TWO_D_OC",
    "TWO_D_CEP",
    "TWO_D_PS",
)


@dataclass(frozen=True, slots=True)
class SigmaConfig:
    base_url: str
    default_lang: str = "zh"
    timeout: float = 5.0
    token: str | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SigmaConfig":
        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, Mapping) or not isinstance(
            loaded.get("sigma"),
            Mapping,
        ):
            raise ValueError("configs/copilot.yaml must define a sigma mapping")
        return cls.from_mapping(loaded["sigma"])

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SigmaConfig":
        base_url = str(value.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("sigma.base_url is required")
        return cls(
            base_url=base_url,
            default_lang=str(value.get("default_lang", "zh")),
            timeout=float(value.get("timeout", 5.0)),
            token=_optional_text(value.get("token")),
        )


class HttpSigmaGateway:
    def __init__(
        self,
        config: SigmaConfig,
        *,
        availability: Mapping[str, Any],
        indicators: Mapping[str, Any],
    ) -> None:
        self._config = config
        self._token = config.token
        self._availability = availability
        self._indicators = indicators

    @classmethod
    def from_yaml(
        cls,
        *,
        config_path: str | Path = "configs/copilot.yaml",
        resolver_path: str | Path = DEFAULT_RESOLVER_CONFIG,
    ) -> "HttpSigmaGateway":
        resolvers = _mapping(
            (yaml.safe_load(Path(resolver_path).read_text(encoding="utf-8")) or {}).get(
                "resolvers"
            )
        )
        return cls(
            SigmaConfig.from_yaml(config_path),
            availability=_mapping(resolvers.get("observation_availability")),
            indicators=_mapping(resolvers.get("observation_indicators")),
        )

    async def list_sensors(self, query: SigmaQuery) -> tuple[SigmaCandidate, ...]:
        rows = self._availability_rows(query, "list_sensors")
        return tuple(
            SigmaCandidate(value)
            for value in _dedupe(row["sensor"] for row in rows)
        )

    async def list_observation_availability(
        self,
        query: SigmaQuery,
    ) -> tuple[SigmaObservationAvailabilityRow, ...]:
        return tuple(
            SigmaObservationAvailabilityRow(
                domain=row["domain"],
                sensor=row["sensor"],
                test_segment=row["test_segment"],
            )
            for row in self._availability_rows(query, "list_observation_availability")
        )

    async def list_test_segments(self, query: SigmaQuery) -> tuple[SigmaCandidate, ...]:
        rows = self._availability_rows(query, "list_test_segments")
        return tuple(
            SigmaCandidate(value) for value in _dedupe(row["test_segment"] for row in rows)
        )

    async def list_indicator_names(self, query: SigmaQuery) -> tuple[SigmaCandidate, ...]:
        rows = self._availability_rows(query, "list_indicator_names")
        type_systems = _type_systems(query.workspace_context)
        if not rows or not type_systems:
            return ()

        candidates: list[SigmaCandidate] = []
        for domain in DOMAIN_ENDPOINTS:
            scoped = [row for row in rows if row["domain"] == domain]
            if not scoped:
                continue
            candidates.extend(
                _with_data_type_metadata(
                    await self.list_observation_indicator_names(
                        query,
                        domain=domain,
                        sensors=tuple(_dedupe(row["sensor"] for row in scoped)),
                        test_segments=tuple(
                            _dedupe(row["test_segment"] for row in scoped)
                        ),
                    ),
                    domain,
                )
            )
        return tuple(_dedupe_candidates(candidates))

    async def list_observation_indicator_names(
        self,
        query: SigmaQuery,
        *,
        domain: str,
        sensors: tuple[str, ...],
        test_segments: tuple[str, ...],
    ) -> tuple[SigmaCandidate, ...]:
        type_systems = _type_systems(query.workspace_context)
        if not sensors or not test_segments or not type_systems:
            return ()
        endpoint = _indicator_endpoint(self._indicators, domain)
        if not endpoint:
            return ()
        body: dict[str, Any] = {
            "sensorList": list(sensors),
            "testNameList": list(test_segments),
            "typeSystemVOList": type_systems,
        }
        if endpoint.get("include_data_type"):
            body["dataType"] = domain
        payload = self._json_request(
            endpoint,
            query,
            "list_observation_indicator_names",
            body=body,
        )
        return tuple(_dedupe_candidates(_indicator_candidates(payload, endpoint)))

    def domains_for_action(self, action_name: str) -> tuple[str, ...]:
        return tuple(
            _domains_for_action(
                (self._availability, self._indicators),
                action_name,
            )
        )

    def _availability_rows(
        self,
        query: SigmaQuery,
        operation: str,
    ) -> list[dict[str, str]]:
        if query.workspace_context is None or not query.workspace_context.dataset_id:
            return []
        payload = self._json_request(self._availability, query, operation)
        data = _path_get(payload, "data")
        if not isinstance(data, Mapping):
            return []
        rows: list[dict[str, str]] = []
        for domain, sensors in data.items():
            if not isinstance(sensors, Mapping):
                continue
            for sensor, segments in sensors.items():
                if isinstance(segments, Sequence) and not isinstance(segments, str):
                    rows.extend(
                        {
                            "domain": str(domain),
                            "sensor": str(sensor),
                            "test_segment": str(segment),
                        }
                        for segment in segments
                    )
        return rows

    def _json_request(
        self,
        source: Mapping[str, Any],
        query: SigmaQuery,
        operation: str,
        *,
        body: Any = None,
        allow_refresh: bool = True,
    ) -> Any:
        url = _url(self._config, source)
        params = _params(self._config, source, query.workspace_context)
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        headers = {"Token": self._token} if self._token else {}
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=data,
            method=str(source.get("method", "GET")).upper(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except Exception as exc:
            raise SigmaGatewayError(
                f"SigMA {operation} request failed",
                operation=operation,
                query=query,
                cause=exc,
            ) from exc
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise SigmaGatewayError(
                f"SigMA {operation} returned invalid JSON",
                operation=operation,
                query=query,
                cause=exc,
            ) from exc
        refreshed = _refresh_token(payload)
        if allow_refresh and refreshed:
            self._token = refreshed
            return self._json_request(
                source,
                query,
                operation,
                body=body,
                allow_refresh=False,
            )
        _raise_backend_error(payload, operation, query)
        return payload


def _indicator_endpoint(source: Mapping[str, Any], domain: str) -> Mapping[str, Any]:
    return _mapping(_mapping(source.get("endpoints_by_domain")).get(domain)) or _mapping(
        source.get("default_endpoint")
    )


def _domains_for_action(
    sources: Sequence[Mapping[str, Any]],
    action_name: str,
) -> list[str]:
    for source in sources:
        values = _as_string_list(_mapping(source.get("domain_by_action")).get(action_name))
        if values:
            return values
    return []


def _indicator_candidates(
    payload: Any,
    endpoint: Mapping[str, Any],
) -> list[SigmaCandidate]:
    response = _mapping(endpoint.get("response"))
    items = _items(payload, _optional_text(response.get("items_path")))
    value_key = _optional_text(response.get("value_key")) or "value"
    label_key = _optional_text(response.get("label_field"))
    result = []
    iterable: Iterable[Any]
    if isinstance(items, Mapping):
        iterable = items.values()
    elif isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
        iterable = items
    elif items is None:
        iterable = ()
    else:
        iterable = (items,)
    for item in iterable:
        if isinstance(item, Mapping):
            value = item.get(value_key) or item.get("name") or item.get("value")
            label = item.get(label_key) if label_key else None
        else:
            value, label = item, None
        if value is not None:
            index = str(label) if label not in (None, "") else None
            result.append(
                SigmaCandidate(
                    str(value),
                    label=index,
                    metadata={"index": index} if index else {},
                )
            )
    return result


def _type_systems(context: WorkspaceContext | None) -> list[dict[str, str]]:
    if context is None:
        return []
    if context.type_systems:
        return [item.to_backend() for item in context.type_systems]
    return [
        {
            "type": f"{item.product_type}_{item.product_version}",
            "systemNo": item.system_no,
        }
        for item in context.products
    ]


def _url(config: SigmaConfig, source: Mapping[str, Any]) -> str:
    path = str(source.get("url") or source.get("path") or "")
    return (
        path
        if path.startswith(("http://", "https://"))
        else f"{config.base_url}{path}"
    )


def _params(
    config: SigmaConfig,
    source: Mapping[str, Any],
    context: WorkspaceContext | None,
) -> dict[str, Any]:
    payload = context.model_dump(mode="json") if context else {}
    params = {
        str(key): config.default_lang if value == "{default_lang}" else value
        for key, value in _mapping(source.get("params")).items()
    }
    for key, context_key in _mapping(source.get("query_from_context")).items():
        value = payload.get(str(context_key))
        if value not in (None, "", []):
            params[str(key)] = value
    return params


def _items(payload: Any, path: str | None) -> Any:
    value = _path_get(payload, path or "data")
    return value if value is not None else []


def _path_get(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _dedupe(values) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value is not None))


def _dedupe_candidates(values: Sequence[SigmaCandidate]) -> list[SigmaCandidate]:
    merged: dict[str, SigmaCandidate] = {}
    for item in values:
        existing = merged.get(item.value)
        if existing is None:
            merged[item.value] = item
            continue
        merged[item.value] = SigmaCandidate(
            value=item.value,
            label=item.label or existing.label,
            metadata=_merge_candidate_metadata(existing, item),
        )
    return list(merged.values())


def _with_data_type_metadata(
    values: Sequence[SigmaCandidate],
    data_type: str,
) -> list[SigmaCandidate]:
    result = []
    for item in values:
        data_types = tuple(
            dict.fromkeys(_as_string_list(item.metadata.get("data_types")) + [data_type])
        )
        metadata = {**item.metadata, "data_types": data_types}
        indexes = _candidate_indexes(item)
        if indexes:
            metadata["indexes_by_data_type"] = {data_type: indexes[0]}
        if len(indexes) > 1:
            metadata["index_conflicts_by_data_type"] = {
                data_type: tuple(indexes)
            }
        result.append(
            SigmaCandidate(
                value=item.value,
                label=item.label,
                metadata=metadata,
            )
        )
    return result


def _merge_candidate_metadata(
    existing: SigmaCandidate,
    item: SigmaCandidate,
) -> dict[str, Any]:
    metadata = {**existing.metadata, **item.metadata}
    metadata["data_types"] = tuple(
        dict.fromkeys(
            _as_string_list(existing.metadata.get("data_types"))
            + _as_string_list(item.metadata.get("data_types"))
        )
    )

    indexes = _candidate_indexes(existing) + _candidate_indexes(item)
    indexes = list(dict.fromkeys(indexes))
    if indexes:
        metadata["index"] = indexes[0]
    if len(indexes) > 1:
        metadata["index_conflicts"] = tuple(indexes)

    by_data_type: dict[str, list[str]] = {}
    for candidate in (existing, item):
        for domain, index in _mapping(
            candidate.metadata.get("indexes_by_data_type")
        ).items():
            by_data_type.setdefault(str(domain), []).extend(_as_string_list(index))
        for domain, conflicts in _mapping(
            candidate.metadata.get("index_conflicts_by_data_type")
        ).items():
            by_data_type.setdefault(str(domain), []).extend(
                _as_string_list(conflicts)
            )

    normalized = {
        domain: list(dict.fromkeys(values))
        for domain, values in by_data_type.items()
        if values
    }
    if normalized:
        metadata["indexes_by_data_type"] = {
            domain: values[0] for domain, values in normalized.items()
        }
    conflicts = {
        domain: tuple(values)
        for domain, values in normalized.items()
        if len(values) > 1
    }
    if conflicts:
        metadata["index_conflicts_by_data_type"] = conflicts
    return metadata


def _candidate_indexes(candidate: SigmaCandidate) -> list[str]:
    values = _as_string_list(candidate.metadata.get("index_conflicts"))
    values.extend(_as_string_list(candidate.metadata.get("index")))
    if not values and candidate.label:
        values.append(candidate.label)
    return list(dict.fromkeys(values))


def _refresh_token(payload: Any) -> str | None:
    if not isinstance(payload, Mapping) or payload.get("code") != 1001:
        return None
    return _optional_text(payload.get("data"))


def _raise_backend_error(payload: Any, operation: str, query: SigmaQuery) -> None:
    if not isinstance(payload, Mapping):
        return
    code = payload.get("code")
    if code in (None, 0, 200):
        return
    message = payload.get("msg") or f"SigMA returned code {code}"
    raise SigmaGatewayError(str(message), operation=operation, query=query)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value]
    return []
