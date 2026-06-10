"""Synapse adapter for Themis intent recognition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import yaml
from themis import (
    BusinessIntentRecognizer,
    ChatLLM,
    IntentDecision,
    IntentMatch,
    IntentSlot,
    OpenAICompatibleLLM,
    RecognitionConfig,
    ResolverProvider,
    TreePromptConfig,
    load_intents,
)

from synapse.engine import TurnContext
from synapse.planning.planner import DECISION_ARTIFACT
from synapse.planning.plans import ClarifyPlan, ReplyPlan
from synapse.recognition.candidates import CANDIDATE_CATALOG_ARTIFACT, CandidateCatalog
from synapse.recognition.tree_prompt import load_tree_prompt_config


DEFAULT_THEMIS_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "copilot.yaml"
DEFAULT_INTENT_CONFIG_DIR = (
    Path(__file__).resolve().parents[3] / "configs" / "themis" / "intents"
)


class IntentRecognizer(Protocol):
    async def recognize(
        self,
        message: str,
        *,
        resolver: ResolverProvider | None = None,
    ) -> Any:
        ...


class ThemisRecognitionStep:
    """Runs Themis recognition and handles recognition-level early responses."""

    def __init__(self, recognizer: IntentRecognizer) -> None:
        self._recognizer = recognizer

    async def run(self, context: TurnContext) -> TurnContext:
        resolver = None
        catalog = context.artifacts.get(CANDIDATE_CATALOG_ARTIFACT)
        if isinstance(catalog, CandidateCatalog):
            resolver = catalog.as_themis_resolver()
        decision = await self._recognizer.recognize(
            context.message,
            resolver=resolver,
        )
        decision = _normalize_resolver_query_decision(decision)
        context = context.with_artifact(DECISION_ARTIFACT, decision)

        verdict = _verdict_value(decision)
        if verdict == "low":
            return context.with_plan(
                ReplyPlan(message="我还没有识别出明确的业务意图。").model_dump(mode="json")
            )
        if verdict == "ambiguous":
            return context.with_plan(
                ClarifyPlan(
                    reason="ambiguous_intent",
                    message="我还不能确定你想执行哪个业务意图。",
                ).model_dump(mode="json")
            )
        return context


class LazyThemisRecognizer:
    """Defers real Themis initialization until the first recognition call."""

    def __init__(
        self,
        *,
        config_path: str | Path = DEFAULT_THEMIS_CONFIG,
        intent_config_dir: str | Path = DEFAULT_INTENT_CONFIG_DIR,
        intent_config_paths: Sequence[str | Path] | None = None,
    ) -> None:
        self._config_path = config_path
        self._intent_config_dir = intent_config_dir
        self._intent_config_paths = intent_config_paths
        self._recognizer: BusinessIntentRecognizer | None = None

    async def recognize(
        self,
        message: str,
        *,
        resolver: ResolverProvider | None = None,
    ) -> Any:
        if self._recognizer is None:
            self._recognizer = build_themis_recognizer_from_config(
                config_path=self._config_path,
                intent_config_dir=self._intent_config_dir,
                intent_config_paths=self._intent_config_paths,
            )
        decision = await self._recognizer.recognize(message, resolver=resolver)
        return _normalize_resolver_query_decision(decision)


def build_themis_recognizer(
    *,
    llm: ChatLLM,
    resolver: ResolverProvider | None = None,
    recognition_config: RecognitionConfig | None = None,
    tree_prompt: TreePromptConfig | None = None,
    intent_config_dir: str | Path = DEFAULT_INTENT_CONFIG_DIR,
    intent_config_paths: Sequence[str | Path] | None = None,
) -> BusinessIntentRecognizer:
    """Initialize Themis from Synapse runtime inputs."""

    entries = []
    for path in _intent_paths(intent_config_dir, intent_config_paths):
        entries.extend(load_intents(path))
    return BusinessIntentRecognizer(
        entries,
        llm,
        resolver=resolver,
        config=recognition_config,
        tree_prompt=tree_prompt,
    )


def build_themis_recognizer_from_config(
    *,
    config_path: str | Path = DEFAULT_THEMIS_CONFIG,
    resolver: ResolverProvider | None = None,
    intent_config_dir: str | Path = DEFAULT_INTENT_CONFIG_DIR,
    intent_config_paths: Sequence[str | Path] | None = None,
) -> BusinessIntentRecognizer:
    """Initialize Themis from the current runtime config file."""

    config = _load_mapping(config_path)
    recognition = _mapping(config.get("recognition"), "recognition")
    llm_settings = _mapping(recognition.get("llm"), "recognition.llm")
    themis_settings = recognition.get("themis", {})
    if themis_settings is None:
        themis_settings = {}
    if not isinstance(themis_settings, Mapping):
        raise TypeError("recognition.themis must be a mapping")

    llm = OpenAICompatibleLLM(
        model=_required_text(llm_settings, "model"),
        base_url=_optional_text(llm_settings.get("base_url")),
        api_key=_optional_text(llm_settings.get("api_key")),
        temperature=float(llm_settings.get("temperature", 0.1)),
        max_tokens=int(llm_settings.get("max_tokens", 800)),
        retries=int(llm_settings.get("retries", 2)),
    )
    return build_themis_recognizer(
        llm=llm,
        resolver=resolver,
        recognition_config=RecognitionConfig(**dict(themis_settings)),
        tree_prompt=load_tree_prompt_config(
            config_path=config_path,
            config=config,
            intent_config_dir=intent_config_dir,
            intent_config_paths=intent_config_paths,
            load_intents=load_intents,
        ),
        intent_config_dir=intent_config_dir,
        intent_config_paths=intent_config_paths,
    )


def _intent_paths(
    intent_config_dir: str | Path,
    intent_config_paths: Sequence[str | Path] | None,
) -> tuple[Path, ...]:
    paths = (
        tuple(Path(path) for path in intent_config_paths)
        if intent_config_paths is not None
        else tuple(sorted(Path(intent_config_dir).glob("*.yaml")))
    )
    if not paths:
        raise FileNotFoundError(
            f"no Themis intent YAML files found in: {intent_config_dir}"
        )
    return paths


def _load_mapping(path: str | Path) -> Mapping[str, Any]:
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return _mapping(loaded, str(path))


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"recognition.llm.{key} is required")
    return value


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _verdict_value(decision: Any) -> str:
    verdict = getattr(decision, "verdict", "")
    return str(getattr(verdict, "value", verdict))


def _normalize_resolver_query_decision(decision: Any) -> Any:
    if not isinstance(decision, IntentDecision):
        return decision

    intents = []
    changed = False
    for intent in decision.intents:
        if _resolver_query_slot_only(intent):
            intents.append(
                IntentMatch(
                    name=intent.name,
                    score=intent.score,
                    slots=IntentSlot(),
                )
            )
            changed = True
        elif _resolver_query_targeted(intent):
            intents.extend(
                (
                    intent,
                    IntentMatch(
                        name=intent.name,
                        score=intent.score,
                        slots=IntentSlot(),
                    ),
                )
            )
            changed = True
        else:
            intents.append(intent)

    if not changed:
        return decision
    return IntentDecision(
        verdict=decision.verdict,
        intents=tuple(intents),
        diagnostics=decision.diagnostics,
        degraded=decision.degraded,
    )


def _resolver_query_slot_only(intent: IntentMatch) -> bool:
    slots = intent.slots
    return (
        intent.name.startswith("inquiry.nvh.resolver_query.")
        and not slots.action
        and not slots.target
        and bool(slots.entity_type)
    )


def _resolver_query_targeted(intent: IntentMatch) -> bool:
    slots = intent.slots
    return (
        intent.name.startswith("inquiry.nvh.resolver_query.")
        and not slots.action
        and bool(slots.target)
    )
