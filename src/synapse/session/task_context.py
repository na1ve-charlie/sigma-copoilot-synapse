from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from synapse.engine import TurnContext


PENDING_TASK_ARTIFACT = "pending_task"
ACTIVE_TASK_ARTIFACT = "active_task"
ACTIVE_TASK_NAME_ARTIFACT = "active_task_name"


@dataclass(frozen=True, slots=True)
class TaskContextState:
    pending_task_name: str | None = None
    active_task_name: str | None = None


class InMemoryTaskContextStore:
    def __init__(
        self,
        states: Mapping[str, TaskContextState] | None = None,
    ) -> None:
        self._states = dict(states or {})

    def get(self, session_id: str) -> TaskContextState:
        return self._states.get(session_id, TaskContextState())

    def put(self, session_id: str, state: TaskContextState) -> None:
        self._states[session_id] = state

    @property
    def states(self) -> Mapping[str, TaskContextState]:
        return dict(self._states)


class TaskContextLoaderStep:
    def __init__(self, store: InMemoryTaskContextStore) -> None:
        self._store = store

    async def run(self, context: TurnContext) -> TurnContext:
        return _apply_state_artifacts(
            context,
            self._store.get(context.request.session_id),
        )


class TaskContextCommitterStep:
    run_after_plan = True

    def __init__(self, store: InMemoryTaskContextStore) -> None:
        self._store = store

    async def run(self, context: TurnContext) -> TurnContext:
        if context.plan is None:
            return context
        session_id = context.request.session_id
        current = self._store.get(session_id)
        updated = _next_state(current, context.plan)
        self._store.put(session_id, updated)
        return _apply_state_artifacts(context, updated)

    @property
    def states(self) -> Mapping[str, TaskContextState]:
        return self._store.states


def _apply_state_artifacts(
    context: TurnContext,
    state: TaskContextState,
) -> TurnContext:
    artifacts = dict(context.artifacts)
    _set_or_remove(artifacts, PENDING_TASK_ARTIFACT, state.pending_task_name)
    _set_or_remove(artifacts, ACTIVE_TASK_NAME_ARTIFACT, state.active_task_name)
    if state.active_task_name:
        artifacts[ACTIVE_TASK_ARTIFACT] = {"name": state.active_task_name}
    else:
        artifacts.pop(ACTIVE_TASK_ARTIFACT, None)
    return TurnContext(
        request=context.request,
        message=context.message,
        artifacts=artifacts,
        diagnostics=context.diagnostics,
        plan=context.plan,
    )


def _next_state(
    current: TaskContextState,
    plan: Mapping[str, object],
) -> TaskContextState:
    kind = _text(plan.get("kind"))
    if kind == "context_clear":
        return TaskContextState()

    pending_task_name = current.pending_task_name
    active_task_name = current.active_task_name

    if kind == "clarify":
        pending = _text(plan.get("pending_task"))
        if pending:
            pending_task_name = pending
    elif kind == "task":
        name = _text(plan.get("name"))
        status = _text(plan.get("status"))
        if name and status in {"ready", "needs_confirmation"}:
            active_task_name = name
            pending_task_name = None

    return TaskContextState(
        pending_task_name=pending_task_name,
        active_task_name=active_task_name,
    )


def _set_or_remove(
    artifacts: dict[str, object],
    key: str,
    value: str | None,
) -> None:
    if value:
        artifacts[key] = value
        return
    artifacts.pop(key, None)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
