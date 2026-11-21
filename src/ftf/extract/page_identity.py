"""Reference extractor: where a page lives.

Signals A14 (registrable domain, host + path) from the saved-from URL, the capture manifest or the HAR. This is the smallest end-to-end example of the Extractor contract; richer
identifier harvesting belongs in sibling modules.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import tldextract

from ..contracts import Observation, Source, View, ViewKind
from ..registry import Context, extractors

# Use the suffix list bundled with the library: no network call at run time.
_tld = tldextract.TLDExtract(suffix_list_urls=())


def registrable_domain(host: str) -> str:
    parts = _tld(host)
    return f"{parts.domain}.{parts.suffix}" if parts.domain and parts.suffix else host


@extractors.register
class PageIdentity:
    name = "page.identity"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        if view.kind is not ViewKind.PAGE:
            return []
        # In order of trust. A first-party request is not the page itself, so it gives the host only.
        choices = (
            ("saved_from", view.primary, "html:comment[saved from url]", True),
            ("manifest_url", view.primary, "manifest.json:page_url", True),
            ("har_document", view.har, "har:main document request.url", True),
            ("har_first_party", view.har, "har:busiest first-party host", False),
        )
        for key, file, locator, is_page_url in choices:
            if key in view.meta:
                url, source = view.meta[key], Source(file=file, locator=locator)
                break
        else:
            return []
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
