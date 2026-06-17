from __future__ import annotations

from fastapi.testclient import TestClient

from maia.api import TurnResponse
from maia.integrations.sigma import MutableSigmaTokenProvider


class _FakeTurnHandler:
    async def handle_turn(self, request):
        del request
        return TurnResponse(plan={"kind": "reply", "message": "ok"})


def test_maia_app_uses_composition_root_by_default(monkeypatch) -> None:
    from maia.api.main import create_app

    monkeypatch.delenv("SIGMA_ENABLE_MAIA", raising=False)
    monkeypatch.setattr("maia.api.main.create_maia_runtime", lambda **_: _FakeTurnHandler())

    response = TestClient(create_app()).post("/turns", json={"session_id": "s1", "message": "hello"})

    assert response.status_code == 200
    assert response.json()["plan"]["message"] == "ok"


def test_maia_app_returns_503_when_flag_is_disabled(monkeypatch) -> None:
    from maia.api.main import create_app

    monkeypatch.setenv("SIGMA_ENABLE_MAIA", "0")
    response = TestClient(create_app()).post("/turns", json={"session_id": "s1", "message": "hello"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Turn handler is not configured"


def test_maia_app_uses_composition_root_when_flag_is_enabled(monkeypatch) -> None:
    from maia.api.main import create_app

    monkeypatch.setenv("SIGMA_ENABLE_MAIA", "1")
    monkeypatch.setattr("maia.api.main.create_maia_runtime", lambda **_: _FakeTurnHandler())

    response = TestClient(create_app()).post("/turns", json={"session_id": "s1", "message": "hello"})

    assert response.status_code == 200
    assert response.json() == {
        "plan": {
            "dataset": {},
            "kind": "reply",
            "message": "ok",
            "data": {},
            "suggestions": [],
            "slot_state_diff": {"changes": []},
        }
    }


def test_maia_app_exposes_mutable_sigma_token_provider() -> None:
    from maia.api.main import create_app

    provider = MutableSigmaTokenProvider("token-v1")
    app = create_app(turn_handler=_FakeTurnHandler(), sigma_token_provider=provider)

    assert app.state.sigma_token_provider.get() == "token-v1"
    app.state.sigma_token_provider.set("token-v2")
    assert app.state.sigma_token_provider.get() == "token-v2"
