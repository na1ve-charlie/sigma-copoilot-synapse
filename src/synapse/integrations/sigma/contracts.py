"""Contracts for Synapse interaction with the SigMA business system."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from synapse.turns import TurnRequest, WorkspaceContext


@dataclass(frozen=True, slots=True)
class SigmaCandidate:
    """A candidate value loaded from SigMA for domain slot resolution."""

    value: str
    label: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SigmaObservationAvailabilityRow:
    domain: str
    sensor: str
    test_segment: str


@dataclass(frozen=True, slots=True)
class SigmaQuery:
    """Business scope required to query SigMA safely."""

    workspace_context: WorkspaceContext | None = None
    session_id: str | None = None

    @classmethod
    def from_turn(cls, request: TurnRequest) -> "SigmaQuery":
        return cls(
            workspace_context=request.workspace_context,
            session_id=request.session_id,
        )


class SigmaGatewayError(RuntimeError):
    """Raised when SigMA access fails while preserving request context."""

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        query: SigmaQuery,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.query = query
        if cause is not None:
            self.__cause__ = cause


@runtime_checkable
class SigmaGateway(Protocol):
    """Protocol implemented by SigMA adapters used by Synapse domains."""

    async def list_sensors(self, query: SigmaQuery) -> tuple[SigmaCandidate, ...]:
        ...

    async def list_test_segments(
        self,
        query: SigmaQuery,
    ) -> tuple[SigmaCandidate, ...]:
        ...

    async def list_indicator_names(
        self,
        query: SigmaQuery,
    ) -> tuple[SigmaCandidate, ...]:
        ...
