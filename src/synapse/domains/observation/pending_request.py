from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from synapse.engine import TurnContext


PENDING_OBSERVATION_REQUEST_ARTIFACT = "pending_observation_request"
CLEAR_PENDING_OBSERVATION_REQUEST_ARTIFACT = "clear_pending_observation_request"


@dataclass(frozen=True, slots=True)
class PendingObservationRequest:
    indicator_name: str
    candidate_data_types: tuple[str, ...]
    intent_name: str
    score: float


class InMemoryPendingObservationRequestStore:
    def __init__(
        self,
        states: Mapping[str, PendingObservationRequest] | None = None,
    ) -> None:
        self._states = dict(states or {})

    def get(self, session_id: str) -> PendingObservationRequest | None:
        return self._states.get(session_id)

    def put(
        self,
        session_id: str,
        request: PendingObservationRequest | None,
    ) -> None:
        if request is None:
            self._states.pop(session_id, None)
            return
        self._states[session_id] = request


class PendingObservationRequestLoaderStep:
    def __init__(self, store: InMemoryPendingObservationRequestStore) -> None:
        self._store = store

    async def run(self, context: TurnContext) -> TurnContext:
        request = self._store.get(context.request.session_id)
        if request is None:
            return context
        return context.with_artifact(PENDING_OBSERVATION_REQUEST_ARTIFACT, request)


class PendingObservationRequestCommitterStep:
    run_after_plan = True

    def __init__(self, store: InMemoryPendingObservationRequestStore) -> None:
        self._store = store

    async def run(self, context: TurnContext) -> TurnContext:
        if context.plan is None:
            return context
        session_id = context.request.session_id
        if (
            context.artifacts.get(CLEAR_PENDING_OBSERVATION_REQUEST_ARTIFACT)
            or _plan_finishes_pending_request(context.plan)
        ):
            self._store.put(session_id, None)
            return context
        request = context.artifacts.get(PENDING_OBSERVATION_REQUEST_ARTIFACT)
        if isinstance(request, PendingObservationRequest):
            self._store.put(session_id, request)
        return context


def _plan_finishes_pending_request(plan: Mapping[str, object]) -> bool:
    kind = plan.get("kind")
    if kind in {"context_clear", "task"}:
        return True
    return kind == "clarify" and bool(plan.get("pending_task"))
