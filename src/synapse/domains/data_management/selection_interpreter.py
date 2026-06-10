"""Themis Decision Interpreter for data-management domain (Task 08).

Consumes a Themis recognition result (or duck-type equivalent) and
produces a ``SelectionIntentResolution`` — a domain-level description
of what the user wants to do with record selections.

**No** Themis private types, **no** SelectionService calls, **no**
repository writes, **no** final-plan construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from synapse.domains.data_management.selection_criteria import (
    RecordSelectionCriteria,
)
from synapse.selection.time_ranges import parse_time_range as _parse_tr


# ===================================================================
# Result types
# ===================================================================


@dataclass(frozen=True, slots=True)
class OperationIntent:
    """A recognised action intent from the Themis decision."""

    operation_type: Literal["data_delete", "trend_analysis", "unsupported"]
    intent_name: str


@dataclass(frozen=True, slots=True)
class NewSelectionCriteria:
    """Build a brand-new selection from the extracted criteria."""

    criteria: RecordSelectionCriteria
    operation_intents: tuple[OperationIntent, ...] = ()


@dataclass(frozen=True, slots=True)
class ExistingSelectionReference:
    """Re-use the currently active ``SelectionSet`` without changes."""

    selection_id: str
    operation_intents: tuple[OperationIntent, ...] = ()


@dataclass(frozen=True, slots=True)
class DerivedSelectionCriteria:
    """Derive a new selection from an existing one + extra conditions."""

    base_selection_id: str
    criteria: RecordSelectionCriteria
    operation_intents: tuple[OperationIntent, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectionClarificationRequired:
    """The decision is ambiguous, invalid, or missing required info."""

    reason: str
    missing_fields: tuple[str, ...] = ()


SelectionIntentResolution = (
    NewSelectionCriteria
    | ExistingSelectionReference
    | DerivedSelectionCriteria
    | SelectionClarificationRequired
)


# ===================================================================
# Interpret
# ===================================================================


_RECORD_TIME_RANGE = "record_time_range"
_RECORD_JUDGEMENT = "record_judgement"
_SELECTION_REFERENCE = "selection_reference"

_ACTION_MAP: dict[str, str] = {
    "task.nvh.data_management.records.delete": "data_delete",
    "task.nvh.data_observation.indicator_trend_analysis.trend": "trend_analysis",
}


def _str(v: object) -> str:
    return str(v) if v is not None else ""


def _as_list(v: object) -> tuple[str, ...]:
    if isinstance(v, (list, tuple)):
        return tuple(str(x) for x in v)
    return (_str(v),)


def _verdict(decision: object) -> str:
    v = getattr(decision, "verdict", None)
    if v is None:
        raise ValueError("decision.verdict is missing — cannot interpret decision")
    return _str(getattr(v, "value", v))


def _slot_operations(decision: object) -> tuple[object, ...]:
    return tuple(getattr(decision, "slot_operations", ()))


def _action_intents(decision: object) -> tuple[object, ...]:
    return tuple(getattr(decision, "action_intents", ()))


def _extract_operations(intents: tuple[object, ...]) -> tuple[OperationIntent, ...]:
    result: list[OperationIntent] = []
    for intent in intents:
        name = _str(getattr(intent, "name", ""))
        op_type = _ACTION_MAP.get(name, "unsupported")
        result.append(OperationIntent(operation_type=op_type, intent_name=name))  # type: ignore[arg-type]
    return tuple(result)


def _has_unsupported(ops: tuple[OperationIntent, ...]) -> bool:
    return any(o.operation_type == "unsupported" for o in ops)


def _needs_selection(ops: tuple[OperationIntent, ...]) -> bool:
    return any(o.operation_type in ("data_delete", "trend_analysis") for o in ops)


# ---------------------------------------------------------------------------
# interpret_decision
# ---------------------------------------------------------------------------


def interpret_decision(
    decision: object,
    *,
    now: datetime,
    active_selection_id: str | None,
) -> SelectionIntentResolution:
    """Convert a Themis recognition result into a domain-level selection intent.

    Parameters
    ----------
    decision:
        A Themis ``IntentDecision`` (or duck-type) with attributes
        ``verdict``, ``slot_operations``, and ``action_intents``.
    now:
        Timezone-aware reference instant for resolving relative time ranges.
    active_selection_id:
        The ID of the currently-active ``SelectionSet`` in the conversation,
        or ``None``.
    """
    v = _verdict(decision)

    if v == "low":
        return SelectionClarificationRequired(reason="verdict_low")
    if v == "ambiguous":
        return SelectionClarificationRequired(reason="verdict_ambiguous")

    slots = _slot_operations(decision)
    actions = _action_intents(decision)
    ops = _extract_operations(actions)

    has_ref = False
    criteria_kwargs: dict = {}

    time_ranges_buffer: list[object] = []
    time_range_errors: list[str] = []

    for op in slots:
        entity_type = _str(getattr(op, "entity_type", ""))
        action = _str(getattr(op, "action", ""))
        targets = _as_list(getattr(op, "target", ""))

        if entity_type == _SELECTION_REFERENCE:
            if "active" in targets:
                has_ref = True
            continue

        if entity_type == _RECORD_TIME_RANGE:
            for t in targets:
                try:
                    tr = _parse_tr(t, now=now)
                except ValueError:
                    time_range_errors.append(t)
                    continue

                if action == "remove":
                    time_ranges_buffer = [
                        e for e in time_ranges_buffer if e != tr
                    ]
                else:
                    time_ranges_buffer.append(tr)
            continue

        if entity_type == _RECORD_JUDGEMENT:
            if action == "remove":
                existing = set(criteria_kwargs.get("judgement_results", ()))
                criteria_kwargs["judgement_results"] = tuple(
                    j for j in existing if j not in targets
                )
            elif action == "add":
                existing = criteria_kwargs.get("judgement_results", ())
                criteria_kwargs["judgement_results"] = existing + targets
            else:
                criteria_kwargs["judgement_results"] = targets
            continue

    if time_range_errors:
        return SelectionClarificationRequired(
            reason="invalid_time_range",
            missing_fields=tuple(time_range_errors),
        )
    if time_ranges_buffer:
        criteria_kwargs["time_ranges"] = tuple(time_ranges_buffer)

    has_criteria = bool(criteria_kwargs)
    criteria = (
        RecordSelectionCriteria(**criteria_kwargs)
        if has_criteria
        else RecordSelectionCriteria()
    )

    if has_ref and has_criteria and active_selection_id is not None:
        return DerivedSelectionCriteria(
            base_selection_id=active_selection_id,
            criteria=criteria,
            operation_intents=ops,
        )

    if has_ref and active_selection_id is not None:
        return ExistingSelectionReference(
            selection_id=active_selection_id,
            operation_intents=ops,
        )

    if has_ref and active_selection_id is None:
        return SelectionClarificationRequired(
            reason="missing_active_selection",
        )

    if has_criteria:
        return NewSelectionCriteria(criteria=criteria, operation_intents=ops)

    if _needs_selection(ops):
        return SelectionClarificationRequired(
            reason="missing_selection_criteria",
        )

    if _has_unsupported(ops):
        return SelectionClarificationRequired(
            reason="unsupported_operation",
        )

    return SelectionClarificationRequired(reason="no_actionable_intent")
