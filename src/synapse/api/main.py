"""FastAPI app for the public Synapse turns API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from synapse.runtime import create_synapse_runtime
from synapse.turns import TurnRequest, TurnResponse


class TurnHandler(Protocol):
    async def handle_turn(self, request: TurnRequest) -> Any:
        ...


TurnHandlerCallable = Callable[[TurnRequest], Awaitable[Any]]


def create_app(
    *,
    turn_handler: TurnHandler | TurnHandlerCallable | None = None,
) -> FastAPI:
    app = FastAPI(title="Sigma Synapse", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.turn_handler = turn_handler or create_synapse_runtime()

    @app.post("/turns", response_model=TurnResponse)
    async def turns(turn_request: TurnRequest, request: Request) -> TurnResponse:
        handler = _handler(request.app)
        result = await _handle_turn(handler, turn_request)
        return TurnResponse(plan=_plan_from_result(result))

    return app


def _handler(app: FastAPI) -> TurnHandler | TurnHandlerCallable:
    handler = app.state.turn_handler
    if handler is None:
        raise HTTPException(status_code=503, detail="Turn handler is not configured")
    return handler


async def _handle_turn(
    handler: TurnHandler | TurnHandlerCallable,
    turn_request: TurnRequest,
) -> Any:
    method = getattr(handler, "handle_turn", None)
    if method is not None:
        return await method(turn_request)
    return await cast(TurnHandlerCallable, handler)(turn_request)


def _plan_from_result(result: Any) -> dict[str, Any]:
    if isinstance(result, TurnResponse):
        return result.plan
    if isinstance(result, Mapping):
        plan = result.get("plan")
    else:
        plan = getattr(result, "plan", None)
    return plan if isinstance(plan, dict) else {}


app = create_app()
