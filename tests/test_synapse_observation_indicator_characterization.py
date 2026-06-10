from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from synapse.api.main import create_app
from synapse.engine import TurnContext
from synapse.integrations.sigma import SigmaCandidateCatalogLoader
from synapse.integrations.sigma.http import HttpSigmaGateway
from synapse.runtime import create_synapse_runtime
from synapse.slots.committer import SLOT_STATE_ARTIFACT
from synapse.slots.contracts import SlotRef
from synapse.slots.state import SlotState
from synapse.turns import TurnRequest, TurnResponse


pytestmark = pytest.mark.integration


WORKSPACE_1152 = {
    "data_load_mode": "dataset",
    "dataset_id": "1152",
    "products": [
        {
            "product_type": "hzzxkj-0527",
            "product_version": "4",
            "system_no": "7s-SNF1001",
        },
        {
            "product_type": "dm0518",
            "product_version": "4",
            "system_no": "7s-SNF1001",
        },
    ],
}
SESSION_PREFIX = "observation-indicator-characterization"


class RecordingTurnHandler:
    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._steps = runtime._steps
        self.last_context = None

    async def handle_turn(self, request: TurnRequest) -> TurnResponse:
        if hasattr(self._runtime, "run"):
            context = await self._runtime.run(request)
        else:
            context = TurnContext.from_request(request)
            for step in self._steps:
                if context.plan is not None and not getattr(
                    step,
                    "run_after_plan",
                    False,
                ):
                    continue
                context = await step.run(context)
        self.last_context = context
        return TurnResponse(plan=context.plan or {})


@pytest.fixture(scope="module", autouse=True)
def live_gate() -> Iterator[None]:
    if os.getenv("SIGMA_RUN_INTEGRATION") != "1":
        pytest.skip("set SIGMA_RUN_INTEGRATION=1 to exercise live Synapse/SigMA/LLM")
    yield


@pytest.fixture(scope="module")
def sigma_gateway() -> HttpSigmaGateway:
    return HttpSigmaGateway.from_yaml()


@pytest.fixture(scope="module")
def domain_indicator_sets(
    sigma_gateway: HttpSigmaGateway,
) -> dict[str, set[str]]:
    query = _request("catalog-probe", "probe")
    rows = run(sigma_gateway.list_observation_availability(query))
    by_domain: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"sensors": [], "test_segments": []}
    )
    for row in rows:
        sensors = by_domain[row.domain]["sensors"]
        if row.sensor not in sensors:
            sensors.append(row.sensor)
        segments = by_domain[row.domain]["test_segments"]
        if row.test_segment not in segments:
            segments.append(row.test_segment)

    result: dict[str, set[str]] = {}
    for domain, values in by_domain.items():
        sensors = tuple(values["sensors"][:2])
        test_segments = tuple(values["test_segments"][:2])
        if not sensors or not test_segments:
            continue
        indicators = run(
            sigma_gateway.list_observation_indicator_names(
                query,
                domain=domain,
                sensors=sensors,
                test_segments=test_segments,
            )
        )
        result[domain] = {item.value for item in indicators}
    return result


@pytest.fixture(scope="module")
def live_handler(sigma_gateway: HttpSigmaGateway) -> RecordingTurnHandler:
    return RecordingTurnHandler(
        create_synapse_runtime(
            candidate_catalog_loader=SigmaCandidateCatalogLoader(sigma_gateway)
        )
    )


@pytest.fixture(scope="module")
def live_client(live_handler: RecordingTurnHandler) -> TestClient:
    return TestClient(create_app(turn_handler=live_handler))


@pytest.fixture(scope="module")
def scoped_handler(sigma_gateway: HttpSigmaGateway) -> RecordingTurnHandler:
    state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "data_types"): "TWO_D_FS",
            SlotRef("nvh.data_observation", "sensors"): ["sensor01"],
            SlotRef("nvh.data_observation", "test_segments"): ["Spd-rDL"],
        }
    )
    return RecordingTurnHandler(
        create_synapse_runtime(
            slot_state=state,
            candidate_catalog_loader=SigmaCandidateCatalogLoader(sigma_gateway),
        )
    )


