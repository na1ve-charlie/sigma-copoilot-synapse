"""Shared SigMA token provider for runtime hot updates."""

from __future__ import annotations

from typing import Protocol


class SigmaTokenProvider(Protocol):
    def get(self) -> str | None: ...
    def set(self, token: str | None) -> None: ...


class MutableSigmaTokenProvider:
    def __init__(self, token: str | None = None) -> None:
        self._token = _normalize(token)

    def get(self) -> str | None:
        return self._token

    def set(self, token: str | None) -> None:
        self._token = _normalize(token)


def _normalize(token: str | None) -> str | None:
    if token is None:
        return None
    value = token.strip()
    return value or None


__all__ = ["MutableSigmaTokenProvider", "SigmaTokenProvider"]
