from __future__ import annotations

from datetime import datetime

import pytest

from maia.recognition import normalization as normalization_module


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("NG", "不合格"),
        ("FAIL", "不合格"),
        ("OK", "合格"),
        ("PASS", "合格"),
        ("不合格", "不合格"),
    ],
)
def test_normalize_entity_target_maps_summary_result_aliases_to_canonical_values(
    raw: str,
    expected: str,
) -> None:
    assert normalization_module.normalize_entity_target("summary_result", raw) == expected


def test_normalize_entity_target_expands_recent_time_range_to_absolute_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        normalization_module,
        "_now",
        lambda: datetime(2026, 6, 12, 15, 30, 0),
    )

    assert normalization_module.normalize_entity_target("time_range", "最近1周") == (
        "start=2026-06-05 15:30:00; end=2026-06-12 15:30:00"
    )


def test_normalize_entity_target_keeps_date_only_upper_bound_at_midnight() -> None:
    assert normalization_module.normalize_entity_target("time_range", "2026-06-12前") == (
        "end=2026-06-12 00:00:00"
    )


def test_normalize_entity_target_normalizes_latest_n_to_positive_integer_text() -> None:
    assert normalization_module.normalize_entity_target("latest_n", " 05 ") == "5"
