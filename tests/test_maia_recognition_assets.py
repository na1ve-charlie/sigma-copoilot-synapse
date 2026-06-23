from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from themis import load_intents

from maia import build_maia_recognizer_from_config
from maia.recognition import adapter as adapter_module


INTENTS_DIR = Path("configs/maia/runtime/intents")
CALIBRATION_PATH = Path("configs/maia/runtime/calibration_cases.yaml")
TREE_PROMPT_PATH = Path("configs/maia/runtime/tree_prompt.yaml")
TREE_PROMPT_EXAMPLES_PATH = Path("configs/maia/runtime/tree_prompt_examples.yaml")


def test_maia_intent_files_cover_first_batch_actions_and_selection_slots() -> None:
    names = {entry.name for entry in _load_maia_entries()}

    assert {
        "task.nvh.record_search",
        "task.nvh.excel_export",
        "task.nvh.origin_data_export",
        "task.nvh.data_backup",
        "task.nvh.data_delete",
        "task.nvh.data_observation.view_indicator_result",
        "task.nvh.data_observation.indicator_trend_analysis.trend",
        "task.nvh.report.download",
        "task.nvh.report.generate",
        "task.nvh.audio.generate",
        "task.nvh.colormap.recompute",
    }.issubset(names)
    assert "task.nvh.data_export" not in names
    assert {
        "task.nvh.selection.set_time_range",
        "task.nvh.selection.set_product_type",
        "task.nvh.selection.set_summary_result",
        "task.nvh.selection.set_sensor",
        "task.nvh.selection.set_test_segment",
        "task.nvh.selection.set_test_section",
        "task.nvh.selection.set_status",
        "task.nvh.selection.set_indicator",
        "task.nvh.selection.set_manual_tagging",
        "task.nvh.selection.set_remark",
        "task.nvh.selection.set_filter_operator",
        "task.nvh.selection.set_data_kind",
        "task.nvh.selection.use_active_selection",
    }.issubset(names)


def test_maia_calibration_cases_cover_goal_examples_and_declared_intents() -> None:
    payload = yaml.safe_load(CALIBRATION_PATH.read_text(encoding="utf-8"))
    cases = {case["message"]: case for case in payload["cases"]}
    names = {entry.name for entry in _load_maia_entries()}

    assert list(cases)[:5] == [
        "查找最近一周不合格记录",
        "导出 A 型号的原始数据",
        "导出所有传感器的全部数据 Excel",
        "Vib1 或 Vib2 任意一个不合格",
        "删除上面这些数据",
    ]
    assert cases["查找最近一周不合格记录"]["expected"] == [
        "task.nvh.selection.set_time_range",
        "task.nvh.selection.set_summary_result",
        "task.nvh.record_search",
    ]
    assert cases["导出 A 型号的原始数据"]["expected"] == [
        "task.nvh.selection.set_product_type",
        "task.nvh.origin_data_export",
    ]
    assert cases["导出所有传感器的全部数据 Excel"]["expected"] == [
        "task.nvh.excel_export",
    ]
    assert cases["删除上面这些数据"]["expected"] == [
        "task.nvh.selection.use_active_selection",
        "task.nvh.data_delete",
    ]
    assert cases["先备份这些数据，然后删除本地原始数据"]["expected"] == [
        "task.nvh.selection.use_active_selection",
        "task.nvh.data_backup",
        "task.nvh.data_delete",
    ]
    for case in payload["cases"]:
        expected = case["expected"]
        expected_names = [expected] if isinstance(expected, str) else expected
        assert set(expected_names).issubset(names)


def test_default_maia_recognizer_config_now_loads_first_batch_intents(
    monkeypatch,
) -> None:
    created: dict[str, Any] = {}

    def fake_builder(
        entries: list[Any],
        llm: Any,
        *,
        resolver: Any | None = None,
        config: Any | None = None,
        tree_prompt: Any | None = None,
    ) -> object:
        created["entries"] = entries
        created["llm"] = llm
        created["resolver"] = resolver
        created["config"] = config
        created["tree_prompt"] = tree_prompt
        return object()

    monkeypatch.setattr(adapter_module, "BusinessIntentRecognizer", fake_builder)

    recognizer = build_maia_recognizer_from_config(llm="llm-client")

    assert recognizer is not None
    assert len(created["entries"]) >= 20
    assert {entry.name for entry in created["entries"]}.issuperset(
        {
            "task.nvh.record_search",
            "task.nvh.excel_export",
            "task.nvh.origin_data_export",
            "task.nvh.selection.set_time_range",
            "task.nvh.selection.use_active_selection",
        }
    )
    assert created["llm"] == "llm-client"
    assert created["tree_prompt"] is not None
    assert "task.nvh.record_search" in {
        intent.intent
        for example in created["tree_prompt"].examples
        for intent in example.intents
    }
    assert {
        "latest_n",
        "product_type",
        "selection_reference",
        "summary_result",
        "time_range",
    }.issubset(set(created["tree_prompt"].entity_types))


