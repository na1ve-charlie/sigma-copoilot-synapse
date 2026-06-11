from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from maia.selection import AllOf, AnyOf, Not, Predicate, iter_predicates, parse_filter_expression


SAMPLES_PATH = Path("configs/maia/filter_expression_samples.yaml")


def test_filter_expression_samples_load_as_typed_trees() -> None:
    cases = yaml.safe_load(SAMPLES_PATH.read_text(encoding="utf-8"))["cases"]

    first = parse_filter_expression(cases[0]["expression"])
    second = parse_filter_expression(cases[1]["expression"])

    assert isinstance(first, AllOf)
    assert isinstance(first.expressions[2], AnyOf)
    assert [predicate.name for predicate in iter_predicates(first)] == [
        "product_type_in",
        "tested_at_between",
        "summary_result_in",
        "indicator_failed",
    ]

    assert isinstance(second, AllOf)
    assert isinstance(second.expressions[1], Not)
    assert [predicate.name for predicate in iter_predicates(second)] == [
        "sensor_in",
        "test_segment_in",
    ]


def test_filter_expression_json_dump_keeps_kind_tags_and_nested_order() -> None:
    expression = parse_filter_expression(
        {
            "kind": "all_of",
            "expressions": [
                {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}},
                {
                    "kind": "not",
                    "expression": {
                        "kind": "predicate",
                        "name": "test_segment_in",
                        "params": {"values": ["TS-03"]},
                    },
                },
            ],
        }
    )

    assert expression.model_dump(mode="json") == {
        "kind": "all_of",
        "expressions": [
            {"kind": "predicate", "name": "summary_result_in", "params": {"values": ["FAIL"]}},
            {
                "kind": "not",
                "expression": {
                    "kind": "predicate",
                    "name": "test_segment_in",
                    "params": {"values": ["TS-03"]},
                },
            },
        ],
    }


def test_filter_expression_collections_require_non_empty_children() -> None:
    with pytest.raises(ValidationError, match="at least one expression"):
        parse_filter_expression({"kind": "all_of", "expressions": []})

    with pytest.raises(ValidationError, match="at least one expression"):
        parse_filter_expression({"kind": "any_of", "expressions": []})


def test_predicate_rejects_blank_name_and_empty_value_lists() -> None:
    with pytest.raises(ValidationError, match="predicate name must not be blank"):
        Predicate(name="   ")

    with pytest.raises(ValidationError, match="params\\.values must not be empty"):
        Predicate(name="sensor_in", params={"values": []})
