from __future__ import annotations

import asyncio
import json

import pytest

from maia.api import WorkspaceContext


def test_origin_export_client_posts_legacy_payload() -> None:
    from maia.integrations.sigma.origin_export import (
        ORIGIN_DATA_INFO_LOOKUP_PATH,
        OriginExportClient,
        OriginExportRequest,
    )

    calls: list[tuple[str, dict[str, str], object]] = []

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del timeout
        decoded = json.loads((body or b"null").decode("utf-8"))
        calls.append((url, headers, decoded))
        if ORIGIN_DATA_INFO_LOOKUP_PATH in url:
            return 200, json.dumps([{"id": 30191}])
        return 200, json.dumps({"code": 200, "data": True})

    client = OriginExportClient(
        base_url="http://sigma",
        token="token-1",
        transport=transport,
    )

    payload = asyncio.run(
        client.export(
            OriginExportRequest(
                id_list=(29181,),
                path="D:\\exportOriginFile",
                data_export_type=1,
                system_no="7s-SNF1001",
            ),
            workspace_context=WorkspaceContext(lang="zh"),
        )
    )

    assert payload == {"code": 200, "data": True}
    assert calls == [
        (
            "http://sigma/api/storage/dataGroup/listOriginDataInfoByResultIdList?lang=zh",
            {"Content-Type": "application/json", "Token": "token-1"},
            [29181],
        ),
        (
            "http://sigma/api/storage/originData/OriginExport?lang=zh",
            {"Content-Type": "application/json", "Token": "token-1"},
            {
                "idList": [30191],
                "path": "D:\\exportOriginFile",
                "dataExportType": 1,
                "systemNo": "7s-SNF1001",
            },
        )
    ]


def test_origin_export_client_maps_lookup_backend_error() -> None:
    from maia.integrations.sigma.origin_export import (
        ORIGIN_DATA_INFO_LOOKUP_PATH,
        OriginExportClient,
        OriginExportError,
        OriginExportRequest,
    )

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del headers, body, timeout
        if ORIGIN_DATA_INFO_LOOKUP_PATH in url:
            return 200, json.dumps({"code": 500, "msg": "lookup failed"})
        return 200, json.dumps({"code": 200, "data": True})

    client = OriginExportClient(base_url="http://sigma", transport=transport)

    with pytest.raises(OriginExportError, match="lookup failed"):
        asyncio.run(
            client.export(
                OriginExportRequest(
                    id_list=(29181,),
                    data_export_type=1,
                    system_no="SYS",
                ),
                workspace_context=None,
            )
        )


def test_origin_export_client_maps_backend_error() -> None:
    from maia.integrations.sigma.origin_export import (
        ORIGIN_DATA_INFO_LOOKUP_PATH,
        OriginExportClient,
        OriginExportError,
        OriginExportRequest,
    )

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del headers, body, timeout
        if ORIGIN_DATA_INFO_LOOKUP_PATH in url:
            return 200, json.dumps([{"id": 30191}])
        return 200, json.dumps({"code": 500, "msg": "failed"})

    client = OriginExportClient(base_url="http://sigma", transport=transport)

    with pytest.raises(OriginExportError, match="failed"):
        asyncio.run(
            client.export(
                OriginExportRequest(
                    id_list=(29181,),
                    data_export_type=1,
                    system_no="SYS",
                ),
                workspace_context=None,
            )
        )


def test_origin_export_client_uses_lookup_endpoint_before_export() -> None:
    from maia.integrations.sigma.origin_export import (
        ORIGIN_DATA_INFO_LOOKUP_PATH,
        OriginExportClient,
        OriginExportRequest,
        ORIGIN_EXPORT_PATH,
    )

    seen_urls: list[str] = []

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del headers, body, timeout
        seen_urls.append(url)
        if ORIGIN_DATA_INFO_LOOKUP_PATH in url:
            return 200, json.dumps([{"id": 30191}, {"id": 30192}])
        if ORIGIN_EXPORT_PATH in url:
            return 200, json.dumps({"code": 200, "data": True})
        raise AssertionError(f"unexpected url: {url}")

    client = OriginExportClient(base_url="http://sigma", transport=transport)

    payload = asyncio.run(
        client.export(
            OriginExportRequest(
                id_list=(29181, 29182),
                data_export_type=1,
                system_no="SYS",
            ),
            workspace_context=WorkspaceContext(lang="zh"),
        )
    )

    assert payload == {"code": 200, "data": True}
    assert seen_urls == [
        "http://sigma/api/storage/dataGroup/listOriginDataInfoByResultIdList?lang=zh",
        "http://sigma/api/storage/originData/OriginExport?lang=zh",
    ]


