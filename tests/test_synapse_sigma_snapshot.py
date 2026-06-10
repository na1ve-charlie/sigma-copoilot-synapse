from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from synapse.integrations.sigma import SigmaCandidate, SigmaQuery
from synapse.integrations.sigma.contracts import SigmaObservationAvailabilityRow
from synapse.integrations.sigma.snapshot import (
    SnapshotSigmaGateway,
    SigmaSnapshot,
    SigmaSnapshotRow,
    capture_snapshot,
)
from synapse.turns import TurnRequest


def run(coro):
    return asyncio.run(coro)


@dataclass(frozen=True)
class FakeSnapshotGateway:
    availability_rows: tuple[SigmaObservationAvailabilityRow, ...]
    indicator_map: dict[tuple[str, str, str], tuple[SigmaCandidate, ...]]

    async def list_observation_availability(
        self,
        query: SigmaQuery,
    ) -> tuple[SigmaObservationAvailabilityRow, ...]:
        return self.availability_rows

    async def list_observation_indicator_names(
        self,
        query: SigmaQuery,
        *,
        domain: str,
        sensors: tuple[str, ...],
        test_segments: tuple[str, ...],
    ) -> tuple[SigmaCandidate, ...]:
        return self.indicator_map[(domain, sensors[0], test_segments[0])]


def test_capture_snapshot_materializes_rows_from_sigma_gateway() -> None:
    gateway = FakeSnapshotGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("ONE_D", "Vib1", "1500rpm"),
            SigmaObservationAvailabilityRow("TWO_D_FS", "sensor01", "Spd-rDL"),
        ),
        indicator_map={
            ("ONE_D", "Vib1", "1500rpm"): (
                SigmaCandidate("48阶", label="one-d-48"),
                SigmaCandidate("均方根-均方根法", label="one-d-rms"),
            ),
            ("TWO_D_FS", "sensor01", "Spd-rDL"): (
                SigmaCandidate("频谱", label="fs-spectrum"),
            ),
        },
    )
    request = TurnRequest(
        session_id="snapshot-1152",
        message="capture",
        workspace_context={"dataset_id": "1152", "lang": "zh"},
    )

    snapshot = run(
        capture_snapshot(
            gateway,  # type: ignore[arg-type]
            request,
            snapshot_id="offline_1152",
            name="offline / dataset 1152",
        )
    )

    assert snapshot.workspace_context == {"dataset_id": "1152", "lang": "zh"}
    assert snapshot.rows == (
        SigmaSnapshotRow("ONE_D", "Vib1", "1500rpm", "48阶", "one-d-48"),
        SigmaSnapshotRow(
            "ONE_D",
            "Vib1",
            "1500rpm",
            "均方根-均方根法",
            "one-d-rms",
        ),
        SigmaSnapshotRow("TWO_D_FS", "sensor01", "Spd-rDL", "频谱", "fs-spectrum"),
    )


def test_snapshot_sigma_gateway_replays_candidates_and_domain_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "offline_1152.json"
    SigmaSnapshot(
        snapshot_id="offline_1152",
        name="offline / dataset 1152",
        workspace_context={"dataset_id": "1152", "lang": "zh"},
        rows=(
            SigmaSnapshotRow("ONE_D", "Vib1", "1500rpm", "48阶", "one-d-48"),
            SigmaSnapshotRow(
                "TWO_D_OC",
                "sensor01",
                "Spd-rDL",
                "48阶",
                "order-cut-48",
            ),
            SigmaSnapshotRow(
                "TWO_D_FS",
                "sensor01",
                "Spd-rDL",
                "频谱",
                "fs-spectrum",
            ),
            SigmaSnapshotRow(
                "TWO_D_CEP",
                "sensor02",
                "Spd-rCH",
                "倒阶次谱",
                "cepstrum",
            ),
        ),
    ).save(path)

    gateway = SnapshotSigmaGateway.load(path)
    query = SigmaQuery.from_turn(
        TurnRequest(
            session_id="offline-test",
            message="show indicators",
            workspace_context={"dataset_id": "1152", "lang": "zh"},
        )
    )

    sensors = [item.value for item in run(gateway.list_sensors(query))]
    indicators = {
        item.value: item for item in run(gateway.list_indicator_names(query))
    }
    scoped = run(
        gateway.list_observation_indicator_names(
            query,
            domain="TWO_D_FS",
            sensors=("sensor01",),
            test_segments=("Spd-rDL",),
        )
    )

    assert sensors == ["Vib1", "sensor01", "sensor02"]
    assert set(indicators["48阶"].metadata["data_types"]) == {"ONE_D", "TWO_D_OC"}
    assert indicators["48阶"].metadata["indexes_by_data_type"] == {
        "ONE_D": "one-d-48",
        "TWO_D_OC": "order-cut-48",
    }
    assert indicators["频谱"].label == "fs-spectrum"
    assert [item.value for item in scoped] == ["频谱"]
    assert scoped[0].metadata["indexes_by_data_type"] == {
        "TWO_D_FS": "fs-spectrum"
    }


def test_snapshot_sigma_gateway_preserves_same_domain_index_conflicts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conflicting-indexes.json"
    SigmaSnapshot(
        snapshot_id="conflicting-indexes",
        name="conflicting indexes",
        workspace_context={"dataset_id": "1152"},
        rows=(
            SigmaSnapshotRow("ONE_D", "Vib1", "runup", "RMS", "rms-a"),
            SigmaSnapshotRow("ONE_D", "Vib2", "coast", "RMS", "rms-b"),
        ),
    ).save(path)

    gateway = SnapshotSigmaGateway.load(path)
    query = SigmaQuery.from_turn(
        TurnRequest(
            session_id="offline-conflict",
            message="show RMS",
            workspace_context={"dataset_id": "1152"},
        )
    )

    candidate = run(gateway.list_indicator_names(query))[0]

    assert candidate.metadata["index_conflicts_by_data_type"] == {
        "ONE_D": ("rms-a", "rms-b")
    }
