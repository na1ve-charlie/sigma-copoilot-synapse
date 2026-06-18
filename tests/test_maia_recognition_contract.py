from __future__ import annotations

from pathlib import Path

import yaml


CONTRACT_PATH = Path("configs/maia/contracts/recognition_report_contract.yaml")
SAMPLES_PATH = Path("configs/maia/testdata/recognition_samples.yaml")

EXPECTED_FIELD_ORDER = [
    "message",
    "verdict",
    "requires_confirmation",
    "degraded",
    "intents",
    "action_intents",
    "slot_operations",
    "diagnostics",
]
FORBIDDEN_OUTPUT_FIELDS = {
    "plan",
    "task_id",
    "selection_set_id",
    "selection_hash",
    "filter_expression",
    "preview",
    "risk_level",
    "confirmation_token",
}


def test_recognition_report_contract_keeps_stable_json_field_order() -> None:
    payload = _load_yaml(CONTRACT_PATH)

    assert payload["name"] == "RecognitionReport"
    assert payload["field_order"] == EXPECTED_FIELD_ORDER
    assert payload["public_field_sources"] == {
        "message": "cli input echo",
        "verdict": "themis.decision.verdict",
        "requires_confirmation": "themis.decision.requires_confirmation",
        "degraded": "themis.decision.degraded",
        "intents": "themis.decision.intents",
        "action_intents": "themis.decision.action_intents",
        "slot_operations": "themis.decision.slot_operations",
        "diagnostics": "themis.decision.diagnostics",
    }


def test_recognition_report_contract_stays_out_of_execution_concerns() -> None:
    payload = _load_yaml(CONTRACT_PATH)

    assert set(payload["forbidden_output_fields"]) == FORBIDDEN_OUTPUT_FIELDS
    assert payload["field_shapes"]["diagnostics"]["default_without_flag"] == {}
    assert payload["field_shapes"]["verdict"]["enum"] == ["clear", "ambiguous", "low"]
    assert payload["nested_shapes"]["action_intent"]["required_fields"] == ["name", "score"]
    assert payload["nested_shapes"]["slot_operation"]["required_fields"] == [
        "intent",
        "score",
        "action",
        "entity_type",
        "target",
        "slot_valid",
    ]


def test_recognition_samples_cover_goal_acceptance_examples() -> None:
    payload = _load_yaml(SAMPLES_PATH)
    cases = {case["message"]: case for case in payload["cases"]}

    assert payload["report_contract"] == "RecognitionReport"
    assert list(cases) == [
        "查找最近一周不合格记录",
        "导出 A 型号的原始数据",
        "导出 Excel",
        "Vib1 或 Vib2 任意一个不合格",
        "删除上面这些数据",
        "先备份这些数据，然后删除本地原始数据",
    ]
    assert cases["查找最近一周不合格记录"]["expected_report"]["action_intents"] == [
        {"name": "task.nvh.record_search"}
    ]
    assert cases["导出 A 型号的原始数据"]["expected_report"]["action_intents"] == [
        {"name": "task.nvh.origin_data_export"}
    ]
    assert cases["导出 Excel"]["expected_report"]["action_intents"] == [
        {"name": "task.nvh.excel_export"}
    ]
    assert cases["删除上面这些数据"]["expected_report"]["action_intents"] == [
        {"name": "task.nvh.data_delete"}
    ]
    assert cases["先备份这些数据，然后删除本地原始数据"]["expected_report"][
        "action_intents"
    ] == [{"name": "task.nvh.data_backup"}, {"name": "task.nvh.data_delete"}]


def test_recognition_samples_use_declared_scope_and_no_execution_fields() -> None:
    contract = _load_yaml(CONTRACT_PATH)
    samples = _load_yaml(SAMPLES_PATH)
    allowed_actions = set(contract["initial_scope"]["action_intents"])
    allowed_slots = set(contract["initial_scope"]["slot_entity_types"])

    for case in samples["cases"]:
        report = case["expected_report"]
        assert FORBIDDEN_OUTPUT_FIELDS.isdisjoint(report)
        assert report["diagnostics"] == {}
        for action_intent in report["action_intents"]:
            assert action_intent["name"] in allowed_actions
        for slot_operation in report["slot_operations"]:
            assert slot_operation["entity_type"] in allowed_slots


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
