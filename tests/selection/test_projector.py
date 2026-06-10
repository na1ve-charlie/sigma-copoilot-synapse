"""Tests for Selection Query Projector (Task 09)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from synapse.domains.data_management.selection_criteria import (
    ProductConfig,
    RecordSelectionCriteria,
)
from synapse.domains.data_management.selection_filters import (
    ExcessLimitTupleMatch,
    ProductTypeMatch,
)
from synapse.domains.data_management.selection_projector import (
    EmptySelectionCriteriaError,
    project_criteria,
)
from synapse.selection.filters import (
    AllOf,
    AnyOf,
    FieldEquals,
    FieldIn,
    StringContains,
    TimeBetween,
)
from synapse.selection.models import SortRule
from synapse.selection.time_ranges import TimeRangeCriteria

_UTC = timezone.utc

_DT = datetime(2026, 6, 10, 15, 30, 0, tzinfo=_UTC)


def _dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=_UTC)


# ======================================================================
# Empty criteria
# ======================================================================


class TestEmptyCriteria:
    def test_all_defaults_raises(self) -> None:
        with pytest.raises(EmptySelectionCriteriaError):
            project_criteria(RecordSelectionCriteria())


# ======================================================================
# Single-filter projections
# ======================================================================


class TestSingleFilter:
    def test_product_configs(self) -> None:
        c = RecordSelectionCriteria(
            product_configs=(
                ProductConfig("dm0608", "3", "7s-SNF1001"),
            ),
        )
        q = project_criteria(c)
        assert isinstance(q.expression, ProductTypeMatch)
        assert q.expression.configs == (("dm0608", "3", "7s-SNF1001"),)

    def test_serial_contains(self) -> None:
        c = RecordSelectionCriteria(serial_contains="S1F")
        q = project_criteria(c)
        assert isinstance(q.expression, StringContains)
        assert q.expression.field == "serial_no"
        assert q.expression.value == "S1F"

    def test_excess_limit(self) -> None:
        c = RecordSelectionCriteria(
            excess_limit_sensors=("sensor01", "sensor02"),
            excess_limit_test_names=("Std-D",),
            excess_limit_indicators=("倒频谱-0.2",),
        )
        q = project_criteria(c)
        assert isinstance(q.expression, ExcessLimitTupleMatch)
        assert q.expression.sensors == ("sensor01", "sensor02")
        assert q.expression.indicators == ("倒频谱-0.2",)

    def test_single_time_range(self) -> None:
        tr = TimeRangeCriteria(
            start=_dt(2026, 6, 1),
            end=_dt(2026, 6, 10, 23, 59, 59),
        )
        c = RecordSelectionCriteria(time_ranges=(tr,))
        q = project_criteria(c)
        assert isinstance(q.expression, TimeBetween)
        assert q.expression.start == tr.start

    def test_multiple_time_ranges(self) -> None:
        tr1 = TimeRangeCriteria(start=_dt(2026, 6, 1), end=_dt(2026, 6, 3))
        tr2 = TimeRangeCriteria(start=_dt(2026, 6, 7), end=_dt(2026, 6, 9))
        c = RecordSelectionCriteria(time_ranges=(tr1, tr2))
        q = project_criteria(c)
        assert isinstance(q.expression, AnyOf)
        assert len(q.expression.children) == 2
        assert isinstance(q.expression.children[0], TimeBetween)

    def test_single_sided_time_range_is_skipped(self) -> None:
        """Single-sided ranges cannot be projected to two-sided TimeBetween."""
        tr = TimeRangeCriteria(start=_dt(2026, 6, 1), end=None)
        c = RecordSelectionCriteria(time_ranges=(tr,))
        with pytest.raises(EmptySelectionCriteriaError):
            project_criteria(c)

    def test_judgement_results(self) -> None:
        c = RecordSelectionCriteria(judgement_results=("不合格", "合格"))
        q = project_criteria(c)
        assert isinstance(q.expression, FieldIn)
        assert q.expression.field == "judgement_result"
        assert q.expression.values == ("不合格", "合格")

    def test_manual_verdict(self) -> None:
        c = RecordSelectionCriteria(manual_verdict="合格")
        q = project_criteria(c)
        assert isinstance(q.expression, FieldEquals)
        assert q.expression.field == "manual_verdict"
        assert q.expression.value == "合格"

    def test_record_status(self) -> None:
        c = RecordSelectionCriteria(record_status="合格")
        q = project_criteria(c)
        assert isinstance(q.expression, FieldEquals)
        assert q.expression.field == "record_status"

    def test_test_section(self) -> None:
        c = RecordSelectionCriteria(test_section=12)
        q = project_criteria(c)
        assert isinstance(q.expression, FieldEquals)
        assert q.expression.field == "test_section"
        assert q.expression.value == 12

    def test_remark_contains(self) -> None:
        c = RecordSelectionCriteria(remark_contains="2321")
        q = project_criteria(c)
        assert isinstance(q.expression, StringContains)
        assert q.expression.field == "remark"
        assert q.expression.value == "2321"

    def test_archived_true(self) -> None:
        c = RecordSelectionCriteria(archived=True)
        q = project_criteria(c)
        assert isinstance(q.expression, FieldEquals)
        assert q.expression.value is True

    def test_archived_false(self) -> None:
        c = RecordSelectionCriteria(archived=False)
        q = project_criteria(c)
        assert isinstance(q.expression, FieldEquals)
        assert q.expression.value is False


# ======================================================================
# Multi-filter → AllOf
# ======================================================================


class TestMultiFilterAllOf:
    def test_two_filters_wrapped_in_allof(self) -> None:
        c = RecordSelectionCriteria(
            serial_contains="S1F",
            judgement_results=("不合格",),
        )
        q = project_criteria(c)
        assert isinstance(q.expression, AllOf)
        assert len(q.expression.children) == 2
        types = [type(ch) for ch in q.expression.children]
        assert StringContains in types
        assert FieldIn in types

    def test_three_filters(self) -> None:
        c = RecordSelectionCriteria(
            serial_contains="S1F",
            record_status="合格",
            archived=False,
        )
        q = project_criteria(c)
        assert isinstance(q.expression, AllOf)
        assert len(q.expression.children) == 3


# ======================================================================
# Aggregation / sort / limit
# ======================================================================


class TestAggregationSortLimit:
    def test_keep_last_per_serial(self) -> None:
        c = RecordSelectionCriteria(
            serial_contains="S1F",
            keep_last_per_serial=True,
        )
        q = project_criteria(c)
        assert q.aggregate is not None
        assert q.aggregate.keep_last_per_serial is True

    def test_only_repeat_serials(self) -> None:
        c = RecordSelectionCriteria(
            serial_contains="S1F",
            only_repeat_serials=True,
        )
        q = project_criteria(c)
        assert q.aggregate is not None
        assert q.aggregate.only_repeat_serials is True

    def test_no_aggregate_when_both_false(self) -> None:
        c = RecordSelectionCriteria(serial_contains="S1F")
        q = project_criteria(c)
        assert q.aggregate is None

    def test_sort_preserved(self) -> None:
        sr = (SortRule("created_at", "desc"),)
        c = RecordSelectionCriteria(serial_contains="S1F", sort=sr)
        q = project_criteria(c)
        assert q.sort == sr

    def test_limit_preserved(self) -> None:
        c = RecordSelectionCriteria(serial_contains="S1F", limit=20)
        q = project_criteria(c)
        assert q.limit == 20


# ======================================================================
# Pure function — no mutation
# ======================================================================


class TestPureFunction:
    def test_criteria_unchanged(self) -> None:
        c = RecordSelectionCriteria(
            serial_contains="S1F",
            judgement_results=("不合格",),
        )
        original = (c.serial_contains, c.judgement_results)
        project_criteria(c)
        assert c.serial_contains == original[0]
        assert c.judgement_results == original[1]
