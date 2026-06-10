from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from synapse.domains.observation.catalog import (
    ObservationCatalog,
    ObservationCatalogEntry,
)
from synapse.domains.observation.resolver_query_view import build_resolver_query_facets
from synapse.domains.observation.scope import (
    ObservationScopePolicy,
    build_observation_scope_context,
    build_observation_scope_policy,
)
from synapse.domains.observation.sigma_catalog import SigmaObservationCatalogSource
from synapse.planning.planner import PlanningContext
from synapse.planning.plans import ClarifyPlan, Plan, ReplyPlan
from synapse.recognition import CandidateCatalog
from synapse.slots.state import SlotState


DOMAIN_ID = "nvh.data_observation"
VIEW_ID = "observation_resolver_query"
RESOLVER_QUERY_PREFIX = "inquiry.nvh.resolver_query."


@dataclass(frozen=True, slots=True)
class ResolverQueryField:
    intent_name: str
    slot_name: str
    domain_id: str = DOMAIN_ID
    view_id: str = VIEW_ID


@dataclass(frozen=True, slots=True)
class ObservationResolverQueryRequest:
    slot_names: tuple[str, ...]
    fields: tuple[ResolverQueryField, ...]


class ObservationResolverQueryResponder:
    def __init__(
        self,
        *,
        entries: Sequence[Mapping[str, Any]] = (),
        catalog: ObservationCatalog | None = None,
        catalog_source: SigmaObservationCatalogSource | None = None,
        action_name_by_intent: Mapping[str, str] | None = None,
        fields: Sequence[ResolverQueryField] = (),
        scope_policy: ObservationScopePolicy | None = None,
    ) -> None:
        self._catalog = catalog or _catalog_from_entries(entries)
        self._catalog_source = catalog_source
        self._action_name_by_intent = dict(action_name_by_intent or {})
        self._fields = tuple(fields) or _default_fields()
        self._scope_policy = scope_policy or build_observation_scope_policy(
            self._catalog_source.domains_for_action
            if self._catalog_source is not None
            else None
        )

    def request_from_intents(
        self,
        intent_names: Sequence[str],
    ) -> ObservationResolverQueryRequest | None:
        names = set(intent_names)
        fields = tuple(field for field in self._fields if field.intent_name in names)
        if not fields:
            return None
        return ObservationResolverQueryRequest(
            slot_names=tuple(field.slot_name for field in fields),
            fields=fields,
        )

    async def build(
        self,
        context: PlanningContext,
        intent_names: tuple[str, ...],
    ) -> Plan | None:
        if _unknown_resolver_query_intents(intent_names, self._fields):
            return ClarifyPlan(
                reason="ambiguous_intent",
                message="暂不支持当前查询。",
            )
        request = self.request_from_intents(intent_names)
        if request is None:
            return None
        if _has_route_conflict(request.fields):
            return ClarifyPlan(
                reason="ambiguous_intent",
                message="当前查询意图存在歧义。",
            )
        catalog = await self._catalog_for_request(context, request, intent_names)
        if catalog is None:
            return ClarifyPlan(
                reason="ambiguous_intent",
                message="当前观测范围存在歧义。",
            )
        if (
            catalog.is_empty
            and "indicator_names" in request.slot_names
            and _slot_values(context.slot_state, "indicator_names")
        ):
            return ClarifyPlan(
                reason="ambiguous_intent",
                message="当前观测选择不可用。",
            )
        entries = catalog.distinct_entries(request.slot_names)
        return ReplyPlan(
            message="当前可用候选如下。",
            data={
                "entries": entries,
                "facets": build_resolver_query_facets(
                    catalog,
                    request.slot_names,
                    context.slot_state,
                )
                if entries
                else [],
            },
        )

    async def _catalog_for_request(
        self,
        context: PlanningContext,
        request: ObservationResolverQueryRequest,
        intent_names: tuple[str, ...],
    ) -> ObservationCatalog | None:
        if self._catalog_source is None:
            return _scope_catalog(
                self._catalog,
                request.slot_names,
                context.slot_state,
                (),
            )
        availability = await self._catalog_source.load_availability_catalog(
            context.request
        )
        action_name = _action_name_for_context(
            intent_names,
            context,
            self._action_name_by_intent,
        )
        availability = _scope_catalog(
            availability,
            request.slot_names,
            context.slot_state,
            ()
            if "indicator_names" in request.slot_names
            else _domains_for_action(
                self._catalog_source,
                action_name,
                request.slot_names,
            ),
            include_selected_data_types="indicator_names" not in request.slot_names,
        )
        if "indicator_names" not in request.slot_names:
            return availability
        if availability.is_empty:
            return ObservationCatalog(())
        scope = _indicator_scope_decision(
            availability,
            context,
            self._scope_policy,
            explicit_data_types=(
                *_explicit_data_types_from_decision(context.decision),
                *_domains_for_action(
                    self._catalog_source,
                    _action_name_from_intents(
                        intent_names,
                        self._action_name_by_intent,
                    ),
                    ("indicator_names",),
                ),
                *_pre_recognition_explicit_data_types(
                    context.artifacts,
                    context.artifacts.get("candidate_catalog"),
                ),
            ),
        )
        if scope.status == "invalid":
            return None
        domains = _indicator_domains_for_query(availability, scope)
        if not domains:
            return ObservationCatalog(())
        indicators = await _load_indicator_catalog_for_domains(
            self._catalog_source,
            context,
            availability,
            domains,
        )
        selected_indicator_names = tuple(
            _slot_values(context.slot_state, "indicator_names")
        )
        if selected_indicator_names:
            scoped = indicators.where(indicator_names=selected_indicator_names)
            if scoped.is_empty:
                return ObservationCatalog(())
        return indicators


