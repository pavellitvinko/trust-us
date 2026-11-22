"""Links between ad screenshots: the same post, and a shared advertiser or account.

Built from stored image observations only, so existing screenshots gain them without a new
model call. These links never feed campaign grouping: one account can run many ads and
campaigns, so a shared advertiser or account says nothing about which campaign an ad is in.

- same_post: the same advertiser, the same distinctive post text and the same button. A
  Library ID read on one screenshot is shown on the others of the same post, with its source.
- same_account: a stable page or account ID is on both items and agrees.
- different_account: the display name matches but the account IDs differ. No link.
- possible_advertiser: only the display name matches.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .contracts import Observation, State, View, ViewKind

_URL_KEYS = ("final_url", "manifest_url", "saved_from", "har_document", "har_first_party")
_ACCOUNT = re.compile(r"[?&](?:view_all_page_id|page_id)=(\d{5,20})")
# Lines that every sponsored post shows, or that belong to another post or to the Library.
_CHROME = re.compile(
    r"^(?:sponsorowane|sponsored|reklama|instagram|facebook|www\.facebook\.com|aktywna|active)$"
    r"|^(?=.*\d)[\d\s.,:+]+(?:\s*(?:tys|k|m)\.?)?$"  # counters, times, "1,2 tys."
    r"|^\d[\d\s.,]*\s*(?:wyświetle\w*|views?|polubie\w*|likes?|komentarz\w*|comments?)$"
    r"|\.\.\.|…|\bwięcej$|\bmore$"  # a truncated caption of a neighbouring post
    r"|identyfikator|library id|data zamieszczenia|started running|platformy|platforms",
    re.IGNORECASE,
)
_MARKS = ("image.logo", "image.domain", "image.claimed_publisher", "image.brand_offer")
MIN_SHARED_WORDS = 4  # "Czas ucieka!" alone is generic; a whole caption is not


@dataclass(frozen=True)
class AdLink:
    kind: str  # same_post | same_account | different_account | possible_advertiser
    other: str  # the linked view
    evidence: tuple[str, ...]


@dataclass
class _Facts:
    advertisers: dict[str, str]  # normalised name -> name as written
    accounts: set[str]
    lines: dict[str, str]  # normalised distinctive text -> text as written
    ctas: dict[str, str]


@dataclass
class AdLinks:
    links: dict[str, list[AdLink]] = field(default_factory=dict)
    posts: dict[str, set[str]] = field(default_factory=dict)  # view -> views of the same post


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


def account_ids(view: View, observations: list[Observation]) -> set[str]:
    """Stable page or account IDs: from the material's URLs, or an `ad.page_id` observation."""
    ids = {
        m.group(1) for key in _URL_KEYS for m in _ACCOUNT.finditer(str(view.meta.get(key) or ""))
    }
    ids |= {str(o.value) for o in _present(observations, "ad.page_id")}
    return ids


def _facts(view: View, observations: list[Observation]) -> _Facts:
    advertisers = {_norm(_raw(o)): _raw(o) for o in _present(observations, "image.advertiser")}
    advertisers.pop("", None)
    ctas = {_norm(_raw(o)): _raw(o) for o in _present(observations, "image.cta")}
    ctas.pop("", None)
    # Marks on the creative (a TV logo, a news site's domain) are the imagery, not the post:
    # different ads reuse them, so they never count as shared post text.
    marks = {_norm(_raw(o)) for ns in _MARKS for o in _present(observations, ns)}
    lines = {}
    for o in _present(observations, "image.text"):
        raw = _raw(o)
        key = _norm(raw)
        if key and key not in advertisers | ctas.keys() | marks and not _CHROME.search(raw):
            lines[key] = raw
    return _Facts(advertisers, account_ids(view, observations), lines, ctas)


def _quote(values) -> str:
    return ", ".join(f"“{v}”" for v in values)


def build_ad_links(views: dict[str, View], observations: dict[str, list[Observation]]) -> AdLinks:
    facts = {
        vid: _facts(view, observations.get(vid, []))
        for vid, view in views.items()
        if view.kind is ViewKind.IMAGE
    }
    by_name: dict[str, list[str]] = defaultdict(list)
    for vid, f in facts.items():
        for name in f.advertisers:
            by_name[name].append(vid)
    pairs = {
        tuple(sorted((a, b))) for vids in by_name.values() for a in vids for b in vids if a < b
    }

    out = AdLinks()
    for a, b in sorted(pairs):
        fa, fb = facts[a], facts[b]
        names = [fa.advertisers[n] for n in sorted(fa.advertisers.keys() & fb.advertisers.keys())]
        said = f"Advertiser {_quote(names)} on both"
        if fa.accounts and fb.accounts:
            if fa.accounts & fb.accounts:
                _add(
                    out,
                    "same_account",
                    a,
                    b,
                    (said, f"Account {_quote(sorted(fa.accounts & fb.accounts))} on both"),
                )
            else:
                # The same display name on two different accounts: not the same advertiser.
                _add(out, "different_account", a, b, (
                    said,
                    f"Different accounts: {_quote(sorted(fa.accounts))} and {_quote(sorted(fb.accounts))}",
                ))  # fmt: skip
                continue
        else:
            _add(out, "possible_advertiser", a, b, (said, "Only the display name matches"))
        shared = sorted(fa.lines.keys() & fb.lines.keys())
        ctas = sorted(fa.ctas.keys() & fb.ctas.keys())
        if ctas and sum(len(s.split()) for s in shared) >= MIN_SHARED_WORDS:
            _add(out, "same_post", a, b, (
                said,
                f"Same post text: {_quote(fa.lines[s] for s in shared)}",
                f"Same button: {_quote(fa.ctas[c] for c in ctas)}",
            ))  # fmt: skip
    out.posts = _components(out)
    return out


def _add(out: AdLinks, kind: str, a: str, b: str, evidence: tuple[str, ...]) -> None:
    out.links.setdefault(a, []).append(AdLink(kind, b, evidence))
    out.links.setdefault(b, []).append(AdLink(kind, a, evidence))


def _components(out: AdLinks) -> dict[str, set[str]]:
    """Screenshots of one post, joined through same_post links."""
    seen: dict[str, set[str]] = {}
    for start in out.links:
        if start in seen:
            continue
        group, todo = {start}, [start]
        while todo:
            vid = todo.pop()
            for link in out.links.get(vid, []):
                if link.kind == "same_post" and link.other not in group:
                    group.add(link.other)
                    todo.append(link.other)
        for vid in group:
            seen[vid] = group
    return {vid: group for vid, group in seen.items() if len(group) > 1}
