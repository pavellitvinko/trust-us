"""Server and proxy headers of the page's own domain (signal C10). Weak kit tier.

The product name only, lower-cased, without version or protocol (`1.1 Caddy` -> `caddy`).
CDN fronts are on most unrelated sites and are stoplisted.
"""

from __future__ import annotations

import re

from ..contracts import Observation, View
from ..registry import Context, extractors
from . import _material as m

_HEADERS = {"server": "http.server", "via": "http.via", "x-powered-by": "http.powered_by"}
STOP_PRODUCTS = frozenset({
    "cloudflare", "cloudfront", "akamaighost", "akamai", "gws", "gse", "esf",
    "google frontend", "varnish", "fastly",
})  # fmt: skip
_PROTOCOL = re.compile(r"^(?:https?/)?\d+(?:\.\d+)?\s+", re.IGNORECASE)
_VERSION = re.compile(r"[/\s(].*$")


def product(value: str) -> str | None:
    """`1.1 Caddy` -> `caddy`; `nginx/1.25.3` -> `nginx`; a stoplisted front -> None."""
    first = value.split(",")[0].strip()
    first = _PROTOCOL.sub("", first).lower()
    # Multiword fronts (`Google Frontend`) are matched before the version is cut off.
    if any(re.match(rf"{re.escape(stop)}(?:$|[/\s(])", first) for stop in STOP_PRODUCTS):
        return None
    name = _VERSION.sub("", first).strip()
    if not name or not re.match(r"^[a-z][\w.\-]*$", name):
        return None
    return name


@extractors.register
class InfraHeaders:
    name = "infra.headers"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        own = m.own_domain(view)
        if not own:
            return []
        mat = m.load(view, ctx)
        emit = m.Emitter(view, self.name, self.version)

        def headers() -> None:
            for req in mat.har_requests:
                if m.registrable_domain(m.host_of(req.url)) != own:
                    continue
                for header, value in req.response_headers:
                    namespace = _HEADERS.get(header)
                    name = product(value) if namespace else None
                    if name:
                        source = m.at(req.source, f"response.headers.{header}")
                        emit("C10", namespace, name, source, raw=value)

        m.run_steps([headers])
        return emit.out