def test_origin_data_export_tree_examples_are_action_only() -> None:
    payload = yaml.safe_load(TREE_PROMPT_EXAMPLES_PATH.read_text(encoding="utf-8"))
    examples = {example["input"]: example for example in payload["examples"]}

    for message in (
        "帮我导出原始数据",
        "导出 TDMS",
        "把这些记录导成 H5 原始文件",
    ):
        intents = examples[message]["intents"]
        assert intents == [
            {
                "intent": "task.nvh.origin_data_export",
                "score": 1.0,
                "action": "",
                "entity_type": "",
                "target": "",
                "slot_valid": True,
            }
        ]
    assert examples["导出 Excel"]["intents"] == [
        {
            "intent": "task.nvh.excel_export",
            "score": 1.0,
            "action": "",
            "entity_type": "",
            "target": "",
            "slot_valid": True,
        }
    ]
    assert examples["导出所有传感器的全部数据 Excel"]["intents"] == [
        {
            "intent": "task.nvh.excel_export",
            "score": 1.0,
            "action": "",
            "entity_type": "",
            "target": "",
            "slot_valid": True,
        }
    ]


def test_data_management_tree_examples_keep_terminal_actions_slotless() -> None:
    payload = yaml.safe_load(TREE_PROMPT_EXAMPLES_PATH.read_text(encoding="utf-8"))
    examples = {example["input"]: example for example in payload["examples"]}

    expected = {
        "帮我删除这批测试记录": [_slotless_intent("task.nvh.data_delete")],
        "备份这批测试记录": [_slotless_intent("task.nvh.data_backup")],
        "先备份这批测试记录，然后删除本地原始数据": [
            _slotless_intent("task.nvh.data_backup"),
            _slotless_intent("task.nvh.data_delete"),
        ],
    }

    for message, intents in expected.items():
        assert examples[message]["intents"] == intents


def test_audio_generation_tree_example_keeps_ng_as_task_parameter() -> None:
    payload = yaml.safe_load(TREE_PROMPT_EXAMPLES_PATH.read_text(encoding="utf-8"))
    examples = {example["input"]: example for example in payload["examples"]}

    assert examples["帮我生成 NG 音频"]["intents"] == [
        _slotless_intent("task.nvh.audio.generate")
    ]


def test_data_management_tree_examples_are_within_render_budget() -> None:
    examples_payload = yaml.safe_load(TREE_PROMPT_EXAMPLES_PATH.read_text(encoding="utf-8"))
    prompt_payload = yaml.safe_load(TREE_PROMPT_PATH.read_text(encoding="utf-8"))
    example_inputs = [example["input"] for example in examples_payload["examples"]]
    max_examples = prompt_payload["example_rendering"]["max_examples"]

    required_examples = (
        "帮我删除这批测试记录",
        "备份这批测试记录",
        "先备份这批测试记录，然后删除本地原始数据",
        "帮我生成 NG 音频",
    )

    assert max(example_inputs.index(message) for message in required_examples) < max_examples


def test_terminal_action_tree_examples_do_not_emit_slot_operations() -> None:
    payload = yaml.safe_load(TREE_PROMPT_EXAMPLES_PATH.read_text(encoding="utf-8"))
    terminal_intents = {
        "task.nvh.origin_data_export",
        "task.nvh.excel_export",
        "task.nvh.data_backup",
        "task.nvh.data_delete",
        "task.nvh.audio.generate",
    }

    for example in payload["examples"]:
        for intent in example["intents"]:
            if intent.get("intent") not in terminal_intents:
                continue
            assert intent["action"] == ""
            assert intent["entity_type"] == ""
            assert intent["target"] == ""
            assert "target_from" not in intent
            assert intent["slot_valid"] is True


def test_tree_prompt_examples_do_not_include_sensor_resolver_query() -> None:
    payload = yaml.safe_load(TREE_PROMPT_EXAMPLES_PATH.read_text(encoding="utf-8"))

    assert "inquiry.nvh.resolver_query.sensors" not in {
        intent.get("intent")
        for example in payload["examples"]
        for intent in example["intents"]
    }


def test_tree_prompt_examples_do_not_include_context_switch_examples() -> None:
    payload = yaml.safe_load(TREE_PROMPT_EXAMPLES_PATH.read_text(encoding="utf-8"))

    assert {
        "去掉 {old_sensor} 加上 {new_sensor}",
        "切换到 {segment} 后查看 {observation_name}",
    }.isdisjoint({example["input"] for example in payload["examples"]})


def _load_maia_entries() -> list[Any]:
    entries: list[Any] = []
    for path in sorted(INTENTS_DIR.glob("*.yaml")):
        entries.extend(load_intents(path))
    return entries


def _slotless_intent(name: str) -> dict[str, object]:
    return {
        "intent": name,
        "score": 1.0,
        "action": "",
        "entity_type": "",
        "target": "",
        "slot_valid": True,
    }
