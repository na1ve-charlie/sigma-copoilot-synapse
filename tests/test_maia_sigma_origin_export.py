from __future__ import annotations

import asyncio
import json

import pytest

from maia.api import WorkspaceContext


def test_origin_export_client_posts_legacy_payload() -> None:
    from maia.integrations.sigma.origin_export import OriginExportClient, OriginExportRequest

    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del timeout
        calls.append((url, headers, json.loads((body or b"{}").decode("utf-8"))))
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
            "http://sigma/api/storage/originData/OriginExport?lang=zh",
            {"Content-Type": "application/json", "Token": "token-1"},
            {
                "idList": [29181],
                "path": "D:\\exportOriginFile",
                "dataExportType": 1,
                "systemNo": "7s-SNF1001",
            },
        )
    ]


def test_origin_export_client_maps_backend_error() -> None:
    from maia.integrations.sigma.origin_export import (
        OriginExportClient,
        OriginExportError,
        OriginExportRequest,
    )

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del url, headers, body, timeout
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
