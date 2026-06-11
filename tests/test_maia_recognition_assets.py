from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from themis import load_intents

from maia import build_maia_recognizer_from_config
from maia.recognition import adapter as adapter_module


INTENTS_DIR = Path("configs/maia/intents")
CALIBRATION_PATH = Path("configs/maia/calibration_cases.yaml")


def test_maia_intent_files_cover_first_batch_actions_and_selection_slots() -> None:
    names = {entry.name for entry in _load_maia_entries()}

    assert {
        "task.nvh.record_search",
        "task.nvh.data_export",
        "task.nvh.data_backup",
        "task.nvh.data_delete",
        "task.nvh.data_observation.batch.frequency_spectrum",
        "task.nvh.data_observation.indicator_trend_analysis.trend",
        "task.nvh.report.download",
        "task.nvh.report.generate",
        "task.nvh.audio.generate",
        "task.nvh.colormap.recompute",
    }.issubset(names)
    assert {
        "task.nvh.selection.set_time_range",
        "task.nvh.selection.set_product_type",
        "task.nvh.selection.set_summary_result",
        "task.nvh.selection.set_sensor",
        "task.nvh.selection.set_test_segment",
        "task.nvh.selection.set_indicator",
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
        "Vib1 或 Vib2 任意一个不合格",
        "删除上面这些数据",
        "先备份这些数据，然后删除本地原始数据",
    ]
    assert cases["查找最近一周不合格记录"]["expected"] == [
        "task.nvh.selection.set_time_range",
        "task.nvh.selection.set_summary_result",
        "task.nvh.record_search",
    ]
    assert cases["导出 A 型号的原始数据"]["expected"] == [
        "task.nvh.selection.set_product_type",
        "task.nvh.selection.set_data_kind",
        "task.nvh.data_export",
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
            "task.nvh.data_export",
            "task.nvh.selection.set_time_range",
            "task.nvh.selection.use_active_selection",
        }
    )
    assert created["llm"] == "llm-client"
    assert created["tree_prompt"] is None


def _load_maia_entries() -> list[Any]:
    entries: list[Any] = []
    for path in sorted(INTENTS_DIR.glob("*.yaml")):
        entries.extend(load_intents(path))
    return entries
