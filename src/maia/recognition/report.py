"""Stable RecognitionReport output model defined by the G00 contract."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


JsonScalar: TypeAlias = str | int | float | bool
JsonSequence: TypeAlias = tuple[JsonScalar, ...]


class RecognitionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    score: float
    slots: dict[str, Any] = Field(default_factory=dict)


class RecognitionActionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    score: float


class RecognitionSlotOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str | tuple[str, ...]
    score: float | tuple[float, ...]
    action: str | tuple[str, ...]
    entity_type: str
    target: JsonScalar | JsonSequence
    slot_valid: bool | tuple[bool, ...]


class RecognitionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    verdict: Literal["clear", "ambiguous", "low"]
    requires_confirmation: bool
    degraded: bool
    intents: tuple[RecognitionIntent, ...] = ()
    action_intents: tuple[RecognitionActionIntent, ...] = ()
    slot_operations: tuple[RecognitionSlotOperation, ...] = ()
    diagnostics: dict[str, Any] = Field(default_factory=dict)
