from __future__ import annotations

from pathlib import Path

import yaml


CONTRACT_PATH = Path("configs/maia/contracts/turns_response_contract.yaml")
SAMPLES_PATH = Path("configs/maia/testdata/turns_response_samples.yaml")

PLAN_KINDS = [
    "reply",
    "clarify",
    "task",
    "confirm",
    "context_update",
    "context_clear",
]


def test_turns_response_contract_keeps_public_wrapper_and_kind_list() -> None:
    contract = _load_yaml(CONTRACT_PATH)

    assert contract["name"] == "TurnResponse"
    assert contract["wrapper"]["required_top_level_fields"] == ["plan"]
    assert contract["wrapper"]["routing_field"] == "plan.kind"
    assert contract["plan_kinds"] == PLAN_KINDS
    assert set(contract["wrapper"]["forbidden_top_level_fields"]) == {
        "status",
        "message",
        "diagnostics",
        "selection_set_id",
        "task_id",
        "risk_level",
    }


def test_turns_response_contract_locks_frontend_visible_plan_shapes() -> None:
    contract = _load_yaml(CONTRACT_PATH)

    assert contract["kind_shapes"]["reply"]["required_fields"] == ["kind", "message", "dataset"]
    assert contract["kind_shapes"]["clarify"]["required_fields"] == [
        "kind",
        "reason",
        "message",
        "dataset",
    ]
    assert contract["kind_shapes"]["task"]["required_fields"] == [
        "kind",
        "status",
        "name",
        "intent",
        "title",
        "risk_level",
        "requires_confirmation",
        "params",
        "message",
        "dataset",
    ]
    assert contract["kind_shapes"]["confirm"]["required_fields"] == [
        "kind",
        "reason",
        "message",
        "dataset",
    ]
    assert contract["kind_shapes"]["context_update"]["required_fields"] == [
        "kind",
        "message",
        "dataset",
    ]
    assert contract["kind_shapes"]["context_clear"]["required_fields"] == [
        "kind",
        "message",
        "dataset",
    ]
    assert contract["kind_shapes"]["clarify"]["prompt_shape"]["required_fields"] == [
        "id",
        "target",
        "label",
        "message",
        "required",
        "input_type",
    ]
    assert contract["slot_state_diff"]["required_fields"] == ["changes"]
    assert contract["slot_state_diff"]["change_shape"] == ["slot", "before", "after"]
    assert contract["dataset"]["empty_allowed"] is True
    assert "selection_params" in contract["dataset"]["fields"]
    assert contract["dataset"]["selection_params_excluded_fields"] == ["page", "rows"]


def test_turns_response_samples_cover_all_plan_kinds_and_public_wrapper_only() -> None:
    contract = _load_yaml(CONTRACT_PATH)
    samples = _load_yaml(SAMPLES_PATH)
    required_by_kind = {
        kind: set(shape["required_fields"])
        for kind, shape in contract["kind_shapes"].items()
    }

    assert samples["response_contract"] == "TurnResponse"
    assert [case["response"]["plan"]["kind"] for case in samples["cases"]] == PLAN_KINDS

    for case in samples["cases"]:
        response = case["response"]
        plan = response["plan"]

        assert list(response) == ["plan"]
        assert set(response).isdisjoint(contract["wrapper"]["forbidden_top_level_fields"])
        assert required_by_kind[plan["kind"]].issubset(plan)


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
