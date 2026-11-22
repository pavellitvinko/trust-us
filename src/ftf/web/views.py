"""What the web screens show, as plain JSON-ready dicts built from the Library.

Everything a reader sees is worded here or in ``ftf.library``: the screens only lay it out.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from urllib.parse import quote

from ..library import (
    EXPLAIN,
    KIND_NAMES,
    LINK_NAMES,
    UNCHECKED_PLAIN,
    Campaign,
    Item,
    Library,
    campaign_why,
    case_key,
    case_title,
    cases_text,
    plain_clue,
    plural,
    visitor_reason,
)

AD_LIBRARY = "https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
MAP_WIDTH = 1150  # groups wrap onto a new row past this width (map units)


def ad_library_url(ad_id: str | None = None, terms: str | None = None) -> str:
    """Meta's public Ad Library: one ad by its Library ID, or a keyword search in Poland."""
    if ad_id:
        return f"{AD_LIBRARY}&id={quote(ad_id)}"
    return (
        f"{AD_LIBRARY}&country=PL&media_type=all&search_type=keyword_unordered"
        f"&q={quote(terms or '')}"
    )


def _label(item: Item, kind: str) -> str:
    return next((label.text for label in item.labels if label.kind == kind), "")


def campaign_name(library: Library, item: Item) -> str:
    campaign = library.campaigns.get(item.campaign or "")
    return (campaign.name or "") if campaign else ""


def kit_name(library: Library, item: Item) -> str:
    kit = library.kits.get(item.kit or "")
    return kit.name if kit else ""


def _firm(item: Item) -> list:
    return [m for m in item.matches if m.kind != "possible"]


def summary(library: Library, item: Item) -> dict[str, Any]:
    """One row of the Library table."""
    duplicate = _label(item, "duplicate")
    return {
        "id": item.view_id,
        "title": item.title,
        "type": KIND_NAMES[item.kind],
        "host": item.host or "",
        "case": case_title(item.case_id),
        "caseId": item.case_id,
        "added": library.added(item.case_id)[:10],
        "campaign": campaign_name(library, item),
        "campaignId": item.campaign or "",
        "kit": kit_name(library, item),
        "kitId": item.kit if kit_name(library, item) else "",
        "duplicateOf": duplicate.split(" · ", 1)[-1] if duplicate else "",
        "comparable": item.labels[0].text != "Not enough evidence",
        "signs": len(item.risk.signs),
        # Every kind unchecked: nothing was looked at, so "none found" would mislead.
        "checked": len(item.risk.unchecked) < len(UNCHECKED_PLAIN),
        "links": len(_firm(item)),
        "possible": len(item.matches) - len(_firm(item)),
        "picture": bool(item.thumbnail),
        "note": item.note or "",
    }


def map_payload(library: Library) -> dict[str, Any]:
    """Items joined by a firm link, placed group by group: small groups on a circle, pairs flat."""
    items = {i.view_id: i for i in library.items()}
    edges: dict[tuple[str, str], Any] = {}
    for item in items.values():
        for match in _firm(item):
            key = tuple(sorted((item.view_id, match.view_id)))
            if match.view_id in items and key not in edges:
                edges[key] = match
    parent = {vid: vid for pair in edges for vid in pair}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for vid in sorted(parent, key=lambda v: (library.added(items[v].case_id), v), reverse=True):
        groups.setdefault(find(vid), []).append(vid)

    nodes, x0, y0, row = [], 0.0, 0.0, 0.0
    for members in sorted(groups.values(), key=len, reverse=True):
        n = len(members)
        flat = n == 2
        radius = 165 if flat else max(250, 90 * n / math.pi)
        width = 2 * radius + 220
        if x0 and x0 + width > MAP_WIDTH:
            x0, y0, row = 0.0, y0 + row + 40, 0.0
        cx, cy = x0 + width / 2, y0 + (0 if flat else radius) + 70
        for k, vid in enumerate(members):
            angle = math.pi * (1 - k) if flat else -math.pi / 2 + 2 * math.pi * k / n
            item = items[vid]
            nodes.append(
                {
                    "id": vid,
                    "x": round(cx + radius * math.cos(angle)),
                    "y": round(cy + radius * math.sin(angle)),
                    "title": item.title,
                    "case": case_title(item.case_id),
                    "signs": len(item.risk.signs),
                    "picture": bool(item.thumbnail),
                }
            )
        x0 += width
        row = max(row, (0 if flat else 2 * radius) + 150)
    return {
        "nodes": nodes,
        "edges": [
            {"a": a, "b": b, "kind": m.kind, "reason": m.reason, "label": m.label}
            for (a, b), m in edges.items()
        ],
    }


