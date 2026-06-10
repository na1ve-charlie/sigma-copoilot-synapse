"""Application-layer Selection Resolution (Task 13).

Orchestrates Interpreter → Projector → SelectionService → result,
with reference resolution for active / previous / derived selections.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from synapse.domains.data_management.selection_interpreter import (
    DerivedSelectionCriteria,
    ExistingSelectionReference,
    NewSelectionCriteria,
    OperationIntent,
    SelectionClarificationRequired,
    interpret_decision,
)
from synapse.domains.data_management.selection_projector import (
    project_criteria,
)
from synapse.selection.filters import AllOf
from synapse.selection.models import RecordQuery
from synapse.selection.references import SelectionReferenceContext
from synapse.selection.service import (
    SelectionExpiredError,
    SelectionNotFoundError,
    SelectionService,
)


# ===================================================================
# Result types
# ===================================================================


@dataclass(frozen=True, slots=True)
class SelectionOnlyResult:
    """A selection was created / resolved with no downstream operation."""

    selection: SelectionSet


@dataclass(frozen=True, slots=True)
class SelectionOperationResult:
    """A selection was resolved and bound to a downstream operation."""

    selection: SelectionSet
    operation_type: str
    requires_confirmation: bool
    selection_hash: str
    snapshot_version: str


@dataclass(frozen=True, slots=True)
class SelectionClarifyResult:
    """The user needs to clarify or provide more information."""

    reason: str
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectionUnsupportedResult:
    """The request contains an unsupported combination of operations."""

    reason: str


SelectionResolutionResult = (
    SelectionOnlyResult
    | SelectionOperationResult
    | SelectionClarifyResult
    | SelectionUnsupportedResult
)


# ===================================================================
# Risk mapping
# ===================================================================

_RISK_MAP: dict[str, bool] = {
    "trend_analysis": False,
    "data_delete": True,
}


def _count_supported(ops: tuple[OperationIntent, ...]) -> int:
    return sum(1 for o in ops if o.operation_type != "unsupported")


def _pick_operation(ops: tuple[OperationIntent, ...]) -> OperationIntent | None:
    supported = [o for o in ops if o.operation_type != "unsupported"]
    if len(supported) == 1:
        return supported[0]
    return None


# ===================================================================
# resolve
# ===================================================================


def resolve_selection(
    decision: object,
    *,
    now: datetime,
    ref_context: SelectionReferenceContext,
    service: SelectionService,
) -> SelectionResolutionResult:
    """Full pipeline: interpret → project → create / get → bind operation.

    Parameters
    ----------
    decision:
        The Themis ``IntentDecision`` (or duck-type).
    now:
        Timezone-aware reference instant.
    ref_context:
        Conversation-level active / recent selection IDs.
    service:
        The injected ``SelectionService``.
    """
    resolution = interpret_decision(
        decision,
        now=now,
        active_selection_id=ref_context.active_selection_id,
    )

    if isinstance(resolution, SelectionClarificationRequired):
        return SelectionClarifyResult(
            reason=resolution.reason,
            missing_fields=resolution.missing_fields,
        )

    if isinstance(resolution, ExistingSelectionReference):
        try:
            sel = service.get(resolution.selection_id)
        except SelectionNotFoundError as e:
            return SelectionClarifyResult(reason="selection_not_found")
        except SelectionExpiredError as e:
            return SelectionClarifyResult(reason="selection_expired")
        return _bind_operation(sel, resolution.operation_intents)

    if isinstance(resolution, NewSelectionCriteria):
        query = project_criteria(resolution.criteria)  # type: ignore[arg-type]
        sel = service.create(query, _scope_from_context(ref_context))
        return _bind_operation(sel, resolution.operation_intents)

    if isinstance(resolution, DerivedSelectionCriteria):
        try:
            old = service.get(resolution.base_selection_id)
        except SelectionNotFoundError:
            return SelectionClarifyResult(reason="base_selection_not_found")
        except SelectionExpiredError:
            return SelectionClarifyResult(reason="base_selection_expired")

        new_query = project_criteria(resolution.criteria)  # type: ignore[arg-type]
        merged = _merge_queries(old.query, new_query)
        sel = service.create(
            merged,
            old.scope,
            derived_from=old.id,
        )
        return _bind_operation(sel, resolution.operation_intents)

    return SelectionUnsupportedResult(reason="unknown_resolution_type")


# ===================================================================
# Helpers
# ===================================================================


def _scope_from_context(ctx: SelectionReferenceContext) -> object:
    """Minimal scope — can be enriched later."""
    from synapse.selection.models import SelectionScope
    return SelectionScope()


def _merge_queries(old: RecordQuery, new: RecordQuery) -> RecordQuery:
    """Merge *new* query on top of *old* query for derived selections."""
    return RecordQuery(
        expression=AllOf((old.expression, new.expression)),
        aggregate=new.aggregate if new.aggregate is not None else old.aggregate,
        sort=new.sort if new.sort else old.sort,
        limit=new.limit if new.limit is not None else old.limit,
    )


def _bind_operation(
    sel: SelectionSet,
    ops: tuple[OperationIntent, ...],
) -> SelectionResolutionResult:
    """Attach an operation (if any) to the resolved selection."""
    supported_count = _count_supported(ops)
    if supported_count > 1:
        return SelectionUnsupportedResult(reason="multiple_operations")

    op = _pick_operation(ops)
    if op is not None:
        requires = _RISK_MAP.get(op.operation_type, True)
        return SelectionOperationResult(
            selection=sel,
            operation_type=op.operation_type,
            requires_confirmation=requires,
            selection_hash=sel.content_hash,
            snapshot_version=sel.snapshot_version,
        )

    return SelectionOnlyResult(selection=sel)
