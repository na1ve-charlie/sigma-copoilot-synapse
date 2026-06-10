from __future__ import annotations

from dataclasses import dataclass

from synapse.domains.observation.scope import (
    ActiveTaskScopeRule,
    ExplicitDataTypeScopeRule,
    ObservationScopeContext,
    ObservationScopeDecision,
    ObservationScopePolicy,
    PendingTaskScopeRule,
    SelectedDataTypeScopeRule,
    UniqueIndicatorDataTypeScopeRule,
    build_observation_scope_context,
)
from synapse.slots.contracts import SlotRef
from synapse.slots.state import SlotState


@dataclass
class RecordingRule:
    decision: ObservationScopeDecision
    calls: int = 0

    def resolve(self, context: ObservationScopeContext) -> ObservationScopeDecision:
        self.calls += 1
        return self.decision


def test_scope_policy_runs_rules_in_registration_order_until_resolved() -> None:
    first = RecordingRule(ObservationScopeDecision.pass_through())
    second = RecordingRule(
        ObservationScopeDecision.resolved("TWO_D_FS", source="second")
    )
    third = RecordingRule(
        ObservationScopeDecision.resolved("TWO_D_CEP", source="third")
    )

    result = ObservationScopePolicy((first, second, third)).resolve(
        ObservationScopeContext()
    )

    assert result.status == "resolved"
    assert result.data_type == "TWO_D_FS"
    assert first.calls == 1
    assert second.calls == 1
    assert third.calls == 0


def test_scope_policy_returns_conflict_without_consulting_later_rules() -> None:
    first = RecordingRule(
        ObservationScopeDecision.conflict(
            source="indicator_unique",
            candidates=("TWO_D_FS", "TWO_D_CEP"),
        )
    )
    second = RecordingRule(
        ObservationScopeDecision.resolved("TWO_D_FS", source="selected_data_type")
    )

    result = ObservationScopePolicy((first, second)).resolve(ObservationScopeContext())

    assert result.status == "conflict"
    assert result.candidates == ("TWO_D_FS", "TWO_D_CEP")
    assert first.calls == 1
    assert second.calls == 0


def test_scope_policy_returns_invalid_without_consulting_later_rules() -> None:
    first = RecordingRule(
        ObservationScopeDecision.invalid(
            source="selected_data_type",
            candidates=("TWO_D_FS",),
        )
    )
    second = RecordingRule(
        ObservationScopeDecision.resolved("TWO_D_FS", source="active_task")
    )

    result = ObservationScopePolicy((first, second)).resolve(ObservationScopeContext())

    assert result.status == "invalid"
    assert result.candidates == ("TWO_D_FS",)
    assert first.calls == 1
    assert second.calls == 0


def test_explicit_data_type_rule_has_priority_over_following_sources() -> None:
    policy = ObservationScopePolicy(
        (
            ExplicitDataTypeScopeRule(),
            UniqueIndicatorDataTypeScopeRule(),
            SelectedDataTypeScopeRule(),
            PendingTaskScopeRule(lambda _: ("TWO_D_OS",)),
            ActiveTaskScopeRule(lambda _: ("TWO_D_CEP",)),
        )
    )

    result = policy.resolve(
        ObservationScopeContext(
            explicit_data_types=("TWO_D_FS",),
            indicator_data_types=("TWO_D_OC",),
            selected_data_type="TWO_D_TD",
            pending_task_name="query_order_spectrum",
            active_task_name="query_cepstrum",
            available_data_types=("TWO_D_FS", "TWO_D_TD", "TWO_D_OC"),
        )
    )

    assert result.status == "resolved"
    assert result.source == "explicit_data_type"
    assert result.data_type == "TWO_D_FS"


def test_unique_indicator_rule_resolves_single_domain() -> None:
    result = UniqueIndicatorDataTypeScopeRule().resolve(
        ObservationScopeContext(indicator_data_types=("TWO_D_OC",))
    )

    assert result.status == "resolved"
    assert result.source == "indicator_unique"
    assert result.data_type == "TWO_D_OC"


def test_selected_data_type_rule_reads_single_selected_value() -> None:
    result = SelectedDataTypeScopeRule().resolve(
        ObservationScopeContext(
            selected_data_type="TWO_D_TD",
            available_data_types=("TWO_D_TD", "TWO_D_FS"),
        )
    )

    assert result.status == "resolved"
    assert result.source == "selected_data_type"
    assert result.data_type == "TWO_D_TD"