def library_payload(library: Library) -> dict[str, Any]:
    items = library.items()
    rows = [summary(library, i) for i in items]
    return {
        "items": rows,
        "cases": len(library.cases),
        "linked": sum(1 for r in rows if r["links"]),
        "map": map_payload(library),
    }


def _brief(item: Item) -> dict[str, Any]:
    return {
        "id": item.view_id,
        "title": item.title,
        "type": KIND_NAMES[item.kind],
        "case": case_title(item.case_id),
        "picture": bool(item.thumbnail),
    }


def detail(library: Library, item: Item) -> dict[str, Any]:
    """Everything the item page shows."""

    def own_reason(kind: str) -> str:
        """What joins this item to its group: its own strongest link of that kind."""
        return next((m.reason for m in item.matches if m.kind == kind), "")

    groups = [
        {"kind": kind, "id": gid or "", "name": name, "reason": own_reason(kind)}
        for kind, gid, name in (
            ("campaign", item.campaign, campaign_name(library, item)),
            ("kit", item.kit, kit_name(library, item)),
        )
        if name
    ]
    journey = library.journeys.get(item.view_id)
    path = None
    if journey:
        path = {
            "ad": journey.ads[0] if journey.ads else None,
            "steps": [_brief(library.get(vid)) for vid, _ in journey.steps if library.get(vid)],
            "reasons": [visitor_reason(p) for p in journey.proofs],
        }
    matches = []
    for match in item.matches:
        other = library.get(match.view_id)
        if other is None:
            continue
        matches.append(
            {
                **_brief(other),
                "kind": match.kind,
                "name": LINK_NAMES[match.kind],
                "reason": match.reason,
                "strength": "" if match.strength == "Not assessed" else match.strength,
                "sameCase": match.same_case,
            }
        )
    on_path = [library.get(vid) for vid, _ in journey.steps] if journey else [item]
    library_id = next((i.library_id for i in on_path if i and i.library_id), None)
    actions = []
    if library_id:
        actions.append(
            {"label": "Find the Meta ad in Ad Library", "url": ad_library_url(ad_id=library_id)}
        )
    elif journey and journey.ads and journey.host:
        actions.append(
            {
                "label": f"Search Ad Library for {journey.host}",
                "url": ad_library_url(terms=journey.host),
                "help": "The page address carries an Ads Manager ID, which the Ad Library cannot "
                "look up, so this searches for ads that mention the landing domain.",
            }
        )
    case = next(c for c in library.cases if c.case_id == item.case_id)
    return {
        **summary(library, item),
        "groups": groups,
        "warningSigns": item.risk.signs,
        "unchecked": [UNCHECKED_PLAIN.get(u, u.lower()) for u in item.risk.unchecked],
        "path": path,
        "matches": matches,
        "sameCase": [_brief(i) for i in case.items if i.view_id != item.view_id],
        "actions": actions,
        "findings": [
            {"kind": f.kind, "summary": f.summary, "source": f.source} for f in item.risk.findings
        ],
    }


_STRENGTH_RANK = {"Strong": 0, "Moderate": 1, "Weak": 2, "": 3}
_KIND_WORDS = {"Page": "page", "Image": "picture", "Video": "video"}


def _day(iso: str) -> str:
    """ "20 Nov 2026" from an ISO time; empty when the time is unknown."""
    try:
        return datetime.fromisoformat(iso).strftime("%-d %b %Y")
    except ValueError:
        return ""


def _checked(item: Item) -> bool:
    return len(item.risk.unchecked) < len(UNCHECKED_PLAIN)


def _items_text(items: list[Item]) -> str:
    """ "2 pages", "1 page and 2 pictures": what the group is made of."""
    counts: dict[str, int] = {}
    for item in items:
        word = _KIND_WORDS[KIND_NAMES[item.kind]]
        counts[word] = counts.get(word, 0) + 1
    parts = [plural(n, word) for word, n in counts.items()]
    return " and ".join(parts) if len(parts) < 3 else ", ".join(parts)


def _signs(items: list[Item]) -> dict[str, Any]:
    signs = list(dict.fromkeys(s for i in items for s in i.risk.signs))
    checked = any(_checked(i) for i in items)
    if signs:
        text = plural(len(signs), "warning sign")
    else:
        text = "No warning signs found" if checked else "Warning signs not checked"
    return {"count": len(signs), "text": text, "examples": signs[:2], "checked": checked}


