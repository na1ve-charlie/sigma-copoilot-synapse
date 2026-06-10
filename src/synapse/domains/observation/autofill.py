from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from synapse.domains.observation.catalog import ObservationCatalog
from synapse.domains.observation.sigma_catalog import SigmaObservationCatalogSource
from synapse.engine import TurnContext
from synapse.planning.planner import DECISION_ARTIFACT
from synapse.slots.contracts import SlotOperation, SlotRef
from synapse.slots.state import SlotState


DOMAIN_ID = "nvh.data_observation"
DATA_TYPES_REF = SlotRef(DOMAIN_ID, "data_types")
SENSORS_REF = SlotRef(DOMAIN_ID, "sensors")
TEST_SEGMENTS_REF = SlotRef(DOMAIN_ID, "test_segments")
INDICATORS_REF = SlotRef(DOMAIN_ID, "indicator_names")

PREFERRED_INDICATOR_ALIASES = {
    "TWO_D_TD": ("时间域", "时域", "Time Domain"),
    "TWO_D_FS": ("频谱", "Frequency Spectrum", "Spectrum"),
    "TWO_D_OS": ("阶次谱", "Order Spectrum"),
    "TWO_D_CEP": ("倒阶次谱", "倒频谱", "倒谱", "Cepstrum"),
}


@dataclass(frozen=True, slots=True)
class ObservationAutofillPolicy:
    catalog_source: SigmaObservationCatalogSource
    action_name_by_intent: Mapping[str, str]
    data_types_by_task_name: Mapping[str, Sequence[str]]
    decision_artifact: str = DECISION_ARTIFACT

    async def operations_for(
        self,
        *,
        state: SlotState,
        context: TurnContext,
        operations: tuple[SlotOperation, ...],
    ) -> tuple[SlotOperation, ...]:
        action_name = _action_name_for_decision(
            context.artifacts.get(self.decision_artifact),
            self.action_name_by_intent,
        )
        if action_name is None:
            return ()
        availability = await self.catalog_source.load_availability_catalog(
            context.request
        )
        if availability.is_empty:
            return ()

        touched = {
            operation.ref.name
            for operation in operations
            if operation.ref.domain_id == DOMAIN_ID
        }
        current = state
        updates: list[SlotOperation] = []

        current = _append(
            updates,
            current,
            _data_type_operation(
                availability=availability,
                state=current,
                touched=touched,
                action_name=action_name,
                data_types_by_task_name=self.data_types_by_task_name,
            ),
        )
        current = _append(
            updates,
            current,
            _default_multi_value(
                availability=availability,
                state=current,
                touched=touched,
                slot_name="sensors",
                ref=SENSORS_REF,
            ),
        )
        current = _append(
            updates,
            current,
            _default_multi_value(
                availability=availability,
                state=current,
                touched=touched,
                slot_name="test_segments",
                ref=TEST_SEGMENTS_REF,
            ),
        )
        current = _append(
            updates,
            current,
            await _indicator_operation(
                catalog_source=self.catalog_source,
                context=context,
                state=current,
                touched=touched,
            ),
        )
        return tuple(updates)


def _data_type_operation(
    *,
    availability: ObservationCatalog,
    state: SlotState,
    touched: set[str],
    action_name: str,
    data_types_by_task_name: Mapping[str, Sequence[str]],
) -> SlotOperation | None:
    if "data_types" in touched:
        return None
    preferred = _single_value(data_types_by_task_name.get(action_name, ()))
    if preferred:
        return (
            None
            if _slot_value(state, "data_types") == preferred
            else SlotOperation.replace(
                DATA_TYPES_REF,
                preferred,
                source="observation_autofill",
            )
        )
    if _slot_value(state, "data_types"):
        return None
    value = _first_scoped_value(availability, state, "data_types")
    if value is None:
        return None
    return SlotOperation.replace(
        DATA_TYPES_REF,
        value,
        source="observation_autofill",
    )


def _default_multi_value(
    *,
    availability: ObservationCatalog,
    state: SlotState,
    touched: set[str],
    slot_name: str,
    ref: SlotRef,
) -> SlotOperation | None:
    if slot_name in touched or _slot_values(state, slot_name):
        return None
    value = _first_scoped_value(availability, state, slot_name)
    if value is None:
        return None
    return SlotOperation.replace(
        ref,
        [value],
        source="observation_autofill",
    )


