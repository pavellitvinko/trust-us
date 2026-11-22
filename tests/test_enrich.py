"""DNS and RDAP enrichers (HAC-16). No network: lookups are served by fakes.

The RDAP fixtures are real registry responses captured on 2026-11-21, trimmed to the
fields the parser reads.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ftf import pipeline
from ftf.contracts import Incident, Observation, State, View, ViewKind
from ftf.enrich import _cache, dns_lookup, rdap
from ftf.extract.domain_shape import DomainShape, shape, tld_risk
from ftf.registry import Context

FIXTURES = Path(__file__).parent / "fixtures" / "rdap"
SAMPLE = Path(__file__).resolve().parents[1] / "testing_sample"

BOOTSTRAP = {
    "services": [
        [["live", "pro", "life"], ["https://rdap.identitydigital.services/rdap/"]],
        [["top"], ["https://rdap.zdnsgtld.com/top/"]],
        [["xyz"], ["https://rdap.centralnic.com/xyz/"]],
        [["icu"], ["https://rdap.centralnic.com/icu/"]],
        [["org"], ["https://rdap.publicinterestregistry.org/rdap/"]],
        [["com"], ["https://rdap.verisign.com/com/v1/"]],
    ]
}


def rdap_doc(domain: str) -> dict:
    return json.loads((FIXTURES / f"{domain}.json").read_text(encoding="utf-8"))


class FakeCache(_cache.Cache):
    """Serves HTTP from a dict: url suffix -> JSON, or an exception to raise."""

    def __init__(self, root: Path, routes: dict, mode: str = "on"):
        super().__init__(root=root, mode=mode)
        self.routes = routes
        self.calls: list[str] = []

    def http_json(self, url: str, accept: str = "application/json", timeout: float = 0):
        self.calls.append(url)
        if url == rdap.BOOTSTRAP_URL:
            return BOOTSTRAP
        for suffix, answer in self.routes.items():
            if url.endswith(suffix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise _cache.FetchError("HTTP 404")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Install a fake cache and DNS; returns a helper to configure them."""
    dns_answers: dict[str, object] = {}

    def resolve(domain, host=None):
        answer = dns_answers.get(host) or dns_answers.get(domain)
        if answer is None or isinstance(answer, Exception):
            raise answer or _cache.FetchError("Timeout", cacheable=False)
        return {"host": host or domain, "aaaa": [], **answer}

    monkeypatch.setattr(dns_lookup, "resolve", resolve)

    def install(routes: dict, mode: str = "on") -> FakeCache:
        cache = FakeCache(tmp_path / "cache", routes, mode)
        _cache.reset_shared_cache(cache)
        return cache

    install.dns = dns_answers
    return install


def page_view(store, tmp_path, domain: str, har_time: str | None = None) -> tuple[View, Context]:
    """One page view on `domain`, with a HAR captured at `har_time` when given."""
    root = tmp_path / "incident"
    root.mkdir(exist_ok=True)
    har = None
    if har_time:
        har = "page.har"
        entry = {
            "startedDateTime": har_time,
            "request": {"url": f"https://{domain}/"},
            "response": {"status": 200, "content": {"mimeType": "text/html"}},
        }
        (root / har).write_text(json.dumps({"log": {"entries": [entry]}}), encoding="utf-8")
    view = View(
        view_id=f"t:{domain}",
        incident_id="t",
        kind=ViewKind.PAGE,
        label=domain,
        har=har,
        meta={"saved_from": f"https://{domain}/article"},
    )
    store.put_incident(Incident(incident_id="t", root=str(root)), [], [view])
    return view, Context(store)


def by_ns(observations: list[Observation]) -> dict[str, list[Observation]]:
    out: dict[str, list[Observation]] = {}
    for o in observations:
        out.setdefault(o.namespace, []).append(o)
    return out


# --- RDAP -------------------------------------------------------------------------------


def test_parse_rdap_real_responses():
    top = rdap.parse_rdap(rdap_doc("ineptsolidoval.top"))
    assert top["registered_at"] == "2026-08-02T16:00:58.0Z"
    assert top["nameservers"] == ["deborah.ns.cloudflare.com", "miguel.ns.cloudflare.com"]
    assert top["registrar_iana_id"] == "1068"
    live = rdap.parse_rdap(rdap_doc("civicdaybook.live"))
    assert live["registrar"] == "Dynadot Inc"
    assert live["nameservers"] == ["ns1.dyna-ns.net", "ns2.dyna-ns.net"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("NameCheap, Inc.", "namecheap"),
        ("Namecheap Inc.", "namecheap"),
        ("NameSilo, LLC", "namesilo"),
        ("NICENIC INTERNATIONAL GROUP CO., LIMITED", "nicenic"),
        ("PDR Ltd. d/b/a PublicDomainRegistry.com", "publicdomainregistry"),
        ("Dynadot Inc", "dynadot"),
    ],
)
def test_registrar_normalised(raw, expected):
    assert rdap.normalise_registrar(raw) == expected


