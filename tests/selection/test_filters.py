"""Tests for generic FilterExpression nodes (Task 02).

Covers:
- Construction of every node type
- Frozen (immutability) behaviour
- Structural value-equality
- Validation: empty AllOf / AnyOf
- Validation: empty FieldIn
- Validation: reversed TimeBetween
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_dt(*args: int) -> datetime:
    """Shorthand: _utc_dt(2026, 6, 1, 12, 0, 0)."""
    return datetime(*args, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    """Every node type must be constructible with valid arguments."""

    def test_filter_expression_base(self) -> None:
        node = FilterExpression()
        assert isinstance(node, FilterExpression)

    def test_all_of(self) -> None:
        child = FieldEquals("a", 1)
        node = AllOf((child,))
        assert node.children == (child,)

    def test_any_of(self) -> None:
        child = FieldEquals("a", 1)
        node = AnyOf((child,))
        assert node.children == (child,)

    def test_not(self) -> None:
        child = FieldEquals("a", 1)
        node = Not(child)
        assert node.child is child

    def test_field_equals(self) -> None:
        node = FieldEquals("status", "OK")
        assert node.field == "status"
        assert node.value == "OK"

    def test_field_equals_none_value(self) -> None:
        node = FieldEquals("archived", None)
        assert node.value is None

    def test_field_in(self) -> None:
        node = FieldIn("colour", ("red", "blue"))
        assert node.field == "colour"
        assert node.values == ("red", "blue")

    def test_string_contains(self) -> None:
        node = StringContains("serial_no", "S1F")
        assert node.field == "serial_no"
        assert node.value == "S1F"

    def test_string_equals(self) -> None:
        node = StringEquals("product", "dm0608")
        assert node.field == "product"
        assert node.value == "dm0608"

    def test_time_between(self) -> None:
        start = _utc_dt(2026, 6, 1)
        end = _utc_dt(2026, 6, 10)
        node = TimeBetween(start, end)
        assert node.start == start
        assert node.end == end

    def test_all_of_multiple_children(self) -> None:
        a = FieldEquals("x", 1)
        b = StringContains("y", "hi")
        node = AllOf((a, b))
        assert node.children == (a, b)

    def test_any_of_multiple_children(self) -> None:
        a = FieldEquals("x", 1)
        b = FieldIn("y", ("a", "b"))
        node = AnyOf((a, b))
        assert node.children == (a, b)


# ---------------------------------------------------------------------------
# Frozen / immutability
# ---------------------------------------------------------------------------


class TestFrozen:
    """Modifying any attribute should raise FrozenInstanceError."""

    def test_field_equals_is_frozen(self) -> None:
        node = FieldEquals("k", "v")
        with pytest.raises(FrozenInstanceError):
            node.field = "other"  # type: ignore[misc]

    def test_all_of_is_frozen(self) -> None:
        node = AllOf((FieldEquals("a", 1),))
        with pytest.raises(FrozenInstanceError):
            node.children = ()  # type: ignore[misc]

    def test_time_between_is_frozen(self) -> None:
        node = TimeBetween(_utc_dt(2026, 1, 1), _utc_dt(2026, 6, 1))
        with pytest.raises(FrozenInstanceError):
            node.start = _utc_dt(2025, 1, 1)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Equality
# ---------------------------------------------------------------------------


class TestEquality:
    """Structural value-equality — same fields → equal; different → not equal."""

    def test_identical_leaves_are_equal(self) -> None:
        a = FieldEquals("x", 1)
        b = FieldEquals("x", 1)
        assert a == b
        assert hash(a) == hash(b)

    def test_different_field_not_equal(self) -> None:
        a = FieldEquals("x", 1)
        b = FieldEquals("y", 1)
        assert a != b

    def test_different_value_not_equal(self) -> None:
        a = FieldEquals("x", 1)
        b = FieldEquals("x", 2)
        assert a != b

    def test_nested_all_of_equal(self) -> None:
        leaf = FieldEquals("z", 3)
        a = AllOf((leaf,))
        b = AllOf((FieldEquals("z", 3),))
        assert a == b
        assert hash(a) == hash(b)

    def test_nested_all_of_not_equal(self) -> None:
        a = AllOf((FieldEquals("z", 3),))
        b = AllOf((FieldEquals("z", 4),))
        assert a != b

    def test_time_between_equal(self) -> None:
        s = _utc_dt(2026, 1, 1)
        e = _utc_dt(2026, 6, 1)
        assert TimeBetween(s, e) == TimeBetween(s, e)

    def test_time_between_different_start(self) -> None:
        assert TimeBetween(_utc_dt(2026, 1, 1), _utc_dt(2026, 6, 1)) != TimeBetween(
            _utc_dt(2025, 1, 1), _utc_dt(2026, 6, 1)
        )

    def test_not_equality(self) -> None:
        child = FieldEquals("a", 1)
        assert Not(child) == Not(FieldEquals("a", 1))
        assert Not(child) != Not(FieldEquals("a", 2))

    def test_different_node_types_not_equal(self) -> None:
        assert FieldEquals("x", 1) != StringEquals("x", "1")
        assert AllOf((FieldEquals("a", 1),)) != AnyOf((FieldEquals("a", 1),))


# ---------------------------------------------------------------------------
# Validation: empty children
# ---------------------------------------------------------------------------


class TestEmptyChildrenRejected:
    def test_all_of_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one child"):
            AllOf(())

    def test_any_of_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one child"):
            AnyOf(())


# ---------------------------------------------------------------------------
# Validation: empty FieldIn
# ---------------------------------------------------------------------------


class TestEmptyFieldInRejected:
    def test_field_in_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            FieldIn("f", ())


# ---------------------------------------------------------------------------
# Validation: reversed TimeBetween
# ---------------------------------------------------------------------------


class TestTimeBetweenReversed:
    def test_start_after_end_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be later"):
            TimeBetween(_utc_dt(2026, 6, 10), _utc_dt(2026, 6, 1))

    def test_start_equals_end_allowed(self) -> None:
        # Edge case: exact same instant is allowed (represents a point-in-time
        # range and is accepted by the current validation).
        dt = _utc_dt(2026, 6, 1, 12, 0, 0)
        node = TimeBetween(dt, dt)  # does not raise
        assert node.start == node.end == dt
