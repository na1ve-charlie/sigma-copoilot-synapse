"""Observation-domain pre-recognition rules."""

from __future__ import annotations

import re
from collections.abc import Iterable

from synapse.recognition import (
    CANDIDATE_CATALOG_ARTIFACT,
    CandidateCatalog,
    CandidateItem,
)
from synapse.recognition.preprocessing.contracts import (
    DomainHint,
    MessageRewriteProposal,
    PreRecognitionContext,
    PreRecognitionEffect,
)


OBSERVATION_DOMAIN_ID = "observation"
_ENTITY_KEYS = (
    ("sensor", "sensors"),
    ("test_segment", "test_segments"),
    ("indicator", "indicator_names"),
    ("data_type", "data_types"),
)


class ObservationPreProcessor:
    """Normalize observation entities and narrow candidate catalogs."""

    domain_id = OBSERVATION_DOMAIN_ID
    priority = 50

    def matches(self, context: PreRecognitionContext) -> bool:
        return isinstance(
            context.artifacts.get(CANDIDATE_CATALOG_ARTIFACT),
            CandidateCatalog,
        )

    def propose(self, context: PreRecognitionContext) -> PreRecognitionEffect:
        catalog = context.artifacts[CANDIDATE_CATALOG_ARTIFACT]
        assert isinstance(catalog, CandidateCatalog)

        matches = _matched_candidates(context.message, catalog)
        message = _normalize_message(context.message, matches)
        message = _apply_unique_indicator_scope_hint(message, matches)
        narrowed = _narrow_catalog(catalog, matches) if matches else None
        rewrite = (
            MessageRewriteProposal(
                rewritten_message=message,
                confidence=1.0,
                reason="observation_entity_normalization",
            )
            if message != context.message
            else None
        )

        return PreRecognitionEffect(
            domain_id=self.domain_id,
            priority=self.priority,
            message_rewrite=rewrite,
            candidate_catalog=narrowed,
            domain_hints=(
                DomainHint(
                    domain_id=self.domain_id,
                    confidence=1.0,
                    reason="observation_candidate_match",
                ),
            )
            if matches
            else (),
            diagnostics={"observation_matches": sorted(matches)},
        )


def _matched_candidates(
    message: str,
    catalog: CandidateCatalog,
) -> dict[str, tuple[CandidateItem, ...]]:
    result: dict[str, tuple[CandidateItem, ...]] = {}
    for keys in _ENTITY_KEYS:
        candidates = _dedupe_candidates(
            item
            for key in keys
            for item in catalog.candidates_for_entity(key)
            if _matches_any_alias(message, item)
        )
        if candidates:
            for key in keys:
                result[key] = candidates
    return result


def _normalize_message(
    message: str,
    matches: dict[str, tuple[CandidateItem, ...]],
) -> str:
    normalized = message
    data_type_values = {
        item.value
        for key in ("data_type", "data_types")
        for item in matches.get(key, ())
    }
    for item in _dedupe_candidates(
        item for values in matches.values() for item in values
    ):
        if item.value in data_type_values:
            continue
        for alias in sorted(_aliases(item), key=len, reverse=True):
            if alias != item.value:
                normalized = _replace_alias(normalized, alias, item.value)
    return normalized


def _apply_unique_indicator_scope_hint(
    message: str,
    matches: dict[str, tuple[CandidateItem, ...]],
) -> str:
    if matches.get("data_type") or matches.get("data_types"):
        return message
    indicators = matches.get("indicator_names") or matches.get("indicator") or ()
    data_types = {
        data_type
        for item in indicators
        for data_type in _metadata_values(item, "data_type")
    }
    if len(indicators) != 1 or len(data_types) != 1:
        return message
    data_type = next(iter(data_types))
    return message if data_type in message else f"{data_type} {message}"


def _narrow_catalog(
    catalog: CandidateCatalog,
    matches: dict[str, tuple[CandidateItem, ...]],
) -> CandidateCatalog:
    by_entity = dict(catalog.by_entity)
    by_entity.update(matches)
    by_entity.update(_dependency_matches(catalog, matches))
    return CandidateCatalog(by_entity=by_entity)


def _dependency_matches(
    catalog: CandidateCatalog,
    matches: dict[str, tuple[CandidateItem, ...]],
) -> dict[str, tuple[CandidateItem, ...]]:
    scope = {
        key: {item.value for item in matches.get(key, ())}
        for key in ("data_type", "sensor", "test_segment")
    }
    indicators = _dedupe_candidates(
        item
        for key in ("indicator", "indicator_names")
        for item in catalog.candidates_for_entity(key)
        if _matches_scope(item, scope)
    )
    if not indicators:
        return {}
    return {"indicator": indicators, "indicator_names": indicators}


def _matches_scope(
    item: CandidateItem,
    scope: dict[str, set[str]],
) -> bool:
    constrained = False
    for key, selected in scope.items():
        if not selected:
            continue
        allowed = set(_metadata_values(item, key))
        if not allowed:
            continue
        constrained = True
        if allowed.isdisjoint(selected):
            return False
    return constrained


def _metadata_values(item: CandidateItem, key: str) -> tuple[str, ...]:
    aliases = {
        "data_type": ("data_type", "data_types", "domain", "domains"),
        "sensor": ("sensor", "sensors"),
        "test_segment": ("test_segment", "test_segments"),
    }
    values = []
    for alias in aliases[key]:
        value = item.metadata.get(alias)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, Iterable):
            values.extend(str(item) for item in value if item is not None)
    return tuple(values)


def _matches_any_alias(message: str, item: CandidateItem) -> bool:
    return any(_alias_pattern(alias).search(message) for alias in _aliases(item))


def _replace_alias(message: str, alias: str, value: str) -> str:
    return _alias_pattern(alias).sub(value, message)


def _alias_pattern(value: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])", re.I)


def _aliases(item: CandidateItem) -> tuple[str, ...]:
    aliases = [item.value, item.label]
    raw_aliases = item.metadata.get("aliases")
    if isinstance(raw_aliases, str):
        aliases.append(raw_aliases)
    elif isinstance(raw_aliases, Iterable):
        aliases.extend(str(value) for value in raw_aliases if value)
    return tuple(str(alias) for alias in aliases if alias)


def _dedupe_candidates(values: Iterable[CandidateItem]) -> tuple[CandidateItem, ...]:
    result: list[CandidateItem] = []
    seen: set[str] = set()
    for value in values:
        if value.value in seen:
            continue
        seen.add(value.value)
        result.append(value)
    return tuple(result)
