from __future__ import annotations

from maia.api import ClarifyPlan, PlanDataset, Prompt, PromptCandidate, TaskPlan
from maia.selection.sets import SelectionSet
from maia.tasks import PendingTask, TaskSpec
from maia.tasks.data_observation_models import (
    DATA_OBSERVATION_INTENT,
    DATA_TYPE_SLOT,
    DOMAIN_LABELS,
    INDICATOR_SLOT,
    SENSOR_LIST_SLOT,
    TEST_NAME_LIST_SLOT,
    ObservationCandidates,
    ObservationResolution,
)


class DataObservationPolicy:
    def task_for_selection(
        self,
        selection: SelectionSet,
        *,
        task_id: str,
    ) -> TaskSpec:
        return TaskSpec(
            task_id=task_id,
            name=DATA_OBSERVATION_INTENT,
            title="View indicator result",
            operations=(DATA_OBSERVATION_INTENT,),
            selection_set_id=selection.selection_set_id,
            selection_hash=selection.selection_hash,
            params={},
            risk_level="low",
            requires_confirmation=False,
        )

    def task_plan(self, task: TaskSpec | PendingTask, dataset: PlanDataset) -> TaskPlan:
        return TaskPlan(
            status="ready",
            name=task.name,
            intent=DATA_OBSERVATION_INTENT,
            title=task.title,
            risk_level=task.risk_level,
            requires_confirmation=task.requires_confirmation,
            params=task.params,
            message="Observation task is ready.",
            dataset=dataset,
        )

    def blocked_plan(
        self,
        task: TaskSpec | PendingTask | None,
        reason: str,
        message: str,
        *,
        dataset: PlanDataset | None = None,
    ) -> TaskPlan:
        return TaskPlan(
            status="blocked",
            name=DATA_OBSERVATION_INTENT if task is None else task.name,
            intent=DATA_OBSERVATION_INTENT,
            title="View indicator result" if task is None else task.title,
            risk_level="low" if task is None else task.risk_level,
            requires_confirmation=False if task is None else task.requires_confirmation,
            params={} if task is None else task.params,
            message=message,
            reason=reason,
            dataset=dataset or PlanDataset(),
        )

    def clarify(
        self,
        *,
        resolution: ObservationResolution,
        dataset: PlanDataset,
    ) -> ClarifyPlan:
        slots = resolution.missing_slots or resolution.invalid_slots
        prompts = [self._prompt(slot, resolution.candidates) for slot in slots]
        return ClarifyPlan(
            reason="invalid_slots" if resolution.invalid_slots else "missing_slots",
            message="Select data observation parameters.",
            pending_task=DATA_OBSERVATION_INTENT,
            missing_slots=list(resolution.missing_slots),
            invalid_slots=list(resolution.invalid_slots),
            prompts=prompts,
            suggestions=[candidate.label for prompt in prompts for candidate in prompt.candidates],
            dataset=dataset,
        )

    def _prompt(self, slot: str, candidates: ObservationCandidates) -> Prompt:
        if slot == DATA_TYPE_SLOT:
            return Prompt(
                id=DATA_TYPE_SLOT,
                target="slot",
                label="指标域",
                message="请选择指标域。",
                required=True,
                input_type="single_select",
                candidates=[
                    PromptCandidate(value=value, label=DOMAIN_LABELS.get(value, value))
                    for value in candidates.data_types
                ],
            )
        if slot == SENSOR_LIST_SLOT:
            return Prompt(
                id=SENSOR_LIST_SLOT,
                target="slot",
                label="传感器",
                message="请选择传感器。",
                required=True,
                input_type="multi_select",
                candidates=[PromptCandidate(value=value, label=value) for value in candidates.sensors],
            )
        if slot == TEST_NAME_LIST_SLOT:
            return Prompt(
                id=TEST_NAME_LIST_SLOT,
                target="slot",
                label="测试段",
                message="请选择测试段。",
                required=True,
                input_type="multi_select",
                candidates=[PromptCandidate(value=value, label=value) for value in candidates.test_names],
            )
        return Prompt(
            id=INDICATOR_SLOT,
            target="slot",
            label="指标",
            message="请选择指标。",
            required=True,
            input_type="single_select",
            candidates=[
                PromptCandidate(value=indicator.to_param(), label=indicator.name)
                for indicator in candidates.indicators
            ],
        )


__all__ = ["DataObservationPolicy"]
