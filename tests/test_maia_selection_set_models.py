from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from maia.selection import (
    InMemorySelectionSetRepository,
    SelectionLineage,
    SelectionSetRepository,
    SelectionSet,
    SelectionSort,
)


def test_selection_lineage_requires_parent_for_derived_operations() -> None:
    assert SelectionLineage(operation="create").parent_selection_set_id is None

    for operation in ("refine", "expand", "exclude", "replace", "limit"):
        lineage = SelectionLineage(
            operation=operation,
            parent_selection_set_id="sel-parent",
        )
        assert lineage.operation == operation

    with pytest.raises(ValidationError, match="parent_selection_set_id"):
        SelectionLineage(operation="refine")

    with pytest.raises(ValidationError, match="parent_selection_set_id"):
        SelectionLineage(operation="create", parent_selection_set_id="sel-parent")


def test_selection_set_hash_is_stable_for_equal_content() -> None:
    created_at = datetime(2026, 6, 11, 9, 0, tzinfo=UTC)
    first = SelectionSet(
        selection_set_id="sel-1",
        expression={
            "kind": "predicate",
            "name": "summary_result_in",
            "params": {"values": ["FAIL"]},
        },
        sort=[{"field": "tested_at", "direction": "desc"}],
        limit=5,
        record_count=2,
        record_ids=("r-2", "r-1"),
        source_version="sigma-fixture-v1",
        created_at=created_at,
        expires_at=created_at + timedelta(days=1),
        lineage={"operation": "create"},
    )
    second = SelectionSet(
        selection_set_id="sel-2",
        expression=first.expression,
        sort=(SelectionSort(field="tested_at", direction="desc"),),
        limit=5,
        record_count=2,
        record_ids=("r-2", "r-1"),
        source_version="sigma-fixture-v1",
        created_at=created_at + timedelta(hours=1),
        expires_at=created_at + timedelta(days=2),
        lineage=SelectionLineage(operation="create"),
    )

    assert first.selection_hash == second.selection_hash
    assert first.derived_operation == "create"
    assert first.parent_selection_set_id is None


def test_selection_set_requires_one_record_source_and_matching_count() -> None:
    with pytest.raises(ValidationError, match="record source"):
        _selection_set(record_ids=None, snapshot_ref=None)

    with pytest.raises(ValidationError, match="record source"):
        _selection_set(record_ids=("r-1",), snapshot_ref="snapshot-1")

    with pytest.raises(ValidationError, match="record_count"):
        _selection_set(record_count=2, record_ids=("r-1",))


def test_selection_set_rejects_blank_ids_and_past_expiry() -> None:
    with pytest.raises(ValidationError, match="selection_set_id"):
        _selection_set(selection_set_id="  ")

    with pytest.raises(ValidationError, match="source_version"):
        _selection_set(source_version="  ")

    with pytest.raises(ValidationError, match="expires_at"):
        _selection_set(expires_at=datetime(2026, 6, 11, 8, 59, tzinfo=UTC))


def test_repository_round_trips_and_lists_recent_sets() -> None:
    repository: SelectionSetRepository = InMemorySelectionSetRepository()
    older = _selection_set(
        selection_set_id="sel-older",
        created_at=datetime(2026, 6, 11, 9, 0, tzinfo=UTC),
    )
    newer = _selection_set(
        selection_set_id="sel-newer",
        created_at=datetime(2026, 6, 11, 10, 0, tzinfo=UTC),
        lineage=SelectionLineage(operation="refine", parent_selection_set_id="sel-older"),
    )

    repository.save(older)
    repository.save(newer)

    assert repository.get("sel-older") == older
    assert repository.find_by_hash(newer.selection_hash) == newer
    assert repository.list_recent() == (newer, older)
    assert repository.list_recent(limit=1) == (newer,)


def test_repository_rejects_overwriting_existing_selection_set_id() -> None:
    repository = InMemorySelectionSetRepository()
    original = _selection_set(selection_set_id="sel-1", record_ids=("r-1",))
    replacement = _selection_set(selection_set_id="sel-1", record_ids=("r-2",))

    repository.save(original)

    with pytest.raises(ValueError, match="already exists"):
        repository.save(replacement)


def _selection_set(
    *,
    selection_set_id: str = "sel-1",
    expression: object | None = None,
    sort: object = (),
    limit: int | None = None,
    record_count: int = 1,
    record_ids: tuple[str, ...] | None = ("r-1",),
    snapshot_ref: str | None = None,
    source_version: str = "sigma-fixture-v1",
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    lineage: object | None = None,
) -> SelectionSet:
    return SelectionSet(
        selection_set_id=selection_set_id,
        expression=expression
        or {"kind": "predicate", "name": "product_type_in", "params": {"values": ["A"]}},
        sort=sort,
        limit=limit,
        record_count=record_count,
        record_ids=record_ids,
        snapshot_ref=snapshot_ref,
        source_version=source_version,
        created_at=created_at or datetime(2026, 6, 11, 9, 0, tzinfo=UTC),
        expires_at=expires_at,
        lineage=lineage or SelectionLineage(operation="create"),
    )
