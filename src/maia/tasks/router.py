from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from maia.api import ReplyPlan, TurnPlan, TurnRequest
from maia.conversation.state import ConversationSelectionState
from maia.recognition import RecognitionReport


@dataclass(frozen=True)
class TaskContext:
    request: TurnRequest
    report: RecognitionReport
    state: ConversationSelectionState


@dataclass(frozen=True)
class TaskResult:
    plan: TurnPlan
    state: ConversationSelectionState


class TaskHandler(Protocol):
    def can_handle(self, context: TaskContext) -> bool: ...
    async def handle(self, context: TaskContext) -> TaskResult: ...


class TaskRouter:
    def __init__(self, handlers: tuple[TaskHandler, ...]) -> None:
        self._handlers = handlers
        self._unsupported = UnsupportedTaskHandler()

    async def handle(self, context: TaskContext) -> TaskResult:
        for handler in self._handlers:
            if handler.can_handle(context):
                return await handler.handle(context)
        return await self._unsupported.handle(context)


class UnsupportedTaskHandler:
    def can_handle(self, context: TaskContext) -> bool:
        del context
        return True

    async def handle(self, context: TaskContext) -> TaskResult:
        return TaskResult(
            plan=ReplyPlan(message="Maia currently supports record search only."),
            state=context.state,
        )


__all__ = ["TaskContext", "TaskHandler", "TaskResult", "TaskRouter"]
