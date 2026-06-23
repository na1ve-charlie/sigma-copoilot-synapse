from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from maia.api import RiskLevel
from maia.recognition.report import RecognitionReport, RecognitionSlotOperation
from maia.selection.sets import SelectionSet

_OPERATIONS: dict[str, tuple[str, RiskLevel, tuple[str, ...]]] = {
    "task.nvh.record_search": ("Record search", "low", ()),
    "task.nvh.excel_export": ("Excel export", "medium", ()),
    "task.nvh.origin_data_export": ("Origin data export", "medium", ()),
    "task.nvh.data_backup": ("Data backup", "medium", ()),
    "task.nvh.data_delete": ("Data delete", "high", ()),
    "task.nvh.data_observation.view_indicator_result": ("View indicator result", "low", ()),
    "task.nvh.data_observation.indicator_trend_analysis.trend": ("Indicator trend", "low", ()),
    "task.nvh.report.download": ("Report download", "medium", ()),
    "task.nvh.report.generate": ("Report generation", "medium", ()),
    "task.nvh.audio.generate": ("Audio generation", "medium", ()),
    "task.nvh.colormap.recompute": ("Colormap recompute", "medium", ()),
}


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str
    name: str
    title: str
    operations: tuple[str, ...]
    selection_set_id: str
    selection_hash: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    requires_confirmation: bool


class PendingTask(TaskSpec):
    missing_slots: tuple[str, ...] = ()


class TaskPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task: TaskSpec
    payload: dict[str, Any] = Field(default_factory=dict)


class PendingConfirmation(TaskPreview):
    reason: str
    token: str
    expires_at: datetime


class OperationRegistry:
    def resolve(self, names: tuple[str, ...]) -> tuple[tuple[str, str, RiskLevel, tuple[str, ...]], ...]:
        try:
            return tuple((name, *_OPERATIONS[name]) for name in names)
        except KeyError as error:
            raise LookupError(f"unsupported operation: {error.args[0]}") from None


class RiskPolicy:
    def evaluate(self, operations: tuple[tuple[str, str, RiskLevel, tuple[str, ...]], ...]) -> tuple[RiskLevel, bool]:
        risk_level = "high" if any(item[2] == "high" for item in operations) else "medium" if any(item[2] == "medium" for item in operations) else "low"
        return risk_level, risk_level != "low"


class TaskSpecBuilder:
    def __init__(self, *, registry: OperationRegistry | None = None, risk_policy: RiskPolicy | None = None, id_factory: Callable[[], str] | None = None) -> None:
        self._registry = registry or OperationRegistry()
        self._risk_policy = risk_policy or RiskPolicy()
        self._id_factory = id_factory or (lambda: f"task-{uuid4().hex}")

    def build(self, report: RecognitionReport, selection_set: SelectionSet) -> TaskSpec | PendingTask | None:
        names = tuple(intent.name for intent in report.action_intents)
        if not names:
            return None
        operations = self._registry.resolve(names)
        risk_level, requires_confirmation = self._risk_policy.evaluate(operations)
        return self._finalize(
            report,
            TaskSpec(
                task_id=self._id_factory(),
                name="+".join(names),
                title=" -> ".join(item[1] for item in operations),
                operations=names,
                selection_set_id=selection_set.selection_set_id,
                selection_hash=selection_set.selection_hash,
                risk_level=risk_level,
                requires_confirmation=requires_confirmation,
            ),
            operations,
        )

    def resume(self, task: TaskSpec | PendingTask | None, report: RecognitionReport, *, selection_set: SelectionSet | None = None) -> TaskSpec | PendingTask | None:
        if task is None:
            return None
        return self._finalize(report, self.rebase(task, selection_set) if selection_set else task, self._registry.resolve(task.operations))

    def rebase(self, task: TaskSpec | PendingTask, selection_set: SelectionSet) -> TaskSpec | PendingTask:
        return task.model_copy(update={"selection_set_id": selection_set.selection_set_id, "selection_hash": selection_set.selection_hash})

    def _finalize(self, report: RecognitionReport, task: TaskSpec | PendingTask, operations: tuple[tuple[str, str, RiskLevel, tuple[str, ...]], ...]) -> TaskSpec | PendingTask:
        params = dict(task.params)
        required_slots = tuple(dict.fromkeys(slot for item in operations for slot in item[3]))
        _apply_report_params(params, report.slot_operations, required_slots)
        missing = tuple(slot for slot in required_slots if slot not in params)
        payload = task.model_dump(mode="python")
        payload.pop("missing_slots", None)
        payload["params"] = params
        return PendingTask(**payload, missing_slots=missing) if missing else TaskSpec(**payload)


