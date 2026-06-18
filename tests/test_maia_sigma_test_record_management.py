from __future__ import annotations

import asyncio
import json

import pytest

from maia.api import WorkspaceContext


def test_test_record_management_client_posts_backend_payload() -> None:
    from maia.integrations.sigma.test_record_management import (
        TestRecordManagementClient,
        TestRecordManagementRequest,
    )

    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del timeout
        calls.append((url, headers, json.loads((body or b"{}").decode("utf-8"))))
        return 200, json.dumps({"code": 200, "data": True})

    client = TestRecordManagementClient(
        base_url="http://sigma",
        token="token-1",
        transport=transport,
    )

    payload = asyncio.run(
        client.submit(
            TestRecordManagementRequest(
                result_id_list=(46704, 46703),
                color_map=True,
                origin_data=True,
                result_data=False,
                data_export_type=2,
                file_name="backup-001",
            ),
            workspace_context=WorkspaceContext(lang="zh"),
        )
    )

    assert payload == {"code": 200, "data": True}
    assert calls == [
        (
            "http://sigma/api/storage/dataFile/exportData?lang=zh",
            {"Content-Type": "application/json", "Token": "token-1"},
            {
                "resultIdList": [46704, 46703],
                "colorMap": True,
                "originData": True,
                "resultData": False,
                "dataExportType": 2,
                "filePath": "D:/数据备份/",
                "fileName": "backup-001",
            },
        )
    ]


def test_test_record_management_delete_payload_omits_file_name() -> None:
    from maia.integrations.sigma.test_record_management import TestRecordManagementRequest

    request = TestRecordManagementRequest(
        result_id_list=(46704,),
        color_map=False,
        origin_data=True,
        result_data=False,
        data_export_type=1,
    )

    assert request.to_body() == {
        "resultIdList": [46704],
        "colorMap": False,
        "originData": True,
        "resultData": False,
        "dataExportType": 1,
        "filePath": "D:/数据备份/",
    }


def test_test_record_management_client_maps_backend_error() -> None:
    from maia.integrations.sigma.test_record_management import (
        TestRecordManagementClient,
        TestRecordManagementError,
        TestRecordManagementRequest,
    )

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del url, headers, body, timeout
        return 200, json.dumps({"code": 500, "msg": "failed"})

    client = TestRecordManagementClient(base_url="http://sigma", transport=transport)

    with pytest.raises(TestRecordManagementError, match="failed"):
        asyncio.run(
            client.submit(
                TestRecordManagementRequest(
                    result_id_list=(46704,),
                    color_map=True,
                    origin_data=False,
                    result_data=False,
                    data_export_type=3,
                    file_name="backup-001",
                ),
                workspace_context=None,
            )
        )


def test_test_record_management_client_maps_invalid_json() -> None:
    from maia.integrations.sigma.test_record_management import (
        TestRecordManagementClient,
        TestRecordManagementError,
        TestRecordManagementRequest,
    )

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del url, headers, body, timeout
        return 200, "not-json"

    client = TestRecordManagementClient(base_url="http://sigma", transport=transport)

    with pytest.raises(TestRecordManagementError, match="invalid JSON"):
        asyncio.run(
            client.submit(
                TestRecordManagementRequest(
                    result_id_list=(46704,),
                    color_map=True,
                    origin_data=False,
                    result_data=False,
                    data_export_type=2,
                    file_name="backup-001",
                ),
                workspace_context=None,
            )
        )


@pytest.mark.parametrize(
    "payload, error",
    [
        ({"result_id_list": (), "data_export_type": 2, "file_name": "ok"}, "resultIdList"),
        ({"result_id_list": (1,), "data_export_type": 9, "file_name": "ok"}, "dataExportType"),
        ({"result_id_list": (1,), "data_export_type": 2, "file_name": ""}, "fileName"),
        ({"result_id_list": (1,), "data_export_type": 2, "file_name": "bad:name"}, "fileName"),
        ({"result_id_list": (1,), "data_export_type": 2, "file_name": "CON"}, "fileName"),
        ({"result_id_list": (1,), "data_export_type": 2, "file_name": "backup "}, "fileName"),
        ({"result_id_list": (1,), "data_export_type": 1, "file_name": "unused"}, "fileName"),
    ],
)
def test_test_record_management_request_validation(payload: dict[str, object], error: str) -> None:
    from maia.integrations.sigma.test_record_management import TestRecordManagementRequest

    with pytest.raises(ValueError, match=error):
        TestRecordManagementRequest(
            color_map=True,
            origin_data=False,
            result_data=False,
            **payload,
        )
