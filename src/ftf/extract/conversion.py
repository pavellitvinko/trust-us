"""Where a page sends the visitor and their data (signals A6–A8, A12, A13, A15, B7, I3).

Conversion targets only: form actions, CTA links, beacon/fetch/XHR endpoints and the
requests a HAR shows the page making. Not every host a page loads from — image CDNs and
fonts say nothing about who runs it. Hosts the ingest noise list covers are skipped.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import unquote, urljoin, urlsplit

from ..contracts import Observation, Purpose, Source, View
from ..ingest.har import is_noise_host
from ..registry import Context, extractors
from . import _material as m

_Q = r"""['"`]"""

# JS constants naming an endpoint: LANDING_FORM_EVENT_BACKEND_DOMAIN = 'https://…'.
# The name must say where data goes; CDN_URL or ASSETS_PATH do not count.
ENDPOINT_CONST = re.compile(rf"\b([A-Z][A-Z0-9_]{{2,80}})\s*[:=]\s*{_Q}([^'\"`\s]{{2,300}}){_Q}")
_ENDPOINT_WORDS = ("BACKEND", "ENDPOINT", "API", "FORM", "ORDER", "LEAD", "SUBMIT", "POSTBACK")


def _concat_path(const: str) -> re.Pattern[str]:
    """`CONST + '/v1/x'` or `${CONST}/v1/x`: the path a kit appends to its endpoint."""
    name = re.escape(const)
    return re.compile(
        rf"\b{name}\s*\+\s*{_Q}(/[\w\-./]{{1,200}}){_Q}|\$\{{\s*{name}\s*\}}(/[\w\-./]{{1,200}})"
    )


# URL literals handed to a network call.
NETWORK_CALL = re.compile(
    rf"(?:sendBeacon|fetch|\.open|\.post|\.ajax|axios(?:\.\w+)?)\(\s*"
    rf"(?:{_Q}[A-Z]+{_Q}\s*,\s*)?{_Q}(https?://[^'\"`\s]+){_Q}"
)
# Redirects in onclick handlers and inline scripts.
REDIRECT = re.compile(
    rf"(?:location(?:\.href)?\s*=|location\.(?:assign|replace)\(|window\.open\()\s*{_Q}(https?://[^'\"`\s]+){_Q}"
)
KEY_ID = re.compile(
    rf"(?:\b[A-Z][A-Z0-9_]*KEY_ID|\b(?:key_?id|keyId))\b{_Q}?\s*[:=]\s*{_Q}([\w.\-]{{2,64}}){_Q}"
)
PEM = re.compile(
    r"-----BEGIN (?:RSA )?PUBLIC KEY-----(.+?)-----END (?:RSA )?PUBLIC KEY-----", re.DOTALL
)

# Affiliate fields, by hidden-input or URL-parameter name. Generic fields are left out.
AFF_FIELDS = {
    "network": "aff.network",
    "aff_network": "aff.network",
    "user_id": "aff.user_id",
    "webmaster_id": "aff.user_id",
    "wm_id": "aff.user_id",
    "aff_id": "aff.user_id",
    "affiliate_id": "aff.user_id",
    "offer_id": "aff.offer_uuid",
    "offer_uuid": "aff.offer_uuid",
    "landing_id": "aff.landing_id",
    "lp_id": "aff.landing_id",
    "_trk": "aff.tracker",
    "trk": "aff.tracker",
    "tracker": "aff.tracker",
    "sid": "aff.sid",
    "x_id": "aff.x_id",
}
_SUB = re.compile(r"^sub_?(?:id)?_?(\d{1,2})$")
OFFER_NAME_FIELDS = ("offer", "product", "product_name", "offer_name", "goods", "item_name")
_ID_LIKE = re.compile(r"^(?:\d+|[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{16,})$", re.IGNORECASE)

PHONE = re.compile(r"^\+?\d{7,15}$")
EMAIL = re.compile(r"^[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+$")
HANDLE = re.compile(r"^[A-Za-z][\w]{3,31}$")
IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?)\b")
_TAGS = re.compile(r"<(script|style)\b.*?</\1\s*>|<[^>]+>", re.IGNORECASE | re.DOTALL)
CONVERSION_TYPES = {"fetch", "xhr", "ping", "beacon"}
THIRD_PARTY_TYPES = {"script", "fetch", "xhr", "ping", "beacon", "iframe", "sub_frame"}


