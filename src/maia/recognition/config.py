"""Load Maia recognition settings without coupling to recognizer construction."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict


def _application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


DEFAULT_RECOGNITION_CONFIG_PATH = (
    _application_root() / "configs" / "maia" / "runtime" / "recognition.yaml"
)


class RecognitionLLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.1
    max_tokens: int = 800
    retries: int = 2


class ThemisRecognitionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alpha: float = 0.10
    delta: float = 0.10
    min_intent_score: float = 0.50
    build_index_on_init: bool = False


class MaiaRecognitionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_path: Path
    intents_path: Path
    tree_prompt_path: Path | None = None
    report_contract_path: Path
    llm: RecognitionLLMConfig
    themis: ThemisRecognitionConfig


def load_recognition_config(
    path: str | Path = DEFAULT_RECOGNITION_CONFIG_PATH,
) -> MaiaRecognitionConfig:
    config_path = Path(path).resolve()
    payload = _load_mapping(config_path)
    recognition = _mapping(payload.get("recognition"), "recognition")
    llm_settings = _mapping(recognition.get("llm"), "recognition.llm")
    themis_settings = recognition.get("themis", {})
    if themis_settings is None:
        themis_settings = {}
    if not isinstance(themis_settings, Mapping):
        raise TypeError("recognition.themis must be a mapping")

    base_dir = config_path.parent
    llm_config = RecognitionLLMConfig(
        model=_required_text(llm_settings.get("model"), "recognition.llm.model"),
        base_url=_optional_text(llm_settings.get("base_url")),
        api_key=_optional_text(llm_settings.get("api_key")),
        temperature=float(llm_settings.get("temperature", 0.1)),
        max_tokens=int(llm_settings.get("max_tokens", 800)),
        retries=int(llm_settings.get("retries", 2)),
    )
    return MaiaRecognitionConfig(
        config_path=config_path,
        intents_path=_resolve_required_path(
            recognition.get("intents_path"),
            base_dir=base_dir,
            name="recognition.intents_path",
        ),
        tree_prompt_path=_resolve_optional_path(
            recognition.get("tree_prompt_path"),
            base_dir=base_dir,
        ),
        report_contract_path=_resolve_required_path(
            recognition.get("report_contract_path"),
            base_dir=base_dir,
            name="recognition.report_contract_path",
        ),
        llm=llm_config,
        themis=ThemisRecognitionConfig(**dict(themis_settings)),
    )


def _load_mapping(path: Path) -> Mapping[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _mapping(loaded, str(path))


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _resolve_required_path(value: Any, *, base_dir: Path, name: str) -> Path:
    return _resolve_path(_required_text(value, name), base_dir=base_dir)


def _resolve_optional_path(value: Any, *, base_dir: Path) -> Path | None:
    text = _optional_text(value)
    return None if text is None else _resolve_path(text, base_dir=base_dir)


def _resolve_path(path: str | Path, *, base_dir: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def _required_text(value: Any, name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None
