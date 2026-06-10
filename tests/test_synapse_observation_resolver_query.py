from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from synapse.domains.observation.catalog import (
    ObservationCatalog,
    ObservationCatalogEntry,
)
from synapse.domains.observation.resolver_query import (
    ObservationResolverQueryResponder,
    ResolverQueryField,
)
from synapse.domains.observation.sigma_catalog import SigmaObservationCatalogSource
from synapse.integrations.sigma import SigmaCandidate, SigmaQuery
from synapse.integrations.sigma.contracts import SigmaObservationAvailabilityRow
from synapse.planning.planner import PlanningContext
from synapse.planning.tasks import TaskCatalog, TaskPlanBuilder
from synapse.slots.contracts import SlotRef
from synapse.slots.state import SlotState
from synapse.turns import TurnRequest


SENSORS = "inquiry.nvh.resolver_query.sensors"
SEGMENTS = "inquiry.nvh.resolver_query.test_segments"
INDICATORS = "inquiry.nvh.resolver_query.indicators"
TASK_FS = "task.nvh.data_observation.batch.frequency_spectrum"
UNKNOWN = "inquiry.nvh.resolver_query.unknown"
OTHER = "inquiry.nvh.resolver_query.other"
LABELS = {
    "data_types": "指标域",
    "sensors": "传感器",
    "test_segments": "测试段",
    "indicator_names": "指标",
}


def test_single_resolver_query_intent_maps_to_single_field_request() -> None:
    responder = ObservationResolverQueryResponder(entries=[])

    request = responder.request_from_intents((SENSORS,))

    assert request is not None
    assert request.slot_names == ("sensors",)


def test_multiple_resolver_query_intents_merge_into_one_request_and_reply() -> None:
    responder = ObservationResolverQueryResponder(
        entries=[
            {"sensors": "Vib1", "test_segments": "runup"},
            {"sensors": "Vib1", "test_segments": "runup"},
            {"sensors": "Vib2", "test_segments": "coast"},
        ]
    )

    request = responder.request_from_intents((SENSORS, SEGMENTS))
    plan = run(responder.build(context(SENSORS, SEGMENTS), (SENSORS, SEGMENTS)))

    assert request is not None
    assert request.slot_names == ("sensors", "test_segments")
    assert plan is not None
    assert plan.kind == "reply"
    assert plan.message == "当前可用候选如下。"
    assert plan.data == {
        "entries": [
            {"sensors": "Vib1", "test_segments": "runup"},
            {"sensors": "Vib2", "test_segments": "coast"},
        ],
        "facets": [
            facet("sensors", ["Vib1", "Vib2"]),
            facet("test_segments", ["runup", "coast"]),
        ],
    }


def test_responder_can_project_entries_from_observation_catalog() -> None:
    responder = ObservationResolverQueryResponder(
        catalog=ObservationCatalog(
            (
                ObservationCatalogEntry(sensors="Vib1", test_segments="runup"),
                ObservationCatalogEntry(sensors="Vib1", test_segments="runup"),
                ObservationCatalogEntry(sensors="Vib2", test_segments="coast"),
            )
        )
    )

    plan = run(responder.build(context(SENSORS, SEGMENTS), (SENSORS, SEGMENTS)))

    assert plan is not None
    assert plan.kind == "reply"
    assert plan.data == {
        "entries": [
            {"sensors": "Vib1", "test_segments": "runup"},
            {"sensors": "Vib2", "test_segments": "coast"},
        ],
        "facets": [
            facet("sensors", ["Vib1", "Vib2"]),
            facet("test_segments", ["runup", "coast"]),
        ],
    }


def test_duplicate_resolver_query_intents_dedupe_in_registry_order() -> None:
    responder = ObservationResolverQueryResponder(entries=[])

    request = responder.request_from_intents((SEGMENTS, SENSORS, SEGMENTS))

    assert request is not None
    assert request.slot_names == ("sensors", "test_segments")


def test_unknown_resolver_query_intent_clarifies() -> None:
    responder = ObservationResolverQueryResponder(entries=[])

    plan = run(responder.build(context(UNKNOWN), (UNKNOWN,)))

    assert plan is not None
    assert plan.kind == "clarify"
    assert plan.message == "暂不支持当前查询。"


