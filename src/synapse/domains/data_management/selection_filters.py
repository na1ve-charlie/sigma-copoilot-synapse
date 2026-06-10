"""Data-management domain ``FilterExpression`` nodes (Task 07).

These nodes extend the generic ``FilterExpression`` hierarchy from
``synapse.selection.filters`` with business semantics specific to
test-record queries.

* ``ProductTypeMatch`` — product type/version/system_no composite
* ``ExcessLimitTupleMatch`` — three independent arrays (sensors /
  test_names / indicators), back-end handles the Cartesian product

The ``data_management_expression_decoders()`` function returns decoders
that can be injected into ``synapse.selection.normalization`` to
enable round-trip serialization of domain expression trees.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from synapse.selection.filters import FilterExpression
from synapse.selection.normalization import ExpressionDecoder


# ---------------------------------------------------------------------------
# ProductTypeMatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProductTypeMatch(FilterExpression):
    """One or more (type, version, system_no) composite tuples.

    Each tuple is a unit — backend matching treats the three fields
    as a single composite condition, NOT three independent lists.
    """

    configs: tuple[tuple[str, str, str], ...]

    def __post_init__(self) -> None:
        if len(self.configs) == 0:
            raise ValueError("ProductTypeMatch.configs must not be empty")


# ---------------------------------------------------------------------------
# ExcessLimitTupleMatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExcessLimitTupleMatch(FilterExpression):
    """Three independent arrays for excess-limit matching.

    The back-end generates the Cartesian product of sensors ×
    test_names × indicators internally.  Matching strategy
    (any / all) is deferred to the SigMA integration layer.
    """

    sensors: tuple[str, ...]
    test_names: tuple[str, ...]
    indicators: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.sensors) == 0 and len(self.test_names) == 0 and len(self.indicators) == 0:
            raise ValueError(
                "ExcessLimitTupleMatch requires at least one sensor, "
                "test_name, or indicator"
            )


# ---------------------------------------------------------------------------
# Domain expression decoders
# ---------------------------------------------------------------------------


def data_management_expression_decoders() -> dict[str, ExpressionDecoder]:
    """Return decoders for the data-management expression nodes.

    The returned mapping is compatible with the *expression_decoders*
    parameter of ``query_from_dict`` / ``selection_from_dict`` etc.
    """
    def _decode_product_type_match(d: Mapping[str, object]) -> ProductTypeMatch:
        raw = cast(list, d["configs"])
        configs = tuple(
            (str(t[0]), str(t[1]), str(t[2]))
            for t in cast(list, raw)
        )
        return ProductTypeMatch(configs=configs)

    def _decode_excess_limit_tuple_match(
        d: Mapping[str, object],
    ) -> ExcessLimitTupleMatch:
        return ExcessLimitTupleMatch(
            sensors=tuple(str(s) for s in cast(list, d.get("sensors", []))),
            test_names=tuple(str(t) for t in cast(list, d.get("test_names", []))),
            indicators=tuple(str(i) for i in cast(list, d.get("indicators", []))),
        )

    return {
        "product_type_match": _decode_product_type_match,
        "excess_limit_tuple_match": _decode_excess_limit_tuple_match,
    }
