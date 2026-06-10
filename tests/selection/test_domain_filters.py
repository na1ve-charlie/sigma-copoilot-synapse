"""Tests for data-management domain FilterExpression nodes (Task 07).

Covers:
- ProductTypeMatch construction / frozen / equality / empty rejection
- ExcessLimitTupleMatch construction / frozen / equality / empty rejection
- data_management_expression_decoders keys
- Round-trip through normalization (normalize_query + query_from_dict)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from synapse.domains.data_management.selection_filters import (
    ExcessLimitTupleMatch,
    ProductTypeMatch,
    data_management_expression_decoders,
)
from synapse.selection.filters import FilterExpression
from synapse.selection.normalization import normalize_query, query_from_dict


# ======================================================================
# ProductTypeMatch
# ======================================================================


class TestProductTypeMatch:
    def test_construction_single(self) -> None:
        node = ProductTypeMatch(
            configs=(("dm0608", "3", "7s-SNF1001"),)
        )
        assert node.configs == (("dm0608", "3", "7s-SNF1001"),)

    def test_construction_multiple(self) -> None:
        node = ProductTypeMatch(
            configs=(
                ("dm0608", "3", "7s-SNF1001"),
                ("dm0608", "4", "7s-SNF1001"),
            )
        )
        assert len(node.configs) == 2
        assert node.configs[1][1] == "4"

    def test_is_filter_expression(self) -> None:
        node = ProductTypeMatch(configs=(("dm0608", "3", "7s-SNF1001"),))
        assert isinstance(node, FilterExpression)

    def test_frozen(self) -> None:
        node = ProductTypeMatch(configs=(("dm0608", "3", "7s-SNF1001"),))
        with pytest.raises(FrozenInstanceError):
            node.configs = ()  # type: ignore[misc]

    def test_equality(self) -> None:
        a = ProductTypeMatch(configs=(("dm0608", "3", "7s-SNF1001"),))
        b = ProductTypeMatch(configs=(("dm0608", "3", "7s-SNF1001"),))
        assert a == b
        assert hash(a) == hash(b)

        c = ProductTypeMatch(configs=(("dm0609", "3", "7s-SNF1001"),))
        assert a != c

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            ProductTypeMatch(configs=())


# ======================================================================
# ExcessLimitTupleMatch
# ======================================================================


class TestExcessLimitTupleMatch:
    def test_construction(self) -> None:
        node = ExcessLimitTupleMatch(
            sensors=("sensor01", "sensor02"),
            test_names=("Std-D",),
            indicators=("倒频谱-0.2",),
        )
        assert node.sensors == ("sensor01", "sensor02")
        assert node.test_names == ("Std-D",)
        assert node.indicators == ("倒频谱-0.2",)

    def test_sensors_only(self) -> None:
        node = ExcessLimitTupleMatch(
            sensors=("sensor01",),
            test_names=(),
            indicators=(),
        )
        assert node.sensors == ("sensor01",)
        assert node.test_names == ()
        assert node.indicators == ()

    def test_indicators_only(self) -> None:
        node = ExcessLimitTupleMatch(
            sensors=(),
            test_names=(),
            indicators=("peak", "RMS"),
        )
        assert node.indicators == ("peak", "RMS")

    def test_is_filter_expression(self) -> None:
        node = ExcessLimitTupleMatch(
            sensors=("sensor01",), test_names=(), indicators=()
        )
        assert isinstance(node, FilterExpression)

    def test_frozen(self) -> None:
        node = ExcessLimitTupleMatch(
            sensors=("sensor01",), test_names=(), indicators=()
        )
        with pytest.raises(FrozenInstanceError):
            node.sensors = ()  # type: ignore[misc]

    def test_equality(self) -> None:
        a = ExcessLimitTupleMatch(
            sensors=("sensor01", "sensor02"),
            test_names=("Std-D",),
            indicators=("倒频谱-0.2",),
        )
        b = ExcessLimitTupleMatch(
            sensors=("sensor01", "sensor02"),
            test_names=("Std-D",),
            indicators=("倒频谱-0.2",),
        )
        assert a == b
        assert hash(a) == hash(b)

        c = ExcessLimitTupleMatch(
            sensors=("sensor03",), test_names=(), indicators=()
        )
        assert a != c

    def test_all_three_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="requires at least one"):
            ExcessLimitTupleMatch(sensors=(), test_names=(), indicators=())


# ======================================================================
# data_management_expression_decoders
# ======================================================================


class TestDataManagementExpressionDecoders:
    def test_contains_both_keys(self) -> None:
        decoders = data_management_expression_decoders()
        assert "product_type_match" in decoders
        assert "excess_limit_tuple_match" in decoders

    def test_decoders_are_callable(self) -> None:
        decoders = data_management_expression_decoders()
        assert callable(decoders["product_type_match"])
        assert callable(decoders["excess_limit_tuple_match"])


# ======================================================================
# Round-trip through normalization
# ======================================================================


class TestDomainFilterRoundTrip:
    def test_product_type_match_roundtrip(self) -> None:
        from synapse.selection.models import RecordQuery

        node = ProductTypeMatch(
            configs=(
                ("dm0608", "3", "7s-SNF1001"),
                ("dm0608", "4", "7s-SNF1001"),
            )
        )
        query = RecordQuery(expression=node)
        payload = normalize_query(query)

        decoders = data_management_expression_decoders()
        restored = query_from_dict(payload, expression_decoders=decoders)
        assert restored == query

    def test_excess_limit_tuple_match_roundtrip(self) -> None:
        from synapse.selection.models import RecordQuery

        node = ExcessLimitTupleMatch(
            sensors=("sensor01", "sensor02"),
            test_names=("Std-D",),
            indicators=("倒频谱-0.2",),
        )
        query = RecordQuery(expression=node)
        payload = normalize_query(query)

        decoders = data_management_expression_decoders()
        restored = query_from_dict(payload, expression_decoders=decoders)
        assert restored == query

    def test_normalized_type_keys_match_decoder(self) -> None:
        """Verify the snake_case 'type' emitted during normalization matches
        the decoder keys produced by data_management_expression_decoders."""
        from synapse.selection.models import RecordQuery

        decoders = data_management_expression_decoders()

        # ProductTypeMatch
        pm_node = ProductTypeMatch(configs=(("dm0608", "3", "7s-SNF1001"),))
        pm_payload = normalize_query(RecordQuery(expression=pm_node))
        assert pm_payload["expression"]["type"] in decoders

        # ExcessLimitTupleMatch
        el_node = ExcessLimitTupleMatch(
            sensors=("sensor01",), test_names=("Std-D",), indicators=("倒频谱-0.2",)
        )
        el_payload = normalize_query(RecordQuery(expression=el_node))
        assert el_payload["expression"]["type"] in decoders
