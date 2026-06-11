from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from maia.api import WorkspaceContext
from maia.integrations.sigma.record_client import TestRecordClient, TestRecordClientError


WORKSPACE_CONTEXT_PATH = Path("configs/maia/sigma/offline_1152.workspace_context.json")


def test_test_record_client_calls_legacy_endpoint_with_mapped_query_params() -> None:
    captured: dict[str, object] = {}

    def transport(
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return (
            200,
            json.dumps(
                {
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "total": 1,
                        "list": [
                            {
                                "recordId": "rec-001",
                                "testedAt": "2026-05-03T10:11:12Z",
                                "productType": "dm0518",
                                "summaryResult": "FAIL",
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        )

    async def exercise() -> object:
        client = TestRecordClient(
            base_url="http://sigma.local/",
            token="secret-token",
            timeout=7.5,
            transport=transport,
        )
        return await client.list_records(
            {
                "kind": "all_of",
                "expressions": [
                    {
                        "kind": "predicate",
                        "name": "product_type_in",
                        "params": {"values": ["dm0518"]},
                    },
                    {
                        "kind": "predicate",
                        "name": "summary_result_in",
                        "params": {"values": ["FAIL"]},
                    },
                ],
            },
            workspace_context=_workspace_context(),
            page=3,
            rows=50,
        )

    page = asyncio.run(exercise())

    assert page.total == 1
    assert page.record_ids == ("rec-001",)
    assert captured["url"] == "http://sigma.local/api/storage/singleStationReport/listReportByMulti"
    assert captured["headers"] == {"Token": "secret-token"}
    assert captured["timeout"] == 7.5
    assert captured["params"] == {
        "dataGroupId": "1152",
        "lang": "zh",
        "page": 3,
        "rows": 50,
        "productTypeList": ["dm0518"],
        "summaryResultList": ["FAIL"],
    }


def test_test_record_client_wraps_transport_errors_with_request_context() -> None:
    def transport(
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        raise TimeoutError("timed out")

    async def exercise() -> None:
        client = TestRecordClient(base_url="http://sigma.local", transport=transport)
        await client.list_records(
            {
                "kind": "predicate",
                "name": "summary_result_in",
                "params": {"values": ["FAIL"]},
            },
            workspace_context=_workspace_context(),
        )

    with pytest.raises(TestRecordClientError, match="request failed") as raised:
        asyncio.run(exercise())

    assert raised.value.operation == "list_test_records"
    assert raised.value.path == "/api/storage/singleStationReport/listReportByMulti"
    assert raised.value.request_params["dataGroupId"] == "1152"
    assert isinstance(raised.value.__cause__, TimeoutError)


def test_test_record_client_wraps_invalid_json_payloads() -> None:
    def transport(
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        return 200, "not-json"

    async def exercise() -> None:
        client = TestRecordClient(base_url="http://sigma.local", transport=transport)
        await client.list_records(
            {
                "kind": "predicate",
                "name": "summary_result_in",
                "params": {"values": ["FAIL"]},
            },
            workspace_context=_workspace_context(),
        )

    with pytest.raises(TestRecordClientError, match="invalid JSON") as raised:
        asyncio.run(exercise())

    assert raised.value.request_params["summaryResultList"] == ["FAIL"]
    assert isinstance(raised.value.__cause__, json.JSONDecodeError)


def test_test_record_client_retries_once_when_backend_returns_refresh_token() -> None:
    calls: list[dict[str, object]] = []

    def transport(
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if len(calls) == 1:
            return 200, json.dumps({"code": 1001, "msg": "Refresh Token", "data": "token-v2"})
        return (
            200,
            json.dumps(
                {
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "total": 1,
                        "list": [
                            {
                                "recordId": "rec-001",
                                "testedAt": "2026-05-03T10:11:12Z",
                                "productType": "dm0518",
                                "summaryResult": "FAIL",
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        )

    async def exercise() -> object:
        client = TestRecordClient(
            base_url="http://sigma.local",
            token="token-v1",
            transport=transport,
        )
        return await client.list_records(
            {
                "kind": "predicate",
                "name": "summary_result_in",
                "params": {"values": ["FAIL"]},
            },
            workspace_context=_workspace_context(),
        )

    page = asyncio.run(exercise())

    assert page.record_ids == ("rec-001",)
    assert len(calls) == 2
    assert calls[0]["headers"] == {"Token": "token-v1"}
    assert calls[1]["headers"] == {"Token": "token-v2"}


def test_test_record_client_surfaces_token_validation_errors_from_backend() -> None:
    def transport(
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        return (
            200,
            json.dumps(
                {
                    "code": 1000,
                    "msg": None,
                    "data": {
                        "code": 1000,
                        "content": "账号在其他设备登录",
                        "name": "token验证失败,",
                        "operation": "请重新登录获取Token",
                    },
                },
                ensure_ascii=False,
            ),
        )

    async def exercise() -> None:
        client = TestRecordClient(base_url="http://sigma.local", transport=transport)
        await client.list_records(
            {
                "kind": "predicate",
                "name": "summary_result_in",
                "params": {"values": ["FAIL"]},
            },
            workspace_context=_workspace_context(),
        )

    with pytest.raises(TestRecordClientError, match="账号在其他设备登录") as raised:
        asyncio.run(exercise())

    assert raised.value.status_code == 200


def test_test_record_client_surfaces_http_error_payload_message() -> None:
    def transport(
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        return (
            503,
            json.dumps(
                {
                    "timestamp": "2026-06-11T08:07:50.681+0000",
                    "path": "/api/storage/singleStationReport/listReportByMulti",
                    "status": 503,
                    "error": "Service Unavailable",
                    "message": "Unable to find instance for shengteng-storage",
                    "requestId": "1b6fe39e",
                },
                ensure_ascii=False,
            ),
        )

    async def exercise() -> None:
        client = TestRecordClient(base_url="http://sigma.local", transport=transport)
        await client.list_records(
            {
                "kind": "predicate",
                "name": "summary_result_in",
                "params": {"values": ["FAIL"]},
            },
            workspace_context=_workspace_context(),
        )

    with pytest.raises(
        TestRecordClientError,
        match="Unable to find instance for shengteng-storage",
    ) as raised:
        asyncio.run(exercise())

    assert raised.value.status_code == 503


def test_test_record_client_wraps_legacy_payload_mapping_failures() -> None:
    def transport(
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        return (
            200,
            json.dumps(
                {
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "total": 1,
                        "list": [{"productType": "dm0518", "summaryResult": "FAIL"}],
                    },
                },
                ensure_ascii=False,
            ),
        )

    async def exercise() -> None:
        client = TestRecordClient(base_url="http://sigma.local", transport=transport)
        await client.list_records(
            {
                "kind": "predicate",
                "name": "summary_result_in",
                "params": {"values": ["FAIL"]},
            },
            workspace_context=_workspace_context(),
        )

    with pytest.raises(TestRecordClientError, match="response mapping failed") as raised:
        asyncio.run(exercise())

    assert raised.value.request_params["summaryResultList"] == ["FAIL"]
    assert isinstance(raised.value.__cause__, ValueError)


def _workspace_context() -> WorkspaceContext:
    return WorkspaceContext.model_validate(
        json.loads(WORKSPACE_CONTEXT_PATH.read_text(encoding="utf-8"))
    )
