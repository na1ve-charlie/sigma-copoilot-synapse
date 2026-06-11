from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from maia.api import WorkspaceContext
from maia.conversation.draft import SelectionDraft, SelectionSort
from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary
from maia.selection import InMemorySelectionSetRepository
from maia.selection.compiler import SelectionQueryCompiler


def _product(value: str) -> dict[str, object]:
    return {"kind": "predicate", "name": "product_type_in", "params": {"values": [value]}}


def _summary(value: str) -> dict[str, object]:
    return {"kind": "predicate", "name": "summary_result_in", "params": {"values": [value]}}


def test_selection_set_service_reuses_existing_hash_for_identical_query() -> None:
    from maia.selection.service import SelectionSetService

    repository = InMemorySelectionSetRepository()
    service = SelectionSetService(
        repository,
        SelectionQueryCompiler(_record_client()),
        source_version="sigma-fixture-v1",
        id_factory=_id_factory(),
        clock=lambda: datetime(2026, 6, 11, 9, 0, tzinfo=UTC),
    )
    draft = SelectionDraft(expression=_product("A"))

    first = asyncio.run(service.create_or_derive(draft, workspace_context=_workspace_context()))
    second = asyncio.run(service.create_or_derive(draft, workspace_context=_workspace_context()))

    assert second == first
    assert repository.list_recent() == (first,)


@pytest.mark.parametrize(
    ("draft", "expected_ids", "expected_operation"),
    [
        (
            SelectionDraft(
                expression={
                    "kind": "all_of",
                    "expressions": [_product("A"), _summary("FAIL")],
                }
            ),
            ("r-2",),
            "refine",
        ),
        (
            SelectionDraft(
                expression={
                    "kind": "any_of",
                    "expressions": [_product("A"), _product("B")],
                }
            ),
            ("r-1", "r-2", "r-3", "r-4", "r-5"),
            "expand",
        ),
        (
            SelectionDraft(
                expression={
                    "kind": "all_of",
                    "expressions": [
                        _product("A"),
                        {"kind": "not", "expression": _summary("FAIL")},
                    ],
                }
            ),
            ("r-1", "r-3"),
            "exclude",
        ),
        (
            SelectionDraft(expression=_product("B")),
            ("r-4", "r-5"),
            "replace",
        ),
        (
            SelectionDraft(
                expression=_product("A"),
                sort=[SelectionSort(field="tested_at", direction="desc")],
                limit=1,
            ),
            ("r-3",),
            "limit",
        ),
    ],
)
def test_selection_set_service_classifies_derived_lineage(
    draft: SelectionDraft,
    expected_ids: tuple[str, ...],
    expected_operation: str,
) -> None:
    from maia.selection.service import SelectionSetService

    repository = InMemorySelectionSetRepository()
    service = SelectionSetService(
        repository,
        SelectionQueryCompiler(_record_client()),
        source_version="sigma-fixture-v1",
        id_factory=_id_factory(),
        clock=lambda: datetime(2026, 6, 11, 9, 0, tzinfo=UTC),
    )
    base = asyncio.run(
        service.create_or_derive(
            SelectionDraft(expression=_product("A")),
            workspace_context=_workspace_context(),
        )
    )

    derived = asyncio.run(
        service.create_or_derive(
            draft.model_copy(update={"base_selection_id": base.selection_set_id}),
            workspace_context=_workspace_context(),
        )
    )

    assert derived.record_ids == expected_ids
    assert derived.derived_operation == expected_operation
    assert derived.parent_selection_set_id == base.selection_set_id


class _RecordClient:
    def __init__(self, pages: dict[str, list[TestRecordPage]]) -> None:
        self._pages = pages

    async def list_records(
        self,
        expression,
        *,
        workspace_context: WorkspaceContext | None,
        page: int | None = None,
        rows: int | None = None,
    ) -> TestRecordPage:
        del workspace_context, page, rows
        key = "<all>" if expression is None else _key(expression)
        if key not in self._pages:
            raise ValueError(f"unsupported query branch: {key}")
        return self._pages[key][0]


def _record_client() -> _RecordClient:
    return _RecordClient(
        {
            _key(_product("A")): [_page(["r-1", "r-2", "r-3"])],
            _key(_product("B")): [_page(["r-4", "r-5"])],
            _key(_summary("FAIL")): [_page(["r-2", "r-4"])],
        }
    )


def _page(record_ids: list[str]) -> TestRecordPage:
    return TestRecordPage(total=len(record_ids), records=tuple(_record(record_id) for record_id in record_ids))


def _record(record_id: str) -> TestRecordSummary:
    day = int(record_id.split("-")[1])
    return TestRecordSummary(
        record_id=record_id,
        tested_at=datetime(2026, 6, day, 9, 30, tzinfo=UTC),
        product_type="A" if day < 4 else "B",
        config_version="1",
        system_no="SYS",
        serial_number=f"SN-{record_id}",
        summary_result="FAIL" if record_id in {"r-2", "r-4"} else "PASS",
        available_artifacts=("raw_data",),
    )


def _key(expression: object) -> str:
    payload = expression.model_dump(mode="json") if hasattr(expression, "model_dump") else expression
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _workspace_context() -> WorkspaceContext:
    return WorkspaceContext(workspace_session_id="ws-1")


def _id_factory():
    ids = iter(("sel-1", "sel-2", "sel-3", "sel-4", "sel-5", "sel-6"))
    return lambda: next(ids)
