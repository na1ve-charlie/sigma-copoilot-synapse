from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maia.api import WorkspaceContext
from maia.integrations.sigma.records import TestRecordPage, TestRecordSummary
from maia.selection.compiler import ALL_RECORDS_PREDICATE_NAME, SelectionQueryCompileError, SelectionQueryCompiler
from maia.selection.query import SelectionQuery


WORKSPACE_CONTEXT_PATH = Path("configs/maia/testdata/sigma/offline_1152.workspace_context.json")


def test_query_compiler_unions_paginated_branches_and_applies_sort_limit() -> None:
    client = FakeRecordClient(
        {
            _key({"kind": "predicate", "name": "product_type_in", "params": {"values": ["A"]}}): [
                _page(["r-1", "r-2"], total=2),
            ],
            _key({"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}}): [
                _page(["r-2", "r-3"], total=3),
                _page(["r-4"], total=3),
            ],
        }
    )

    result = asyncio.run(
        SelectionQueryCompiler(client, page_size=2).compile(
            SelectionQuery(
                expression={
                    "kind": "any_of",
                    "expressions": [
                        {"kind": "predicate", "name": "product_type_in", "params": {"values": ["A"]}},
                        {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}},
                    ],
                },
                sort=[{"field": "tested_at", "direction": "desc"}],
                limit=2,
            ),
            workspace_context=_workspace_context(),
        )
    )

    assert result.record_ids == ("r-4", "r-3")
    assert result.record_count == 2
    assert tuple(client.calls) == (
        ("predicate:product_type_in", 1, 2),
        ("predicate:summary_result_in", 1, 2),
        ("predicate:summary_result_in", 2, 2),
    )


def test_query_compiler_uses_limit_as_rows_for_latest_pushdown_query() -> None:
    expression = {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}}
    client = FakeRecordClient(
        {
            _key(expression): [
                _page(["r-5", "r-4"], total=20),
            ],
        }
    )

    result = asyncio.run(
        SelectionQueryCompiler(client, page_size=500).compile(
            SelectionQuery(
                expression=expression,
                sort=[{"field": "tested_at", "direction": "desc"}],
                limit=2,
            ),
            workspace_context=_workspace_context(),
        )
    )

    assert result.record_ids == ("r-5", "r-4")
    assert tuple(client.calls) == (("predicate:summary_result_in", 1, 2),)


def test_query_compiler_paginates_until_latest_limit_is_satisfied() -> None:
    expression = {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}}
    client = FakeRecordClient(
        {
            _key(expression): [
                _page(["r-5", "r-4"], total=5),
                _page(["r-3", "r-2"], total=5),
            ],
        }
    )

    result = asyncio.run(
        SelectionQueryCompiler(client, page_size=2).compile(
            SelectionQuery(
                expression=expression,
                sort=[{"field": "tested_at", "direction": "desc"}],
                limit=3,
            ),
            workspace_context=_workspace_context(),
        )
    )

    assert result.record_ids == ("r-5", "r-4", "r-3")
    assert tuple(client.calls) == (
        ("predicate:summary_result_in", 1, 2),
        ("predicate:summary_result_in", 2, 2),
    )


