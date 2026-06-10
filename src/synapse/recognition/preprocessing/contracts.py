"""Contracts shared by pre-recognition processors and the conductor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from synapse.recognition.candidates import CandidateCatalog
from synapse.turns import TurnRequest


@dataclass(frozen=True, slots=True)
class PreRecognitionContext:
    """Input snapshot for pre-recognition processors."""

    request: TurnRequest
    message: str
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_request(cls, request: TurnRequest) -> "PreRecognitionContext":
        return cls(request=request, message=request.message)

    def with_message(self, message: str) -> "PreRecognitionContext":
        return replace(self, message=message)


@dataclass(frozen=True, slots=True)
class MessageRewriteProposal:
    """Proposal to rewrite text before intent recognition."""

    rewritten_message: str
    confidence: float
    reason: str
    purpose: Literal["routing"] = "routing"


@dataclass(frozen=True, slots=True)
class DomainHint:
    """Lightweight domain signal discovered before formal recognition."""

    domain_id: str
    confidence: float
    reason: str
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class PreRecognitionConflict:
    """Conflict detected while merging pre-recognition proposals."""

    kind: str
    reason: str
    domain_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreRecognitionEffect:
    """Single processor's proposed pre-recognition changes."""

    domain_id: str
    priority: int = 100
    message_rewrite: MessageRewriteProposal | None = None
    slot_candidates: tuple[Any, ...] = ()
    candidate_catalog: CandidateCatalog | None = None
    domain_hints: tuple[DomainHint, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreRecognitionResult:
    """Merged result consumed by the later turn pipeline."""

    message: str
    effects: tuple[PreRecognitionEffect, ...] = ()
    slot_candidates: tuple[Any, ...] = ()
    candidate_catalog: CandidateCatalog | None = None
    domain_hints: tuple[DomainHint, ...] = ()
    conflicts: tuple[PreRecognitionConflict, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


class DomainPreProcessor(Protocol):
    """Domain or global preprocessing unit."""

    domain_id: str
    priority: int

    def matches(self, context: PreRecognitionContext) -> bool:
        ...

    def propose(self, context: PreRecognitionContext) -> PreRecognitionEffect:
        ...


PreRecognitionProcessor = DomainPreProcessor