def _default_fields() -> tuple[ResolverQueryField, ...]:
    return (
        ResolverQueryField(
            "inquiry.nvh.resolver_query.sensors",
            "sensors",
        ),
        ResolverQueryField(
            "inquiry.nvh.resolver_query.test_segments",
            "test_segments",
        ),
        ResolverQueryField(
            "inquiry.nvh.resolver_query.indicators",
            "indicator_names",
        ),
        ResolverQueryField(
            "inquiry.nvh.resolver_query.data_types",
            "data_types",
        ),
    )


def _unknown_resolver_query_intents(
    intent_names: Sequence[str],
    fields: Sequence[ResolverQueryField],
) -> tuple[str, ...]:
    known = {field.intent_name for field in fields}
    return tuple(
        intent_name
        for intent_name in intent_names
        if intent_name.startswith(RESOLVER_QUERY_PREFIX) and intent_name not in known
    )


def _has_route_conflict(fields: Sequence[ResolverQueryField]) -> bool:
    routes = {(field.domain_id, field.view_id) for field in fields}
    return len(routes) > 1


def _catalog_from_entries(
    entries: Sequence[Mapping[str, Any]],
) -> ObservationCatalog:
    return ObservationCatalog(
        tuple(
            ObservationCatalogEntry(
                sensors=_entry_text(entry, "sensors"),
                test_segments=_entry_text(entry, "test_segments"),
                indicator_names=_entry_text(entry, "indicator_names"),
                data_types=_entry_text(entry, "data_types"),
            )
            for entry in entries
        )
    )


def _entry_text(entry: Mapping[str, Any], slot_name: str) -> str | None:
    value = entry.get(slot_name)
    if value is None:
        return None
    return str(value)


def _scope_catalog(
    catalog: ObservationCatalog,
    slot_names: Sequence[str],
    slot_state: SlotState,
    action_domains: tuple[str, ...],
    *,
    include_selected_data_types: bool = True,
) -> ObservationCatalog:
    data_types = (
        tuple(_slot_values(slot_state, "data_types"))
        if include_selected_data_types
        else ()
    )
    if "data_types" not in slot_names:
        catalog = catalog.where(data_types=data_types or action_domains)
    if "sensors" not in slot_names:
        catalog = catalog.where(sensors=_slot_values(slot_state, "sensors"))
    if "test_segments" not in slot_names:
        catalog = catalog.where(test_segments=_slot_values(slot_state, "test_segments"))
    if "indicator_names" not in slot_names:
        catalog = catalog.where(
            indicator_names=_slot_values(slot_state, "indicator_names")
        )
    return catalog


def _indicator_scope_decision(
    availability: ObservationCatalog,
    context: PlanningContext,
    policy: ObservationScopePolicy,
    *,
    explicit_data_types: Sequence[str],
) -> Any:
    decision = policy.resolve(
        build_observation_scope_context(
            slot_state=context.slot_state,
            artifacts=context.artifacts,
            explicit_data_types=explicit_data_types,
            indicator_data_types=_indicator_data_types(context),
            available_data_types=availability.values("data_types"),
        )
    )
    return decision


def _indicator_domains_for_query(
    availability: ObservationCatalog,
    scope: Any,
) -> tuple[str, ...]:
    if scope.status == "resolved" and scope.data_type is not None:
        return (scope.data_type,)
    if scope.status == "invalid":
        return ()
    remaining = tuple(availability.values("data_types"))
    return remaining


