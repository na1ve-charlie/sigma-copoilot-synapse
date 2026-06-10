"""Request-scoped recognition candidates for Synapse."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from synapse.engine import TurnContext
from synapse.turns import TurnRequest


CANDIDATE_CATALOG_ARTIFACT = "candidate_catalog"


@dataclass(frozen=True, slots=True)
class CandidateItem:
    """One candidate value that can be exposed to Themis resolver prompts."""

    value: str
    label: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_input(cls, value: Any) -> "CandidateItem":
        if isinstance(value, CandidateItem):
            return value
        if isinstance(value, str):
            return cls(value=value)
        if isinstance(value, Mapping):
            raw_value = value.get("value")
            if raw_value is None:
                raise ValueError("candidate mapping must include value")
            label = value.get("label")
            metadata = value.get("metadata")
            return cls(
                value=str(raw_value),
                label=str(label) if label else None,
                metadata=metadata if isinstance(metadata, Mapping) else {},
            )
        raise TypeError(f"unsupported candidate input: {type(value).__name__}")

    def to_themis_payload(self) -> dict[str, str]:
        payload = {"value": self.value}
        if self.label:
            payload["label"] = self.label
        return payload


CandidateInput = str | Mapping[str, Any] | CandidateItem


@dataclass(frozen=True, slots=True)
class CandidateCatalog:
    """Entity-keyed candidates used for request-scoped recognition."""

    by_entity: Mapping[str, tuple[CandidateItem, ...]] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        candidates: Mapping[str, Sequence[CandidateInput]],
    ) -> "CandidateCatalog":
        return cls(
            by_entity={
                str(entity_type): tuple(
                    CandidateItem.from_input(item) for item in values
                )
                for entity_type, values in candidates.items()
            }
        )

    def candidates_for_entity(self, entity_type: str) -> list[CandidateItem]:
        return list(self.by_entity.get(entity_type, ()))

    def as_themis_resolver(self) -> "_CandidateCatalogThemisResolver":
        return _CandidateCatalogThemisResolver(self)


class CandidateCatalogLoader(Protocol):
    """Loads request-scoped candidates before recognition."""

    async def load(self, request: TurnRequest) -> CandidateCatalog:
        ...


class CandidateCatalogStep:
    """Inject the current turn's candidate catalog into the turn context."""

    def __init__(self, loader: CandidateCatalogLoader | None = None) -> None:
        self._loader = loader

    async def run(self, context: TurnContext) -> TurnContext:
        catalog = (
            await self._loader.load(context.request)
            if self._loader is not None
            else CandidateCatalog()
        )
        return context.with_artifact(CANDIDATE_CATALOG_ARTIFACT, catalog)


class _CandidateCatalogThemisResolver:
    def __init__(self, catalog: CandidateCatalog) -> None:
        self._catalog = catalog

    async def resolve(
        self,
        entity_type: str,
        context: Mapping[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        return [
            item.to_themis_payload()
            for item in self._catalog.candidates_for_entity(entity_type)
        ]
