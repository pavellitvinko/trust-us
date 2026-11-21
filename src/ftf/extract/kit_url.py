"""Tracker-kit fingerprints from URLs and paths (signals B1, B2, B5, B6).

These identify the software behind a page, not one campaign: they survive when every
ID is rotated. Only names and shapes leave this module, never a per-click value
(`fbclid`, a `utm_visit` UUID, a click ID).

The ad platforms' own parameters are on every page those platforms send traffic to,
so they are stoplisted. A standard ID parameter whose value does not have the
platform's form (Meta's `utm_id` is a long number) is kept as a shape: a kit filled
it in.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..contracts import Observation, View
from ..registry import Context, extractors
from . import _material as m

# Parameters the ad platforms and analytics add themselves (catalogue stoplist).
STANDARD_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "utm_id",
    "fbclid", "gclid", "gbraid", "wbraid", "dclid", "ttclid", "msclkid", "yclid",
    "ad_id", "adset_id", "campaign_id", "ad_name", "adset_name", "campaign_name",
    "placement", "site_source_name",
})  # fmt: skip

# Standard ID parameters and the shape the platform gives them.
_STANDARD_ID_SHAPE = re.compile(r"^digits\d+$")
_STANDARD_IDS = frozenset({"utm_id", "ad_id", "adset_id", "campaign_id"})

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_HEX = re.compile(r"^[0-9a-f]+$", re.IGNORECASE)
_B64 = re.compile(r"^[A-Za-z0-9+/_\-]+={0,2}$")
_LITERAL = re.compile(r"^_?[a-z]+(?:[-_][a-z]+){0,2}$")
_MAX_LITERAL = 24
_MAX_DEPTH = 4
# Framework and CDN folders every unrelated site has.
_STOP_PREFIXES = frozenset({"_next", "cdn-cgi", "wp-content", "wp-includes", "wp-json"})
_VERSION = re.compile(r"^v\d+$")
_API_SEGMENTS = frozenset({"api", "ajax", "rpc", "graphql"})

_BASE_HREF = re.compile(r"""<base\b[^>]*?\bhref\s*=\s*\\?["']([^"'\\]+)""", re.IGNORECASE)
_API_LITERAL = re.compile(r"""["'`](/(?:api|ajax|rpc|graphql)/[^"'`\s?#]{0,200})""")


def value_shape(value: str) -> str:
    """The form of a value without the value: `uuid`, `digits18`, `alnum20`, `text`…"""
    v = value.strip()
    n = len(v)
    if _UUID.match(v):
        return "uuid"
    if v.isdigit():
        return f"digits{n}"
    if v.isascii() and v.isalnum():
        if _HEX.match(v) and any(c.isdigit() for c in v) and n >= 8:
            return f"hex{n}"
        if v.islower():
            return f"lower{n}"
        if v.isupper():
            return f"upper{n}"
        if v.isalpha():
            return f"alpha{n}"
        return f"alnum{n}"
    if n >= 16 and _B64.match(v):
        return f"b64{n}"
    return "text"


def _mask(segment: str) -> str:
    """Character classes with runs collapsed: `L41156846fl-923e569` -> `A9a-9a9`."""
    out: list[str] = []
    for c in segment:
        k = "9" if c.isdigit() else "A" if c.isupper() else "a" if c.isalpha() else c
        if not out or out[-1] != k:
            out.append(k)
    return "".join(out)


def segment_shape(segment: str) -> tuple[str, bool]:
    """(shape, is_literal) of one path segment."""
    s = segment.strip()
    if len(s) <= _MAX_LITERAL and _LITERAL.match(s):
        return s, True
    if _UUID.match(s):
        return "<uuid>", False
    if s.isdigit():
        return "<num>", False
    if len(s) >= 8 and _HEX.match(s):
        return "<hex>", False
    return "<" + _mask(s) + ">", False


def path_convention(path: str) -> str | None:
    """Directory segments up to and including the first variable one, at most four.

    `/lander/prstt-pl-x/index.html` -> `/lander/<a-a-a>/`. None for the root, a lone
    literal folder (`/css/`), a framework folder (`/_next/…`) or an API path (B6).
    """
    segments = [s for s in path.split("/") if s]
    if segments and not path.endswith("/"):
        segments = segments[:-1]  # the last segment is a file
    if not segments or segments[0].lower() in _STOP_PREFIXES | _API_SEGMENTS:
        return None
    out: list[str] = []
    variable = False
    for seg in segments[:_MAX_DEPTH]:
        shape, literal = segment_shape(seg)
        out.append(shape)
        if not literal:
            variable = True
            break
    if not variable and len(out) < 2:
        return None
    return "/" + "/".join(out) + "/"


def api_route(path: str) -> str | None:
    """The static prefix of an API path: `/api/v1/trigger/field/<uuid>/` -> `/api/v1/trigger/field/`."""
    segments = [s for s in path.split("/") if s]
    if not segments or not any(s.lower() in _API_SEGMENTS for s in segments[:2]):
        return None
    out: list[str] = []
    for seg in segments:
        if not _VERSION.match(seg) and (any(c.isdigit() for c in seg) or not seg.isascii()):
            break  # an ID, a hash or a number: the route's variable part starts here
        out.append(seg.lower())
    if len(out) < 2:
        return None
    return "/" + "/".join(out) + "/"


def _path_of(url: str) -> str:
    try:
        return urlsplit(url).path or ""
    except ValueError:
        return ""


@extractors.register
class KitUrl:
    name = "kit.url"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        emit = m.Emitter(view, self.name, self.version)
        found = m.landing_url(view)
        landing = found if found is not None and found[2] else None
        mat = m.load(view, ctx)
        own = m.own_domain(view)

        def params() -> None:
            if landing is None:
                return
            url, source, _ = landing
            for name, value in m.query_params(url):
                where = m.at(source, f"url:query.{name}")
                shape = value_shape(value)
                if name in STANDARD_PARAMS:
                    if name in _STANDARD_IDS and not _STANDARD_ID_SHAPE.match(shape):
                        emit("B2", "url.param_shape", f"{name}:{shape}", where, raw=name)
                    continue
                emit("B1", "url.param_name", name, where, raw=name)
                emit("B2", "url.param_shape", f"{name}:{shape}", where, raw=name)

        def paths() -> None:
            if landing is not None:
                conv = path_convention(_path_of(landing[0]))
                if conv:
                    emit("B5", "url.path_shape", conv, m.at(landing[1], "url:path"))
            if mat.html is not None:
                for match in _BASE_HREF.finditer(mat.html.text):
                    conv = path_convention(_path_of(match.group(1)))
                    if conv:
                        emit("B5", "url.path_shape", conv, m.at(mat.html.source, "base.href"))
            for req in mat.har_requests:
                if own and m.registrable_domain(m.host_of(req.url)) == own:
                    conv = path_convention(_path_of(req.url))
                    if conv:
                        emit("B5", "url.path_shape", conv, req.source)

        def routes() -> None:
            for req in mat.har_requests:
                if own and m.registrable_domain(m.host_of(req.url)) == own:
                    route = api_route(_path_of(req.url))
                    if route:
                        emit("B6", "api.route", route, req.source)
            for text in mat.script_texts():
                if m.is_library_script(text.source.file):
                    continue
                for match in _API_LITERAL.finditer(text.text):
                    route = api_route(match.group(1))
                    if route:
                        emit("B6", "api.route", route, m.at(text.source, "literal"))

        m.run_steps([params, paths, routes])
        return emit.out
