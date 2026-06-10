from __future__ import annotations

import asyncio
from types import SimpleNamespace

from synapse.domains.observation import ObservationSlotResolutionPipeline
from synapse.engine import TurnContext
from synapse.recognition import CANDIDATE_CATALOG_ARTIFACT, CandidateCatalog
from synapse.slots.committer import SLOT_STATE_ARTIFACT, SlotCommitterStep
from synapse.slots.resolution import SLOT_OPERATIONS_ARTIFACT, SlotResolutionStep
from synapse.slots.validation import SlotValidationStep
from synapse.turns import TurnRequest


def test_observation_multi_replace_keeps_all_targets_as_list() -> None:
    context = _context(
        SimpleNamespace(
            action=["replace", "replace"],
            entity_type="sensor",
            target=["sensor_1", "sensor_3"],
            slot_valid=[True, True],
        )
    )

    context = asyncio.run(
        SlotResolutionStep(ObservationSlotResolutionPipeline()).run(context)
    )
    operation = context.artifacts[SLOT_OPERATIONS_ARTIFACT][0]

    assert operation.kind == "replace"
    assert operation.value == ["sensor_1", "sensor_3"]

    context = asyncio.run(SlotValidationStep().run(context))
    context = asyncio.run(SlotCommitterStep().run(context))

    state = context.artifacts[SLOT_STATE_ARTIFACT]
    assert next(iter(state.values.values())) == ["sensor_1", "sensor_3"]


def test_themis_switch_action_maps_to_replace_operation() -> None:
    context = _context(
        SimpleNamespace(
            action="switch",
            entity_type="sensor",
            target="sensor_1",
            slot_valid=True,
        )
    )

    context = asyncio.run(
        SlotResolutionStep(ObservationSlotResolutionPipeline()).run(context)
    )
    operation = context.artifacts[SLOT_OPERATIONS_ARTIFACT][0]

    assert operation.kind == "replace"
    assert operation.value == ["sensor_1"]


def _context(operation: SimpleNamespace) -> TurnContext:
    return (
        TurnContext.from_request(
            TurnRequest(session_id="s1", message="switch")
        )
        .with_artifact(
            CANDIDATE_CATALOG_ARTIFACT,
            CandidateCatalog.from_mapping(
                {"sensors": ["sensor_1", "sensor_2", "sensor_3"]}
            ),
        )
        .with_artifact(
            "intent_decision",
            SimpleNamespace(slot_operations=(operation,)),
        )
    )