async def _indicator_operation(
    *,
    catalog_source: SigmaObservationCatalogSource,
    context: TurnContext,
    state: SlotState,
    touched: set[str],
) -> SlotOperation | None:
    if "indicator_names" in touched:
        return None
    domain = _slot_value(state, "data_types")
    sensors = tuple(_slot_values(state, "sensors"))
    test_segments = tuple(_slot_values(state, "test_segments"))
    if not domain or not sensors or not test_segments:
        return None

    current = _slot_values(state, "indicator_names")
    if _matches_preferred_alias(current, domain):
        return None

    catalog = await catalog_source.load_indicator_catalog(
        context.request,
        domain=domain,
        sensors=sensors,
        test_segments=test_segments,
    )
    values = tuple(catalog.values("indicator_names"))
    if not values:
        return None

    preferred = _preferred_indicator_name(catalog, domain)
    if preferred is not None:
        if current == [preferred]:
            return None
        return SlotOperation.replace(
            INDICATORS_REF,
            [preferred],
            source="observation_autofill",
        )
    if not current or any(value not in values for value in current):
        return SlotOperation.replace(
            INDICATORS_REF,
            [values[0]],
            source="observation_autofill",
        )
    return None


def _preferred_indicator_name(
    catalog: ObservationCatalog,
    data_type: str,
) -> str | None:
    aliases = PREFERRED_INDICATOR_ALIASES.get(data_type, ())
    values = catalog.values("indicator_names")
    for alias in aliases:
        for value in values:
            if value.casefold() == alias.casefold():
                return value
    return None


def _matches_preferred_alias(values: Sequence[str], data_type: str) -> bool:
    if len(values) != 1:
        return False
    return any(
        values[0].casefold() == alias.casefold()
        for alias in PREFERRED_INDICATOR_ALIASES.get(data_type, ())
    )


def _first_scoped_value(
    availability: ObservationCatalog,
    state: SlotState,
    slot_name: str,
) -> str | None:
    scoped = availability
    data_type = _slot_value(state, "data_types")
    sensors = _slot_values(state, "sensors")
    test_segments = _slot_values(state, "test_segments")

    if slot_name != "data_types" and data_type:
        scoped = scoped.where(data_types=(data_type,))
    if slot_name != "sensors" and sensors:
        scoped = scoped.where(sensors=sensors)
    if slot_name != "test_segments" and test_segments:
        scoped = scoped.where(test_segments=test_segments)

    values = scoped.values(slot_name)
    return values[0] if values else None


def _slot_value(state: SlotState, slot_name: str) -> str | None:
    values = _slot_values(state, slot_name)
    if len(values) != 1:
        return None
    return values[0]


def _slot_values(state: SlotState, slot_name: str) -> list[str]:
    for ref, value in state.values.items():
        if ref.domain_id != DOMAIN_ID or ref.name != slot_name:
            continue
        if isinstance(value, str):
            return [value]
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [str(item) for item in value if item is not None]
        return [str(value)]
    return []


def _append(
    operations: list[SlotOperation],
    state: SlotState,
    operation: SlotOperation | None,
) -> SlotState:
    if operation is None:
        return state
    operations.append(operation)
    return state.apply(operation)


def _action_name_for_decision(
    decision: object,
    action_name_by_intent: Mapping[str, str],
) -> str | None:
    for intent in getattr(decision, "action_intents", ()) or ():
        name = _intent_name(intent)
        if not name:
            continue
        action_name = action_name_by_intent.get(name)
        if action_name:
            return action_name
    return None


def _intent_name(intent: object) -> str | None:
    if isinstance(intent, str):
        return intent
    if isinstance(intent, Mapping):
        value = intent.get("name")
        return value if isinstance(value, str) else None
    value = getattr(intent, "name", None)
    return value if isinstance(value, str) else None


def _single_value(values: Sequence[Any]) -> str | None:
    normalized = tuple(dict.fromkeys(str(value) for value in values if value))
    if len(normalized) != 1:
        return None
    return normalized[0]
