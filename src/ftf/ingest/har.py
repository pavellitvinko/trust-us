"""HAR loading helpers shared by ingestion and extractors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit


def load_har(path: Path) -> list[dict[str, Any]]:
    """Return the HAR's entries, or an empty list if the file is not a readable HAR."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        entries = data["log"]["entries"]
        if not isinstance(entries, list):
            return []
        # Keep indexes stable for locators: a malformed entry becomes an empty one.
        return [e if _well_formed(e) else {} for e in entries]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _well_formed(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    response = entry.get("response", {})
    return (
        isinstance(entry.get("request", {}), dict)
        and isinstance(response, dict)
        and isinstance(response.get("content", {}) or {}, dict)
    )


def _is_html(entry: dict[str, Any]) -> bool:
    mime = (entry.get("response", {}).get("content", {}) or {}).get("mimeType", "") or ""
    return "html" in mime.lower()


# Hosts that serve tags, ads, fonts and libraries to everyone. Never a page's own host.
NOISE_HOST_SUFFIXES = (
    "googletagmanager.com", "google-analytics.com", "google.com", "google.pl", "doubleclick.net",
    "googleapis.com", "gstatic.com", "facebook.com", "facebook.net", "fbcdn.net", "jsdelivr.net",
    "cloudflare.com", "unpkg.com", "jquery.com", "bing.net", "quora.com", "ads-twitter.com",
)  # fmt: skip


def is_noise_host(host: str) -> bool:
    return any(host == s or host.endswith("." + s) for s in NOISE_HOST_SUFFIXES)


def first_party_request(entries: list[dict[str, Any]]) -> tuple[int, str] | None:
    """Fallback when no HTML document was captured: the first request to the busiest non-noise host."""
    seen: dict[str, list[int]] = {}
    for i, entry in enumerate(entries):
        host = urlsplit(entry.get("request", {}).get("url", "")).hostname or ""
        if "." in host and not is_noise_host(host):
            seen.setdefault(host, []).append(i)
    if not seen:
        return None
    host = max(seen, key=lambda h: (len(seen[h]), -seen[h][0]))
    index = seen[host][0]
    return index, entries[index]["request"]["url"]


def main_document(
    entries: list[dict[str, Any]], prefer_host: str | None = None
) -> tuple[int, str] | None:
    """Pick the page's own document: (entry index, URL).

    The first entry is often not it (a video segment or an API call can come first), so
    choose by content type: a successful HTML response, preferring the browser's own
    'document' resource type and, when known, the host the page was saved from.
    """
    candidates = []
    for i, entry in enumerate(entries):
        url = entry.get("request", {}).get("url", "")
        host = urlsplit(url).hostname or ""
        if not host or "." not in host or is_noise_host(host):
            continue  # placeholders such as https://delivered/, and tag or ad iframes
        if entry.get("response", {}).get("status") != 200 or not _is_html(entry):
            continue
        rank = (
            0 if prefer_host and host == prefer_host else 1,
            0 if entry.get("_resourceType") == "document" else 1,
            i,
        )
        candidates.append((rank, i, url))
    if not candidates:
        return None
    _, index, url = min(candidates)
    return index, url


MAX_HOPS = 20


def document_redirects(entries: list[dict[str, Any]]) -> list[tuple[int, str, str, int]]:
    """(entry index, from URL, to URL, status) of the HTTP redirects into the main document,
    in the order they happened."""
    doc = main_document(entries)
    if doc is None:
        return []
    target, hops, seen = doc[1], [], set()
    while len(hops) < MAX_HOPS:
        found = None
        for i, entry in enumerate(entries):
            if i in seen:
                continue
            req, resp = entry.get("request") or {}, entry.get("response") or {}
            status = resp.get("status") or 0
            location = resp.get("redirectURL") or ""
            url = req.get("url") or ""
            if not (300 <= status < 400 and location and url):
                continue
            if entry.get("_resourceType") not in (None, "document"):
                continue
            if urljoin(url, location).split("#")[0] == target.split("#")[0]:
                found = (i, url, target, status)
                break
        if found is None:
            break
        seen.add(found[0])
        hops.append(found)
        target = found[1]
    return hops[::-1]
