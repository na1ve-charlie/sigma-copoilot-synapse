import asyncio
from dataclasses import dataclass

from synapse.domains.observation.catalog import ObservationCatalog
from synapse.domains.observation.sigma_catalog import SigmaObservationCatalogSource
from synapse.integrations.sigma import SigmaCandidate, SigmaQuery
from synapse.integrations.sigma.contracts import SigmaObservationAvailabilityRow
from synapse.turns import TurnRequest


def run(coro):
    return asyncio.run(coro)


@dataclass(frozen=True, slots=True)
class IndicatorCall:
    domain: str
    sensors: tuple[str, ...]
    test_segments: tuple[str, ...]


class FakeObservationGateway:
    def __init__(
        self,
        *,
        availability_rows: tuple[SigmaObservationAvailabilityRow, ...] = (),
        indicators: dict[tuple[str, tuple[str, ...], tuple[str, ...]], tuple[SigmaCandidate, ...]] | None = None,
        domains_by_action: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._availability_rows = availability_rows
        self._indicators = indicators or {}
        self._domains_by_action = domains_by_action or {}
        self.indicator_calls: list[IndicatorCall] = []

    async def list_observation_availability(
        self,
        query: SigmaQuery,
    ) -> tuple[SigmaObservationAvailabilityRow, ...]:
        return self._availability_rows

    async def list_observation_indicator_names(
        self,
        query: SigmaQuery,
        *,
        domain: str,
        sensors: tuple[str, ...],
        test_segments: tuple[str, ...],
    ) -> tuple[SigmaCandidate, ...]:
        self.indicator_calls.append(IndicatorCall(domain, sensors, test_segments))
        return self._indicators[(domain, sensors, test_segments)]

    def domains_for_action(self, action_name: str) -> tuple[str, ...]:
        return self._domains_by_action.get(action_name, ())


def test_sigma_observation_catalog_source_loads_availability_rows() -> None:
    source = SigmaObservationCatalogSource(
        FakeObservationGateway(
            availability_rows=(
                SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
                SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
                SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib2", "coast"),
            )
        )
    )

    catalog = run(source.load_availability_catalog(request()))

    assert isinstance(catalog, ObservationCatalog)
    assert catalog.distinct_entries(("data_types", "sensors", "test_segments")) == [
        {
            "data_types": "TWO_D_FS",
            "sensors": "Vib1",
            "test_segments": "runup",
        },
        {
            "data_types": "TWO_D_CEP",
            "sensors": "Vib2",
            "test_segments": "coast",
        },
    ]


def test_sigma_observation_catalog_source_materializes_scoped_indicator_entries() -> None:
    source = SigmaObservationCatalogSource(
        FakeObservationGateway(
            indicators={
                (
                    "TWO_D_FS",
                    ("Vib1",),
                    ("runup",),
                ): (
                    SigmaCandidate("RMS"),
                    SigmaCandidate("Spectrum"),
                )
            }
        )
    )

    catalog = run(
        source.load_indicator_catalog(
            request(),
            domain="TWO_D_FS",
            sensors=("Vib1",),
            test_segments=("runup",),
        )
    )

    assert catalog.distinct_entries(
        ("data_types", "sensors", "test_segments", "indicator_names")
    ) == [
        {
            "data_types": "TWO_D_FS",
            "sensors": "Vib1",
            "test_segments": "runup",
            "indicator_names": "RMS",
        },
        {
            "data_types": "TWO_D_FS",
            "sensors": "Vib1",
            "test_segments": "runup",
            "indicator_names": "Spectrum",
        },
    ]


def request() -> TurnRequest:
    return TurnRequest(
        session_id="s1",
        message="query",
        workspace_context={"dataset_id": "1152"},
    )
