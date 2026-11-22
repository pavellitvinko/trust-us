"""Links between ad screenshots of one case: the same post, a shared advertiser or account.

Built from stored image observations only, so existing screenshots gain them without a new
model call. These links never feed campaign grouping: one account can run many ads and
campaigns, so a shared advertiser or account says nothing about which campaign an ad is in.

- same_post: the same advertiser, the same button and shared post text: at least four words,
  one line of them at least three words long. Text on the creative itself (a burned-in caption, a TV logo)
  and the platform's own chrome do not count: different ads reuse them.
- same_account: a Facebook page ID is on both items and agrees.
- different_account: the display name matches but the page IDs differ. No other link.
- possible advertiser: only the display name matches. Not stored per pair (one common name
  can cover thousands of screenshots); `possible_advertisers` lists them on demand.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from .contracts import Observation, State, View, ViewKind

_URL_KEYS = ("final_url", "manifest_url", "saved_from", "har_document", "har_first_party")
_ACCOUNT = re.compile(
    r"^https?://(?:[\w-]+\.)?facebook\.com/[^#]*?[?&](?:view_all_page_id|page_id)=(\d{5,20})",
    re.IGNORECASE,
)
# Whole lines, normalised (casefolded, no punctuation), that every sponsored post or the Ad
# Library shows, so they say nothing about which post a screenshot is of.
_CHROME_LINES = (
    r"sponsorowane|sponsored|reklama|instagram|facebook|www facebook com",
    r"aktywna|nieaktywna|active|inactive|platformy|platforms",
    r"obserwuj|follow|lubię to|like|komentarz|comment|udostępnij|share|wyślij|send",
    r"zobacz tłumaczenie|see translation|teraz|just now",
    r"[\d\s]+(?:tys|mln|k|m)?",  # counters
    r"\d[\d\s]*(?:tys\s*)?(?:wyświetle\w*|views?|polubie\w*|likes?|komentarz\w*)",
    r"\d[\d\s]*(?:tys\s*)?(?:comments?|udostępnie\w*|shares?)",
    r"\d+\s*(?:s|sek|min|m|h|godz|d|dni|dzień|tyg|w|mies|lat|y)",  # "2 dni", "5 h"
    r"(?:identyfikator|library id|data zamieszczenia|started running)\b.*",
)
_CHROME = re.compile(f"^(?:{'|'.join(_CHROME_LINES)})$")
# A neighbouring post's caption, cut short: "Nie będzie żadnej... więcej".
_TRUNCATED = re.compile(r"(?:\.\.\.|…)\s*(?:więcej|more)?\s*$", re.IGNORECASE)
_REGION = re.compile(r"image:region\[([\d.]+),([\d.]+),([\d.]+),([\d.]+)\]")
_MARKS = ("image.logo", "image.domain", "image.claimed_publisher", "image.brand_offer")
MIN_LINE_WORDS = 3  # one shared line this long: "Czas ucieka!" alone is generic
MIN_SHARED_WORDS = 4  # in all, over the shared lines
MAX_POST_BUCKET = 50  # more screenshots than this sharing one line: template text, not a post


@dataclass(frozen=True)
class AdLink:
    kind: str  # same_post | same_account | different_account
    other: str  # the linked view
    evidence: tuple[str, ...]


@dataclass
class _Facts:
    case: str
    advertisers: dict[str, str]  # normalised name -> name as written
    accounts: set[str]
    lines: dict[str, str]  # normalised distinctive post text -> text as written
    ctas: dict[str, str]


@dataclass
class AdLinks:
    links: dict[str, list[AdLink]] = field(default_factory=dict)
    posts: dict[str, set[str]] = field(default_factory=dict)  # view -> direct same_post links
    _facts: dict[str, _Facts] = field(default_factory=dict)
    _by_name: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    def possible_advertisers(self, vid: str, limit: int = 20) -> tuple[list[AdLink], int]:
        """Screenshots of the case with the same advertiser name and no stronger link (or a
        conflicting account), the first `limit` of them, and how many there are in all."""
        facts = self._facts.get(vid)
        if facts is None:
            return [], 0
        linked = {link.other for link in self.links.get(vid, [])}
        others: dict[str, list[str]] = defaultdict(list)
        for name in sorted(facts.advertisers):
            for other in self._by_name.get((facts.case, name), []):
                if other != vid and other not in linked:
                    others[other].append(facts.advertisers[name])
        shown = [
            AdLink(
                "possible_advertiser",
                other,
                (f"Advertiser {_quote(names)} on both", "Only the display name matches"),
            )
            for other, names in sorted(others.items())[:limit]
        ]
        return shown, len(others)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def _present(observations: list[Observation], namespace: str) -> list[Observation]:
    return [
        o
        for o in observations
        if o.namespace == namespace and o.state is State.PRESENT and (o.raw or o.value)
    ]


def _raw(o: Observation) -> str:
    return (o.raw or str(o.value)).strip()


def _box(o: Observation) -> tuple[float, float, float, float] | None:
    match = _REGION.fullmatch(o.source.locator or "")
    return tuple(float(v) for v in match.groups()) if match else None  # type: ignore[return-value]


def account_ids(view: View) -> set[str]:
    """Facebook page IDs from the material's own Facebook URLs (an Ad Library page link)."""
    return {
        m.group(1)
        for key in _URL_KEYS
        if (m := _ACCOUNT.match(str(view.meta.get(key) or "").strip()))
    }


