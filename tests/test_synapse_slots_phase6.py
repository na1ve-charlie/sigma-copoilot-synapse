from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from synapse.engine import TurnContext
from synapse.recognition import CandidateCatalog
from synapse.recognition.candidates import CANDIDATE_CATALOG_ARTIFACT
from synapse.slots.committer import (
    SLOT_STATE_ARTIFACT,
    SLOT_STATE_DIFF_ARTIFACT,
    SlotPostCommitPolicy,
    SlotCommitterStep,
)
from synapse.slots.contracts import (
    PendingSlotBundle,
    SlotCandidate,
    SlotOperation,
    SlotRef,
    SlotSchema,
)
from synapse.slots.resolution import SLOT_OPERATIONS_ARTIFACT, SlotResolutionStep
from synapse.slots.state import SlotState
from synapse.slots.themis import operations_from_decision
from synapse.slots.validation import (
    SLOT_VALIDATION_ARTIFACT,
    SlotClarifyBuilder,
    SlotValidationIssue,
    SlotValidationResult,
    SlotValidationStep,
)
from synapse.turns import TurnRequest


SENSOR = SlotRef("observation", "sensor")
METRIC = SlotRef("observation", "metric")


def test_slot_state_applies_operations_without_mutating_original() -> None:
    original = SlotState()

    updated = original.apply_all(
        (
            SlotOperation.replace(METRIC, "RMS"),
            SlotOperation.add(SENSOR, "S1"),
            SlotOperation.add(SENSOR, "S1"),
            SlotOperation.add(SENSOR, "S2"),
            SlotOperation.remove(SENSOR, "S1"),
        )
    )

    assert original.values == {}
    assert updated.get(METRIC) == "RMS"
    assert updated.get(SENSOR) == ["S2"]


def test_slot_state_diff_can_build_rollback_operations() -> None:
    original = SlotState.from_values({SENSOR: ["S1"], METRIC: "RMS"})
    updated = original.apply_all(
        (
            SlotOperation.add(SENSOR, "S2"),
            SlotOperation.clear(METRIC),
        )
    )

    diff = original.diff(updated)
    rolled_back = updated.apply_all(diff.rollback_operations())

    assert rolled_back == original
    assert [change.ref for change in diff.changes] == [METRIC, SENSOR]


def test_pending_slot_bundle_keeps_candidates_and_operations() -> None:
    schema = SlotSchema(ref=SENSOR, required=True, multi=True)
    candidate = SlotCandidate(ref=SENSOR, value="S1", confidence=0.8)
    operation = SlotOperation.add(SENSOR, "S1", source="resolver")

    bundle = PendingSlotBundle(
        operations=(operation,),
        candidates=(candidate,),
        diagnostics={"schema": schema},
    )

    assert bundle.operations == (operation,)
    assert bundle.candidates == (candidate,)
    assert bundle.diagnostics["schema"].multi is True


def test_slot_state_rejects_unknown_operation_kind() -> None:
    operation = SlotOperation(ref=SENSOR, kind="unknown")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unsupported slot operation kind"):
        SlotState().apply(operation)


def test_themis_slot_operations_map_entities_through_candidate_catalog() -> None:
    decision = SimpleNamespace(
        slot_operations=(
            SimpleNamespace(
                action=["add", "add", "add"],
                entity_type="sensor",
                target=["VibX", "VibY", "invalid"],
                slot_valid=[True, True, False],
            ),
            SimpleNamespace(
                action="replace",
                entity_type="indicator",
                target="RMS",
                slot_valid=True,
            ),
        )
    )
    catalog = CandidateCatalog.from_mapping(
        {
            "sensors": ["VibX", "VibY"],
            "indicator_names": ["RMS"],
        }
    )

    state = SlotState().apply_all(
        operations_from_decision(decision, catalog=catalog)
    )

    assert state.get(SlotRef("recognition", "sensors")) == ["VibX", "VibY"]
    assert state.get(SlotRef("recognition", "indicator_names")) == "RMS"


def test_themis_slot_operations_infer_entity_type_from_context_intent() -> None:
    decision = SimpleNamespace(
        slot_operations=(
            SimpleNamespace(
                intent="task.nvh.context_management.switch_sensor",
                action="replace",
                entity_type="",
                target="sensor01",
                slot_valid=True,
            ),
        )
    )
    catalog = CandidateCatalog.from_mapping({"sensors": ["sensor01"]})

    operations = operations_from_decision(decision, catalog=catalog)

    assert operations[0].ref == SlotRef("recognition", "sensors")
    assert operations[0].value == "sensor01"


