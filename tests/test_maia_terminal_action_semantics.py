from __future__ import annotations

from pathlib import Path

import yaml


INTENTS_PATH = Path("configs/maia/runtime/intents/nvh_terminal_actions.yaml")
EXAMPLES_PATH = Path("configs/maia/runtime/tree_prompt_examples.yaml")
PROMPT_PATH = Path("configs/maia/runtime/tree_prompt.yaml")
INTENTS = {
    intent["name"]: intent
    for intent in yaml.safe_load(INTENTS_PATH.read_text(encoding="utf-8"))["intents"]
}


def test_origin_export_semantics_include_business_aliases() -> None:
    origin_export = INTENTS["task.nvh.origin_data_export"]

    assert "导出时域波形" in origin_export["embed_text"]
    assert "导出工况数据" in origin_export["embed_text"]
    assert "时域波形" in origin_export["tree_text"]
    assert "工况数据" in origin_export["tree_text"]


def test_excel_export_semantics_include_data_and_indicator_terms() -> None:
    excel_export = INTENTS["task.nvh.excel_export"]

    assert "一维数据或一维指标" in excel_export["tree_text"]
    assert "二维数据或二维指标" in excel_export["tree_text"]


def test_management_semantics_keep_artifacts_as_application_params() -> None:
    for name in ("task.nvh.data_backup", "task.nvh.data_delete"):
        intent = INTENTS[name]
        assert "彩图" in intent["tree_text"]
        assert "原始数据" in intent["tree_text"]
        assert "结果数据" in intent["tree_text"]
        assert "应用层" in intent["tree_text"]
        assert "无 slot" in intent["tree_text"]


def test_tree_prompt_forbids_terminal_task_slot_operations() -> None:
    prompt = yaml.safe_load(PROMPT_PATH.read_text(encoding="utf-8"))["template"]

    assert 'do not encode deletion as action="remove"' in prompt
    assert "do not map artifact names to data_kind, archive_status" in prompt


def test_regression_utterances_are_not_added_to_runtime_few_shot_examples() -> None:
    payload = yaml.safe_load(EXAMPLES_PATH.read_text(encoding="utf-8"))
    example_inputs = {example["input"] for example in payload["examples"]}

    assert {
        "我想导出一维数据、二维数据以及结果数据到Excel中",
        "我想导出Mic2的一维指标、二维指标以及结果数据到Excel中",
        "帮我删除这批数据的彩图、结果数据",
        "帮我备份并删除彩图、原始数据",
    }.isdisjoint(example_inputs)
