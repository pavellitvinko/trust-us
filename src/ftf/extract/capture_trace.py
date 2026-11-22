"""What the capture saw on the way to the page: redirect hops, origin server, certificate, outcome.

Signals I5 (redirect chain and its hop hosts), C11 (origin IP when not behind a CDN), C14
(certificate issuer and validity) and C13 (the capture failed: dead, refused, timed out).

Two sources, in order of trust:
- the manifest `ftf capture` writes: an ordered hop list that tells HTTP redirects,
  meta-refresh and script navigations apart, plus the final response's server facts;
- an archived HAR: only HTTP redirects of the browser's document requests that lead into
  the page's own document. Redirects of beacons and pixels are not hops on the way to the
  page and are left out, or every ad network would become a shared hop.

Observed redirects are kept apart from stored navigation history (`nav.landing_uuid`,
`extract/kit_config.py`), which says a page was visited before, not how it was reached.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts import Observation, Purpose, Source, View, ViewKind
from ..ingest.har import document_redirects, load_har
from ..registry import Context, extractors
from . import _material as m
from .infra_headers import STOP_PRODUCTS

# Headers only a CDN edge adds: behind one, the answering IP is the CDN's, not the operator's.
CDN_HEADERS = frozenset({
    "cf-ray", "x-amz-cf-id", "x-amz-cf-pop", "x-akamai-transformed", "akamai-grn",
    "x-fastly-request-id", "x-served-by", "x-azure-ref", "x-cdn", "cdn-pullzone",
})  # fmt: skip


def behind_cdn(headers: dict[str, str]) -> bool:
    lowered = {k.lower(): str(v).lower() for k, v in headers.items()}
    if CDN_HEADERS & lowered.keys():
        return True
    server = lowered.get("server", "")
    return any(server.startswith(stop) for stop in STOP_PRODUCTS)


def _manifest(view: View, ctx: Context) -> tuple[dict[str, Any], str] | None:
    for rel in view.attachments:
        if Path(rel).name != "manifest.json":
            continue
        try:
            data = json.loads(ctx.read_text(view, rel))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get("schema", "")).startswith("ftf.capture/"):
            return data, rel
    return None


@extractors.register
class CaptureTrace:
    name = "capture.trace"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        if view.kind is not ViewKind.PAGE:
            return []
        emit = m.Emitter(view, self.name, self.version)
        own = m.own_domain(view)
        found = _manifest(view, ctx)

        def hop(i: int, src: str, dst: str, kind: str, status: Any, source: Source) -> None:
            raw = f"{kind}{f' {status}' if status else ''}: {src} -> {dst}"
            emit("I5", "redirect.hop", raw, source, purpose=Purpose.RISK)
            if kind == "history":
                return  # same-origin address rewrite: no host was visited
            for url in (src, dst):
                host = m.host_of(url)
                domain = m.registrable_domain(host) if host else None
                if domain and domain != own:
                    emit("I5", "redirect.domain", domain, source, raw=raw)

        def from_manifest() -> None:
            if found is None:
                return
            data, rel = found
            for i, h in enumerate(data.get("redirects") or []):
                if isinstance(h, dict) and h.get("from_url") and h.get("to_url"):
                    source = Source(file=rel, locator=f"manifest:redirects[{i}]")
                    hop(i, h["from_url"], h["to_url"], str(h.get("kind")), h.get("status"), source)

        def from_har() -> None:
            if found is not None or not view.har:
                return
            entries = load_har(ctx.path(view, view.har))
            for i, src, dst, status in document_redirects(entries):
                source = Source(file=view.har, locator=f"har:entries[{i}].response.redirectURL")
                hop(i, src, dst, "http", status, source)

        def server() -> None:
            if found is None:
                return
            data, rel = found
            facts = data.get("server") or {}
            if not isinstance(facts, dict):
                return
            headers = facts.get("headers") or {}
            ip = facts.get("ip")
            # Through a proxy the browser reports the proxy's address, not the origin's.
            direct = data.get("egress", "direct") == "direct"
            if ip and direct and not behind_cdn(headers if isinstance(headers, dict) else {}):
                emit("C11", "origin.ip", ip, Source(file=rel, locator="manifest:server.ip"))
            tls = facts.get("tls") or {}
            if isinstance(tls, dict):
                source = Source(file=rel, locator="manifest:server.tls")
                if tls.get("issuer"):
                    emit("C14", "tls.issuer", tls["issuer"], source, purpose=Purpose.RISK)
                days = _days(tls.get("valid_from"), tls.get("valid_to"))
                if days is not None:
                    emit(
                        "C14", "tls.validity_days", days, source, purpose=Purpose.RISK,
                        raw=f"{tls.get('valid_from')} .. {tls.get('valid_to')}",
                    )  # fmt: skip

        def outcome() -> None:
            if found is None:
                return
            data, rel = found
            status = data.get("status")
            if status and status != "ok":
                emit(
                    "C13", "capture.status", status, Source(file=rel, locator="manifest:status"),
                    raw=data.get("error") or status, purpose=Purpose.RISK,
                )  # fmt: skip

        m.run_steps([from_manifest, from_har, server, outcome])
        return emit.out


def _days(start: Any, end: Any) -> int | None:
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
    except (TypeError, ValueError):
        return None
