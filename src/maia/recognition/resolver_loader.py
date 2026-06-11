"""Build CLI resolvers from local YAML using Themis public resolver helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from themis import ResolverProvider, build_resolver


def load_cli_resolver(path: str | Path) -> ResolverProvider:
    resolver_path = Path(path).resolve()
    payload = _load_mapping(resolver_path)
    config = _normalize_resolver_config(payload)
    resolver = build_resolver(config)
    if resolver is None:
        raise ValueError(
            "resolver config must define enum values or a Themis resolver config"
        )
    return resolver


def _load_mapping(path: Path) -> Mapping[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, Mapping):
        raise TypeError(f"resolver config must be a mapping: {path}")
    return loaded


def _normalize_resolver_config(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    if any(key in payload for key in ("http", "enum", "enums", "merge")):
        return payload

    values = payload.get("values")
    if isinstance(values, Mapping):
        return {
            "enum": {
                "case_sensitive": False,
                "values": values,
            }
        }
    if _looks_like_enum_values(payload):
        return {
            "enum": {
                "case_sensitive": False,
                "values": payload,
            }
        }
    raise ValueError(
        "resolver config must define enum values or a Themis resolver config"
    )


def _looks_like_enum_values(payload: Mapping[str, Any]) -> bool:
    if not payload:
        return False
    for values in payload.values():
        if isinstance(values, (str, bytes)):
            return False
        if not isinstance(values, Sequence):
            return False
    return True
