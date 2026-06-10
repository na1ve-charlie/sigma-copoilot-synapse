"""Contracts for generic Synapse slot handling."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal


SlotOperationKind = Literal["replace", "add", "remove", "clear"]


@dataclass(frozen=True, slots=True)
class SlotRef:
    """Stable identifier for one domain-owned slot."""

    domain_id: str
    name: str


@dataclass(frozen=True, slots=True)
class SlotSchema:
    """Domain-owned rules, configured under each domain's slot module."""

    ref: SlotRef
    required: bool = False
    multi: bool = False
    dependencies: tuple[SlotRef, ...] = ()


@dataclass(frozen=True, slots=True)
class SlotCandidate:
    """Candidate value loaded by domain resolvers before commit."""

    ref: SlotRef
    value: Any
    confidence: float = 1.0
    source: str = ""
    display: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SlotOperation:
    """Internal normalized mutation, not Themis' public operation model."""

    ref: SlotRef
    kind: SlotOperationKind
    value: Any = None
    source: str = ""

    @classmethod
    def replace(cls, ref: SlotRef, value: Any, *, source: str = "") -> "SlotOperation":
        return cls(ref=ref, kind="replace", value=value, source=source)

    @classmethod
    def add(cls, ref: SlotRef, value: Any, *, source: str = "") -> "SlotOperation":
        return cls(ref=ref, kind="add", value=value, source=source)

    @classmethod
    def remove(cls, ref: SlotRef, value: Any, *, source: str = "") -> "SlotOperation":
        return cls(ref=ref, kind="remove", value=value, source=source)

    @classmethod
    def clear(cls, ref: SlotRef, *, source: str = "") -> "SlotOperation":
        return cls(ref=ref, kind="clear", source=source)


@dataclass(frozen=True, slots=True)
class PendingSlotBundle:
    """Uncommitted slot evidence preserved across clarify turns."""

    operations: tuple[SlotOperation, ...] = ()
    candidates: tuple[SlotCandidate, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
