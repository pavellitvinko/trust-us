"""Ad-library facts attached to an ad view (signals L1, L2, L4, G4, O1, A14).

Two inputs:
- `ad.json`, written by discovery for an ad found in the Meta Ad Library;
- a yt-dlp `.info.json` of an ad downloaded from the library (sample case 5), which carries
  the Ad Library ID and the advertiser page.

The link URL is emitted as `domain.registrable` and `url.host_path`, the namespaces the
`ids.domain` layer already compares, so an ad links to a known landing page with no new layer.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..contracts import Observation, Purpose, Source, View
from ..registry import Context, extractors
from . import _material as m

# Domains that every ad on the platform shares; they would link everything to everything.
_PLATFORM = frozenset({"facebook.com", "fb.me", "fb.com", "instagram.com", "m.me", "wa.me",
                       "messenger.com", "whatsapp.com"})  # fmt: skip
_DOMAINISH = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


def _read_json(view: View, ctx: Context, rel: str) -> dict[str, Any] | None:
    try:
        data = json.loads(ctx.read_text(view, rel))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@extractors.register
class AdLibrary:
    name = "ids.ad_library"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        emit = m.Emitter(view, self.name, self.version)
        for rel in view.attachments:
            if rel.endswith("ad.json"):
                data = _read_json(view, ctx, rel)
                ad = (data or {}).get("ad")
                if isinstance(ad, dict):
                    m.run_steps([lambda ad=ad, rel=rel: self._from_ad(emit, ad, rel)])
            elif rel.endswith(".info.json"):
                data = _read_json(view, ctx, rel)
                if data and str(data.get("extractor", "")).startswith("facebook:ads"):
                    m.run_steps([lambda data=data, rel=rel: self._from_ytdlp(emit, data, rel)])
        return emit.out

    @staticmethod
    def _domain(emit: m.Emitter, value: str, source: Source, raw: str) -> None:
        domain = m.registrable_domain(value.lower())
        if domain and domain not in _PLATFORM:
            emit("A14", "domain.registrable", domain, source, raw=raw)

    def _from_ad(self, emit: m.Emitter, ad: dict[str, Any], rel: str) -> None:
        src = lambda key: Source(file=rel, locator=f"ad.json:{key}")
        emit("L1", "ad_library.ad_id", str(ad["ad_id"]), src("ad_id"))
        if ad.get("page_id"):
            emit("L1", "ad_library.page_id", str(ad["page_id"]), src("page_id"))
        if ad.get("page_name"):
            emit(
                "G4",
                "ad_library.page_name",
                ad["page_name"],
                src("page_name"),
                purpose=Purpose.BOTH,
            )
        if ad.get("collation_id"):
            emit("L1", "ad_library.collation_id", str(ad["collation_id"]), src("collation_id"))
        for key in ("start_date", "end_date"):
            if ad.get(key):
                emit("O1", f"ad_library.{key}", ad[key], src(key), purpose=Purpose.RISK)
        for platform in ad.get("platforms") or []:
            emit("L4", "ad_library.platform", platform, src("platforms"), purpose=Purpose.RISK)
        if ad.get("enforcement"):
            emit("L1", "ad_library.enforcement", ad["enforcement"], src("enforcement"),
                 purpose=Purpose.RISK)  # fmt: skip
        for key, namespace in (("bodies", "ad.body"), ("titles", "ad.title"),
                               ("link_descriptions", "ad.link_description"), ("cta_texts", "ad.cta")):  # fmt: skip
            for text in ad.get(key) or []:
                emit("L2", namespace, text, src(key), purpose=Purpose.BOTH)
        for url in ad.get("link_urls") or []:
            host = m.host_of(url)
            if not host:
                continue
            emit("A14", "ad.link_url", url, src("link_urls"))
            self._domain(emit, host, src("link_urls"), url)
            path = urlsplit(url).path.rstrip("/")
            if path and m.registrable_domain(host) not in _PLATFORM:
                emit("A14", "url.host_path", f"{host}{path}", src("link_urls"), raw=url)
        for caption in ad.get("captions") or []:
            caption = caption.strip().lower()
            if _DOMAINISH.match(caption):
                self._domain(emit, caption, src("captions"), caption)

    def _from_ytdlp(self, emit: m.Emitter, data: dict[str, Any], rel: str) -> None:
        src = lambda key: Source(file=rel, locator=f"info.json:{key}")
        ad_id = None
        url = data.get("webpage_url") or data.get("playlist_webpage_url") or ""
        ids = parse_qs(urlsplit(url).query).get("id")
        if ids and ids[0].isdigit():
            ad_id = ids[0]
        elif str(data.get("playlist_id", "")).isdigit():
            ad_id = str(data["playlist_id"])
        if ad_id:
            emit("L1", "ad_library.ad_id", ad_id, src("webpage_url"), raw=url or None)
        page_id = str(data.get("playlist_uploader_id") or data.get("uploader_id") or "")
        if page_id.isdigit():
            emit("L1", "ad_library.page_id", page_id, src("playlist_uploader_id"))
        page_name = data.get("playlist_uploader") or data.get("uploader")
        if isinstance(page_name, str) and page_name.strip():
            emit("G4", "ad_library.page_name", page_name.strip(), src("playlist_uploader"),
                 purpose=Purpose.BOTH)  # fmt: skip
