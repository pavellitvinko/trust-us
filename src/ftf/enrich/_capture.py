"""When a view was captured, for domain age at capture time (signal C1).

File modification times are not used: saved pages are copied around, so their mtime is the
copy date. Order of trust: the view's own HAR, another HAR of the same incident that talks
to the same domain, the capture bundle's manifest. Without any of them the caller falls back
to the lookup time and says so.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from ..contracts import View
from ..ingest.har import load_har, main_document
from ..registry import Context

_MANIFEST_KEYS = ("captured_at", "capture_time", "started_at", "timestamp")


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@lru_cache(maxsize=64)
def _har_summary(path: str, _stamp: float) -> tuple[datetime | None, frozenset[str]]:
    """(time of the main document or first entry, registrable domains requested)."""
    from ..extract._material import registrable_domain

    entries = load_har(Path(path))
    hosts = frozenset(
        registrable_domain(h)
        for e in entries
        if (h := (urlsplit(e.get("request", {}).get("url", "")).hostname or "").lower())
    )
    doc = main_document(entries)
    order = [doc[0]] if doc else []
    order += range(len(entries))
    for i in order:
        when = parse_time(entries[i].get("startedDateTime"))
        if when is not None:
            return when, hosts
    return None, hosts


def _har(path: Path) -> tuple[datetime | None, frozenset[str]]:
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None, frozenset()
    return _har_summary(str(path), stamp)


def capture_time(view: View, domain: str, ctx: Context) -> tuple[datetime, str] | None:
    """(when, basis) or None. Basis names where the time came from."""
    if view.har:
        when, _ = _har(ctx.path(view, view.har))
        if when is not None:
            return when, f"har:{view.har}"
    for rel in view.attachments:
        if Path(rel).name != "manifest.json":
            continue
        try:
            manifest = json.loads(ctx.read_text(view, rel))
        except (OSError, ValueError):
            continue
        for key in _MANIFEST_KEYS:
            when = parse_time(manifest.get(key)) if isinstance(manifest, dict) else None
            if when is not None:
                return when, f"manifest:{rel}:{key}"
    found = []
    for art in ctx.store.artifacts(view.incident_id):
        if art.role != "har" or art.path == view.har:
            continue
        when, hosts = _har(ctx.path(view, art.path))
        if when is not None and domain in hosts:
            found.append((when, art.path))
    if found:
        when, rel = min(found)
        return when, f"incident_har:{rel}"
    return None
