"""Compatibility wrapper for record-search prompt reply helpers."""

from maia.tasks.record_search_replies import (
    mark_pending_prompts,
    prompt_replies_allow_all_products,
    resolve_pending_prompt_reply,
)

__all__ = [
    "mark_pending_prompts",
    "prompt_replies_allow_all_products",
    "resolve_pending_prompt_reply",
]
