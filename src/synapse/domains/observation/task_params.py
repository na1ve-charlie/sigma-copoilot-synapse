from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Never

from synapse.planning.planner import PlanningContext
from synapse.planning.tasks import TaskDefinition
from synapse.recognition import CANDIDATE_CATALOG_ARTIFACT, CandidateCatalog, CandidateItem
from synapse.slots.state import SlotState


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ObservationTaskParamProvider:
    data_types_by_task_name: Mapping[str, Sequence[str]]
    param_name: str = "data_types"

    def params_for(
        self,
        task: TaskDefinition,
        context: PlanningContext,
    ) -> Mapping[str, Any]:
        if task.name not in self.data_types_by_task_name:
            return {}
        selected = _slot_value(context.slot_state, self.param_name)
        data_type = selected or _single_data_type(
            self.data_types_by_task_name.get(task.name, ())
        )
        params: dict[str, Any] = {}
        if data_type is not None:
            params[self.param_name] = data_type

        indicator_names = _slot_values(context.slot_state, "indicator_names")
        if not indicator_names:
            return params
        if data_type is None:
            _raise_indicator_index_error(
                task.name,
                indicator_names[0],
                None,
                "data type is unavailable",
            )

        catalog = context.artifacts.get(CANDIDATE_CATALOG_ARTIFACT)
        if not isinstance(catalog, CandidateCatalog):
            _raise_indicator_index_error(
                task.name,
                indicator_names[0],
                data_type,
                "candidate catalog is unavailable",
            )
        params["indicator_names"] = [
            {
                "name": name,
                "index": _indicator_index(
                    catalog,
                    task_name=task.name,
                    indicator_name=name,
                    data_type=data_type,
                ),
            }
            for name in dict.fromkeys(indicator_names)
        ]
        return params


def _single_data_type(values: Sequence[str]) -> str | None:
    normalized = tuple(dict.fromkeys(str(value) for value in values if value))
    if len(normalized) != 1:
        return None
    return normalized[0]


def _slot_value(state: SlotState, slot_name: str) -> str | None:
    values = _slot_values(state, slot_name)
    return values[0] if len(values) == 1 else None


def _slot_values(state: SlotState, slot_name: str) -> list[str]:
    for ref, value in state.values.items():
        if ref.name != slot_name:
            continue
        if isinstance(value, str) and value:
            return [value]
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [str(item) for item in value if item not in (None, "")]
    return []


def _indicator_index(
    catalog: CandidateCatalog,
    *,
    task_name: str,
    indicator_name: str,
    data_type: str,
) -> str:
    candidates = [
        item
        for item in catalog.candidates_for_entity("indicator_names")
        if indicator_name in {item.value, item.label}
    ]
    indexes = []
    for candidate in candidates:
        indexes.extend(_indexes_for_data_type(candidate, data_type))
    indexes = list(dict.fromkeys(indexes))
    if len(indexes) == 1:
        return indexes[0]
    reason = "index is missing" if not indexes else f"indexes conflict: {indexes}"
    _raise_indicator_index_error(task_name, indicator_name, data_type, reason)


def _indexes_for_data_type(candidate: CandidateItem, data_type: str) -> list[str]:
    indexes = _mapping(candidate.metadata.get("indexes_by_data_type"))
    conflicts = _mapping(candidate.metadata.get("index_conflicts_by_data_type"))
    values = _as_strings(indexes.get(data_type))
    values.extend(_as_strings(conflicts.get(data_type)))
    return list(dict.fromkeys(values))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value if item not in (None, "")]
    return []


def _raise_indicator_index_error(
    task_name: str,
    indicator_name: str,
    data_type: str | None,
    reason: str,
) -> Never:
    message = (
        "cannot build observation task params: "
        f"task={task_name}, indicator={indicator_name}, "
        f"data_type={data_type}, reason={reason}"
    )
    logger.error(message)
    raise ValueError(message)