def test_origin_export_client_accepts_lookup_data_array_wrapper() -> None:
    from maia.integrations.sigma.origin_export import (
        ORIGIN_DATA_INFO_LOOKUP_PATH,
        OriginExportClient,
        OriginExportRequest,
    )

    export_bodies: list[object] = []

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del headers, timeout
        decoded = json.loads((body or b"null").decode("utf-8"))
        if ORIGIN_DATA_INFO_LOOKUP_PATH in url:
            return 200, json.dumps({"code": 200, "data": [{"id": 30191}]})
        export_bodies.append(decoded)
        return 200, json.dumps({"code": 200, "data": True})

    client = OriginExportClient(base_url="http://sigma", transport=transport)

    asyncio.run(
        client.export(
            OriginExportRequest(
                id_list=(29181,),
                data_export_type=1,
                system_no="SYS",
            ),
            workspace_context=WorkspaceContext(lang="zh"),
        )
    )

    assert export_bodies == [
        {
            "idList": [30191],
            "path": "D:\\exportOriginFile",
            "dataExportType": 1,
            "systemNo": "SYS",
        }
    ]


def test_origin_export_client_accepts_lookup_data_rows_wrapper() -> None:
    from maia.integrations.sigma.origin_export import (
        ORIGIN_DATA_INFO_LOOKUP_PATH,
        OriginExportClient,
        OriginExportRequest,
    )

    export_bodies: list[object] = []

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del headers, timeout
        decoded = json.loads((body or b"null").decode("utf-8"))
        if ORIGIN_DATA_INFO_LOOKUP_PATH in url:
            return 200, json.dumps({"code": 200, "data": {"rows": [{"id": 30191}]}})
        export_bodies.append(decoded)
        return 200, json.dumps({"code": 200, "data": True})

    client = OriginExportClient(base_url="http://sigma", transport=transport)

    asyncio.run(
        client.export(
            OriginExportRequest(
                id_list=(29181,),
                data_export_type=1,
                system_no="SYS",
            ),
            workspace_context=WorkspaceContext(lang="zh"),
        )
    )

    assert export_bodies == [
        {
            "idList": [30191],
            "path": "D:\\exportOriginFile",
            "dataExportType": 1,
            "systemNo": "SYS",
        }
    ]


def test_origin_export_client_accepts_lookup_nested_content_wrapper() -> None:
    from maia.integrations.sigma.origin_export import (
        ORIGIN_DATA_INFO_LOOKUP_PATH,
        OriginExportClient,
        OriginExportRequest,
    )

    export_bodies: list[object] = []

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del headers, timeout
        decoded = json.loads((body or b"null").decode("utf-8"))
        if ORIGIN_DATA_INFO_LOOKUP_PATH in url:
            return 200, json.dumps({"code": 200, "data": {"content": [{"id": 30191}]}})
        export_bodies.append(decoded)
        return 200, json.dumps({"code": 200, "data": True})

    client = OriginExportClient(base_url="http://sigma", transport=transport)

    asyncio.run(
        client.export(
            OriginExportRequest(
                id_list=(29181,),
                data_export_type=1,
                system_no="SYS",
            ),
            workspace_context=WorkspaceContext(lang="zh"),
        )
    )

    assert export_bodies == [
        {
            "idList": [30191],
            "path": "D:\\exportOriginFile",
            "dataExportType": 1,
            "systemNo": "SYS",
        }
    ]


def test_origin_export_client_maps_lookup_nested_backend_error() -> None:
    from maia.integrations.sigma.origin_export import (
        ORIGIN_DATA_INFO_LOOKUP_PATH,
        OriginExportClient,
        OriginExportError,
        OriginExportRequest,
    )

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del headers, body, timeout
        if ORIGIN_DATA_INFO_LOOKUP_PATH in url:
            return 200, json.dumps(
                {
                    "code": 1000,
                    "data": {
                        "content": "用户授权失败",
                        "name": "token验证失败",
                        "operation": "请重新登录获取Token",
                    },
                }
            )
        return 200, json.dumps({"code": 200, "data": True})

    client = OriginExportClient(base_url="http://sigma", transport=transport)

    with pytest.raises(OriginExportError, match="用户授权失败"):
        asyncio.run(
            client.export(
                OriginExportRequest(
                    id_list=(29181,),
                    data_export_type=1,
                    system_no="SYS",
                ),
                workspace_context=WorkspaceContext(lang="zh"),
            )
        )
