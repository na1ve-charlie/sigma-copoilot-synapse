"""Outer coordinator for one Synapse turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from synapse.turns import TurnRequest, TurnResponse


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Immutable state passed between conductor steps."""

    request: TurnRequest
    message: str
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] | None = None

    @classmethod
    def from_request(cls, request: TurnRequest) -> "TurnContext":
        return cls(request=request, message=request.message)

    def with_artifact(self, key: str, value: Any) -> "TurnContext":
        artifacts = dict(self.artifacts)
        artifacts[key] = value
        return replace(self, artifacts=artifacts)

    def with_message(self, message: str) -> "TurnContext":
        return replace(self, message=message)

    def with_plan(self, plan: Mapping[str, Any]) -> "TurnContext":
        return replace(self, plan=dict(plan))


class TurnStep(Protocol):
    """One injected unit of turn processing."""

    async def run(self, context: TurnContext) -> TurnContext:
        ...


class SynapseConductor:
    """Coordinates injected turn steps without business-domain logic."""

    def __init__(self, steps: Sequence[TurnStep]) -> None:
        self._steps = tuple(steps)

    async def run(self, request: TurnRequest) -> TurnContext:
        context = TurnContext.from_request(request)
        for step in self._steps:
            if context.plan is not None and not _runs_after_plan(step):
                continue
            context = await step.run(context)
        return context

    async def handle_turn(self, request: TurnRequest) -> TurnResponse:
        context = await self.run(request)
        return TurnResponse(plan=context.plan or {})


def _runs_after_plan(step: TurnStep) -> bool:
    return bool(getattr(step, "run_after_plan", False))