class ConfirmationError(ValueError):
    pass


class ConfirmationService:
    def __init__(self, *, token_factory: Callable[[], str] | None = None, clock: Callable[[], datetime] | None = None, ttl: timedelta = timedelta(minutes=10)) -> None:
        self._token_factory = token_factory or (lambda: f"confirm-{uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl

    def preview(self, task: TaskSpec, *, record_count: int | None = None) -> TaskPreview | PendingConfirmation:
        payload = {} if record_count is None else {"record_count": record_count}
        if not task.requires_confirmation:
            return TaskPreview(task=task, payload=payload)
        return PendingConfirmation(
            task=task,
            payload=payload,
            reason=f"{task.risk_level}_risk_operation",
            token=self._token_factory(),
            expires_at=self._clock() + self._ttl,
        )

    def confirm(self, pending: PendingConfirmation, *, token: str, selection_hash: str, now: datetime | None = None) -> TaskSpec:
        if token != pending.token:
            raise ConfirmationError("confirmation token is invalid")
        if selection_hash != pending.task.selection_hash:
            raise ConfirmationError("selection hash is stale")
        if (now or self._clock()) > pending.expires_at:
            raise ConfirmationError("confirmation has expired")
        return pending.task


def _apply_report_params(params: dict[str, Any], slot_operations: tuple[RecognitionSlotOperation, ...], allowed_slots: tuple[str, ...]) -> None:
    for slot_operation in slot_operations:
        if slot_operation.entity_type not in allowed_slots:
            continue
        actions = _as_tuple(slot_operation.action)
        targets = _as_tuple(slot_operation.target)
        valids = _broadcast(_as_tuple(slot_operation.slot_valid), max(len(actions), len(targets)))
        values = _broadcast(targets, len(valids))
        if len(actions) == 1:
            kept = tuple(target for target, valid in zip(values, valids, strict=True) if valid)
            if kept:
                _apply_batch(params, slot_operation.entity_type, str(actions[0]), kept)
            continue
        for action, target, valid in zip(_broadcast(actions, len(valids)), values, valids, strict=True):
            if valid:
                _apply_batch(params, slot_operation.entity_type, str(action), (target,))


def _apply_batch(params: dict[str, Any], entity_type: str, action: str, targets: tuple[object, ...]) -> None:
    if action == "clear":
        params.pop(entity_type, None)
        return
    if action == "replace":
        params[entity_type] = targets[0] if len(targets) == 1 else targets
        return
    current = _as_tuple(params.get(entity_type)) if entity_type in params else ()
    if action == "add":
        merged = tuple(dict.fromkeys(current + targets))
        params[entity_type] = merged[0] if len(merged) == 1 else merged
        return
    if action in {"remove", "exclude"}:
        remaining = tuple(value for value in current if value not in set(targets))
        if remaining:
            params[entity_type] = remaining[0] if len(remaining) == 1 else remaining
        else:
            params.pop(entity_type, None)


def _as_tuple(value: object) -> tuple[object, ...]:
    return value if isinstance(value, tuple) else (value,)


def _broadcast(values: tuple[object, ...], size: int) -> tuple[object, ...]:
    if len(values) == size:
        return values
    if len(values) == 1:
        return values * size
    raise ValueError("slot operation arrays must align")


__all__ = ["ConfirmationError", "ConfirmationService", "OperationRegistry", "PendingConfirmation", "PendingTask", "RiskPolicy", "TaskPreview", "TaskSpec", "TaskSpecBuilder"]
