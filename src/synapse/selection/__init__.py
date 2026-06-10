"""Selection package — stable record-set model, query normalization, and service layer.

This package is independent of Themis, FastAPI, SigMA integrations, and
application planning. It defines what a record set is, not how it is
recognized from natural language or rendered in an API response.
"""

from synapse.selection.filters import (
    AllOf,
    AnyOf,
    FieldEquals,
    FieldIn,
    FilterExpression,
    Not,
    StringContains,
    StringEquals,
    TimeBetween,
)

from synapse.selection.models import (
    AggregationStrategy,
    RecordQuery,
    SelectionScope,
    SelectionSet,
    SortRule,
)

__all__ = [
    "AggregationStrategy",
    "AllOf",
    "AnyOf",
    "FieldEquals",
    "FieldIn",
    "FilterExpression",
    "Not",
    "RecordQuery",
    "SelectionScope",
    "SelectionSet",
    "SortRule",
    "StringContains",
    "StringEquals",
    "TimeBetween",
]