def iban_valid(raw: str) -> bool:
    text = raw.replace(" ", "")
    if not 15 <= len(text) <= 34:
        return False
    moved = text[4:] + text[:4]
    digits = "".join(str(int(ch, 36)) for ch in moved)
    return int(digits) % 97 == 1


def pem_fingerprint(body: str) -> str:
    clean = re.sub(r"\\[nr]|\s", "", body)
    return "sha256:" + hashlib.sha256(clean.encode()).hexdigest()


@extractors.register
class ConversionTargets:
    name = "conversion.targets"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        mat = m.load(view, ctx)
        if mat.html is None and not mat.har_requests:
            return []
        emit = m.Emitter(view, self.name, self.version)
        own = m.own_domain(view)
        landing = m.landing_url(view)
        base = mat.base_href or (landing[0] if landing else "")
        scripts = mat.script_texts()

        def absolute(url: str) -> str:
            try:
                return urljoin(base, url.strip()) if base else url.strip()
            except ValueError:
                return url.strip()

        def foreign(host: str) -> bool:
            return (
                bool(host)
                and "." in host
                and not is_noise_host(host)
                and (own is None or m.registrable_domain(host) != own)
            )

        def outbound(url: str, source: Source) -> None:
            host = m.host_of(url)
            if foreign(host):
                emit("A15", "outbound.domain", m.registrable_domain(host), source, raw=url)

        def endpoint(url: str, source: Source) -> None:
            """A place form data is sent: its host when foreign, and its path."""
            host = m.host_of(url) if "//" in url else ""
            if foreign(host):
                emit("A7", "form_endpoint_host", host, source, raw=url)
            try:
                path = urlsplit(url).path.rstrip("/") if "//" in url or url.startswith("/") else ""
            except ValueError:
                path = ""
            if path:
                emit("A7", "form_endpoint_path", path, source, raw=url)

        def forms() -> None:
            for form in mat.forms:
                action = form.attrs.get("action", "").strip()
                if not action or action.startswith(("#", "javascript:")):
                    continue
                url = absolute(action)
                endpoint(url if "//" in action else urlsplit(url).path, form.source)
                outbound(url, form.source)

        def endpoint_constants() -> None:
            names: list[str] = []
            for t in scripts:
                for match in ENDPOINT_CONST.finditer(t.text):
                    const, value = match.groups()
                    if not any(word in const for word in _ENDPOINT_WORDS):
                        continue
                    if "//" in value or value.startswith("/"):
                        endpoint(value, t.source)
                    elif re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", value, re.IGNORECASE):
                        endpoint(f"https://{value}", t.source)
                    else:
                        continue
                    names.append(const)
            for const in dict.fromkeys(names):
                pattern = _concat_path(const)
                for t in scripts:
                    for match in pattern.finditer(t.text):
                        path = (match.group(1) or match.group(2)).rstrip("/")
                        emit("A7", "form_endpoint_path", path, t.source, raw=match.group(0))

        def anchors() -> None:
            for a in mat.anchors:
                href = a.attrs.get("href", "").strip()
                if href.startswith(("http://", "https://", "//")):
                    outbound(absolute(href), a.source)
                for match in REDIRECT.finditer(a.attrs.get("onclick", "")):
                    outbound(match.group(1), a.source)

        def script_calls() -> None:
            for t in scripts:
                for pattern in (NETWORK_CALL, REDIRECT):
                    for match in pattern.finditer(t.text):
                        outbound(match.group(1), t.source)

        def har_conversions() -> None:
            for r in mat.har_requests:
                if r.method == "POST" or r.resource_type in CONVERSION_TYPES:
                    outbound(r.url, r.source)

        def third_party() -> None:
            hosts: list[tuple[str, Source]] = [
                (r.url, r.source) for r in mat.har_requests if r.resource_type in THIRD_PARTY_TYPES
            ]
            hosts += [(absolute(t.attrs.get("src", "")), t.source) for t in mat.script_srcs]
            hosts += [(absolute(t.attrs.get("src", "")), t.source) for t in mat.iframes]
            for url, source in hosts:
                host = m.host_of(url)
                if foreign(host):
                    emit("B7", "third_party.host", host, source, raw=url)

        def keys() -> None:
            for t in scripts:
                for match in KEY_ID.finditer(t.text):
                    emit("A8", "api_key_id", match.group(1), t.source, raw=match.group(0))
                for match in PEM.finditer(t.text):
                    emit("A8", "public_key", pem_fingerprint(match.group(1)), t.source)
            push = m.config_get(mat.aio, "push.key")
            if isinstance(push, str):
                emit("A8", "push_public_key", push.strip(), m.at(mat.aio_source, "aio:push.key"))
            for tag in mat.hidden_inputs:
                if (tag.attrs.get("name") or "").lower() == "api_key":
                    emit("A8", "aff.api_key", tag.attrs.get("value", "").strip(), tag.source)

        def affiliate_fields() -> None:
            fields: list[tuple[str, str, Source]] = [
                ((t.attrs.get("name") or "").lower(), t.attrs.get("value", "").strip(), t.source)
                for t in mat.hidden_inputs
            ]
            urls = [(landing[0], landing[1])] if landing and landing[2] else []
            urls += [(f.attrs.get("action", ""), f.source) for f in mat.forms]
            for url, source in urls:
                for name, value in m.query_params(url):
                    fields.append((name, value, m.at(source, f"url:query.{name}")))
            for name, value, source in fields:
                namespace = AFF_FIELDS.get(name)
                sub = _SUB.match(name)
                if sub:
                    namespace = f"aff.sub{int(sub.group(1))}"
                if namespace:
                    emit("A6", namespace, value, source)
                elif name in OFFER_NAME_FIELDS:
                    if _ID_LIKE.match(value):
                        emit("A6", "aff.offer_uuid", value, source)
                    else:
                        emit("A13", "offer.name", value, source, purpose=Purpose.BOTH)

        def contacts() -> None:
            for a in mat.anchors:
                href = unquote(a.attrs.get("href", "").strip())
                lower = href.lower()
                if lower.startswith("tel:"):
                    phone = re.sub(r"[^\d+]", "", href[4:])
                    if PHONE.match(phone):
                        emit("A12", "contact.phone", phone, a.source, raw=href)
                elif lower.startswith("mailto:"):
                    email = href[7:].split("?")[0].strip().lower()
                    if EMAIL.match(email):
                        emit("A12", "contact.email", email, a.source, raw=href)
                elif "t.me/" in lower or "telegram.me/" in lower:
                    handle = urlsplit(href).path.strip("/").split("/")[0].lstrip("@")
                    if HANDLE.match(handle):
                        emit("A12", "contact.telegram", handle.lower(), a.source, raw=href)
                elif "wa.me/" in lower or "whatsapp.com/send" in lower:
                    phone = urlsplit(href).path.strip("/") if "wa.me/" in lower else ""
                    phone = phone or dict(m.query_params(href)).get("phone", "")
                    phone = re.sub(r"\D", "", phone)
                    if PHONE.match(phone):
                        emit("A12", "contact.whatsapp", phone, a.source, raw=href)
            if mat.html is not None:
                visible = _TAGS.sub(" ", mat.html.text)
                for match in IBAN.finditer(visible):
                    if iban_valid(match.group(1)):
                        value = match.group(1).replace(" ", "")
                        emit("A12", "contact.iban", value, mat.html.source, raw=match.group(1))

        m.run_steps(
            [
                forms,
                endpoint_constants,
                anchors,
                script_calls,
                har_conversions,
                third_party,
                keys,
                affiliate_fields,
                contacts,
            ]
        )
        return emit.out