def test_selected_data_type_rule_returns_invalid_when_value_is_unavailable() -> None:
    result = SelectedDataTypeScopeRule().resolve(
        ObservationScopeContext(
            selected_data_type="TWO_D_OC",
            available_data_types=("TWO_D_TD", "TWO_D_FS"),
        )
    )

    assert result.status == "invalid"
    assert result.source == "selected_data_type"
    assert result.candidates == ("TWO_D_TD", "TWO_D_FS")


def test_pending_task_rule_reads_task_name_from_context() -> None:
    result = PendingTaskScopeRule(lambda _: ("TWO_D_FS",)).resolve(
        ObservationScopeContext(pending_task_name="query_frequency_spectrum")
    )

    assert result.status == "resolved"
    assert result.source == "pending_task"
    assert result.data_type == "TWO_D_FS"


def test_active_task_rule_reads_task_name_from_context() -> None:
    result = ActiveTaskScopeRule(lambda _: ("TWO_D_CEP",)).resolve(
        ObservationScopeContext(active_task_name="query_cepstrum")
    )

    assert result.status == "resolved"
    assert result.source == "active_task"
    assert result.data_type == "TWO_D_CEP"


def test_selected_data_type_has_priority_over_pending_and_active_tasks() -> None:
    policy = ObservationScopePolicy(
        (
            ExplicitDataTypeScopeRule(),
            UniqueIndicatorDataTypeScopeRule(),
            SelectedDataTypeScopeRule(),
            PendingTaskScopeRule(lambda _: ("TWO_D_FS",)),
            ActiveTaskScopeRule(lambda _: ("TWO_D_CEP",)),
        )
    )

    result = policy.resolve(
        ObservationScopeContext(
            selected_data_type="TWO_D_OC",
            pending_task_name="query_frequency_spectrum",
            active_task_name="query_cepstrum",
            available_data_types=("TWO_D_OC", "TWO_D_FS", "TWO_D_CEP"),
        )
    )

    assert result.status == "resolved"
    assert result.source == "selected_data_type"
    assert result.data_type == "TWO_D_OC"


def test_unique_indicator_has_priority_over_pending_and_active_tasks() -> None:
    policy = ObservationScopePolicy(
        (
            ExplicitDataTypeScopeRule(),
            UniqueIndicatorDataTypeScopeRule(),
            SelectedDataTypeScopeRule(),
            PendingTaskScopeRule(lambda _: ("TWO_D_FS",)),
            ActiveTaskScopeRule(lambda _: ("TWO_D_CEP",)),
        )
    )

    result = policy.resolve(
        ObservationScopeContext(
            indicator_data_types=("TWO_D_OC",),
            pending_task_name="query_frequency_spectrum",
            active_task_name="query_cepstrum",
        )
    )

    assert result.status == "resolved"
    assert result.source == "indicator_unique"
    assert result.data_type == "TWO_D_OC"


def test_pending_task_has_priority_over_active_task() -> None:
    policy = ObservationScopePolicy(
        (
            ExplicitDataTypeScopeRule(),
            UniqueIndicatorDataTypeScopeRule(),
            SelectedDataTypeScopeRule(),
            PendingTaskScopeRule(lambda _: ("TWO_D_FS",)),
            ActiveTaskScopeRule(lambda _: ("TWO_D_CEP",)),
        )
    )

    result = policy.resolve(
        ObservationScopeContext(
            pending_task_name="query_frequency_spectrum",
            active_task_name="query_cepstrum",
        )
    )

    assert result.status == "resolved"
    assert result.source == "pending_task"
    assert result.data_type == "TWO_D_FS"


def test_build_scope_context_reads_selected_pending_and_active_sources() -> None:
    state = SlotState.from_values(
        {
            SlotRef("nvh.data_observation", "data_types"): "TWO_D_FS",
        }
    )

    context = build_observation_scope_context(
        slot_state=state,
        artifacts={
            "pending_task": "query_frequency_spectrum",
            "active_task": {"action_name": "query_order_slice"},
        },
        explicit_data_types=("TWO_D_OC",),
        indicator_data_types=("TWO_D_FS",),
        available_data_types=("TWO_D_FS", "TWO_D_OC"),
    )

    assert context.explicit_data_types == ("TWO_D_OC",)
    assert context.indicator_data_types == ("TWO_D_FS",)
    assert context.selected_data_type == "TWO_D_FS"
    assert context.pending_task_name == "query_frequency_spectrum"
    assert context.active_task_name == "query_order_slice"
    assert context.available_data_types == ("TWO_D_FS", "TWO_D_OC")
