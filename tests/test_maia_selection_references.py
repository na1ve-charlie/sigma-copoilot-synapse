from __future__ import annotations

from datetime import UTC, datetime

import pytest

from maia.recognition.report import RecognitionReport
from maia.selection import InMemorySelectionSetRepository, SelectionLineage, SelectionSet


def test_reference_resolver_resolves_active_selection_from_report() -> None:
    from maia.conversation.references import SelectionReferenceResolver
    from maia.conversation.state import ConversationSelectionState

    repository = InMemorySelectionSetRepository()
    active = _selection_set("sel-active", ("r-1",))
    repository.save(active)
    repository.save(_selection_set("sel-older", ("r-2",)))

    resolved = SelectionReferenceResolver(repository).resolve_report(
        _reference_report("active_selection"),
        ConversationSelectionState(
            active_selection_set_id="sel-active",
            recent_selection_set_ids=("sel-active", "sel-older"),
        ),
    )

    assert resolved == active


def test_reference_resolver_supports_recent_history_and_hash_lookup() -> None:
    from maia.conversation.references import SelectionReferenceResolver
    from maia.conversation.state import ConversationSelectionState

    repository = InMemorySelectionSetRepository()
    newer = repository.save(_selection_set("sel-newer", ("r-2",)))
    older = repository.save(_selection_set("sel-older", ("r-1",)))
    resolver = SelectionReferenceResolver(repository)
    state = ConversationSelectionState(
        active_selection_set_id="sel-newer",
        recent_selection_set_ids=("sel-newer", "sel-older"),
    )

    assert resolver.resolve("recent_selection:1", state) == older
    assert resolver.resolve(newer.selection_hash, state) == newer


def test_reference_resolver_requires_active_selection_when_report_asks_for_it() -> None:
    from maia.conversation.references import SelectionReferenceResolutionError, SelectionReferenceResolver
    from maia.conversation.state import ConversationSelectionState

    resolver = SelectionReferenceResolver(InMemorySelectionSetRepository())

    with pytest.raises(SelectionReferenceResolutionError, match="active selection"):
        resolver.resolve_report(
            _reference_report("active_selection"),
            ConversationSelectionState(),
        )


def _reference_report(target: str) -> RecognitionReport:
    return RecognitionReport(
        message="删除上面这些数据",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        slot_operations=[
            {
                "intent": "task.nvh.selection.use_active_selection",
                "score": 0.99,
                "action": "replace",
                "entity_type": "selection_reference",
                "target": target,
                "slot_valid": True,
            }
        ],
    )


def _selection_set(selection_set_id: str, record_ids: tuple[str, ...]) -> SelectionSet:
    return SelectionSet(
        selection_set_id=selection_set_id,
        expression={"kind": "predicate", "name": "product_type_in", "params": {"values": ["A"]}},
        record_count=len(record_ids),
        record_ids=record_ids,
        source_version="sigma-fixture-v1",
        created_at=datetime(2026, 6, 11, 9, 0, tzinfo=UTC),
        lineage=SelectionLineage(operation="create"),
    )