def test_multi_view_resolver_query_conflict_clarifies() -> None:
    responder = ObservationResolverQueryResponder(
        entries=[],
        fields=[
            ResolverQueryField(SENSORS, "sensors", view_id="first"),
            ResolverQueryField(OTHER, "other", view_id="second"),
        ],
    )

    plan = run(responder.build(context(SENSORS, OTHER), (SENSORS, OTHER)))

    assert plan is not None
    assert plan.kind == "clarify"
    assert plan.message == "当前查询意图存在歧义。"


def test_multi_domain_resolver_query_conflict_clarifies() -> None:
    responder = ObservationResolverQueryResponder(
        entries=[],
        fields=[
            ResolverQueryField(SENSORS, "sensors", domain_id="first"),
            ResolverQueryField(OTHER, "other", domain_id="second"),
        ],
    )

    plan = run(responder.build(context(SENSORS, OTHER), (SENSORS, OTHER)))

    assert plan is not None
    assert plan.kind == "clarify"
    assert plan.message == "当前查询意图存在歧义。"


def test_task_builder_uses_observation_responder_through_handler_extension() -> None:
    responder = ObservationResolverQueryResponder(entries=[{"sensors": "Vib1"}])

    plan = run(
        TaskPlanBuilder(catalog(), resolver_query_handler=responder).build(
            context(SENSORS)
        )
    )

    assert plan.model_dump(mode="json") == {
        "kind": "reply",
        "message": "当前可用候选如下。",
        "data": {
            "entries": [{"sensors": "Vib1"}],
            "facets": [facet("sensors", ["Vib1"])],
        },
        "suggestions": [],
        "slot_state_diff": {"changes": []},
    }


def test_sensor_query_keeps_selected_values_in_facets() -> None:
    responder = ObservationResolverQueryResponder(
        entries=[{"sensors": "Vib1"}, {"sensors": "sensor01"}, {"sensors": "sensor02"}]
    )

    plan = run(
        responder.build(
            context(
                SENSORS,
                state=SlotState.from_values(
                    {SlotRef("nvh.data_observation", "sensors"): ["Vib1"]}
                ),
            ),
            (SENSORS,),
        )
    )

    assert plan is not None
    assert plan.kind == "reply"
    assert plan.data == {
        "entries": [
            {"sensors": "Vib1"},
            {"sensors": "sensor01"},
            {"sensors": "sensor02"},
        ],
        "facets": [
            facet("sensors", ["Vib1", "sensor01", "sensor02"], selected=["Vib1"])
        ],
    }


def test_indicator_query_filters_by_action_domain_scope() -> None:
    source = SigmaObservationCatalogSource(
        FakeObservationGateway(
            availability_rows=(
                SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
                SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib1", "runup"),
            ),
            indicators={
                ("TWO_D_FS", ("Vib1",), ("runup",)): (SigmaCandidate("Spectrum"),),
                ("TWO_D_CEP", ("Vib1",), ("runup",)): (SigmaCandidate("Cepstrum"),),
            },
            domains_by_action={"query_frequency_spectrum": ("TWO_D_FS",)},
        )
    )
    responder = ObservationResolverQueryResponder(
        catalog_source=source,
        action_name_by_intent={TASK_FS: "query_frequency_spectrum"},
    )

    plan = run(
        responder.build(
            context(
                INDICATORS,
                TASK_FS,
                state=SlotState.from_values(
                    {
                        SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
                        SlotRef("nvh.data_observation", "test_segments"): ["runup"],
                    }
                ),
            ),
            (INDICATORS, TASK_FS),
        )
    )

    assert plan is not None
    assert plan.kind == "reply"
    assert plan.data == {
        "entries": [{"indicator_names": "Spectrum"}],
        "facets": [
            indicator_facet(
                ["Spectrum"],
                candidate_domains={"Spectrum": ["TWO_D_FS"]},
            ),
        ],
    }