def test_rdap_enricher_age_from_har(store, tmp_path, fake):
    fake({"/domain/civicdaybook.live": rdap_doc("civicdaybook.live")})
    view, ctx = page_view(store, tmp_path, "civicdaybook.live", "2026-10-09T06:25:24.977Z")
    obs = by_ns(rdap.RdapEnricher().run(view, [], ctx))

    assert obs["rdap.registered_at"][0].value == "2026-10-08"
    age = obs["rdap.domain_age_days"][0]
    assert age.value == 0 and age.confidence == 1.0
    raw = json.loads(age.raw)
    assert raw["basis"] == "har:page.har"
    assert raw["captured_at"].startswith("2026-10-09T06:25:24")
    assert raw["endpoint"].endswith("/rdap/domain/civicdaybook.live")
    assert "lookup_at" in raw and raw["cached"] is False
    registrar = obs["rdap.registrar"][0]
    assert registrar.value == "dynadot"
    assert json.loads(registrar.raw)["registrar"] == "Dynadot Inc"


def test_rdap_age_falls_back_to_lookup_time(store, tmp_path, fake):
    fake({"/domain/ineptsolidoval.top": rdap_doc("ineptsolidoval.top")})
    view, ctx = page_view(store, tmp_path, "ineptsolidoval.top")
    age = by_ns(rdap.RdapEnricher().run(view, [], ctx))["rdap.domain_age_days"][0]
    assert json.loads(age.raw)["basis"] == "lookup"
    assert age.confidence == 0.5
    assert age.value > 100  # registered 2026-08-02, looked up later


@pytest.mark.parametrize("failure", ["404", "timeout", "unknown_tld"])
def test_rdap_failure_is_not_observable(store, tmp_path, fake, failure):
    domain = "example.unknowntld" if failure == "unknown_tld" else "gone.live"
    routes = (
        {"/domain/gone.live": _cache.FetchError("network: timed out")}
        if failure == "timeout"
        else {}
    )
    fake(routes)
    view, ctx = page_view(store, tmp_path, domain)
    obs = rdap.RdapEnricher().run(view, [], ctx)
    assert {o.namespace for o in obs} == set(rdap.NAMESPACES)
    assert all(o.state is State.NOT_OBSERVABLE and o.value is None for o in obs)
    assert all(json.loads(o.raw)["error"] for o in obs)


# --- DNS --------------------------------------------------------------------------------


def test_ns_pair_is_sorted_and_trimmed():
    assert dns_lookup.ns_pair(["Miguel.NS.cloudflare.com.", "deborah.ns.cloudflare.com."]) == (
        "deborah.ns.cloudflare.com|miguel.ns.cloudflare.com"
    )


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"a": ["1.1.1.1"], "ns": ["ns1.dyna-ns.net", "ns2.dyna-ns.net"]}, "parked"),
        ({"a": ["0.0.0.0"], "ns": ["a.ns.cloudflare.com"]}, "parked"),
        ({"nxdomain": True, "a": [], "ns": []}, "dead"),
        ({"a": [], "aaaa": [], "ns": ["a.ns.cloudflare.com"]}, "dead"),
        ({"a": [], "aaaa": ["2606:4700::1"], "ns": ["a.ns.cloudflare.com"]}, "live"),
        ({"a": [], "aaaa": None, "ns": ["a.ns.cloudflare.com"]}, None),
        ({"host_nxdomain": True, "host": "x.example.org", "a": [], "aaaa": [], "ns": []}, "dead"),
        ({"a": ["1.2.3.4"], "ns": ["ns1.sinkhole.example.org"]}, "sinkholed"),
        ({"a": ["104.21.0.1"], "ns": ["a.ns.cloudflare.com"]}, "live"),
    ],
)
def test_dns_status(data, expected):
    decided = dns_lookup.status(data)
    assert (decided[0] if decided else None) == expected


