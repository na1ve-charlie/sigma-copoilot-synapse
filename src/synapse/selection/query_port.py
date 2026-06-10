"""Selection query port — abstract interface for materialising selections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from synapse.selection.models import RecordQuery, SelectionScope


# ---------------------------------------------------------------------------
# SelectionMaterialization
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectionMaterialization:
    """The result of executing a ``RecordQuery`` against a backend."""

    record_count: int
    snapshot_version: str
    content_hash: str
    backend_ref: str | None = None


# ---------------------------------------------------------------------------
# SelectionQueryPort (protocol)
# ---------------------------------------------------------------------------


class SelectionQueryPort(Protocol):
    """Abstract port for materialising a ``RecordQuery``.

    Implementations may contact SigMA, an in-memory stub, or any
    other backend.
    """

    def materialize(
        self,
        query: RecordQuery,
        scope: SelectionScope,
    ) -> SelectionMaterialization:
        ...


# ---------------------------------------------------------------------------
# StaticSelectionQueryPort
# ---------------------------------------------------------------------------


class StaticSelectionQueryPort:
    """In-memory query port that always returns the same result.

    Every call records the received *query* and *scope* into
    ``last_query`` / ``last_scope`` for test assertions.
    """

    def __init__(self, materialization: SelectionMaterialization) -> None:
        self._materialization = materialization
        self.last_query: RecordQuery | None = None
        self.last_scope: SelectionScope | None = None

    def materialize(
        self,
        query: RecordQuery,
        scope: SelectionScope,
    ) -> SelectionMaterialization:
        self.last_query = query
        self.last_scope = scope
        return self._materialization