def test_indicator_query_clarifies_for_missing_sensor_segment_indicator_combo() -> None:
    source = SigmaObservationCatalogSource(
        FakeObservationGateway(
            availability_rows=(
                SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
            ),
            indicators={
                ("TWO_D_FS", ("Vib1",), ("runup",)): (SigmaCandidate("Spectrum"),),
            },
            domains_by_action={"query_frequency_spectrum": ("TWO_D_FS",)},
        )
    )
    responder = ObservationResolverQueryResponder(
        catalog_source=source,
        action_name_by_intent={TASK_FS: "query_frequency_spectrum"},
    )

    plan = run(
        responder.build(
            context(
                INDICATORS,
                TASK_FS,
                state=SlotState.from_values(
                    {
                        SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
                        SlotRef("nvh.data_observation", "test_segments"): ["runup"],
                        SlotRef("nvh.data_observation", "indicator_names"): ["Order"],
                    }
                ),
            ),
            (INDICATORS, TASK_FS),
        )
    )

    assert plan is not None
    assert plan.kind == "clarify"
    assert plan.message == "当前观测选择不可用。"


def test_active_task_artifact_can_scope_indicator_domain() -> None:
    source = SigmaObservationCatalogSource(
        FakeObservationGateway(
            availability_rows=(
                SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
                SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib1", "runup"),
            ),
            indicators={
                ("TWO_D_FS", ("Vib1",), ("runup",)): (SigmaCandidate("Spectrum"),),
                ("TWO_D_CEP", ("Vib1",), ("runup",)): (SigmaCandidate("Cepstrum"),),
            },
            domains_by_action={"query_frequency_spectrum": ("TWO_D_FS",)},
        )
    )
    responder = ObservationResolverQueryResponder(
        catalog_source=source,
        action_name_by_intent={TASK_FS: "query_frequency_spectrum"},
    )

    plan = run(
        responder.build(
            context(
                INDICATORS,
                state=SlotState.from_values(
                    {
                        SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
                        SlotRef("nvh.data_observation", "test_segments"): ["runup"],
                    }
                ),
                artifacts={"active_task": {"action_name": "query_frequency_spectrum"}},
            ),
            (INDICATORS,),
        )
    )

    assert plan is not None
    assert plan.model_dump(mode="json") == {
        "kind": "reply",
        "message": "当前可用候选如下。",
        "data": {
            "entries": [{"indicator_names": "Spectrum"}],
            "facets": [
                indicator_facet(
                    ["Spectrum"],
                    candidate_domains={"Spectrum": ["TWO_D_FS"]},
                ),
            ],
        },
        "suggestions": [],
        "slot_state_diff": {"changes": []},
    }


def test_pending_task_artifact_can_scope_indicator_domain() -> None:
    source = SigmaObservationCatalogSource(
        FakeObservationGateway(
            availability_rows=(
                SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
                SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib1", "runup"),
            ),
            indicators={
                ("TWO_D_FS", ("Vib1",), ("runup",)): (SigmaCandidate("Spectrum"),),
                ("TWO_D_CEP", ("Vib1",), ("runup",)): (SigmaCandidate("Cepstrum"),),
            },
            domains_by_action={"query_frequency_spectrum": ("TWO_D_FS",)},
        )
    )
    responder = ObservationResolverQueryResponder(
        catalog_source=source,
        action_name_by_intent={TASK_FS: "query_frequency_spectrum"},
    )

    plan = run(
        responder.build(
            context(
                INDICATORS,
                state=SlotState.from_values(
                    {
                        SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
                        SlotRef("nvh.data_observation", "test_segments"): ["runup"],
                    }
                ),
                artifacts={"pending_task": "query_frequency_spectrum"},
            ),
            (INDICATORS,),
        )
    )

    assert plan is not None
    assert plan.kind == "reply"
    assert plan.data == {
        "entries": [{"indicator_names": "Spectrum"}],
        "facets": [
            indicator_facet(
                ["Spectrum"],
                candidate_domains={"Spectrum": ["TWO_D_FS"]},
            ),
        ],
    }


