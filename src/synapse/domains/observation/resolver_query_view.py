from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from synapse.domains.observation.catalog import ObservationCatalog
from synapse.slots.state import SlotState


_SLOT_LABELS = {
    "data_types": "指标域",
    "sensors": "传感器",
    "test_segments": "测试段",
    "indicator_names": "指标",
}


class _ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FacetView(_ViewModel):
    slot_name: str
    label: str
    candidates: list[str] = Field(default_factory=list)
    selected: list[str] = Field(default_factory=list)


class IndicatorFacetView(FacetView):
    candidate_domains: dict[str, list[str]] = Field(default_factory=dict)


def build_resolver_query_facets(
    catalog: ObservationCatalog,
    slot_names: Sequence[str],
    slot_state: SlotState,
) -> list[dict[str, Any]]:
    indicator_domains = _candidate_domains_from_entries(
        catalog.distinct_entries(("indicator_names", "data_types"))
    )
    facets = []
    for slot_name in slot_names:
        candidates = catalog.values(slot_name)
        selected = _slot_values(slot_state, slot_name)
        if slot_name == "indicator_names":
            facets.append(
                IndicatorFacetView(
                    slot_name=slot_name,
                    label=slot_label(slot_name),
                    candidates=candidates,
                    selected=selected,
                    candidate_domains={
                        candidate: indicator_domains.get(candidate, [])
                        for candidate in candidates
                    },
                ).model_dump(mode="json")
            )
            continue
        facets.append(
            FacetView(
                slot_name=slot_name,
                label=slot_label(slot_name),
                candidates=candidates,
                selected=selected,
            ).model_dump(mode="json")
        )
    return facets


def slot_label(slot_name: str) -> str:
    return _SLOT_LABELS.get(slot_name, slot_name)


def _candidate_domains_from_entries(
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    domains: dict[str, list[str]] = {}
    for entry in entries:
        indicator_name = _entry_value(entry, "indicator_names")
        data_type = _entry_value(entry, "data_types")
        if indicator_name is None or data_type is None:
            continue
        current = domains.setdefault(indicator_name, [])
        if data_type not in current:
            current.append(data_type)
    return domains


def _entry_value(entry: Mapping[str, Any], slot_name: str) -> str | None:
    value = entry.get(slot_name)
    if value is None:
        return None
    return str(value)


def _slot_values(state: SlotState, slot_name: str) -> list[str]:
    for ref, value in state.values.items():
        if ref.name != slot_name:
            continue
        if isinstance(value, str):
            return [value]
        if isinstance(value, Sequence) and not isinstance(value, bytes):
            return [str(item) for item in value]
        return [str(value)]
    return []
