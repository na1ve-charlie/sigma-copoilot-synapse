from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from synapse.planning.planner import PlanningContext
from synapse.planning.display import slot_label, slot_prompt_message
from synapse.planning.plans import (
    ClarifyPlan,
    ContextClearPlan,
    ContextUpdatePlan,
    Plan,
    Prompt,
    PromptCandidate,
    ReplyPlan,
    RiskLevel,
    SlotStateChangeView,
    SlotStateDiffView,
    TaskPlan,
)
from synapse.planning.resolver_query import ResolverQueryHandler
from synapse.recognition import (
    CANDIDATE_CATALOG_ARTIFACT,
    CandidateCatalog,
    CandidateItem,
)
from synapse.slots.state import SlotState


CONTEXT_CLEAR_INTENT = "task.nvh.context_management.clear_context"
CURRENT_CONTEXT_INTENT = "inquiry.nvh.context_management.current"
RESOLVER_QUERY_PREFIX = "inquiry.nvh.resolver_query."


class TaskDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    intent_names: tuple[str, ...]
    title: str
    risk_level: RiskLevel
    requires_confirmation: bool
    required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...] = ()


class TaskParamProvider(Protocol):
    def params_for(
        self,
        task: TaskDefinition,
        context: PlanningContext,
    ) -> Mapping[str, Any]:
        ...


class TaskCatalog:
    def __init__(self, tasks: Sequence[TaskDefinition]) -> None:
        self._tasks = tuple(tasks)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TaskCatalog":
        tasks = [
            TaskDefinition(name=name, **value)
            for name, value in data.items()
        ]
        return cls(tasks)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TaskCatalog":
        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, Mapping):
            raise TypeError("task YAML must contain a mapping")
        return cls.from_mapping(loaded)

    @classmethod
    def from_yamls(cls, paths: Sequence[str | Path]) -> "TaskCatalog":
        data: dict[str, Any] = {}
        for path in paths:
            loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, Mapping):
                raise TypeError(f"task YAML must contain a mapping: {path}")
            duplicates = set(data).intersection(loaded)
            if duplicates:
                joined = ", ".join(sorted(duplicates))
                raise ValueError(f"duplicate task definitions: {joined}")
            data.update(loaded)
        return cls.from_mapping(data)

    @classmethod
    def from_directory(cls, directory: str | Path) -> "TaskCatalog":
        paths = sorted(Path(directory).glob("*.yaml"))
        if not paths:
            raise FileNotFoundError(f"no task YAML files found in: {directory}")
        return cls.from_yamls(paths)

    def match(self, intent_names: Sequence[str]) -> TaskDefinition | None:
        requested = set(intent_names)
        for task in self._tasks:
            if requested.intersection(task.intent_names):
                return task
        return None


class TaskPlanBuilder:
    def __init__(
        self,
        catalog: TaskCatalog,
        *,
        candidates: Mapping[str, Sequence[Any]] | None = None,
        resolver_query_handler: ResolverQueryHandler | None = None,
        task_param_providers: Sequence[TaskParamProvider] = (),
    ) -> None:
        self._catalog = catalog
        self._candidates = candidates or {}
        self._task_param_providers = tuple(task_param_providers)
        self._resolver_query_handler = (
            resolver_query_handler
            or _CandidateResolverQueryHandler(self)
        )

    async def build(self, context: PlanningContext) -> Plan:
        intent_names = _decision_intent_names(context.decision)
        diff = _slot_state_diff_view(context)
        if CONTEXT_CLEAR_INTENT in intent_names:
            return ContextClearPlan(slot_state_diff=diff)

        if CURRENT_CONTEXT_INTENT in intent_names:
            return ReplyPlan(
                message="当前上下文如下。",
                data={"slots": _projected_slots(context.slot_state)},
                slot_state_diff=diff,
            )

        if _has_resolver_query(intent_names):
            plan = await self._resolver_query_handler.build(
                context,
                intent_names,
            )
            if plan is not None:
                return plan

        task = self._catalog.match(intent_names) or _active_task_for_slot_only_turn(
            self._catalog,
            context,
            intent_names,
        )
        if task is None:
            if context.slot_state_diff.changes:
                return ContextUpdatePlan(
                    message="已更新当前上下文。",
                    projected_slots=_projected_slots(context.slot_state),
                    slot_state_diff=diff,
                )
            return ReplyPlan(message="未匹配到可执行任务。")

        missing = [
            slot for slot in task.required_slots
            if not _has_value(_slot_value(context.slot_state, slot))
        ]
        if missing:
            return ClarifyPlan(
                reason="missing_slots",
                message="缺少必填任务参数。",
                pending_task=task.name,
                missing_slots=missing,
                prompts=[self._slot_prompt(slot, context) for slot in missing],
                slot_state_diff=diff,
            )

        return TaskPlan(
            status="needs_confirmation" if task.requires_confirmation else "ready",
            name=task.name,
            title=task.title,
            risk_level=task.risk_level,
            requires_confirmation=task.requires_confirmation,
            params=_task_params(
                context.slot_state,
                task,
                self._task_param_providers,
                context,
            ),
            message=f"任务已就绪：{task.title}",
            slot_state_diff=diff,
        )

    def _slot_prompt(self, slot: str, context: PlanningContext) -> Prompt:
        values = self._slot_candidates(slot, context)
        if not values:
            raise KeyError(f"missing candidates for slot: {slot}")
        return Prompt(
            id=slot,
            target="slot",
            label=slot_label(slot),
            message=slot_prompt_message(slot),
            required=True,
            input_type="multi_select",
            candidates=[_prompt_candidate(value) for value in values],
        )

    def _slot_candidates(
        self,
        slot: str,
        context: PlanningContext,
    ) -> Sequence[Any]:
        values = self._candidates.get(slot)
        if values:
            return values
        catalog = context.artifacts.get(CANDIDATE_CATALOG_ARTIFACT)
        if isinstance(catalog, CandidateCatalog):
            return catalog.candidates_for_entity(slot)
        return ()


