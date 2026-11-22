"""Funnel edges inside an incident and the ad-destination mismatch (docs/architecture.md section 4, 10).

An ad (image or video view) and the page it leads to join one campaign only with evidence:
the domain shown in the ad is the page host; the ad ID appears in the landing URL; a shared
brand, offer or named person; a redirect or stored navigation record; or (when the vector
layers exist) semantic closeness. Two ads in one folder are one ad (a `same_ad` edge) when
they carry the same Ad Library ID, advertiser, or brand/offer: Ad Library screenshots of an
ad then join the campaign its feed screenshot led to. `co_captured` edges — same folder,
nothing else — are returned for display and never used for grouping. When pages exist in the
folder and no evidence ties the ad (or an ad it is the same as) to any of them, a
`funnel.mismatch` observation is recorded instead of forcing an edge.
"""

from __future__ import annotations

import re
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
    kind: str  # "funnel" | "same_ad" (two views of one ad, both ads) | "co_captured"
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


# The fixture names and the Perception track's names for what a screenshot shows.
AD_DOMAIN = ("ocr.domain", "image.domain", "redirect.domain")
AD_BRAND = ("ocr.brand", "image.brand_offer", "brand.offer")
AD_PERSON = ("ocr.person", "image.person_name")
AD_ID = ("ad.id", "ad.ad_id")
AD_ADVERTISER = ("ad.advertiser", "image.advertiser")  # the sponsoring page's name
PAGE_BRAND = ("brand.offer", "offer.name", "ocr.brand", "page.headline", "page.title")
# The operator's own campaign label may name the brand among other words: matched as a
# whole-word phrase. Not page titles: a site sharing the brand's domain in its title can be a
# shared host or white page, which fusion keeps apart on purpose.
PAGE_NAMING = ("aio.campaign_name",)
PAGE_PERSON = ("entity.person", "ocr.person")
# Names that appear in many unrelated ads: the platform around the ad, the button, or a news
# outlet the scam imitates. They are never anyone's brand.
NOT_A_BRAND = frozenset(
    {
        "facebook", "instagram", "meta", "messenger", "threads", "whatsapp", "tiktok",
        "youtube", "google", "amazon", "amazon.com", "fb.me", "app store", "google play",
        "sponsorowane", "sponsored", "play game", "zagraj w grę", "dowiedz się więcej",
        "learn more", "tvn24", "tvn24+", "polsat news", "tvp info", "interia", "onet",
        "gazeta.pl", "wp.pl", "cnn", "bbc", "fox news", "nbc news", "reuters",
    }
)
MIN_PHRASE = 5  # a shorter name inside longer text matches too much


def _any(observations: list[Observation], namespaces) -> set[str]:
    out: set[str] = set()
    for ns in namespaces:
        out |= _values(observations, ns)
    return out


def _names(observations: list[Observation], namespaces) -> set[str]:
    """Normalised brand-like names, without the ones every ad carries."""
    out = {_normalise_offer("", v) for v in _any(observations, namespaces)}
    return {n for n in out if len(n) >= 3 and n not in NOT_A_BRAND}


def _within(name: str, text: str) -> bool:
    return len(name) >= MIN_PHRASE and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None


def _library_ids(observations: list[Observation]) -> set[str]:
    from ..perception.ad_library_id import find_library_ids

    return set(find_library_ids(observations))


def _navigation_evidence(src: list[Observation], dst: list[Observation]) -> list[str]:
    # The downstream capture remembers an upstream landing. Self-history adds no edge.
    previous = _values(dst, "nav.landing_uuid") - _values(dst, "aio.landing_uuid")
    nav = previous & _values(src, "aio.landing_uuid")
    if nav:
        return ["stored navigation names the landing: " + ", ".join(sorted(nav))]
    return []


