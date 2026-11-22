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
    Journey,
    Kit,
    Library,
    campaign_heading,
    campaign_why,
    case_key,
    case_title,
    cases_text,
    match_sort_key,
    plain_clue,
    plural,
    short_clue,
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
        **_map_groups(library),
    }


def _map_groups(library: Library) -> dict[str, Any]:
    """What the map's tree needs from the grouping. Worked out once per built Library."""
    if "map_groups" not in library.view_cache:
        library.view_cache["map_groups"] = {
            # Some campaigns are joined by a journey, not by a link.
            "campaignWhy": {
                cid: why.label
                for cid, campaign in library.campaigns.items()
                if (why := campaign_why(library, campaign)).label
            },
            "repeats": [list(c.members) for c in library.campaigns.values() if c.repeat],
            "journeys": {
                vid: [step for step, _ in journey.steps if step != vid]
                for vid, journey in library.journeys.items()
                if len(journey.steps) > 1
            },
        }
    return library.view_cache["map_groups"]


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


def _path(library: Library, journey: Journey | None) -> dict[str, Any] | None:
    """A journey as pictures: the ad it starts from, its pages, why each leads to the next."""
    if journey is None:
        return None
    return {
        "ad": journey.ads[0] if journey.ads else None,
        "steps": [_brief(library.get(vid)) for vid, _ in journey.steps if library.get(vid)],
        "reasons": [visitor_reason(p) for p in journey.proofs],
    }


def _actions(library: Library, item: Item) -> list[dict[str, str]]:
    """Where to look further: the Meta Ad Library, by the ad's ID or by the landing domain."""
    journey = library.journeys.get(item.view_id)
    on_path = [library.get(vid) for vid, _ in journey.steps] if journey else [item]
    library_id = next((i.library_id for i in on_path if i and i.library_id), None)
    if library_id:
        return [
            {"label": "Find the Meta ad in Ad Library", "url": ad_library_url(ad_id=library_id)}
        ]
    if journey and journey.ads and journey.host:
        return [
            {
                "label": f"Search Ad Library for {journey.host}",
                "url": ad_library_url(terms=journey.host),
                "help": "The page address carries an Ads Manager ID, which the Ad Library cannot "
                "look up, so this searches for ads that mention the landing domain.",
            }
        ]
    return []


_AD_LINK_NAMES = {
    "same_post": "Same post",
    "same_account": "Same account",
    "possible_advertiser": "Possibly the same advertiser",
    "different_account": "Same name, different accounts",
}
_AD_LINK_ORDER = list(_AD_LINK_NAMES)
_LIBRARY_SOURCES = {
    "metadata": "From the saved material",
    "ocr": "Read on this screenshot",
    ("same_post", "ocr"): "Read on another screenshot of the same post",
    ("same_post", "metadata"): "From the saved material of another screenshot of the same post",
}
MAX_ADVERTISER_PEERS = 20


def _ad(library: Library, item: Item) -> dict[str, Any] | None:
    """The ad's Library ID and advertiser, where each was read, and the screenshots it
    shares a post, account or advertiser name with. None of these names a campaign."""
    peers, total = library.ads.possible_advertisers(item.view_id, MAX_ADVERTISER_PEERS)
    links = []
    for link in [
        *sorted(item.ad_links, key=lambda x: (_AD_LINK_ORDER.index(x.kind), x.other)),
        *peers,
    ]:
        other = library.get(link.other)
        if other is not None:
            links.append(
                {
                    **_brief(other),
                    "kind": link.kind,
                    "name": _AD_LINK_NAMES[link.kind],
                    "evidence": list(link.evidence),
                }
            )
    if not (item.library_id or item.advertiser or links):
        return None
    via = library.get(item.library_via) if item.library_via else None
    source = item.library_source or ""
    return {
        "libraryId": item.library_id,
        "librarySource": _LIBRARY_SOURCES.get(
            (source, item.library_origin) if source == "same_post" else source, ""
        ),
        "libraryEvidence": [
            {"text": e.raw, "where": e.locator or "", "file": e.file or ""}
            for e in item.library_evidence
        ],
        "via": _brief(via) if via else None,
        "advertiser": item.advertiser,
        "links": links,
        "moreAdvertiserPeers": total - len(peers),
    }


