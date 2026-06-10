"""Selection Query Projector (Task 09).

Pure-function projection from ``RecordSelectionCriteria`` (Task 06)
to ``RecordQuery`` (Task 03).  No external calls, no ID generation,
no clock access, no mutation of input.
"""

from __future__ import annotations

from synapse.domains.data_management.selection_criteria import (
    RecordSelectionCriteria,
)
from synapse.domains.data_management.selection_filters import (
    ExcessLimitTupleMatch,
    ProductTypeMatch,
)
from synapse.selection.filters import (
    AllOf,
    AnyOf,
    FieldEquals,
    FieldIn,
    StringContains,
    TimeBetween,
)
from synapse.selection.models import AggregationStrategy, RecordQuery
from synapse.selection.time_ranges import TimeRangeCriteria


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class EmptySelectionCriteriaError(ValueError):
    """Raised when *RecordSelectionCriteria* carries no filter conditions."""


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


def _project_time_ranges(
    time_ranges: tuple[TimeRangeCriteria, ...],
) -> tuple[TimeBetween, ...]:
    """Convert time-range criteria into ``TimeBetween`` nodes.

    Single-sided ranges (start-only or end-only) are skipped — they
    cannot be projected to the two-sided ``TimeBetween`` filter node.
    """
    result: list[TimeBetween] = []
    for tr in time_ranges:
        if tr.start is not None and tr.end is not None:
            result.append(TimeBetween(start=tr.start, end=tr.end))
    return tuple(result)


def project_criteria(criteria: RecordSelectionCriteria) -> RecordQuery:
    """Convert business criteria into a stable ``RecordQuery``.

    Raises ``EmptySelectionCriteriaError`` when *criteria* contains
    no filter conditions.
    """
    children: list = []

    if criteria.product_configs:
        configs: tuple[tuple[str, str, str], ...] = tuple(
            (pc.type, pc.version, pc.system_no)
            for pc in criteria.product_configs
        )
        children.append(ProductTypeMatch(configs=configs))

    if criteria.serial_contains is not None:
        children.append(
            StringContains(field="serial_no", value=criteria.serial_contains)
        )

    if (
        criteria.excess_limit_sensors
        or criteria.excess_limit_test_names
        or criteria.excess_limit_indicators
    ):
        children.append(
            ExcessLimitTupleMatch(
                sensors=criteria.excess_limit_sensors,
                test_names=criteria.excess_limit_test_names,
                indicators=criteria.excess_limit_indicators,
            )
        )

    time_nodes = _project_time_ranges(criteria.time_ranges)
    if time_nodes:
        if len(time_nodes) == 1:
            children.append(time_nodes[0])
        else:
            children.append(AnyOf(time_nodes))

    if criteria.judgement_results:
        children.append(
            FieldIn(
                field="judgement_result",
                values=criteria.judgement_results,
            )
        )

    if criteria.manual_verdict is not None:
        children.append(
            FieldEquals(field="manual_verdict", value=criteria.manual_verdict)
        )

    if criteria.record_status is not None:
        children.append(
            FieldEquals(field="record_status", value=criteria.record_status)
        )

    if criteria.test_section is not None:
        children.append(
            FieldEquals(field="test_section", value=criteria.test_section)
        )

    if criteria.remark_contains is not None:
        children.append(
            StringContains(field="remark", value=criteria.remark_contains)
        )

    if criteria.archived is not None:
        children.append(
            FieldEquals(field="archived", value=criteria.archived)
        )

    if not children:
        raise EmptySelectionCriteriaError(
            "RecordSelectionCriteria contains no filter conditions"
        )

    expression = (
        children[0] if len(children) == 1 else AllOf(tuple(children))
    )

    aggregate = AggregationStrategy(
        keep_last_per_serial=criteria.keep_last_per_serial,
        only_repeat_serials=criteria.only_repeat_serials,
    )
    has_aggregate = (
        aggregate.keep_last_per_serial or aggregate.only_repeat_serials
    )

    return RecordQuery(
        expression=expression,
        aggregate=aggregate if has_aggregate else None,
        sort=criteria.sort,
        limit=criteria.limit,
    )
