"""Stable query normalization and content-hash for RecordQuery / SelectionSet.

All output is deterministic: JSON keys are sorted, datetimes use ISO 8601,
tuples become JSON arrays, and FilterExpression nodes carry a ``"type"``
discriminator.

Domain-specific expression types are decoded via an injectable
*expression_decoders* mapping — the core registry only knows the
generic Task 02 nodes.
"""

# NOTE: no ``from __future__ import annotations`` — we need runtime type
# resolution for dataclasses.fields() and isinstance checks.
# If a future linter / import sorter flags E402 here, allow the exemption.

import json
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from hashlib import sha256
from types import GenericAlias, UnionType
from typing import cast, get_type_hints

from synapse.selection.filters import (
    AllOf,
    AnyOf,
    FieldEquals,
    FieldIn,
    FilterExpression,
    Not,
    StringContains,
    StringEquals,
    TimeBetween,
)
from synapse.selection.models import (
    AggregationStrategy,
    RecordQuery,
    SelectionScope,
    SelectionSet,
    SortRule,
)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

ExpressionDecoder = Callable[[Mapping[str, object]], FilterExpression]
"""Decodes a single expression node from its dict representation."""


# ---------------------------------------------------------------------------
# Snake-case helper
# ---------------------------------------------------------------------------


def _snake_case(name: str) -> str:
    """``AllOf`` → ``all_of``, ``ExcessLimitTuple`` → ``excess_limit_tuple``.

    Consecutive upper-case runs are treated as a single word unless they
    appear at the very start.  ``JSONField`` → ``json_field``.
    """
    out: list[str] = []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch.isupper():
            # Collect consecutive upper-case chars
            j = i + 1
            while j < len(name) and name[j].isupper():
                j += 1
            # If the run is followed by a lower-case char, the last upper
            # char starts a new word (e.g. "JSONField" → "json_field").
            if j > i + 1 and j < len(name) and name[j].islower():
                j -= 1
            if out:
                out.append("_")
            out.append(name[i:j].lower())
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# ===================================================================
# Serialize
# ===================================================================


def _serialize_value(value: object) -> object:
    """Recursively convert a model tree into plain dict / list / scalar."""
    if isinstance(value, FilterExpression):
        node: dict[str, object] = {
            "type": _snake_case(type(value).__name__),
        }
        for f in fields(value):
            v = getattr(value, f.name)
            node[f.name] = _serialize_value(v)
        return _sort_dict(node)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, tuple):
        return [_serialize_value(v) for v in value]

    if isinstance(value, list):
        return [_serialize_value(v) for v in value]

    if is_dataclass(value) and not isinstance(value, type):  # type: ignore[arg-type]
        result: dict[str, object] = {}
        for f in fields(value):
            v = getattr(value, f.name)
            result[f.name] = _serialize_value(v)
        return _sort_dict(result)

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    raise TypeError(
        f"Cannot serialize value of type {type(value).__name__}: {value!r}"
    )


def _sort_dict(payload: dict[str, object]) -> dict[str, object]:
    """Return a new dict with keys in lexicographic order."""
    return {k: payload[k] for k in sorted(payload)}


# ===================================================================
# Public: query normalization
# ===================================================================


def normalize_query(query: RecordQuery) -> dict[str, object]:
    """Produce a stable, sorted dict representation of *query*.

    The output is independent of creation order and is suitable for
    equality comparison or hashing.
    """
    raw: dict[str, object] = {
        "expression": _serialize_value(query.expression),
        "aggregate": _serialize_value(query.aggregate),
        "sort": _serialize_value(query.sort),
        "limit": query.limit,
    }
    return _sort_dict(raw)


def query_json(query: RecordQuery) -> str:
    """Deterministic compact JSON for *query* (sorted keys, no trailing
    whitespace)."""
    payload = normalize_query(query)
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def query_hash(query: RecordQuery) -> str:
    """``sha256:<hex>`` of *normalize_query*'s deterministic JSON."""
    raw = query_json(query).encode("utf-8")
    return f"sha256:{sha256(raw).hexdigest()}"


