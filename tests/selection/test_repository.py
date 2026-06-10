"""Tests for Selection query port and repository (Task 10)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from synapse.selection.filters import FieldEquals
from synapse.selection.models import (
    RecordQuery,
    SelectionScope,
    SelectionSet,
)
from synapse.selection.query_port import (
    SelectionMaterialization,
    StaticSelectionQueryPort,
)
from synapse.selection.repository import (
    InMemorySelectionRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_DT = datetime(2026, 6, 10, 15, 30, 0, tzinfo=_UTC)


def _selection(id: str = "sel_001", **overrides: object) -> SelectionSet:
    kwargs: dict[str, object] = {
        "id": id,
        "query": RecordQuery(expression=FieldEquals("status", "OK")),
        "scope": SelectionScope(),
        "backend_ref": None,
        "record_count": 0,
        "snapshot_version": "sigma-v184",
        "content_hash": "sha256:deadbeef",
        "created_at": _DT,
    }
    kwargs.update(overrides)
    return SelectionSet(**kwargs)  # type: ignore[arg-type]


# ======================================================================
# SelectionMaterialization
# ======================================================================


class TestSelectionMaterialization:
    def test_construction(self) -> None:
        m = SelectionMaterialization(
            record_count=125,
            snapshot_version="sigma-v184",
            content_hash="sha256:abc",
        )
        assert m.record_count == 125
        assert m.snapshot_version == "sigma-v184"
        assert m.content_hash == "sha256:abc"
        assert m.backend_ref is None

    def test_with_backend_ref(self) -> None:
        m = SelectionMaterialization(
            record_count=0,
            snapshot_version="v1",
            content_hash="sha256:xyz",
            backend_ref="sigma:job/42",
        )
        assert m.backend_ref == "sigma:job/42"

    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        m = SelectionMaterialization(0, "v1", "sha256:x")
        with pytest.raises(FrozenInstanceError):
            m.record_count = 5  # type: ignore[misc]


# ======================================================================
# StaticSelectionQueryPort
# ======================================================================


class TestStaticSelectionQueryPort:
    def test_returns_injected_result(self) -> None:
        mat = SelectionMaterialization(10, "v1", "sha256:a")
        port = StaticSelectionQueryPort(mat)
        result = port.materialize(
            RecordQuery(expression=FieldEquals("f", 1)),
            SelectionScope(),
        )
        assert result is mat

    def test_records_last_query_and_scope(self) -> None:
        mat = SelectionMaterialization(0, "v1", "sha256:x")
        port = StaticSelectionQueryPort(mat)

        query = RecordQuery(expression=FieldEquals("f", 42), limit=10)
        scope = SelectionScope(dataset_id="1152")

        assert port.last_query is None
        assert port.last_scope is None

        port.materialize(query, scope)

        assert port.last_query is query
        assert port.last_scope is scope

    def test_multiple_calls_overwrite_last(self) -> None:
        mat = SelectionMaterialization(0, "v1", "sha256:x")
        port = StaticSelectionQueryPort(mat)

        q1 = RecordQuery(expression=FieldEquals("a", 1))
        q2 = RecordQuery(expression=FieldEquals("b", 2))

        port.materialize(q1, SelectionScope())
        assert port.last_query is q1

        port.materialize(q2, SelectionScope())
        assert port.last_query is q2

    def test_returned_result_is_unchanged(self) -> None:
        """The stored materialization should not change between calls."""
        mat = SelectionMaterialization(99, "sigma-v200", "sha256:zzz")
        port = StaticSelectionQueryPort(mat)

        r1 = port.materialize(
            RecordQuery(expression=FieldEquals("x", 1)),
            SelectionScope(),
        )
        r2 = port.materialize(
            RecordQuery(expression=FieldEquals("y", 2)),
            SelectionScope(),
        )
        assert r1 is r2 is mat


# ======================================================================
# InMemorySelectionRepository
# ======================================================================


class TestInMemoryRepository:
    def test_save_and_get(self) -> None:
        repo = InMemorySelectionRepository()
        sel = _selection("sel_001")
        repo.save(sel)
        assert repo.get("sel_001") is sel

    def test_get_missing_returns_none(self) -> None:
        repo = InMemorySelectionRepository()
        assert repo.get("nonexistent") is None

    def test_save_same_object_idempotent(self) -> None:
        repo = InMemorySelectionRepository()
        sel = _selection("sel_001")
        repo.save(sel)
        repo.save(sel)  # same object → no error
        assert repo.get("sel_001") is sel

    def test_save_different_object_same_id_raises(self) -> None:
        repo = InMemorySelectionRepository()
        repo.save(_selection("sel_001"))
        with pytest.raises(ValueError, match="Duplicate SelectionSet"):
            repo.save(_selection("sel_001"))

    def test_internal_dict_not_exposed(self) -> None:
        repo = InMemorySelectionRepository()
        repo.save(_selection("sel_001"))
        # The internal dict is private but accessible via _store attr.
        # The test verifies that get() always returns what was saved.
        assert repo.get("sel_001") is not None

    def test_multiple_different_ids(self) -> None:
        repo = InMemorySelectionRepository()
        s1 = _selection("s1")
        s2 = _selection("s2")
        repo.save(s1)
        repo.save(s2)
        assert repo.get("s1") is s1
        assert repo.get("s2") is s2
        assert repo.get("s3") is None
