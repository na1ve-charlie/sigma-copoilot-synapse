from __future__ import annotations

from maia.api import ClarifyPlan, Prompt, PromptCandidate, PromptReply
from maia.conversation.draft import SelectionDraft
from maia.recognition import RecognitionReport
from maia.runtime_slot_replies import (
    mark_pending_prompts,
    resolve_pending_prompt_reply,
)


def test_mark_pending_prompts_tracks_multiple_slot_prompts() -> None:
    draft = SelectionDraft()
    clarify = ClarifyPlan(
        reason="missing_slots",
        message="choose slots",
        missing_slots=["product_type", "config_version"],
        prompts=[
            _prompt("product_type", "A"),
            _prompt("config_version", "2"),
        ],
    )

    updated = mark_pending_prompts(draft, clarify)

    assert updated.pending_questions == ("product_type", "config_version")


def test_explicit_prompt_replies_apply_multiple_pending_slots() -> None:
    draft = SelectionDraft(pending_questions=("product_type", "config_version"))

    report = resolve_pending_prompt_reply(
        draft,
        "",
        _empty_report(),
        prompt_replies=[
            PromptReply(prompt_id="product_type", value="A"),
            PromptReply(prompt_id="config_version", value=2),
        ],
    )

    assert [(operation.entity_type, operation.target) for operation in report.slot_operations] == [
        ("product_type", "A"),
        ("config_version", "2"),
    ]


def test_plain_text_reply_does_not_guess_when_multiple_prompts_are_pending() -> None:
    draft = SelectionDraft(pending_questions=("product_type", "config_version"))
    original = _empty_report()

    report = resolve_pending_prompt_reply(draft, "A", original)

    assert report is original


def _prompt(prompt_id: str, value: str) -> Prompt:
    return Prompt(
        id=prompt_id,
        target="slot",
        label=prompt_id,
        message=f"choose {prompt_id}",
        required=True,
        input_type="single_select",
        candidates=[PromptCandidate(value=value, label=value)],
    )


def _empty_report() -> RecognitionReport:
    return RecognitionReport(
        message="",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
    )
