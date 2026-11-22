"""HLS playlists a page streams video from (signal M7, and C12 for a CDN video library).

Where: playlist URLs in HAR requests, in the page HTML and its inline scripts, and playlist
files saved among the page's resources. A playlist body comes from the HAR response or the
saved file, and is read as text only: nothing is fetched.

What is kept:
- `video.hls_host`: the host serving the playlist and its segments (links weakly through
  `infra.host`). On a shared video CDN the library's own host name is an account: it is
  emitted as `cdn.zone_id` (identity) and no host row is written, since the CDN's own
  domain (`b-cdn.net`) is shared by every customer and the library is already counted.
- `cdn.zone_id`: the BunnyCDN Stream library (`vz-6110f311-389` in `vz-…b-cdn.net`).
- `video.hls_shape`: how the stream was packaged: master or media playlist, version, target
  segment duration, variant ladder, segment naming (`output_999.ts`). Evidence to read: the
  same encoder settings are common, so no layer links on it.
- `video.hls_url`: the playlist URL itself, for the analyst.
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from ..contracts import Observation, Purpose, Source, View, ViewKind
from ..ingest.har import load_har
from ..registry import Context, extractors
from . import _material as m

NAME = "video.stream"
VERSION = "1"
MAX_PLAYLIST = 2 << 20  # characters of one playlist read
MAX_PLAYLISTS = 50

_URL = re.compile(r"""(?:https?:)?//[^\s"'<>()\\]+?\.m3u8(?:\?[^\s"'<>()\\]*)?""", re.IGNORECASE)
_BUNNY_STREAM = re.compile(r"^(vz-[0-9a-f]{8}-[0-9a-f]{3})\.b-cdn\.net$", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def zone_id(host: str) -> str | None:
    """The CDN video library a host belongs to, when the host name is the library."""
    found = _BUNNY_STREAM.match(host)
    return found.group(1).lower() if found else None


def _segment_name(uri: str) -> str:
    name = Path(urlsplit(uri).path).name
    return _DIGITS.sub("999", name)


def shape(text: str) -> tuple[str | None, list[str]]:
    """(structure of a playlist, its child URIs). None when the text is not a playlist."""
    lines = [line.strip() for line in text.lstrip("\ufeff").splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        return None, []
    version = target = None
    variants: list[str] = []
    uris: list[str] = []
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-VERSION:"):
            version = line.split(":", 1)[1]
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            target = line.split(":", 1)[1]
        elif line.startswith("#EXT-X-STREAM-INF:"):
            attrs = dict(_ATTR.findall(line.split(":", 1)[1]))
            variants.append(attrs.get("RESOLUTION") or attrs.get("BANDWIDTH") or "?")
            if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                uris.append(lines[i + 1])
        elif not line.startswith("#") and not variants:
            uris.append(line)
    parts = [f"v{version or '?'}"]
    if variants:
        parts.insert(0, "master")
        parts.append("variants=" + ",".join(sorted(variants)))
    else:
        parts.insert(0, "media")
        parts.append(f"target={target or '?'}")
        names = sorted({_segment_name(u) for u in uris})
        parts.append("segments=" + ",".join(names[:3]))
    return ";".join(parts), uris


def _har_text(content: dict) -> str | None:
    """A HAR response body as text, base64 or not, up to MAX_PLAYLIST characters."""
    text = content.get("text")
    if not isinstance(text, str) or not text:
        return None
    if content.get("encoding") == "base64":
        try:  # 4 base64 characters carry 3 bytes
            raw = base64.b64decode(text[: (MAX_PLAYLIST // 3 + 1) * 4], validate=False)
        except (binascii.Error, ValueError):
            return None
        return raw.decode("utf-8", errors="replace")
    return text[:MAX_PLAYLIST]


def _hosts(url: str, children: list[str]) -> list[str]:
    hosts = [m.host_of(url)] if url else []
    for child in children:
        hosts.append(m.host_of(urljoin(url, child)) if url else m.host_of(child))
    return [h for h in dict.fromkeys(hosts) if h]


@extractors.register
class VideoStream:
    name = NAME
    version = VERSION

    def run(self, view: View, ctx: Context) -> list[Observation]:
        if view.kind is not ViewKind.PAGE:
            return []
        emit = m.Emitter(view, self.name, self.version)
        seen = 0

        def playlist(url: str, body: str | None, source: Source) -> None:
            nonlocal seen
            if seen >= MAX_PLAYLISTS:
                return
            seen += 1
            if url.startswith("//"):
                url = "https:" + url
            structure, children = shape(body[:MAX_PLAYLIST]) if body else (None, [])
            if url:
                emit("M7", "video.hls_url", url, source, purpose=Purpose.RISK)
            for host in _hosts(url, children):
                zone = zone_id(host)
                if zone:
                    emit("C12", "cdn.zone_id", zone, source, raw=host)
                else:
                    emit("M7", "video.hls_host", host, source)
            if structure:
                emit("M7", "video.hls_shape", structure, source, purpose=Purpose.RISK)

        def har() -> None:
            if not view.har:
                return
            for i, entry in enumerate(load_har(ctx.path(view, view.har))):
                url = str((entry.get("request") or {}).get("url") or "")
                content = (entry.get("response") or {}).get("content") or {}
                mime = str(content.get("mimeType") or "").lower()
                if ".m3u8" not in url.lower() and "mpegurl" not in mime:
                    continue
                body = _har_text(content)
                playlist(url, body, Source(file=view.har, locator=f"har:entries[{i}]"))

        def html() -> None:
            material = m.load(view, ctx)
            texts = [material.html] if material.html else []
            for text in [*texts, *material.inline_scripts]:
                for found in _URL.findall(m.js_unescape(text.text)):
                    playlist(found, None, text.source)

        def saved() -> None:
            if not view.resources_dir:
                return
            root = ctx.path(view, "")
            for p in sorted(ctx.path(view, view.resources_dir).rglob("*.m3u8")):
                if p.is_file() and p.stat().st_size <= MAX_PLAYLIST:
                    rel = p.relative_to(root).as_posix()
                    body = p.read_text(encoding="utf-8-sig", errors="replace")
                    playlist("", body, Source(file=rel, locator="file:text"))

        m.run_steps([har, html, saved])
        return emit.out