def test_dns_enricher_parked_domain(store, tmp_path, fake):
    fake({})
    fake.dns["civicdaybook.live"] = {
        "nxdomain": False,
        "a": ["1.1.1.1"],
        "ns": ["ns1.dyna-ns.net", "ns2.dyna-ns.net"],
        "mx": [],
    }
    view, ctx = page_view(store, tmp_path, "civicdaybook.live")
    obs = by_ns(dns_lookup.DnsEnricher().run(view, [], ctx))
    assert obs["dns.ns_pair"][0].value == "ns1.dyna-ns.net|ns2.dyna-ns.net"
    # parking name servers are shared by unrelated domains: a risk fact, never a link
    assert obs["dns.ns_pair"][0].purpose.value == "risk"
    assert obs["dns.status"][0].value == "parked"
    assert "dyna-ns.net" in json.loads(obs["dns.status"][0].raw)["rule"]
    assert [o.value for o in obs["dns.a"]] == ["1.1.1.1"]


def test_dns_dead_domain_takes_ns_from_rdap(store, tmp_path, fake):
    fake({"/domain/ineptsolidoval.top": rdap_doc("ineptsolidoval.top")})
    fake.dns["ineptsolidoval.top"] = {"nxdomain": True, "a": [], "ns": [], "mx": []}
    view, ctx = page_view(store, tmp_path, "ineptsolidoval.top")
    obs = by_ns(dns_lookup.DnsEnricher().run(view, [], ctx))
    pair = obs["dns.ns_pair"][0]
    assert pair.value == "deborah.ns.cloudflare.com|miguel.ns.cloudflare.com"
    assert pair.source.locator.endswith(":nameservers") and pair.confidence == 0.8
    assert pair.purpose.value == "risk"  # a dead domain's delegation does not link
    assert obs["dns.status"][0].value == "dead"


def test_dns_live_domain_ns_pair_links(store, tmp_path, fake):
    fake({})
    fake.dns["glowpixel.org"] = {"a": ["104.21.0.1"], "ns": ["ed.ns.cloudflare.com"], "mx": []}
    view, ctx = page_view(store, tmp_path, "glowpixel.org")
    pair = by_ns(dns_lookup.DnsEnricher().run(view, [], ctx))["dns.ns_pair"][0]
    assert pair.purpose.value == "linkage"


def test_dns_resolves_the_page_host_not_only_the_apex(store, tmp_path, fake):
    """A live page on a sub-domain whose apex has no address is not called dead."""
    fake({})
    fake.dns["example.xyz"] = {"a": [], "ns": ["a.ns.cloudflare.com"], "mx": []}
    fake.dns["promo.example.xyz"] = {"a": ["104.21.0.1"], "ns": ["a.ns.cloudflare.com"], "mx": []}
    view, _ = page_view(store, tmp_path, "example.xyz")
    host = Observation(
        view_id=view.view_id, signal_id="A14", namespace="url.host",
        value="promo.example.xyz", extractor="page.identity", version="1",
    )  # fmt: skip
    obs = by_ns(dns_lookup.DnsEnricher().run(view, [host], Context(store)))
    assert obs["dns.status"][0].value == "live"
    assert json.loads(obs["dns.status"][0].raw)["host"] == "promo.example.xyz"


def test_dns_resolver_failure_is_not_observable(store, tmp_path, fake):
    fake({})  # no RDAP either
    view, ctx = page_view(store, tmp_path, "gone.live")
    obs = by_ns(dns_lookup.DnsEnricher().run(view, [], ctx))
    assert obs["dns.ns_pair"][0].state is State.NOT_OBSERVABLE
    assert obs["dns.status"][0].state is State.NOT_OBSERVABLE
    assert "dns.a" not in obs


# --- cache ------------------------------------------------------------------------------


