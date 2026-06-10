"""Tests for data-management selection criteria (Task 06).

Covers:
- ProductConfig construction / frozen / equality / empty rejections
- RecordSelectionCriteria minimum / full / limit validation
- time_ranges as tuple (multiple TimeRangeCriteria)
- RelativeSelectionReference construction / frozen / kind
- No Themis / SigMA imports
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from synapse.domains.data_management import (
    ProductConfig,
    RecordSelectionCriteria,
    RelativeSelectionReference,
)
from synapse.selection.models import SortRule
from synapse.selection.time_ranges import TimeRangeCriteria

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc


def _dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=_UTC)


# ======================================================================
# ProductConfig
# ======================================================================


class TestProductConfig:
    def test_construction(self) -> None:
        pc = ProductConfig(type="dm0608", version="3", system_no="7s-SNF1001")
        assert pc.type == "dm0608"
        assert pc.version == "3"
        assert pc.system_no == "7s-SNF1001"

    def test_frozen(self) -> None:
        pc = ProductConfig(type="dm0608", version="3", system_no="7s-SNF1001")
        with pytest.raises(FrozenInstanceError):
            pc.type = "dm0609"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = ProductConfig("t", "v", "s")
        b = ProductConfig("t", "v", "s")
        assert a == b
        assert hash(a) == hash(b)
        assert a != ProductConfig("t2", "v", "s")

    def test_tuple_of_configs(self) -> None:
        configs = (
            ProductConfig("dm0608", "3", "7s-SNF1001"),
            ProductConfig("dm0608", "4", "7s-SNF1001"),
        )
        assert len(configs) == 2
        assert configs[0].version == "3"
        assert configs[1].version == "4"

    def test_empty_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="type must not be empty"):
            ProductConfig(type="", version="3", system_no="7s-SNF1001")

    def test_empty_version_rejected(self) -> None:
        with pytest.raises(ValueError, match="version must not be empty"):
            ProductConfig(type="dm0608", version="", system_no="7s-SNF1001")

    def test_empty_system_no_rejected(self) -> None:
        with pytest.raises(ValueError, match="system_no must not be empty"):
            ProductConfig(type="dm0608", version="3", system_no="")


# ======================================================================
# RecordSelectionCriteria — construction
# ======================================================================


class TestRecordSelectionCriteria:
    def test_default_all_empty(self) -> None:
        c = RecordSelectionCriteria()
        assert c.product_configs == ()
        assert c.serial_contains is None
        assert c.excess_limit_sensors == ()
        assert c.excess_limit_test_names == ()
        assert c.excess_limit_indicators == ()
        assert c.time_ranges == ()
        assert c.judgement_results == ()
        assert c.manual_verdict is None
        assert c.record_status is None
        assert c.test_section is None
        assert c.remark_contains is None
        assert c.archived is None
        assert c.keep_last_per_serial is False
        assert c.only_repeat_serials is False
        assert c.sort == ()
        assert c.limit is None

    def test_full(self) -> None:
        pc = (ProductConfig("dm0608", "3", "7s-SNF1001"),)
        tr = (
            TimeRangeCriteria(
                start=_dt(2026, 6, 1),
                end=_dt(2026, 6, 10, 23, 59, 59),
            ),
        )
        sr = (SortRule("created_at", "desc"),)
        c = RecordSelectionCriteria(
            product_configs=pc,
            serial_contains="S1F",
            excess_limit_sensors=("sensor01", "sensor02"),
            excess_limit_test_names=("Std-D",),
            excess_limit_indicators=("倒频谱-0.2",),
            time_ranges=tr,
            judgement_results=("不合格",),
            manual_verdict="合格",
            record_status="合格",
            test_section=12,
            remark_contains="2321",
            archived=False,
            keep_last_per_serial=True,
            only_repeat_serials=False,
            sort=sr,
            limit=20,
        )
        assert c.product_configs == pc
        assert c.serial_contains == "S1F"
        assert c.excess_limit_sensors == ("sensor01", "sensor02")
        assert c.excess_limit_test_names == ("Std-D",)
        assert c.excess_limit_indicators == ("倒频谱-0.2",)
        assert c.time_ranges == tr
        assert c.judgement_results == ("不合格",)
        assert c.manual_verdict == "合格"
        assert c.record_status == "合格"
        assert c.test_section == 12
        assert c.remark_contains == "2321"
        assert c.archived is False
        assert c.keep_last_per_serial is True
        assert c.only_repeat_serials is False
        assert c.sort == sr
        assert c.limit == 20

    def test_excess_limit_independent_arrays(self) -> None:
        """sensors / test_names / indicators are independent arrays;
        the back-end generates the Cartesian product."""
        c = RecordSelectionCriteria(
            excess_limit_sensors=("sensor01", "sensor02"),
            excess_limit_test_names=("Std-D", "Std-E"),
            excess_limit_indicators=("倒频谱-0.2", "peak"),
        )
        assert c.excess_limit_sensors == ("sensor01", "sensor02")
        assert c.excess_limit_test_names == ("Std-D", "Std-E")
        assert c.excess_limit_indicators == ("倒频谱-0.2", "peak")
        # Can also have different lengths — back-end handles Cartesian product
        c2 = RecordSelectionCriteria(
            excess_limit_sensors=("sensor01",),
            excess_limit_indicators=("a", "b", "c"),
        )
        assert len(c2.excess_limit_sensors) == 1
        assert len(c2.excess_limit_indicators) == 3

    def test_multiple_time_ranges(self) -> None:
        tr = (
            TimeRangeCriteria(start=_dt(2026, 6, 1), end=_dt(2026, 6, 3)),
            TimeRangeCriteria(start=_dt(2026, 6, 7), end=_dt(2026, 6, 9)),
        )
        c = RecordSelectionCriteria(time_ranges=tr)
        assert len(c.time_ranges) == 2
        assert c.time_ranges[0].start == _dt(2026, 6, 1)
        assert c.time_ranges[1].start == _dt(2026, 6, 7)

    def test_single_sided_time_ranges(self) -> None:
        """start-only and end-only time ranges."""
        tr = (
            TimeRangeCriteria(start=_dt(2026, 6, 1), end=None),
            TimeRangeCriteria(start=None, end=_dt(2026, 6, 10)),
        )
        c = RecordSelectionCriteria(time_ranges=tr)
        assert c.time_ranges[0].start is not None
        assert c.time_ranges[0].end is None
        assert c.time_ranges[1].start is None
        assert c.time_ranges[1].end is not None

    def test_frozen(self) -> None:
        c = RecordSelectionCriteria()
        with pytest.raises(FrozenInstanceError):
            c.limit = 50  # type: ignore[misc]

    def test_equality(self) -> None:
        a = RecordSelectionCriteria(serial_contains="S1F")
        b = RecordSelectionCriteria(serial_contains="S1F")
        assert a == b
        assert a != RecordSelectionCriteria(serial_contains="S2F")

    def test_missing_optional_time_range(self) -> None:
        """Empty time_ranges is valid (no time filter)."""
        c = RecordSelectionCriteria()
        assert c.time_ranges == ()

    def test_excess_limit_empty_allowed(self) -> None:
        """All three excess_limit arrays are optional — empty defaults
        mean 'no excess-limit filter'."""
        c = RecordSelectionCriteria(
            excess_limit_sensors=(),
            excess_limit_test_names=(),
            excess_limit_indicators=(),
        )
        assert c.excess_limit_sensors == ()
        assert c.excess_limit_test_names == ()
        assert c.excess_limit_indicators == ()

    def test_excess_limit_sensors_only(self) -> None:
        """Only sensors specified — test_names and indicators stay empty."""
        c = RecordSelectionCriteria(
            excess_limit_sensors=("sensor01", "sensor02"),
        )
        assert c.excess_limit_sensors == ("sensor01", "sensor02")
        assert c.excess_limit_test_names == ()
        assert c.excess_limit_indicators == ()

    def test_excess_limit_indicators_only(self) -> None:
        """Only indicators specified — other fields stay empty."""
        c = RecordSelectionCriteria(
            excess_limit_indicators=("peak", "RMS"),
        )
        assert c.excess_limit_sensors == ()
        assert c.excess_limit_test_names == ()
        assert c.excess_limit_indicators == ("peak", "RMS")


# ======================================================================
# RecordSelectionCriteria — validation
# ======================================================================


class TestRecordSelectionCriteriaValidation:
    def test_limit_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="greater than zero"):
            RecordSelectionCriteria(limit=0)

    def test_limit_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="greater than zero"):
            RecordSelectionCriteria(limit=-1)

    def test_limit_positive_ok(self) -> None:
        c = RecordSelectionCriteria(limit=1)
        assert c.limit == 1

    def test_limit_none_ok(self) -> None:
        c = RecordSelectionCriteria(limit=None)
        assert c.limit is None


# ======================================================================
# RelativeSelectionReference
# ======================================================================


class TestRelativeSelectionReference:
    def test_construction_active(self) -> None:
        ref = RelativeSelectionReference(kind="active")
        assert ref.kind == "active"

    def test_frozen(self) -> None:
        ref = RelativeSelectionReference(kind="active")
        with pytest.raises(FrozenInstanceError):
            ref.kind = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = RelativeSelectionReference(kind="active")
        b = RelativeSelectionReference(kind="active")
        assert a == b
        assert hash(a) == hash(b)