def _creative_zones(observations: list[Observation]) -> list[tuple[float, float]]:
    """Vertical spans of a feed post's creative: from the advertiser line down to its button."""
    heads = [b for o in _present(observations, "image.advertiser") if (b := _box(o))]
    zones = []
    for o in _present(observations, "image.cta"):
        if not (cta := _box(o)):
            continue
        above = [h[1] + h[3] for h in heads if h[1] + h[3] <= cta[1]]
        if above:
            zones.append((max(above), cta[1]))
    return zones


def _inside(box, zones, marks) -> bool:
    if box is None:
        return False
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    in_zone = any(top <= cy <= bottom for top, bottom in zones)
    in_mark = any(m[0] <= cx <= m[0] + m[2] and m[1] <= cy <= m[1] + m[3] for m in marks)
    return in_zone or in_mark


def _facts(view: View, observations: list[Observation]) -> _Facts:
    advertisers = {_norm(_raw(o)): _raw(o) for o in _present(observations, "image.advertiser")}
    advertisers.pop("", None)
    ctas = {_norm(_raw(o)): _raw(o) for o in _present(observations, "image.cta")}
    ctas.pop("", None)
    # Marks on the creative (a TV logo, a news site's domain) are the imagery, not the post.
    mark_obs = [o for ns in _MARKS for o in _present(observations, ns)]
    marks = {_norm(_raw(o)) for o in mark_obs}
    mark_boxes = [b for o in mark_obs if o.namespace != "image.domain" and (b := _box(o))]
    zones = _creative_zones(observations)
    lines = {}
    for o in _present(observations, "image.text"):
        raw = _raw(o)
        if _TRUNCATED.search(raw) or _inside(_box(o), zones, mark_boxes):
            continue
        key = _norm(raw)
        for name in advertisers:  # "Sustain Studio · Obserwuj" is the name and chrome
            key = " ".join(re.sub(rf"\b{re.escape(name)}\b", " ", key).split())
        if key and key not in ctas and key not in marks and not _CHROME.match(key):
            lines[key] = raw
    return _Facts(view.incident_id, advertisers, account_ids(view), lines, ctas)


def _quote(values) -> str:
    return ", ".join(f"“{v}”" for v in values)


def build_ad_links(views: dict[str, View], observations: dict[str, list[Observation]]) -> AdLinks:
    """Links between the image views given, within each case. Views of a case that is gone
    should not be passed: they would link items to something the reader cannot open."""
    facts = {
        vid: _facts(view, observations.get(vid, []))
        for vid, view in views.items()
        if view.kind is ViewKind.IMAGE
    }
    by_name: dict[tuple[str, str], list[str]] = defaultdict(list)
    for vid in sorted(facts):
        for name in facts[vid].advertisers:
            by_name[(facts[vid].case, name)].append(vid)
    out = AdLinks(_facts=facts, _by_name=dict(by_name))

    # Accounts: only views that carry one, which are few, are paired.
    conflicting: set[tuple[str, str]] = set()
    account_pairs = {
        (a, b)
        for vids in by_name.values()
        for a, b in combinations([v for v in vids if facts[v].accounts], 2)
    }
    for a, b in sorted(account_pairs):
        fa, fb = facts[a], facts[b]
        said = _said(fa, fb)
        if fa.accounts & fb.accounts:
            shared = _quote(sorted(fa.accounts & fb.accounts))
            _add(out, "same_account", a, b, (said, f"Account {shared} on both"))
        else:
            conflicting.add((a, b))
            a_ids, b_ids = _quote(sorted(fa.accounts)), _quote(sorted(fb.accounts))
            _add(out, "different_account", a, b, (said, f"Different accounts: {a_ids} and {b_ids}"))

    # Same post: candidates share the case, an advertiser name, a button and a long line.
    buckets: dict[tuple, list[str]] = defaultdict(list)
    for vid in sorted(facts):
        f = facts[vid]
        for name in f.advertisers:
            for cta in f.ctas:
                for line in f.lines:
                    if len(line.split()) >= MIN_LINE_WORDS:
                        buckets[(f.case, name, cta, line)].append(vid)
    post_pairs = {
        pair
        for vids in buckets.values()
        if len(vids) <= MAX_POST_BUCKET
        for pair in combinations(vids, 2)
    }
    for a, b in sorted(post_pairs - conflicting):
        fa, fb = facts[a], facts[b]
        shared = sorted(fa.lines.keys() & fb.lines.keys())
        ctas = sorted(fa.ctas.keys() & fb.ctas.keys())
        if sum(len(line.split()) for line in shared) < MIN_SHARED_WORDS:
            continue
        _add(out, "same_post", a, b, (
            _said(fa, fb),
            f"Same post text: {_quote(fa.lines[s] for s in shared)}",
            f"Same button: {_quote(fa.ctas[c] for c in ctas)}",
        ))  # fmt: skip
        out.posts.setdefault(a, set()).add(b)
        out.posts.setdefault(b, set()).add(a)
    return out


def _said(fa: _Facts, fb: _Facts) -> str:
    names = [fa.advertisers[n] for n in sorted(fa.advertisers.keys() & fb.advertisers.keys())]
    return f"Advertiser {_quote(names)} on both"


def _add(out: AdLinks, kind: str, a: str, b: str, evidence: tuple[str, ...]) -> None:
    out.links.setdefault(a, []).append(AdLink(kind, b, evidence))
    out.links.setdefault(b, []).append(AdLink(kind, a, evidence))
