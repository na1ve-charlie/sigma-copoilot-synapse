"""SigMA integration models for Maia."""

from maia.integrations.sigma.records import ArtifactKind, TestRecordPage, TestRecordSummary
from maia.integrations.sigma.request_mapper import (
    LegacyRecordRequestMapper,
    LegacyRecordRequestParams,
)
from maia.integrations.sigma.response_mapper import LegacyRecordResponseMapper

__all__ = [
    "ArtifactKind",
    "LegacyRecordRequestMapper",
    "LegacyRecordRequestParams",
    "LegacyRecordResponseMapper",
    "TestRecordPage",
    "TestRecordSummary",
]