def test_current_turn_action_scope_beats_selected_data_type_scope() -> None:
    source = SigmaObservationCatalogSource(
        FakeObservationGateway(
            availability_rows=(
                SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
                SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib1", "runup"),
            ),
            indicators={
                ("TWO_D_FS", ("Vib1",), ("runup",)): (SigmaCandidate("Spectrum"),),
                ("TWO_D_CEP", ("Vib1",), ("runup",)): (SigmaCandidate("Cepstrum"),),
            },
            domains_by_action={"query_frequency_spectrum": ("TWO_D_FS",)},
        )
    )
    responder = ObservationResolverQueryResponder(
        catalog_source=source,
        action_name_by_intent={TASK_FS: "query_frequency_spectrum"},
    )

    plan = run(
        responder.build(
            context(
                INDICATORS,
                TASK_FS,
                state=SlotState.from_values(
                    {
                        SlotRef("nvh.data_observation", "data_types"): "TWO_D_CEP",
                        SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
                        SlotRef("nvh.data_observation", "test_segments"): ["runup"],
                    }
                ),
            ),
            (INDICATORS, TASK_FS),
        )
    )

    assert plan is not None
    assert plan.kind == "reply"
    assert plan.data == {
        "entries": [{"indicator_names": "Spectrum"}],
        "facets": [
            indicator_facet(
                ["Spectrum"],
                candidate_domains={"Spectrum": ["TWO_D_FS"]},
            ),
        ],
    }


def test_indicator_query_returns_aggregated_indicators_when_scope_is_unresolved() -> None:
    source = SigmaObservationCatalogSource(
        FakeObservationGateway(
            availability_rows=(
                SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
                SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib1", "runup"),
            ),
            indicators={
                ("TWO_D_FS", ("Vib1",), ("runup",)): (
                    SigmaCandidate("Spectrum"),
                    SigmaCandidate("Shared"),
                ),
                ("TWO_D_CEP", ("Vib1",), ("runup",)): (
                    SigmaCandidate("Cepstrum"),
                    SigmaCandidate("Shared"),
                ),
            },
        )
    )
    responder = ObservationResolverQueryResponder(catalog_source=source)

    plan = run(
        responder.build(
            context(
                INDICATORS,
                state=SlotState.from_values(
                    {
                        SlotRef("nvh.data_observation", "sensors"): ["Vib1"],
                        SlotRef("nvh.data_observation", "test_segments"): ["runup"],
                    }
                ),
            ),
            (INDICATORS,),
        )
    )

    assert plan is not None
    assert plan.kind == "reply"
    assert plan.data == {
        "entries": [
            {"indicator_names": "Spectrum"},
            {"indicator_names": "Shared"},
            {"indicator_names": "Cepstrum"},
        ],
        "facets": [
            indicator_facet(
                ["Spectrum", "Shared", "Cepstrum"],
                candidate_domains={
                    "Spectrum": ["TWO_D_FS"],
                    "Shared": ["TWO_D_FS", "TWO_D_CEP"],
                    "Cepstrum": ["TWO_D_CEP"],
                },
            ),
        ],
    }


def test_indicator_query_unresolved_scope_loads_each_available_data_type_once() -> None:
    gateway = FakeObservationGateway(
        availability_rows=(
            SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
            SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib2", "coast"),
        ),
        indicators={
            ("TWO_D_FS", ("Vib1",), ("runup",)): (SigmaCandidate("Spectrum"),),
            ("TWO_D_CEP", ("Vib2",), ("coast",)): (SigmaCandidate("Cepstrum"),),
        },
    )
    responder = ObservationResolverQueryResponder(
        catalog_source=SigmaObservationCatalogSource(gateway)
    )

    plan = run(
        responder.build(
            context(INDICATORS),
            (INDICATORS,),
        )
    )

    assert plan is not None
    assert plan.kind == "reply"
    assert gateway.indicator_calls == [
        IndicatorCall("TWO_D_FS", ("Vib1",), ("runup",)),
        IndicatorCall("TWO_D_CEP", ("Vib2",), ("coast",)),
    ]


