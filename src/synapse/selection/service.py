"""SelectionService — creates, reads and refreshes immutable SelectionSets."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from synapse.selection.models import SelectionSet
from synapse.selection.query_port import SelectionQueryPort
from synapse.selection.repository import SelectionRepository


# ---------------------------------------------------------------------------
# Injected dependencies (Protocols defined here)
# ---------------------------------------------------------------------------


class Clock(Protocol):
    def now(self) -> datetime:
        ...


class SelectionIdGenerator(Protocol):
    def new_id(self) -> str:
        ...


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class SelectionNotFoundError(Exception):
    """The requested *selection_id* does not exist in the repository."""


class SelectionExpiredError(Exception):
    """The requested selection has expired and should not be used."""


# ---------------------------------------------------------------------------
# SelectionService
# ---------------------------------------------------------------------------


class SelectionService:
    """Manages the lifecycle of ``SelectionSet`` entities.

    Parameters
    ----------
    query_port:
        Port for materialising queries against a backend.
    repository:
        Port for persisting and retrieving ``SelectionSet`` entities.
    clock:
        Provides the current time.
    id_generator:
        Generates unique selection IDs.
    """

    def __init__(
        self,
        query_port: SelectionQueryPort,
        repository: SelectionRepository,
        clock: Clock,
        id_generator: SelectionIdGenerator,
    ) -> None:
        self._query_port = query_port
        self._repository = repository
        self._clock = clock
        self._id_generator = id_generator

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    def create(
        self,
        query: RecordQuery,
        scope: SelectionScope,
        *,
        derived_from: str | None = None,
        expires_at: datetime | None = None,
    ) -> SelectionSet:
        """Materialise a *query*, persist a new ``SelectionSet``, and return it.

        The ``content_hash`` on the resulting ``SelectionSet`` comes from
        the query port's materialization, **not** from ``query_hash(query)``.
        """
        mat = self._query_port.materialize(query, scope)
        selection_id = self._id_generator.new_id()
        created_at = self._clock.now()

        sel = SelectionSet(
            id=selection_id,
            query=query,
            scope=scope,
            backend_ref=mat.backend_ref,
            record_count=mat.record_count,
            snapshot_version=mat.snapshot_version,
            content_hash=mat.content_hash,
            created_at=created_at,
            expires_at=expires_at,
            derived_from=derived_from,
            supersedes=None,
        )
        self._repository.save(sel)
        return sel

    # ------------------------------------------------------------------
    # get
    # ------------------------------------------------------------------

    def get(self, selection_id: str) -> SelectionSet:
        """Retrieve an existing ``SelectionSet`` by ID.

        Raises ``SelectionNotFoundError`` if no selection with *id* exists,
        and ``SelectionExpiredError`` if the selection has expired.
        """
        sel = self._repository.get(selection_id)
        if sel is None:
            raise SelectionNotFoundError(
                f"SelectionSet not found: {selection_id!r}"
            )
        if self.is_expired(sel):
            raise SelectionExpiredError(
                f"SelectionSet {selection_id!r} has expired"
            )
        return sel

    # ------------------------------------------------------------------
    # refresh
    # ------------------------------------------------------------------

    def refresh(
        self,
        selection_id: str,
        *,
        expires_at: datetime | None = None,
    ) -> SelectionSet:
        """Re-materialise an existing selection's query and create a new
        ``SelectionSet`` that supersedes it.

        The old selection is **not** modified or deleted — it remains
        accessible via ``get``.

        ``refresh`` does **not** raise ``SelectionExpiredError`` even
        when the old selection has expired."
        """
        old = self._repository.get(selection_id)
        if old is None:
            raise SelectionNotFoundError(
                f"SelectionSet not found: {selection_id!r}"
            )

        # Re-materialize using the old query and scope
        mat = self._query_port.materialize(old.query, old.scope)
        new_id = self._id_generator.new_id()
        created_at = self._clock.now()

        sel = SelectionSet(
            id=new_id,
            query=old.query,
            scope=old.scope,
            backend_ref=mat.backend_ref,
            record_count=mat.record_count,
            snapshot_version=mat.snapshot_version,
            content_hash=mat.content_hash,
            created_at=created_at,
            expires_at=expires_at,
            derived_from=old.derived_from,
            supersedes=old.id,
        )
        self._repository.save(sel)
        return sel

    # ------------------------------------------------------------------
    # is_expired
    # ------------------------------------------------------------------

    def is_expired(self, selection: SelectionSet, *, now: datetime | None = None) -> bool:
        """Check whether *selection* has expired.

        Delegates to ``SelectionSet.is_expired`` using *now* or the
        injected ``clock.now()``.
        """
        ref = now if now is not None else self._clock.now()
        return selection.is_expired(now=ref)
