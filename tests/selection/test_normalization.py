"""Tests for stable normalization and query hash (Task 04).

Covers:
- normalize_query deterministic output
- query_json compact / sorted
- query_hash golden value
- Round-trip: query → dict → query
- Unknown expression type raises
- datetime ISO 8601
- tuple → array
- AllOf child order preserved
- Custom (domain) expression_decoders injection
- selection_to_dict / selection_from_dict round-trip
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

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
from synapse.selection.normalization import (
    normalize_query,
    query_from_dict,
    query_hash,
    query_json,
    selection_from_dict,
    selection_to_dict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_DT = datetime(2026, 6, 10, 15, 30, 0, tzinfo=_UTC)

_LEAF = FieldEquals("status", "OK")


def _min_query(**overrides: object) -> RecordQuery:
    kwargs: dict[str, object] = {"expression": _LEAF}
    kwargs.update(overrides)
    return RecordQuery(**kwargs)  # type: ignore[arg-type]


def _min_scope() -> SelectionScope:
    return SelectionScope()


def _min_selection(**overrides: object) -> SelectionSet:
    kwargs: dict[str, object] = {
        "id": "sel_001",
        "query": _min_query(),
        "scope": _min_scope(),
        "backend_ref": None,
        "record_count": 0,
        "snapshot_version": "sigma-v184",
        "content_hash": "sha256:deadbeef",
        "created_at": _DT,
    }
    kwargs.update(overrides)
    return SelectionSet(**kwargs)  # type: ignore[arg-type]


# ======================================================================
# normalize_query — basic shapes
# ======================================================================


class TestNormalizeQueryBasic:
    def test_single_leaf(self) -> None:
        result = normalize_query(_min_query())
        assert result["expression"] == {
            "field": "status",
            "type": "field_equals",
            "value": "OK",
        }
        assert result["limit"] is None

    def test_all_of_with_two_children(self) -> None:
        q = RecordQuery(
            expression=AllOf((
                FieldEquals("a", 1),
                StringContains("b", "hi"),
            ))
        )
        result = normalize_query(q)
        expr = result["expression"]
        assert expr["type"] == "all_of"
        assert len(expr["children"]) == 2
        assert expr["children"][0]["type"] == "field_equals"
        assert expr["children"][1]["type"] == "string_contains"

    def test_not_node(self) -> None:
        q = RecordQuery(expression=Not(FieldEquals("x", True)))
        result = normalize_query(q)
        assert result["expression"]["type"] == "not"
        assert result["expression"]["child"]["type"] == "field_equals"

    def test_time_between(self) -> None:
        t1 = datetime(2026, 1, 1, tzinfo=_UTC)
        t2 = datetime(2026, 6, 1, tzinfo=_UTC)
        q = RecordQuery(expression=TimeBetween(t1, t2))
        result = normalize_query(q)
        expr = result["expression"]
        assert expr["type"] == "time_between"
        assert expr["start"] == "2026-01-01T00:00:00+00:00"
        assert expr["end"] == "2026-06-01T00:00:00+00:00"

    def test_field_in(self) -> None:
        q = RecordQuery(expression=FieldIn("colour", ("red", "blue")))
        result = normalize_query(q)
        assert result["expression"]["type"] == "field_in"
        assert result["expression"]["values"] == ["red", "blue"]

    def test_string_equals(self) -> None:
        q = RecordQuery(expression=StringEquals("product", "dm0608"))
        result = normalize_query(q)
        assert result["expression"]["type"] == "string_equals"
        assert result["expression"]["field"] == "product"
        assert result["expression"]["value"] == "dm0608"

    def test_full_query(self) -> None:
        ag = AggregationStrategy(keep_last_per_serial=True)
        sr = (SortRule("ts", "desc"), SortRule("id", "asc"))
        q = RecordQuery(
            expression=AllOf((_LEAF,)),
            aggregate=ag,
            sort=sr,
            limit=25,
        )
        result = normalize_query(q)
        assert result["limit"] == 25
        assert result["aggregate"]["keep_last_per_serial"] is True
        assert result["aggregate"]["only_repeat_serials"] is False
        assert result["sort"][0]["field"] == "ts"
        assert result["sort"][0]["direction"] == "desc"


# ======================================================================
# Deterministic key ordering
# ======================================================================


class TestNormalizeQueryKeyOrder:
    def test_keys_are_sorted(self) -> None:
        result = normalize_query(_min_query())
        keys = list(result)
        assert keys == sorted(keys), f"Keys not sorted: {keys}"

    def test_expression_keys_sorted(self) -> None:
        result = normalize_query(_min_query())
        keys = list(result["expression"])
        assert keys == sorted(keys), f"Expression keys not sorted: {keys}"

    def test_nested_keys_sorted(self) -> None:
        q = RecordQuery(expression=AllOf((FieldEquals("x", 1), FieldEquals("y", 2))))
        result = normalize_query(q)
        for child in result["expression"]["children"]:
            assert list(child) == sorted(child)


# ======================================================================
# query_json
# ======================================================================


class TestQueryJson:
    def test_compact_json(self) -> None:
        j = query_json(_min_query())
        # no pretty-print indentation
        assert "\n" not in j

    def test_deterministic_same_input(self) -> None:
        a = query_json(_min_query())
        b = query_json(_min_query())
        assert a == b

    def test_deterministic_with_anyof(self) -> None:
        q = RecordQuery(expression=AnyOf((FieldEquals("x", 1), FieldEquals("x", 2))))
        a = query_json(q)
        b = query_json(q)
        assert a == b

    def test_bool_preserved(self) -> None:
        # FieldEquals value=True → JSON true
        q = RecordQuery(expression=FieldEquals("archived", True))
        j = query_json(q)
        assert "true" in j


# ======================================================================
# query_hash — golden value
# ======================================================================


class TestQueryHash:
    def test_hash_format(self) -> None:
        h = query_hash(_min_query())
        assert h.startswith("sha256:")
        # hex part is 64 chars
        assert len(h) == 7 + 64

    def test_deterministic_hash(self) -> None:
        assert query_hash(_min_query()) == query_hash(_min_query())

    def test_different_query_different_hash(self) -> None:
        a = query_hash(_min_query())
        b = query_hash(_min_query(limit=10))
        assert a != b

    def test_golden_hash_value(self) -> None:
        """Fixed golden hash — must never change for this exact query."""
        h = query_hash(_min_query())
        # Golden value computed from the current implementation.
        assert h == "sha256:bed9522a2f633801b22a0ee5afec4811c9d90e0cc90196d6b3a4159c46ec6c9e"


# ======================================================================
# Round-trip: query → dict → query
# ======================================================================


class TestQueryRoundTrip:
    def test_simple_leaf(self) -> None:
        original = _min_query()
        payload = normalize_query(original)
        restored = query_from_dict(payload)
        assert restored == original

    def test_all_of(self) -> None:
        original = RecordQuery(
            expression=AllOf((FieldEquals("a", 1), StringContains("b", "x")))
        )
        restored = query_from_dict(normalize_query(original))
        assert restored == original

    def test_any_of(self) -> None:
        original = RecordQuery(expression=AnyOf((FieldEquals("a", 1),)))
        restored = query_from_dict(normalize_query(original))
        assert restored == original

    def test_not(self) -> None:
        original = RecordQuery(expression=Not(FieldEquals("a", 1)))
        restored = query_from_dict(normalize_query(original))
        assert restored == original

    def test_time_between(self) -> None:
        original = RecordQuery(
            expression=TimeBetween(
                datetime(2026, 1, 1, tzinfo=_UTC),
                datetime(2026, 6, 1, tzinfo=_UTC),
            )
        )
        restored = query_from_dict(normalize_query(original))
        assert restored == original

    def test_full_query(self) -> None:
        ag = AggregationStrategy(keep_last_per_serial=True)
        sr = (SortRule("ts", "desc"),)
        original = RecordQuery(
            expression=AllOf((_LEAF,)),
            aggregate=ag,
            sort=sr,
            limit=50,
        )
        restored = query_from_dict(normalize_query(original))
        assert restored == original


# ======================================================================
# Unknown expression type
# ======================================================================


class TestUnknownExpressionType:
    def test_unknown_type_raises(self) -> None:
        payload = {
            "expression": {"type": "product_type_match", "configs": []},
            "aggregate": None,
            "sort": [],
            "limit": None,
        }
        with pytest.raises(ValueError, match="Unknown expression type"):
            query_from_dict(payload)

    def test_unknown_type_with_custom_decoder(self) -> None:
        """Custom domain decoder should resolve unknown types."""

        def _decode_pm(d: object) -> FilterExpression:
            # A domain decoder that returns a FieldEquals placeholder
            m = d if isinstance(d, dict) else {}
            return FieldEquals("product", m.get("configs", "unknown"))

        decoders = {"product_type_match": _decode_pm}
        payload = {
            "expression": {"type": "product_type_match", "configs": "test"},
            "aggregate": None,
            "sort": [],
            "limit": None,
        }
        q = query_from_dict(payload, expression_decoders=decoders)
        assert isinstance(q.expression, FieldEquals)
        assert q.expression.field == "product"


# ======================================================================
# Child order preservation
# ======================================================================


class TestChildOrderPreservation:
    def test_all_of_children_keep_order(self) -> None:
        original = RecordQuery(
            expression=AllOf((
                FieldEquals("a", 1),
                FieldEquals("b", 2),
                FieldEquals("c", 3),
            ))
        )
        payload = normalize_query(original)
        types = [c["type"] for c in payload["expression"]["children"]]
        assert types == ["field_equals", "field_equals", "field_equals"]
        fields_in_order = [c["field"] for c in payload["expression"]["children"]]
        assert fields_in_order == ["a", "b", "c"]

    def test_any_of_children_keep_order(self) -> None:
        original = RecordQuery(
            expression=AnyOf((
                StringContains("x", "1"),
                FieldEquals("y", 2),
            ))
        )
        payload = normalize_query(original)
        types = [c["type"] for c in payload["expression"]["children"]]
        assert types == ["string_contains", "field_equals"]


# ======================================================================
# selection_to_dict / selection_from_dict
# ======================================================================


class TestSelectionDictRoundTrip:
    def test_to_dict_includes_all_fields(self) -> None:
        sel = _min_selection()
        d = selection_to_dict(sel)
        assert d["id"] == "sel_001"
        assert "query" in d
        assert "scope" in d
        assert "created_at" in d

    def test_round_trip_minimal(self) -> None:
        original = _min_selection()
        restored = selection_from_dict(selection_to_dict(original))
        assert restored == original

    def test_round_trip_with_expires_and_chains(self) -> None:
        original = _min_selection(
            expires_at=_DT + timedelta(hours=1),
            derived_from="sel_000",
            supersedes="sel_000",
        )
        restored = selection_from_dict(selection_to_dict(original))
        assert restored == original

    def test_round_trip_with_full_query(self) -> None:
        ag = AggregationStrategy(keep_last_per_serial=True)
        sr = (SortRule("ts", "desc"),)
        q = RecordQuery(
            expression=AllOf((_LEAF, StringContains("remark", "test"))),
            aggregate=ag,
            sort=sr,
            limit=10,
        )
        original = _min_selection(query=q, record_count=125, backend_ref="sigma:job/42")
        restored = selection_from_dict(selection_to_dict(original))
        assert restored == original

    def test_round_trip_with_custom_decoders(self) -> None:
        # Ensure selection_from_dict accepts expression_decoders
        sel = _min_selection()
        d = selection_to_dict(sel)
        restored = selection_from_dict(d, expression_decoders=None)
        assert restored == sel
