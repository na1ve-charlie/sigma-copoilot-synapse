from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from maia.api import ClarifyPlan, PromptReply
from maia.conversation.draft import SelectionDraft
from maia.recognition import RecognitionReport

_RECORD_SEARCH_INTENT = "task.nvh.record_search"
_SLOT_REPLY_ACTIONS = frozenset(
    {
        "archive_status",
        "artifact_availability",
        "config_version",
        "data_kind",
        "indicator",
        "manual_tagging",
        "product_type",
        "repeat_serial",
        "remark",
        "sensor",
        "serial_number",
        "status",
        "summary_result",
        "test_section",
        "test_segment",
        "time_range",
        "type_system",
    }
)
_ALL_PRODUCTS_ALIASES = frozenset(
    {
        "__ALL_PRODUCTS__",
        "\u5168\u90e8\u4ea7\u54c1",
        "\u6240\u6709\u4ea7\u54c1",
        "\u4ea7\u54c1\u4e0d\u9650",
        "\u578b\u53f7\u4e0d\u9650",
        "\u5168\u90e8\u578b\u53f7",
    }
)


def mark_pending_prompts(draft: SelectionDraft, clarify: ClarifyPlan) -> SelectionDraft:
    slots = _clarify_slots(clarify)
    if not slots or draft.pending_questions == slots:
        return draft
    return draft.model_copy(update={"pending_questions": slots})


def prompt_replies_allow_all_products(prompt_replies: Sequence[PromptReply]) -> bool:
    return any(
        reply.prompt_id == "product_type" and _is_all_products_value(reply.value)
        for reply in prompt_replies
    )


def resolve_pending_prompt_reply(
    draft: SelectionDraft | None,
    message: str,
    report: RecognitionReport,
    *,
    prompt_replies: Sequence[PromptReply] = (),
) -> RecognitionReport:
    if prompt_replies:
        return _report_from_prompt_replies(draft, message, prompt_replies)
    return report


def _report_from_prompt_replies(
    draft: SelectionDraft | None,
    message: str,
    prompt_replies: Sequence[PromptReply],
) -> RecognitionReport:
    pending = set(_pending_slots(draft))
    if not pending:
        raise ValueError("prompt replies require a pending clarify prompt")

    operations: list[dict[str, object]] = []
    for reply in prompt_replies:
        slot = reply.prompt_id
        if slot not in pending:
            raise ValueError(f"prompt reply is not pending: {slot}")
        if slot not in _SLOT_REPLY_ACTIONS:
            raise ValueError(f"prompt reply is not supported: {slot}")
        values = _reply_values(slot, reply.value)
        if slot == "product_type" and all(_is_all_products_value(value) for value in values):
            continue
        operations.append(_slot_operation(slot, values))

    return _reply_report(message, *operations)


def _clarify_slots(clarify: ClarifyPlan) -> tuple[str, ...]:
    slots: list[str] = []
    for prompt in clarify.prompts:
        if prompt.target == "slot" and prompt.id in _SLOT_REPLY_ACTIONS:
            slots.append(prompt.id)
    for slot in (*clarify.missing_slots, *clarify.invalid_slots):
        if slot in _SLOT_REPLY_ACTIONS and slot not in slots:
            slots.append(slot)
    return tuple(slots)


def _pending_slots(draft: SelectionDraft | None) -> tuple[str, ...]:
    if draft is None:
        return ()
    return tuple(slot for slot in draft.pending_questions if slot in _SLOT_REPLY_ACTIONS)


def _reply_report(
    message: str,
    *slot_operations: dict[str, object],
    action_intents: tuple[object, ...] = (),
) -> RecognitionReport:
    return RecognitionReport(
        message=message,
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        action_intents=action_intents
        or ({"name": _RECORD_SEARCH_INTENT, "score": 1.0},),
        slot_operations=slot_operations,
    )


def _slot_operation(slot: str, values: tuple[str, ...]) -> dict[str, object]:
    return {
        "intent": f"task.nvh.selection.set_{slot}",
        "score": 1.0,
        "action": "replace",
        "entity_type": slot,
        "target": values[0] if len(values) == 1 else values,
        "slot_valid": True,
    }


def _reply_values(slot: str, value: Any) -> tuple[str, ...]:
    raw_values = value if isinstance(value, (list, tuple)) else (value,)
    values = tuple(str(item).strip() for item in raw_values if str(item).strip())
    if not values:
        raise ValueError(f"prompt reply value is required: {slot}")
    return values


def _is_all_products_value(value: Any) -> bool:
    return str(value).strip() in _ALL_PRODUCTS_ALIASES


__all__ = [
    "mark_pending_prompts",
    "prompt_replies_allow_all_products",
    "resolve_pending_prompt_reply",
]
