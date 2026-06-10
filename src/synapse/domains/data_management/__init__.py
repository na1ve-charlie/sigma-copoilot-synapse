"""Data management domain extensions.

Responsible for interpreting record-selection semantics from recognized
commands and projecting them into stable RecordQuery / FilterExpression
trees that the Selection package can consume.
"""

from synapse.domains.data_management.selection_criteria import (
    ProductConfig,
    RecordSelectionCriteria,
    RelativeSelectionReference,
)

__all__ = [
    "ProductConfig",
    "RecordSelectionCriteria",
    "RelativeSelectionReference",
]