def test_query_compiler_intersects_positive_branch_and_excludes_not_branch() -> None:
    client = FakeRecordClient(
        {
            _key({"kind": "predicate", "name": "product_type_in", "params": {"values": ["A"]}}): [
                _page(["r-1", "r-2", "r-3"], total=3),
            ],
            _key({"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}}): [
                _page(["r-2", "r-4"], total=2),
            ],
        }
    )

    result = asyncio.run(
        SelectionQueryCompiler(client, page_size=5).compile(
            {
                "expression": {
                    "kind": "all_of",
                    "expressions": [
                        {"kind": "predicate", "name": "product_type_in", "params": {"values": ["A"]}},
                        {
                            "kind": "not",
                            "expression": {
                                "kind": "predicate",
                                "name": "summary_result_in",
                                "params": {"values": ["FAIL"]},
                            },
                        },
                    ],
                }
            },
            workspace_context=_workspace_context(),
        )
    )

    assert result.record_ids == ("r-1", "r-3")
    assert result.record_count == 2


def test_query_compiler_merges_conjunctive_pushdown_predicates_into_one_request() -> None:
    expression = {
        "kind": "all_of",
        "expressions": [
            {"kind": "predicate", "name": "product_type_in", "params": {"values": ["A"]}},
            {"kind": "predicate", "name": "config_version_in", "params": {"values": ["1"]}},
            {
                "kind": "predicate",
                "name": "tested_at_between",
                "params": {"start": "2026-06-01 00:00:00", "end": "2026-06-12 00:00:00"},
            },
        ],
    }
    client = FakeRecordClient(
        {
            _key(expression): [
                _page(["r-1", "r-2"], total=2),
            ],
        }
    )

    result = asyncio.run(
        SelectionQueryCompiler(client, page_size=5).compile(
            {"expression": expression},
            workspace_context=_workspace_context(),
        )
    )

    assert result.record_ids == ("r-1", "r-2")
    assert result.record_count == 2
    assert tuple(client.calls) == (("all_of", 1, 5),)


def test_query_compiler_supports_root_not_against_dataset_scope() -> None:
    client = FakeRecordClient(
        {
            "<all>": [_page(["r-1", "r-2", "r-3"], total=3)],
            _key({"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}}): [
                _page(["r-2"], total=1),
            ],
        }
    )

    result = asyncio.run(
        SelectionQueryCompiler(client, page_size=10).compile(
            {
                "expression": {
                    "kind": "not",
                    "expression": {
                        "kind": "predicate",
                        "name": "summary_result_in",
                        "params": {"values": ["FAIL"]},
                    },
                }
            },
            workspace_context=_workspace_context(),
        )
    )

    assert result.record_ids == ("r-1", "r-3")
    assert result.record_count == 2
    assert client.calls[0] == ("<all>", 1, 10)


def test_query_compiler_treats_all_records_predicate_as_dataset_scope() -> None:
    client = FakeRecordClient(
        {
            "<all>": [_page(["r-1", "r-2", "r-3"], total=3)],
        }
    )

    result = asyncio.run(
        SelectionQueryCompiler(client, page_size=10).compile(
            {
                "expression": {
                    "kind": "predicate",
                    "name": ALL_RECORDS_PREDICATE_NAME,
                    "params": {},
                }
            },
            workspace_context=_workspace_context(),
        )
    )

    assert result.record_ids == ("r-1", "r-2", "r-3")
    assert result.record_count == 3
    assert client.calls[0] == ("<all>", 1, 10)


def test_query_compiler_rejects_predicates_without_query_or_record_level_support() -> None:
    client = FakeRecordClient({})

    with pytest.raises(
        SelectionQueryCompileError,
        match="indicator_failed",
    ):
        asyncio.run(
            SelectionQueryCompiler(client).compile(
                {
                    "expression": {
                        "kind": "predicate",
                        "name": "indicator_failed",
                        "params": {
                            "sensor": "Vib1",
                            "segment": "TS-01",
                            "indicator": "RMS",
                        },
                    }
                },
                workspace_context=_workspace_context(),
            )
        )


class FakeRecordClient:
    def __init__(self, pages: Mapping[str, list[TestRecordPage]]) -> None:
        self._pages = dict(pages)
        self.calls: list[tuple[str, int, int]] = []

    async def list_records(
        self,
        expression,
        *,
        workspace_context: WorkspaceContext | None,
        page: int | None = None,
        rows: int | None = None,
    ) -> TestRecordPage:
        key = "<all>" if expression is None else _key(expression)
        page_number = page or 1
        row_count = rows or 5000
        self.calls.append((_call_label(key), page_number, row_count))
        if key not in self._pages:
            raise ValueError(f"unsupported query branch: {key}")
        branches = self._pages[key]
        if page_number > len(branches):
            return TestRecordPage(total=0, records=())
        return branches[page_number - 1]


def _page(record_ids: list[str], *, total: int) -> TestRecordPage:
    return TestRecordPage(
        total=total,
        records=tuple(_record(record_id) for record_id in record_ids),
    )


def _record(record_id: str) -> TestRecordSummary:
    day = int(record_id.split("-")[1])
    return TestRecordSummary(
        record_id=record_id,
        tested_at=datetime(2026, 6, day, 9, 30, tzinfo=UTC),
        product_type="A",
        config_version="1",
        system_no="SYS",
        serial_number=f"SN-{record_id}",
        summary_result="FAIL" if record_id in {"r-2", "r-4"} else "PASS",
        available_artifacts=("raw_data",),
    )


def _key(expression) -> str:
    if isinstance(expression, SelectionQuery):
        payload = expression.expression.model_dump(mode="json")
    elif hasattr(expression, "model_dump"):
        payload = expression.model_dump(mode="json")
    else:
        payload = expression
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _call_label(key: str) -> str:
    if key == "<all>":
        return key
    payload = json.loads(key)
    if payload["kind"] == "predicate":
        return f"{payload['kind']}:{payload['name']}"
    return payload["kind"]


def _workspace_context() -> WorkspaceContext:
    return WorkspaceContext.model_validate(
        json.loads(WORKSPACE_CONTEXT_PATH.read_text(encoding="utf-8"))
    )
