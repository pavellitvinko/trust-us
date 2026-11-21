"""Reference extractor: where a page lives.

Signals A14 (registrable domain, host + path) from the saved-from URL, the capture manifest or the HAR. This is the smallest end-to-end example of the Extractor contract; richer
identifier harvesting belongs in sibling modules.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..contracts import Observation, View, ViewKind
from ..registry import Context, extractors
from ._material import landing_url, registrable_domain

__all__ = ["PageIdentity", "registrable_domain"]


@extractors.register
class PageIdentity:
    name = "page.identity"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        if view.kind is not ViewKind.PAGE:
            return []
        found = landing_url(view)
        if found is None:
            return []
        url, source, is_page_url = found
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host:
            return []

        def obs(namespace: str, value: str) -> Observation:
            return Observation(
                view_id=view.view_id,
                signal_id="A14",
                namespace=namespace,
                value=value,
                raw=url,
                source=source,
                extractor=self.name,
                version=self.version,
            )

        out = [obs("domain.registrable", registrable_domain(host)), obs("url.host", host)]
        path = parts.path.rstrip("/")
        if path and is_page_url:  # a bare "/" identifies nothing beyond the host
            out.append(obs("url.host_path", f"{host}{path}"))
        return out
