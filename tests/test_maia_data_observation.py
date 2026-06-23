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
        params={
            INDICATOR_SLOT: {"name": "倒谱", "index": "cep"},
            SENSOR_LIST_SLOT: ["Vib1"],
            TEST_NAME_LIST_SLOT: ["B"],
        },
        include_message=False,
    )

    assert resolution.invalid_slots == (TEST_NAME_LIST_SLOT,)
    assert resolution.candidates.sensors == ("Vib1",)
    assert resolution.candidates.test_names == ("A",)
    assert [item.name for item in resolution.candidates.indicators] == ["倒谱"]


def test_observation_matcher_prompts_indicator_before_sensor_and_test_names() -> None:
    resolution = ObservationMatcher(
        (
            _row("TWO_D_OS", "Mic1", "constant1", "48Ord", "48-one"),
            _row("TWO_D_OS", "Mic2", "constant2", "72Ord", "72-one"),
            _row("TWO_D_FS", "Mic9", "constant9", "频谱", "fs"),
        )
    ).resolve(message="我要看阶次谱", params={}, include_message=True)

    assert resolution.params == {DATA_TYPE_SLOT: "TWO_D_OS"}
    assert resolution.missing_slots == (INDICATOR_SLOT,)
    assert [item.name for item in resolution.candidates.indicators] == ["48Ord", "72Ord"]


def test_observation_matcher_prompts_data_type_only_for_same_indicator_in_multiple_domains() -> None:
    resolution = ObservationMatcher(
        (
            _row("ONE_D", "Mic1", "constant1", "48Ord", "48"),
            _row("TWO_D_OC", "Mic1", "constant1", "48Ord", "48"),
        )
    ).resolve(message="我要看48Ord", params={}, include_message=True)

    assert resolution.params == {INDICATOR_SLOT: {"name": "48Ord", "index": "48"}}
    assert resolution.missing_slots == (DATA_TYPE_SLOT,)
    assert resolution.candidates.data_types == ("ONE_D", "TWO_D_OC")


def test_observation_matcher_uses_selected_indicator_domain_for_sensor_and_test_candidates() -> None:
    resolution = ObservationMatcher(
        (
            _row("TWO_D_OS", "Mic1", "constant1", "48Ord", "48"),
            _row("TWO_D_OS", "Mic2", "constant2", "48Ord", "48"),
            _row("ONE_D", "Mic9", "constant9", "48Ord", "48"),
            _row("TWO_D_OS", "Mic3", "constant3", "72Ord", "72"),
        )
    ).resolve(
        message="我要看48Ord阶次谱",
        params={},
        include_message=True,
    )

    assert resolution.params == {
        DATA_TYPE_SLOT: "TWO_D_OS",
        INDICATOR_SLOT: {"name": "48Ord", "index": "48"},
    }
    assert resolution.missing_slots == (SENSOR_LIST_SLOT, TEST_NAME_LIST_SLOT)
    assert resolution.candidates.sensors == ("Mic1", "Mic2")
    assert resolution.candidates.test_names == ("constant1", "constant2")


def test_observation_matcher_reports_unknown_explicit_indicator() -> None:
    resolution = ObservationMatcher(
        (
            _row("TWO_D_OS", "Mic1", "constant1", "48Ord", "48"),
            _row("TWO_D_OS", "Mic2", "constant2", "72Ord", "72"),
        )
    ).resolve(message="我要看Missing1指标", params={}, include_message=True)

    assert resolution.invalid_slots == (INDICATOR_SLOT,)
    assert resolution.invalid_values == {INDICATOR_SLOT: ("Missing1",)}
    assert [item.name for item in resolution.candidates.indicators] == ["48Ord", "72Ord"]


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
