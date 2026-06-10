"""Data-management domain selection criteria (Task 06).

Defines the business-level record-selection concepts that sit
between Themis recognition results and the generic Selection model.
These types carry **no** knowledge of Themis internals, SigMA
adapters, or application planning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from synapse.selection.models import SortRule
from synapse.selection.time_ranges import TimeRangeCriteria


# ---------------------------------------------------------------------------
# ProductConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProductConfig:
    """A single (type, version, system_no) composite.

    These three values form a unit — they must not be split into
    independent lists.  The downstream projector converts a set of
    ``ProductConfig`` into a ``ProductTypeMatch`` filter node
    (Task 07).
    """

    type: str
    version: str
    system_no: str

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("ProductConfig.type must not be empty")
        if not self.version:
            raise ValueError("ProductConfig.version must not be empty")
        if not self.system_no:
            raise ValueError("ProductConfig.system_no must not be empty")


# ---------------------------------------------------------------------------
# RecordSelectionCriteria
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecordSelectionCriteria:
    """Business-level record-filtering criteria.

    All fields default to neutral / empty so callers only need to
    set the relevant dimensions.  This type is not a query — it must
    be projected to a ``RecordQuery`` before any back-end work.
    """

    product_configs: tuple[ProductConfig, ...] = ()
    serial_contains: str | None = None
    excess_limit_sensors: tuple[str, ...] = ()
    excess_limit_test_names: tuple[str, ...] = ()
    excess_limit_indicators: tuple[str, ...] = ()
    time_ranges: tuple[TimeRangeCriteria, ...] = ()
    judgement_results: tuple[str, ...] = ()
    manual_verdict: str | None = None
    record_status: str | None = None
    test_section: int | None = None
    remark_contains: str | None = None
    archived: bool | None = None
    keep_last_per_serial: bool = False
    only_repeat_serials: bool = False
    sort: tuple[SortRule, ...] = ()
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit <= 0:
            raise ValueError("RecordSelectionCriteria.limit must be greater than zero")


# ---------------------------------------------------------------------------
# RelativeSelectionReference
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RelativeSelectionReference:
    """A symbolic reference to a conversation-level selection.

    ``kind="active"`` means "the currently active SelectionSet in
    this conversation".  Resolution to a concrete ``selection_id``
    is the responsibility of the application layer (Task 13).
    """

    kind: Literal["active"]
