"""Wiring shared by the command line and the interface: paths, sources, the query set."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..config import Settings, get_settings
from ..store import Store
from .meta_web import MetaWebSource
from .models import LibraryQuery, QueryOrigin
from .queries import load_watchlist, model_queries, pivot_queries
from .replay import ReplaySource
from .source import AdLibrarySource
from .state import DiscoveryState


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def watchlist(self) -> Path:
        return self.root / "watchlist.toml"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def incidents(self) -> Path:
        return self.root / "incidents"


def paths(settings: Settings | None = None) -> Paths:
    return Paths((settings or get_settings()).data_dir / "discovery")


def open_state(settings: Settings | None = None) -> DiscoveryState:
    return DiscoveryState((settings or get_settings()).db_path)


def make_source(
    replay_dir: Path | None = None, headed: bool = False, settings: Settings | None = None
) -> AdLibrarySource:
    if replay_dir is not None:
        return ReplaySource(replay_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return MetaWebSource(paths(settings).raw / stamp, headed=headed)


def plan_queries(
    store: Store,
    state: DiscoveryState,
    *,
    watchlist: bool = True,
    pivots: bool = True,
    model: bool = False,
    extra: list[LibraryQuery] | None = None,
    read: Callable | None = None,
    settings: Settings | None = None,
) -> tuple[list[LibraryQuery], list[str]]:
    """The queries for a run, in order: one-off, watchlist, pivots, model. Plus notes to show.

    Every candidate is recorded in state; a query an analyst disabled is left out.
    """
    notes: list[str] = []
    candidates: list[LibraryQuery] = list(extra or [])
    if watchlist:
        candidates += load_watchlist(paths(settings).watchlist)
        candidates += [
            row["query"]
            for row in state.queries()
            if row["origin"] in (QueryOrigin.WATCHLIST.value, QueryOrigin.MODEL.value)
        ]
    if pivots:
        candidates += pivot_queries(store)
    if model:
        proposed, why_none = model_queries(store, read=read)
        candidates += proposed
        if why_none:
            notes.append(f"no model-proposed queries: {why_none}")
    disabled = {row["query_id"] for row in state.queries() if not row["enabled"]}
    out: list[LibraryQuery] = []
    seen: set[str] = set()
    for query in candidates:
        if query.query_id in seen:
            continue
        seen.add(query.query_id)
        state.upsert_query(query)
        if query.query_id in disabled:
            continue
        out.append(query)
    return out, notes