def test_multi_field_indicator_query_keeps_entries_and_enriched_facets_shape() -> None:
    source = SigmaObservationCatalogSource(
        FakeObservationGateway(
            availability_rows=(
                SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
                SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib1", "runup"),
            ),
            indicators={
                ("TWO_D_FS", ("Vib1",), ("runup",)): (SigmaCandidate("Spectrum"),),
                ("TWO_D_CEP", ("Vib1",), ("runup",)): (SigmaCandidate("Cepstrum"),),
            },
        )
    )
    responder = ObservationResolverQueryResponder(catalog_source=source)

    plan = run(
        responder.build(
            context(SENSORS, SEGMENTS, INDICATORS),
            (SENSORS, SEGMENTS, INDICATORS),
        )
    )

    assert plan is not None
    assert plan.kind == "reply"
    assert plan.message == "当前可用候选如下。"
    assert set(plan.data) == {"entries", "facets"}
    assert all(
        set(entry).issubset({"sensors", "test_segments", "indicator_names"})
        for entry in plan.data["entries"]
    )
    assert plan.data["facets"] == [
        facet("sensors", ["Vib1"]),
        facet("test_segments", ["runup"]),
        indicator_facet(
            ["Spectrum", "Cepstrum"],
            candidate_domains={
                "Spectrum": ["TWO_D_FS"],
                "Cepstrum": ["TWO_D_CEP"],
            },
        ),
    ]


def test_sensor_query_dedupes_from_availability_source() -> None:
    responder = ObservationResolverQueryResponder(
        catalog_source=SigmaObservationCatalogSource(
            FakeObservationGateway(
                availability_rows=(
                    SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
                    SigmaObservationAvailabilityRow("TWO_D_CEP", "Vib1", "coast"),
                    SigmaObservationAvailabilityRow("TWO_D_FS", "Vib2", "runup"),
                )
            )
        )
    )

    plan = run(responder.build(context(SENSORS), (SENSORS,)))

    assert plan is not None
    assert plan.kind == "reply"
    assert plan.data == {
        "entries": [{"sensors": "Vib1"}, {"sensors": "Vib2"}],
        "facets": [facet("sensors", ["Vib1", "Vib2"])],
    }


def test_test_segment_query_dedupes_from_availability_source_with_sensor_scope() -> None:
    responder = ObservationResolverQueryResponder(
        catalog_source=SigmaObservationCatalogSource(
            FakeObservationGateway(
                availability_rows=(
                    SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "runup"),
                    SigmaObservationAvailabilityRow("TWO_D_FS", "Vib1", "coast"),
                    SigmaObservationAvailabilityRow("TWO_D_FS", "Vib2", "coast"),
                )
            )
        )
    )

    plan = run(
        responder.build(
            context(
                SEGMENTS,
                state=SlotState.from_values(
                    {SlotRef("nvh.data_observation", "sensors"): ["Vib1"]}
                ),
            ),
            (SEGMENTS,),
        )
    )

    assert plan is not None
    assert plan.kind == "reply"
    assert plan.data == {
        "entries": [{"test_segments": "runup"}, {"test_segments": "coast"}],
        "facets": [
            facet("test_segments", ["runup", "coast"]),
        ],
    }


def test_empty_catalog_returns_empty_entries_and_facets() -> None:
    responder = ObservationResolverQueryResponder(entries=[])

    plan = run(responder.build(context(SENSORS), (SENSORS,)))

    assert plan is not None
    assert plan.kind == "reply"
    assert plan.message == "当前可用候选如下。"
    assert plan.data == {"entries": [], "facets": []}


def context(
    *intent_names: str,
    state: SlotState | None = None,
    artifacts: dict[str, object] | None = None,
) -> PlanningContext:
    return PlanningContext(
        request=TurnRequest(session_id="s1", message="query"),
        decision=SimpleNamespace(
            action_intents=({"name": name} for name in intent_names)
        ),
        slot_state=state or SlotState(),
        artifacts=artifacts or {},
    )


def run(coro):
    return asyncio.run(coro)


def catalog() -> TaskCatalog:
    return TaskCatalog.from_mapping({})


def facet(
    slot_name: str,
    candidates: list[str],
    *,
    selected: list[str] | None = None,
) -> dict[str, object]:
    return {
        "slot_name": slot_name,
        "label": LABELS[slot_name],
        "candidates": candidates,
        "selected": selected or [],
    }


def indicator_facet(
    candidates: list[str],
    *,
    selected: list[str] | None = None,
    candidate_domains: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    return {
        **facet("indicator_names", candidates, selected=selected),
        "candidate_domains": candidate_domains or {},
    }


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
        indicators: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]],
            tuple[SigmaCandidate, ...],
        ] | None = None,
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
