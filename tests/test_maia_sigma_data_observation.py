from __future__ import annotations

import asyncio
import json

import pytest

from maia.api import WorkspaceContext
from maia.integrations.sigma.data_observation import (
    DataObservationCatalogError,
    ObservationTypeSystem,
    SigmaDataObservationCatalogClient,
)


def test_data_observation_client_expands_availability_map() -> None:
    calls: list[tuple[str, dict[str, object], dict[str, str]]] = []

    def availability_transport(url, params, headers, timeout):
        del timeout
        calls.append((url, params, headers))
        return 200, json.dumps(
            {
                "code": 200,
                "data": {
                    "ONE_D": {"Vib1": ["1500rpm"]},
                    "TWO_D_CEP": {"sensor02": ["Spd-rCH", "Spd-rDL"]},
                },
            },
            ensure_ascii=False,
        )

    client = SigmaDataObservationCatalogClient(
        base_url="http://sigma",
        token="token-1",
        availability_transport=availability_transport,
    )

    rows = asyncio.run(client.list_availability("1226"))

    assert [(row.data_type, row.sensor, row.test_name) for row in rows] == [
        ("ONE_D", "Vib1", "1500rpm"),
        ("TWO_D_CEP", "sensor02", "Spd-rCH"),
        ("TWO_D_CEP", "sensor02", "Spd-rDL"),
    ]
    assert calls == [
        (
            "http://sigma/api/storage/resultData/getResultExistMap",
            {"dataGroupId": "1226"},
            {"Token": "token-1"},
        )
    ]


@pytest.mark.parametrize(
    ("data_type", "expected_path", "expected_data_type"),
    [
        ("ONE_D", "/api/storage/config/listOneIndicatorsByResult?lang=zh", None),
        ("TWO_D_FS", "/api/storage/config/listLineIndicatorsByResult?lang=zh", "TWO_D_FS"),
        ("TWO_D_OC", "/api/storage/config/listMultiLineIndicatorsByResult?lang=zh", "TWO_D_OC"),
    ],
)
def test_data_observation_client_posts_indicator_query(
    data_type: str,
    expected_path: str,
    expected_data_type: str | None,
) -> None:
    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def indicator_transport(url, headers, body, timeout):
        del timeout
        calls.append((url, headers, json.loads((body or b"{}").decode("utf-8"))))
        return 200, json.dumps(
            {"code": 200, "data": [{"name": "倒谱", "index": "cep-index"}]},
            ensure_ascii=False,
        )

    client = SigmaDataObservationCatalogClient(
        base_url="http://sigma",
        token="token-1",
        indicator_transport=indicator_transport,
    )

    indicators = asyncio.run(
        client.list_indicators(
            data_type=data_type,
            sensor_list=("Vib1",),
            test_name_list=("Spd-rDL",),
            type_systems=(ObservationTypeSystem("byd0601_7", "7s-SNF1001"),),
            workspace_context=WorkspaceContext(lang="zh"),
        )
    )

    assert [item.to_param() for item in indicators] == [{"name": "倒谱", "index": "cep-index"}]
    assert calls == [
        (
            f"http://sigma{expected_path}",
            {"Content-Type": "application/json", "Token": "token-1"},
            {
                "sensorList": ["Vib1"],
                "testNameList": ["Spd-rDL"],
                "typeSystemVOList": [{"type": "byd0601_7", "systemNo": "7s-SNF1001"}],
                **({} if expected_data_type is None else {"dataType": expected_data_type}),
            },
        )
    ]


def test_data_observation_client_maps_backend_error() -> None:
    def availability_transport(url, params, headers, timeout):
        del url, params, headers, timeout
        return 200, json.dumps({"code": 500, "msg": "failed"})

    client = SigmaDataObservationCatalogClient(
        base_url="http://sigma",
        availability_transport=availability_transport,
    )

    with pytest.raises(DataObservationCatalogError, match="failed"):
        asyncio.run(client.list_availability("1226"))
