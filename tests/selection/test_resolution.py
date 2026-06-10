"""Tests for application-layer Selection Resolution (Task 13)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from synapse.application.selection_resolution import (
    SelectionClarifyResult,
    SelectionOnlyResult,
    SelectionOperationResult,
    SelectionUnsupportedResult,
    resolve_selection,
)
from synapse.selection.filters import AllOf, FieldEquals
from synapse.selection.models import RecordQuery, SelectionScope
from synapse.selection.query_port import (
    SelectionMaterialization,
    StaticSelectionQueryPort,
)
from synapse.selection.references import SelectionReferenceContext
from synapse.selection.repository import InMemorySelectionRepository
from synapse.selection.service import SelectionService


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

_UTC = timezone.utc

_DT = datetime(2026, 6, 10, 15, 30, 0, tzinfo=_UTC)


class _FakeClock:
    def __init__(self, dt: datetime = _DT) -> None:
        self._dt = dt

    def now(self) -> datetime:
        return self._dt


class _CountingIdGenerator:
    def __init__(self) -> None:
        self._counter = 0

    def new_id(self) -> str:
        self._counter += 1
        return f"sel_{self._counter:03d}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc(
    materialization: SelectionMaterialization | None = None,
) -> tuple[SelectionService, StaticSelectionQueryPort, InMemorySelectionRepository, _FakeClock, _CountingIdGenerator]:
    repo = InMemorySelectionRepository()
    clock = _FakeClock()
    idgen = _CountingIdGenerator()
    mat = materialization or SelectionMaterialization(125, "sigma-v184", "sha256:mat-hash")
    port = StaticSelectionQueryPort(mat)
    svc = SelectionService(port, repo, clock, idgen)
    return svc, port, repo, clock, idgen


def _ctx(active_id: str | None = None) -> SelectionReferenceContext:
    return SelectionReferenceContext(active_selection_id=active_id)


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
# Pure selection — no operation
# ======================================================================


class TestPureSelection:
    def test_new_selection_no_operation(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_judgement", "replace", "不合格"),
            ),
        )
        svc, _, _, _, _ = _svc()
        result = resolve_selection(d, now=_DT, ref_context=_ctx(), service=svc)
        assert isinstance(result, SelectionOnlyResult)
        assert result.selection.record_count == 125

    def test_selection_persisted(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_judgement", "replace", "不合格"),
            ),
        )
        svc, _, repo, _, _ = _svc()
        result = resolve_selection(d, now=_DT, ref_context=_ctx(), service=svc)
        assert repo.get(result.selection.id) is result.selection  # type: ignore[union-attr]


# ======================================================================
# Selection + operation
# ======================================================================


class TestSelectionWithOperation:
    def test_selection_plus_delete(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_judgement", "replace", "不合格"),
            ),
            action_intents=(
                _action_intent("task.nvh.data_management.records.delete"),
            ),
        )
        svc, _, _, _, _ = _svc()
        result = resolve_selection(d, now=_DT, ref_context=_ctx(), service=svc)
        assert isinstance(result, SelectionOperationResult)
        assert result.operation_type == "data_delete"  # type: ignore[union-attr]
        assert result.requires_confirmation is True  # type: ignore[union-attr]

    def test_selection_plus_trend(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_judgement", "replace", "不合格"),
            ),
            action_intents=(
                _action_intent("task.nvh.data_observation.indicator_trend_analysis.trend"),
            ),
        )
        svc, _, _, _, _ = _svc()
        result = resolve_selection(d, now=_DT, ref_context=_ctx(), service=svc)
        assert isinstance(result, SelectionOperationResult)
        assert result.operation_type == "trend_analysis"  # type: ignore[union-attr]
        assert result.requires_confirmation is False  # type: ignore[union-attr]


# ======================================================================
# Existing reference
# ======================================================================


class TestExistingReference:
    def test_existing_reference_with_operation(self) -> None:
        svc, _, _, _, _ = _svc()
        # Pre-create a selection
        pre = svc.create(
            RecordQuery(expression=FieldEquals("x", 1)),
            SelectionScope(),
        )
        d = _decision(
            slot_operations=(
                _slot_op("selection_reference", "replace", "active"),
            ),
            action_intents=(
                _action_intent("task.nvh.data_management.records.delete"),
            ),
        )
        result = resolve_selection(
            d, now=_DT, ref_context=_ctx(active_id=pre.id), service=svc,
        )
        assert isinstance(result, SelectionOperationResult)
        assert result.selection.id == pre.id  # type: ignore[union-attr]

    def test_existing_reference_not_found(self) -> None:
        svc, _, _, _, _ = _svc()
        d = _decision(
            slot_operations=(
                _slot_op("selection_reference", "replace", "active"),
            ),
        )
        result = resolve_selection(
            d, now=_DT, ref_context=_ctx(active_id="nonexistent"), service=svc,
        )
        assert isinstance(result, SelectionClarifyResult)
        assert result.reason == "selection_not_found"  # type: ignore[union-attr]


# ======================================================================
# Derived selection
# ======================================================================


class TestDerivedSelection:
    def test_derived_merges_queries(self) -> None:
        svc, port, _, _, _ = _svc()
        # Pre-create a selection
        old_q = RecordQuery(expression=FieldEquals("x", 1))
        pre = svc.create(old_q, SelectionScope())

        d = _decision(
            slot_operations=(
                _slot_op("selection_reference", "replace", "active"),
                _slot_op("record_judgement", "replace", "不合格"),
            ),
        )
        result = resolve_selection(
            d, now=_DT, ref_context=_ctx(active_id=pre.id), service=svc,
        )
        assert isinstance(result, SelectionOnlyResult)
        sel = result.selection  # type: ignore[union-attr]
        assert sel.derived_from == pre.id
        # Query should be AllOf(old, new)
        assert isinstance(sel.query.expression, AllOf)

    def test_derived_base_not_found(self) -> None:
        svc, _, _, _, _ = _svc()
        d = _decision(
            slot_operations=(
                _slot_op("selection_reference", "replace", "active"),
                _slot_op("record_judgement", "replace", "不合格"),
            ),
        )
        result = resolve_selection(
            d, now=_DT, ref_context=_ctx(active_id="nonexistent"), service=svc,
        )
        assert isinstance(result, SelectionClarifyResult)
        assert result.reason == "base_selection_not_found"  # type: ignore[union-attr]


# ======================================================================
# Clarification from interpreter
# ======================================================================


class TestClarification:
    def test_low_verdict(self) -> None:
        d = _decision(verdict="low")
        svc, _, _, _, _ = _svc()
        result = resolve_selection(d, now=_DT, ref_context=_ctx(), service=svc)
        assert isinstance(result, SelectionClarifyResult)
        assert result.reason == "verdict_low"  # type: ignore[union-attr]

    def test_ambiguous_verdict(self) -> None:
        d = _decision(verdict="ambiguous")
        svc, _, _, _, _ = _svc()
        result = resolve_selection(d, now=_DT, ref_context=_ctx(), service=svc)
        assert isinstance(result, SelectionClarifyResult)
        assert result.reason == "verdict_ambiguous"  # type: ignore[union-attr]


# ======================================================================
# Multiple operations
# ======================================================================


class TestMultipleOperations:
    def test_multiple_supported_ops_returns_unsupported(self) -> None:
        d = _decision(
            slot_operations=(
                _slot_op("record_judgement", "replace", "不合格"),
            ),
            action_intents=(
                _action_intent("task.nvh.data_management.records.delete"),
                _action_intent("task.nvh.data_observation.indicator_trend_analysis.trend"),
            ),
        )
        svc, _, _, _, _ = _svc()
        result = resolve_selection(d, now=_DT, ref_context=_ctx(), service=svc)
        assert isinstance(result, SelectionUnsupportedResult)
        assert result.reason == "multiple_operations"  # type: ignore[union-attr]


# ======================================================================
# Preserves query materialization
# ======================================================================


class TestQueryMaterialization:
    def test_projector_maps_to_query(self) -> None:
        svc, port, _, _, _ = _svc()
        d = _decision(
            slot_operations=(
                _slot_op("record_judgement", "replace", "不合格"),
            ),
        )
        result = resolve_selection(d, now=_DT, ref_context=_ctx(), service=svc)
        assert port.last_query is not None
        assert port.last_query.expression is not None
