from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass

import pytest

from synapse.integrations.sigma import (
    HttpSigmaGateway,
    SigmaCandidate,
    SigmaCandidateCatalogLoader,
    SigmaGateway,
    SigmaGatewayError,
    SigmaQuery,
)
from synapse.integrations.sigma.http import (
    _dedupe_candidates,
    _indicator_candidates,
    _with_data_type_metadata,
)
from synapse.turns import TurnRequest


def run(coro):
    return asyncio.run(coro)


@dataclass(frozen=True, slots=True)
class GatewayCall:
    operation: str
    query: SigmaQuery


class FakeSigmaGateway:
    def __init__(
        self,
        *,
        sensors: tuple[SigmaCandidate, ...] = (),
        test_segments: tuple[SigmaCandidate, ...] = (),
        indicator_names: tuple[SigmaCandidate, ...] = (),
        fail_operation: str | None = None,
    ) -> None:
        self._sensors = sensors
        self._test_segments = test_segments
        self._indicator_names = indicator_names
        self._fail_operation = fail_operation
        self.calls: list[GatewayCall] = []

    async def list_sensors(self, query: SigmaQuery) -> tuple[SigmaCandidate, ...]:
        self._record("list_sensors", query)
        return self._sensors

    async def list_test_segments(
        self,
        query: SigmaQuery,
    ) -> tuple[SigmaCandidate, ...]:
        self._record("list_test_segments", query)
        return self._test_segments

    async def list_indicator_names(
        self,
        query: SigmaQuery,
    ) -> tuple[SigmaCandidate, ...]:
        self._record("list_indicator_names", query)
        return self._indicator_names

    def _record(self, operation: str, query: SigmaQuery) -> None:
        self.calls.append(GatewayCall(operation, query))
        if operation == self._fail_operation:
            raise SigmaGatewayError(
                "SigMA request failed",
                operation=operation,
                query=query,
            )


def test_fake_sigma_gateway_satisfies_protocol_and_lists_candidates() -> None:
    gateway = FakeSigmaGateway(
        sensors=(SigmaCandidate("VibX", label="Seat X"),),
        test_segments=(SigmaCandidate("run-1"),),
        indicator_names=(SigmaCandidate("RMS"),),
    )
    query = SigmaQuery.from_turn(
        TurnRequest(
            session_id="s1",
            message="show data",
            workspace_context={"dataset_id": "1152"},
        )
    )

    assert isinstance(gateway, SigmaGateway)
    assert run(gateway.list_sensors(query))[0].label == "Seat X"
    assert run(gateway.list_test_segments(query))[0].value == "run-1"
    assert run(gateway.list_indicator_names(query))[0].value == "RMS"
    assert [call.operation for call in gateway.calls] == [
        "list_sensors",
        "list_test_segments",
        "list_indicator_names",
    ]
    assert gateway.calls[0].query.workspace_context is not None
    assert gateway.calls[0].query.workspace_context.dataset_id == "1152"


def test_sigma_gateway_error_preserves_operation_and_query() -> None:
    gateway = FakeSigmaGateway(fail_operation="list_sensors")
    query = SigmaQuery(session_id="s1")

    with pytest.raises(SigmaGatewayError) as raised:
        run(gateway.list_sensors(query))

    assert raised.value.operation == "list_sensors"
    assert raised.value.query is query


def test_sigma_candidate_catalog_loader_builds_recognition_catalog() -> None:
    gateway = FakeSigmaGateway(
        sensors=(SigmaCandidate("VibX", label="Seat X"),),
        test_segments=(SigmaCandidate("run-1"),),
        indicator_names=(SigmaCandidate("RMS"),),
    )
    request = TurnRequest(
        session_id="s1",
        message="show data",
        workspace_context={"dataset_id": "1152"},
    )

    catalog = run(SigmaCandidateCatalogLoader(gateway).load(request))

    assert gateway.calls[0].query.workspace_context is not None
    assert gateway.calls[0].query.workspace_context.dataset_id == "1152"
    assert run(catalog.as_themis_resolver().resolve("sensor")) == [
        {"value": "VibX", "label": "Seat X"}
    ]
    assert run(catalog.as_themis_resolver().resolve("sensors")) == [
        {"value": "VibX", "label": "Seat X"}
    ]
    assert run(catalog.as_themis_resolver().resolve("test_segment")) == [
        {"value": "run-1"}
    ]
    assert run(catalog.as_themis_resolver().resolve("indicator")) == [
        {"value": "RMS"}
    ]
    data_types = catalog.candidates_for_entity("data_type")
    assert {item.value: item.label for item in data_types} == {
        "ONE_D": "一维数据",
        "TWO_D_TD": "时间域",
        "TWO_D_FS": "频谱",
        "TWO_D_OS": "阶次谱",
        "TWO_D_OC": "阶次切片",
        "TWO_D_CEP": "倒谱",
        "TWO_D_PS": "心理声学",
    }
    assert "frequency spectrum" in data_types[2].metadata["aliases"]


