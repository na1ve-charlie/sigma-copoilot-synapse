"""Helpers for loading optional Themis tree prompt config."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from themis import (
    TreePromptConfig,
    TreePromptExample,
    TreePromptExampleIntent,
    TreePromptExampleVariable,
    TreePromptIntentGroup,
    TreePromptIntentOption,
    TreePromptRenderingConfig,
)


def load_tree_prompt_config(
    *,
    config_path: str | Path,
    config: Mapping[str, Any],
    intent_config_dir: str | Path,
    intent_config_paths: Sequence[str | Path] | None,
    load_intents,
) -> TreePromptConfig | None:
    recognition = _mapping(config.get("recognition"), "recognition")
    prompt_setting = _optional_text(recognition.get("tree_prompt_path"))
    if prompt_setting is None:
        return None

    base_dir = Path(config_path).resolve().parent
    prompt_path = _resolve_path(prompt_setting, base_dir=base_dir)
    prompt_payload = _load_mapping(prompt_path)
    template = _required_text(prompt_payload, "template")

    examples_payload, examples_path = _examples_payload(prompt_payload, prompt_path)
    entries = []
    for path in _intent_paths(intent_config_dir, intent_config_paths):
        entries.extend(load_intents(path))
    known_intents = {entry.name for entry in entries}

    slot_payload = _slot_payload(config, base_dir=base_dir)
    entity_types = _entity_types(slot_payload)
    resolver_entity_types = _resolver_entity_types(slot_payload)
    intent_groups = _intent_groups(
        examples_payload.get("intent_groups"),
        path=examples_path,
        known_intents=known_intents,
    )
    examples = _examples(
        examples_payload.get("examples"),
        path=examples_path,
        known_intents=known_intents,
        entity_types=entity_types,
        intent_groups=intent_groups,
    )

    return TreePromptConfig(
        template=template,
        examples=examples,
        intent_groups=intent_groups,
        example_rendering=_rendering(prompt_payload.get("example_rendering")),
        entity_types=tuple(entity_types),
        resolver_entity_types=tuple(resolver_entity_types),
    )


def _intent_paths(
    intent_config_dir: str | Path,
    intent_config_paths: Sequence[str | Path] | None,
) -> tuple[Path, ...]:
    if intent_config_paths is not None:
        return tuple(Path(path) for path in intent_config_paths)
    return tuple(sorted(Path(intent_config_dir).glob("*.yaml")))


def _resolve_path(path: str | Path, *, base_dir: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return base_dir / candidate


def _load_mapping(path: Path) -> Mapping[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _mapping(loaded, str(path))


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = _optional_text(data.get(key))
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _examples_payload(
    payload: Mapping[str, Any],
    prompt_path: Path,
) -> tuple[Mapping[str, Any], Path]:
    examples_setting = _optional_text(payload.get("examples_path"))
    if examples_setting is None:
        return payload, prompt_path
    examples_path = _resolve_path(examples_setting, base_dir=prompt_path.parent)
    return _load_mapping(examples_path), examples_path


def _slot_payload(config: Mapping[str, Any], *, base_dir: Path) -> Mapping[str, Any]:
    slots = dict(_mapping(config.get("slots", {}), "slots"))
    includes = config.get("includes", {})
    if includes in (None, {}):
        return slots
    include_mapping = _mapping(includes, "includes")
    for pattern in _string_list(include_mapping.get("slots", []), "includes.slots"):
        for path in sorted(base_dir.glob(pattern)):
            payload = _load_mapping(path)
            slots.update(_mapping(payload.get("slots", {}), f"{path}.slots"))
    return slots


def _entity_types(slots: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for slot_name, raw_slot in slots.items():
        if not isinstance(slot_name, str) or not isinstance(raw_slot, Mapping):
            continue
        entity_type = _optional_text(raw_slot.get("entity_type")) or slot_name
        if entity_type not in result:
            result.append(entity_type)
    return result


def _resolver_entity_types(slots: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for slot_name, raw_slot in slots.items():
        if not isinstance(slot_name, str) or not isinstance(raw_slot, Mapping):
            continue
        if _optional_text(raw_slot.get("kind")) == "user":
            continue
        entity_type = _optional_text(raw_slot.get("entity_type")) or slot_name
        if entity_type not in result:
            result.append(entity_type)
    return result


def _string_list(value: Any, name: str) -> list[str]:
    if value in (None, []):
        return []
    if isinstance(value, str) or not isinstance(value, list):
        raise TypeError(f"{name} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{name} must be a list of strings")
        result.append(item)
    return result


def _intent_groups(
    raw_groups: Any,
    *,
    path: Path,
    known_intents: set[str],
) -> dict[str, TreePromptIntentGroup]:
    if raw_groups in (None, {}):
        return {}
    groups = _mapping(raw_groups, f"{path}.intent_groups")
    result: dict[str, TreePromptIntentGroup] = {}
    for group_name, raw_group in groups.items():
        options_payload = _mapping(raw_group, f"{path}.intent_groups.{group_name}")
        options: list[TreePromptIntentOption] = []
        for index, raw_option in enumerate(
            _list(options_payload.get("options"), f"{path}.intent_groups.{group_name}.options")
        ):
            option_payload = _mapping(
                raw_option,
                f"{path}.intent_groups.{group_name}.options[{index}]",
            )
            intent = _required_text(option_payload, "intent")
            if intent not in known_intents:
                raise ValueError(f"unknown prompt intent: {intent}")
            options.append(
                TreePromptIntentOption(
                    intent=intent,
                    variables=_string_mapping(option_payload.get("variables", {})),
                    keywords=tuple(
                        _string_list(
                            option_payload.get("keywords", []),
                            f"{path}.intent_groups.{group_name}.options[{index}].keywords",
                        )
                    ),
                )
            )
        result[str(group_name)] = TreePromptIntentGroup(options=tuple(options))
    return result


def _examples(
    raw_examples: Any,
    *,
    path: Path,
    known_intents: set[str],
    entity_types: Sequence[str],
    intent_groups: Mapping[str, TreePromptIntentGroup],
) -> list[TreePromptExample]:
    if raw_examples in (None, []):
        return []
    result: list[TreePromptExample] = []
    for index, raw_example in enumerate(_list(raw_examples, f"{path}.examples")):
        example_payload = _mapping(raw_example, f"{path}.examples[{index}]")
        intents_payload = _list(
            example_payload.get("intents"),
            f"{path}.examples[{index}].intents",
        )
        variables = _example_variables(
            example_payload.get("variables"),
            path=path,
            entity_types=entity_types,
        )
        intents = [
            _example_intent(
                item,
                path=path,
                known_intents=known_intents,
                variables=variables,
                intent_groups=intent_groups,
            )
            for item in intents_payload
        ]
        result.append(
            TreePromptExample(
                input=_required_text(example_payload, "input"),
                intents=intents,
                variables=variables,
            )
        )
    return result


def _example_intent(
    raw_intent: Any,
    *,
    path: Path,
    known_intents: set[str],
    variables: Mapping[str, TreePromptExampleVariable],
    intent_groups: Mapping[str, TreePromptIntentGroup],
) -> TreePromptExampleIntent:
    payload = _mapping(raw_intent, f"{path}.examples[].intents[]")
    intent = _optional_text(payload.get("intent"))
    intent_from = _optional_text(payload.get("intent_from"))
    if bool(intent) == bool(intent_from):
        raise ValueError(f"{path} example intent must declare intent or intent_from")
    if intent is not None and intent not in known_intents:
        raise ValueError(f"unknown prompt intent: {intent}")
    if intent_from is not None and intent_from not in intent_groups:
        raise ValueError(f"unknown prompt intent group: {intent_from}")
    target_from = _optional_text(payload.get("target_from"))
    if target_from is not None and target_from not in variables:
        raise ValueError(f"unknown prompt variable: {target_from}")
    return TreePromptExampleIntent(
        intent=intent,
        intent_from=intent_from,
        score=float(payload.get("score", 1.0)),
        action=_optional_text(payload.get("action")),
        entity_type=_optional_text(payload.get("entity_type")),
        target=_optional_text(payload.get("target")),
        target_from=target_from,
        slot_valid=bool(payload.get("slot_valid", True)),
    )


def _example_variables(
    raw_variables: Any,
    *,
    path: Path,
    entity_types: Sequence[str],
) -> dict[str, TreePromptExampleVariable]:
    if raw_variables in (None, {}):
        return {}
    mapping = _mapping(raw_variables, f"{path}.examples[].variables")
    result: dict[str, TreePromptExampleVariable] = {}
    for name, raw_variable in mapping.items():
        payload = _mapping(raw_variable, f"{path}.examples[].variables.{name}")
        entity_type = _required_text(payload, "entity_type")
        if entity_type not in entity_types:
            raise ValueError(f"unknown prompt entity type: {entity_type}")
        result[str(name)] = TreePromptExampleVariable(
            entity_type=entity_type,
            pick=int(payload.get("pick", 0)),
        )
    return result


def _string_mapping(value: Any) -> dict[str, str]:
    if value in (None, {}):
        return {}
    mapping = _mapping(value, "variables")
    result: dict[str, str] = {}
    for key, item in mapping.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError("variables must contain string keys and values")
        result[key] = item
    return result


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list")
    return value


def _rendering(raw_rendering: Any) -> TreePromptRenderingConfig:
    if raw_rendering in (None, {}):
        return TreePromptRenderingConfig()
    rendering = _mapping(raw_rendering, "example_rendering")
    return TreePromptRenderingConfig(
        max_examples=int(rendering.get("max_examples", 3)),
        group_selection=_optional_text(rendering.get("group_selection")) or "first",
    )
