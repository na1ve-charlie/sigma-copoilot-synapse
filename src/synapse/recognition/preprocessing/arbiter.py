"""Merge pre-recognition proposals into a single result."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TypeVar

from synapse.recognition.candidates import CandidateCatalog
from synapse.recognition.preprocessing.contracts import (
    DomainHint,
    PreRecognitionConflict,
    PreRecognitionContext,
    PreRecognitionEffect,
    PreRecognitionResult,
)

T = TypeVar("T")


class PreRecognitionArbiter:
    """Applies deterministic merge rules for pre-recognition effects."""

    def merge(
        self,
        context: PreRecognitionContext,
        effects: Sequence[PreRecognitionEffect],
    ) -> PreRecognitionResult:
        ordered = tuple(
            sorted(effects, key=lambda item: (item.priority, item.domain_id))
        )

        rewrites = []
        for effect in ordered:
            rewrite = effect.message_rewrite
            if rewrite is not None:
                rewrites.append((effect.domain_id, rewrite))

        messages = {rewrite.rewritten_message for _, rewrite in rewrites}
        message = next(iter(messages)) if len(messages) == 1 else context.message

        conflicts = ()
        if len(messages) > 1:
            conflicts = (
                PreRecognitionConflict(
                    kind="message_rewrite_conflict",
                    reason="multiple pre-recognition rewrites disagree",
                    domain_ids=tuple(domain_id for domain_id, _ in rewrites),
                ),
            )

        return PreRecognitionResult(
            message=message,
            effects=ordered,
            slot_candidates=_dedupe(
                candidate
                for effect in ordered
                for candidate in effect.slot_candidates
            ),
            candidate_catalog=_candidate_catalog_override(ordered),
            domain_hints=_dedupe_hints(
                hint
                for effect in ordered
                for hint in effect.domain_hints
            ),
            conflicts=conflicts,
            diagnostics={"effect_count": len(ordered)},
        )


def _dedupe(values: Iterable[T]) -> tuple[T, ...]:
    result: list[T] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _candidate_catalog_override(
    effects: Sequence[PreRecognitionEffect],
) -> CandidateCatalog | None:
    for effect in reversed(effects):
        if effect.candidate_catalog is not None:
            return effect.candidate_catalog
    return None


def _dedupe_hints(values: Iterable[DomainHint]) -> tuple[DomainHint, ...]:
    seen: set[tuple[str, str | None, str]] = set()
    result: list[DomainHint] = []
    for hint in values:
        key = (hint.domain_id, hint.evidence, hint.reason)
        if key in seen:
            continue
        seen.add(key)
        result.append(hint)
    return tuple(result)