def detail(library: Library, item: Item) -> dict[str, Any]:
    """Everything the item page shows."""

    def own_reason(kind: str) -> str:
        """What joins this item to its group: its own strongest link of that kind."""
        return next((m.reason for m in item.matches if m.kind == kind), "")

    campaign = library.campaigns.get(item.campaign or "")
    kit = library.kits.get(item.kit or "")
    groups = [
        {"kind": kind, "id": gid or "", "name": name, "reason": own_reason(kind), "title": title}
        for kind, gid, name, title in (
            ("campaign", item.campaign, campaign_name(library, item),
             campaign and f"{campaign_heading(campaign.title)} ({plural(len(campaign.members), 'page')})"),
            ("kit", item.kit, kit_name(library, item), kit and kit.title),
        )
        if name
    ]  # fmt: skip
    journey = library.journeys.get(item.view_id)
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
    case = next(c for c in library.cases if c.case_id == item.case_id)
    return {
        **summary(library, item),
        "groups": groups,
        "warningSigns": item.risk.signs,
        "unchecked": [UNCHECKED_PLAIN.get(u, u.lower()) for u in item.risk.unchecked],
        "path": _path(library, journey),
        "matches": matches,
        "sameCase": [_brief(i) for i in case.items if i.view_id != item.view_id],
        "actions": _actions(library, item),
        "ad": _ad(library, item),
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


def _hosts(items: list[Item]) -> list[str]:
    return sorted({i.host for i in items if i.host})


def _hosts_text(hosts: list[str], most: int = 3) -> str:
    more = f" and {len(hosts) - most} more" if len(hosts) > most else ""
    return ", ".join(hosts[:most]) + more


def _case_ids(items: list[Item]) -> list[str]:
    return sorted({i.case_id for i in items}, key=case_key)


def _signs_cell(items: list[Item]) -> dict[str, Any]:
    """The warning-signs cell: how many signs, or why there are none."""
    signs = list(dict.fromkeys(s for i in items for s in i.risk.signs))
    if signs:
        text = plural(len(signs), "warning sign")
    else:
        text = "none found" if any(_checked(i) for i in items) else "not checked"
    return {"count": len(signs), "text": text}


def _signs_detail(items: list[Item]) -> dict[str, Any]:
    """Each sign once, with how many of the group's items show it."""
    word = "page" if all(i.kind.value == "page" for i in items) else "item"
    signs = list(dict.fromkeys(s for i in items for s in i.risk.signs))
    rows = []
    for sign in signs:
        on = sum(1 for i in items if sign in i.risk.signs)
        where = f"on {on} of {plural(len(items), word)}" if len(items) > 1 else ""
        rows.append({"text": sign, "where": where})
    checked = any(_checked(i) for i in items)
    return {
        "signs": rows,
        "empty": "" if rows else ("None found." if checked else "Not checked."),
    }


def _members(library: Library, member_ids: list[str]) -> tuple[list[Item], Journey | None]:
    """The items in the order a reader should meet them, and the journey they all are on."""
    items = [i for v in member_ids if (i := library.get(v))]
    journeys = {id(library.journeys[i.view_id]) for i in items if i.view_id in library.journeys}
    if len(items) > 1 and len(journeys) == 1 and all(i.step for i in items):
        journey = library.journeys[items[0].view_id]
        return sorted(items, key=lambda i: i.step.position), journey
    return sorted(items, key=lambda i: (case_key(i.case_id), i.title.lower(), i.view_id)), None


def _repeat_once(library: Library, campaign: Campaign) -> list[Item]:
    """A kit lists each page once: a page found in several cases is one item."""
    items = [i for v in campaign.members if (i := library.get(v))]
    return items[:1] if campaign.repeat else items


def _kit_items(library: Library, kit: Kit) -> list[Item]:
    return [i for c in kit.campaigns for i in _repeat_once(library, library.campaigns[c])]


def _row(library: Library, campaign: Campaign) -> dict[str, Any]:
    """One row of the Campaigns table."""
    items, journey = _members(library, campaign.members)
    why = campaign_why(library, campaign)
    hosts = _hosts(items)
    sub = _items_text(items) + (", one after another" if journey else "")
    if hosts:
        sub += f" · {_hosts_text(hosts)}"
    return {
        "id": campaign.campaign_id,
        "name": campaign.name,
        "title": campaign.title,
        "sub": sub,
        "picture": _brief(items[0]),
        "linkedBy": why.label,
        "strength": why.strength,
        "cases": ", ".join(_case_ids(items)),
        "signs": _signs_cell(items),
        "kit": library.kits[campaign.kit].name if campaign.kit in library.kits else "",
        "items": len(items),
    }


def _kit_row(library: Library, kit: Kit) -> dict[str, Any]:
    items = _kit_items(library, kit)
    named = sum(1 for c in kit.campaigns if library.campaigns[c].name)
    lone = sum(
        len(_repeat_once(library, library.campaigns[c]))
        for c in kit.campaigns
        if not library.campaigns[c].name
    )
    parts = [plural(named, "campaign")] if named else []
    if lone:
        parts.append(
            plural(lone, "lone page" if all(i.kind.value == "page" for i in items) else "lone item")
        )
    return {
        "id": kit.kit_id,
        "name": kit.name,
        "title": kit.title,
        "sub": f"{_items_text(items)} · {' and '.join(parts)}",
        "picture": _brief(items[0]),
        "linkedBy": short_clue(kit.linked_by[0]) if kit.linked_by else "",
        "cases": ", ".join(_case_ids(items)),
        "signs": _signs_cell(items),
    }


def campaigns_payload(library: Library) -> dict[str, Any]:
    """The Campaigns table: one row per campaign, one per kit, and what is left over."""
    named = [c for c in library.campaigns.values() if c.name]
    first = {c.campaign_id: c.first_seen for c in named}
    rows = sorted(
        (_row(library, c) for c in named),
        key=lambda r: (-r["items"], _STRENGTH_RANK.get(r["strength"], 3), first[r["id"]], r["id"]),
    )
    kits = [_kit_row(library, k) for k in library.kits.values()]
    grouped = {v for c in library.campaigns.values() for v in c.members}
    rest = [i for i in library.items() if i.view_id not in grouped]
    repeats = sum(1 for c in library.campaigns.values() if c.repeat)
    count = f"{plural(len(rows), 'campaign')} in {plural(len(library.items()), 'item')}"
    if kits:
        count += f" · {plural(len(kits), 'shared kit')}"
    return {
        "count": count,
        "campaigns": rows,
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


def _pairs(library: Library, items: list[Item], kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    """Every link of the given kinds between two of the items, each pair once."""
    inside = {i.view_id for i in items}
    seen: dict[tuple[str, str], Any] = {}
    for item in items:
        for m in sorted(item.matches, key=match_sort_key):
            key = tuple(sorted((item.view_id, m.view_id)))
            if m.view_id in inside and m.kind in kinds and key not in seen:
                seen[key] = m
    return [
        {
            "a": _brief(library.get(a)),
            "b": _brief(library.get(b)),
            "kind": m.kind,
            "name": LINK_NAMES[m.kind],
            "reason": m.reason,
            "strength": "" if m.strength == "Not assessed" else m.strength,
        }
        for (a, b), m in seen.items()
    ]


def _possible(library: Library, items: list[Item]) -> list[dict[str, Any]]:
    """Weak resemblances to material outside the group, each other item once."""
    inside = {i.view_id for i in items}
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        for m in item.matches:
            other = library.get(m.view_id)
            if m.kind == "possible" and m.view_id not in inside and other and m.view_id not in out:
                out[m.view_id] = {
                    "a": _brief(item),
                    "b": _brief(other),
                    "kind": m.kind,
                    "name": LINK_NAMES[m.kind],
                    "reason": m.reason,
                    "strength": "",
                }
    return list(out.values())


def _evidence(items: list[Item]) -> list[dict[str, str]]:
    return [
        {"item": i.title, "kind": f.kind, "summary": f.summary, "source": f.source}
        for i in items
        for f in i.risk.findings
    ]


def _facts(items: list[Item], journey: Journey | None, first_seen: str) -> list[dict[str, str]]:
    hosts = _hosts(items)
    facts = [{"label": "Made of", "value": _items_text(items)}]
    if hosts:
        facts.append(
            {
                "label": "Website" if len(hosts) == 1 else "Websites",
                "value": ", ".join(hosts),
            }
        )
    facts.append({"label": "Found in", "value": cases_text(_case_ids(items))})
    if first := _day(first_seen):
        facts.append({"label": "First seen", "value": first})
    if journey and journey.ads:
        facts.append(
            {
                "label": "Visitors arrive",
                "value": "From a Facebook ad: the first page's address carries the ad's ID",
            }
        )
    elif journey:
        facts.append(
            {"label": "Visitors arrive", "value": "Through the pages in order, shown below"}
        )
    return facts


def _all_actions(library: Library, items: list[Item]) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for item in items:
        for action in _actions(library, item):
            seen.setdefault(action["url"], action)
    return list(seen.values())


def _summary(items: list[Item], tail: str) -> str:
    """One line under a group's title: what it is made of, where, and what holds it together."""
    hosts = _hosts(items)
    where = f" on {plural(len(hosts), 'website')}" if hosts else ""
    return f"{_items_text(items)}{where}, {tail}"


def campaign_detail(library: Library, campaign: Campaign) -> dict[str, Any]:
    """Everything a campaign's page shows."""
    items, journey = _members(library, campaign.members)
    why = campaign_why(library, campaign)
    kit = library.kits.get(campaign.kit or "")
    kit_info = None
    if kit:
        inside = {i.view_id for i in items}
        others = [i for i in _kit_items(library, kit) if i.view_id not in inside]
        kit_info = {
            "id": kit.kit_id,
            "name": kit.name,
            "why": plain_clue(kit.linked_by[0]) if kit.linked_by else "",
            "text": f"Built with the same kit as {plural(len(others), 'other item')}",
            "others": [_brief(i) for i in others],
        }
    return {
        "kind": "campaign",
        "id": campaign.campaign_id,
        "name": campaign.name,
        "title": campaign_heading(campaign.title),
        "short": campaign.title,
        "summary": _summary(items, f"linked by {why.label}"),
        "pictures": [_brief(i) for i in items],
        "facts": _facts(items, journey, campaign.first_seen),
        "why": why._asdict(),
        **_signs_detail(items),
        "actions": _all_actions(library, items),
        "path": _path(library, journey),
        "members": [_brief(i) | {"host": i.host or ""} for i in items],
        "pairs": _pairs(library, items, ("campaign", "duplicate")),
        "kit": kit_info,
        "campaigns": [],
        "possible": _possible(library, items),
        "evidence": _evidence(items),
    }


def kit_detail(library: Library, kit: Kit) -> dict[str, Any]:
    """Everything a kit's page shows: what joins it, its campaigns and its lone pages."""
    items = _kit_items(library, kit)
    named = [library.campaigns[c] for c in kit.campaigns if library.campaigns[c].name]
    first = min((library.campaigns[c].first_seen for c in kit.campaigns), default="")
    return {
        "kind": "kit",
        "id": kit.kit_id,
        "name": kit.name,
        "title": kit.title,
        "short": kit.name,
        "summary": _summary(items, _kit_row(library, kit)["sub"].split(" · ", 1)[-1]),
        "pictures": [_brief(i) for i in items],
        "facts": _facts(items, None, first),
        "why": {
            "text": plain_clue(kit.linked_by[0])
            if kit.linked_by
            else "Built from the same template",
            "label": short_clue(kit.linked_by[0]) if kit.linked_by else "",
            "strength": "",
            "note": "",
        },
        **_signs_detail(items),
        "actions": _all_actions(library, items),
        "path": None,
        "members": [_brief(i) | {"host": i.host or ""} for i in items],
        "pairs": _pairs(library, items, ("kit",)),
        "kit": None,
        "campaigns": [_row(library, c) for c in named],
        "possible": _possible(library, items),
        "evidence": _evidence(items),
    }


def explain_payload() -> dict[str, Any]:
    return {"groups": EXPLAIN, "links": LINK_NAMES}