def _decision_intent_names(decision: Any) -> tuple[str, ...]:
    intents = getattr(decision, "action_intents", ())
    return tuple(name for intent in intents if (name := _intent_name(intent)))


def _prompt_candidate(value: Any) -> PromptCandidate:
    if isinstance(value, CandidateItem):
        return PromptCandidate(
            value=value.value,
            label=value.label or value.value,
        )
    return PromptCandidate(value=value, label=str(value))


def _candidate_value(value: Any) -> str:
    if isinstance(value, CandidateItem):
        return value.value
    return str(value)


def _intent_name(intent: Any) -> str | None:
    if isinstance(intent, str):
        return intent
    if isinstance(intent, Mapping):
        value = intent.get("name")
        return value if isinstance(value, str) else None
    value = getattr(intent, "name", None)
    return value if isinstance(value, str) else None


def _has_resolver_query(intent_names: Sequence[str]) -> bool:
    return any(
        intent_name.startswith(RESOLVER_QUERY_PREFIX)
        for intent_name in intent_names
    )


def _active_task_for_slot_only_turn(
    catalog: TaskCatalog,
    context: PlanningContext,
    intent_names: Sequence[str],
) -> TaskDefinition | None:
    if intent_names or not context.slot_state_diff.changes:
        return None
    task_name = _task_name_from_artifacts(context.artifacts)
    if task_name is None:
        return None
    for task in catalog._tasks:
        if task.name == task_name:
            return task
    return None


def _task_name_from_artifacts(artifacts: Mapping[str, Any]) -> str | None:
    return _task_name_value(
        artifacts.get("active_task") or artifacts.get("active_task_name")
    )


def _task_name_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, Mapping):
        for key in ("name", "action_name"):
            current = value.get(key)
            if isinstance(current, str) and current:
                return current
    return None


class _CandidateResolverQueryHandler:
    def __init__(self, builder: TaskPlanBuilder) -> None:
        self._builder = builder

    async def build(
        self,
        context: PlanningContext,
        intent_names: tuple[str, ...],
    ) -> Plan | None:
        slot_name = _resolver_query_slot(intent_names, context, self._builder)
        if slot_name is None:
            return None
        values = self._builder._slot_candidates(slot_name, context)
        candidates = [_candidate_value(value) for value in values]
        return ReplyPlan(
            message=f"当前可用{slot_label(slot_name)}如下。",
            data={
                "slot_name": slot_name,
                "candidates": candidates,
            },
            suggestions=candidates,
            slot_state_diff=_slot_state_diff_view(context),
        )


def _resolver_query_slot(
    intent_names: Sequence[str],
    context: PlanningContext,
    builder: TaskPlanBuilder,
) -> str | None:
    known_slots = _known_slot_names(context, builder)
    for intent_name in intent_names:
        if not intent_name.startswith(RESOLVER_QUERY_PREFIX):
            continue
        key = intent_name.removeprefix(RESOLVER_QUERY_PREFIX)
        for candidate in _slot_name_candidates(key):
            if candidate in known_slots:
                return candidate
        return key
    return None


def _known_slot_names(
    context: PlanningContext,
    builder: TaskPlanBuilder,
) -> set[str]:
    result = set(builder._candidates)
    for task in builder._catalog._tasks:
        result.update(task.required_slots)
        result.update(task.optional_slots)
    candidate_catalog = context.artifacts.get(CANDIDATE_CATALOG_ARTIFACT)
    if isinstance(candidate_catalog, CandidateCatalog):
        result.update(candidate_catalog.by_entity)
    return result


def _slot_name_candidates(key: str) -> tuple[str, ...]:
    singular = key[:-1] if key.endswith("s") else key
    return (key, f"{singular}_names", singular)


def _slot_value(state: SlotState, slot_name: str) -> Any:
    for ref, value in state.values.items():
        if ref.name == slot_name:
            return value
    return None


def _has_value(value: Any) -> bool:
    return value not in (None, "", [])


def _task_params(
    state: SlotState,
    task: TaskDefinition,
    providers: Sequence[TaskParamProvider],
    context: PlanningContext,
) -> dict[str, Any]:
    slots = (*task.required_slots, *task.optional_slots)
    params = {
        slot: value
        for slot in slots
        if _has_value(value := _slot_value(state, slot))
    }
    for provider in providers:
        params.update(provider.params_for(task, context))
    return params


def _projected_slots(state: SlotState) -> dict[str, Any]:
    return {ref.name: value for ref, value in state.values.items()}


def _slot_state_diff_view(context: PlanningContext) -> SlotStateDiffView:
    return SlotStateDiffView(
        changes=[
            SlotStateChangeView(
                slot=change.ref.name,
                label=slot_label(change.ref.name),
                before=change.before,
                after=change.after,
            )
            for change in context.slot_state_diff.changes
        ]
    )
