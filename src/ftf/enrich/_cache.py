"""Timestamped file cache and a polite fetcher shared by the enrichers.

One JSON file per key: `<cache_dir>/<source>/<key>.json` holding
`{"fetched_at": ISO, "ok": bool, "endpoint": str, "data": ..., "error": str}`.

Modes (env `FTF_ENRICH`): `on` looks up and caches; `cache` reads the cache only; `off`
does nothing, so every lookup is reported as not observable. When a fresh lookup fails, an
older successful entry is still used and marked stale: a past fact beats no fact.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import get_settings

OK_TTL = timedelta(days=7)
ERROR_TTL = timedelta(hours=1)
HTTP_TIMEOUT = 10.0
MIN_INTERVAL = 1.0  # seconds between two requests to one host
USER_AGENT = "ftf-enricher/1 (research tool)"


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Lookup:
    """The outcome of one lookup, with what is needed to cite it."""

    ok: bool
    data: Any = None
    error: str = ""
    endpoint: str = ""
    fetched_at: datetime | None = None
    from_cache: bool = False
    stale: bool = False

    def provenance(self) -> dict[str, Any]:
        out: dict[str, Any] = {"endpoint": self.endpoint}
        if self.fetched_at is not None:
            out["lookup_at"] = self.fetched_at.isoformat(timespec="seconds")
            out["cache_age_s"] = int((now() - self.fetched_at).total_seconds())
        out["cached"] = self.from_cache
        if self.stale:
            out["stale"] = True
        if not self.ok:
            out["error"] = self.error
        return out


class FetchError(Exception):
    """A lookup failed. `cacheable` is False for failures that say nothing about the domain."""


_SAFE = re.compile(r"[^a-z0-9._-]+")


class Cache:
    def __init__(self, root: Path | None = None, mode: str | None = None):
        settings = get_settings()
        self.root = root if root is not None else settings.enrich_cache_dir
        self.mode = (mode or settings.enrich_mode).lower()
        self._memo: dict[tuple[str, str], Lookup] = {}
        self._lock = threading.Lock()
        self._last_hit: dict[str, float] = {}

    def _path(self, source: str, key: str) -> Path:
        return self.root / source / f"{_SAFE.sub('_', key.lower())[:150]}.json"

    def _read(self, source: str, key: str) -> Lookup | None:
        try:
            body = json.loads(self._path(source, key).read_text(encoding="utf-8"))
            return Lookup(
                ok=bool(body["ok"]),
                data=body.get("data"),
                error=body.get("error", ""),
                endpoint=body.get("endpoint", ""),
                fetched_at=datetime.fromisoformat(body["fetched_at"]),
                from_cache=True,
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write(self, source: str, key: str, result: Lookup) -> None:
        path = self._path(source, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            body = {
                "fetched_at": result.fetched_at.isoformat() if result.fetched_at else None,
                "ok": result.ok,
                "endpoint": result.endpoint,
                "data": result.data,
                "error": result.error,
            }
            path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # a read-only cache must not stop the run

    def get(
        self,
        source: str,
        key: str,
        fetch: Callable[[], tuple[Any, str]],
        endpoint: str = "",
    ) -> Lookup:
        """Return the cached value for (source, key), fetching it when allowed and needed.

        `fetch` returns (data, endpoint) or raises FetchError / any exception.
        """
        memo_key = (source, key)
        if memo_key in self._memo:
            return self._memo[memo_key]
        result = self._get(source, key, fetch, endpoint)
        self._memo[memo_key] = result
        return result

    def _get(self, source, key, fetch, endpoint) -> Lookup:
        if self.mode == "off":
            return Lookup(ok=False, error="enrichment off (FTF_ENRICH=off)", endpoint=endpoint)
        cached = self._read(source, key)
        if cached is not None and cached.fetched_at is not None:
            ttl = OK_TTL if cached.ok else ERROR_TTL
            if self.mode == "cache" or now() - cached.fetched_at < ttl:
                return cached
        if self.mode == "cache":
            return Lookup(ok=False, error="not in cache (FTF_ENRICH=cache)", endpoint=endpoint)
        try:
            data, used = fetch()
            result = Lookup(ok=True, data=data, endpoint=used or endpoint, fetched_at=now())
        except Exception as exc:  # noqa: BLE001 - every failure becomes not observable
            result = Lookup(ok=False, error=_describe(exc), endpoint=endpoint, fetched_at=now())
            if cached is not None and cached.ok:
                cached.stale = True  # the old file is kept, so the next run retries
                return cached
        self._write(source, key, result)
        return result

    # -- polite HTTP ------------------------------------------------------------------

    def _wait_for(self, host: str) -> None:
        with self._lock:
            last = self._last_hit.get(host, 0.0)
            delay = MIN_INTERVAL - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
            self._last_hit[host] = time.monotonic()

    def http_json(self, url: str, accept: str = "application/json") -> Any:
        """GET a JSON document. 404 and other HTTP errors raise FetchError."""
        self._wait_for(urlsplit(url).hostname or "")
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raise FetchError(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError(f"network: {getattr(exc, 'reason', exc)}") from exc
        except ValueError as exc:
            raise FetchError("invalid JSON") from exc


def _describe(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    return text[:200]


_shared: Cache | None = None


def shared_cache() -> Cache:
    """One cache per process, so views of one domain share a lookup."""
    global _shared
    if _shared is None:
        _shared = Cache()
    return _shared


def reset_shared_cache(cache: Cache | None = None) -> None:
    """Drop (or replace) the process-wide cache; used by tests and when settings change."""
    global _shared
    _shared = cache
