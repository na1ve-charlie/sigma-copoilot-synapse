"""Compatibility wrapper for record-search product filter helpers."""

from maia.tasks.record_search_filters import (
    ALL_PRODUCTS_VALUE,
    complete_config_version_filter,
    complete_product_type_filter,
    complete_type_system_filter,
    config_version_scope,
    distinct_values,
    invalidate_product_filters_on_scope_change,
    is_all_product_types_request,
    product_type_scope,
    selection_expression_for_storage,
    selection_expression_from_storage,
    type_system_scope,
)

__all__ = [
    "ALL_PRODUCTS_VALUE",
    "complete_config_version_filter",
    "complete_product_type_filter",
    "complete_type_system_filter",
    "config_version_scope",
    "distinct_values",
    "invalidate_product_filters_on_scope_change",
    "is_all_product_types_request",
    "product_type_scope",
    "selection_expression_for_storage",
    "selection_expression_from_storage",
    "type_system_scope",
]
