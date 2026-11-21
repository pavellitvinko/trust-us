"""Read ads out of whatever the Ad Library page loads: its HTML and its GraphQL responses.

The parser keys only on objects that carry `ad_archive_id` and a `snapshot`, wherever they
sit, so a change in how Meta nests its responses does not break it. Unknown fields are ignored.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from .models import SOURCE_META, LibraryAd, Media

_JSON_SCRIPT = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.DOTALL)
_PLACEHOLDER = re.compile(
    r"^\s*\{\{[^}]*\}\}\s*$"
)  # dynamic-creative macros such as {{product.brand}}
LIBRARY_URL = "https://www.facebook.com/ads/library/?id={}"


def json_documents(text: str) -> Iterator[Any]:
    """Every JSON document in a response body: HTML script blocks, or one or more JSON lines."""
    stripped = text.lstrip()
    if stripped.startswith("<"):
        for body in _JSON_SCRIPT.findall(text):
            if "ad_archive_id" in body or "captcha_required" in body:
                try:
                    yield json.loads(body)
                except ValueError:
                    continue
        return
    for line in text.splitlines():
        line = line.strip().removeprefix("for (;;);")
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _walk(obj: Any) -> Iterator[dict[str, Any]]:
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def captcha_required(text: str) -> bool:
    """True when the page says it wants a CAPTCHA before showing results."""
    for doc in json_documents(text):
        for node in _walk(doc):
            if node.get("captcha_required") is True:
                return True
    return False


def _date(value: Any) -> str | None:
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def _text(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("text")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or _PLACEHOLDER.match(value):
        return None
    return value


def _str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _add(items: list[str], value: Any) -> None:
    text = _text(value)
    if text and text not in items:
        items.append(text)


def _add_url(items: list[str], value: Any) -> None:
    if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in items:
        items.append(value)


def _media(snapshot: dict[str, Any], parts: list[dict[str, Any]]) -> list[Media]:
    out: list[Media] = []
    seen: set[str] = set()

    def add(kind: str, url: Any) -> None:
        if isinstance(url, str) and url.startswith("https://") and url not in seen:
            seen.add(url)
            out.append(Media(kind=kind, url=url))

    for part in parts:
        add("image", part.get("original_image_url") or part.get("resized_image_url"))
        add("video", part.get("video_hd_url") or part.get("video_sd_url"))
        add("video_preview", part.get("video_preview_image_url"))
    for key in ("images", "extra_images"):
        for image in snapshot.get(key) or []:
            if isinstance(image, dict):
                add("image", image.get("original_image_url") or image.get("resized_image_url"))
    for key in ("videos", "extra_videos"):
        for video in snapshot.get(key) or []:
            if isinstance(video, dict):
                add("video", video.get("video_hd_url") or video.get("video_sd_url"))
                add("video_preview", video.get("video_preview_image_url"))
    return out


def ad_from_node(node: dict[str, Any]) -> LibraryAd | None:
    """Normalise one library result object. Returns None when it has no usable ID."""
    ad_id = _str(node.get("ad_archive_id"))
    if not ad_id or not ad_id.isdigit():
        return None
    snapshot = node.get("snapshot") if isinstance(node.get("snapshot"), dict) else {}
    cards = [c for c in snapshot.get("cards") or [] if isinstance(c, dict)]
    parts = [snapshot, *cards]

    bodies: list[str] = []
    titles: list[str] = []
    descriptions: list[str] = []
    links: list[str] = []
    captions: list[str] = []
    ctas: list[str] = []
    for part in parts:
        _add(bodies, part.get("body"))
        _add(titles, part.get("title"))
        _add(descriptions, part.get("link_description"))
        _add_url(links, part.get("link_url"))
        _add(captions, part.get("caption"))
        _add(ctas, part.get("cta_text"))
    for extra in snapshot.get("extra_texts") or []:
        _add(bodies, extra)
    for extra in snapshot.get("extra_links") or []:
        _add_url(links, extra)

    regulation = node.get("regional_regulation_data") or {}
    finserv = (regulation.get("finserv") or {}).get("is_deemed_finserv")
    page_id = _str(node.get("page_id") or snapshot.get("page_id"))
    return LibraryAd(
        source=SOURCE_META,
        ad_id=ad_id,
        page_id=page_id,
        page_name=_str(snapshot.get("page_name") or node.get("page_name")),
        page_url=_str(snapshot.get("page_profile_uri")),
        page_categories=[c for c in snapshot.get("page_categories") or [] if isinstance(c, str)],
        page_like_count=_int(snapshot.get("page_like_count")),
        is_active=node.get("is_active") if isinstance(node.get("is_active"), bool) else None,
        start_date=_date(node.get("start_date")),
        end_date=_date(node.get("end_date")),
        platforms=[p for p in node.get("publisher_platform") or [] if isinstance(p, str)],
        bodies=bodies,
        titles=titles,
        link_descriptions=descriptions,
        link_urls=links,
        captions=captions,
        cta_texts=ctas,
        display_format=_str(snapshot.get("display_format")),
        media=_media(snapshot, cards),
        collation_id=_str(node.get("collation_id")),
        collation_count=_int(node.get("collation_count")),
        enforcement=_str(node.get("gated_type")),
        is_deemed_finserv=finserv if isinstance(finserv, bool) else None,
        library_url=LIBRARY_URL.format(ad_id),
    )


def ads_from_text(text: str) -> list[tuple[LibraryAd, dict[str, Any]]]:
    """Every ad in one response body, with the raw object it came from, first occurrence kept."""
    found: dict[str, tuple[LibraryAd, dict[str, Any]]] = {}
    for doc in json_documents(text):
        for node in _walk(doc):
            if "ad_archive_id" not in node or not isinstance(node.get("snapshot"), dict):
                continue
            ad = ad_from_node(node)
            if ad is not None and ad.ad_id not in found:
                found[ad.ad_id] = (ad, node)
    return list(found.values())
