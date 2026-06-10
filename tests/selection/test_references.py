"""Tests for Selection reference resolution (Task 12)."""

from __future__ import annotations

from synapse.selection.references import (
    ResolvedSelection,
    SelectionRefClarificationRequired,
    SelectionReferenceContext,
    resolve_selection_ref,
)


# ======================================================================
# active
# ======================================================================


class TestActiveReference:
    def test_resolves_active_when_set(self) -> None:
        ctx = SelectionReferenceContext(active_selection_id="sel_001")
        result = resolve_selection_ref("active", ctx)
        assert isinstance(result, ResolvedSelection)
        assert result.selection_id == "sel_001"

    def test_no_active_clarifies(self) -> None:
        ctx = SelectionReferenceContext(active_selection_id=None)
        result = resolve_selection_ref("active", ctx)
        assert isinstance(result, SelectionRefClarificationRequired)
        assert result.reason == "no_active_selection"


# ======================================================================
# previous
# ======================================================================


class TestPreviousReference:
    def test_returns_first_recent_different_from_active(self) -> None:
        ctx = SelectionReferenceContext(
            active_selection_id="sel_active",
            recent_selection_ids=("sel_active", "sel_001", "sel_002"),
        )
        result = resolve_selection_ref("previous", ctx)
        assert isinstance(result, ResolvedSelection)
        assert result.selection_id == "sel_001"  # skips same-as-active

    def test_all_recents_are_active(self) -> None:
        ctx = SelectionReferenceContext(
            active_selection_id="sel_001",
            recent_selection_ids=("sel_001",),
        )
        result = resolve_selection_ref("previous", ctx)
        assert isinstance(result, SelectionRefClarificationRequired)
        assert result.reason == "no_previous_selection"

    def test_no_recents_clarifies(self) -> None:
        ctx = SelectionReferenceContext(
            active_selection_id="sel_001",
            recent_selection_ids=(),
        )
        result = resolve_selection_ref("previous", ctx)
        assert isinstance(result, SelectionRefClarificationRequired)
        assert result.reason == "no_previous_selection"

    def test_active_none_with_recents(self) -> None:
        ctx = SelectionReferenceContext(
            active_selection_id=None,
            recent_selection_ids=("sel_001", "sel_002"),
        )
        result = resolve_selection_ref("previous", ctx)
        assert isinstance(result, ResolvedSelection)
        assert result.selection_id == "sel_001"


# ======================================================================
# id: literal
# ======================================================================


class TestIdReference:
    def test_resolves_literal_id(self) -> None:
        ctx = SelectionReferenceContext()
        result = resolve_selection_ref("id:sel_xyz", ctx)
        assert isinstance(result, ResolvedSelection)
        assert result.selection_id == "sel_xyz"

    def test_empty_literal_id_clarifies(self) -> None:
        ctx = SelectionReferenceContext()
        result = resolve_selection_ref("id:", ctx)
        assert isinstance(result, SelectionRefClarificationRequired)
        assert result.reason == "empty_selection_id"

    def test_does_not_verify_existence(self) -> None:
        """id: prefix just extracts the ID string — no repo access."""
        ctx = SelectionReferenceContext()
        result = resolve_selection_ref("id:nonexistent", ctx)
        assert isinstance(result, ResolvedSelection)
        assert result.selection_id == "nonexistent"


# ======================================================================
# unknown reference
# ======================================================================


class TestUnknownReference:
    def test_clarifies(self) -> None:
        ctx = SelectionReferenceContext()
        result = resolve_selection_ref("garbage", ctx)
        assert isinstance(result, SelectionRefClarificationRequired)
        assert "unknown_reference" in result.reason
        assert "garbage" in result.reason


# ======================================================================
# SelectionReferenceContext construction
# ======================================================================


class TestSelectionReferenceContext:
    def test_defaults(self) -> None:
        ctx = SelectionReferenceContext()
        assert ctx.active_selection_id is None
        assert ctx.recent_selection_ids == ()

    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError
        import pytest

        ctx = SelectionReferenceContext(active_selection_id="sel_001")
        with pytest.raises(FrozenInstanceError):
            ctx.active_selection_id = "sel_002"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = SelectionReferenceContext(
            active_selection_id="sel_001",
            recent_selection_ids=("sel_002",),
        )
        b = SelectionReferenceContext(
            active_selection_id="sel_001",
            recent_selection_ids=("sel_002",),
        )
        assert a == b
        assert hash(a) == hash(b)
