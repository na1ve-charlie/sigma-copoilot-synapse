from __future__ import annotations

from datetime import UTC, datetime

import pytest

from maia.recognition.report import RecognitionReport
from maia.selection import SelectionLineage, SelectionSet


def test_task_spec_builder_returns_medium_risk_origin_export_task() -> None:
    from maia.tasks import TaskSpec, TaskSpecBuilder

    builder = TaskSpecBuilder(id_factory=iter(("task-1",)).__next__)

    task = builder.build(_report(actions=["task.nvh.origin_data_export"]), _selection_set("sel-1"))

    assert isinstance(task, TaskSpec)
    assert task.name == "task.nvh.origin_data_export"
    assert task.params == {}
    assert task.selection_set_id == "sel-1"
    assert task.risk_level == "medium"
    assert task.requires_confirmation is True


def test_task_spec_builder_escalates_composite_risk() -> None:
    from maia.tasks import TaskSpec, TaskSpecBuilder

    builder = TaskSpecBuilder(id_factory=iter(("task-1",)).__next__)
    task = builder.build(
        _report(actions=["task.nvh.origin_data_export", "task.nvh.data_delete"]),
        _selection_set("sel-1"),
    )

    assert isinstance(task, TaskSpec)
    assert task.operations == ("task.nvh.origin_data_export", "task.nvh.data_delete")
    assert task.risk_level == "high"
    assert task.requires_confirmation is True


def test_task_spec_builder_rejects_unknown_action_intent() -> None:
    from maia.tasks import TaskSpecBuilder

    builder = TaskSpecBuilder(id_factory=iter(("task-1",)).__next__)

    with pytest.raises(LookupError, match="unsupported operation"):
        builder.build(_report(actions=["task.nvh.unknown"]), _selection_set("sel-1"))


def _report(
    *,
    actions: list[str] | None = None,
    operations: list[dict[str, object]] | None = None,
) -> RecognitionReport:
    return RecognitionReport(
        message="导出这些数据",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        action_intents=[
            {"name": name, "score": 0.95}
            for name in actions or []
        ],
        slot_operations=[
            {
                "intent": "task.nvh.selection.set_data_kind",
                "score": 0.92,
                "slot_valid": True,
                **operation,
            }
            for operation in operations or []
        ],
    )


def _selection_set(selection_set_id: str) -> SelectionSet:
    return SelectionSet(
        selection_set_id=selection_set_id,
        expression={"kind": "predicate", "name": "product_type_in", "params": {"values": ["A"]}},
        record_count=2,
        record_ids=("r-1", "r-2"),
        source_version="sigma-fixture-v1",
        created_at=datetime(2026, 6, 11, 9, 0, tzinfo=UTC),
        lineage=SelectionLineage(operation="create"),
    )
