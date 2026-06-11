"""Thin adapter from Themis public decisions to the Maia recognition report."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from themis import (
    BusinessIntentRecognizer,
    ChatLLM,
    OpenAICompatibleLLM,
    RecognitionConfig,
    ResolverProvider,
    load_intents,
)

from maia.recognition.config import (
    DEFAULT_RECOGNITION_CONFIG_PATH,
    MaiaRecognitionConfig,
    load_recognition_config,
)
from maia.recognition.report import RecognitionReport


class IntentRecognizer(Protocol):
    async def recognize(
        self,
        message: str,
        *,
        resolver: ResolverProvider | None = None,
    ) -> Any:
        ...


class MaiaRecognizer:
    """Wraps a Themis recognizer and emits the stable G00 RecognitionReport."""

    def __init__(self, recognizer: IntentRecognizer) -> None:
        self._recognizer = recognizer

    async def recognize(
        self,
        message: str,
        *,
        resolver: ResolverProvider | None = None,
        include_diagnostics: bool = False,
    ) -> RecognitionReport:
        decision = await self._recognizer.recognize(message, resolver=resolver)
        payload = decision.to_dict()
        return RecognitionReport(
            message=message,
            verdict=_verdict_value(decision),
            requires_confirmation=bool(decision.requires_confirmation),
            degraded=bool(decision.degraded),
            intents=tuple(_intent_payload(intent) for intent in decision.intents),
            action_intents=tuple(
                {"name": intent.name, "score": float(intent.score)}
                for intent in decision.action_intents
            ),
            slot_operations=tuple(
                _slot_operation_payload(operation)
                for operation in decision.slot_operations
            ),
            diagnostics=payload.get("diagnostics", {}) if include_diagnostics else {},
        )


def build_maia_recognizer_from_config(
    *,
    config_path: str | Path = DEFAULT_RECOGNITION_CONFIG_PATH,
    llm: ChatLLM | None = None,
    resolver: ResolverProvider | None = None,
    intent_paths: Sequence[str | Path] | None = None,
) -> MaiaRecognizer:
    config = load_recognition_config(config_path)
    recognizer = build_themis_recognizer(
        config=config,
        llm=llm,
        resolver=resolver,
        intent_paths=intent_paths,
    )
    return MaiaRecognizer(recognizer)


def build_themis_recognizer(
    *,
    config: MaiaRecognitionConfig,
    llm: ChatLLM | None = None,
    resolver: ResolverProvider | None = None,
    intent_paths: Sequence[str | Path] | None = None,
) -> BusinessIntentRecognizer:
    entries = []
    for path in _intent_paths(config.intents_path, intent_paths):
        entries.extend(load_intents(path))

    llm_client = llm or OpenAICompatibleLLM(
        model=config.llm.model,
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        retries=config.llm.retries,
    )
    return BusinessIntentRecognizer(
        entries,
        llm_client,
        resolver=resolver,
        config=RecognitionConfig(**config.themis.model_dump(mode="python")),
    )


def _intent_paths(
    intents_path: Path,
    intent_paths: Sequence[str | Path] | None,
) -> tuple[Path, ...]:
    if intent_paths is not None:
        paths = tuple(Path(path) for path in intent_paths)
    elif intents_path.is_file():
        paths = (intents_path,)
    else:
        paths = tuple(sorted(intents_path.glob("*.yaml")))
    if not paths:
        raise FileNotFoundError(f"no Maia intent YAML files found in: {intents_path}")
    return paths


def _verdict_value(decision: Any) -> str:
    verdict = getattr(decision, "verdict", "")
    return str(getattr(verdict, "value", verdict))


def _intent_payload(intent: Any) -> dict[str, Any]:
    return {
        "name": intent.name,
        "score": float(intent.score),
        "slots": _slot_payload(intent.slots),
    }


def _slot_payload(slot: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if getattr(slot, "action", ""):
        payload["action"] = slot.action
    if getattr(slot, "entity_type", ""):
        payload["entity_type"] = slot.entity_type
    if getattr(slot, "target", ""):
        payload["target"] = slot.target
    if payload or not bool(getattr(slot, "slot_valid", True)):
        payload["slot_valid"] = bool(getattr(slot, "slot_valid", True))
    return payload


def _slot_operation_payload(operation: Any) -> Mapping[str, Any]:
    return {
        "intent": _sequence_value(operation.intent),
        "score": _sequence_value(operation.score),
        "action": _sequence_value(operation.action),
        "entity_type": operation.entity_type,
        "target": _sequence_value(operation.target),
        "slot_valid": _sequence_value(operation.slot_valid),
    }


def _sequence_value(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(value)
    return value
