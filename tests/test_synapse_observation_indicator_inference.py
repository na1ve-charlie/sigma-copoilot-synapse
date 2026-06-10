from __future__ import annotations

import asyncio

from themis import IntentDecision, IntentMatch, IntentSlot, RecognitionVerdict

from synapse.domains.observation.indicator_inference import (
    ObservationIndicatorInferenceStep,
)
from synapse.domains.observation.pending_request import (
    PENDING_OBSERVATION_REQUEST_ARTIFACT,
    PendingObservationRequest,
)
from synapse.engine import TurnContext
from synapse.planning.planner import DECISION_ARTIFACT
from synapse.recognition import CANDIDATE_CATALOG_ARTIFACT, CandidateCatalog
from synapse.turns import TurnRequest


def test_step_infers_task_intent_and_replace_slot_from_unique_indicator_domain() -> None:
    context = TurnContext.from_request(TurnRequest(session_id="s1", message="show"))
    context = context.with_artifact(DECISION_ARTIFACT, _decision(
        IntentMatch(
            name="task.nvh.data_observation.indicator_query.list",
            score=0.95,
            slots=IntentSlot(entity_type="indicator", target="RMS"),
        )
    )).with_artifact(
        CANDIDATE_CATALOG_ARTIFACT,
        CandidateCatalog.from_mapping(
            {
                "indicator_names": [
                    {"value": "RMS", "metadata": {"data_types": ["ONE_D"]}},
                ],
                "data_types": [{"value": "ONE_D", "label": "one dimensional data"}],
            }
        ),
    )

    updated = run(
        ObservationIndicatorInferenceStep(
            task_intent_by_data_type={"ONE_D": "task.nvh.data_observation.batch.one_dim_data"},
            data_types_by_task_intent={
                "task.nvh.data_observation.batch.one_dim_data": ("ONE_D",),
            },
        ).run(context)
    )
    decision = updated.artifacts[DECISION_ARTIFACT]

    assert updated.plan is None
    assert [intent.name for intent in decision.action_intents] == [
        "task.nvh.data_observation.batch.one_dim_data"
    ]
    assert len(decision.slot_operations) == 1
    assert decision.slot_operations[0].entity_type == "indicator"
    assert decision.slot_operations[0].action == "replace"
    assert decision.slot_operations[0].target == "RMS"
    assert "pending_task" not in updated.artifacts
    assert "active_task" not in updated.artifacts


def test_step_returns_clarify_plan_with_data_type_candidates_for_conflict() -> None:
    context = TurnContext.from_request(TurnRequest(session_id="s1", message="show"))
    context = context.with_artifact(DECISION_ARTIFACT, _decision(
        IntentMatch(
            name="task.nvh.data_observation.indicator_query.list",
            score=0.95,
            slots=IntentSlot(entity_type="indicator", target="48阶"),
        )
    )).with_artifact(
        CANDIDATE_CATALOG_ARTIFACT,
        CandidateCatalog.from_mapping(
            {
                "indicator_names": [
                    {
                        "value": "48阶",
                        "metadata": {"data_types": ["ONE_D", "TWO_D_OC"]},
                    },
                ],
                "data_types": [
                    {"value": "ONE_D", "label": "one dimensional data"},
                    {"value": "TWO_D_OC", "label": "order slice"},
                ],
            }
        ),
    )

    updated = run(
        ObservationIndicatorInferenceStep(
            task_intent_by_data_type={
                "ONE_D": "task.nvh.data_observation.batch.one_dim_data",
                "TWO_D_OC": "task.nvh.data_observation.batch.order_slice",
            },
            data_types_by_task_intent={
                "task.nvh.data_observation.batch.one_dim_data": ("ONE_D",),
                "task.nvh.data_observation.batch.order_slice": ("TWO_D_OC",),
            },
        ).run(context)
    )

    assert updated.plan is not None
    assert updated.plan["kind"] == "clarify"
    assert updated.plan["reason"] == "ambiguous_slots"
    assert updated.plan["message"] == "当前数据类型存在歧义。"
    assert updated.plan["missing_slots"] == ["data_types"]
    assert updated.plan["prompts"][0]["id"] == "data_types"
    assert updated.plan["prompts"][0]["label"] == "数据类型"
    assert updated.plan["prompts"][0]["message"] == "请选择数据类型。"
    assert [item["value"] for item in updated.plan["prompts"][0]["candidates"]] == [
        "ONE_D",
        "TWO_D_OC",
    ]
    assert updated.artifacts[PENDING_OBSERVATION_REQUEST_ARTIFACT] == (
        PendingObservationRequest(
            indicator_name="48阶",
            candidate_data_types=("ONE_D", "TWO_D_OC"),
            intent_name="task.nvh.data_observation.indicator_query.list",
            score=0.95,
        )
    )


