"""Automatic discovery of suspicious ads in the Meta Ad Library.

Searches (watchlist, pivots from known material, model-proposed), records every ad seen,
screens new ads, turns the most relevant into incidents, and reports which of them link to
campaigns already in the store. See docs/architecture.md §7.
"""

from .models import LibraryAd, LibraryQuery, QueryKind, QueryOrigin, Triage
from .run import RunReport, ingest_ads, run_discovery
from .state import DiscoveryState

__all__ = [
    "DiscoveryState",
    "LibraryAd",
    "LibraryQuery",
    "QueryKind",
    "QueryOrigin",
    "RunReport",
    "Triage",
    "ingest_ads",
    "run_discovery",
]
