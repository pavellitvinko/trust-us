"""Funnel edges inside an incident and the ad-destination mismatch (docs/architecture.md section 4, 10).

An ad (image or video view) and the page it leads to join one campaign only with evidence:
the domain shown in the ad is the page host; the ad ID appears in the landing URL; a shared
brand, offer or named person; a redirect or stored navigation record; or (when the vector
layers exist) semantic closeness. `co_captured` edges — same folder, nothing else — are
returned for display and never used for grouping. When pages exist in the folder and no
evidence ties the ad to any of them, a `funnel.mismatch` observation is recorded instead of
forcing an edge.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..contracts import Observation, Purpose, Source, State, View, ViewKind
from ..layers.ids_identity import _normalise_offer

if TYPE_CHECKING:
    from ..store import Store

MISMATCH_NS = "funnel.mismatch"
EXTRACTOR = "funnel"


@dataclass
class FunnelEdge:
    source: str  # the ad, screenshot or upstream page
    target: str  # the page it leads to
    kind: str  # "funnel" | "co_captured"
    evidence: list[str] = field(default_factory=list)


def _values(observations: list[Observation], namespace: str) -> set[str]:
    return {
        str(o.value).strip().lower()
        for o in observations
        if o.namespace == namespace and o.state is State.PRESENT and o.value is not None
    }


def _registrable(host: str) -> str:
    from ..extract.page_identity import registrable_domain

    return registrable_domain(host.lower().strip().rstrip("/"))


def funnel_evidence(src: list[Observation], dst: list[Observation]) -> list[str]:
    """Why `src` (an ad or an upstream page) leads to `dst` (a page). Empty = no evidence."""
    out = []
    shown = {_registrable(d) for d in _values(src, "ocr.domain") | _values(src, "redirect.domain")}
    hosts = _values(dst, "domain.registrable")
    if shown & hosts:
        out.append("domain shown in the ad = page host: " + ", ".join(sorted(shown & hosts)))
    ad_ids = _values(src, "ad.id") & _values(dst, "ad.id")
    if ad_ids:
        out.append("ad ID present in the landing URL: " + ", ".join(sorted(ad_ids)))
    brands = {
        _normalise_offer("", b) for b in _values(src, "ocr.brand") | _values(src, "brand.offer")
    }
    page_brands = {
        _normalise_offer("", b) for b in _values(dst, "brand.offer") | _values(dst, "ocr.brand")
    }
    if brands & page_brands:
        out.append("shared brand or offer: " + ", ".join(sorted(brands & page_brands)))
    people = _values(src, "ocr.person") & (
        _values(dst, "entity.person") | _values(dst, "ocr.person")
    )
    if people:
        out.append("shared named person: " + ", ".join(sorted(people)))
    nav = _values(src, "nav.landing_uuid") & _values(dst, "aio.landing_uuid")
    if nav:
        out.append("stored navigation names the landing: " + ", ".join(sorted(nav)))
    nav_back = _values(dst, "nav.landing_uuid") & _values(src, "aio.landing_uuid")
    if nav_back:
        out.append("stored navigation names the landing: " + ", ".join(sorted(nav_back)))
    return out


def funnel_edges(
    store: Store, incident_id: str
) -> tuple[list[FunnelEdge], list[FunnelEdge], list[Observation]]:
    """(funnel edges, co-captured edges, mismatch observations) for one incident."""
    views = store.views(incident_id)
    obs = {v.view_id: store.observations(v.view_id) for v in views}
    pages = [v for v in views if v.kind is ViewKind.PAGE]
    non_pages = [v for v in views if v.kind is not ViewKind.PAGE]
    funnel, co_captured, mismatches = [], [], []

    def consider(src: View, dst: View) -> bool:
        why = funnel_evidence(obs[src.view_id], obs[dst.view_id])
        if why:
            funnel.append(FunnelEdge(src.view_id, dst.view_id, "funnel", why))
            return True
        co_captured.append(FunnelEdge(src.view_id, dst.view_id, "co_captured"))
        return False

    for ad in non_pages:
        matched = False
        for page in pages:  # every page is considered: co-captured edges are recorded for each
            matched = consider(ad, page) or matched
        shown = sorted(
            _values(obs[ad.view_id], "ocr.domain") | _values(obs[ad.view_id], "ocr.brand")
        )
        if pages and not matched and shown:
            mismatches.append(
                Observation(
                    view_id=ad.view_id,
                    signal_id="I1",
                    namespace=MISMATCH_NS,
                    value={
                        "ad_shows": shown,
                        "pages": [p.view_id for p in pages],
                        "page_hosts": sorted(
                            {
                                h
                                for p in pages
                                for h in _values(obs[p.view_id], "domain.registrable")
                            }
                        ),
                    },
                    purpose=Purpose.RISK,
                    source=Source(
                        file=None,
                        locator="funnel: no evidence ties the ad to any page in the folder",
                    ),
                    extractor=EXTRACTOR,
                    version="1",
                )
            )
    for i, a in enumerate(pages):
        for b in pages[i + 1 :]:
            why = funnel_evidence(obs[a.view_id], obs[b.view_id])
            if why:
                funnel.append(FunnelEdge(a.view_id, b.view_id, "funnel", why))
    return funnel, co_captured, mismatches


def run_funnel(store: Store) -> dict[str, tuple[list[FunnelEdge], list[FunnelEdge]]]:
    """Compute edges for every incident, store the mismatch observations, return the edges."""
    out = {}
    for incident in store.incidents():
        funnel, co, mismatches = funnel_edges(store, incident.incident_id)
        by_view: dict[str, list[Observation]] = defaultdict(list)
        for m in mismatches:
            by_view[m.view_id].append(m)
        for v in store.views(incident.incident_id):
            store.put_observations(v.view_id, EXTRACTOR, by_view.get(v.view_id, []))
        out[incident.incident_id] = (funnel, co)
    return out
