"""Tests for SelectionService (Task 11)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from synapse.selection.filters import FieldEquals
from synapse.selection.models import (
    RecordQuery,
    SelectionScope,
)
from synapse.selection.query_port import (
    SelectionMaterialization,
    StaticSelectionQueryPort,
)
from synapse.selection.repository import InMemorySelectionRepository
from synapse.selection.service import (
    SelectionExpiredError,
    SelectionNotFoundError,
    SelectionService,
)


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
    def __init__(self, prefix: str = "sel") -> None:
        self._counter = 0
        self._prefix = prefix

    def new_id(self) -> str:
        self._counter += 1
        return f"{self._prefix}_{self._counter:03d}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc(
    dt: datetime = _DT,
    materialization: SelectionMaterialization | None = None,
) -> tuple[SelectionService, StaticSelectionQueryPort, InMemorySelectionRepository, _FakeClock, _CountingIdGenerator]:
    repo = InMemorySelectionRepository()
    clock = _FakeClock(dt)
    idgen = _CountingIdGenerator()
    mat = materialization or SelectionMaterialization(125, "sigma-v184", "sha256:mat-hash")
    port = StaticSelectionQueryPort(mat)
    svc = SelectionService(port, repo, clock, idgen)
    return svc, port, repo, clock, idgen


def _query() -> RecordQuery:
    return RecordQuery(expression=FieldEquals("status", "OK"))


def _scope() -> SelectionScope:
    return SelectionScope()


# ======================================================================
# create
# ======================================================================


class TestCreate:
    def test_basic(self) -> None:
        svc, port, repo, _, idgen = _svc()
        sel = svc.create(_query(), _scope())
        assert sel.id == "sel_001"
        assert sel.record_count == 125
        assert sel.snapshot_version == "sigma-v184"
        assert sel.content_hash == "sha256:mat-hash"
        assert sel.expires_at is None
        assert sel.derived_from is None
        assert sel.supersedes is None
        assert repo.get("sel_001") is sel

    def test_uses_materialization_hash_not_query_hash(self) -> None:
        mat = SelectionMaterialization(99, "v2", "sha256:custom-hash")
        svc, _, _, _, _ = _svc(materialization=mat)
        sel = svc.create(_query(), _scope())
        assert sel.content_hash == "sha256:custom-hash"

    def test_with_derived_from(self) -> None:
        svc, _, _, _, _ = _svc()
        sel = svc.create(_query(), _scope(), derived_from="sel_000")
        assert sel.derived_from == "sel_000"

    def test_with_expires_at(self) -> None:
        svc, _, _, _, _ = _svc()
        exp = _DT + timedelta(hours=1)
        sel = svc.create(_query(), _scope(), expires_at=exp)
        assert sel.expires_at == exp

    def test_query_port_called_with_correct_args(self) -> None:
        svc, port, _, _, _ = _svc()
        q = _query()
        s = _scope()
        svc.create(q, s)
        assert port.last_query is q
        assert port.last_scope is s

    def test_unique_ids_per_create(self) -> None:
        svc, _, _, _, _ = _svc()
        a = svc.create(_query(), _scope())
        b = svc.create(_query(), _scope())
        assert a.id != b.id


# ======================================================================
# get
# ======================================================================


class TestGet:
    def test_gets_existing(self) -> None:
        svc, _, repo, _, _ = _svc()
        created = svc.create(_query(), _scope())
        assert svc.get(created.id) is created

    def test_not_found(self) -> None:
        svc, _, _, _, _ = _svc()
        with pytest.raises(SelectionNotFoundError):
            svc.get("nonexistent")

    def test_expired_raises(self) -> None:
        exp = _DT + timedelta(hours=1)
        svc, _, _, clock, _ = _svc()
        created = svc.create(_query(), _scope(), expires_at=exp)
        # Advance clock past expiration
        clock._dt = exp + timedelta(seconds=1)
        with pytest.raises(SelectionExpiredError):
            svc.get(created.id)


# ======================================================================
# refresh
# ======================================================================


class TestRefresh:
    def test_creates_new_id(self) -> None:
        svc, _, _, _, _ = _svc()
        old = svc.create(_query(), _scope())
        new = svc.refresh(old.id)
        assert new.id != old.id

    def test_new_supersedes_old(self) -> None:
        svc, _, _, _, _ = _svc()
        old = svc.create(_query(), _scope())
        new = svc.refresh(old.id)
        assert new.supersedes == old.id

    def test_preserves_derived_from(self) -> None:
        svc, _, _, _, _ = _svc()
        old = svc.create(_query(), _scope(), derived_from="sel_000")
        new = svc.refresh(old.id)
        assert new.derived_from == "sel_000"

    def test_old_still_gettable(self) -> None:
        svc, _, _, _, _ = _svc()
        old = svc.create(_query(), _scope())
        svc.refresh(old.id)
        # Old ID still accessible directly via repository
        assert svc.get(old.id) is old

    def test_old_not_modified(self) -> None:
        svc, _, _, _, _ = _svc()
        old = svc.create(_query(), _scope())
        old_supersedes = old.supersedes
        svc.refresh(old.id)
        assert old.supersedes == old_supersedes
        assert old.id != ""  # unchanged

    def test_refresh_with_expires_at(self) -> None:
        svc, _, _, _, _ = _svc()
        old = svc.create(_query(), _scope())
        exp = _DT + timedelta(days=7)
        new = svc.refresh(old.id, expires_at=exp)
        assert new.expires_at == exp

    def test_refresh_without_expires_resets_to_none(self) -> None:
        svc, _, _, _, _ = _svc()
        old = svc.create(_query(), _scope(), expires_at=_DT + timedelta(hours=1))
        new = svc.refresh(old.id)
        assert new.expires_at is None

    def test_refresh_does_not_raise_on_expired(self) -> None:
        exp = _DT + timedelta(hours=1)
        svc, _, _, clock, _ = _svc()
        old = svc.create(_query(), _scope(), expires_at=exp)
        clock._dt = exp + timedelta(seconds=1)
        # refresh should succeed even though old is expired
        new = svc.refresh(old.id)
        assert new.supersedes == old.id

    def test_refresh_not_found(self) -> None:
        svc, _, _, _, _ = _svc()
        with pytest.raises(SelectionNotFoundError):
            svc.refresh("nonexistent")

    def test_refresh_uses_old_query_and_scope(self) -> None:
        svc, port, _, _, _ = _svc()
        q = RecordQuery(expression=FieldEquals("custom", 42))
        s = SelectionScope(dataset_id="999")
        old = svc.create(q, s)
        # Reset last_query so we can verify refresh passes the right args
        port.last_query = None
        port.last_scope = None
        svc.refresh(old.id)
        assert port.last_query is q
        assert port.last_scope is s


# ======================================================================
# is_expired
# ======================================================================


class TestIsExpired:
    def test_not_expired_when_no_expires_at(self) -> None:
        svc, _, _, _, _ = _svc()
        sel = svc.create(_query(), _scope())
        assert svc.is_expired(sel) is False

    def test_not_expired_when_future(self) -> None:
        svc, _, _, _, _ = _svc()
        sel = svc.create(_query(), _scope(), expires_at=_DT + timedelta(hours=1))
        assert svc.is_expired(sel) is False

    def test_expired_when_past(self) -> None:
        svc, _, _, _, _ = _svc()
        sel = svc.create(_query(), _scope(), expires_at=_DT + timedelta(hours=1))
        assert svc.is_expired(sel, now=_DT + timedelta(hours=2)) is True

    def test_uses_injected_clock(self) -> None:
        svc, _, _, clock, _ = _svc()
        sel = svc.create(_query(), _scope(), expires_at=_DT + timedelta(hours=1))
        clock._dt = _DT + timedelta(hours=2)
        assert svc.is_expired(sel) is True


# ======================================================================
# materialize failure
# ======================================================================


class TestMaterializeFailure:
    def test_repository_unchanged_on_failure(self) -> None:
        class FailingPort:
            def materialize(self, query: object, scope: object) -> None:
                raise RuntimeError("backend down")

        svc = SelectionService(
            FailingPort(),
            InMemorySelectionRepository(),
            _FakeClock(),
            _CountingIdGenerator(),
        )
        with pytest.raises(RuntimeError, match="backend down"):
            svc.create(_query(), _scope())
        # Repository should be empty — no half-saved selection
        with pytest.raises(SelectionNotFoundError):
            svc.get("sel_001")
