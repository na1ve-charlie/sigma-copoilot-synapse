"""Load Maia tree prompt config through Themis public prompt models."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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

from maia.recognition.config import MaiaRecognitionConfig


def load_tree_prompt_config(
    config: MaiaRecognitionConfig,
) -> TreePromptConfig | None:
    if config.tree_prompt_path is None:
        return None

    prompt_path = config.tree_prompt_path
    prompt_payload = _load_mapping(prompt_path)
    examples_payload = _load_examples_payload(prompt_path, prompt_payload)
    base_examples = _load_examples(examples_payload.get("examples"))
    record_search_examples, calibration_entity_types = _load_calibration_examples(
        config.config_path.parent / "calibration_cases.yaml"
    )
    examples = [*record_search_examples, *base_examples]

    return TreePromptConfig(
        template=_required_text(prompt_payload.get("template"), "template"),
        examples=examples,
        intent_groups=_load_intent_groups(examples_payload.get("intent_groups")),
        example_rendering=_load_rendering(prompt_payload.get("example_rendering")),
        entity_types=_ordered_values(
            [
                *calibration_entity_types,
                *_example_entity_types(base_examples),
            ]
        ),
        resolver_entity_types=_ordered_values(
            variable.entity_type
            for example in base_examples
            for variable in example.variables.values()
        ),
    )


def _load_examples_payload(
    prompt_path: Path,
    prompt_payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    examples_path = _optional_path(
        prompt_payload.get("examples_path"),
        base_dir=prompt_path.parent,
    )
    return {} if examples_path is None else _load_mapping(examples_path)


def _load_calibration_examples(
    path: Path,
) -> tuple[list[TreePromptExample], tuple[str, ...]]:
    if not path.is_file():
        return [], ()

    payload = _load_mapping(path)
    entity_types: list[str] = []
    examples: list[TreePromptExample] = []
    for case in _mapping_sequence(payload.get("cases")):
        expected_names = _expected_names(case.get("expected"))
        slots = _case_slots(case.get("slots"), len(expected_names))
        entity_types.extend(_slot_entity_types(slots))
        if not any(name.endswith(".record_search") for name in expected_names):
            continue
        examples.append(
            TreePromptExample(
                input=_required_text(case.get("message"), "cases[].message"),
                intents=[
                    TreePromptExampleIntent(
                        intent=intent_name,
                        action=_optional_text(slot.get("action")),
                        entity_type=_optional_text(slot.get("entity_type")),
                        target=_optional_text(slot.get("target")),
                        slot_valid=bool(slot.get("slot_valid", True)),
                    )
                    for intent_name, slot in zip(expected_names, slots, strict=False)
                ],
            )
        )

    return examples, _ordered_values(entity_types)


def _load_examples(value: Any) -> list[TreePromptExample]:
    examples: list[TreePromptExample] = []
    for item in _mapping_sequence(value):
        examples.append(
            TreePromptExample(
                input=_required_text(item.get("input"), "examples[].input"),
                intents=_load_example_intents(item.get("intents")),
                variables=_load_example_variables(item.get("variables")),
            )
        )
    return examples


def _load_example_intents(value: Any) -> list[TreePromptExampleIntent]:
    intents: list[TreePromptExampleIntent] = []
    for item in _mapping_sequence(value):
        intents.append(
            TreePromptExampleIntent(
                intent=_optional_text(item.get("intent")),
                intent_from=_optional_text(item.get("intent_from")),
                score=float(item.get("score", 1.0)),
                action=_optional_text(item.get("action")),
                entity_type=_optional_text(item.get("entity_type")),
                target=_optional_text(item.get("target")),
                target_from=_optional_text(item.get("target_from")),
                slot_valid=bool(item.get("slot_valid", True)),
            )
        )
    return intents


def _load_example_variables(
    value: Any,
) -> dict[str, TreePromptExampleVariable]:
    variables: dict[str, TreePromptExampleVariable] = {}
    for name, item in _mapping(value).items():
        item_mapping = _mapping(item)
        variables[str(name)] = TreePromptExampleVariable(
            entity_type=_required_text(
                item_mapping.get("entity_type"),
                f"examples[].variables.{name}.entity_type",
            ),
            pick=int(item_mapping.get("pick", 0)),
        )
    return variables


def _load_intent_groups(value: Any) -> dict[str, TreePromptIntentGroup]:
    groups: dict[str, TreePromptIntentGroup] = {}
    for name, item in _mapping(value).items():
        item_mapping = _mapping(item)
        options = []
        for option in _mapping_sequence(item_mapping.get("options")):
            options.append(
                TreePromptIntentOption(
                    intent=_required_text(option.get("intent"), f"intent_groups.{name}.intent"),
                    variables={str(key): str(raw) for key, raw in _mapping(option.get("variables")).items()},
                    keywords=tuple(_text_sequence(option.get("keywords"))),
                )
            )
        groups[str(name)] = TreePromptIntentGroup(options=tuple(options))
    return groups


def _load_rendering(value: Any) -> TreePromptRenderingConfig:
    mapping = _mapping(value)
    return TreePromptRenderingConfig(
        max_examples=int(mapping.get("max_examples", 3)),
        group_selection=_optional_text(mapping.get("group_selection")) or "first",
    )


def _example_entity_types(examples: Iterable[TreePromptExample]) -> tuple[str, ...]:
    values: list[str] = []
    for example in examples:
        values.extend(variable.entity_type for variable in example.variables.values())
        values.extend(intent.entity_type for intent in example.intents if intent.entity_type)
    return _ordered_values(values)


def _expected_names(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (_required_text(value, "cases[].expected"),)
    return tuple(_required_text(item, "cases[].expected[]") for item in _sequence(value))


def _case_slots(value: Any, count: int) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        return (_mapping(value),)

    slots = tuple(_mapping(item) for item in _sequence(value))
    if len(slots) >= count:
        return slots[:count]
    return (*slots, *({} for _ in range(count - len(slots))))


def _slot_entity_types(slots: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    return _ordered_values(_optional_text(slot.get("entity_type")) for slot in slots)


def _load_mapping(path: Path) -> Mapping[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _mapping(loaded)


def _mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("tree prompt config sections must be mappings")
    return value


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    return tuple(_mapping(item) for item in _sequence(value))


def _sequence(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError("tree prompt config lists must be arrays")
    return tuple(value)


def _text_sequence(value: Any) -> tuple[str, ...]:
    return tuple(_required_text(item, "keywords[]") for item in _sequence(value))


def _ordered_values(values: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _optional_text(value)
        if not text or text in seen:
            continue
        ordered.append(text)
        seen.add(text)
    return tuple(ordered)


def _optional_path(value: Any, *, base_dir: Path) -> Path | None:
    text = _optional_text(value)
    if not text:
        return None
    return (base_dir / text).resolve()


def _required_text(value: Any, name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _optional_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
