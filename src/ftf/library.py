"""What the Library screen shows: cases, the items in them, their labels, risk and journey.

Read-only over the store. Labels come from grouping (campaign, kit, duplicate), risk from
the suspicion summary, journeys from the proven funnel edges inside a case. A campaign or
kit with a single member is not named: it says nothing a reader can act on.
"""

from __future__ import annotations

import itertools
import json
import re
import string
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import prod
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from .campaign_map import TOGGLES, _shared_values, link_tiers
from .compare.weights import WeightTable
from .contracts import RelationType, Tier, View, ViewKind
from .fusion import DEFAULT
from .fusion.demotion import find_demotions
from .grouping.funnel import AD_ID
from .grouping.levels import Groups
from .grouping.suspicion import reason_summary_for_view
from .registry import layers, load_plugins
from .store import Store

KIND_NAMES = {ViewKind.PAGE: "Page", ViewKind.IMAGE: "Image", ViewKind.VIDEO: "Video"}
_URL_KEYS = ("final_url", "manifest_url", "saved_from", "har_document", "har_first_party")
_PROOFS = (
    ("domain shown in the ad = page host: ", "The ad shows the page's domain: {}"),
    ("ad ID present in the landing URL: ", "Ad ID {} is in the landing URL"),
    ("shared brand or offer: ", "Same brand or offer: {}"),
    ("shared named person: ", "Same person named: {}"),
    ("stored navigation names the landing: ", "Stored navigation names the earlier page: {}"),
)
KIND_TITLES = {
    "domain": "Domain",
    "impersonation": "Impersonation",
    "absent_elements": "Expected but absent",
    "evasion": "Evasion",
    "persuasion": "Persuasion",
}
META_FILE = "library.json"
# The Ad Library's own ID (not the Ads Manager ad ID a landing URL carries).
_LIBRARY_URL = re.compile(r"facebook\.com/ads/library/?\?(?:[^#]*&)?id=(\d+)")


@dataclass
class Label:
    kind: str  # campaign | kit | duplicate | none
    text: str


@dataclass
class Finding:
    kind: str
    summary: str
    source: str


@dataclass
class Risk:
    level: str  # none | warning | error
    findings: list[Finding] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.findings)


@dataclass
class Step:
    chain: int  # chain number inside the case
    position: int  # 1-based
    total: int


@dataclass
class Journey:
    ads: list[str]  # ad IDs the entry page carries, when the ad itself is not in the library
    steps: list[tuple[str, str]]  # (view_id, title), in the order a visitor meets them
    proofs: list[str]  # why step i leads to step i + 1
    host: str | None = None  # the entry page's host, for an Ad Library search


@dataclass
class Item:
    view_id: str
    case_id: str
    kind: ViewKind
    title: str
    host: str | None
    labels: list[Label]
    risk: Risk
    step: Step | None = None
    thumbnail: str | None = None  # absolute path of an image to show
    library_id: str | None = None  # Meta Ad Library ID, when the item is a Library ad
    note: str | None = None  # what the latest add changed for this item
    matches: list[Match] = field(default_factory=list)


@dataclass(frozen=True)
class Match:
    view_id: str
    same_case: bool
    verdict: str
    tiers: tuple[str, ...]
    confidence: str
    observed_groups: int
    total_groups: int
    clue: str

    @property
    def strength(self) -> str:
        return {"high": "Strong", "medium": "Moderate", "low": "Weak"}.get(
            self.confidence, "Not assessed"
        )

    @property
    def coverage(self) -> str:
        return f"{self.observed_groups} of {self.total_groups} evidence groups observed"


@dataclass
class Case:
    case_id: str
    title: str
    added_at: str
    items: list[Item]


@dataclass
class Library:
    cases: list[Case]
    journeys: dict[str, Journey]  # view_id -> the journey it is on
    links: dict[str, set[str]]  # view_id -> views it is linked with (campaign or duplicate)
    playbook_run: bool = False

    def items(self) -> list[Item]:
        return [i for c in self.cases for i in c.items]

    def newest(self) -> list[Item]:
        return sorted(self.items(), key=lambda i: self.added(i.case_id), reverse=True)

    def added(self, case_id: str) -> str:
        return next((c.added_at for c in self.cases if c.case_id == case_id), "")

    def get(self, view_id: str) -> Item | None:
        return next((i for i in self.items() if i.view_id == view_id), None)


# -- case metadata kept beside the store (the store itself is frozen) ----------------------


