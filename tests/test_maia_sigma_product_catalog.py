from __future__ import annotations

import asyncio
import json

from maia.integrations.sigma.product_catalog import SigmaProductCatalogClient


def test_product_catalog_preserves_zero_version_values() -> None:
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
                    "code": 200,
                    "data": {
                        "rows": [
                            {
                                "type": "HZXJ0515",
                                "version": 0,
                                "systemNo": "SYS-01",
                                "updateTime": "2026-06-12 08:30:00",
                            }
                        ]
                    },
                },
                ensure_ascii=False,
            ),
        )

    configs = asyncio.run(
        SigmaProductCatalogClient(
            base_url="http://sigma.local",
            transport=transport,
        ).list_configs()
    )

    assert captured["url"] == "http://sigma.local/api/storage/type"
    assert captured["params"] == {"page": 1, "rows": 99999, "lang": "zh"}
    assert configs[0].product_type == "HZXJ0515"
    assert configs[0].config_version == "0"
    assert configs[0].type_system == "SYS-01"


def test_product_catalog_lists_versions_from_dedicated_endpoint() -> None:
    captured: list[tuple[str, dict[str, object]]] = []

    def transport(
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        del headers, timeout
        captured.append((url, params))
        return (
            200,
            json.dumps(
                {
                    "code": 200,
                    "data": [5, 4, 3, 2, 1, 0],
                },
                ensure_ascii=False,
            ),
        )

    versions = asyncio.run(
        SigmaProductCatalogClient(
            base_url="http://sigma.local",
            transport=transport,
        ).list_versions("HZXJ0515")
    )

    assert captured == [
        (
            "http://sigma.local/api/storage/type/listVersions",
            {"typeList": "HZXJ0515", "lang": "zh"},
        )
    ]
    assert versions == ("5", "4", "3", "2", "1", "0")


def test_product_catalog_lists_systems_from_dedicated_endpoint() -> None:
    captured: list[tuple[str, dict[str, object]]] = []

    def transport(
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        del headers, timeout
        captured.append((url, params))
        return (
            200,
            json.dumps(
                {
                    "code": 200,
                    "data": ["SYS-05", "SYS-04"],
                },
                ensure_ascii=False,
            ),
        )

    systems = asyncio.run(
        SigmaProductCatalogClient(
            base_url="http://sigma.local",
            transport=transport,
        ).list_systems("测试")
    )

    assert captured == [
        (
            "http://sigma.local/api/storage/type/listSystemNos",
            {"typeList": "测试", "lang": "zh"},
        )
    ]
    assert systems == ("SYS-05", "SYS-04")
