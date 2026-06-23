from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from maia.integrations.sigma.data_observation import ObservationIndicator
from maia.tasks.data_observation_models import (
    DATA_TYPE_SLOT,
    DOMAIN_LABELS,
    DOMAIN_OPTIONS,
    INDICATOR_SLOT,
    SENSOR_LIST_SLOT,
    TEST_NAME_LIST_SLOT,
    ObservationCandidates,
    ObservationIndicatorOption,
    ObservationResolution,
    ObservationWideRow,
)
from maia.tasks.slot_value_resolution import MessageSlotResolver, SlotCandidate, SlotCandidateSet


_INDICATOR_HINT_PATTERNS = (
    re.compile(r"\u6307\u6807\s*[:\uff1a\u4e3a]?\s*([A-Za-z][A-Za-z0-9_.-]*)", re.IGNORECASE),
    re.compile(r"([A-Za-z][A-Za-z0-9_.-]*)\s*\u6307\u6807", re.IGNORECASE),
)


class ObservationMatcher:
    def __init__(self, rows: tuple[ObservationWideRow, ...]) -> None:
        self._rows = rows
        self._resolver = MessageSlotResolver()

    def resolve(
        self,
        *,
        message: str,
        params: Mapping[str, object],
        include_message: bool,
    ) -> ObservationResolution:
        selected: dict[str, object] = {}
        invalid: list[str] = []
        invalid_values: dict[str, tuple[str, ...]] = {}

        data_type = self._data_type_param(params.get(DATA_TYPE_SLOT))
        if data_type is None and include_message:
            data_type = self._data_type_from_message(message, self._rows)
        elif DATA_TYPE_SLOT in params and data_type is None:
            invalid.append(DATA_TYPE_SLOT)
        data_type_rows = self._filter_by_data_type(self._rows, data_type)

        indicator = self._indicator_param(params.get(INDICATOR_SLOT))
        if indicator is None and include_message:
            indicator = self._indicator_from_message(message, data_type_rows)
        elif INDICATOR_SLOT in params and indicator is None:
            invalid.append(INDICATOR_SLOT)
        if indicator is None:
            options = self._indicator_options(data_type_rows)
            if INDICATOR_SLOT in invalid:
                return ObservationResolution(
                    params=selected,
                    missing_slots=(),
                    invalid_slots=tuple(dict.fromkeys(invalid)),
                    candidates=self._candidates(data_type_rows),
                    invalid_values=invalid_values,
                )
            hint = self._unknown_indicator_hint(message, data_type_rows, include_message)
            if hint:
                invalid.append(INDICATOR_SLOT)
                invalid_values[INDICATOR_SLOT] = (hint,)
            elif len(options) == 1:
                indicator = options[0].indicator
            else:
                if data_type is not None:
                    selected[DATA_TYPE_SLOT] = data_type
                return ObservationResolution(
                    params=selected,
                    missing_slots=() if INDICATOR_SLOT in invalid else (INDICATOR_SLOT,),
                    invalid_slots=tuple(dict.fromkeys(invalid)),
                    candidates=self._candidates(data_type_rows),
                    invalid_values=invalid_values,
                )
            if indicator is None:
                if data_type is not None:
                    selected[DATA_TYPE_SLOT] = data_type
                return ObservationResolution(
                    params=selected,
                    missing_slots=(),
                    invalid_slots=tuple(dict.fromkeys(invalid)),
                    candidates=self._candidates(data_type_rows),
                    invalid_values=invalid_values,
                )

        selected[INDICATOR_SLOT] = indicator.to_param()
        indicator_rows = self._filter_by_indicator(self._rows, indicator)
        data_types = self._unique(row.data_type for row in indicator_rows)
        if DATA_TYPE_SLOT in invalid:
            return ObservationResolution(
                params=selected,
                missing_slots=(),
                invalid_slots=tuple(dict.fromkeys(invalid)),
                candidates=self._candidates(indicator_rows),
            )
        if data_type is None:
            if len(data_types) == 1:
                data_type = data_types[0]
            else:
                return ObservationResolution(
                    params=selected,
                    missing_slots=(DATA_TYPE_SLOT,),
                    invalid_slots=(),
                    candidates=self._candidates(indicator_rows),
                )
        if data_type not in data_types:
            invalid.append(DATA_TYPE_SLOT)
            return ObservationResolution(
                params=selected,
                missing_slots=(),
                invalid_slots=tuple(dict.fromkeys(invalid)),
                candidates=self._candidates(indicator_rows),
            )
        selected[DATA_TYPE_SLOT] = data_type

        base_rows = self._filter_by_data_type(indicator_rows, data_type)
        sensors = self._text_tuple(params.get(SENSOR_LIST_SLOT))
        if not sensors and include_message:
            sensors = self._values_from_message(
                message,
                SENSOR_LIST_SLOT,
                self._unique(row.sensor for row in base_rows),
                multi=True,
            )
        sensor_invalid = self._invalid_values(sensors, self._unique(row.sensor for row in base_rows))
        if sensor_invalid:
            invalid.append(SENSOR_LIST_SLOT)
            invalid_values[SENSOR_LIST_SLOT] = sensor_invalid
        elif sensors:
            selected[SENSOR_LIST_SLOT] = sensors

        test_names = self._text_tuple(params.get(TEST_NAME_LIST_SLOT))
        if not test_names and include_message:
            test_names = self._values_from_message(
                message,
                TEST_NAME_LIST_SLOT,
                self._unique(row.test_name for row in base_rows),
                multi=True,
            )
        test_invalid = self._invalid_values(test_names, self._unique(row.test_name for row in base_rows))
        if test_invalid:
            invalid.append(TEST_NAME_LIST_SLOT)
            invalid_values[TEST_NAME_LIST_SLOT] = test_invalid
        elif test_names:
            selected[TEST_NAME_LIST_SLOT] = test_names

        rows = self._filter_by_sensor_and_test(base_rows, sensors, test_names)
        if not rows and sensors and test_names and not invalid:
            invalid_slot = self._invalid_combination_slot(selected)
            return ObservationResolution(
                params=selected,
                missing_slots=(),
                invalid_slots=(invalid_slot,),
                candidates=self._candidates(base_rows),
            )

        missing = tuple(
            slot
            for slot in (SENSOR_LIST_SLOT, TEST_NAME_LIST_SLOT)
            if slot not in selected and slot not in invalid
        )
        return ObservationResolution(
            params=selected,
            missing_slots=missing,
            invalid_slots=tuple(dict.fromkeys(invalid)),
            candidates=self._candidates(base_rows),
            invalid_values=invalid_values,
        )

    def _candidates(self, rows: tuple[ObservationWideRow, ...]) -> ObservationCandidates:
        return ObservationCandidates(
            data_types=self._unique(row.data_type for row in rows),
            sensors=self._unique(row.sensor for row in rows),
            test_names=self._unique(row.test_name for row in rows),
            indicators=self._unique_indicators(row.indicator for row in rows),
            indicator_options=self._indicator_options(rows),
        )

    def _indicator_options(self, rows: tuple[ObservationWideRow, ...]) -> tuple[ObservationIndicatorOption, ...]:
        options: list[ObservationIndicatorOption] = []
        for indicator in self._unique_indicators(row.indicator for row in rows):
            data_types = self._unique(row.data_type for row in rows if row.indicator == indicator)
            options.append(ObservationIndicatorOption(indicator=indicator, data_types=data_types))
        return tuple(options)

    def _invalid_combination_slot(self, selected: Mapping[str, object]) -> str:
        for slot in (TEST_NAME_LIST_SLOT, SENSOR_LIST_SLOT, DATA_TYPE_SLOT, INDICATOR_SLOT):
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

    def _indicator_from_message(
        self,
        message: str,
        rows: tuple[ObservationWideRow, ...],
    ) -> ObservationIndicator | None:
        result = self._resolver.resolve_message(
            message,
            SlotCandidateSet(
                slot=INDICATOR_SLOT,
                candidates=tuple(
                    SlotCandidate(value=indicator, label=indicator.name, aliases=(indicator.index,))
                    for indicator in self._unique_indicators(row.indicator for row in rows)
                ),
            ),
        )
        return result.first

    def _data_type_param(self, value: object) -> str | None:
        result = self._resolver.resolve_value(value, self._data_type_candidate_set(self._rows))
        return result.first

    def _data_type_from_message(self, message: str, rows: tuple[ObservationWideRow, ...]) -> str | None:
        result = self._resolver.resolve_message(message, self._data_type_candidate_set(rows))
        return result.first

    def _data_type_candidate_set(self, rows: tuple[ObservationWideRow, ...]) -> SlotCandidateSet:
        return SlotCandidateSet(
            slot=DATA_TYPE_SLOT,
            candidates=tuple(
                SlotCandidate(
                    value=data_type,
                    label=DOMAIN_LABELS.get(data_type, data_type),
                    aliases=self._domain_aliases(data_type),
                )
                for data_type in self._unique(row.data_type for row in rows)
            ),
        )

    def _values_from_message(
        self,
        message: str,
        slot: str,
        candidates: tuple[str, ...],
        *,
        multi: bool,
    ) -> tuple[str, ...]:
        result = self._resolver.resolve_message(
            message,
            SlotCandidateSet(
                slot=slot,
                candidates=tuple(SlotCandidate(value=value, label=value) for value in candidates),
                multi=multi,
            ),
        )
        return result.matched

    def _domain_aliases(self, data_type: str) -> tuple[str, ...]:
        for value, label, aliases in DOMAIN_OPTIONS:
            if value == data_type:
                return (label, *aliases)
        return ()

    def _filter_by_indicator(
        self,
        rows: tuple[ObservationWideRow, ...],
        indicator: ObservationIndicator,
    ) -> tuple[ObservationWideRow, ...]:
        return tuple(row for row in rows if row.indicator == indicator)

    def _filter_by_data_type(
        self,
        rows: tuple[ObservationWideRow, ...],
        data_type: str | None,
    ) -> tuple[ObservationWideRow, ...]:
        return rows if data_type is None else tuple(row for row in rows if row.data_type == data_type)

    def _filter_by_sensor_and_test(
        self,
        rows: tuple[ObservationWideRow, ...],
        sensors: tuple[str, ...],
        test_names: tuple[str, ...],
    ) -> tuple[ObservationWideRow, ...]:
        return tuple(
            row
            for row in rows
            if (not sensors or row.sensor in sensors)
            and (not test_names or row.test_name in test_names)
        )

    def _unknown_indicator_hint(
        self,
        message: str,
        rows: tuple[ObservationWideRow, ...],
        include_message: bool,
    ) -> str | None:
        if not include_message:
            return None
        known = {
            text.casefold()
            for text in (
                *self._unique(row.sensor for row in rows),
                *self._unique(row.test_name for row in rows),
                *(indicator.name for indicator in self._unique_indicators(row.indicator for row in rows)),
                *(indicator.index for indicator in self._unique_indicators(row.indicator for row in rows)),
            )
        }
        for pattern in _INDICATOR_HINT_PATTERNS:
            match = pattern.search(message)
            if match and match.group(1).casefold() not in known:
                return match.group(1)
        return None

    def _invalid_values(self, values: tuple[str, ...], candidates: tuple[str, ...]) -> tuple[str, ...]:
        available = set(candidates)
        return tuple(value for value in values if value not in available)

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


__all__ = ["ObservationMatcher"]