def test_sigma_candidate_catalog_loader_preserves_indicator_domain_metadata() -> None:
    gateway = FakeSigmaGateway(
        indicator_names=(
            SigmaCandidate("48阶", metadata={"data_types": ("ONE_D", "TWO_D_OC")}),
            SigmaCandidate("频谱", metadata={"data_types": ("TWO_D_FS",)}),
        ),
    )

    catalog = run(
        SigmaCandidateCatalogLoader(gateway).load(
            TurnRequest(
                session_id="s1",
                message="show data",
                workspace_context={"dataset_id": "1152"},
            )
        )
    )
    by_value = {
        item.value: item for item in catalog.candidates_for_entity("indicator_names")
    }

    assert by_value["48阶"].metadata == {"data_types": ("ONE_D", "TWO_D_OC")}
    assert by_value["频谱"].metadata == {"data_types": ("TWO_D_FS",)}


@pytest.mark.parametrize(
    ("response", "item", "data_type", "name", "index"),
    [
        (
            {"items_path": "data", "value_key": "name", "label_field": "index"},
            {"name": "RMS", "index": "RMS-one-d"},
            "ONE_D",
            "RMS",
            "RMS-one-d",
        ),
        (
            {
                "items_path": "data",
                "value_key": "indicatorName",
                "label_field": "indicatorIndex",
            },
            {"indicatorName": "Spectrum", "indicatorIndex": "FS-spectrum"},
            "TWO_D_FS",
            "Spectrum",
            "FS-spectrum",
        ),
    ],
)
def test_indicator_candidates_preserve_backend_index_by_data_type(
    response,
    item,
    data_type,
    name,
    index,
) -> None:
    candidates = _indicator_candidates({"data": [item]}, {"response": response})
    enriched = _with_data_type_metadata(candidates, data_type)

    assert enriched == [
        SigmaCandidate(
            name,
            label=index,
            metadata={
                "index": index,
                "data_types": (data_type,),
                "indexes_by_data_type": {data_type: index},
            },
        )
    ]


def test_indicator_candidate_merge_keeps_indexes_for_each_data_type() -> None:
    merged = _dedupe_candidates(
        [
            SigmaCandidate(
                "Shared",
                metadata={
                    "data_types": ("ONE_D",),
                    "indexes_by_data_type": {"ONE_D": "shared-one-d"},
                },
            ),
            SigmaCandidate(
                "Shared",
                metadata={
                    "data_types": ("TWO_D_OC",),
                    "indexes_by_data_type": {"TWO_D_OC": "shared-order-cut"},
                },
            ),
        ]
    )

    assert merged[0].metadata["indexes_by_data_type"] == {
        "ONE_D": "shared-one-d",
        "TWO_D_OC": "shared-order-cut",
    }


def test_indicator_candidate_merge_preserves_same_domain_index_conflict() -> None:
    merged = _dedupe_candidates(
        [
            SigmaCandidate(
                "RMS",
                metadata={
                    "data_types": ("ONE_D",),
                    "indexes_by_data_type": {"ONE_D": "rms-a"},
                },
            ),
            SigmaCandidate(
                "RMS",
                metadata={
                    "data_types": ("ONE_D",),
                    "indexes_by_data_type": {"ONE_D": "rms-b"},
                },
            ),
        ]
    )

    assert merged[0].metadata["index_conflicts_by_data_type"] == {
        "ONE_D": ("rms-a", "rms-b")
    }


@pytest.mark.integration
def test_http_sigma_gateway_loads_dataset_1152_candidates_from_business_system() -> None:
    if os.getenv("SIGMA_RUN_INTEGRATION") != "1":
        pytest.skip("set SIGMA_RUN_INTEGRATION=1 to call the configured SigMA backend")

    gateway = HttpSigmaGateway.from_yaml()
    query = SigmaQuery.from_turn(
        TurnRequest(
            session_id="sigma-integration",
            message="load candidates",
            workspace_context={
                "data_load_mode": "dataset",
                "dataset_id": "1152",
                "products": [
                    {
                        "product_type": "hzzxkj-0527",
                        "product_version": "4",
                        "system_no": "7s-SNF1001",
                    },
                    {
                        "product_type": "dm0518",
                        "product_version": "4",
                        "system_no": "7s-SNF1001",
                    },
                ],
            },
        )
    )

    sensors = run(gateway.list_sensors(query))
    test_segments = run(gateway.list_test_segments(query))
    indicator_names = run(gateway.list_indicator_names(query))

    print(
        json.dumps(
            {
                "sensors": _candidate_payload(sensors),
                "test_segments": _candidate_payload(test_segments),
                "indicator_names": _candidate_payload(indicator_names),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    assert sensors
    assert test_segments
    assert indicator_names


def _candidate_payload(candidates: tuple[SigmaCandidate, ...]) -> list[dict[str, str]]:
    return [
        {"value": item.value, "label": item.label or ""}
        for item in candidates
    ]
