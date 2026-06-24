from __future__ import annotations


class ExcelExportDataTypeParser:
    """Parse Excel-export data types without relying on generic text boundaries."""

    options = (
        ("one_data", "一维数据", "oneData"),
        ("two_data", "二维数据", "twoData"),
        ("result_data", "结果数据", "resultData"),
    )
    _aliases = {
        "one_data": ("一维数据", "一维指标", "一维", "1d", "one data", "oneData"),
        "two_data": ("二维数据", "二维指标", "二维", "2d", "two data", "twoData"),
        "result_data": ("结果数据", "结果", "result data", "resultData"),
    }
    _all_aliases = ("全部数据", "所有数据", "全量数据", "all data")

    def parse_message(self, message: str) -> tuple[str, ...]:
        normalized = message.casefold()
        if any(alias.casefold() in normalized for alias in self._all_aliases):
            return tuple(value for value, _label, _backend_key in self.options)

        positions: dict[str, int] = {}
        for value, _label, _backend_key in self.options:
            for alias in self._aliases[value]:
                position = normalized.find(alias.casefold())
                if position >= 0:
                    positions[value] = min(position, positions.get(value, position))
        return tuple(sorted(positions, key=positions.__getitem__))

    def parse_value(self, raw_value: object) -> tuple[str, ...]:
        if raw_value is None:
            return ()
        items = raw_value if isinstance(raw_value, (list, tuple, set)) else (raw_value,)
        selected: list[str] = []
        for item in items:
            normalized = str(item).strip().casefold()
            if not normalized:
                continue
            if normalized in {alias.casefold() for alias in self._all_aliases}:
                return tuple(value for value, _label, _backend_key in self.options)
            for value, label, backend_key in self.options:
                accepted = {
                    value.casefold(),
                    label.casefold(),
                    backend_key.casefold(),
                    *(alias.casefold() for alias in self._aliases[value]),
                }
                if normalized in accepted and value not in selected:
                    selected.append(value)
                    break
        return tuple(selected)
