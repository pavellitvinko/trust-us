"""Certificate transparency enricher: certificate history from crt.sh (signals C8, C9).

One crt.sh query per registrable domain (`https://crt.sh/?q=<domain>&output=json`) gives every
logged certificate naming the domain or a host under it. From it:

- `ct.cert_count`, `ct.first_seen`, `ct.issuer`, `ct.wildcard`, `ct.sibling_host` (C8);
- `ct.prior_life_days` (C9): certificates older than the current registration, a sign the
  domain had an earlier owner or was dropped and caught again (`glowpixel.org`: certificates
  from 2025-05, registered 2026-09). Registries stamp the registration event minutes to hours
  after the first certificate of a fresh domain, so a gap under `PRIOR_LIFE_MARGIN_DAYS` is not
  counted;
- `ct.san_domain` and `ct.shared_cert`: a certificate that also names another registrable
  domain. The link value is the sorted set of registrable domains on the certificate
  (`a.xyz|b.top`), the same on both sides and the same across renewals, so a year of renewals
  is one shared fact, not five. Only certificates issued under the current registration link:
  an earlier owner's certificate says nothing about who runs the domain now, and without RDAP
  nothing links. CDN and hosting certificates that bundle unrelated customers never link.

crt.sh logs a pre-certificate and the final certificate under one serial; they count once.
crt.sh is slow and often fails (502, 404, timeouts): failures are not cached and give
not_observable, never a negative finding. Under load it also answers `[]` for domains that do
have certificates, so an empty answer is not observable too (cached one hour). After `BREAKER_FAILURES` failures in a row the
enricher stops asking for `BREAKER_PAUSE_S`, so a crt.sh outage costs seconds, not minutes.
"""

from __future__ import annotations

import ipaddress
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from ..contracts import Observation, Purpose, View
from ..registry import Context, enrichers
from . import _cache, rdap
from ._capture import parse_time
from ._common import Out, view_domain, view_host

CRTSH_URL = "https://crt.sh/?q={domain}&output=json"
# The cache holds parsed certificates, not crt.sh's rows: bump when `parse_crtsh` changes.
CACHE_SOURCE = "ct-v2"
EMPTY_ANSWER = "crt.sh listed no certificate (empty answers are not trusted)"
CRTSH_TIMEOUT = 45.0  # crt.sh took up to 28 s for small domains on 2026-11-22
MAX_CERTS = 2000  # kept per domain: the earliest and the newest ones
MAX_SIBLINGS = 25
MAX_SHARED_DOMAINS = 5  # registrable domains on one certificate, own one included
PRIOR_LIFE_MARGIN_DAYS = 30
BREAKER_FAILURES = 3
BREAKER_PAUSE_S = 600.0

# Certificates issued by CDNs and hosts for many unrelated customers at once.
SHARED_CERT_DOMAINS = frozenset(
    {
        "cloudflaressl.com", "cloudflare.com", "cloudflare-dns.com", "incapsula.com",
        "fastly.net", "fastlylb.net", "akamaized.net", "edgekey.net", "wpengine.com",
        "kinsta.cloud", "squarespace.com", "myshopify.com", "shopify.com", "wixsite.com",
        "wix.com", "netlify.app", "vercel.app", "herokuapp.com", "github.io", "sucuri.net",
    }
)  # fmt: skip
# A hosting server's own name on a certificate that also covers its customers' sites
# (Plesk "secure the hostname with all domains", reseller panels).
_SERVER_LABEL = re.compile(
    r"^(server|srv|host|hosting|vps|vds|dedi|node|cp|cpanel|plesk|whm|panel|web)[-\d]*$"
)

NAMESPACES = (
    "ct.cert_count",
    "ct.first_seen",
    "ct.issuer",
    "ct.wildcard",
    "ct.prior_life_days",
)

_DN_PART = re.compile(r'\s*([A-Za-z]+)=("(?:[^"]|"")*"|[^,]*)')
_HOSTNAME = re.compile(r"^(\*\.)?[a-z0-9_-]+(\.[a-z0-9_-]+)+$")


def parse_dn(dn: str) -> dict[str, str]:
    """Attributes of an issuer name as crt.sh prints it (`C=US, O="X, Inc.", CN=Y`)."""
    out: dict[str, str] = {}
    for key, value in _DN_PART.findall(dn or ""):
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('""', '"')
        out.setdefault(key.upper(), value)
    return out


