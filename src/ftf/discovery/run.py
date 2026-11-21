"""One discovery run: search, record, screen, turn the chosen ads into incidents, report links."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .. import pipeline
from ..ingest import ingest_folder
from ..store import Store
from .materialise import materialise
from .models import LibraryAd, LibraryQuery, QueryKind
from .source import AdLibrarySource, FetchStatus
from .state import FAILED, INGESTED, NEW, SCREENED_OUT, SELECTED, DiscoveryState
from .triage import known_matches, selected, triage

log = logging.getLogger(__name__)

MAX_QUERIES_PER_RUN = 20
MAX_ADS_PER_QUERY = 50
MAX_INGEST_PER_RUN = 20
LINK_MIN_SCORE = 0.3

Progress = Callable[[str], None]


@dataclass
class IngestedAd:
    ad_id: str
    incident_id: str
    links: list[dict[str, Any]] = field(default_factory=list)  # {view, label, layer, score, shared}
    # identifiers the ad shares with material ingested earlier: {ns, value, incidents}
    known: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RunReport:
    run_id: str
    status: str
    error: str | None = None
    screenshot: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    ingested: list[IngestedAd] = field(default_factory=list)
    skipped_queries: list[str] = field(default_factory=list)


def _effective(query: LibraryQuery, state: DiscoveryState) -> LibraryQuery:
    """A keyword query re-run asks only for ads delivered since its last good run (one day overlap)."""
    if query.kind is not QueryKind.KEYWORDS or query.date_min:
        return query
    last = state.last_success(query.query_id)
    if not last:
        return query
    since = (datetime.fromisoformat(last) - timedelta(days=1)).astimezone(UTC)
    return query.model_copy(update={"date_min": since.strftime("%Y-%m-%d")})


def links_for(store: Store, view_ids: list[str], incident_id: str) -> list[dict[str, Any]]:
    labels = {v.view_id: v.label for v in store.views()}
    out = []
    for view_id in view_ids:
        for ev in store.evidence(view_a=view_id, min_score=LINK_MIN_SCORE):
            other = ev.view_b if ev.view_a == view_id else ev.view_a
            if other.startswith(incident_id + ":"):
                continue
            out.append({
                "view": other, "label": labels.get(other, other), "layer": ev.layer_id,
                "score": round(ev.score, 3),
                "shared": [f"{s['ns']}={s['value']}" for s in ev.shared][:5],
            })  # fmt: skip
    out.sort(key=lambda x: -x["score"])
    return out


def ingest_ads(
    store: Store, state: DiscoveryState, source: AdLibrarySource | None, ad_ids: list[str],
    incidents_dir: Path, download_media: bool = True, progress: Progress | None = None,
) -> list[IngestedAd]:  # fmt: skip
    """Materialise ads already in state as incidents, run the pipeline, and report their links."""
    say = progress or (lambda _msg: None)
    done: list[IngestedAd] = []
    new_view_ids: dict[str, list[str]] = {}
    for ad_id in ad_ids:
        row = state.ad(ad_id)
        if row is None:
            continue
        ad: LibraryAd = row["ad"]
        say(f"materialising ad {ad_id}")
        known = [
            {"ns": m["ns"], "value": m["value"],
             "incidents": sorted({v.split(":")[0] for v in m["views"]})}
            for m in known_matches(store, ad)
        ]  # fmt: skip
        try:
            folder = materialise(ad, row["raw"], incidents_dir, source, download_media)
            incident, views = ingest_folder(folder, store)
            new_view_ids[incident.incident_id] = [v.view_id for v in views]
            state.set_status(ad_id, INGESTED, incident_id=incident.incident_id)
            done.append(IngestedAd(ad_id=ad_id, incident_id=incident.incident_id, known=known))
        except Exception as exc:  # noqa: BLE001 — one bad ad must not stop the others
            log.warning("could not ingest ad %s: %s", ad_id, exc)
            state.set_status(ad_id, FAILED, note=f"{type(exc).__name__}: {exc}"[:300])
    if done:
        say("running the pipeline on the new incidents")
        all_new = [v for ids in new_view_ids.values() for v in ids]
        pipeline.run_extract(store, all_new)
        pipeline.run_fingerprint(store, all_new)
        pipeline.run_compare_all(store)
        for item in done:
            item.links = links_for(store, new_view_ids[item.incident_id], item.incident_id)
    return done


def run_discovery(
    store: Store, state: DiscoveryState, source: AdLibrarySource, queries: list[LibraryQuery],
    incidents_dir: Path, *, max_ads: int = MAX_ADS_PER_QUERY, max_ingest: int = MAX_INGEST_PER_RUN,
    ingest: bool = True, use_model: bool = True, download_media: bool = True,
    read: Callable | None = None, progress: Progress | None = None,
) -> RunReport:  # fmt: skip
    say = progress or (lambda _msg: None)
    run_id = state.start_run(source.name)
    report = RunReport(run_id=run_id, status="ok")
    counts = {"queries": 0, "seen": 0, "new": 0, "stopped": 0, "selected": 0, "ingested": 0,
              "failed_queries": 0}  # fmt: skip
    new_ads: dict[str, LibraryAd] = {}
    try:
        if len(queries) > MAX_QUERIES_PER_RUN:
            report.skipped_queries = [q.label() for q in queries[MAX_QUERIES_PER_RUN:]]
            queries = queries[:MAX_QUERIES_PER_RUN]
        for query in queries:
            state.upsert_query(query)
            effective = _effective(query, state)
            say(f"searching {effective.label()}")
            result = source.fetch(effective, max_ads)
            counts["queries"] += 1
            if result.status is FetchStatus.BLOCKED:
                state.mark_query(query.query_id, "blocked", 0)
                report.status, report.error = "blocked", result.error
                report.screenshot = str(result.screenshot) if result.screenshot else None
                say(f"stopped: {result.error}")
                break
            if result.status is FetchStatus.FAILED:
                counts["failed_queries"] += 1
                state.mark_query(query.query_id, "failed", 0)
                report.status = "partial"
                continue
            for ad, raw in result.ads:
                outcome = state.record_ad(run_id, query.query_id, ad, raw)
                counts["seen"] += 1
                if outcome == "new":
                    counts["new"] += 1
                    new_ads[ad.ad_id] = ad
                elif outcome == "stopped":
                    counts["stopped"] += 1
            state.mark_query(query.query_id, "ok", len(result.ads))

        # Triage every new ad, including those found before a block.
        ranked = []
        for ad_id, ad in new_ads.items():
            say(f"screening ad {ad_id}")
            result = triage(store, ad, use_model=use_model, read=read)
            ranked.append((result.score, ad_id, result))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        chosen = []
        for score, ad_id, result in ranked:
            if selected(result) and len(chosen) < max_ingest:
                state.set_triage(ad_id, result, SELECTED)
                chosen.append(ad_id)
            else:
                state.set_triage(ad_id, result, SCREENED_OUT)
        counts["selected"] = len(chosen)

        if ingest and chosen and report.status != "blocked":
            report.ingested = ingest_ads(store, state, source, chosen, incidents_dir,
                                         download_media=download_media, progress=progress)  # fmt: skip
            counts["ingested"] = len(report.ingested)
    except Exception as exc:
        log.exception("discovery run failed")
        report.status, report.error = "failed", f"{type(exc).__name__}: {exc}"[:300]
    finally:
        report.counts = counts
        state.finish_run(run_id, report.status, counts, report.error, report.screenshot)
    return report


def pending(state: DiscoveryState) -> list[str]:
    """Ads selected but not ingested yet, e.g. because the last run was blocked."""
    return [row["ad_id"] for row in state.ads(status=SELECTED)] + [
        row["ad_id"] for row in state.ads(status=NEW)
    ]
