"""The capture bundle's manifest and the redirect trace. No browser here, so this is testable.

A bundle is the layout ingestion already reads: `dom.html`, `page.har`, `screenshot.png`,
`manifest.json`, `resources/`. A failed capture (dead domain, refused connection) still
writes `manifest.json` with an explicit `status`, so the failure is an observation and not a
silent gap.
"""

from __future__ import annotations

import base64
import html
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field

SCHEMA = "ftf.capture/1"


class Status(StrEnum):
    OK = "ok"
    HTTP_ERROR = "http_error"  # the page answered with 4xx/5xx; DOM and screenshot still kept
    DNS_FAILED = "dns_failed"
    CONNECTION_FAILED = "connection_failed"
    TLS_FAILED = "tls_failed"
    TIMEOUT = "timeout"
    NOT_IN_ARCHIVE = "not_in_archive"  # replay: the archive has no response for the URL
    # The page loaded, then navigated on to somewhere that failed (a dead onward host).
    # The bundle keeps the page that did load; the failed hop is the last in the trace.
    NAVIGATION_FAILED = "navigation_failed"
    FAILED = "failed"


class HopKind(StrEnum):
    HTTP = "http"  # 3xx with a Location header
    META_REFRESH = "meta_refresh"  # <meta http-equiv="refresh"> in the previous document
    SCRIPT = "script"  # any other main-frame navigation the page started itself
    HISTORY = "history"  # the address changed through the History API; nothing was loaded


class Hop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    from_url: str
    to_url: str
    kind: HopKind
    status: int | None = None  # HTTP status of the redirecting response
    source: str | None = None  # "page.har:entries[3]" — where the hop can be checked


class Tls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str | None = None
    issuer: str | None = None
    subject: str | None = None
    valid_from: str | None = None  # ISO UTC
    valid_to: str | None = None
    error: str | None = None  # certificate error the browser reported, when it was bypassed


