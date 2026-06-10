"""Tests for core Selection models (Task 03).

Covers:
- Minimum valid object construction
- Invalid limit (record query)
- Invalid record count (negative)
- Invalid expiration (expires_at <= created_at)
- derived_from / supersedes fields preserved
- is_expired / is_stale computed helpers
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from synapse.selection.filters import FieldEquals
from synapse.selection.models import (
    AggregationStrategy,
    RecordQuery,
    SelectionScope,
    SelectionSet,
    SortRule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc

_DT = datetime(2026, 6, 10, 15, 30, 0, tzinfo=_UTC)


def _min_query() -> RecordQuery:
    return RecordQuery(expression=FieldEquals("status", "OK"))


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
# SortRule
# ======================================================================


class TestSortRule:
    def test_basic(self) -> None:
        sr = SortRule(field="created_at", direction="desc")
        assert sr.field == "created_at"
        assert sr.direction == "desc"

    def test_asc(self) -> None:
        sr = SortRule(field="serial_no", direction="asc")
        assert sr.direction == "asc"

    def test_frozen(self) -> None:
        sr = SortRule(field="x", direction="asc")
        with pytest.raises(FrozenInstanceError):
            sr.direction = "desc"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = SortRule("f", "asc")
        b = SortRule("f", "asc")
        assert a == b
        assert hash(a) == hash(b)
        assert a != SortRule("f", "desc")


# ======================================================================
# AggregationStrategy
# ======================================================================


class TestAggregationStrategy:
    def test_defaults(self) -> None:
        ag = AggregationStrategy()
        assert ag.keep_last_per_serial is False
        assert ag.only_repeat_serials is False

    def test_custom(self) -> None:
        ag = AggregationStrategy(
            keep_last_per_serial=True,
            only_repeat_serials=True,
        )
        assert ag.keep_last_per_serial is True
        assert ag.only_repeat_serials is True

    def test_frozen(self) -> None:
        ag = AggregationStrategy()
        with pytest.raises(FrozenInstanceError):
            ag.keep_last_per_serial = True  # type: ignore[misc]


# ======================================================================
# RecordQuery
# ======================================================================


class TestRecordQuery:
    def test_minimal(self) -> None:
        q = RecordQuery(expression=FieldEquals("f", 1))
        assert q.expression == FieldEquals("f", 1)
        assert q.aggregate is None
        assert q.sort == ()
        assert q.limit is None

    def test_full(self) -> None:
        ag = AggregationStrategy()
        sr = (SortRule("ts", "desc"),)
        q = RecordQuery(
            expression=FieldEquals("f", 1),
            aggregate=ag,
            sort=sr,
            limit=10,
        )
        assert q.aggregate is ag
        assert q.sort == sr
        assert q.limit == 10

    def test_limit_positive_one(self) -> None:
        q = RecordQuery(expression=FieldEquals("f", 1), limit=1)
        assert q.limit == 1

    def test_limit_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="greater than zero"):
            RecordQuery(expression=FieldEquals("f", 1), limit=0)

    def test_limit_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="greater than zero"):
            RecordQuery(expression=FieldEquals("f", 1), limit=-1)

    def test_frozen(self) -> None:
        q = _min_query()
        with pytest.raises(FrozenInstanceError):
            q.limit = 50  # type: ignore[misc]

    def test_equality(self) -> None:
        a = _min_query()
        b = _min_query()
        assert a == b

        c = RecordQuery(expression=FieldEquals("f", 2))
        assert a != c


# ======================================================================
# SelectionScope
# ======================================================================


class TestSelectionScope:
    def test_defaults_all_none(self) -> None:
        sc = SelectionScope()
        assert sc.workspace_session_id is None
        assert sc.dataset_id is None
        assert sc.dataset_version is None
        assert sc.filter_hash is None

    def test_partial(self) -> None:
        sc = SelectionScope(dataset_id="1152", dataset_version=3)
        assert sc.dataset_id == "1152"
        assert sc.dataset_version == 3
        assert sc.workspace_session_id is None

    def test_frozen(self) -> None:
        sc = SelectionScope()
        with pytest.raises(FrozenInstanceError):
            sc.dataset_id = "x"  # type: ignore[misc]


# ======================================================================
# SelectionSet — minimum valid
# ======================================================================


class TestSelectionSetMinimumValid:
    """Task 03 requirement: minimum valid object."""

    def test_minimal_construction(self) -> None:
        sel = _min_selection()
        assert sel.id == "sel_001"
        assert sel.record_count == 0
        assert sel.snapshot_version == "sigma-v184"
        assert sel.content_hash == "sha256:deadbeef"
        assert sel.created_at == _DT

    def test_with_expires_at(self) -> None:
        sel = _min_selection(
            expires_at=_DT + timedelta(hours=1),
        )
        assert sel.expires_at == _DT + timedelta(hours=1)

    def test_record_count_positive(self) -> None:
        sel = _min_selection(record_count=125)
        assert sel.record_count == 125

    def test_backend_ref(self) -> None:
        sel = _min_selection(backend_ref="sigma:job/42")
        assert sel.backend_ref == "sigma:job/42"

    def test_frozen(self) -> None:
        sel = _min_selection()
        with pytest.raises(FrozenInstanceError):
            sel.record_count = 5  # type: ignore[misc]


# ======================================================================
# SelectionSet — invalid record_count
# ======================================================================


class TestSelectionSetInvalidRecordCount:
    def test_negative_one_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            _min_selection(record_count=-1)

    def test_large_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            _min_selection(record_count=-999)


# ======================================================================
# SelectionSet — invalid id / snapshot / hash
# ======================================================================


class TestSelectionSetInvalidRequiredStrings:
    def test_empty_id_raises(self) -> None:
        with pytest.raises(ValueError, match="id must not be empty"):
            _min_selection(id="")

    def test_empty_snapshot_version_raises(self) -> None:
        with pytest.raises(ValueError, match="snapshot_version must not be empty"):
            _min_selection(snapshot_version="")

    def test_empty_content_hash_raises(self) -> None:
        with pytest.raises(ValueError, match="content_hash must not be empty"):
            _min_selection(content_hash="")


# ======================================================================
# SelectionSet — invalid expiration
# ======================================================================


class TestSelectionSetInvalidExpiration:
    def test_expires_at_equals_created_at_raises(self) -> None:
        with pytest.raises(ValueError, match="must be later than created_at"):
            _min_selection(expires_at=_DT)

    def test_expires_at_before_created_at_raises(self) -> None:
        with pytest.raises(ValueError, match="must be later than created_at"):
            _min_selection(expires_at=_DT - timedelta(seconds=1))


# ======================================================================
# SelectionSet — derived_from / supersedes
# ======================================================================


class TestSelectionSetDerivedSupersedes:
    """Task 03: derived_from / supersedes fields preserved."""

    def test_derived_from_preserved(self) -> None:
        sel = _min_selection(derived_from="sel_000")
        assert sel.derived_from == "sel_000"

    def test_supersedes_preserved(self) -> None:
        sel = _min_selection(supersedes="sel_000")
        assert sel.supersedes == "sel_000"

    def test_both_chain(self) -> None:
        sel = _min_selection(derived_from="sel_001", supersedes="sel_000")
        assert sel.derived_from == "sel_001"
        assert sel.supersedes == "sel_000"

    def test_default_none(self) -> None:
        sel = _min_selection()
        assert sel.derived_from is None
        assert sel.supersedes is None


# ======================================================================
# is_expired
# ======================================================================


class TestIsExpired:
    def test_no_expires_at_not_expired(self) -> None:
        sel = _min_selection()
        assert sel.is_expired(now=_DT + timedelta(days=365)) is False

    def test_future_not_expired(self) -> None:
        sel = _min_selection(expires_at=_DT + timedelta(hours=1))
        assert sel.is_expired(now=_DT) is False

    def test_past_is_expired(self) -> None:
        sel = _min_selection(expires_at=_DT + timedelta(hours=1))
        assert sel.is_expired(now=_DT + timedelta(hours=2)) is True

    def test_exact_now_treated_as_expired(self) -> None:
        # expires_at <= now → expired
        sel = _min_selection(expires_at=_DT + timedelta(hours=1))
        assert sel.is_expired(now=_DT + timedelta(hours=1)) is True

    def test_default_now(self) -> None:
        """Smoke test: is_expired with default now parameter.
        Uses a fixed point in the distant past so wall-clock 'now'
        is always after it."""
        created = datetime(2020, 1, 1, 0, 0, 0, tzinfo=_UTC)
        expired = datetime(2020, 1, 2, 0, 0, 0, tzinfo=_UTC)
        sel = _min_selection(created_at=created, expires_at=expired)
        assert sel.is_expired() is True


# ======================================================================
# is_stale
# ======================================================================


class TestIsStale:
    def test_same_version_not_stale(self) -> None:
        sel = _min_selection(snapshot_version="sigma-v184")
        assert sel.is_stale(current_snapshot_version="sigma-v184") is False

    def test_different_version_is_stale(self) -> None:
        sel = _min_selection(snapshot_version="sigma-v183")
        assert sel.is_stale(current_snapshot_version="sigma-v184") is True

    def test_empty_current_version_is_stale(self) -> None:
        sel = _min_selection(snapshot_version="sigma-v184")
        assert sel.is_stale(current_snapshot_version="") is True


# ======================================================================
# SelectionSet — timezone-aware enforcement (P2-1 fix)
# ======================================================================


class TestSelectionSetTimezoneRequired:
    def test_naive_created_at_raises(self) -> None:
        naive = datetime(2026, 6, 10, 15, 30, 0)
        with pytest.raises(ValueError, match="must be timezone-aware"):
            _min_selection(created_at=naive)

    def test_naive_expires_at_raises(self) -> None:
        naive = datetime(2026, 6, 11, 15, 30, 0)
        with pytest.raises(ValueError, match="must be timezone-aware"):
            _min_selection(expires_at=naive)
