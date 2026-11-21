"""DNS enricher: A, NS, MX, the name-server pair and dead / parked status (signals C3, C13).

NS and MX are asked of the registrable domain; A and AAAA of the host the page was served
from, so a live sub-domain or an IPv6-only site is not called dead. Parking and sinkhole
name servers are shared by unrelated domains, so their pair is a risk fact, never a link.

Uses the system resolver through dnspython, so domains are not sent to a third-party DNS
service. When the zone has no NS answer (a dead or suspended domain) the delegation from the
RDAP record stands in, marked by its locator. A resolver failure gives not_observable.
"""

from __future__ import annotations

from typing import Any

import dns.exception
import dns.resolver

from ..contracts import Observation, Purpose, View
from ..registry import Context, enrichers
from . import _cache, rdap
from ._common import Out, view_domain, view_host

DNS_LIFETIME = 5.0

# Name servers of parking and for-sale services, and registrar defaults that serve a parking page.
PARKING_NS_SUFFIXES = (
    "dyna-ns.net", "sedoparking.com", "parkingcrew.net", "bodis.com", "above.com",
    "parklogic.com", "dan.com", "afternic.com", "uniregistrymarket.link", "namebrightdns.com",
    "ztomy.com",
)  # fmt: skip
# Addresses registrars put in a zone instead of a host.
PLACEHOLDER_IPS = frozenset({"0.0.0.0", "127.0.0.1", "1.1.1.1", "8.8.8.8", "255.255.255.255"})


def _clean(name: str) -> str:
    return name.lower().rstrip(".")


def ns_pair(nameservers: list[str]) -> str:
    """Both name servers, lower-cased, sorted, `|`-joined (docs/method-namespaces.md)."""
    return "|".join(sorted({_clean(n) for n in nameservers if n}))


def _query(resolver, name: str, rtype: str, errors: list[str]) -> list[str] | None:
    """Values of one record type; [] for no answer; None when the resolver failed.

    Raises NXDOMAIN to the caller, which decides what a missing name means.
    """
    try:
        answer = resolver.resolve(name, rtype)
    except dns.resolver.NoAnswer:
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        errors.append(f"{rtype} {name}: {type(exc).__name__}")
        return None
    if rtype in ("A", "AAAA"):
        return sorted(r.address for r in answer)
    if rtype == "NS":
        return sorted(_clean(r.target.to_text()) for r in answer)
    return sorted(_clean(r.exchange.to_text()) for r in answer if r.exchange)


def resolve(domain: str, host: str | None = None) -> dict[str, Any]:
    """NS and MX of the registrable domain; A and AAAA of the page's host (CNAMEs followed).

    Raises FetchError (not cached) when the resolver gives no usable answer at all.
    """
    host = host or domain
    resolver = dns.resolver.Resolver()
    resolver.lifetime = DNS_LIFETIME
    out: dict[str, Any] = {"host": host, "nxdomain": False, "host_nxdomain": False}
    errors: list[str] = []
    try:
        out["ns"] = _query(resolver, domain, "NS", errors)
        out["mx"] = _query(resolver, domain, "MX", errors)
    except dns.resolver.NXDOMAIN:
        out.update(nxdomain=True, host_nxdomain=True, ns=[], mx=[], a=[], aaaa=[])
        return out
    try:
        out["a"] = _query(resolver, host, "A", errors)
        out["aaaa"] = _query(resolver, host, "AAAA", errors)
    except dns.resolver.NXDOMAIN:
        out.update(host_nxdomain=True, a=[], aaaa=[])
    if all(out.get(k) is None for k in ("a", "aaaa", "ns", "mx")):
        raise _cache.FetchError("; ".join(errors) or "no answer", cacheable=False)
    out["errors"] = errors
    return out


def lookup(domain: str, host: str | None = None, cache: _cache.Cache | None = None):
    cache = cache or _cache.shared_cache()
    host = host or domain
    endpoint = f"dns:{host}" if host == domain else f"dns:{domain}+{host}"
    return cache.get("dns", f"{domain} {host}", lambda: (resolve(domain, host), endpoint), endpoint)


