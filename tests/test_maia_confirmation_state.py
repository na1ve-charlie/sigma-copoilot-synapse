from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from maia.recognition.report import RecognitionReport
from maia.selection import SelectionLineage, SelectionSet


def test_confirmation_service_skips_pending_for_low_risk_tasks() -> None:
    from maia.tasks import ConfirmationService, TaskPreview, TaskSpecBuilder

    builder = TaskSpecBuilder(id_factory=iter(("task-1",)).__next__)
    task = builder.build(_report(actions=["task.nvh.record_search"]), _selection_set("sel-1"))
    preview = ConfirmationService().preview(task, record_count=2)

    assert isinstance(preview, TaskPreview)
    assert preview.payload == {"record_count": 2}


def test_confirmation_state_store_submits_cancels_and_expires_confirmation() -> None:
    from maia.conversation.state import ConversationSelectionState, ConversationTaskStateStore
    from maia.tasks import ConfirmationService, PendingConfirmation, TaskSpec, TaskSpecBuilder

    clock = lambda: datetime(2026, 6, 11, 9, 0, tzinfo=UTC)
    service = ConfirmationService(
        token_factory=iter(("confirm-1", "confirm-2")).__next__,
        clock=clock,
        ttl=timedelta(minutes=5),
    )
    builder = TaskSpecBuilder(id_factory=iter(("task-1",)).__next__)
    task = builder.build(_report(actions=["task.nvh.origin_data_export"]), _selection_set("sel-1"))
    preview = service.preview(task, record_count=2)

    assert isinstance(task, TaskSpec)
    assert isinstance(preview, PendingConfirmation)

    store = ConversationTaskStateStore()
    saved = store.save_confirmation(ConversationSelectionState(), preview)
    confirmed = service.confirm(
        preview,
        token="confirm-1",
        selection_hash=task.selection_hash,
    )
    submitted = store.submit(saved, confirmed.task_id)
    cancelled = store.cancel_confirmation(saved)
    expired = store.expire_confirmation(saved, selection_hash="changed")

    assert submitted.active_task_id == task.task_id
    assert submitted.pending_confirmation is None
    assert cancelled.pending_confirmation is None
    assert cancelled.pending_task is None
    assert isinstance(expired.pending_task, TaskSpec)
    assert expired.pending_task.task_id == task.task_id


def test_confirmation_service_rejects_invalid_token_stale_hash_and_expiry() -> None:
    from maia.tasks import ConfirmationError, ConfirmationService, TaskSpecBuilder

    clock = lambda: datetime(2026, 6, 11, 9, 0, tzinfo=UTC)
    service = ConfirmationService(
        token_factory=iter(("confirm-1",)).__next__,
        clock=clock,
        ttl=timedelta(minutes=5),
    )
    builder = TaskSpecBuilder(id_factory=iter(("task-1",)).__next__)
    task = builder.build(_report(actions=["task.nvh.origin_data_export"]), _selection_set("sel-1"))
    preview = service.preview(task, record_count=2)

    with pytest.raises(ConfirmationError, match="token"):
        service.confirm(preview, token="bad-token", selection_hash=task.selection_hash)
    with pytest.raises(ConfirmationError, match="selection"):
        service.confirm(preview, token="confirm-1", selection_hash="stale")
    with pytest.raises(ConfirmationError, match="expired"):
        service.confirm(
            preview,
            token="confirm-1",
            selection_hash=task.selection_hash,
            now=datetime(2026, 6, 11, 9, 6, tzinfo=UTC),
        )


def _report(
    *,
    actions: list[str] | None = None,
    operations: list[dict[str, object]] | None = None,
) -> RecognitionReport:
    return RecognitionReport(
        message="处理当前数据",
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
                "score": 0.93,
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
