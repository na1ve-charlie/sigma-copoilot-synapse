from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from maia.api import ConfirmPlan, ReplyPlan, TaskPlan
from maia.conversation.state import ConversationSelectionState, ConversationTaskStateStore
from maia.integrations.sigma.audio_generation import (
    NgAudioGenerationError,
    NgAudioGenerationRequest,
)
from maia.selection import InMemorySelectionSetRepository
from maia.tasks import ConfirmationService, PendingConfirmation, PendingTask, TaskSpec
from maia.tasks.record_search import RecordSearchHandler
from maia.tasks.router import TaskContext, TaskResult


AUDIO_GENERATION_INTENT = "task.nvh.audio.generate"
PENDING_SELECTION_MARKER = f"__task__:{AUDIO_GENERATION_INTENT}"
NG_RESULTS = {"fail", "ng", "不合格"}
CONFIRM_VALUES = {"确认", "是", "好的", "执行", "confirm", "yes", "y"}
CANCEL_VALUES = {"取消", "不用", "不要", "cancel", "no", "n"}


class AudioGenerator(Protocol):
    async def generate(
        self,
        request: NgAudioGenerationRequest,
        *,
        workspace_context,
    ) -> dict[str, object]: ...


class UnavailableAudioGenerator:
    async def generate(
        self,
        request: NgAudioGenerationRequest,
        *,
        workspace_context,
    ) -> dict[str, object]:
        raise NgAudioGenerationError("NG audio generation client is not configured.")