def read_meta(data_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((data_dir / META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cases": {}}
    return data if isinstance(data.get("cases"), dict) else {"cases": {}}


def record_cases(data_dir: Path, incident_ids: list[str], when: str | None = None) -> None:
    meta = read_meta(data_dir)
    stamp = when or datetime.now(UTC).isoformat(timespec="seconds")
    for incident_id in incident_ids:
        meta["cases"].setdefault(incident_id, {})["added_at"] = stamp
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / (META_FILE + ".tmp")
    tmp.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    tmp.replace(data_dir / META_FILE)


def _added_at(meta: dict[str, Any], incident_id: str, root: str) -> str:
    stamp = (meta["cases"].get(incident_id) or {}).get("added_at")
    if stamp:
        return stamp
    try:  # ingested from the command line: the folder's own time is the best we have
        ts = Path(root).stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


# -- titles ------------------------------------------------------------------------------


def view_url(view: View) -> str | None:
    return next((view.meta[k] for k in _URL_KEYS if view.meta.get(k)), None)


def view_title(view: View) -> tuple[str, str | None]:
    """(what to call the item, its host). A saved page is named after its own title."""
    url = view_url(view)
    host = urlsplit(url).hostname if url else None
    name = None
    if view.primary:
        path = PurePosixPath(view.primary)
        if view.kind is ViewKind.PAGE and path.name in ("dom.html", "index.html"):
            name = host
        elif view.kind is ViewKind.PAGE:
            name = path.stem
        else:
            name = path.name
    if not name:
        label = view.label.split(" · ", 1)[-1]
        name = host or label
    return name, host


def library_id(view: View, root: str) -> str | None:
    """The Meta Ad Library ID of an ad that came from the Library, if the material says so.

    Landing URLs carry the Ads Manager ad ID instead, which the Library cannot look up.
    """
    if view.meta.get("ad_library_id"):  # an ad saved by discovery
        return str(view.meta["ad_library_id"])
    for key in _URL_KEYS:  # an Ad Library page captured from its link
        if match := _LIBRARY_URL.search(str(view.meta.get(key) or "")):
            return match.group(1)
    for name in view.attachments:  # a video downloaded from the Library, with its metadata
        if not name.endswith(".info.json"):
            continue
        try:
            data = json.loads((Path(root) / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if match := _LIBRARY_URL.search(str(data.get("webpage_url") or "")):
            return match.group(1)
    return None


def case_title(incident_id: str) -> str:
    return f"Case {incident_id}" if incident_id.isdigit() else incident_id


def _letters(n: int) -> str:
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = string.ascii_uppercase[r] + out
    return out


# -- risk --------------------------------------------------------------------------------


def risk_for(view_id: str, observations) -> Risk:
    summary = reason_summary_for_view(view_id, observations)
    findings = [
        Finding(
            kind=KIND_TITLES.get(kind, kind.replace("_", " ").capitalize()),
            summary=reason.summary,
            source=":".join(x for x in (reason.source.file, reason.source.locator) if x)
            or reason.namespace,
        )
        for kind, reasons in summary.reasons_by_kind.items()
        for reason in reasons
    ]
    listed = any(
        r.namespace.startswith("warning.")
        for reasons in summary.reasons_by_kind.values()
        for r in reasons
    )
    kinds = len(summary.reasons_by_kind)
    level = "error" if kinds >= 2 or listed else "warning" if kinds == 1 else "none"
    unchecked = [KIND_TITLES.get(k, k) for k in summary.unchecked_kinds]
    return Risk(level=level, findings=findings, unchecked=unchecked)


# -- journeys ----------------------------------------------------------------------------


def plain_proof(reason: str) -> str:
    for prefix, text in _PROOFS:
        if reason.startswith(prefix):
            return text.format(reason[len(prefix) :])
    return reason


def _chains(edges) -> list[list[str]]:
    """Order each connected group of funnel edges the way a visitor meets them."""
    nodes = sorted({e.source for e in edges} | {e.target for e in edges})
    parent = {n: n for n in nodes}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for e in edges:
        parent[find(e.source)] = find(e.target)
    groups: dict[str, list[str]] = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    chains = []
    for members in groups.values():
        inbound = {n: 0 for n in members}
        for e in edges:
            if e.target in inbound and e.source in inbound and e.source != e.target:
                inbound[e.target] += 1
        order, ready = [], sorted(n for n, k in inbound.items() if k == 0)
        while ready:
            n = ready.pop(0)
            order.append(n)
            for e in edges:
                if e.source == n and e.target in inbound:
                    inbound[e.target] -= 1
                    if inbound[e.target] == 0:
                        ready.append(e.target)
        order += sorted(n for n in members if n not in order)  # a cycle keeps its members
        chains.append(order)
    return sorted(chains, key=lambda c: c[0])


# -- the whole screen --------------------------------------------------------------------


_VERDICTS = {
    RelationType.DUPLICATE: "Duplicate",
    RelationType.SAME_CAMPAIGN: "Same campaign",
    RelationType.SAME_FAMILY: "Shared kit",
}
_VERDICT_ORDER = {name: n for n, name in enumerate([*_VERDICTS.values(), "Clue only"])}
_STRENGTH_ORDER = {"high": 0, "medium": 1, "low": 2, "": 3}
LAYER_NAMES = {
    "ids.domain": "Page addresses",
    "ids.campaign": "Campaign records",
    "ids.tracking": "Tracking accounts",
    "ids.conversion": "Contact and conversion accounts",
    "brand.offer": "Brands and offers",
    "asset.exact": "Identical files",
    "asset.perceptual": "Similar images",
    "media.video": "Video frames",
    "ad.creative": "Ad images",
    "text.shingle": "Matching text",
    "text.semantic": "Text meaning",
    "kit.config": "Template settings",
    "kit.url": "Address patterns",
    "kit.form": "Form structure",
    "kit.code": "Code patterns",
    "kit.dom": "Page structure",
    "infra.host": "Hosting",
    "pattern.mo": "Persuasion patterns",
    "kit.absence": "Missing page elements",
    "expected-elements.mo": "Expected page elements",
    "entity.cast": "People and roles",
    "entity.roles": "Persuasion roles",
    "visual.layout": "Visual layout",
    "narrative.skeleton": "Story structure",
}


def match_sort_key(match: Match) -> tuple:
    return (_VERDICT_ORDER[match.verdict], _STRENGTH_ORDER.get(match.confidence, 3), match.view_id)


def filter_matches(item: Item, enabled: list[str] | None = None) -> list[Match]:
    wanted = set(TOGGLES if enabled is None else enabled)
    return sorted((m for m in item.matches if wanted.intersection(m.tiers)), key=match_sort_key)


def match_help(item: Item) -> str:
    tiers = [name for name in TOGGLES if any(name in m.tiers for m in item.matches)]
    return f"{len(item.matches)} items · {', '.join(tiers)}" if tiers else "No matches yet"


def playbook_status(store: Store) -> bool:
    """A missing model output is not a negative match result."""
    load_plugins()
    playbook = {layer.name for layer in layers.all() if layer.tier is Tier.PLAYBOOK}
    return any(fp.layer_id in playbook for fp in store.fingerprints())


def deciding_clue(shared: list[dict], tier: str) -> str:
    names = {
        "fb_pixel": "pixel",
        "gtm_id": "tag manager account",
        "ga_id": "analytics account",
        "aio.campaign_uuid": "campaign record",
        "aio.owner_uuid": "owner record",
        "url.host_path": "page address",
        "contact.phone": "phone number",
        "contact.email": "email address",
        "aio.template_uuid": "template",
        "api_key_id": "conversion account",
        "brand.offer": "brand or offer",
    }
    literal = [s for s in shared if s.get("ns") in names]
    if literal:
        s = literal[0]
        value = str(s["value"])
        if len(value) > 26:
            value = value[:12] + "…" + value[-6:]
        return f"Same {names[s['ns']]} {value}"
    images = {str(s["value"]) for s in shared if s.get("ns", "").startswith("asset.sha256")}
    if images:
        return f"{len(images)} identical file{'s' if len(images) != 1 else ''}"
    return {
        "identity": "Shared account or record",
        "artifact": "Matching content",
        "kit": "Shared template or code",
        "playbook": "Similar persuasion pattern",
    }[tier]


def build_matches(store: Store, groups: Groups, config=DEFAULT) -> dict[str, list[Match]]:
    """Direct matches only; no writes and no mutation of the global method weight table.

    Stored typed links retain their verdict. Additional threshold-passing layers are
    clues, never promoted to a campaign. Demoted and stoplisted values cannot create
    these additional matches or supply their deciding clue.
    """
    load_plugins()
    meta = {layer.name: layer for layer in layers.all()}
    views = {v.view_id: v for v in store.views()}
    table = WeightTable(update_coverage=False)
    table.fit(store, groups.campaign_of)
    demoted, _ = find_demotions(store, config)
    shared_by_tier = _shared_values(store, config)
    evidence = defaultdict(list)
    for e in store.evidence():
        evidence[tuple(sorted((e.view_a, e.view_b)))].append(e)
    relations = {tuple(sorted((r.view_a, r.view_b))): r for r in store.relations()}
    fingerprints = defaultdict(set)
    for fp in store.fingerprints():
        if fp.layer_id in meta:
            fingerprints[fp.view_id].add(fp.layer_id)
    active = {meta[lid].group for ids in fingerprints.values() for lid in ids}
    out = {vid: [] for vid in views}
    for a, b in sorted(evidence.keys() | relations.keys()):
        if a == b or a not in views or b not in views:
            continue
        rows = evidence.get((a, b), [])
        relation = relations.get((a, b))
        typed = relation is not None and relation.type in _VERDICTS
        supported = set(link_tiers(relation, config)) if typed else set()
        allowed = (
            set(link_tiers(relation, config))
            if relation and (typed or relation.type is RelationType.SAME_PLAYBOOK)
            else set(TOGGLES)
        )
        clues = defaultdict(list)
        for e in rows:
            layer = meta.get(e.layer_id)
            if not e.comparable or layer is None or layer.tier not in TOGGLES.values():
                continue
            name = next(name for name, tier in TOGGLES.items() if tier is layer.tier)
            kept = [
                s
                for s in e.shared
                if "value" in s
                and s.get("weight", 1) > 0
                and (s.get("ns"), str(s["value"])) not in demoted
                and not table.is_stoplisted(s.get("ns", ""), str(s["value"]))
            ]
            # For set/hash evidence, an excluded sole value invalidates a stale score.
            score = e.score
            if len(kept) != len(e.shared):
                score = min(score, 1 - prod(1 - float(s.get("weight", 0)) for s in kept))
            if (
                not typed
                and name in allowed
                and score >= getattr(config, name)
                and (kept or not e.shared)
            ):
                supported.add(name)
                clues[name].extend(kept)
            elif typed:
                clues[name].extend(kept)
        if not supported:
            continue
        tiers = tuple(name for name in TOGGLES if name in supported)
        # Reuse the campaign map's value routing, but do not advertise a demoted value
        # or a stoplisted identifier as the clue that decided a match.
        values = []
        for name in tiers:
            mapped = set(
                shared_by_tier.get((a, b, TOGGLES[name]), [])
                + shared_by_tier.get((b, a, TOGGLES[name]), [])
            )
            values.extend(s for s in clues[name] if f"{s.get('ns')}={s['value']}" in mapped)
        # Zero-score comparable layers are not persisted as evidence.
        rejected = {e.layer_id for e in rows if not e.comparable}
        observed = {meta[lid].group for lid in (fingerprints[a] & fingerprints[b]) - rejected}
        verdict = _VERDICTS[relation.type] if typed else "Clue only"
        confidence = (
            relation.confidence
            if relation and (typed or relation.type is RelationType.SAME_PLAYBOOK)
            else ""
        )
        for anchor, other in ((a, b), (b, a)):
            out[anchor].append(
                Match(
                    view_id=other,
                    same_case=views[a].incident_id == views[b].incident_id,
                    verdict=verdict,
                    tiers=tiers,
                    confidence=confidence,
                    observed_groups=len(observed),
                    total_groups=len(active),
                    clue=deciding_clue(values, tiers[0]),
                )
            )
    return {vid: sorted(matches, key=match_sort_key) for vid, matches in out.items()}


def build_library(
    store: Store, groups: Groups, data_dir: Path, previous: Library | None = None
) -> Library:
    meta = read_meta(data_dir)
    incidents = {i.incident_id: i for i in store.incidents()}
    views = {v.view_id: v for v in store.views()}
    added = {iid: _added_at(meta, iid, inc.root) for iid, inc in incidents.items()}
    titles = {vid: view_title(v) for vid, v in views.items()}
    matches = build_matches(store, groups)

    # Name only groups that link more than one item, in the order they were first added.
    multi = {cid: m for cid, m in groups.campaigns.items() if len(m) > 1}
    first = {cid: min(added.get(views[v].incident_id, "") for v in m) for cid, m in multi.items()}
    names = {
        cid: f"Campaign {_letters(n)}"
        for n, cid in enumerate(sorted(multi, key=lambda c: (first[c], c)))
    }
    campaign_of = {v: cid for cid, members in groups.campaigns.items() for v in members}
    kits = {fid: cs for fid, cs in groups.families.items() if len(cs) > 1}
    kit_names = {fid: f"Kit {n + 1}" for n, fid in enumerate(sorted(kits))}
    kit_of = {cid: fid for fid, cs in kits.items() for cid in cs}

    duplicates: dict[str, list[str]] = {}
    for rel in store.relations():
        if rel.type is RelationType.DUPLICATE:
            duplicates.setdefault(rel.view_a, []).append(rel.view_b)
            duplicates.setdefault(rel.view_b, []).append(rel.view_a)
    links = {
        vid: (set(multi.get(campaign_of.get(vid), [])) | set(duplicates.get(vid, []))) - {vid}
        for vid in views
    }
    with_fingerprints = {fp.view_id for fp in store.fingerprints()}

    journeys: dict[str, Journey] = {}
    steps: dict[str, Step] = {}
    for incident_id, (funnel, _co) in (groups.funnel_edges or {}).items():
        edges = [e for e in funnel if e.source in views and e.target in views]
        for number, chain in enumerate(_chains(edges), start=1):
            proofs = []
            for a, b in itertools.pairwise(chain):
                why = next((e.evidence for e in edges if e.source == a and e.target == b), [])
                proofs.append("; ".join(plain_proof(r) for r in why) or "Linked")
            entry = views[chain[0]]
            ads = []
            if entry.kind is ViewKind.PAGE:
                ads = sorted(
                    {
                        str(o.value)
                        for o in store.observations(entry.view_id)
                        if o.namespace in AD_ID and o.value
                    }
                )
            host = next((titles[v][1] for v in chain if titles[v][1]), None)
            journey = Journey(
                ads=ads, steps=[(v, titles[v][0]) for v in chain], proofs=proofs, host=host
            )
            for position, vid in enumerate(chain, start=1):
                journeys[vid] = journey
                steps[vid] = Step(chain=number, position=position, total=len(chain))

    cases = []
    for incident_id, incident in incidents.items():
        items = []
        for view in store.views(incident_id):
            vid = view.view_id
            title, host = titles[vid]
            labels = []
            cid = campaign_of.get(vid)
            if cid in names:
                labels.append(Label("campaign", names[cid]))
            if cid in kit_of:
                labels.append(Label("kit", kit_names[kit_of[cid]]))
            for other in duplicates.get(vid, [])[:1]:
                where = case_title(views[other].incident_id)
                labels.append(Label("duplicate", f"Duplicate · {where}"))
            if not labels:
                enough = vid in with_fingerprints
                labels.append(Label("none", "No link yet" if enough else "Not enough evidence"))
            picture = None
            if view.kind is ViewKind.IMAGE:
                picture = view.primary
            elif view.kind is ViewKind.PAGE:
                picture = next((a for a in view.attachments if a.endswith("screenshot.png")), None)
            # The incident folder can be gone (a store copied from elsewhere): no picture then.
            path = Path(incident.root) / picture if picture else None
            thumbnail = str(path) if path and path.is_file() else None
            note = None
            if previous is not None and previous.get(vid) is not None:
                gained = links[vid] - previous.links.get(vid, set())
                if gained:
                    other = min(gained)
                    note = f"New link to {titles[other][0]}"
            items.append(
                Item(
                    view_id=vid,
                    case_id=incident_id,
                    kind=view.kind,
                    title=title,
                    host=host,
                    labels=labels,
                    risk=risk_for(vid, store.observations(vid)),
                    step=steps.get(vid),
                    thumbnail=thumbnail,
                    library_id=library_id(view, incident.root),
                    note=note,
                    matches=matches[vid],
                )
            )
        # Steps of one journey sit together, in order; everything else keeps its place.
        items.sort(
            key=lambda i: (
                (0, i.step.chain, i.step.position) if i.step else (1, 0, 0),
                KIND_NAMES[i.kind] != "Page",
                i.title.lower(),
            )
        )
        cases.append(
            Case(
                case_id=incident_id,
                title=case_title(incident_id),
                added_at=added[incident_id],
                items=items,
            )
        )
    # Newest first; cases added at the same moment (or with no known time) keep ID order.
    cases.sort(key=lambda c: c.case_id)
    cases.sort(key=lambda c: c.added_at, reverse=True)
    return Library(cases=cases, journeys=journeys, links=links, playbook_run=playbook_status(store))