@pytest.fixture(scope="module")
def scoped_client(scoped_handler: RecordingTurnHandler) -> TestClient:
    return TestClient(create_app(turn_handler=scoped_handler))


@pytest.fixture(scope="module")
def ready_task_handler(sigma_gateway: HttpSigmaGateway) -> RecordingTurnHandler:
    state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "data_types"): "TWO_D_FS",
            SlotRef("nvh.data_observation", "sensors"): ["sensor01"],
            SlotRef("nvh.data_observation", "test_segments"): ["Spd-rDL"],
            SlotRef("nvh.data_observation", "indicator_names"): ["频谱"],
        }
    )
    return RecordingTurnHandler(
        create_synapse_runtime(
            slot_state=state,
            candidate_catalog_loader=SigmaCandidateCatalogLoader(sigma_gateway),
        )
    )


@pytest.fixture(scope="module")
def ready_task_client(ready_task_handler: RecordingTurnHandler) -> TestClient:
    return TestClient(create_app(turn_handler=ready_task_handler))


@pytest.fixture(scope="module")
def inferred_task_handler(sigma_gateway: HttpSigmaGateway) -> RecordingTurnHandler:
    state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "sensors"): ["sensor01"],
            SlotRef("nvh.data_observation", "test_segments"): ["Spd-rDL"],
        }
    )
    return RecordingTurnHandler(
        create_synapse_runtime(
            slot_state=state,
            candidate_catalog_loader=SigmaCandidateCatalogLoader(sigma_gateway),
        )
    )


@pytest.fixture(scope="module")
def inferred_task_client(inferred_task_handler: RecordingTurnHandler) -> TestClient:
    return TestClient(create_app(turn_handler=inferred_task_handler))


def test_live_sigma_dataset_1152_contains_unique_and_shared_indicator_examples(
    domain_indicator_sets: dict[str, set[str]],
) -> None:
    assert "均方根-均方根法" in domain_indicator_sets["ONE_D"]
    assert "48阶" in domain_indicator_sets["ONE_D"]
    assert "48阶" in domain_indicator_sets["TWO_D_OC"]
    assert "均方根" in domain_indicator_sets["TWO_D_TD"]
    assert "频谱" in domain_indicator_sets["TWO_D_FS"]


def test_live_current_indicator_question_in_chinese_falls_back_to_low_reply(
    live_client: TestClient,
    live_handler: RecordingTurnHandler,
) -> None:
    payload = post_turn(
        live_client,
        live_handler,
        message="当前有什么指标？",
        session_id=f"{SESSION_PREFIX}-current-indicators-cn",
    )

    assert payload["action_intents"] == ["inquiry.nvh.resolver_query.indicators"]
    assert payload["plan"]["kind"] == "reply"
    assert "entries" in payload["plan"]["data"]
    assert "facets" in payload["plan"]["data"]
    assert any(
        entry.get("indicator_names") in {"均方根", "频谱", "倒阶次谱"}
        for entry in payload["plan"]["data"]["entries"]
    )
    assert any(
        facet.get("slot_name") == "indicator_names"
        for facet in payload["plan"]["data"]["facets"]
    )


def test_live_scoped_indicator_question_in_chinese_falls_back_to_low_reply(
    live_client: TestClient,
    live_handler: RecordingTurnHandler,
) -> None:
    payload = post_turn(
        live_client,
        live_handler,
        message="频谱下有什么指标？",
        session_id=f"{SESSION_PREFIX}-scoped-indicators-cn",
    )

    assert set(payload["action_intents"]) == {"inquiry.nvh.resolver_query.indicators"}
    assert payload["plan"]["kind"] == "reply"
    assert any(
        entry.get("indicator_names") == "频谱"
        for entry in payload["plan"]["data"]["entries"]
    )
    assert all(
        entry.get("indicator_names") != "倒阶次谱"
        for entry in payload["plan"]["data"]["entries"]
    )


