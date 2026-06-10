"""SigMA business system gateway contracts."""

from synapse.integrations.sigma.candidates import SigmaCandidateCatalogLoader
from synapse.integrations.sigma.contracts import (
    SigmaCandidate,
    SigmaGateway,
    SigmaGatewayError,
    SigmaQuery,
)
from synapse.integrations.sigma.http import HttpSigmaGateway, SigmaConfig

__all__ = [
    "HttpSigmaGateway",
    "SigmaCandidate",
    "SigmaCandidateCatalogLoader",
    "SigmaConfig",
    "SigmaGateway",
    "SigmaGatewayError",
    "SigmaQuery",
]
