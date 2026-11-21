"""DNS enricher: A, NS, MX, the name-server pair and dead / parked status (signals C3, C13).

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
from ._common import Out, view_domain

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


def resolve(domain: str) -> dict[str, Any]:
    """Query A, NS, MX. Raises FetchError when the resolver gives no usable answer at all."""
    resolver = dns.resolver.Resolver()
    resolver.lifetime = DNS_LIFETIME
    out: dict[str, Any] = {"nxdomain": False, "a": None, "ns": None, "mx": None}
    errors = []
    for rtype in ("A", "NS", "MX"):
        key = rtype.lower()
        try:
            answer = resolver.resolve(domain, rtype)
        except dns.resolver.NXDOMAIN:
            out["nxdomain"] = True
            out["a"] = out["ns"] = out["mx"] = []
            return out
        except dns.resolver.NoAnswer:
            out[key] = []
            continue
        except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
            errors.append(f"{rtype}: {type(exc).__name__}")
            continue
        if rtype == "A":
            out[key] = sorted(r.address for r in answer)
        elif rtype == "NS":
            out[key] = sorted(_clean(r.target.to_text()) for r in answer)
        else:
            out[key] = sorted(_clean(r.exchange.to_text()) for r in answer if r.exchange)
    if all(out[k] is None for k in ("a", "ns", "mx")):
        raise _cache.FetchError("; ".join(errors) or "no answer")
    out["errors"] = errors
    return out


def lookup(domain: str, cache: _cache.Cache | None = None) -> _cache.Lookup:
    cache = cache or _cache.shared_cache()
    return cache.get("dns", domain, lambda: (resolve(domain), f"dns:{domain}"), f"dns:{domain}")


def status(data: dict[str, Any]) -> tuple[str, str] | None:
    """(live | dead | parked | sinkholed, the rule that decided) or None when unknown."""
    if data.get("nxdomain"):
        return "dead", "NXDOMAIN"
    ns = data.get("ns") or []
    a = data.get("a")
    for host in ns:
        if "sinkhole" in host:
            return "sinkholed", f"name server {host}"
    for host in ns:
        if any(host == s or host.endswith("." + s) for s in PARKING_NS_SUFFIXES):
            return "parked", f"parking name server {host}"
    if a is None:
        return None
    if a and all(ip in PLACEHOLDER_IPS for ip in a):
        return "parked", f"placeholder address {', '.join(a)}"
    if not a:
        return "dead", "no A record"
    return "live", "resolves to an address"


@enrichers.register
class DnsEnricher:
    name = "dns"
    version = "1"

    def run(self, view: View, observations: list[Observation], ctx: Context) -> list[Observation]:
        domain = view_domain(view, observations)
        if not domain:
            return []
        out = Out(view, self.name, self.version)
        found = lookup(domain)
        info = {"domain": domain, **found.provenance()}
        locator = f"dns:{domain}"
        data = found.data if found.ok else {}

        # Records, one row per value.
        for ip in data.get("a") or []:
            out.add("C13", "dns.a", ip, locator + ":A", info)
        for mx in data.get("mx") or []:
            out.add("C13", "dns.mx", mx, locator + ":MX", info)

        # Name-server pair; the RDAP delegation stands in when the zone gives none.
        if data.get("ns"):
            out.add(
                "C3", "dns.ns_pair", ns_pair(data["ns"]), locator + ":NS", info, Purpose.LINKAGE
            )
        else:
            self._ns_from_rdap(out, domain, info)

        decided = status(data) if found.ok else None
        if decided is not None:
            value, rule = decided
            out.add("C13", "dns.status", value, locator, {**info, "rule": rule})
        else:
            reason = info if not found.ok else {**info, "error": "no A answer"}
            out.missing("C13", "dns.status", locator, reason)
        return out.items

    @staticmethod
    def _ns_from_rdap(out: Out, domain: str, info: dict[str, Any]) -> None:
        reg = rdap.lookup(domain)
        nameservers = (reg.data or {}).get("nameservers") if reg.ok else None
        if nameservers:
            out.add(
                "C3",
                "dns.ns_pair",
                ns_pair(nameservers),
                f"rdap:{reg.endpoint}:nameservers",
                {**info, "fallback": "rdap", "rdap": reg.provenance()},
                Purpose.LINKAGE,
                confidence=0.8,
            )
        else:
            missing = {**info, "rdap": reg.provenance()}
            out.missing("C3", "dns.ns_pair", f"dns:{domain}:NS", missing, Purpose.LINKAGE)
