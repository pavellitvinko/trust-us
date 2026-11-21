"""Ad-network pixels and tag IDs (signals A3, A4, A5).

A value counts only inside a recognised context — an init call, a vendor URL, a named
parameter or field — because `ad_id` and `utm_id` are long digit strings on the same
URLs. Locations are visited in order of trust, so an observation keeps the best source.
"""

from __future__ import annotations

import re

from ..contracts import Observation, Source, View
from ..registry import Context, extractors
from . import _material as m

_PIXEL = r"(\d{15,16})"
_Q = r"""['"]"""

# Facebook pixel, in scripts and in any text that holds URLs.
FB_INIT = re.compile(rf"fbq\(\s*{_Q}init{_Q}\s*,\s*{_Q}?{_PIXEL}\b")
FB_URL = re.compile(
    rf"facebook\.com/tr/?\?(?:[^\"'\s<>]*?&(?:amp;)?)?id={_PIXEL}\b"
    rf"|connect\.facebook\.net/signals/config/{_PIXEL}\b"
)
FB_PARAMS = ("pxl", "fbpx", "pixel_fb", "fb_pixel", "fbpixel", "pixel")
FB_FIELDS = ("fb_pixel", "fbpixel", "pixel_fb", "fbpx", "pxl")
_BARE_PIXEL = re.compile(rf"^{_PIXEL}$")

# Google: a tag ID only next to gtag/gtm context.
_GID = r"(AW-\d{6,12}(?:/[\w-]+)?|GTM-[A-Z0-9]{4,10}|G-[A-Z0-9]{6,12}|UA-\d{4,12}-\d{1,4})"
G_CONTEXT = re.compile(
    rf"gtag\(\s*{_Q}(?:config|event){_Q}\s*,\s*{_Q}{_GID}{_Q}"
    rf"|send_to{_Q}?\s*:\s*{_Q}{_GID}{_Q}"
    rf"|(?:gtag/js|gtm\.js|ns\.html)\?(?:[^\"'\s<>]*?&(?:amp;)?)?id={_GID}"
    rf"|GTM-[A-Z0-9]{{4,10}}(?={_Q}\s*\))"  # (window,document,'script','dataLayer','GTM-X')
)
_GTM_TAIL = re.compile(r"\(\s*window\s*,\s*document\s*,[^)]*?['\"](GTM-[A-Z0-9]{4,10})['\"]\s*\)")

# Other vendors: (namespace, pattern, text that must also be present). Only a literal ID counts.
VENDORS: tuple[tuple[str, re.Pattern[str], str | None], ...] = (
    ("quora_pixel", re.compile(rf"qp\(\s*{_Q}init{_Q}\s*,\s*{_Q}([0-9a-f]{{32}}){_Q}"), None),
    (
        "twitter_pixel",
        re.compile(rf"twq\(\s*{_Q}(?:init|config){_Q}\s*,\s*{_Q}([a-z0-9]{{5,6}}){_Q}"),
        None,
    ),
    ("tiktok_pixel", re.compile(rf"ttq\.load\(\s*{_Q}([A-Z0-9]{{20}}){_Q}"), None),
    ("bing_uet", re.compile(rf"\bti\s*:\s*{_Q}?(\d{{5,10}}){_Q}?\s*[,}}]"), "uetq"),
    (
        "bing_uet",
        re.compile(r"bat\.bing\.com/action/0\?(?:[^\"'\s<>]*?&(?:amp;)?)?ti=(\d{5,10})"),
        None,
    ),
    (
        "taboola_id",
        re.compile(
            r"_tfa\.push\(\{[^}]*?\bid\s*:\s*['\"]?(\d{6,8})|cdn\.taboola\.com/libtrc/unip/(\d{6,8})/"
        ),
        None,
    ),
    (
        "yandex_metrica",
        re.compile(
            rf"\bym\(\s*(\d{{6,10}})\s*,\s*{_Q}init{_Q}"
            r"|mc\.yandex\.(?:ru|com)/(?:watch/|metrika/tag\.js\?id=)(\d{6,10})"
        ),
        None,
    ),
)