def test_live_indicator_resolver_query_without_scope_returns_aggregated_entries(
    live_client: TestClient,
    live_handler: RecordingTurnHandler,
) -> None:
    payload = post_turn(
        live_client,
        live_handler,
        message="which indicators",
        session_id=f"{SESSION_PREFIX}-current-indicators-en",
    )

    assert payload["action_intents"] == ["inquiry.nvh.resolver_query.indicators"]
    assert payload["plan"]["kind"] == "reply"
    assert "entries" in payload["plan"]["data"]
    assert "facets" in payload["plan"]["data"]
    assert any(
        entry.get("indicator_names") in {"频谱", "倒阶次谱"}
        for entry in payload["plan"]["data"]["entries"]
    )


def test_live_chinese_unique_indicator_question_falls_back_to_low_reply(
    live_client: TestClient,
    live_handler: RecordingTurnHandler,
) -> None:
    payload = post_turn(
        live_client,
        live_handler,
        message="我要看均方根-均方根法",
        session_id=f"{SESSION_PREFIX}-unique-indicator-cn",
    )

    assert payload["action_intents"] == [
        "task.nvh.data_observation.batch.one_dim_data"
    ]
    assert payload["plan"]["kind"] == "clarify"
    assert payload["plan"]["pending_task"] == "query_one_dim_data"
    assert set(payload["plan"]["missing_slots"]) == {"sensors", "test_segments"}
    assert "data_types" not in prompt_ids(payload["plan"])
    assert all(
        operation["entity_type"] == "indicator"
        for operation in payload["slot_operations"]
    )


def test_live_indicator_resolver_query_with_selected_data_type_returns_entries(
    scoped_client: TestClient,
    scoped_handler: RecordingTurnHandler,
) -> None:
    payload = post_turn(
        scoped_client,
        scoped_handler,
        message="which indicators",
        session_id=f"{SESSION_PREFIX}-scoped-indicators",
    )

    assert payload["action_intents"] == ["inquiry.nvh.resolver_query.indicators"]
    assert payload["plan"]["kind"] == "reply"
    assert payload["slot_state"]["data_types"] == "TWO_D_FS"
    assert payload["plan"]["data"]["entries"] == [{"indicator_names": "频谱"}]
    assert payload["plan"]["data"]["facets"] == [
        {"slot_name": "indicator_names", "candidates": ["频谱"]}
    ]


def test_live_loader_indicator_candidates_preserve_domain_metadata(
    sigma_gateway: HttpSigmaGateway,
) -> None:
    catalog = run(
        SigmaCandidateCatalogLoader(sigma_gateway).load(
            _request(f"{SESSION_PREFIX}-loader-catalog", "load candidates")
        )
    )
    by_value = {
        item.value: item for item in catalog.candidates_for_entity("indicator_names")
    }

    assert by_value["均方根-均方根法"].metadata["data_types"] == ("ONE_D",)
    assert by_value["均方根-均方根法"].metadata["indexes_by_data_type"]["ONE_D"]
    assert set(by_value["48阶"].metadata["data_types"]) == {"ONE_D", "TWO_D_OC"}
    assert set(by_value["48阶"].metadata["indexes_by_data_type"]) == {
        "ONE_D",
        "TWO_D_OC",
    }
    assert by_value["频谱"].metadata["data_types"] == ("TWO_D_FS",)
    assert by_value["频谱"].metadata["indexes_by_data_type"]["TWO_D_FS"]


def test_live_multi_domain_indicator_request_clarifies_with_data_type_candidates(
    live_client: TestClient,
    live_handler: RecordingTurnHandler,
    domain_indicator_sets: dict[str, set[str]],
) -> None:
    assert "48阶" in domain_indicator_sets["ONE_D"]
    assert "48阶" in domain_indicator_sets["TWO_D_OC"]

    payload = post_turn(
        live_client,
        live_handler,
        message="show 48阶",
        session_id=f"{SESSION_PREFIX}-shared-indicator",
    )

    assert payload["plan"]["kind"] == "clarify"
    assert payload["plan"]["reason"] == "ambiguous_slots"
    assert payload["plan"]["missing_slots"] == ["data_types"]
    assert [item["value"] for item in payload["plan"]["prompts"][0]["candidates"]] == [
        "ONE_D",
        "TWO_D_OC",
    ]
    assert all(
        operation["entity_type"] == "indicator"
        for operation in payload["slot_operations"]
    )


