"""FastAPI app for the Maia /turns endpoint."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from maia.api import TurnRequest, TurnResponse
from maia.integrations.sigma import MutableSigmaTokenProvider
from maia.runtime import create_maia_runtime


class TurnHandler(Protocol):
    async def handle_turn(self, request: TurnRequest) -> Any: ...


TurnHandlerCallable = Callable[[TurnRequest], Awaitable[Any]]


def create_app(
    *,
    turn_handler: TurnHandler | TurnHandlerCallable | None = None,
    maia_enabled: bool | None = None,
    sigma_token_provider: MutableSigmaTokenProvider | None = None,
) -> FastAPI:
    app = FastAPI(title="Sigma Maia", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    token_provider = sigma_token_provider or MutableSigmaTokenProvider(os.getenv("SIGMA_TOKEN"))
    app.state.sigma_token_provider = token_provider
    app.state.turn_handler = turn_handler or (
        create_maia_runtime(token_provider=token_provider) if _enabled(maia_enabled) else None
    )

    @app.post("/turns", response_model=TurnResponse)
    async def turns(turn_request: TurnRequest, request: Request) -> TurnResponse:
        return _response_from_result(await _handle_turn(_handler(request.app), turn_request))

    return app


def _enabled(value: bool | None) -> bool:
    if value is not None:
        return value
    return os.getenv("SIGMA_ENABLE_MAIA", "1") != "0"


def _handler(app: FastAPI) -> TurnHandler | TurnHandlerCallable:
    handler = app.state.turn_handler
    if handler is None:
        raise HTTPException(status_code=503, detail="Turn handler is not configured")
    return handler


async def _handle_turn(handler: TurnHandler | TurnHandlerCallable, turn_request: TurnRequest) -> Any:
    method = getattr(handler, "handle_turn", None)
    if method is not None:
        return await method(turn_request)
    return await cast(TurnHandlerCallable, handler)(turn_request)


def _response_from_result(result: Any) -> TurnResponse:
    if isinstance(result, TurnResponse):
        return result
    if isinstance(result, Mapping):
        plan = result.get("plan", result)
    else:
        plan = getattr(result, "plan", None)
    return TurnResponse(plan=plan if isinstance(plan, dict) else {})


app = create_app()
