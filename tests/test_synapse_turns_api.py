from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from synapse.api.main import create_app
from synapse.turns import TurnRequest, TurnResponse


class FakeTurnHandler:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.requests: list[TurnRequest] = []

    async def handle_turn(self, request: TurnRequest) -> Any:
        self.requests.append(request)
        return self.result


def test_turn_request_accepts_supported_payload_without_user_id() -> None:
    payload = {
        "session_id": "s1",
        "message": "show spectrum",
        "workspace_context": {
            "workspace_session_id": "ws-001",
            "data_load_mode": "dataset",
            "dataset_id": "1152",
            "dataset_name": "MAXV 1152",
            "dataset_origin": "selected_dataset",
            "dataset_version": 3,
            "filter_hash": "abc",
            "products": [
                {
                    "product_type": "P1",
                    "product_version": "V1",
                    "system_no": "SYS-1",
                }
            ],
            "test_time": {"start": "2026-05-01", "end": "2026-05-31"},
            "type_systems": [{"type": "P1", "system_no": "SYS-1"}],
            "lang": "zh",
        },
    }

    request = TurnRequest.model_validate(payload)

    assert request.session_id == "s1"
    assert request.message == "show spectrum"
    assert request.workspace_context is not None
    assert request.workspace_context.dataset_id == "1152"


def test_turns_returns_only_public_plan() -> None:
    handler = FakeTurnHandler(
        {
            "status": "reply",
            "message": "internal",
            "plan": {"kind": "reply", "message": "ok"},
            "diagnostics": {"hidden": True},
        }
    )
    client = TestClient(create_app(turn_handler=handler))

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "message": "hello",
            "workspace_context": {"dataset_id": "1152"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"plan": {"kind": "reply", "message": "ok"}}
    assert handler.requests[0].workspace_context is not None
    assert handler.requests[0].workspace_context.dataset_id == "1152"


def test_turns_rejects_extra_request_fields() -> None:
    client = TestClient(create_app(turn_handler=FakeTurnHandler(TurnResponse(plan={}))))

    response = client.post(
        "/turns",
        json={
            "session_id": "s1",
            "message": "hello",
            "unexpected": True,
        },
    )

    assert response.status_code == 422


def test_turns_rejects_removed_user_id_field() -> None:
    client = TestClient(create_app(turn_handler=FakeTurnHandler(TurnResponse(plan={}))))

    response = client.post(
        "/turns",
        json={"session_id": "s1", "user_id": "u1", "message": "hello"},
    )

    assert response.status_code == 422


def test_turns_without_handler_uses_default_synapse_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        "synapse.api.main.create_synapse_runtime",
        lambda: FakeTurnHandler(TurnResponse(plan={"kind": "reply"})),
    )

    response = TestClient(create_app()).post(
        "/turns",
        json={"session_id": "s1", "message": "hello"},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["kind"] == "reply"
