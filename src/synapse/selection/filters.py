"""Immutable, comparable filter expression nodes.

These nodes are pure domain-agnostic building blocks.  They carry no
SigMA knowledge, no SQL generation, and no Pydantic dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class FilterExpression:
    """Base for every filter expression node."""


# ---------------------------------------------------------------------------
# Combinator nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllOf(FilterExpression):
    """Conjunction — *all* children must match."""

    children: tuple[FilterExpression, ...]

    def __post_init__(self) -> None:
        if len(self.children) == 0:
            raise ValueError("AllOf requires at least one child expression")


@dataclass(frozen=True, slots=True)
class AnyOf(FilterExpression):
    """Disjunction — *at least one* child must match."""

    children: tuple[FilterExpression, ...]

    def __post_init__(self) -> None:
        if len(self.children) == 0:
            raise ValueError("AnyOf requires at least one child expression")


@dataclass(frozen=True, slots=True)
class Not(FilterExpression):
    """Negation of a single child expression."""

    child: FilterExpression


# ---------------------------------------------------------------------------
# Field-level leaf nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldEquals(FilterExpression):
    """Scalar exact-match on a named field."""

    field: str
    value: object


@dataclass(frozen=True, slots=True)
class FieldIn(FilterExpression):
    """Match when *field* value is among the given set."""

    field: str
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.values) == 0:
            raise ValueError("FieldIn.values must not be empty")


@dataclass(frozen=True, slots=True)
class StringContains(FilterExpression):
    """Substring (LIKE %value%) match on a named field."""

    field: str
    value: str


@dataclass(frozen=True, slots=True)
class StringEquals(FilterExpression):
    """Exact string match on a named field."""

    field: str
    value: str


# ---------------------------------------------------------------------------
# Temporal node
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimeBetween(FilterExpression):
    """Closed time-range filter."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(
                f"TimeBetween.start ({self.start.isoformat()}) "
                f"must not be later than end ({self.end.isoformat()})"
            )
