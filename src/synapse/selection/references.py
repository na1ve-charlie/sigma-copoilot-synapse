"""Selection reference resolution (Task 12).

Resolves symbolic selection references (``active``, ``previous``,
``id:<selection_id>``) from conversation-level selection context.
Does **not** manage full session state — only resolves IDs.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectionReferenceContext:
    """Conversation-level selection bookmarks.

    Parameters
    ----------
    active_selection_id:
        The ID of the currently-active ``SelectionSet``, or ``None``
        when no selection has been made yet.
    recent_selection_ids:
        The most-recently-used selection IDs (most recent first),
        excluding the active ID.
    """

    active_selection_id: str | None = None
    recent_selection_ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Resolution result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedSelection:
    """A successfully resolved concrete selection ID."""

    selection_id: str


@dataclass(frozen=True, slots=True)
class SelectionRefClarificationRequired:
    """The reference could not be resolved to a concrete selection ID."""

    reason: str


SelectionRefResolution = ResolvedSelection | SelectionRefClarificationRequired


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------


def resolve_selection_ref(
    reference: str,
    context: SelectionReferenceContext,
) -> SelectionRefResolution:
    """Resolve a symbolic selection reference string into a concrete ID.

    Supported references:

    * ``"active"`` — the currently-active selection
    * ``"previous"`` — the first recent selection that differs from active
    * ``"id:<selection_id>"`` — a literal selection ID (not verified)
    """
    if reference == "active":
        if context.active_selection_id is None:
            return SelectionRefClarificationRequired(
                reason="no_active_selection"
            )
        return ResolvedSelection(selection_id=context.active_selection_id)

    if reference == "previous":
        for recent_id in context.recent_selection_ids:
            if recent_id != context.active_selection_id:
                return ResolvedSelection(selection_id=recent_id)
        return SelectionRefClarificationRequired(
            reason="no_previous_selection"
        )

    if reference.startswith("id:"):
        literal_id = reference[len("id:"):]
        if not literal_id:
            return SelectionRefClarificationRequired(
                reason="empty_selection_id"
            )
        return ResolvedSelection(selection_id=literal_id)

    return SelectionRefClarificationRequired(
        reason=f"unknown_reference:{reference!r}"
    )
