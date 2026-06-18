from __future__ import annotations

import asyncio
import json

import pytest

from maia.api import WorkspaceContext


def test_sensor_list_client_loads_candidates() -> None:
    from maia.integrations.sigma.excel_export import SensorListClient

    calls: list[tuple[str, dict[str, object], dict[str, str]]] = []

    def transport(url: str, params: dict[str, object], headers: dict[str, str], timeout: float):
        del timeout
        calls.append((url, params, headers))
        return 200, json.dumps({"code": 200, "data": ["Torque", "sensor01", "sensor02"]})

    client = SensorListClient(base_url="http://sigma", token="token-1", transport=transport)

    sensors = asyncio.run(
        client.list_sensors(
            type_="dm0608_5",
            system_no="7s-SNF1001",
            workspace_context=WorkspaceContext(lang="zh"),
        )
    )

    assert sensors == ("Torque", "sensor01", "sensor02")
    assert calls == [
        (
            "http://sigma/api/storage/config/sensor-list",
            {"type": "dm0608_5", "systemNo": "7s-SNF1001", "lang": "zh"},
            {"Token": "token-1"},
        )
    ]


def test_excel_export_client_posts_backend_payload() -> None:
    from maia.integrations.sigma.excel_export import ExcelExportClient, ExcelExportRequest

    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del timeout
        calls.append((url, headers, json.loads((body or b"{}").decode("utf-8"))))
        return 200, json.dumps({"code": 200, "data": True})

    client = ExcelExportClient(base_url="http://sigma", token="token-1", transport=transport)

    payload = asyncio.run(
        client.export(
            ExcelExportRequest(
                type_="dm0608_5",
                system_no="7s-SNF1001",
                id_list=(46704, 46703),
                sensor_id_list=("sensor02", "sensor01", "Torque"),
                one_data=1,
                two_data=1,
                result_data=1,
            ),
            workspace_context=WorkspaceContext(lang="zh"),
        )
    )

    assert payload == {"code": 200, "data": True}
    assert calls == [
        (
            "http://sigma/api/storage/singleStationReport/export?lang=zh",
            {"Content-Type": "application/json", "Token": "token-1"},
            {
                "type": "dm0608_5",
                "systemNo": "7s-SNF1001",
                "idList": [46704, 46703],
                "sensorIdList": ["sensor02", "sensor01", "Torque"],
                "oneData": 1,
                "twoData": 1,
                "resultData": 1,
            },
        )
    ]


def test_excel_export_client_maps_backend_error() -> None:
    from maia.integrations.sigma.excel_export import (
        ExcelExportClient,
        ExcelExportError,
        ExcelExportRequest,
    )

    def transport(url: str, headers: dict[str, str], body: bytes | None, timeout: float):
        del url, headers, body, timeout
        return 200, json.dumps({"code": 500, "msg": "failed"})

    client = ExcelExportClient(base_url="http://sigma", transport=transport)

    with pytest.raises(ExcelExportError, match="failed"):
        asyncio.run(
            client.export(
                ExcelExportRequest(
                    type_="dm0608_5",
                    system_no="SYS",
                    id_list=(46704,),
                    sensor_id_list=("Torque",),
                    one_data=1,
                    two_data=0,
                    result_data=1,
                ),
                workspace_context=None,
            )
        )


def test_sensor_list_client_maps_backend_error() -> None:
    from maia.integrations.sigma.excel_export import SensorListClient, SensorListError

    def transport(url: str, params: dict[str, object], headers: dict[str, str], timeout: float):
        del url, params, headers, timeout
        return 200, json.dumps({"code": 500, "msg": "failed"})

    client = SensorListClient(base_url="http://sigma", transport=transport)

    with pytest.raises(SensorListError, match="failed"):
        asyncio.run(
            client.list_sensors(
                type_="dm0608_5",
                system_no="SYS",
                workspace_context=None,
            )
        )
