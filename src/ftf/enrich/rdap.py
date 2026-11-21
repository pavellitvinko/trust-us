"""RDAP enricher: registration date, registrar, domain age at capture time (signals C1, C2).

The registry's RDAP server is found through the IANA bootstrap file
(https://data.iana.org/rdap/dns.json). Coverage was confirmed on 2026-11-21 for every TLD in
the sample: .live .pro .life (Identity Digital), .xyz .icu (CentralNic), .top (ZDNS),
.org (PIR), .com (Verisign).

A failed lookup gives not_observable for each namespace, never a negative finding.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..contracts import Observation, Purpose, View
from ..registry import Context, enrichers
from . import _cache
from ._capture import capture_time, parse_time
from ._common import Out, view_domain

BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
BOOTSTRAP_TTL_KEY = "dns"
NAMESPACES = ("rdap.registered_at", "rdap.domain_age_days", "rdap.registrar")

# Corporate words dropped so one registrar spelled two ways compares equal
# ("NameCheap, Inc." and "Namecheap Inc." -> "namecheap").
_CORPORATE = {
    "inc", "llc", "ltd", "limited", "co", "corp", "corporation", "company", "gmbh", "sa",
    "sarl", "srl", "bv", "ag", "plc", "pte", "pty", "group", "international", "holdings",
    "the", "kk", "spa", "oy", "ab", "as", "sp", "zoo",
}  # fmt: skip
_TLD_TAIL = re.compile(r"\.(com|net|org|io|co|biz|info)$")


def normalise_registrar(name: str) -> str:
    """Lower-cased registrar name without corporate suffixes and punctuation."""
    text = name.strip()
    dba = re.split(r"\bd/?b/?a\b", text, flags=re.IGNORECASE)
    if len(dba) > 1 and dba[-1].strip():
        text = dba[-1]
    text = _TLD_TAIL.sub("", text.strip().lower())
    words = [w for w in re.split(r"[^a-z0-9]+", text) if w and w not in _CORPORATE]
    return " ".join(words)


def _vcard_fn(entity: dict[str, Any]) -> str | None:
    vcard = entity.get("vcardArray")
    if not (isinstance(vcard, list) and len(vcard) > 1 and isinstance(vcard[1], list)):
        return None
    for item in vcard[1]:
        if isinstance(item, list) and len(item) > 3 and item[0] == "fn" and item[3]:
            return str(item[3])
    return None


def parse_rdap(doc: dict[str, Any]) -> dict[str, Any]:
    """Keep the few facts we use from an RDAP domain response."""
    events = {
        e.get("eventAction"): e.get("eventDate")
        for e in doc.get("events", []) or []
        if isinstance(e, dict)
    }
    registrar = None
    iana_id = None
    for entity in doc.get("entities", []) or []:
        if not isinstance(entity, dict) or "registrar" not in (entity.get("roles") or []):
            continue
        registrar = _vcard_fn(entity) or entity.get("handle")
        for pid in entity.get("publicIds", []) or []:
            if isinstance(pid, dict) and "iana" in str(pid.get("type", "")).lower():
                iana_id = str(pid.get("identifier"))
        break
    nameservers = sorted(
        {
            str(ns.get("ldhName", "")).lower().rstrip(".")
            for ns in doc.get("nameservers", []) or []
            if isinstance(ns, dict) and ns.get("ldhName")
        }
    )
    return {
        "domain": str(doc.get("ldhName", "")).lower().rstrip(".") or None,
        "registered_at": events.get("registration"),
        "expires_at": events.get("expiration"),
        "last_changed_at": events.get("last changed"),
        "registrar": registrar,
        "registrar_iana_id": iana_id,
        "nameservers": nameservers,
        "status": list(doc.get("status", []) or []),
    }


def bootstrap(cache: _cache.Cache) -> _cache.Lookup:
    def fetch():
        doc = cache.http_json(BOOTSTRAP_URL)
        services = {}
        for tlds, urls in doc.get("services", []):
            for tld in tlds:
                if urls:
                    services[tld.lower()] = urls[0]
        if not services:
            raise _cache.FetchError("empty bootstrap")
        return services, BOOTSTRAP_URL

    return cache.get("rdap_bootstrap", BOOTSTRAP_TTL_KEY, fetch, BOOTSTRAP_URL)


def lookup(domain: str, cache: _cache.Cache | None = None) -> _cache.Lookup:
    """RDAP facts for a registrable domain (cached). Also used by the DNS enricher."""
    cache = cache or _cache.shared_cache()

    def fetch():
        boot = bootstrap(cache)
        if not boot.ok:
            raise _cache.FetchError(f"bootstrap unavailable: {boot.error}")
        tld = domain.rsplit(".", 1)[-1]
        base = boot.data.get(tld)
        if not base:
            raise _cache.FetchError(f"no RDAP server for .{tld} in the IANA bootstrap")
        url = f"{base.rstrip('/')}/domain/{domain}"
        return parse_rdap(cache.http_json(url, accept="application/rdap+json")), url

    return cache.get("rdap", domain, fetch)


@enrichers.register
class RdapEnricher:
    name = "rdap"
    version = "1"

    def run(self, view: View, observations: list[Observation], ctx: Context) -> list[Observation]:
        domain = view_domain(view, observations)
        if not domain:
            return []
        out = Out(view, self.name, self.version)
        found = lookup(domain)
        info = {"domain": domain, **found.provenance()}
        locator = f"rdap:{found.endpoint or domain}"
        if not found.ok:
            for ns in NAMESPACES:
                purpose = Purpose.BOTH if ns == "rdap.registrar" else Purpose.RISK
                signal = "C2" if ns == "rdap.registrar" else "C1"
                out.missing(signal, ns, locator, info, purpose)
            return out.items

        data = found.data
        registered = parse_time(data.get("registered_at"))
        if registered is not None:
            out.add("C1", "rdap.registered_at", registered.date().isoformat(), locator, info)
            self._age(out, view, domain, registered, found, info, locator, ctx)
        else:
            missing = {**info, "error": "no registration event in the RDAP response"}
            out.missing("C1", "rdap.registered_at", locator, missing)
            out.missing("C1", "rdap.domain_age_days", locator, missing)

        registrar = data.get("registrar")
        if registrar and normalise_registrar(registrar):
            out.add(
                "C2",
                "rdap.registrar",
                normalise_registrar(registrar),
                locator,
                {**info, "registrar": registrar, "iana_id": data.get("registrar_iana_id")},
                purpose=Purpose.BOTH,
            )
        else:
            missing = {**info, "error": "no registrar entity in the RDAP response"}
            out.missing("C2", "rdap.registrar", locator, missing, Purpose.BOTH)
        return out.items

    @staticmethod
    def _age(out, view, domain, registered: datetime, found, info, locator, ctx) -> None:
        captured = capture_time(view, domain, ctx)
        if captured is not None:
            when, basis = captured
            confidence = 1.0
        else:
            # No capture time on record: age at lookup time, marked as such.
            when, basis = found.fetched_at or _cache.now(), "lookup"
            confidence = 0.5
        days = (when - registered).days
        age_info = {
            **info,
            "registered_at": registered.isoformat(),
            "captured_at": when.isoformat(timespec="seconds"),
            "basis": basis,
        }
        if days < 0:
            age_info["note"] = "current registration is newer than the capture (re-registered)"
        out.add("C1", "rdap.domain_age_days", days, locator, age_info, confidence=confidence)