def funnel_evidence(src: list[Observation], dst: list[Observation]) -> list[str]:
    """Why `src` (an ad or an upstream page) leads to `dst` (a page). Empty = no evidence."""
    from ..layers.playbook import cast_names

    out = []
    shown = {_registrable(d) for d in _any(src, AD_DOMAIN)}
    hosts = _values(dst, "domain.registrable") | {_registrable(h) for h in _values(dst, "url.host")}
    if shown & hosts:
        out.append("domain shown in the ad = page host: " + ", ".join(sorted(shown & hosts)))
    ad_ids = _any(src, AD_ID) & _any(dst, AD_ID)
    if ad_ids:
        out.append("ad ID present in the landing URL: " + ", ".join(sorted(ad_ids)))
    brands = _names(src, AD_BRAND + AD_ADVERTISER)
    page_brands = _names(dst, PAGE_BRAND)
    if brands & page_brands:
        out.append("shared brand or offer: " + ", ".join(sorted(brands & page_brands)))
    else:
        texts = {_normalise_offer("", t) for t in _any(dst, PAGE_NAMING)}
        named = {b for b in brands if any(_within(b, t) for t in texts)}
        if named:
            out.append("brand named on the page or its campaign: " + ", ".join(sorted(named)))
    page_people = _any(dst, PAGE_PERSON) | {n.lower() for n in cast_names(dst)}
    people = _any(src, AD_PERSON) & page_people
    if people:
        out.append("shared named person: " + ", ".join(sorted(people)))
    out.extend(_navigation_evidence(src, dst))
    return out


def same_ad_evidence(a: list[Observation], b: list[Observation]) -> list[str]:
    """Why two ad views (feed screenshot, Ad Library screenshot, recording) show one ad."""
    out = []
    ids = _library_ids(a) & _library_ids(b)
    if ids:
        out.append("same Ad Library ID: " + ", ".join(sorted(ids)))
    advertisers = _names(a, AD_ADVERTISER) & _names(b, AD_ADVERTISER)
    if advertisers:
        out.append("same advertiser: " + ", ".join(sorted(advertisers)))
    brands_a, brands_b = _names(a, AD_BRAND), _names(b, AD_BRAND)
    shared = {
        min(x, y, key=len)
        for x in brands_a
        for y in brands_b
        if x == y or _within(min(x, y, key=len), max(x, y, key=len))
    }
    if shared:
        out.append("same brand or offer in both ads: " + ", ".join(sorted(shared)))
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

    reached: set[str] = set()  # ads with evidence for some page in the folder
    for ad in non_pages:
        for page in pages:  # every page is considered: co-captured edges are recorded for each
            if consider(ad, page):
                reached.add(ad.view_id)
    # Views of one ad share its page: an Ad Library screenshot rarely shows the landing.
    same: dict[str, set[str]] = defaultdict(set)
    for i, a in enumerate(non_pages):
        for b in non_pages[i + 1 :]:
            why = same_ad_evidence(obs[a.view_id], obs[b.view_id])
            if why:
                funnel.append(FunnelEdge(a.view_id, b.view_id, "same_ad", why))
                same[a.view_id].add(b.view_id)
                same[b.view_id].add(a.view_id)
    frontier = list(reached)
    while frontier:
        for other in same[frontier.pop()] - reached:
            reached.add(other)
            frontier.append(other)
    for ad in non_pages:
        shown = sorted(_any(obs[ad.view_id], AD_DOMAIN[:2]) | _any(obs[ad.view_id], AD_BRAND[:2]))
        if pages and ad.view_id not in reached and shown:
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
            forward = _navigation_evidence(obs[a.view_id], obs[b.view_id])
            backward = _navigation_evidence(obs[b.view_id], obs[a.view_id])
            if forward or backward:
                for src, dst, why in ((a, b, forward), (b, a, backward)):
                    if why:
                        funnel.append(FunnelEdge(src.view_id, dst.view_id, "funnel", why))
                continue
            why = funnel_evidence(obs[a.view_id], obs[b.view_id])
            if why:
                funnel.append(FunnelEdge(a.view_id, b.view_id, "funnel", why))
    return funnel, co_captured, mismatches


def run_funnel(
    store: Store, persist: bool = True
) -> dict[str, tuple[list[FunnelEdge], list[FunnelEdge]]]:
    """Compute edges for every incident and return them. `persist` stores the mismatches."""
    out = {}
    for incident in store.incidents():
        funnel, co, mismatches = funnel_edges(store, incident.incident_id)
        if persist:
            by_view: dict[str, list[Observation]] = defaultdict(list)
            for m in mismatches:
                by_view[m.view_id].append(m)
            for v in store.views(incident.incident_id):
                store.put_observations(v.view_id, EXTRACTOR, by_view.get(v.view_id, []))
        out[incident.incident_id] = (funnel, co)
    return out
