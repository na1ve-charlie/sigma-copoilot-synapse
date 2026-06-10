"""Tests for Themis Decision Interpreter (Task 08).

Covers:
- Pure selection (time_range + judgement)
- Selection + delete action
- Selection + trend action
- active reference (existing)
- active reference + new conditions (derived)
- missing active selection
- invalid TimeRange
- unknown entity_type
- unknown action intent
- low / ambiguous verdict
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from synapse.domains.data_management.selection_interpreter import (
    DerivedSelectionCriteria,
    ExistingSelectionReference,
    NewSelectionCriteria,
    OperationIntent,
    SelectionClarificationRequired,
    interpret_decision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_NOW = datetime(2026, 6, 10, 15, 30, 0, tzinfo=_UTC)
_ACTIVE = "sel_active_001"


def _dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=_UTC)


def _slot_op(entity_type: str, action: str, target: str | tuple[str, ...]) -> object:
    return SimpleNamespace(entity_type=entity_type, action=action, target=target)


def _action_intent(name: str) -> object:
    return SimpleNamespace(name=name, score=0.95)


def _decision(
    verdict: str = "clear",
    slot_operations: tuple[object, ...] = (),
    action_intents: tuple[object, ...] = (),
) -> object:
    return SimpleNamespace(
        verdict=verdict,
        slot_operations=slot_operations,
        action_intents=action_intents,
    )


# ======================================================================
# Pure Selection
# ======================================================================


class TestPureSelection:
    def test_time_range_and_judgement(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_time_range", "replace", "relative:last_7_days"),
                _slot_op("record_judgement", "replace", "不合格"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, NewSelectionCriteria)
        assert len(result.criteria.time_ranges) == 1
        assert result.criteria.time_ranges[0].start == _dt(2026, 6, 4, 0, 0, 0)
        assert result.criteria.judgement_results == ("不合格",)
        assert result.operation_intents == ()

    def test_multiple_time_ranges(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_time_range", "replace", "relative:last_7_days"),
                _slot_op("record_time_range", "add", "relative:today"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, NewSelectionCriteria)
        assert len(result.criteria.time_ranges) == 2

    def test_remove_time_range(self) -> None:
        # Start with last_7_days, then remove it, add today
        d = _decision(
            slot_operations=(
                _slot_op("record_time_range", "replace", "relative:last_7_days"),
                _slot_op("record_time_range", "remove", "relative:last_7_days"),
                _slot_op("record_time_range", "add", "relative:today"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, NewSelectionCriteria)
        assert len(result.criteria.time_ranges) == 1
        assert result.criteria.time_ranges[0].start == _dt(2026, 6, 10, 0, 0, 0)

    def test_judgement_add(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_judgement", "replace", "不合格"),
                _slot_op("record_judgement", "add", "合格"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, NewSelectionCriteria)
        assert result.criteria.judgement_results == ("不合格", "合格")

    def test_judgement_remove(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_judgement", "replace", ("不合格", "合格")),
                _slot_op("record_judgement", "remove", "合格"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, NewSelectionCriteria)
        assert result.criteria.judgement_results == ("不合格",)


# ======================================================================
# Selection + operations
# ======================================================================


class TestSelectionWithOperation:
    def test_selection_plus_delete(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_time_range", "replace", "relative:last_7_days"),
                _slot_op("record_judgement", "replace", "不合格"),
            ),
            action_intents=(
                _action_intent("task.nvh.data_management.records.delete"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, NewSelectionCriteria)
        assert len(result.operation_intents) == 1
        assert result.operation_intents[0].operation_type == "data_delete"

    def test_selection_plus_trend(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_time_range", "replace", "relative:last_7_days"),
            ),
            action_intents=(
                _action_intent("task.nvh.data_observation.indicator_trend_analysis.trend"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, NewSelectionCriteria)
        assert result.operation_intents[0].operation_type == "trend_analysis"


# ======================================================================
# Active reference
# ======================================================================


class TestActiveReference:
    def test_existing_reference(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("selection_reference", "replace", "active"),
            ),
            action_intents=(
                _action_intent("task.nvh.data_management.records.delete"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=_ACTIVE)
        assert isinstance(result, ExistingSelectionReference)
        assert result.selection_id == _ACTIVE
        assert result.operation_intents[0].operation_type == "data_delete"

    def test_derived_selection(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("selection_reference", "replace", "active"),
                _slot_op("record_time_range", "add", "relative:today"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=_ACTIVE)
        assert isinstance(result, DerivedSelectionCriteria)
        assert result.base_selection_id == _ACTIVE
        assert len(result.criteria.time_ranges) == 1

    def test_missing_active_selection(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("selection_reference", "replace", "active"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, SelectionClarificationRequired)
        assert result.reason == "missing_active_selection"


# ======================================================================
# Invalid / edge cases
# ======================================================================


class TestInvalidCases:
    def test_invalid_time_range(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_time_range", "replace", "garbage"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, SelectionClarificationRequired)
        assert result.reason == "invalid_time_range"

    def test_unknown_entity_type(self) -> None:
        """Unknown entity types are silently skipped."""
        d = _decision(
            slot_operations=(
                _slot_op("some_unknown_entity", "replace", "foo"),
                _slot_op("record_judgement", "replace", "不合格"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, NewSelectionCriteria)
        assert result.criteria.judgement_results == ("不合格",)

    def test_unknown_action_intent(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_judgement", "replace", "不合格"),
            ),
            action_intents=(
                _action_intent("some.unknown.action"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, NewSelectionCriteria)
        assert result.operation_intents[0].operation_type == "unsupported"


# ======================================================================
# Verdict gating
# ======================================================================


class TestVerdictGating:
    def test_low_verdict(self) -> None:
        d = _decision(verdict="low")
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, SelectionClarificationRequired)
        assert result.reason == "verdict_low"

    def test_ambiguous_verdict(self) -> None:
        d = _decision(verdict="ambiguous")
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, SelectionClarificationRequired)
        assert result.reason == "verdict_ambiguous"


# ======================================================================
# No actionable intent
# ======================================================================


class TestNoActionableIntent:
    def test_unrecognised_operation_only(self) -> None:
        d = _decision(
            action_intents=(
                _action_intent("some.unknown.action"),
            ),
        )
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, SelectionClarificationRequired)
        assert result.reason == "unsupported_operation"

    def test_empty_decision(self) -> None:
        d = _decision()
        result = interpret_decision(d, now=_NOW, active_selection_id=None)
        assert isinstance(result, SelectionClarificationRequired)
        assert result.reason == "no_actionable_intent"