class AudioGenerationHandler:
    def __init__(
        self,
        *,
        record_search: RecordSearchHandler,
        selection_repository: InMemorySelectionSetRepository,
        generator: AudioGenerator | None,
        confirmation_service: ConfirmationService | None = None,
        task_id_factory=None,
    ) -> None:
        self._record_search = record_search
        self._selection_repository = selection_repository
        self._generator = generator or UnavailableAudioGenerator()
        self._confirmation = confirmation_service or ConfirmationService()
        self._task_store = ConversationTaskStateStore()
        self._task_id_factory = task_id_factory or (lambda: f"task-{uuid4().hex}")

    def can_handle(self, context: TaskContext) -> bool:
        draft = context.state.pending_selection_draft
        task = context.state.pending_task
        confirmation = context.state.pending_confirmation
        return (
            (draft is not None and PENDING_SELECTION_MARKER in draft.pending_questions)
            or (task is not None and AUDIO_GENERATION_INTENT in task.operations)
            or (confirmation is not None and AUDIO_GENERATION_INTENT in confirmation.task.operations)
            or (
                bool(context.report.action_intents)
                and context.report.action_intents[0].name == AUDIO_GENERATION_INTENT
            )
            or any(intent.name == AUDIO_GENERATION_INTENT for intent in context.report.intents)
        )

    async def handle(self, context: TaskContext) -> TaskResult:
        confirmation = context.state.pending_confirmation
        if confirmation is not None and AUDIO_GENERATION_INTENT in confirmation.task.operations:
            return await self._handle_confirmation(context, confirmation)
        task = context.state.pending_task
        if task is not None and AUDIO_GENERATION_INTENT in task.operations:
            return await self._resume_task(context, task)
        return await self._start_task(context)

    async def _start_task(self, context: TaskContext) -> TaskResult:
        if any(intent.name == AUDIO_GENERATION_INTENT for intent in context.report.intents):
            report = context.report.model_copy(
                update={
                    "slot_operations": tuple(
                        operation
                        for operation in context.report.slot_operations
                        if not (
                            operation.intent == AUDIO_GENERATION_INTENT
                            and operation.entity_type == ""
                        )
                    )
                }
            )
            context = TaskContext(context.request, report, context.state)
        selection_result = await self._record_search.resolve_selection(
            context,
            complete_type_system=False,
        )
        if selection_result.clarify is not None:
            state = selection_result.state
            draft = state.pending_selection_draft
            if draft is not None and PENDING_SELECTION_MARKER not in draft.pending_questions:
                state = state.model_copy(
                    update={
                        "pending_selection_draft": draft.model_copy(
                            update={"pending_questions": (*draft.pending_questions, PENDING_SELECTION_MARKER)}
                        ),
                        "version": state.version + 1,
                    }
                )
            return TaskResult(plan=selection_result.clarify, state=state)
        if selection_result.selection is None:
            return TaskResult(
                plan=TaskPlan(
                    status="blocked",
                    name=AUDIO_GENERATION_INTENT,
                    intent=AUDIO_GENERATION_INTENT,
                    title="NG audio generation",
                    risk_level="medium",
                    requires_confirmation=True,
                    message="No records matched the current selection.",
                    reason="empty_selection",
                ),
                state=selection_result.state,
            )
        task = TaskSpec(
            task_id=self._task_id_factory(),
            name=AUDIO_GENERATION_INTENT,
            title="NG audio generation",
            operations=(AUDIO_GENERATION_INTENT,),
            selection_set_id=selection_result.selection.selection_set_id,
            selection_hash=selection_result.selection.selection_hash,
            risk_level="medium",
            requires_confirmation=True,
        )
        return await self._prepare_or_confirm(context, selection_result.state, task)

    async def _resume_task(self, context: TaskContext, task: TaskSpec | PendingTask) -> TaskResult:
        if self._selection_repository.get(task.selection_set_id) is None:
            return TaskResult(
                plan=TaskPlan(
                    status="blocked",
                    name=task.name,
                    intent=AUDIO_GENERATION_INTENT,
                    title=task.title,
                    risk_level=task.risk_level,
                    requires_confirmation=task.requires_confirmation,
                    params=task.params,
                    message="The selected records are no longer available.",
                    reason="selection_not_found",
                ),
                state=self._task_store.clear_pending(context.state),
            )
        return await self._prepare_or_confirm(context, context.state, task)

    async def _prepare_or_confirm(
        self,
        context: TaskContext,
        state: ConversationSelectionState,
        task: TaskSpec | PendingTask,
    ) -> TaskResult:
        selection = self._selection_repository.get(task.selection_set_id)
        if selection is None:
            return TaskResult(
                plan=TaskPlan(
                    status="blocked",
                    name=task.name,
                    intent=AUDIO_GENERATION_INTENT,
                    title=task.title,
                    risk_level=task.risk_level,
                    requires_confirmation=task.requires_confirmation,
                    params=task.params,
                    message="The selected records are no longer available.",
                    reason="selection_not_found",
                ),
                state=self._task_store.clear_pending(state),
            )
        records = await self._record_search.records_for_selection(
            selection,
            workspace_context=context.request.workspace_context,
        )
        if not records:
            return TaskResult(
                plan=TaskPlan(
                    status="blocked",
                    name=task.name,
                    intent=AUDIO_GENERATION_INTENT,
                    title=task.title,
                    risk_level=task.risk_level,
                    requires_confirmation=task.requires_confirmation,
                    params=task.params,
                    message="No records matched the current selection.",
                    reason="empty_selection",
                ),
                state=self._task_store.clear_pending(state),
            )

        result_ids: list[int] = []
        for record in records:
            if str(record.summary_result or "").strip().casefold() not in NG_RESULTS:
                continue
            record_id = record.record_id.strip()
            if not record_id.isdigit():
                return TaskResult(
                    plan=TaskPlan(
                        status="blocked",
                        name=task.name,
                        intent=AUDIO_GENERATION_INTENT,
                        title=task.title,
                        risk_level=task.risk_level,
                        requires_confirmation=task.requires_confirmation,
                        params=task.params,
                        message=f"record_id must be numeric for NG audio generation: {record.record_id}",
                        reason="invalid_record_id",
                    ),
                    state=self._task_store.clear_pending(state),
                )
            result_ids.append(int(record_id))

        if not result_ids:
            return TaskResult(
                plan=ReplyPlan(message="当前测试记录中不包含不合格测试件。"),
                state=self._task_store.clear_pending(state),
            )

        ready_task = task.model_copy(update={"params": {"resultIds": result_ids}})
        confirmation = self._confirmation.preview(ready_task, record_count=len(result_ids))
        if not isinstance(confirmation, PendingConfirmation):
            return TaskResult(
                plan=TaskPlan(
                    status="ready",
                    name=ready_task.name,
                    intent=AUDIO_GENERATION_INTENT,
                    title=ready_task.title,
                    risk_level=ready_task.risk_level,
                    requires_confirmation=ready_task.requires_confirmation,
                    params=ready_task.params,
                    message="NG audio generation is ready.",
                ),
                state=self._task_store.clear_pending(state),
            )
        return TaskResult(
            plan=ConfirmPlan(
                reason=confirmation.reason,
                message="Confirm NG audio generation.",
                payload={
                    **confirmation.payload,
                    "operation": AUDIO_GENERATION_INTENT,
                    "params": ready_task.params,
                },
                dataset=self._record_search.dataset_for(context, state=state, selection=selection),
            ),
            state=self._task_store.save_confirmation(state, confirmation),
        )

    async def _handle_confirmation(self, context: TaskContext, confirmation: PendingConfirmation) -> TaskResult:
        normalized = context.request.message.strip().casefold()
        if normalized in CANCEL_VALUES:
            return TaskResult(
                plan=ReplyPlan(message="NG audio generation cancelled."),
                state=self._task_store.cancel_confirmation(context.state),
            )
        if normalized not in CONFIRM_VALUES:
            return TaskResult(
                plan=ConfirmPlan(
                    reason=confirmation.reason,
                    message="Confirm NG audio generation.",
                    payload=confirmation.payload,
                ),
                state=context.state,
            )
        try:
            task = self._confirmation.confirm(
                confirmation,
                token=confirmation.token,
                selection_hash=confirmation.task.selection_hash,
            )
            await self._generator.generate(
                NgAudioGenerationRequest(result_ids=tuple(int(value) for value in task.params["resultIds"])),
                workspace_context=context.request.workspace_context,
            )
        except (NgAudioGenerationError, ValueError) as exc:
            return TaskResult(
                plan=TaskPlan(
                    status="blocked",
                    name=confirmation.task.name,
                    intent=AUDIO_GENERATION_INTENT,
                    title=confirmation.task.title,
                    risk_level=confirmation.task.risk_level,
                    requires_confirmation=confirmation.task.requires_confirmation,
                    params=confirmation.task.params,
                    message=str(exc),
                    reason="ng_audio_generation_failed",
                ),
                state=self._task_store.cancel_confirmation(context.state),
            )
        return TaskResult(
            plan=TaskPlan(
                status="ready",
                name=task.name,
                intent=AUDIO_GENERATION_INTENT,
                title=task.title,
                risk_level=task.risk_level,
                requires_confirmation=task.requires_confirmation,
                params=task.params,
                message="NG audio generation submitted.",
            ),
            state=self._task_store.submit(context.state, task.task_id),
        )


__all__ = ["AudioGenerationHandler"]
