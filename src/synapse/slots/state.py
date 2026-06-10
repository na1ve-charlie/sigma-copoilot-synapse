"""Immutable slot state and reversible diffs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse.slots.contracts import SlotOperation, SlotRef


@dataclass(frozen=True, slots=True)
class SlotState:
    """Committed slot values for one turn/session snapshot."""

    values: Mapping[SlotRef, Any] = field(default_factory=dict)

    @classmethod
    def from_values(cls, values: Mapping[SlotRef, Any]) -> "SlotState":
        return cls(values=dict(values))

    def get(self, ref: SlotRef, default: Any = None) -> Any:
        return self.values.get(ref, default)

    def apply(self, operation: SlotOperation) -> "SlotState":
        values = dict(self.values)
        ref = operation.ref

        if operation.kind == "replace":
            values[ref] = operation.value
        elif operation.kind == "add":
            values[ref] = _append_unique(values.get(ref), operation.value)
        elif operation.kind == "remove":
            remaining = [
                item for item in _as_list(values.get(ref)) if item != operation.value
            ]
            if remaining:
                values[ref] = remaining
            else:
                values.pop(ref, None)
        elif operation.kind == "clear":
            values.pop(ref, None)
        else:
            raise ValueError(f"unsupported slot operation kind: {operation.kind}")

        return SlotState(values=values)

    def apply_all(self, operations: Iterable[SlotOperation]) -> "SlotState":
        state = self
        for operation in operations:
            state = state.apply(operation)
        return state

    def diff(self, other: "SlotState") -> "SlotStateDiff":
        refs = set(self.values) | set(other.values)
        changes = []
        for ref in sorted(refs, key=lambda item: (item.domain_id, item.name)):
            before_exists = ref in self.values
            after_exists = ref in other.values
            before = self.values.get(ref)
            after = other.values.get(ref)
            if before_exists != after_exists or before != after:
                changes.append(
                    SlotStateChange(
                        ref=ref,
                        before=before,
                        after=after,
                        before_exists=before_exists,
                        after_exists=after_exists,
                    )
                )
        return SlotStateDiff(tuple(changes))


@dataclass(frozen=True, slots=True)
class SlotStateChange:
    ref: SlotRef
    before: Any
    after: Any
    before_exists: bool
    after_exists: bool


@dataclass(frozen=True, slots=True)
class SlotStateDiff:
    changes: tuple[SlotStateChange, ...]

    def rollback_operations(self) -> tuple[SlotOperation, ...]:
        operations = []
        for change in reversed(self.changes):
            if change.before_exists:
                operations.append(SlotOperation.replace(change.ref, change.before))
            else:
                operations.append(SlotOperation.clear(change.ref))
        return tuple(operations)


def _append_unique(current: Any, value: Any) -> list[Any]:
    values = _as_list(current)
    if value not in values:
        values.append(value)
    return values


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]