# ===================================================================
# Deserialize helpers
# ===================================================================


def _require_str(payload: Mapping[str, object], key: str) -> str:
    v = payload.get(key)
    if not isinstance(v, str):
        raise ValueError(f"Expected string for key '{key}', got {type(v).__name__}")
    return v


def _is_optional(annotation: object) -> bool:
    """True when *annotation* is ``T | None`` or ``Optional[T]``."""
    if isinstance(annotation, UnionType):
        return type(None) in annotation.__args__
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        # typing.Optional[T] → UnionType[T, None]
        args = getattr(annotation, "__args__", ())
        return any(a is type(None) for a in args)
    return False


def _unwrap_optional(annotation: object) -> object:
    """Strip ``None`` from a union, returning the non-None type."""
    if isinstance(annotation, UnionType):
        non_none = [a for a in annotation.__args__ if a is not type(None)]
        return non_none[0] if len(non_none) == 1 else annotation
    return annotation


def _decode_value(
    payload: object,
    *,
    annotation: object,
    decoders: Mapping[str, ExpressionDecoder],
) -> object:
    """Walk *payload* and reconstruct the typed model tree.

    *annotation* is the expected type (resolved from ``get_type_hints``).
    """

    # None handling
    if payload is None:
        return None

    # Optional[T] → unwrap and recurse
    if _is_optional(annotation):
        inner = _unwrap_optional(annotation)
        return _decode_value(payload, annotation=inner, decoders=decoders)

    # FilterExpression: use type-discriminator decoder
    if annotation is FilterExpression and isinstance(payload, Mapping):
        return _decode_expression_node(payload, decoders=decoders)

    # Other frozen dataclass
    if isinstance(annotation, type) and is_dataclass(annotation) and isinstance(payload, Mapping):
        kwargs: dict[str, object] = {}
        hints = get_type_hints(annotation)
        for f in fields(annotation):
            if f.name in payload:
                field_annotation = hints.get(f.name, f.type)
                kwargs[f.name] = _decode_value(
                    cast(object, payload[f.name]),
                    annotation=field_annotation,
                    decoders=decoders,
                )
        return annotation(**kwargs)

    # Generic alias: tuple[SortRule, ...], etc.
    if isinstance(annotation, GenericAlias):
        origin = annotation.__origin__
        if origin is tuple:
            inner_ann = annotation.__args__[0] if annotation.__args__ else str
            items = tuple(
                _decode_value(v, annotation=inner_ann, decoders=decoders)
                for v in cast(list, payload)
            )
            return items
        raise ValueError(f"Unsupported generic type: {annotation}")

    # datetime from ISO 8601 string
    if annotation is datetime and isinstance(payload, str):
        return datetime.fromisoformat(payload)

    # int coercion (JSON doesn't distinguish int/float)
    if annotation is int and isinstance(payload, (int, float)):
        return int(payload)

    # Pass-through for primitives
    return payload


def _decode_expression_node(
    payload: Mapping[str, object],
    *,
    decoders: Mapping[str, ExpressionDecoder],
) -> FilterExpression:
    type_name = _require_str(payload, "type")
    decoder = decoders.get(type_name)
    if decoder is None:
        raise ValueError(f"Unknown expression type: {type_name!r}")
    return decoder(payload)


# ---------------------------------------------------------------------------
# Built-in expression decoders (Task 02 types only)
# ---------------------------------------------------------------------------


