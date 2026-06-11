"""FilterExpression models for the Maia selection domain."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


PredicateScalar: TypeAlias = str | int | float | bool
PredicateParam: TypeAlias = PredicateScalar | tuple[PredicateScalar, ...]


class SelectionExpressionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Predicate(SelectionExpressionModel):
    kind: Literal["predicate"] = "predicate"
    name: str
    params: dict[str, PredicateParam] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("predicate name must not be blank")
        return value

    @field_validator("params")
    @classmethod
    def _reject_empty_param_lists(
        cls,
        value: dict[str, PredicateParam],
    ) -> dict[str, PredicateParam]:
        for key, param in value.items():
            if not key.strip():
                raise ValueError("predicate param keys must not be blank")
            if isinstance(param, tuple) and not param:
                raise ValueError(f"params.{key} must not be empty")
        return value


class AllOf(SelectionExpressionModel):
    kind: Literal["all_of"] = "all_of"
    expressions: tuple["FilterExpression", ...]

    @field_validator("expressions")
    @classmethod
    def _require_expressions(
        cls,
        value: tuple["FilterExpression", ...],
    ) -> tuple["FilterExpression", ...]:
        if not value:
            raise ValueError("all_of requires at least one expression")
        return value


class AnyOf(SelectionExpressionModel):
    kind: Literal["any_of"] = "any_of"
    expressions: tuple["FilterExpression", ...]

    @field_validator("expressions")
    @classmethod
    def _require_expressions(
        cls,
        value: tuple["FilterExpression", ...],
    ) -> tuple["FilterExpression", ...]:
        if not value:
            raise ValueError("any_of requires at least one expression")
        return value


class Not(SelectionExpressionModel):
    kind: Literal["not"] = "not"
    expression: "FilterExpression"


FilterExpression: TypeAlias = Annotated[
    Predicate | AllOf | AnyOf | Not,
    Field(discriminator="kind"),
]

AllOf.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()

_FILTER_EXPRESSION_ADAPTER = TypeAdapter(FilterExpression)
_FILTER_EXPRESSION_LIST_ADAPTER = TypeAdapter(tuple[FilterExpression, ...])


def parse_filter_expression(
    value: FilterExpression | Mapping[str, object],
) -> FilterExpression:
    if isinstance(value, (Predicate, AllOf, AnyOf, Not)):
        return value
    return _FILTER_EXPRESSION_ADAPTER.validate_python(value)


def parse_filter_expressions(
    values: Sequence[FilterExpression | Mapping[str, object]],
) -> tuple[FilterExpression, ...]:
    if isinstance(values, tuple) and all(
        isinstance(value, (Predicate, AllOf, AnyOf, Not)) for value in values
    ):
        return values
    return _FILTER_EXPRESSION_LIST_ADAPTER.validate_python(values)


def iter_predicates(
    expression: FilterExpression | Mapping[str, object],
) -> tuple[Predicate, ...]:
    return tuple(_iter_predicates(parse_filter_expression(expression)))


def _iter_predicates(expression: FilterExpression) -> tuple[Predicate, ...]:
    if isinstance(expression, Predicate):
        return (expression,)
    if isinstance(expression, Not):
        return _iter_predicates(expression.expression)

    predicates: list[Predicate] = []
    for child in expression.expressions:
        predicates.extend(_iter_predicates(child))
    return tuple(predicates)


__all__ = [
    "AllOf",
    "AnyOf",
    "FilterExpression",
    "Not",
    "Predicate",
    "PredicateParam",
    "PredicateScalar",
    "iter_predicates",
    "parse_filter_expression",
    "parse_filter_expressions",
]