def _matches(host: str, suffixes) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def ns_is_shared_service(nameservers: list[str]) -> bool:
    """Parking or sinkhole name servers: shared by unrelated domains, never a link."""
    return any("sinkhole" in h or _matches(h, PARKING_NS_SUFFIXES) for h in nameservers)


def status(data: dict[str, Any]) -> tuple[str, str] | None:
    """(live | dead | parked | sinkholed, the rule that decided) or None when unknown."""
    if data.get("nxdomain"):
        return "dead", "NXDOMAIN"
    ns = data.get("ns") or []
    for host in ns:
        if "sinkhole" in host:
            return "sinkholed", f"name server {host}"
    for host in ns:
        if _matches(host, PARKING_NS_SUFFIXES):
            return "parked", f"parking name server {host}"
    if data.get("host_nxdomain"):
        return "dead", f"NXDOMAIN for {data.get('host')}"
    a, aaaa = data.get("a"), data.get("aaaa")
    if a is None and aaaa is None:
        return None
    addresses = (a or []) + (aaaa or [])
    if addresses and all(ip in PLACEHOLDER_IPS for ip in addresses):
        return "parked", f"placeholder address {', '.join(addresses)}"
    if not addresses:
        if a is None or aaaa is None:
            return None  # one family failed, the other was empty: not enough to call it dead
        return "dead", f"no A or AAAA record for {data.get('host')}"
    return "live", "resolves to an address"


@enrichers.register
class DnsEnricher:
    name = "dns"
    version = "2"

    def run(self, view: View, observations: list[Observation], ctx: Context) -> list[Observation]:
        domain = view_domain(view, observations)
        if not domain:
            return []
        host = view_host(view, observations, domain)
        out = Out(view, self.name, self.version)
        found = lookup(domain, host)
        info = {"domain": domain, "host": host, **found.provenance()}
        data = found.data if found.ok else {}
        decided = status(data) if found.ok else None

        # Records, one row per value.
        for ip in (data.get("a") or []) + (data.get("aaaa") or []):
            out.add("C13", "dns.a", ip, f"dns:{host}:A", info)
        for mx in data.get("mx") or []:
            out.add("C13", "dns.mx", mx, f"dns:{domain}:MX", info)

        # Name-server pair; the RDAP delegation stands in when the zone gives none.
        if data.get("ns"):
            purpose = Purpose.RISK if ns_is_shared_service(data["ns"]) else Purpose.LINKAGE
            out.add("C3", "dns.ns_pair", ns_pair(data["ns"]), f"dns:{domain}:NS", info, purpose)
        else:
            dead = decided is not None and decided[0] == "dead"
            self._ns_from_rdap(out, domain, info, dead)

        if decided is not None:
            value, rule = decided
            out.add("C13", "dns.status", value, f"dns:{host}", {**info, "rule": rule})
        else:
            reason = info if not found.ok else {**info, "error": "no usable address answer"}
            out.missing("C13", "dns.status", f"dns:{host}", reason)
        return out.items

    @staticmethod
    def _ns_from_rdap(out: Out, domain: str, info: dict[str, Any], dead: bool) -> None:
        reg = rdap.lookup(domain)
        nameservers = (reg.data or {}).get("nameservers") if reg.ok else None
        if nameservers:
            # A dead domain's delegation is often the registrar's suspension servers, shared
            # by unrelated domains, so it is kept as a risk fact, not a link.
            shared = dead or ns_is_shared_service(nameservers)
            out.add(
                "C3",
                "dns.ns_pair",
                ns_pair(nameservers),
                f"rdap:{reg.endpoint}:nameservers",
                {**info, "fallback": "rdap", "rdap": reg.provenance()},
                Purpose.RISK if shared else Purpose.LINKAGE,
                confidence=0.8,
            )
        else:
            missing = {**info, "rdap": reg.provenance()}
            out.missing("C3", "dns.ns_pair", f"dns:{domain}:NS", missing, Purpose.LINKAGE)