def test_step_restores_pending_indicator_after_data_type_selection() -> None:
    pending = PendingObservationRequest(
        indicator_name="48阶",
        candidate_data_types=("ONE_D", "TWO_D_OC"),
        intent_name="task.nvh.data_observation.indicator_query.list",
        score=0.95,
    )
    context = TurnContext.from_request(TurnRequest(session_id="s1", message="TWO_D_OC"))
    context = (
        context.with_artifact(
            DECISION_ARTIFACT,
            _decision(
                IntentMatch(
                    name="task.nvh.context_management.switch_data_type",
                    score=0.98,
                    slots=IntentSlot(
                        action="replace",
                        entity_type="data_type",
                        target="TWO_D_OC",
                        slot_valid=True,
                    ),
                )
            ),
        )
        .with_artifact(PENDING_OBSERVATION_REQUEST_ARTIFACT, pending)
        .with_artifact(
            CANDIDATE_CATALOG_ARTIFACT,
            CandidateCatalog.from_mapping(
                {
                    "indicator_names": [
                        {
                            "value": "48阶",
                            "metadata": {"data_types": ["ONE_D", "TWO_D_OC"]},
                        },
                    ],
                    "data_types": [
                        {"value": "ONE_D", "label": "一维数据"},
                        {"value": "TWO_D_OC", "label": "阶次切片"},
                    ],
                }
            ),
        )
    )

    updated = run(
        ObservationIndicatorInferenceStep(
            task_intent_by_data_type={
                "ONE_D": "task.nvh.data_observation.batch.one_dim_data",
                "TWO_D_OC": "task.nvh.data_observation.batch.order_slice",
            },
            data_types_by_task_intent={
                "task.nvh.data_observation.batch.one_dim_data": ("ONE_D",),
                "task.nvh.data_observation.batch.order_slice": ("TWO_D_OC",),
            },
        ).run(context)
    )
    decision = updated.artifacts[DECISION_ARTIFACT]

    assert updated.plan is None
    assert [intent.name for intent in decision.action_intents] == [
        "task.nvh.data_observation.batch.order_slice"
    ]
    assert {
        (operation.entity_type, operation.action, operation.target)
        for operation in decision.slot_operations
    } == {
        ("data_type", "replace", "TWO_D_OC"),
        ("indicator", "replace", "48阶"),
    }


def test_step_prefers_explicit_observation_scope_over_indicator_conflict() -> None:
    context = TurnContext.from_request(TurnRequest(session_id="s1", message="show"))
    context = context.with_artifact(DECISION_ARTIFACT, _decision(
        IntentMatch(
            name="task.nvh.data_observation.batch.order_slice",
            score=0.95,
            slots=IntentSlot(entity_type="data_type", target="TWO_D_OC"),
        ),
        IntentMatch(
            name="task.nvh.data_observation.indicator_query.list",
            score=0.95,
            slots=IntentSlot(entity_type="indicator", target="48阶"),
        ),
    )).with_artifact(
        CANDIDATE_CATALOG_ARTIFACT,
        CandidateCatalog.from_mapping(
            {
                "indicator_names": [
                    {
                        "value": "48阶",
                        "metadata": {"data_types": ["ONE_D", "TWO_D_OC"]},
                    },
                ],
                "data_types": [
                    {"value": "ONE_D", "label": "one dimensional data"},
                    {"value": "TWO_D_OC", "label": "order slice"},
                ],
            }
        ),
    )

    updated = run(
        ObservationIndicatorInferenceStep(
            task_intent_by_data_type={
                "ONE_D": "task.nvh.data_observation.batch.one_dim_data",
                "TWO_D_OC": "task.nvh.data_observation.batch.order_slice",
            },
            data_types_by_task_intent={
                "task.nvh.data_observation.batch.one_dim_data": ("ONE_D",),
                "task.nvh.data_observation.batch.order_slice": ("TWO_D_OC",),
            },
        ).run(context)
    )
    decision = updated.artifacts[DECISION_ARTIFACT]

    assert updated.plan is None
    assert [intent.name for intent in decision.action_intents] == [
        "task.nvh.data_observation.batch.order_slice"
    ]
    assert len(decision.slot_operations) == 1
    assert decision.slot_operations[0].entity_type == "indicator"
    assert decision.slot_operations[0].target == "48阶"


def test_step_does_not_turn_resolver_query_indicator_hint_into_task_intent() -> None:
    context = TurnContext.from_request(TurnRequest(session_id="s1", message="which"))
    context = context.with_artifact(DECISION_ARTIFACT, _decision(
        IntentMatch(
            name="inquiry.nvh.resolver_query.indicators",
            score=0.95,
            slots=IntentSlot(entity_type="indicator", target="Spectrum"),
        )
    )).with_artifact(
        CANDIDATE_CATALOG_ARTIFACT,
        CandidateCatalog.from_mapping(
            {
                "indicator_names": [
                    {"value": "Spectrum", "metadata": {"data_types": ["TWO_D_FS"]}},
                ],
                "data_types": [
                    {"value": "TWO_D_FS", "label": "frequency spectrum"},
                ],
            }
        ),
    )

    updated = run(
        ObservationIndicatorInferenceStep(
            task_intent_by_data_type={
                "TWO_D_FS": "task.nvh.data_observation.batch.frequency_spectrum"
            },
            data_types_by_task_intent={
                "task.nvh.data_observation.batch.frequency_spectrum": ("TWO_D_FS",),
            },
        ).run(context)
    )
    decision = updated.artifacts[DECISION_ARTIFACT]

    assert updated.plan is None
    assert decision.action_intents == ()
    assert len(decision.slot_operations) == 1
    assert decision.slot_operations[0].action == ""


def _decision(*intents: IntentMatch) -> IntentDecision:
    return IntentDecision(
        verdict=RecognitionVerdict.CLEAR,
        intents=intents,
    )


def run(coro):
    return asyncio.run(coro)