def test_themis_slot_operations_prefer_target_candidate_entity() -> None:
    decision = SimpleNamespace(
        slot_operations=(
            SimpleNamespace(
                intent="task.nvh.context_management.switch_sensor",
                action="replace",
                entity_type="",
                target="Spd-rDL",
                slot_valid=True,
            ),
        )
    )
    catalog = CandidateCatalog.from_mapping(
        {
            "sensors": ["sensor01"],
            "test_segments": ["Spd-rDL"],
        }
    )

    operations = operations_from_decision(decision, catalog=catalog)

    assert operations[0].ref == SlotRef("recognition", "test_segments")


def test_slot_resolution_validation_and_commit_steps_expose_diff() -> None:
    context = (
        TurnContext.from_request(
            TurnRequest(session_id="s1", message="switch")
        )
        .with_artifact(
            CANDIDATE_CATALOG_ARTIFACT,
            CandidateCatalog.from_mapping({"sensors": ["VibX"]}),
        )
        .with_artifact(
            "intent_decision",
            SimpleNamespace(
                slot_operations=(
                    SimpleNamespace(
                        action="add",
                        entity_type="sensor",
                        target="VibX",
                        slot_valid=True,
                    ),
                )
            ),
        )
    )

    context = asyncio.run(SlotResolutionStep().run(context))
    assert context.artifacts[SLOT_OPERATIONS_ARTIFACT][0].ref.name == "sensors"

    context = asyncio.run(SlotValidationStep().run(context))
    validation = context.artifacts[SLOT_VALIDATION_ARTIFACT]
    assert isinstance(validation, SlotValidationResult)
    assert validation.valid is True

    context = asyncio.run(SlotCommitterStep().run(context))
    state = context.artifacts[SLOT_STATE_ARTIFACT]
    diff = context.artifacts[SLOT_STATE_DIFF_ARTIFACT]

    assert state.get(SlotRef("recognition", "sensors")) == ["VibX"]
    assert [change.ref.name for change in diff.changes] == ["sensors"]


def test_slot_clarify_builder_uses_chinese_slot_labels_and_messages() -> None:
    result = SlotValidationResult(
        issues=(
            SlotValidationIssue(
                ref=SlotRef("recognition", "sensors"),
                value="bad",
                reason="candidate_not_found",
                candidates=("VibX",),
            ),
        )
    )

    plan = SlotClarifyBuilder().build(result)

    assert plan.message == "当前参数值无效。"
    assert plan.prompts[0].model_dump(mode="json") == {
        "id": "sensors",
        "target": "slot",
        "label": "传感器",
        "message": "请选择传感器。",
        "required": True,
        "input_type": "multi_select",
        "candidates": [
            {
                "value": "VibX",
                "label": "VibX",
                "description": None,
                "disabled": False,
            }
        ],
    }


class _MetricAutofillPolicy(SlotPostCommitPolicy):
    async def operations_for(
        self,
        *,
        state: SlotState,
        context: TurnContext,
        operations: tuple[SlotOperation, ...],
    ) -> tuple[SlotOperation, ...]:
        _ = (state, context, operations)
        return (SlotOperation.replace(METRIC, "RMS", source="autofill"),)


def test_slot_committer_applies_post_commit_policy_updates() -> None:
    context = (
        TurnContext.from_request(
            TurnRequest(session_id="s1", message="switch")
        )
        .with_artifact(
            SLOT_VALIDATION_ARTIFACT,
            SlotValidationResult(),
        )
        .with_artifact(
            SLOT_OPERATIONS_ARTIFACT,
            (SlotOperation.add(SENSOR, "S1", source="resolver"),),
        )
    )

    context = asyncio.run(
        SlotCommitterStep(post_commit_policy=_MetricAutofillPolicy()).run(context)
    )
    state = context.artifacts[SLOT_STATE_ARTIFACT]
    diff = context.artifacts[SLOT_STATE_DIFF_ARTIFACT]

    assert state.get(SENSOR) == ["S1"]
    assert state.get(METRIC) == "RMS"
    assert [change.ref for change in diff.changes] == [METRIC, SENSOR]