def _members(library: Library, campaign: Campaign) -> tuple[list[Item], bool]:
    """The members in the order a reader should meet them, and whether that is a journey."""
    items = [i for v in campaign.members if (i := library.get(v))]
    journeys = {id(library.journeys[i.view_id]) for i in items if i.view_id in library.journeys}
    if len(items) > 1 and len(journeys) == 1 and all(i.step for i in items):
        return sorted(items, key=lambda i: i.step.position), True
    return sorted(items, key=lambda i: (case_key(i.case_id), i.title.lower(), i.view_id)), False


def _card(library: Library, campaign: Campaign) -> dict[str, Any]:
    items, journey = _members(library, campaign)
    hosts = sorted({i.host for i in items if i.host})
    facts = [_items_text(items)]
    if hosts:
        facts.append(
            f"{plural(len(hosts), 'website')} ({', '.join(hosts[:3])}"
            f"{f' and {len(hosts) - 3} more' if len(hosts) > 3 else ''})"
        )
    facts.append(("in " if len(campaign.cases) > 1 else "") + cases_text(campaign.cases))
    if first := _day(campaign.first_seen):
        facts.append(f"first seen {first}")
    text, strength, note = campaign_why(library, campaign)
    kit = library.kits.get(campaign.kit or "")
    kit_info = None
    if kit:
        others = sum(
            1
            for c in kit.campaigns
            if c != campaign.campaign_id
            for _ in _kit_members(library, library.campaigns[c])
        )
        kit_info = {
            "id": kit.kit_id,
            "name": kit.name,
            "text": f"Built with the same kit as {plural(others, 'other item')}",
        }
    return {
        "id": campaign.campaign_id,
        "name": campaign.name,
        "title": campaign.title,
        "facts": " · ".join(facts),
        "items": len(items),
        "why": {"text": text, "strength": strength, "note": note},
        "journey": journey,
        "members": [_brief(i) for i in items],
        "signs": _signs(items),
        "kit": kit_info,
    }


def _kit_members(library: Library, campaign: Campaign) -> list[Item]:
    """A kit lists each page once: a page found in several cases is one item."""
    items = [i for v in campaign.members if (i := library.get(v))]
    return items[:1] if campaign.repeat else items


def campaigns_payload(library: Library) -> dict[str, Any]:
    """The Campaigns screen: one card per campaign, the kits, and what is left over."""
    named = [c for c in library.campaigns.values() if c.name]
    cards = [_card(library, c) for c in named]
    cards.sort(
        key=lambda c: (
            -c["items"],
            _STRENGTH_RANK.get(c["why"]["strength"], 3),
            next(x.first_seen for x in named if x.campaign_id == c["id"]),
            c["id"],
        )
    )
    kits = []
    for kit in library.kits.values():
        items = [i for c in kit.campaigns for i in _kit_members(library, library.campaigns[c])]
        names = {
            v: c.name
            for cid in kit.campaigns
            if (c := library.campaigns[cid]).name
            for v in c.members
        }
        kits.append(
            {
                "id": kit.kit_id,
                "name": kit.name,
                "why": plain_clue(kit.linked_by[0]) if kit.linked_by else "",
                "facts": f"{_items_text(items)} · " + cases_text({i.case_id for i in items}),
                "members": [{**_brief(i), "campaign": names.get(i.view_id, "")} for i in items],
            }
        )
    grouped = {v for c in library.campaigns.values() for v in c.members}
    rest = [i for i in library.items() if i.view_id not in grouped]
    repeats = sum(1 for c in library.campaigns.values() if c.repeat)
    count = f"{plural(len(cards), 'campaign')} in {plural(len(library.items()), 'item')}"
    if kits:
        count += f" · {plural(len(kits), 'shared kit')}"
    return {
        "count": count,
        "campaigns": cards,
        "kits": kits,
        "unlinked": (
            f"{plural(len(rest), 'item')} {'is' if len(rest) == 1 else 'are'}n't linked to "
            "anything yet."
            if rest
            else ""
        ),
        "repeats": (
            f"{plural(repeats, 'page')} {'was' if repeats == 1 else 'were'} found in more than "
            "one case. A page found twice is still one page, so "
            + (
                "it isn't counted as a campaign."
                if repeats == 1
                else "they aren't counted as campaigns."
            )
            if repeats
            else ""
        ),
    }


def explain_payload() -> dict[str, Any]:
    return {"groups": EXPLAIN, "links": LINK_NAMES}
