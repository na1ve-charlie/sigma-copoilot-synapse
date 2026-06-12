"""SigMA integration models for Maia."""

from maia.integrations.sigma.records import ArtifactKind, TestRecordPage, TestRecordSummary
from maia.integrations.sigma.dataset_materializer import (
    DatasetMaterializerError,
    DatasetMaterializerTransport,
    REPLACE_DATASET_RECORDS_PATH,
    SAVE_DATASET_PATH,
    SigmaSelectionSetMaterializer,
)
from maia.integrations.sigma.product_catalog import (
    LIST_PRODUCT_CONFIGS_OPERATION,
    LIST_PRODUCT_SYSTEMS_OPERATION,
    LIST_PRODUCT_VERSIONS_OPERATION,
    PRODUCT_CONFIGS_PATH,
    PRODUCT_SYSTEMS_PATH,
    PRODUCT_VERSIONS_PATH,
    ProductCatalogError,
    ProductCatalogTransport,
    ProductConfig,
    SigmaProductCatalogClient,
)
from maia.integrations.sigma.record_client import (
    LEGACY_RECORDS_PATH,
    LIST_TEST_RECORDS_OPERATION,
    RecordTransport,
    TestRecordClient,
    TestRecordClientError,
)
from maia.integrations.sigma.request_mapper import (
    LegacyRecordRequestMapper,
    LegacyRecordRequestParams,
)
from maia.integrations.sigma.response_mapper import LegacyRecordResponseMapper
from maia.integrations.sigma.token_provider import (
    MutableSigmaTokenProvider,
    SigmaTokenProvider,
)

__all__ = [
    "ArtifactKind",
    "DatasetMaterializerError",
    "DatasetMaterializerTransport",
    "LEGACY_RECORDS_PATH",
    "LegacyRecordRequestMapper",
    "LegacyRecordRequestParams",
    "LegacyRecordResponseMapper",
    "LIST_PRODUCT_CONFIGS_OPERATION",
    "LIST_PRODUCT_SYSTEMS_OPERATION",
    "LIST_PRODUCT_VERSIONS_OPERATION",
    "LIST_TEST_RECORDS_OPERATION",
    "MutableSigmaTokenProvider",
    "PRODUCT_CONFIGS_PATH",
    "PRODUCT_SYSTEMS_PATH",
    "PRODUCT_VERSIONS_PATH",
    "ProductCatalogError",
    "ProductCatalogTransport",
    "ProductConfig",
    "REPLACE_DATASET_RECORDS_PATH",
    "RecordTransport",
    "SAVE_DATASET_PATH",
    "SigmaTokenProvider",
    "SigmaProductCatalogClient",
    "SigmaSelectionSetMaterializer",
    "TestRecordClient",
    "TestRecordClientError",
    "TestRecordPage",
    "TestRecordSummary",
]
