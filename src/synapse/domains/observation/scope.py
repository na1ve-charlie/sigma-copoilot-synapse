from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from synapse.slots.state import SlotState


ScopeStatus = Literal["pass", "resolved", "conflict", "invalid"]
TaskDataTypeResolver = Callable[[str], Sequence[str]]


@dataclass(frozen=True, slots=True)
class ObservationScopeDecision:
    status: ScopeStatus
    source: str | None = None
    data_type: str | None = None
    candidates: tuple[str, ...] = ()

    @classmethod
    def pass_through(cls) -> "ObservationScopeDecision":
        return cls(status="pass")

    @classmethod
    def resolved(
        cls,
        data_type: str,
        *,
        source: str,
    ) -> "ObservationScopeDecision":
        return cls(status="resolved", source=source, data_type=data_type)

    @classmethod
    def conflict(
        cls,
        *,
        source: str,
        candidates: Sequence[str],
    ) -> "ObservationScopeDecision":
        return cls(
            status="conflict",
            source=source,
            candidates=_dedupe_strings(candidates),
        )

    @classmethod
    def invalid(
        cls,
        *,
        source: str,
        candidates: Sequence[str] = (),
    ) -> "ObservationScopeDecision":
        return cls(
            status="invalid",
            source=source,
            candidates=_dedupe_strings(candidates),
        )


@dataclass(frozen=True, slots=True)
class ObservationScopeContext:
    explicit_data_types: tuple[str, ...] = ()
    indicator_data_types: tuple[str, ...] = ()
    selected_data_type: str | None = None
    pending_task_name: str | None = None
    active_task_name: str | None = None
    available_data_types: tuple[str, ...] = ()


class ObservationScopeRule(Protocol):
    def resolve(
        self,
        context: ObservationScopeContext,
    ) -> ObservationScopeDecision:
        ...


class ObservationScopePolicy:
    def __init__(self, rules: Sequence[ObservationScopeRule]) -> None:
        self._rules = tuple(rules)

    def resolve(
        self,
        context: ObservationScopeContext,
    ) -> ObservationScopeDecision:
        for rule in self._rules:
            decision = rule.resolve(context)
            if decision.status != "pass":
                return decision
        return ObservationScopeDecision.pass_through()


class ExplicitDataTypeScopeRule:
    def resolve(
        self,
        context: ObservationScopeContext,
    ) -> ObservationScopeDecision:
        return _decision_from_data_types(
            context.explicit_data_types,
            source="explicit_data_type",
        )


class UniqueIndicatorDataTypeScopeRule:
    def resolve(
        self,
        context: ObservationScopeContext,
    ) -> ObservationScopeDecision:
        return _decision_from_data_types(
            context.indicator_data_types,
            source="indicator_unique",
        )


class SelectedDataTypeScopeRule:
    def resolve(
        self,
        context: ObservationScopeContext,
    ) -> ObservationScopeDecision:
        if context.selected_data_type is None:
            return ObservationScopeDecision.pass_through()
        if (
            context.available_data_types
            and context.selected_data_type not in context.available_data_types
        ):
            return ObservationScopeDecision.invalid(
                source="selected_data_type",
                candidates=context.available_data_types,
            )
        return ObservationScopeDecision.resolved(
            context.selected_data_type,
            source="selected_data_type",
        )


class PendingTaskScopeRule:
    def __init__(self, resolve_task_data_types: TaskDataTypeResolver) -> None:
        self._resolve_task_data_types = resolve_task_data_types

    def resolve(
        self,
        context: ObservationScopeContext,
    ) -> ObservationScopeDecision:
        if not context.pending_task_name:
            return ObservationScopeDecision.pass_through()
        return _decision_from_data_types(
            self._resolve_task_data_types(context.pending_task_name),
            source="pending_task",
        )


class ActiveTaskScopeRule:
    def __init__(self, resolve_task_data_types: TaskDataTypeResolver) -> None:
        self._resolve_task_data_types = resolve_task_data_types

    def resolve(
        self,
        context: ObservationScopeContext,
    ) -> ObservationScopeDecision:
        if not context.active_task_name:
            return ObservationScopeDecision.pass_through()
        return _decision_from_data_types(
            self._resolve_task_data_types(context.active_task_name),
            source="active_task",
        )


def build_observation_scope_policy(
    resolve_task_data_types: TaskDataTypeResolver | None = None,
) -> ObservationScopePolicy:
    resolver = resolve_task_data_types or (lambda _: ())
    return ObservationScopePolicy(
        (
            ExplicitDataTypeScopeRule(),
            UniqueIndicatorDataTypeScopeRule(),
            SelectedDataTypeScopeRule(),
            PendingTaskScopeRule(resolver),
            ActiveTaskScopeRule(resolver),
        )
    )


def build_observation_scope_context(
    *,
    slot_state: SlotState,
    artifacts: Mapping[str, object],
    explicit_data_types: Sequence[str] = (),
    indicator_data_types: Sequence[str] = (),
    available_data_types: Sequence[str] = (),
) -> ObservationScopeContext:
    return ObservationScopeContext(
        explicit_data_types=_dedupe_strings(explicit_data_types),
        indicator_data_types=_dedupe_strings(indicator_data_types),
        selected_data_type=_selected_data_type(slot_state),
        pending_task_name=_task_name(artifacts.get("pending_task")),
        active_task_name=_task_name(
            artifacts.get("active_task") or artifacts.get("active_task_name")
        ),
        available_data_types=_dedupe_strings(available_data_types),
    )


def _selected_data_type(state: SlotState) -> str | None:
    values = _slot_values(state, "data_types")
    return values[0] if len(values) == 1 else None


def _slot_values(state: SlotState, slot_name: str) -> list[str]:
    for ref, value in state.values.items():
        if ref.name != slot_name:
            continue
        if isinstance(value, str):
            return [value]
        if isinstance(value, Sequence) and not isinstance(value, bytes):
            return [str(item) for item in value]
        return [str(value)]
    return []


def _task_name(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        for key in ("action_name", "name"):
            current = value.get(key)
            if isinstance(current, str) and current:
                return current
    return None


def _decision_from_data_types(
    data_types: Sequence[str],
    *,
    source: str,
) -> ObservationScopeDecision:
    normalized = _dedupe_strings(data_types)
    if not normalized:
        return ObservationScopeDecision.pass_through()
    if len(normalized) == 1:
        return ObservationScopeDecision.resolved(normalized[0], source=source)
    return ObservationScopeDecision.conflict(source=source, candidates=normalized)


def _dedupe_strings(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(str(value) for value in values if str(value))
    )
