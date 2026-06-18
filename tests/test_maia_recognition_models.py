from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from maia import DEFAULT_RECOGNITION_CONFIG_PATH, RecognitionReport, load_recognition_config


def test_load_default_recognition_config_links_g00_contract() -> None:
    config = load_recognition_config()

    assert config.config_path == DEFAULT_RECOGNITION_CONFIG_PATH.resolve()
    assert config.intents_path == config.config_path.parent / "intents"
    assert config.tree_prompt_path == config.config_path.parent / "tree_prompt.yaml"
    assert config.report_contract_path == (
        config.config_path.parent.parent / "contracts" / "recognition_report_contract.yaml"
    )
    assert config.llm.model == "qwen/qwen3-4b-2507"
    assert config.themis.build_index_on_init is False


def test_load_recognition_config_resolves_relative_paths_from_file_location(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "configs" / "maia"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "recognition.yaml"
    config_path.write_text(
        "\n".join(
            [
                "recognition:",
                "  intents_path: intents",
                "  tree_prompt_path: ../shared/tree_prompt.yaml",
                "  report_contract_path: recognition_report_contract.yaml",
                "  llm:",
                "    model: test-model",
                "    temperature: 0.2",
                "  themis:",
                "    alpha: 0.25",
                "    delta: 0.15",
                "    min_intent_score: 0.6",
                "    build_index_on_init: true",
            ]
        ),
        encoding="utf-8",
    )

    config = load_recognition_config(config_path)

    assert config.config_path == config_path.resolve()
    assert config.intents_path == config_dir / "intents"
    assert config.tree_prompt_path == config_dir.parent / "shared" / "tree_prompt.yaml"
    assert config.report_contract_path == config_dir / "recognition_report_contract.yaml"
    assert config.llm.temperature == 0.2
    assert config.themis.alpha == 0.25


def test_load_recognition_config_requires_llm_model(tmp_path: Path) -> None:
    config_path = tmp_path / "recognition.yaml"
    config_path.write_text("recognition:\n  llm: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="recognition.llm.model is required"):
        load_recognition_config(config_path)


def test_recognition_report_dump_matches_g00_contract_shape() -> None:
    report = RecognitionReport(
        message="删除上面这些数据",
        verdict="clear",
        requires_confirmation=False,
        degraded=False,
        intents=[{"name": "task.nvh.data_delete", "score": 0.98, "slots": {}}],
        action_intents=[{"name": "task.nvh.data_delete", "score": 0.98}],
        slot_operations=[],
    )

    assert list(report.model_dump(mode="json")) == [
        "message",
        "verdict",
        "requires_confirmation",
        "degraded",
        "intents",
        "action_intents",
        "slot_operations",
        "diagnostics",
    ]
    assert report.model_dump(mode="json")["diagnostics"] == {}


def test_recognition_report_forbids_unknown_execution_fields() -> None:
    with pytest.raises(ValidationError, match="selection_set_id"):
        RecognitionReport(
            message="删除上面这些数据",
            verdict="clear",
            requires_confirmation=False,
            degraded=False,
            selection_set_id="sel-1",
        )
