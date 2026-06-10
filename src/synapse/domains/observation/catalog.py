from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ObservationCatalogEntry:
    sensors: str | None = None
    test_segments: str | None = None
    indicator_names: str | None = None
    data_types: str | None = None


class ObservationCatalog:
    def __init__(self, entries: Sequence[ObservationCatalogEntry]) -> None:
        self._entries = tuple(entries)

    @property
    def is_empty(self) -> bool:
        return not self._entries

    def distinct_entries(self, slot_names: Sequence[str]) -> list[dict[str, Any]]:
        seen: set[tuple[Any, ...]] = set()
        result = []
        for entry in self._entries:
            projected = {
                slot_name: value
                for slot_name, value in asdict(entry).items()
                if slot_name in slot_names and value is not None
            }
            key = tuple(projected.get(slot_name) for slot_name in slot_names)
            if key in seen:
                continue
            seen.add(key)
            result.append(projected)
        return result

    def values(self, slot_name: str) -> list[str]:
        return list(
            dict.fromkeys(
                str(value)
                for entry in self._entries
                if (value := getattr(entry, slot_name)) is not None
            )
        )

    def where(
        self,
        *,
        data_types: Sequence[str] | None = None,
        sensors: Sequence[str] | None = None,
        test_segments: Sequence[str] | None = None,
        indicator_names: Sequence[str] | None = None,
    ) -> "ObservationCatalog":
        scopes = {
            "data_types": set(data_types or ()),
            "sensors": set(sensors or ()),
            "test_segments": set(test_segments or ()),
            "indicator_names": set(indicator_names or ()),
        }
        return ObservationCatalog(
            tuple(
                entry
                for entry in self._entries
                if _matches_scopes(entry, scopes)
            )
        )


def facets_for(
    entries: Sequence[dict[str, Any]],
    slot_names: Sequence[str],
) -> list[dict[str, Any]]:
    if not entries:
        return []
    return [
        {
            "slot_name": slot_name,
            "candidates": list(dict.fromkeys(
                entry[slot_name] for entry in entries if slot_name in entry
            )),
        }
        for slot_name in slot_names
    ]


def _matches_scopes(
    entry: ObservationCatalogEntry,
    scopes: dict[str, set[str]],
) -> bool:
    for slot_name, values in scopes.items():
        current = getattr(entry, slot_name)
        if values and current is not None and current not in values:
            return False
    return True