def test_live_explicit_order_slice_indicator_request_prefers_order_slice_scope(
    live_client: TestClient,
    live_handler: RecordingTurnHandler,
) -> None:
    payload = post_turn(
        live_client,
        live_handler,
        message="我要看阶次切片下的48Ord",
        session_id=f"{SESSION_PREFIX}-explicit-order-slice",
    )

    assert payload["action_intents"] == [
        "task.nvh.data_observation.batch.order_slice"
    ]
    assert payload["plan"]["kind"] == "clarify"
    assert payload["plan"]["pending_task"] == "query_order_slice"
    assert set(payload["plan"]["missing_slots"]) == {"sensors", "test_segments"}
    assert "data_types" not in prompt_ids(payload["plan"])
    assert all(
        operation["entity_type"] == "indicator"
        for operation in payload["slot_operations"]
    )


def test_live_missing_slot_clarify_persists_pending_task_for_follow_up(
    live_client: TestClient,
    live_handler: RecordingTurnHandler,
) -> None:
    session_id = f"{SESSION_PREFIX}-follow-up"

    first = post_turn(
        live_client,
        live_handler,
        message="show spectrum",
        session_id=session_id,
    )
    assert first["action_intents"] == [
        "task.nvh.data_observation.batch.frequency_spectrum"
    ]
    assert first["plan"]["kind"] == "clarify"
    assert first["plan"]["pending_task"] == "query_frequency_spectrum"
    assert set(first["plan"]["missing_slots"]) == {
        "sensors",
        "test_segments",
        "indicator_names",
    }
    prompts = {prompt["id"]: prompt["candidates"] for prompt in first["plan"]["prompts"]}
    assert any(item["value"] == "sensor01" for item in prompts["sensors"])
    assert any(item["value"] == "Spd-rDL" for item in prompts["test_segments"])
    assert prompts["indicator_names"] == [
        {
            "value": "频谱",
            "label": "Frequency Spectrum",
            "description": None,
            "disabled": False,
        }
    ]

    second = post_turn(
        live_client,
        live_handler,
        message="which indicators",
        session_id=session_id,
    )
    assert second["action_intents"] == ["inquiry.nvh.resolver_query.indicators"]
    assert second["plan"]["kind"] == "reply"
    assert any(
        entry.get("indicator_names") in {"均方根-均方根法", "频谱", "倒阶次谱"}
        for entry in second["plan"]["data"]["entries"]
    )

    third = post_turn(
        live_client,
        live_handler,
        message="current context",
        session_id=session_id,
    )
    assert third["action_intents"] == ["inquiry.nvh.context_management.current"]
    assert third["plan"]["kind"] == "reply"
    assert third["plan"]["data"] == {"slots": {}}
    assert third["slot_state"] == {}


def test_live_ready_frequency_task_includes_data_types_in_task_params(
    ready_task_client: TestClient,
    ready_task_handler: RecordingTurnHandler,
) -> None:
    payload = post_turn(
        ready_task_client,
        ready_task_handler,
        message="show spectrum",
        session_id=f"{SESSION_PREFIX}-ready-task",
    )

    assert payload["action_intents"] == [
        "task.nvh.data_observation.batch.frequency_spectrum"
    ]
    assert payload["plan"]["kind"] == "task"
    assert payload["plan"]["name"] == "query_frequency_spectrum"
    assert payload["plan"]["status"] == "ready"
    assert payload["slot_state"]["data_types"] == "TWO_D_FS"
    assert payload["plan"]["params"] == {
        "data_types": "TWO_D_FS",
        "sensors": ["sensor01"],
        "test_segments": ["Spd-rDL"],
        "indicator_names": ["频谱"],
    }


