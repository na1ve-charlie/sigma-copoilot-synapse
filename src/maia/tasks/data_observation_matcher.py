from __future__ import annotations

from collections.abc import Iterable, Mapping

from maia.integrations.sigma.data_observation import ObservationIndicator
from maia.tasks.data_observation_models import (
    DATA_TYPE_SLOT,
    DOMAIN_OPTIONS,
    INDICATOR_SLOT,
    SENSOR_LIST_SLOT,
    TEST_NAME_LIST_SLOT,
    ObservationCandidates,
    ObservationResolution,
    ObservationWideRow,
)


class ObservationMatcher:
    def __init__(self, rows: tuple[ObservationWideRow, ...]) -> None:
        self._rows = rows

    def resolve(
        self,
        *,
        message: str,
        params: Mapping[str, object],
        include_message: bool,
    ) -> ObservationResolution:
        selected: dict[str, object] = {}
        invalid: list[str] = []

        indicator = self._indicator_param(params.get(INDICATOR_SLOT))
        if indicator is None and include_message:
            indicator = self._indicator_from_message(message)
        if indicator is not None:
            selected[INDICATOR_SLOT] = indicator.to_param()

        rows = self._filter_by_indicator(self._rows, indicator)
        data_type = self._data_type_param(params.get(DATA_TYPE_SLOT))
        if data_type is None and include_message and indicator is None:
            data_type = self._data_type_from_message(message)
        if data_type is not None:
            if data_type not in self._unique(row.data_type for row in rows):
                invalid.append(DATA_TYPE_SLOT)
            else:
                selected[DATA_TYPE_SLOT] = data_type
                rows = tuple(row for row in rows if row.data_type == data_type)

        sensors = self._text_tuple(params.get(SENSOR_LIST_SLOT))
        if not sensors and include_message:
            sensors = self._values_from_message(message, self._unique(row.sensor for row in rows))
        if sensors:
            available = set(self._unique(row.sensor for row in rows))
            if any(sensor not in available for sensor in sensors):
                invalid.append(SENSOR_LIST_SLOT)
            else:
                selected[SENSOR_LIST_SLOT] = sensors
                rows = tuple(row for row in rows if row.sensor in sensors)

        test_names = self._text_tuple(params.get(TEST_NAME_LIST_SLOT))
        if not test_names and include_message:
            test_names = self._values_from_message(message, self._unique(row.test_name for row in rows))
        if test_names:
            available = set(self._unique(row.test_name for row in rows))
            if any(test_name not in available for test_name in test_names):
                invalid.append(TEST_NAME_LIST_SLOT)
            else:
                selected[TEST_NAME_LIST_SLOT] = test_names
                rows = tuple(row for row in rows if row.test_name in test_names)

        if not rows:
            invalid_slot = self._invalid_combination_slot(selected)
            return ObservationResolution(
                params={},
                missing_slots=tuple(
                    slot
                    for slot in (DATA_TYPE_SLOT, SENSOR_LIST_SLOT, TEST_NAME_LIST_SLOT, INDICATOR_SLOT)
                    if slot != invalid_slot
                ),
                invalid_slots=(invalid_slot,),
                candidates=self._candidates(self._rows),
            )

        data_types = self._unique(row.data_type for row in rows)
        if DATA_TYPE_SLOT not in selected and len(data_types) == 1:
            selected[DATA_TYPE_SLOT] = data_types[0]
        indicators = self._unique_indicators(row.indicator for row in rows)
        if INDICATOR_SLOT not in selected and len(indicators) == 1:
            selected[INDICATOR_SLOT] = indicators[0].to_param()

        candidates = self._candidates(rows)
        missing = tuple(
            slot
            for slot in (DATA_TYPE_SLOT, SENSOR_LIST_SLOT, TEST_NAME_LIST_SLOT, INDICATOR_SLOT)
            if slot not in selected and slot not in invalid
        )
        return ObservationResolution(
            params=selected,
            missing_slots=missing,
            invalid_slots=tuple(dict.fromkeys(invalid)),
            candidates=candidates,
        )

    def _candidates(self, rows: tuple[ObservationWideRow, ...]) -> ObservationCandidates:
        return ObservationCandidates(
            data_types=self._unique(row.data_type for row in rows),
            sensors=self._unique(row.sensor for row in rows),
            test_names=self._unique(row.test_name for row in rows),
            indicators=self._unique_indicators(row.indicator for row in rows),
        )

    def _invalid_combination_slot(self, selected: Mapping[str, object]) -> str:
        for slot in (INDICATOR_SLOT, TEST_NAME_LIST_SLOT, SENSOR_LIST_SLOT, DATA_TYPE_SLOT):
            if slot in selected:
                return slot
        return DATA_TYPE_SLOT

    def _indicator_param(self, value: object) -> ObservationIndicator | None:
        if isinstance(value, Mapping):
            name = self._text(value.get("name"))
            index = self._text(value.get("index"))
            if name and index:
                candidate = ObservationIndicator(name=name, index=index)
                return candidate if candidate in self._unique_indicators(row.indicator for row in self._rows) else None
        text = self._text(value)
        if text is None:
            return None
        matches = tuple(
            indicator
            for indicator in self._unique_indicators(row.indicator for row in self._rows)
            if indicator.name == text or indicator.index == text
        )
        return matches[0] if len(matches) == 1 else None

    def _indicator_from_message(self, message: str) -> ObservationIndicator | None:
        matches = tuple(
            indicator
            for indicator in self._unique_indicators(row.indicator for row in self._rows)
            if self._contains_full_match(message, indicator.name)
        )
        return matches[0] if len(matches) == 1 else None

    def _data_type_param(self, value: object) -> str | None:
        text = self._text(value)
        if text is None:
            return None
        for data_type, label, aliases in DOMAIN_OPTIONS:
            if text == data_type or text == label or text in aliases:
                return data_type
        return None

    def _data_type_from_message(self, message: str) -> str | None:
        matches: list[str] = []
        for data_type, _label, aliases in DOMAIN_OPTIONS:
            if any(self._contains_full_match(message, alias) for alias in aliases):
                matches.append(data_type)
        ordered = tuple(dict.fromkeys(matches))
        return ordered[0] if len(ordered) == 1 else None

    def _values_from_message(self, message: str, candidates: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(value for value in candidates if self._contains_full_match(message, value))

    def _filter_by_indicator(
        self,
        rows: tuple[ObservationWideRow, ...],
        indicator: ObservationIndicator | None,
    ) -> tuple[ObservationWideRow, ...]:
        if indicator is None:
            return rows
        return tuple(row for row in rows if row.indicator == indicator)

    def _unique(self, values: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value for value in values if value))

    def _unique_indicators(self, values: Iterable[ObservationIndicator]) -> tuple[ObservationIndicator, ...]:
        result: list[ObservationIndicator] = []
        keys: set[tuple[str, str]] = set()
        for value in values:
            key = (value.name, value.index)
            if key not in keys:
                keys.add(key)
                result.append(value)
        return tuple(result)

    def _text_tuple(self, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple, set)):
            return tuple(dict.fromkeys(text for item in value if (text := self._text(item))))
        text = self._text(value)
        return () if text is None else (text,)

    def _text(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _contains_full_match(self, message: str, candidate: str) -> bool:
        haystack = message.casefold()
        needle = candidate.casefold().strip()
        start = haystack.find(needle)
        while start >= 0:
            end = start + len(needle)
            if self._left_boundary(message, start) and self._right_boundary(message, end):
                return True
            start = haystack.find(needle, start + 1)
        return False

    def _left_boundary(self, text: str, index: int) -> bool:
        return index == 0 or self._boundary_char(text[index - 1])

    def _right_boundary(self, text: str, index: int) -> bool:
        return index >= len(text) or self._boundary_char(text[index])

    def _boundary_char(self, char: str) -> bool:
        if char == "-":
            return False
        return not char.isalnum() or char in "看查观览示开要想的和及与"


__all__ = ["ObservationMatcher"]
