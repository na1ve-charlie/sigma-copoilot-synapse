from __future__ import annotations

from maia.integrations.sigma.data_observation import ObservationIndicator
from maia.tasks.data_observation import (
    DATA_TYPE_SLOT,
    INDICATOR_SLOT,
    SENSOR_LIST_SLOT,
    TEST_NAME_LIST_SLOT,
    ObservationMatcher,
    ObservationWideRow,
)


def test_observation_matcher_prefers_indicator_full_match_over_domain() -> None:
    resolution = ObservationMatcher(
        (
            _row("TWO_D_FS", "Vib1", "Spd-rDL", "频谱", "indicator-fs"),
            _row("TWO_D_CEP", "Vib2", "Spd-rCH", "倒谱", "indicator-cep"),
        )
    ).resolve(message="我要看频谱", params={}, include_message=True)

    assert resolution.params == {
        DATA_TYPE_SLOT: "TWO_D_FS",
        INDICATOR_SLOT: {"name": "频谱", "index": "indicator-fs"},
    }
    assert resolution.missing_slots == (SENSOR_LIST_SLOT, TEST_NAME_LIST_SLOT)
    assert resolution.candidates.sensors == ("Vib1",)
    assert resolution.candidates.test_names == ("Spd-rDL",)


def test_observation_matcher_does_not_substring_match_indicator_or_domain() -> None:
    matcher = ObservationMatcher(
        (
            _row("TWO_D_TD", "Vib1", "A", "均方根", "rms"),
            _row("TWO_D_FS", "Vib1", "A", "频谱", "fs"),
            _row("TWO_D_OS", "Vib1", "A", "阶次谱", "os"),
        )
    )

    assert INDICATOR_SLOT not in matcher.resolve(
        message="我要看均方根-均方根法",
        params={},
        include_message=True,
    ).params
    assert DATA_TYPE_SLOT not in matcher.resolve(
        message="我要看倒频谱",
        params={},
        include_message=True,
    ).params
    assert DATA_TYPE_SLOT not in matcher.resolve(
        message="我要看倒阶次谱",
        params={},
        include_message=True,
    ).params


def test_observation_matcher_keeps_candidate_order_after_indicator_match() -> None:
    resolution = ObservationMatcher(
        (
            _row("TWO_D_CEP", "Vib2", "B", "倒谱", "cep"),
            _row("TWO_D_CEP", "Vib1", "A", "倒谱", "cep"),
            _row("TWO_D_FS", "Vib9", "Z", "频谱", "fs"),
        )
    ).resolve(message="我要查看倒谱", params={}, include_message=True)

    assert resolution.params == {
        DATA_TYPE_SLOT: "TWO_D_CEP",
        INDICATOR_SLOT: {"name": "倒谱", "index": "cep"},
    }
    assert resolution.candidates.sensors == ("Vib2", "Vib1")
    assert resolution.candidates.test_names == ("B", "A")


def test_observation_matcher_resolves_ready_params_from_message() -> None:
    resolution = ObservationMatcher(
        (
            _row("TWO_D_OS", "Vib1", "Spd-rDL", "阶次谱", "os-index"),
            _row("TWO_D_OS", "Vib2", "Spd-rCH", "阶次谱", "os-index"),
        )
    ).resolve(message="我要查看 Vib1、Spd-rDL 的阶次谱", params={}, include_message=True)

    assert resolution.params == {
        DATA_TYPE_SLOT: "TWO_D_OS",
        SENSOR_LIST_SLOT: ("Vib1",),
        TEST_NAME_LIST_SLOT: ("Spd-rDL",),
        INDICATOR_SLOT: {"name": "阶次谱", "index": "os-index"},
    }
    assert resolution.missing_slots == ()


def test_observation_matcher_returns_full_candidates_for_invalid_combination() -> None:
    resolution = ObservationMatcher(
        (
            _row("TWO_D_CEP", "Vib1", "A", "倒谱", "cep"),
            _row("TWO_D_FS", "Vib2", "B", "频谱", "fs"),
        )
    ).resolve(
        message="",
        params={SENSOR_LIST_SLOT: ["Vib1"], TEST_NAME_LIST_SLOT: ["B"]},
        include_message=False,
    )

    assert resolution.invalid_slots == (TEST_NAME_LIST_SLOT,)
    assert resolution.candidates.sensors == ("Vib1",)
    assert resolution.candidates.test_names == ("A",)
    assert [item.name for item in resolution.candidates.indicators] == ["倒谱"]


def _row(
    data_type: str,
    sensor: str,
    test_name: str,
    indicator_name: str,
    indicator_index: str,
) -> ObservationWideRow:
    return ObservationWideRow(
        data_type=data_type,
        sensor=sensor,
        test_name=test_name,
        indicator=ObservationIndicator(name=indicator_name, index=indicator_index),
    )
