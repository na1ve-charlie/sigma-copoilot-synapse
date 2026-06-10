"""SigMA-backed candidate catalog loading."""

from __future__ import annotations

from synapse.integrations.sigma.contracts import (
    SigmaCandidate,
    SigmaGateway,
    SigmaQuery,
)
from synapse.integrations.sigma.http import HttpSigmaGateway
from synapse.recognition import CandidateCatalog, CandidateItem
from synapse.turns import TurnRequest


_DATA_TYPE_CANDIDATES = (
    CandidateItem(
        "ONE_D",
        label="一维数据",
        metadata={
            "aliases": ("一维指标", "one dim", "one dimensional data"),
        },
    ),
    CandidateItem(
        "TWO_D_TD",
        label="时间域",
        metadata={"aliases": ("时域", "时域数据", "时间域数据", "time domain")},
    ),
    CandidateItem(
        "TWO_D_FS",
        label="频谱",
        metadata={
            "aliases": ("查看频谱", "看频谱", "spectrum", "frequency spectrum"),
        },
    ),
    CandidateItem(
        "TWO_D_OS",
        label="阶次谱",
        metadata={"aliases": ("order spectrum", "order map")},
    ),
    CandidateItem(
        "TWO_D_OC",
        label="阶次切片",
        metadata={"aliases": ("order slice",)},
    ),
    CandidateItem(
        "TWO_D_CEP",
        label="倒谱",
        metadata={"aliases": ("倒频谱", "倒阶次谱", "cepstrum")},
    ),
    CandidateItem(
        "TWO_D_PS",
        label="心理声学",
        metadata={"aliases": ("psychoacoustics",)},
    ),
)


class SigmaCandidateCatalogLoader:
    """Load request-scoped recognition candidates from SigMA."""

    def __init__(self, gateway: SigmaGateway) -> None:
        self._gateway = gateway

    @property
    def gateway(self) -> SigmaGateway:
        return self._gateway

    @classmethod
    def from_yaml(cls) -> "SigmaCandidateCatalogLoader":
        return cls(HttpSigmaGateway.from_yaml())

    async def load(self, request: TurnRequest) -> CandidateCatalog:
        query = SigmaQuery.from_turn(request)
        sensors = await self._gateway.list_sensors(query)
        test_segments = await self._gateway.list_test_segments(query)
        indicators = await self._gateway.list_indicator_names(query)
        return CandidateCatalog.from_mapping(
            {
                "sensor": _candidate_items(sensors),
                "sensors": _candidate_items(sensors),
                "test_segment": _candidate_items(test_segments),
                "test_segments": _candidate_items(test_segments),
                "indicator": _candidate_items(indicators),
                "indicator_names": _candidate_items(indicators),
                "data_type": _DATA_TYPE_CANDIDATES,
                "data_types": _DATA_TYPE_CANDIDATES,
            }
        )


def _candidate_items(
    candidates: tuple[SigmaCandidate, ...],
) -> tuple[CandidateItem, ...]:
    return tuple(
        CandidateItem(
            value=item.value,
            label=item.label,
            metadata=item.metadata,
        )
        for item in candidates
    )
