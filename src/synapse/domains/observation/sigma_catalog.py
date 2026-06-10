from __future__ import annotations

from typing import Protocol, runtime_checkable

from synapse.domains.observation.catalog import ObservationCatalog, ObservationCatalogEntry
from synapse.integrations.sigma import SigmaCandidate, SigmaQuery
from synapse.integrations.sigma.contracts import SigmaObservationAvailabilityRow
from synapse.turns import TurnRequest


@runtime_checkable
class ObservationSigmaGateway(Protocol):
    async def list_observation_availability(
        self,
        query: SigmaQuery,
    ) -> tuple[SigmaObservationAvailabilityRow, ...]:
        ...

    async def list_observation_indicator_names(
        self,
        query: SigmaQuery,
        *,
        domain: str,
        sensors: tuple[str, ...],
        test_segments: tuple[str, ...],
    ) -> tuple[SigmaCandidate, ...]:
        ...

    def domains_for_action(self, action_name: str) -> tuple[str, ...]:
        ...


class SigmaObservationCatalogSource:
    def __init__(self, gateway: ObservationSigmaGateway) -> None:
        self._gateway = gateway

    async def load_availability_catalog(
        self,
        request: TurnRequest,
    ) -> ObservationCatalog:
        rows = await self._gateway.list_observation_availability(
            SigmaQuery.from_turn(request)
        )
        return ObservationCatalog(
            tuple(
                ObservationCatalogEntry(
                    data_types=row.domain,
                    sensors=row.sensor,
                    test_segments=row.test_segment,
                )
                for row in rows
            )
        )

    async def load_indicator_catalog(
        self,
        request: TurnRequest,
        *,
        domain: str,
        sensors: tuple[str, ...],
        test_segments: tuple[str, ...],
    ) -> ObservationCatalog:
        indicators = await self._gateway.list_observation_indicator_names(
            SigmaQuery.from_turn(request),
            domain=domain,
            sensors=sensors,
            test_segments=test_segments,
        )
        return ObservationCatalog(
            tuple(
                ObservationCatalogEntry(
                    data_types=domain,
                    sensors=sensor,
                    test_segments=test_segment,
                    indicator_names=indicator.value,
                )
                for sensor in sensors
                for test_segment in test_segments
                for indicator in indicators
            )
        )

    def domains_for_action(self, action_name: str) -> tuple[str, ...]:
        return self._gateway.domains_for_action(action_name)