def test_cache_reuses_answers_across_instances(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return {"x": 1}, "endpoint"

    first = _cache.Cache(root=tmp_path, mode="on").get("src", "Key.Example", fetch)
    second = _cache.Cache(root=tmp_path, mode="on").get("src", "Key.Example", fetch)
    assert first.ok and not first.from_cache
    assert second.from_cache and second.data == {"x": 1} and len(calls) == 1
    assert second.provenance()["cache_age_s"] >= 0


def test_cache_retries_expired_error_and_keeps_stale_success(tmp_path):
    cache = _cache.Cache(root=tmp_path, mode="on")
    path = cache._path("src", "k")
    path.parent.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    path.write_text(json.dumps({"fetched_at": old, "ok": True, "data": 1, "endpoint": "e"}))

    def failing():
        raise _cache.FetchError("down")

    found = cache.get("src", "k", failing)
    assert found.ok and found.stale and found.data == 1  # old fact beats no fact


def test_cache_files_never_collide(tmp_path):
    cache = _cache.Cache(root=tmp_path, mode="on")
    assert cache._path("rdap", "łódź.pl") != cache._path("rdap", "łódż.pl")
    assert cache._path("rdap", "a" * 200 + "x") != cache._path("rdap", "a" * 200 + "y")


def test_idn_domains_are_looked_up_in_punycode(store, tmp_path, fake):
    cache = fake({"/domain/xn--zakady-5db.pl": rdap_doc("civicdaybook.live")})
    BOOTSTRAP["services"].append([["pl"], ["https://rdap.dns.pl/"]])
    try:
        view, ctx = page_view(store, tmp_path, "zakłady.pl")
        obs = rdap.RdapEnricher().run(view, [], ctx)
    finally:
        BOOTSTRAP["services"].pop()
    assert cache.calls[-1] == "https://rdap.dns.pl/domain/xn--zakady-5db.pl"
    assert all(o.state is State.PRESENT for o in obs)


def test_transient_failures_are_not_cached(tmp_path):
    def down():
        raise _cache.FetchError("network: down", cacheable=False)

    assert not _cache.Cache(root=tmp_path, mode="on").get("s", "k", down).ok
    assert not list(tmp_path.rglob("*.json"))

    def gone():
        raise _cache.FetchError("HTTP 404")

    _cache.Cache(root=tmp_path, mode="on").get("s", "k", gone)
    assert len(list(tmp_path.rglob("*.json"))) == 1


@pytest.mark.parametrize(
    ("value", "mode"),
    [("on", "on"), ("", "on"), ("OFF", "off"), ("false", "off"), ("0", "off"),
     ("no", "off"), ("cache", "cache"), ("typo", "off")],
)  # fmt: skip
def test_enrich_mode_values(value, mode):
    assert _cache.normalise_mode(value) == mode


def test_cache_modes(tmp_path):
    def fetch():
        raise AssertionError("must not fetch")

    assert not _cache.Cache(root=tmp_path, mode="off").get("s", "k", fetch).ok
    assert "not in cache" in _cache.Cache(root=tmp_path, mode="cache").get("s", "k", fetch).error


# --- C4 / C5 ----------------------------------------------------------------------------


def test_tld_risk_and_shape():
    assert tld_risk("live") == tld_risk("icu") == "high"
    assert tld_risk("pl") == "low" and tld_risk("org") == "other"
    assert shape("shop-77vault")[0] == "hyphen_digits"
    assert shape("meroporepufulo")[0] == "generated_pronounceable"
    assert shape("shryxla-mynx")[0] == "generated"
    assert shape("civicdaybook")[0] == "plain"


def test_domain_shape_extractor(store, tmp_path):
    view, ctx = page_view(store, tmp_path, "shop-77vault.xyz")
    obs = by_ns(DomainShape().run(view, ctx))
    assert obs["domain.tld_risk"][0].value == "high"
    assert obs["domain.name_shape"][0].value == "hyphen_digits"


# --- done criterion on the sample -------------------------------------------------------


@pytest.mark.sample
@pytest.mark.skipif(not SAMPLE.is_dir(), reason="testing_sample/ not present")
def test_every_sample_page_has_age_and_name_servers(store, fake):
    """HAC-16 done-when: each page view has domain-age and name-server rows, or not_observable.

    One domain's RDAP fails and another's DNS fails, so both paths are exercised.
    """
    from ftf.ingest import ingest_path

    routes = {
        f"/domain/{d}": rdap_doc(d)
        for d in ("civicdaybook.live", "ineptsolidoval.top", "shryxla-mynx.icu")
    }
    fake(routes)  # every other domain: RDAP 404
    for domain in ("civicdaybook.live", "shryxla-mynx.icu", "glowpixel.org"):
        fake.dns[domain] = {"nxdomain": False, "a": ["104.21.0.1"], "ns": [], "mx": []}
    # ineptsolidoval.top and the rest: resolver timeout

    ingest_path(SAMPLE, store)
    pipeline.run_extract(store)
    counts = pipeline.run_enrich(store)

    pages = [v for v in store.views() if v.kind is ViewKind.PAGE]
    assert pages
    for view in pages:
        obs = by_ns(store.observations(view.view_id))
        for namespace in ("rdap.domain_age_days", "dns.ns_pair"):
            assert namespace in obs, f"{view.view_id} has no {namespace}"
    assert counts[("dns.ns_pair", "present")] and counts[("dns.ns_pair", "not_observable")]
    assert counts[("rdap.domain_age_days", "present")]
    assert counts[("rdap.domain_age_days", "not_observable")]