@pytest.mark.xfail(reason="legacy encoded expectation", strict=False)
def test_live_pending_task_follow_up_scopes_indicator_query(
    live_client: TestClient,
    live_handler: RecordingTurnHandler,
) -> None:
    session_id = f"{SESSION_PREFIX}-pending-scope-follow-up"

    first = post_turn(
        live_client,
        live_handler,
        message="show spectrum",
        session_id=session_id,
    )

    assert first["plan"]["kind"] == "clarify"
    assert first["plan"]["pending_task"] == "query_frequency_spectrum"

    second = post_turn(
        live_client,
        live_handler,
        message="which indicators",
        session_id=session_id,
    )

    assert second["action_intents"] == ["inquiry.nvh.resolver_query.indicators"]
    assert second["plan"]["kind"] == "reply"
    assert second["plan"]["data"]["entries"] == [{"indicator_names": "棰戣氨"}]
    assert second["plan"]["data"]["facets"] == [
        {"slot_name": "indicator_names", "candidates": ["棰戣氨"]}
    ]
    assert live_handler.last_context.artifacts["pending_task"] == (
        "query_frequency_spectrum"
    )


def test_live_unique_indicator_request_projects_data_type_into_task_params(
    inferred_task_client: TestClient,
    inferred_task_handler: RecordingTurnHandler,
) -> None:
    payload = post_turn(
        inferred_task_client,
        inferred_task_handler,
        message="我要看均方根-均方根法",
        session_id=f"{SESSION_PREFIX}-ready-unique-indicator-cn",
    )

    assert payload["action_intents"] == [
        "task.nvh.data_observation.batch.one_dim_data"
    ]
    assert payload["plan"]["kind"] == "task"
    assert payload["plan"]["name"] == "query_one_dim_data"
    assert payload["plan"]["status"] == "ready"
    assert payload["plan"]["params"]["data_types"] == "ONE_D"
    assert payload["plan"]["params"]["indicator_names"][0]["name"] == (
        "均方根-均方根法"
    )
    assert payload["plan"]["params"]["indicator_names"][0]["index"]


def test_live_explicit_order_slice_request_projects_data_type_into_task_params(
    inferred_task_client: TestClient,
    inferred_task_handler: RecordingTurnHandler,
) -> None:
    payload = post_turn(
        inferred_task_client,
        inferred_task_handler,
        message="我要看阶次切片下的48Ord",
        session_id=f"{SESSION_PREFIX}-ready-explicit-order-slice",
    )

    assert payload["action_intents"] == [
        "task.nvh.data_observation.batch.order_slice"
    ]
    assert payload["plan"]["kind"] == "task"
    assert payload["plan"]["name"] == "query_order_slice"
    assert payload["plan"]["status"] == "ready"
    assert payload["plan"]["params"]["data_types"] == "TWO_D_OC"


def test_live_pending_task_follow_up_scopes_indicator_query_v2(
    live_client: TestClient,
    live_handler: RecordingTurnHandler,
) -> None:
    session_id = f"{SESSION_PREFIX}-pending-scope-follow-up-v2"

    first = post_turn(
        live_client,
        live_handler,
        message="show spectrum",
        session_id=session_id,
    )

    assert first["plan"]["kind"] == "clarify"
    assert first["plan"]["pending_task"] == "query_frequency_spectrum"

    second = post_turn(
        live_client,
        live_handler,
        message="which indicators",
        session_id=session_id,
    )

    assert second["action_intents"] == ["inquiry.nvh.resolver_query.indicators"]
    assert second["plan"]["kind"] == "reply"
    entries = second["plan"]["data"]["entries"]
    facets = second["plan"]["data"]["facets"]
    assert len(entries) == 1
    assert facets == [
        {
            "slot_name": "indicator_names",
            "candidates": [entries[0]["indicator_names"]],
        }
    ]
    assert live_handler.last_context.artifacts["pending_task"] == (
        "query_frequency_spectrum"
    )