def _first(match: re.Match[str]) -> str | None:
    return next((g for g in match.groups() if g), None)


@extractors.register
class TrackingPixels:
    name = "tracking.pixels"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        mat = m.load(view, ctx)
        emit = m.Emitter(view, self.name, self.version)
        own = m.own_domain(view)
        landing = m.landing_url(view)
        texts = [*mat.script_texts(), *([mat.html] if mat.html else [])]
        urls: list[tuple[str, Source]] = [(r.url, r.source) for r in mat.har_requests]
        urls += [(r.post_text, r.post_source) for r in mat.har_requests if r.post_text]
        urls += [(t.attrs.get("src", ""), t.source) for t in mat.script_srcs]

        def fb(value: str, source: Source, raw: str | None = None) -> None:
            emit("A3", "fb_pixel", value, source, raw=raw)

        def from_config() -> None:
            value = m.config_get(mat.aio, "visit.fields.fb_pixel")
            if isinstance(value, (str, int)) and _BARE_PIXEL.match(str(value).strip()):
                fb(str(value).strip(), m.at(mat.aio_source, "aio:visit.fields.fb_pixel"))

        def from_init_calls() -> None:
            for t in mat.script_texts():
                for match in FB_INIT.finditer(t.text):
                    fb(match.group(1), t.source, raw=match.group(0))

        def from_vendor_urls() -> None:
            for text, source in [*((t.text, t.source) for t in texts), *urls]:
                for match in FB_URL.finditer(text):
                    fb(_first(match), source, raw=match.group(0))

        def from_url_params() -> None:
            candidates = []
            if landing is not None:
                candidates.append((landing[0], landing[1]))
            candidates += [(f.attrs.get("action", ""), f.source) for f in mat.forms]
            candidates += [
                (r.url, r.source)
                for r in mat.har_requests
                if own and m.registrable_domain(m.host_of(r.url)) == own
            ]
            for url, source in candidates:
                for name, value in m.query_params(url):
                    if name in FB_PARAMS and _BARE_PIXEL.match(value):
                        fb(value, m.at(source, f"url:query.{name}"), raw=url)

        def from_hidden_fields() -> None:
            for tag in mat.hidden_inputs:
                name = (tag.attrs.get("name") or tag.attrs.get("id") or "").lower()
                value = tag.attrs.get("value", "").strip()
                if name in FB_FIELDS and _BARE_PIXEL.match(value):
                    fb(value, tag.source)

        def from_file_names() -> None:
            for t in mat.resource_names:
                match = _BARE_PIXEL.match(m._strip_save_suffix(t.text))
                if match:
                    fb(match.group(1), t.source, raw=t.text)

        def google() -> None:
            for text, source in [*((t.text, t.source) for t in texts), *urls]:
                found = [_first(x) for x in G_CONTEXT.finditer(text)]
                found += [x.group(1) for x in _GTM_TAIL.finditer(text)]
                for gid in found:
                    if not gid:
                        continue
                    if gid.startswith("AW-"):
                        account, _, label = gid.partition("/")
                        emit("A4", "gads_id", account, source, raw=gid)
                        if label:
                            emit("A4", "gads_conversion", gid, source)
                    elif gid.startswith("GTM-"):
                        emit("A4", "gtm_id", gid, source)
                    else:
                        emit("A4", "ga_id", gid, source)

        def other_vendors() -> None:
            for text, source in [*((t.text, t.source) for t in texts), *urls]:
                for namespace, pattern, gate in VENDORS:
                    if gate and gate not in text:
                        continue
                    for match in pattern.finditer(text):
                        emit("A5", namespace, _first(match), source, raw=match.group(0))

        m.run_steps(
            [
                from_config,
                from_init_calls,
                from_vendor_urls,
                from_url_params,
                from_hidden_fields,
                from_file_names,
                google,
                other_vendors,
            ]
        )
        return emit.out