def _make_builtin_decoders() -> dict[str, ExpressionDecoder]:
    """Return decoders for the 8 generic expression nodes."""

    def _decode_all_of(d: Mapping[str, object]) -> AllOf:
        children: tuple[FilterExpression, ...] = tuple(
            _decode_expression_node(
                cast(Mapping[str, object], c), decoders=resolved
            )
            for c in cast(list, d["children"])
        )
        return AllOf(children)

    def _decode_any_of(d: Mapping[str, object]) -> AnyOf:
        children = tuple(
            _decode_expression_node(
                cast(Mapping[str, object], c), decoders=resolved
            )
            for c in cast(list, d["children"])
        )
        return AnyOf(children)

    def _decode_not(d: Mapping[str, object]) -> Not:
        return Not(
            _decode_expression_node(
                cast(Mapping[str, object], d["child"]), decoders=resolved
            )
        )

    def _decode_field_equals(d: Mapping[str, object]) -> FieldEquals:
        return FieldEquals(
            field=_require_str(d, "field"),
            value=d["value"],
        )

    def _decode_field_in(d: Mapping[str, object]) -> FieldIn:
        return FieldIn(
            field=_require_str(d, "field"),
            values=tuple(cast(list, d["values"])),
        )

    def _decode_string_contains(d: Mapping[str, object]) -> StringContains:
        return StringContains(
            field=_require_str(d, "field"),
            value=_require_str(d, "value"),
        )

    def _decode_string_equals(d: Mapping[str, object]) -> StringEquals:
        return StringEquals(
            field=_require_str(d, "field"),
            value=_require_str(d, "value"),
        )

    def _decode_time_between(d: Mapping[str, object]) -> TimeBetween:
        return TimeBetween(
            start=datetime.fromisoformat(_require_str(d, "start")),
            end=datetime.fromisoformat(_require_str(d, "end")),
        )

    resolved: dict[str, ExpressionDecoder] = {
        "all_of": _decode_all_of,
        "any_of": _decode_any_of,
        "not": _decode_not,
        "field_equals": _decode_field_equals,
        "field_in": _decode_field_in,
        "string_contains": _decode_string_contains,
        "string_equals": _decode_string_equals,
        "time_between": _decode_time_between,
    }
    return resolved


# ===================================================================
# Public: query_from_dict
# ===================================================================


def _merge_decoders(
    extra: Mapping[str, ExpressionDecoder] | None,
) -> Mapping[str, ExpressionDecoder]:
    builtin = _make_builtin_decoders()
    if extra is None:
        return builtin
    merged: dict[str, ExpressionDecoder] = dict(builtin)
    merged.update(extra)
    return merged


def _reconstruct_dataclass(
    cls: type,
    payload: Mapping[str, object],
    decoders: Mapping[str, ExpressionDecoder],
) -> object:
    """Generic reconstruction of any frozen dataclass *cls* from *payload*."""
    hints = get_type_hints(cls)
    kwargs: dict[str, object] = {}
    for f in fields(cls):
        if f.name in payload:
            # Use the resolved hint (which handles forward refs / unions)
            annotation = hints.get(f.name, f.type)
            kwargs[f.name] = _decode_value(
                cast(object, payload[f.name]),
                annotation=annotation,
                decoders=decoders,
            )
    return cls(**kwargs)


def query_from_dict(
    payload: Mapping[str, object],
    *,
    expression_decoders: Mapping[str, ExpressionDecoder] | None = None,
) -> RecordQuery:
    """Reconstruct a *RecordQuery* from its dict representation."""
    decoders = _merge_decoders(expression_decoders)
    return cast(
        RecordQuery,
        _reconstruct_dataclass(RecordQuery, payload, decoders),
    )


# ===================================================================
# Public: selection_to_dict / selection_from_dict
# ===================================================================


def selection_to_dict(selection: SelectionSet) -> dict[str, object]:
    """Serialize a *SelectionSet* including all instance fields.

    This is intended for cross-process / cross-turn transport (e.g.
    writing to JSON columns or CLI stdout).  It is **not** the input
    to ``query_hash`` — content hashing only consumes
    ``normalize_query`` and explicitly excludes ``SelectionSet.id``,
    timestamps, and materialization fields.
    """
    result: dict[str, object] = {}
    for f in fields(SelectionSet):
        v = getattr(selection, f.name)
        result[f.name] = _serialize_value(v)
    return _sort_dict(result)


def selection_from_dict(
    payload: Mapping[str, object],
    *,
    expression_decoders: Mapping[str, ExpressionDecoder] | None = None,
) -> SelectionSet:
    """Reconstruct a *SelectionSet* from its dict representation."""
    decoders = _merge_decoders(expression_decoders)
    return cast(
        SelectionSet,
        _reconstruct_dataclass(SelectionSet, payload, decoders),
    )