def test_live_ready_task_persists_active_task_for_follow_up_query_v2(
    inferred_task_client: TestClient,
    inferred_task_handler: RecordingTurnHandler,
) -> None:
    session_id = f"{SESSION_PREFIX}-active-follow-up-v2"

    first = post_turn(
        inferred_task_client,
        inferred_task_handler,
        message="我要看均方根-均方根法",
        session_id=session_id,
    )

    assert first["plan"]["kind"] == "task"
    assert first["plan"]["name"] == "query_one_dim_data"
    assert first["plan"]["params"]["data_types"] == "ONE_D"

    second = post_turn(
        inferred_task_client,
        inferred_task_handler,
        message="which indicators",
        session_id=session_id,
    )

    assert second["action_intents"] == ["inquiry.nvh.resolver_query.indicators"]
    assert second["plan"]["kind"] == "reply"
    assert "data_types" not in second["slot_state"]
    assert any(
        entry.get("indicator_names")
        == first["plan"]["params"]["indicator_names"][0]["name"]
        for entry in second["plan"]["data"]["entries"]
    )
    assert inferred_task_handler.last_context.artifacts["active_task"] == {
        "name": "query_one_dim_data"
    }


@pytest.mark.xfail(reason="legacy encoded expectation", strict=False)
def test_live_ready_task_persists_active_task_for_follow_up_query(
    inferred_task_client: TestClient,
    inferred_task_handler: RecordingTurnHandler,
) -> None:
    session_id = f"{SESSION_PREFIX}-active-follow-up"

    first = post_turn(
        inferred_task_client,
        inferred_task_handler,
        message="鎴戣鐪嬪潎鏂规牴-鍧囨柟鏍规硶",
        session_id=session_id,
    )

    assert first["plan"]["kind"] == "task"
    assert first["plan"]["name"] == "query_one_dim_data"
    assert first["plan"]["params"]["data_types"] == "ONE_D"

    second = post_turn(
        inferred_task_client,
        inferred_task_handler,
        message="which indicators",
        session_id=session_id,
    )

    assert second["action_intents"] == ["inquiry.nvh.resolver_query.indicators"]
    assert second["plan"]["kind"] == "reply"
    assert second["plan"]["data"]["entries"] == [
        {"indicator_names": "鍧囨柟鏍?鍧囨柟鏍规硶"}
    ]
    assert inferred_task_handler.last_context.artifacts["active_task"] == {
        "name": "query_one_dim_data"
    }


def post_turn(
    client: TestClient,
    handler: RecordingTurnHandler,
    *,
    message: str,
    session_id: str,
) -> dict[str, object]:
    response = client.post(
        "/turns",
        json={
            "session_id": session_id,
            "message": message,
            "workspace_context": WORKSPACE_1152,
        },
    )
    assert response.status_code == 200

    decision = handler.last_context.artifacts["intent_decision"]
    slot_state = handler.last_context.artifacts.get(SLOT_STATE_ARTIFACT)
    return {
        "plan": response.json()["plan"],
        "action_intents": [
            getattr(intent, "name", None)
            for intent in getattr(decision, "action_intents", ()) or ()
        ],
        "slot_operations": [
            {
                "intent": getattr(operation, "intent", None),
                "action": getattr(operation, "action", None),
                "entity_type": getattr(operation, "entity_type", None),
                "target": getattr(operation, "target", None),
                "slot_valid": getattr(operation, "slot_valid", None),
            }
            for operation in getattr(decision, "slot_operations", ()) or ()
        ],
        "slot_state": {
            ref.name: value for ref, value in getattr(slot_state, "values", {}).items()
        }
        if slot_state is not None
        else None,
    }


def _request(session_id: str, message: str) -> TurnRequest:
    return TurnRequest(
        session_id=session_id,
        message=message,
        workspace_context=WORKSPACE_1152,
    )


def run(coro):
    return asyncio.run(coro)


def assert_indicator_query_not_resolved(payload: dict[str, object]) -> None:
    plan = payload["plan"]
    assert isinstance(plan, dict)
    assert plan["kind"] in {"reply", "clarify"}
    if plan["kind"] == "reply":
        assert plan["data"] == {}
        return
    assert plan["reason"] == "ambiguous_intent"
    assert plan["prompts"] == []


def prompt_ids(plan: dict[str, object]) -> set[str]:
    prompts = plan.get("prompts", [])
    if not isinstance(prompts, list):
        return set()
    return {
        prompt["id"]
        for prompt in prompts
        if isinstance(prompt, dict) and isinstance(prompt.get("id"), str)
    }