def issuer_org(dn: str) -> str:
    """The issuing organisation, lower-cased (`let's encrypt`, `sectigo limited`)."""
    parts = parse_dn(dn)
    return (parts.get("O") or parts.get("CN") or dn).strip().lower()


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def _names(row: dict[str, Any]) -> set[str]:
    """DNS names on a certificate. crt.sh also lists e-mail addresses and IPs: dropped."""
    raw = f"{row.get('name_value') or ''}\n{row.get('common_name') or ''}"
    names = {n.strip().lower().rstrip(".") for n in raw.split("\n")}
    return {n for n in names if _HOSTNAME.match(n) and not _is_ip(n)}


def _under(name: str, domain: str) -> bool:
    name = name.removeprefix("*.")
    return name == domain or name.endswith("." + domain)


def covers(name: str, host: str) -> bool:
    """True when a certificate name is valid for `host` (exact, or a one-label wildcard)."""
    if name == host:
        return True
    if name.startswith("*."):
        return host.partition(".")[2] == name[2:]
    return False


def _id(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_crtsh(rows: list[dict[str, Any]], domain: str) -> dict[str, Any]:
    """Certificates naming `domain`, one per (CA, serial), oldest first."""
    from ..extract._material import registrable_domain

    certs: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        names = _names(row)
        if not any(_under(n, domain) for n in names):
            continue  # crt.sh search also matches look-alike names
        serial = str(row.get("serial_number") or "")
        key: tuple[Any, ...] = (
            (row.get("issuer_ca_id"), serial)
            if serial
            else ("id", row.get("id") or f"{row.get('not_before')}|{sorted(names)}")
        )
        seen = certs.get(key)
        if seen is not None:
            seen["names"] = sorted(set(seen["names"]) | names)
            ids = [i for i in (seen["id"], row.get("id")) if i is not None]
            seen["id"] = min(ids, key=_id) if ids else None
            continue
        certs[key] = {
            "id": row.get("id"),
            "ca_id": row.get("issuer_ca_id"),
            "serial": serial,
            "issuer": row.get("issuer_name") or "",
            "not_before": row.get("not_before"),
            "not_after": row.get("not_after"),
            "names": sorted(names),
        }
    ordered = sorted(certs.values(), key=lambda c: (str(c["not_before"]), _id(c["id"])))
    truncated = len(ordered) > MAX_CERTS
    if truncated:
        ordered = ordered[:1] + ordered[-(MAX_CERTS - 1) :]
    for cert in ordered:
        others = {
            registrable_domain(n.removeprefix("*.")) for n in cert["names"] if not _under(n, domain)
        }
        others.discard(domain)
        cert["other_domains"] = sorted(others)
        cert["hosting_bundle"] = _hosting_bundle(cert["names"], domain)
    return {"domain": domain, "certs": ordered, "total": len(certs), "truncated": truncated}


def _hosting_bundle(names: list[str], domain: str) -> bool:
    """A server host name of another domain on the certificate: the hoster's, not the owner's."""
    return any(
        not _under(n, domain) and _SERVER_LABEL.match(n.removeprefix("*.").split(".", 1)[0])
        for n in names
    )


# --- crt.sh access ------------------------------------------------------------------------

_breaker_lock = threading.Lock()
_breaker = {"fails": 0, "open_until": 0.0}


def reset_breaker() -> None:
    with _breaker_lock:
        _breaker.update(fails=0, open_until=0.0)


def _breaker_open() -> bool:
    with _breaker_lock:
        return time.monotonic() < _breaker["open_until"]


def _breaker_record(ok: bool) -> None:
    with _breaker_lock:
        if ok:
            _breaker.update(fails=0, open_until=0.0)
            return
        _breaker["fails"] += 1
        if _breaker["fails"] >= BREAKER_FAILURES:
            _breaker.update(fails=0, open_until=time.monotonic() + BREAKER_PAUSE_S)


def lookup(domain: str, cache: _cache.Cache | None = None) -> _cache.Lookup:
    """crt.sh certificate history for a registrable domain (cached)."""
    cache = cache or _cache.shared_cache()
    url = CRTSH_URL.format(domain=quote(domain, safe=""))

    def get():
        try:
            return cache.http_json(url, timeout=CRTSH_TIMEOUT)
        except _cache.FetchError as exc:
            # crt.sh answers "nothing logged" with `200 []`; any error (404 included, seen on
            # 2026-11-22 between 502s) is its own trouble and says nothing about the domain.
            raise _cache.FetchError(str(exc), cacheable=False) from exc

    def fetch():
        if _breaker_open():
            raise _cache.FetchError("crt.sh paused after repeated failures", cacheable=False)
        try:
            try:
                rows = get()
            except _cache.FetchError as exc:
                if not str(exc).startswith("HTTP"):
                    raise  # a timeout already cost CRTSH_TIMEOUT; do not wait twice
                rows = get()  # one retry: crt.sh 502s and 404s come and go
        except _cache.FetchError:
            _breaker_record(ok=False)
            raise
        _breaker_record(ok=True)
        if not isinstance(rows, list):
            raise _cache.FetchError("crt.sh answered with something other than a list", False)
        if not rows:
            # Under load crt.sh also answers `[]` for domains that have certificates
            # (healthlinkq.pro, 2026-11-22: 3 certificates, then `[]`). An empty answer is
            # therefore not a fact: not observable, cached for ERROR_TTL, and an earlier
            # non-empty answer is used instead when there is one.
            raise _cache.FetchError(EMPTY_ANSWER)
        return parse_crtsh(rows, domain), url

    return cache.get(CACHE_SOURCE, domain, fetch, url)


def linkable(cert: dict[str, Any]) -> bool:
    """A certificate naming a few other registrable domains, not a CDN or hosting bundle."""
    others = cert.get("other_domains") or []
    return (
        0 < len(others) <= MAX_SHARED_DOMAINS - 1  # MAX_SHARED_DOMAINS counts the own domain
        and not SHARED_CERT_DOMAINS.intersection(others)
        and not cert.get("hosting_bundle")
    )


def cert_url(cert: dict[str, Any]) -> str:
    return f"https://crt.sh/?id={cert['id']}" if cert.get("id") else "https://crt.sh/"


def _registered(domain: str) -> tuple[datetime | None, _cache.Lookup]:
    reg = rdap.lookup(domain)
    data = reg.data if reg.ok and isinstance(reg.data, dict) else {}
    return parse_time(data.get("registered_at")), reg


@enrichers.register
class CtEnricher:
    name = "ct"
    version = "2"

    def run(self, view: View, observations: list[Observation], ctx: Context) -> list[Observation]:
        domain = view_domain(view, observations)
        if not domain:
            return []
        host = view_host(view, observations, domain)
        out = Out(view, self.name, self.version)
        found = lookup(domain)
        info = {"domain": domain, **found.provenance()}
        locator = f"crtsh:{found.endpoint}"
        if not found.ok:
            for ns in NAMESPACES:
                signal = "C9" if ns == "ct.prior_life_days" else "C8"
                out.missing(signal, ns, locator, info)
            return out.items

        certs: list[dict[str, Any]] = found.data.get("certs") or []
        if found.data.get("truncated"):
            info["truncated"] = True  # the middle of a long history was not kept
        issuers: dict[str, int] = {}
        for cert in certs:
            org = issuer_org(cert["issuer"])
            issuers[org] = issuers.get(org, 0) + 1
        out.add(
            "C8",
            "ct.cert_count",
            int(found.data.get("total", len(certs))),
            locator,
            {**info, "issuers": issuers},
        )
        if not certs:
            empty = {**info, "error": "no certificate for this domain in the CT logs"}
            for ns in NAMESPACES[1:]:
                signal = "C9" if ns == "ct.prior_life_days" else "C8"
                out.missing(signal, ns, locator, empty)
            return out.items

        first = certs[0]
        first_at = parse_time(first["not_before"])
        out.add(
            "C8",
            "ct.first_seen",
            first_at.date().isoformat() if first_at else str(first["not_before"])[:10],
            f"crtsh:{cert_url(first)}",
            {**info, "issuer": first["issuer"], "names": first["names"]},
        )
        registered, reg = _registered(domain)
        self._current(out, certs, host, info, locator)
        self._siblings(out, certs, domain, host, info)
        self._prior_life(out, first, first_at, registered, reg, info, locator)
        self._shared(out, certs, domain, registered, info)
        return out.items

    @staticmethod
    def _current(out: Out, certs, host: str, info, locator: str) -> None:
        """Issuer and wildcard use of the newest certificate valid for the page's host."""
        for_host = [c for c in certs if any(covers(n, host) for n in c["names"])]
        newest = (for_host or certs)[-1]
        detail = {
            **info,
            "host": host,
            "issuer_dn": newest["issuer"],
            "not_before": newest["not_before"],
            "not_after": newest["not_after"],
            "names": newest["names"],
            "covers_host": bool(for_host),
        }
        where = f"crtsh:{cert_url(newest)}"
        if not for_host:
            # No logged certificate names this host: the domain's newest one stands in.
            detail["note"] = "no certificate names the page's host; newest for the domain"
        out.add(
            "C8",
            "ct.issuer",
            issuer_org(newest["issuer"]),
            where,
            detail,
            confidence=1.0 if for_host else 0.5,
        )
        if not for_host:
            out.missing(
                "C8",
                "ct.wildcard",
                locator,
                {**info, "host": host, "error": "no certificate names the page's host"},
            )
            return
        wildcards = [c for c in for_host if any(n.startswith("*.") for n in c["names"])]
        out.add(
            "C8",
            "ct.wildcard",
            bool(wildcards),
            where,
            {**info, "host": host, "wildcard_certs": len(wildcards), "host_certs": len(for_host)},
        )

    @staticmethod
    def _siblings(out: Out, certs, domain: str, host: str, info) -> None:
        """Other host names under the domain: sub-sites, mail, panels the operator set up."""
        first_seen: dict[str, dict[str, Any]] = {}
        for cert in certs:
            for name in cert["names"]:
                if name.startswith("*.") or name in (domain, host) or not _under(name, domain):
                    continue
                first_seen.setdefault(name, cert)
        for name in sorted(first_seen)[:MAX_SIBLINGS]:
            cert = first_seen[name]
            out.add(
                "C8",
                "ct.sibling_host",
                name,
                f"crtsh:{cert_url(cert)}",
                {**info, "first_seen": cert["not_before"], "of": len(first_seen)},
            )

    @staticmethod
    def _prior_life(out: Out, first, first_at, registered, reg, info, locator) -> None:
        if registered is None or first_at is None:
            reason = reg.error if not reg.ok else "no registration date or certificate date"
            out.missing(
                "C9",
                "ct.prior_life_days",
                locator,
                {**info, "error": reason, "rdap_endpoint": reg.endpoint},
            )
            return
        gap = (registered - first_at).days
        days = gap if gap > PRIOR_LIFE_MARGIN_DAYS else 0
        detail = {
            **info,
            "first_cert_at": first["not_before"],
            "first_cert_issuer": first["issuer"],
            "registered_at": registered.isoformat(),
            "gap_days": gap,
            "margin_days": PRIOR_LIFE_MARGIN_DAYS,
            "rdap_endpoint": reg.endpoint,
        }
        if days:
            detail["note"] = "certificates predate the current registration (earlier owner)"
        out.add("C9", "ct.prior_life_days", days, f"crtsh:{cert_url(first)}", detail)

    @staticmethod
    def _shared(out: Out, certs, domain: str, registered: datetime | None, info) -> None:
        """Other registrable domains on one certificate.

        `ct.san_domain` (risk, a pivot) for every such certificate; `ct.shared_cert` (the link)
        only for certificates issued under the current registration, one row per domain set.
        """
        since = registered - timedelta(days=PRIOR_LIFE_MARGIN_DAYS) if registered else None
        sets: dict[str, list[dict[str, Any]]] = {}
        others: dict[str, dict[str, Any]] = {}
        for cert in certs:
            if not linkable(cert):
                continue
            issued = parse_time(cert["not_before"])
            current = since is not None and issued is not None and issued >= since
            for other in cert["other_domains"]:
                seen = others.get(other)
                if seen is None or (current and not seen["current"]):
                    others[other] = {"cert": cert, "current": current}
            if current:
                key = "|".join(sorted({domain, *cert["other_domains"]}))
                sets.setdefault(key, []).append(cert)
        for key, group in sorted(sets.items()):
            newest = group[-1]
            out.add(
                "C8",
                "ct.shared_cert",
                key,
                f"crtsh:{cert_url(newest)}",
                {
                    **info,
                    "certificates": len(group),
                    "first_not_before": group[0]["not_before"],
                    "crtsh_ids": [c["id"] for c in group][:20],
                    "serials": [f"{c['ca_id']}:{c['serial']}" for c in group][:20],
                    "registered_at": registered.isoformat() if registered else None,
                },
                purpose=Purpose.LINKAGE,
            )
        for other, seen in sorted(others.items()):
            cert = seen["cert"]
            detail = {**info, "names": cert["names"], "not_before": cert["not_before"]}
            if not seen["current"]:
                detail["note"] = (
                    "issued before the current registration or registration unknown; not a link"
                )
            out.add("C8", "ct.san_domain", other, f"crtsh:{cert_url(cert)}", detail)
