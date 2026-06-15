from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from maia.integrations.sigma.dataset_materializer import SigmaSelectionSetMaterializer
from maia.integrations.sigma.records import TestRecordSummary
from maia.selection.sets import SelectionLineage, SelectionSet


def test_dataset_materializer_uses_short_hash_name_and_result_list_payload() -> None:
    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def transport(
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, str]:
        del timeout
        calls.append((url, headers, json.loads((body or b"{}").decode("utf-8"))))
        if url.endswith("/saveDataGroup"):
            return 200, json.dumps({"code": 200, "data": {"id": 1172}})
        return 200, json.dumps({"code": 200, "data": True})

    selection = _selection_set()
    records = (
        _record("46700", "中文测试", datetime(2026, 6, 11, 19, 38, 34, tzinfo=UTC)),
        _record("46699", "中文测试", datetime(2026, 6, 11, 11, 57, 44, tzinfo=UTC)),
    )

    dataset_id = asyncio.run(
        SigmaSelectionSetMaterializer(
            base_url="http://sigma.local",
            transport=transport,
        ).materialize(
            selection,
            records=records,
            workspace_context=None,
        )
    )

    assert dataset_id == "1172"
    assert calls[0][0] == "http://sigma.local/api/storage/dataGroup/saveDataGroup"
    assert calls[0][1] == {"Content-Type": "application/json"}
    assert calls[0][2] == {
        "lang": "zh",
        "name": f"maia-{selection.selection_hash[:12]}",
    }
    assert calls[1][0] == "http://sigma.local/api/storage/dataGroup/saveSelectedResult?lang=zh"
    assert calls[1][2] == {
        "id": 1172,
        "info": None,
        "name": f"maia-{selection.selection_hash[:12]}",
        "resultList": [
            {
                "colorId": None,
                "resultId": 46700,
                "serialNo": "中文测试",
                "testTime": "2026-06-11 19:38:34",
                "version": "2",
                "systemNo": "7s-SNF1001",
                "type": "测试",
            },
            {
                "colorId": None,
                "resultId": 46699,
                "serialNo": "中文测试",
                "testTime": "2026-06-11 11:57:44",
                "version": "2",
                "systemNo": "7s-SNF1001",
                "type": "测试",
            },
        ],
        "copyStatus": False,
    }


def test_dataset_materializer_skips_save_selected_result_when_selection_is_empty() -> None:
    calls: list[dict[str, object]] = []

    def transport(
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, str]:
        del url, headers, body, timeout
        calls.append({})
        return 200, json.dumps({"code": 200, "data": {"id": 1172}})

    dataset_id = asyncio.run(
        SigmaSelectionSetMaterializer(
            base_url="http://sigma.local",
            transport=transport,
        ).materialize(
            _selection_set(record_ids=()),
            records=(),
            workspace_context=None,
        )
    )

    assert dataset_id is None
    assert calls == []


def test_dataset_materializer_replaces_existing_dataset_when_selection_is_empty() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def transport(
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, str]:
        del headers, timeout
        calls.append((url, json.loads((body or b"{}").decode("utf-8"))))
        return 200, json.dumps({"code": 200, "data": True})

    dataset_id = asyncio.run(
        SigmaSelectionSetMaterializer(
            base_url="http://sigma.local",
            transport=transport,
        ).materialize(
            _selection_set(record_ids=()),
            records=(),
            workspace_context=None,
            dataset_id="1172",
            dataset_name="maia-session",
        )
    )

    assert dataset_id == "1172"
    assert calls == [
        (
            "http://sigma.local/api/storage/dataGroup/saveSelectedResult?lang=zh",
            {
                "id": 1172,
                "info": None,
                "name": "maia-session",
                "resultList": [],
                "copyStatus": False,
            },
        )
    ]


def _selection_set(*, record_ids: tuple[str, ...] = ("46700", "46699")) -> SelectionSet:
    return SelectionSet(
        selection_set_id="sel-1",
        expression={"kind": "predicate", "name": "product_type_in", "params": {"values": ["测试"]}},
        record_count=len(record_ids),
        record_ids=record_ids,
        source_version="sigma-fixture-v1",
        created_at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
        lineage=SelectionLineage(operation="create"),
    )


def _record(record_id: str, serial_number: str, tested_at: datetime) -> TestRecordSummary:
    return TestRecordSummary(
        record_id=record_id,
        tested_at=tested_at,
        product_type="测试",
        config_version="2",
        system_no="7s-SNF1001",
        serial_number=serial_number,
        summary_result="合格",
    )
