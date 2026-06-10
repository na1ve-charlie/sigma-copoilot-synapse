"""SigMA snapshot capture and replay helpers for offline tests."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from synapse.integrations.sigma.contracts import (
    SigmaCandidate,
    SigmaObservationAvailabilityRow,
    SigmaQuery,
)
from synapse.integrations.sigma.http import DEFAULT_RESOLVER_CONFIG, HttpSigmaGateway
from synapse.turns import TurnRequest, WorkspaceContext


@dataclass(frozen=True, slots=True)
class SigmaSnapshotRow:
    domain: str
    sensor: str
    test_segment: str
    indicator: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class SigmaSnapshot:
    snapshot_id: str
    name: str
    workspace_context: dict[str, Any]
    rows: tuple[SigmaSnapshotRow, ...]

    @classmethod
    def load(cls, path: str | Path) -> "SigmaSnapshot":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            snapshot_id=str(payload.get("id") or Path(path).stem),
            name=str(payload.get("name") or Path(path).stem),
            workspace_context=dict(payload.get("workspace_context") or {}),
            rows=tuple(
                SigmaSnapshotRow(
                    domain=str(item["domain"]),
                    sensor=str(item["sensor"]),
                    test_segment=str(item["test_segment"]),
                    indicator=str(item["indicator"]),
                    label=(
                        str(item["label"])
                        if item.get("label") not in (None, "")
                        else None
                    ),
                )
                for item in payload.get("rows", [])
                if isinstance(item, Mapping)
            ),
        )

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": self.snapshot_id,
            "name": self.name,
            "workspace_context": self.workspace_context,
            "rows": [
                {
                    "domain": row.domain,
                    "sensor": row.sensor,
                    "test_segment": row.test_segment,
                    "indicator": row.indicator,
                    "label": row.label,
                }
                for row in self.rows
            ],
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class SnapshotSigmaGateway:
    def __init__(
        self,
        snapshot: SigmaSnapshot,
        *,
        domains_by_action: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._domains_by_action = dict(domains_by_action or {})

    @classmethod
    def load(
        cls,
        snapshot_path: str | Path,
        *,
        resolver_path: str | Path = DEFAULT_RESOLVER_CONFIG,
    ) -> "SnapshotSigmaGateway":
        return cls(
            SigmaSnapshot.load(snapshot_path),
            domains_by_action=_load_domains_by_action(resolver_path),
        )

    async def list_sensors(self, query: SigmaQuery) -> tuple[SigmaCandidate, ...]:
        return tuple(
            SigmaCandidate(value)
            for value in _dedupe(row.sensor for row in self._rows_for_query(query))
        )

    async def list_test_segments(self, query: SigmaQuery) -> tuple[SigmaCandidate, ...]:
        return tuple(
            SigmaCandidate(value)
            for value in _dedupe(row.test_segment for row in self._rows_for_query(query))
        )

    async def list_indicator_names(self, query: SigmaQuery) -> tuple[SigmaCandidate, ...]:
        merged: dict[str, SigmaCandidate] = {}
        for row in self._rows_for_query(query):
            current = merged.get(row.indicator)
            data_types = (row.domain,) if current is None else tuple(
                dict.fromkeys(
                    (*current.metadata.get("data_types", ()), row.domain)
                )
            )
            indexes_by_data_type = dict(
                current.metadata.get("indexes_by_data_type", {})
                if current is not None
                else {}
            )
            conflicts_by_data_type = dict(
                current.metadata.get("index_conflicts_by_data_type", {})
                if current is not None
                else {}
            )
            if row.label:
                existing_index = indexes_by_data_type.get(row.domain)
                if existing_index and existing_index != row.label:
                    conflicts_by_data_type[row.domain] = tuple(
                        dict.fromkeys(
                            (
                                *conflicts_by_data_type.get(
                                    row.domain,
                                    (existing_index,),
                                ),
                                row.label,
                            )
                        )
                    )
                else:
                    indexes_by_data_type[row.domain] = row.label
            metadata: dict[str, Any] = {"data_types": data_types}
            if indexes_by_data_type:
                metadata["indexes_by_data_type"] = indexes_by_data_type
            if conflicts_by_data_type:
                metadata["index_conflicts_by_data_type"] = conflicts_by_data_type
            merged[row.indicator] = SigmaCandidate(
                row.indicator,
                label=row.label or (current.label if current else None),
                metadata=metadata,
            )
        return tuple(merged.values())

    async def list_observation_availability(
        self,
        query: SigmaQuery,
    ) -> tuple[SigmaObservationAvailabilityRow, ...]:
        seen: set[tuple[str, str, str]] = set()
        result: list[SigmaObservationAvailabilityRow] = []
        for row in self._rows_for_query(query):
            key = (row.domain, row.sensor, row.test_segment)
            if key in seen:
                continue
            seen.add(key)
            result.append(SigmaObservationAvailabilityRow(*key))
        return tuple(result)

    async def list_observation_indicator_names(
        self,
        query: SigmaQuery,
        *,
        domain: str,
        sensors: tuple[str, ...],
        test_segments: tuple[str, ...],
    ) -> tuple[SigmaCandidate, ...]:
        filtered = [
            row
            for row in self._rows_for_query(query)
            if row.domain == domain
            and row.sensor in sensors
            and row.test_segment in test_segments
        ]
        seen: set[str] = set()
        result: list[SigmaCandidate] = []
        for row in filtered:
            if row.indicator in seen:
                continue
            seen.add(row.indicator)
            result.append(
                SigmaCandidate(
                    row.indicator,
                    label=row.label,
                    metadata={
                        "data_types": (row.domain,),
                        **(
                            {"indexes_by_data_type": {row.domain: row.label}}
                            if row.label
                            else {}
                        ),
                    },
                )
            )
        return tuple(result)

    def domains_for_action(self, action_name: str) -> tuple[str, ...]:
        return self._domains_by_action.get(action_name, ())

    def _rows_for_query(self, query: SigmaQuery) -> tuple[SigmaSnapshotRow, ...]:
        requested = (
            None
            if query.workspace_context is None
            else query.workspace_context.dataset_id
        )
        snapshot_dataset_id = self._snapshot.workspace_context.get("dataset_id")
        if requested and snapshot_dataset_id and requested != snapshot_dataset_id:
            return ()
        return self._snapshot.rows


async def capture_snapshot(
    gateway: HttpSigmaGateway,
    request: TurnRequest,
    *,
    snapshot_id: str,
    name: str,
) -> SigmaSnapshot:
    query = SigmaQuery.from_turn(request)
    availability = await gateway.list_observation_availability(query)
    seen: set[tuple[str, str, str, str, str | None]] = set()
    rows: list[SigmaSnapshotRow] = []
    for item in availability:
        indicators = await gateway.list_observation_indicator_names(
            query,
            domain=item.domain,
            sensors=(item.sensor,),
            test_segments=(item.test_segment,),
        )
        for indicator in indicators:
            key = (
                item.domain,
                item.sensor,
                item.test_segment,
                indicator.value,
                indicator.label,
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                SigmaSnapshotRow(
                    domain=item.domain,
                    sensor=item.sensor,
                    test_segment=item.test_segment,
                    indicator=indicator.value,
                    label=indicator.label,
                )
            )
    workspace_context = _snapshot_workspace_context(request.workspace_context)
    return SigmaSnapshot(snapshot_id, name, workspace_context, tuple(rows))


def _load_domains_by_action(path: str | Path) -> dict[str, tuple[str, ...]]:
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    resolvers = loaded.get("resolvers")
    if not isinstance(resolvers, Mapping):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for source_name in ("observation_availability", "observation_indicators"):
        source = resolvers.get(source_name)
        if not isinstance(source, Mapping):
            continue
        for action_name, domains in (source.get("domain_by_action") or {}).items():
            if action_name in result or not isinstance(action_name, str):
                continue
            if isinstance(domains, str):
                result[action_name] = (domains,)
            elif isinstance(domains, list):
                result[action_name] = tuple(str(item) for item in domains)
    return result


def _snapshot_workspace_context(context: WorkspaceContext | None) -> dict[str, Any]:
    if context is None:
        return {}
    payload = context.model_dump(mode="json", exclude_none=True)
    return {
        key: value
        for key, value in payload.items()
        if value not in ([], {})
    }


def _dedupe(values) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value is not None))


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a SigMA snapshot for offline tests.")
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config-path", default="configs/copilot.yaml")
    parser.add_argument("--resolver-path", default=str(DEFAULT_RESOLVER_CONFIG))
    parser.add_argument("--workspace-context")
    parser.add_argument("--workspace-context-file")
    args = parser.parse_args()

    context_json = args.workspace_context
    if args.workspace_context_file:
        context_json = Path(args.workspace_context_file).read_text(encoding="utf-8")
    if not context_json:
        raise SystemExit("either --workspace-context or --workspace-context-file is required")
    workspace_context = WorkspaceContext.model_validate_json(context_json)
    request = TurnRequest(
        session_id=f"snapshot-{args.snapshot_id}",
        message="capture sigma snapshot",
        workspace_context=workspace_context,
    )

    snapshot = asyncio.run(
        capture_snapshot(
            HttpSigmaGateway.from_yaml(
                config_path=args.config_path,
                resolver_path=args.resolver_path,
            ),
            request,
            snapshot_id=args.snapshot_id,
            name=args.name,
        )
    )
    snapshot.save(args.output)


if __name__ == "__main__":
    main()
