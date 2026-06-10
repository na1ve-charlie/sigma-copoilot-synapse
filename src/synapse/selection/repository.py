"""Selection repository — stores and retrieves ``SelectionSet`` entities."""

from __future__ import annotations

from typing import Protocol

from synapse.selection.models import SelectionSet


class SelectionRepository(Protocol):
    """Abstract repository for ``SelectionSet`` persistence.

    ``get`` returns ``None`` when the selection is not found.
    ``save`` raises if a selection with the same *id* already exists.
    """

    def get(self, selection_id: str) -> SelectionSet | None:
        ...

    def save(self, selection: SelectionSet) -> None:
        ...


class InMemorySelectionRepository:
    """In-memory implementation backed by a private ``dict``.

    * ``selection_id`` is the key.
    * Duplicate saves for the same ``id`` with a **different** object
      raise ``ValueError``.
    * ``get`` returns ``None`` for unknown ids.
    * The internal dictionary is never exposed.
    """

    def __init__(self) -> None:
        self._store: dict[str, SelectionSet] = {}

    def get(self, selection_id: str) -> SelectionSet | None:
        return self._store.get(selection_id)

    def save(self, selection: SelectionSet) -> None:
        existing = self._store.get(selection.id)
        if existing is not None:
            if existing is not selection:
                raise ValueError(
                    f"Duplicate SelectionSet.id={selection.id!r}"
                )
            return
        self._store[selection.id] = selection