async def _load_indicator_catalog_for_domains(
    source: SigmaObservationCatalogSource,
    context: PlanningContext,
    availability: ObservationCatalog,
    domains: Sequence[str],
) -> ObservationCatalog:
    selected_sensors = tuple(_slot_values(context.slot_state, "sensors"))
    selected_test_segments = tuple(_slot_values(context.slot_state, "test_segments"))
    catalogs = []
    for domain in domains:
        scoped = availability.where(data_types=(domain,))
        sensors = selected_sensors or tuple(scoped.values("sensors"))
        test_segments = selected_test_segments or tuple(scoped.values("test_segments"))
        if not sensors or not test_segments:
            continue
        catalogs.append(
            await source.load_indicator_catalog(
                context.request,
                domain=domain,
                sensors=sensors,
                test_segments=test_segments,
            )
        )
    return _merge_catalogs(catalogs)


def _merge_catalogs(catalogs: Sequence[ObservationCatalog]) -> ObservationCatalog:
    entries = []
    for catalog in catalogs:
        entries.extend(
            catalog.distinct_entries(
                ("sensors", "test_segments", "indicator_names", "data_types")
            )
        )
    return _catalog_from_entries(entries)


def _domains_for_action(
    source: SigmaObservationCatalogSource,
    action_name: str | None,
    slot_names: Sequence[str],
) -> tuple[str, ...]:
    if action_name is None or "data_types" in slot_names:
        return ()
    return tuple(source.domains_for_action(action_name))


def _action_name_for_context(
    intent_names: Sequence[str],
    context: PlanningContext,
    action_name_by_intent: Mapping[str, str],
) -> str | None:
    action_name = _action_name_from_intents(intent_names, action_name_by_intent)
    if action_name:
        return action_name
    active = context.artifacts.get("active_task")
    if isinstance(active, Mapping):
        for key in ("action_name", "name"):
            value = active.get(key)
            if isinstance(value, str) and value:
                return value
    value = context.artifacts.get("active_task_name")
    return value if isinstance(value, str) and value else None


def _action_name_from_intents(
    intent_names: Sequence[str],
    action_name_by_intent: Mapping[str, str],
) -> str | None:
    for intent_name in intent_names:
        action_name = action_name_by_intent.get(intent_name)
        if action_name:
            return action_name
    return None


def _indicator_data_types(context: PlanningContext) -> tuple[str, ...]:
    selected = tuple(_slot_values(context.slot_state, "indicator_names"))
    if len(selected) != 1:
        return ()
    catalog = context.artifacts.get("candidate_catalog")
    if not isinstance(catalog, CandidateCatalog):
        return ()
    domains = []
    for entity in ("indicator_names", "indicator"):
        for item in catalog.candidates_for_entity(entity):
            if selected[0] not in {item.value, item.label}:
                continue
            domains.extend(_metadata_values(item.metadata, "data_type"))
    return tuple(dict.fromkeys(domains))


def _metadata_values(metadata: Mapping[str, Any], key: str) -> list[str]:
    aliases = {
        "data_type": ("data_type", "data_types", "domain", "domains"),
    }
    values = []
    for alias in aliases[key]:
        value = metadata.get(alias)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, bytes | str):
            values.extend(str(item) for item in value if item is not None)
    return values


def _slot_values(state: SlotState, slot_name: str) -> list[str]:
    for ref, value in state.values.items():
        if ref.name != slot_name:
            continue
        if isinstance(value, str):
            return [value]
        if isinstance(value, Sequence) and not isinstance(value, bytes):
            return [str(item) for item in value]
        return [str(value)]
    return []


def _pre_recognition_explicit_data_types(
    artifacts: Mapping[str, Any],
    catalog: object,
) -> tuple[str, ...]:
    if not isinstance(catalog, CandidateCatalog):
        return ()
    result = artifacts.get("pre_recognition")
    effects = getattr(result, "effects", ())
    for effect in effects or ():
        diagnostics = getattr(effect, "diagnostics", {})
        if not isinstance(diagnostics, Mapping):
            continue
        matches = diagnostics.get("observation_matches")
        if isinstance(matches, Sequence) and any(
            item in {"data_type", "data_types"} for item in matches
        ):
            return tuple(
                item.value for item in catalog.candidates_for_entity("data_types")
            )
    return ()


def _explicit_data_types_from_decision(
    decision: object,
) -> tuple[str, ...]:
    values: list[str] = []
    for intent in getattr(decision, "intents", ()) or ():
        slots = getattr(intent, "slots", None)
        if slots is None:
            continue
        entity_type = getattr(slots, "entity_type", None)
        target = getattr(slots, "target", None)
        if str(entity_type) not in {"data_type", "data_types"} or not target:
            continue
        values.extend(_target_values(target))
    return tuple(dict.fromkeys(values))


def _target_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, bytes | str):
        return [str(item) for item in value if item is not None]
    return [str(value)]
