from __future__ import annotations

from types import SimpleNamespace

import pytest

from synapse.domains.observation.task_params import ObservationTaskParamProvider
from synapse.planning.planner import PlanningContext
from synapse.planning.tasks import TaskDefinition
from synapse.recognition import CandidateCatalog
from synapse.slots.contracts import SlotRef
from synapse.slots.state import SlotState
from synapse.turns import TurnRequest


DATA_TYPE = SlotRef("nvh.data_observation", "data_types")
INDICATORS = SlotRef("nvh.data_observation", "indicator_names")


def _task(name: str) -> TaskDefinition:
    return TaskDefinition(
        name=name,
        intent_names=(f"task.{name}",),
        title=name,
        risk_level="low",
        requires_confirmation=False,
        required_slots=(),
        optional_slots=(),
    )


def _context(
    *,
    state: SlotState | None = None,
    catalog: CandidateCatalog | None = None,
) -> PlanningContext:
    return PlanningContext(
        request=TurnRequest(session_id="s1", message="show"),
        decision=SimpleNamespace(action_intents=()),
        slot_state=state or SlotState(),
        artifacts={"candidate_catalog": catalog} if catalog is not None else {},
    )


def test_observation_task_param_provider_projects_single_data_type_string() -> None:
    provider = ObservationTaskParamProvider(
        data_types_by_task_name={"query_order_slice": ("TWO_D_OC",)}
    )

    params = provider.params_for(_task("query_order_slice"), _context())

    assert params == {"data_types": "TWO_D_OC"}
    assert isinstance(params["data_types"], str)


def test_observation_task_param_provider_skips_ambiguous_task_domains() -> None:
    provider = ObservationTaskParamProvider(
        data_types_by_task_name={"query_shared": ("ONE_D", "TWO_D_OC")}
    )

    params = provider.params_for(_task("query_shared"), _context())

    assert params == {}


def test_observation_task_param_provider_ignores_non_observation_tasks() -> None:
    provider = ObservationTaskParamProvider(data_types_by_task_name={})

    params = provider.params_for(_task("query_other"), _context())

    assert params == {}


def test_observation_task_param_provider_projects_indicator_objects() -> None:
    provider = ObservationTaskParamProvider(
        data_types_by_task_name={"query_one_dim_data": ("ONE_D",)}
    )
    context = _context(
        state=SlotState.from_values(
            {
                DATA_TYPE: "ONE_D",
                INDICATORS: ["RMS", "Peak", "RMS"],
            }
        ),
        catalog=CandidateCatalog.from_mapping(
            {
                "indicator_names": [
                    {
                        "value": "RMS",
                        "metadata": {
                            "indexes_by_data_type": {"ONE_D": "rms-index"}
                        },
                    },
                    {
                        "value": "Peak",
                        "metadata": {
                            "indexes_by_data_type": {"ONE_D": "peak-index"}
                        },
                    },
                ]
            }
        ),
    )

    params = provider.params_for(_task("query_one_dim_data"), context)

    assert params == {
        "data_types": "ONE_D",
        "indicator_names": [
            {"name": "RMS", "index": "rms-index"},
            {"name": "Peak", "index": "peak-index"},
        ],
    }
    assert context.slot_state.get(INDICATORS) == ["RMS", "Peak", "RMS"]


def test_observation_task_param_provider_selects_index_for_task_data_type() -> None:
    provider = ObservationTaskParamProvider(
        data_types_by_task_name={"query_order_slice": ("TWO_D_OC",)}
    )
    context = _context(
        state=SlotState.from_values({INDICATORS: ["48Order"]}),
        catalog=CandidateCatalog.from_mapping(
            {
                "indicator_names": [
                    {
                        "value": "48Order",
                        "metadata": {
                            "indexes_by_data_type": {
                                "ONE_D": "one-d-48",
                                "TWO_D_OC": "order-cut-48",
                            }
                        },
                    }
                ]
            }
        ),
    )

    params = provider.params_for(_task("query_order_slice"), context)

    assert params["indicator_names"] == [
        {"name": "48Order", "index": "order-cut-48"}
    ]


@pytest.mark.parametrize(
    ("metadata", "reason"),
    [
        ({"data_types": ("ONE_D",)}, "index is missing"),
        (
            {
                "indexes_by_data_type": {"ONE_D": "rms-a"},
                "index_conflicts_by_data_type": {
                    "ONE_D": ("rms-a", "rms-b")
                },
            },
            "indexes conflict",
        ),
    ],
)
def test_observation_task_param_provider_rejects_invalid_indicator_index(
    metadata,
    reason,
    caplog,
) -> None:
    provider = ObservationTaskParamProvider(
        data_types_by_task_name={"query_one_dim_data": ("ONE_D",)}
    )
    context = _context(
        state=SlotState.from_values({INDICATORS: ["RMS"]}),
        catalog=CandidateCatalog.from_mapping(
            {"indicator_names": [{"value": "RMS", "metadata": metadata}]}
        ),
    )

    with pytest.raises(ValueError, match=reason):
        provider.params_for(_task("query_one_dim_data"), context)

    assert "task=query_one_dim_data" in caplog.text
    assert "indicator=RMS" in caplog.text
    assert "data_type=ONE_D" in caplog.text
