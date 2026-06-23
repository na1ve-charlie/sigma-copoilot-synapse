from __future__ import annotations

from dataclasses import dataclass

from maia.integrations.sigma.data_observation import ObservationIndicator


DATA_OBSERVATION_INTENT = "task.nvh.data_observation.view_indicator_result"
DATA_TYPE_SLOT = "dataType"
SENSOR_LIST_SLOT = "sensorList"
TEST_NAME_LIST_SLOT = "testNameList"
INDICATOR_SLOT = "indicator"
PENDING_SELECTION_MARKER = f"__task__:{DATA_OBSERVATION_INTENT}"

DOMAIN_OPTIONS = (
    ("ONE_D", "一维指标", ("一维指标", "一维数据")),
    ("TWO_D_TD", "时间域", ("时间域", "时域", "时域数据")),
    ("TWO_D_FS", "频谱", ("频谱", "频谱图")),
    ("TWO_D_OS", "阶次谱", ("阶次谱", "阶次谱图")),
    ("TWO_D_OC", "阶次切片", ("阶次切片",)),
    ("TWO_D_CEP", "倒谱", ("倒谱",)),
    ("TWO_D_PS", "心理声学", ("心理声学",)),
)
DOMAIN_LABELS = {value: label for value, label, _ in DOMAIN_OPTIONS}


@dataclass(frozen=True)
class ObservationWideRow:
    data_type: str
    sensor: str
    test_name: str
    indicator: ObservationIndicator


@dataclass(frozen=True)
class ObservationCandidates:
    data_types: tuple[str, ...]
    sensors: tuple[str, ...]
    test_names: tuple[str, ...]
    indicators: tuple[ObservationIndicator, ...]


@dataclass(frozen=True)
class ObservationResolution:
    params: dict[str, object]
    missing_slots: tuple[str, ...]
    invalid_slots: tuple[str, ...]
    candidates: ObservationCandidates


__all__ = [
    "DATA_OBSERVATION_INTENT",
    "DATA_TYPE_SLOT",
    "DOMAIN_LABELS",
    "DOMAIN_OPTIONS",
    "INDICATOR_SLOT",
    "PENDING_SELECTION_MARKER",
    "SENSOR_LIST_SLOT",
    "TEST_NAME_LIST_SLOT",
    "ObservationCandidates",
    "ObservationResolution",
    "ObservationWideRow",
]