class Server(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = None  # the response these facts belong to (the final document)
    status: int | None = None
    ip: str | None = None
    port: int | None = None
    tls: Tls | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    sha256: str
    mime: str = ""
    size: int = 0
    path: str  # relative to the bundle


class FailedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    method: str = "GET"
    error: str = ""


class Manifest(BaseModel):
    """`manifest.json`. `page_url` and `captured_at` are the keys ingestion already reads."""

    model_config = ConfigDict(extra="forbid")

    schema_: str = Field(default=SCHEMA, alias="schema")
    tool: str = "ftf capture"
    mode: str = "live"  # live | replay
    page_url: str
    final_url: str | None = None
    captured_at: str  # ISO UTC; for replay, the archived capture's own time
    replayed_at: str | None = None
    replay_source: dict[str, Any] | None = None  # {"file": …, "sha256": …}
    status: Status = Status.OK
    error: str | None = None
    egress: str = "direct"  # proxy server, or direct
    profile: str = "desktop"  # see capture/profiles.py
    referer: str | None = None  # the Referer the first navigation was sent with
    click_id_added: bool | None = None  # a synthetic fbclid was appended to page_url
    differential: str | None = None  # the URL a cloaking differential tested (HAC-15)
    differential_run: str | None = None  # one run of that test: its variants share this id
    user_agent: str | None = None
    viewport: dict[str, int] | None = None
    redirects: list[Hop] = Field(default_factory=list)
    server: Server | None = None
    resources: list[Resource] = Field(default_factory=list)
    failed_requests: list[FailedRequest] = Field(default_factory=list)
    blocked: list[FailedRequest] = Field(default_factory=list)  # form posts, downloads
    files: dict[str, str] = Field(default_factory=dict)  # role -> file name in the bundle

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def write(self, bundle: Path) -> Path:
        path = bundle / "manifest.json"
        path.write_text(json.dumps(self.dump(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


def read_manifest(path: Path) -> Manifest | None:
    """The manifest if it is one of ours; None for organiser bundles or unreadable files."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("schema", "")).startswith("ftf.capture/"):
        return None
    try:
        return Manifest.model_validate(data)
    except ValueError:
        return None


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def classify_error(message: str) -> Status:
    """Map a browser navigation error to a capture status."""
    text = message.upper()
    if "ERR_NAME_NOT_RESOLVED" in text or "ERR_NAME_RESOLUTION" in text:
        return Status.DNS_FAILED
    if "ERR_CERT" in text or "ERR_SSL" in text:
        return Status.TLS_FAILED
    if "TIMEOUT" in text or "ERR_TIMED_OUT" in text:
        return Status.TIMEOUT
    if any(
        code in text
        for code in (
            "ERR_CONNECTION",
            "ERR_ADDRESS_UNREACHABLE",
            "ERR_EMPTY_RESPONSE",
            "ERR_INTERNET_DISCONNECTED",
            "ERR_NETWORK",
        )
    ):
        return Status.CONNECTION_FAILED
    return Status.FAILED


# --- redirect trace ---------------------------------------------------------------------

_META_REFRESH = re.compile(
    r"<meta\b[^>]*http-equiv\s*=\s*[\"']?refresh[\"']?[^>]*>", re.IGNORECASE | re.DOTALL
)
_CONTENT = re.compile(r"content\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)
_REFRESH_URL = re.compile(r"url\s*=\s*[\"']?([^\"';]+)", re.IGNORECASE)


def meta_refresh_target(document: str, base_url: str) -> str | None:
    """The absolute URL a `<meta http-equiv="refresh">` sends to, if the document has one."""
    for tag in _META_REFRESH.findall(document or ""):
        content = _CONTENT.search(tag)
        if not content:
            continue
        target = _REFRESH_URL.search(html.unescape(content.group(2)))
        if target:
            return urljoin(base_url, target.group(1).strip())
    return None


def _same_url(a: str, b: str) -> bool:
    return a.split("#")[0].rstrip("/") == b.split("#")[0].rstrip("/")


def _entry_body(entry: dict[str, Any]) -> str:
    content = (entry.get("response") or {}).get("content") or {}
    text = content.get("text") or ""
    if content.get("encoding") == "base64" and text:
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except ValueError:
            return ""
    return text if isinstance(text, str) else ""


def _location(entry: dict[str, Any], base: str) -> str:
    response = entry.get("response") or {}
    location = response.get("redirectURL") or next(
        (
            h.get("value", "")
            for h in response.get("headers") or []
            if isinstance(h, dict) and str(h.get("name", "")).lower() == "location"
        ),
        "",
    )
    return urljoin(base, location) if location else ""


def _find_entry(entries: list[dict[str, Any]], url: str, redirect: bool) -> int | None:
    for i, entry in enumerate(entries):
        if not _same_url((entry.get("request") or {}).get("url", ""), url):
            continue
        status = (entry.get("response") or {}).get("status") or 0
        if redirect == (300 <= status < 400):
            return i
    return None


def build_trace(
    chains: list[list[str]],
    entries: list[dict[str, Any]],
    har_name: str = "page.har",
    final_url: str | None = None,
) -> list[Hop]:
    """Ordered hops from the main frame's navigation chains.

    A chain is one navigation as the browser saw it: the URL requested, then every URL an
    HTTP redirect led to. Within a chain each step is an HTTP hop. Between chains the page
    navigated itself: a meta refresh when the previous document carries one, otherwise a
    script. The HAR supplies status codes, document bodies and the entry to cite. When the
    page ends on an address no request loaded (`history.replaceState`), a last `history`
    hop records the rewrite.
    """
    hops: list[Hop] = []

    def add(src: str, dst: str, kind: HopKind, status: int | None, index: int | None) -> None:
        hops.append(
            Hop(
                index=len(hops),
                from_url=src,
                to_url=dst,
                kind=kind,
                status=status,
                source=f"{har_name}:entries[{index}]" if index is not None else None,
            )
        )

    previous_final: str | None = None
    for chain in (c for c in chains if c):
        if previous_final is not None and not _same_url(previous_final, chain[0]):
            # A HAR router (replay) answers a 3xx by starting a new navigation, so an HTTP
            # redirect can arrive as two chains: the archive's 3xx entry tells.
            r = _find_entry(entries, previous_final, redirect=True)
            if r is not None and _same_url(_location(entries[r], previous_final), chain[0]):
                status = (entries[r].get("response") or {}).get("status")
                add(previous_final, chain[0], HopKind.HTTP, status, r)
            else:
                i = _find_entry(entries, previous_final, redirect=False)
                body = _entry_body(entries[i]) if i is not None else ""
                target = meta_refresh_target(body, previous_final)
                kind = (
                    HopKind.META_REFRESH
                    if target and _same_url(target, chain[0])
                    else HopKind.SCRIPT
                )
                add(previous_final, chain[0], kind, None, i)
        for src, dst in pairwise(chain):
            i = _find_entry(entries, src, redirect=True)
            status = (entries[i].get("response") or {}).get("status") if i is not None else None
            add(src, dst, HopKind.HTTP, status, i)
        previous_final = chain[-1]
    visited = [u for chain in chains for u in chain]
    if (
        previous_final
        and final_url
        and not any(_same_url(u, final_url) for u in visited)  # e.g. a failed onward hop
    ):
        add(previous_final, final_url, HopKind.HISTORY, None, None)
    return hops
