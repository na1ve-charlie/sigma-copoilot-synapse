"""Core Selection models — immutable, no persistence or service logic.

Task 03: RecordQuery, SelectionSet, SelectionScope, SortRule,
AggregationStrategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from synapse.selection.filters import FilterExpression


# ---------------------------------------------------------------------------
# SortRule
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SortRule:
    """Ordering rule for a single field."""

    field: str
    direction: Literal["asc", "desc"]


# ---------------------------------------------------------------------------
# AggregationStrategy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AggregationStrategy:
    """Post-filter collection-level aggregation."""

    keep_last_per_serial: bool = False
    only_repeat_serials: bool = False


# ---------------------------------------------------------------------------
# RecordQuery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecordQuery:
    """A fully-constructed filter + aggregation + sort + limit."""

    expression: FilterExpression
    aggregate: AggregationStrategy | None = None
    sort: tuple[SortRule, ...] = ()
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit <= 0:
            raise ValueError("RecordQuery.limit must be greater than zero")


# ---------------------------------------------------------------------------
# SelectionScope
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectionScope:
    """Identifies the dataset / workspace that a selection operates on.

    All fields are optional by design — the scope may be fully resolved
    at materialisation time.
    """

    workspace_session_id: str | None = None
    dataset_id: str | None = None
    dataset_version: int | None = None
    filter_hash: str | None = None


# ---------------------------------------------------------------------------
# SelectionSet
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectionSet:
    """Immutable snapshot of a resolved record collection."""

    id: str
    query: RecordQuery
    scope: SelectionScope
    backend_ref: str | None
    record_count: int
    snapshot_version: str
    content_hash: str
    created_at: datetime
    expires_at: datetime | None = None
    derived_from: str | None = None
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("SelectionSet.id must not be empty")
        if not self.snapshot_version:
            raise ValueError("SelectionSet.snapshot_version must not be empty")
        if not self.content_hash:
            raise ValueError("SelectionSet.content_hash must not be empty")
        if self.record_count < 0:
            raise ValueError(
                f"SelectionSet.record_count must not be negative, "
                f"got {self.record_count}"
            )
        if self.created_at.tzinfo is None:
            raise ValueError(
                "SelectionSet.created_at must be timezone-aware"
            )
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None:
                raise ValueError(
                    "SelectionSet.expires_at must be timezone-aware"
                )
            if self.expires_at <= self.created_at:
                raise ValueError(
                    f"SelectionSet.expires_at "
                    f"({self.expires_at.isoformat()}) "
                    f"must be later than created_at "
                    f"({self.created_at.isoformat()})"
                )

    # ------------------------------------------------------------------
    # Computed accessors (never mutate the model)
    # ------------------------------------------------------------------

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """True when *expires_at* is set and in the past.

        If *now* is omitted the current wall-clock time (UTC) is used.
        """
        if self.expires_at is None:
            return False
        ref = now if now is not None else datetime.now(timezone.utc)
        return self.expires_at <= ref

    def is_stale(self, *, current_snapshot_version: str) -> bool:
        """True when the selection's snapshot version differs from the
        current backend snapshot version."""
        return self.snapshot_version != current_snapshot_version
