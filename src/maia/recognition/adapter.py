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
from maia.recognition.normalization import normalize_slot_value_with_time_range_extractor
from maia.recognition.report import RecognitionReport
from maia.recognition.summary_result_filling import fill_summary_result_slots
from maia.recognition.time_range import LLMTimeRangeExtractor, TimeRangeExtractor
from maia.recognition.tree_prompt_loader import load_tree_prompt_config


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

    def __init__(
        self,
        recognizer: IntentRecognizer,
        *,
        time_range_extractor: TimeRangeExtractor | None = None,
    ) -> None:
        self._recognizer = recognizer
        self._time_range_extractor = time_range_extractor

    async def recognize(
        self,
        message: str,
        *,
        resolver: ResolverProvider | None = None,
        include_diagnostics: bool = False,
    ) -> RecognitionReport:
        decision = await self._recognizer.recognize(message, resolver=resolver)
        payload = decision.to_dict()
        time_range_cache: dict[str, tuple[Any, bool]] = {}
        report = RecognitionReport(
            message=message,
            verdict=_verdict_value(decision),
            requires_confirmation=bool(decision.requires_confirmation),
            degraded=bool(decision.degraded),
            intents=tuple(
                [
                    await _intent_payload(
                        intent,
                        message=message,
                        time_range_extractor=self._time_range_extractor,
                        time_range_cache=time_range_cache,
                    )
                    for intent in decision.intents
                ]
            ),
            action_intents=tuple(
                {"name": intent.name, "score": float(intent.score)}
                for intent in decision.action_intents
            ),
            slot_operations=tuple(
                [
                    await _slot_operation_payload(
                        operation,
                        message=message,
                        time_range_extractor=self._time_range_extractor,
                        time_range_cache=time_range_cache,
                    )
                    for operation in decision.slot_operations
                ]
            ),
            diagnostics=payload.get("diagnostics", {}) if include_diagnostics else {},
        )
        return fill_summary_result_slots(report)


def build_maia_recognizer_from_config(
    *,
    config_path: str | Path = DEFAULT_RECOGNITION_CONFIG_PATH,
    llm: ChatLLM | None = None,
    resolver: ResolverProvider | None = None,
    intent_paths: Sequence[str | Path] | None = None,
) -> MaiaRecognizer:
    config = load_recognition_config(config_path)
    llm_client = llm or _build_llm_client(config)
    recognizer = build_themis_recognizer(
        config=config,
        llm=llm_client,
        resolver=resolver,
        intent_paths=intent_paths,
    )
    return MaiaRecognizer(
        recognizer,
        time_range_extractor=_build_time_range_extractor(llm_client),
    )


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
        tree_prompt=load_tree_prompt_config(config),
    )


def _build_time_range_extractor(llm: ChatLLM) -> TimeRangeExtractor:
    return LLMTimeRangeExtractor(llm)


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


def _build_llm_client(config: MaiaRecognitionConfig) -> ChatLLM:
    return OpenAICompatibleLLM(
        model=config.llm.model,
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        retries=config.llm.retries,
    )


def _verdict_value(decision: Any) -> str:
    verdict = getattr(decision, "verdict", "")
    return str(getattr(verdict, "value", verdict))


async def _intent_payload(
    intent: Any,
    *,
    message: str,
    time_range_extractor: TimeRangeExtractor | None,
    time_range_cache: dict[str, tuple[Any, bool]],
) -> dict[str, Any]:
    return {
        "name": intent.name,
        "score": float(intent.score),
        "slots": await _slot_payload(
            intent.slots,
            message=message,
            time_range_extractor=time_range_extractor,
            time_range_cache=time_range_cache,
        ),
    }


async def _slot_payload(
    slot: Any,
    *,
    message: str,
    time_range_extractor: TimeRangeExtractor | None,
    time_range_cache: dict[str, tuple[Any, bool]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if getattr(slot, "action", ""):
        payload["action"] = slot.action
    entity_type = getattr(slot, "entity_type", "")
    if entity_type:
        payload["entity_type"] = entity_type
    target, slot_valid = await _normalized_slot_value(
        entity_type,
        getattr(slot, "target", ""),
        getattr(slot, "slot_valid", True),
        message=message,
        time_range_extractor=time_range_extractor,
        time_range_cache=time_range_cache,
    )
    if getattr(slot, "target", ""):
        payload["target"] = target
    if payload or not bool(slot_valid):
        payload["slot_valid"] = bool(slot_valid)
    return payload


async def _slot_operation_payload(
    operation: Any,
    *,
    message: str,
    time_range_extractor: TimeRangeExtractor | None,
    time_range_cache: dict[str, tuple[Any, bool]],
) -> Mapping[str, Any]:
    target, slot_valid = await _normalized_slot_value(
        operation.entity_type,
        _sequence_value(operation.target),
        _sequence_value(operation.slot_valid),
        message=message,
        time_range_extractor=time_range_extractor,
        time_range_cache=time_range_cache,
    )
    return {
        "intent": _sequence_value(operation.intent),
        "score": _sequence_value(operation.score),
        "action": _sequence_value(operation.action),
        "entity_type": operation.entity_type,
        "target": target,
        "slot_valid": slot_valid,
    }


def _sequence_value(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(value)
    return value


async def _normalized_slot_value(
    entity_type: str,
    target: Any,
    slot_valid: Any,
    *,
    message: str,
    time_range_extractor: TimeRangeExtractor | None,
    time_range_cache: dict[str, tuple[Any, bool]],
) -> tuple[Any, Any]:
    return await normalize_slot_value_with_time_range_extractor(
        entity_type,
        target,
        slot_valid,
        message=message,
        time_range_extractor=time_range_extractor,
        time_range_cache=time_range_cache,
    )
