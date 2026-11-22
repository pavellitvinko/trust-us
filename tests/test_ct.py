"""Certificate transparency enricher (HAC-18). No network: crt.sh and RDAP are served by fakes.

The crt.sh fixtures are real responses captured on 2026-11-22, trimmed to a few rows.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from ftf.compare.shared_value import shared_value_evidence
from ftf.contracts import Observation, Purpose, State
from ftf.enrich import _cache, ct
from ftf.grouping.suspicion import reason_summary_for_view
from ftf.layers.ids_domain import IdsDomain

from .test_enrich import FakeCache, by_ns, fake, page_view, rdap_doc  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures" / "ct"


@pytest.fixture(autouse=True)
def _closed_breaker():
    ct.reset_breaker()
    yield
    ct.reset_breaker()


def crtsh(domain: str) -> list:
    return json.loads((FIXTURES / f"{domain}.json").read_text(encoding="utf-8"))


def registered(domain: str, when: str) -> dict:
    return {"ldhName": domain, "events": [{"eventAction": "registration", "eventDate": when}]}


def crt_route(domain: str) -> str:
    return f"?q={domain}&output=json"


def cert_row(serial: str, names: list[str], not_before: str, issuer: str = "C=US, O=X, CN=Y"):
    return {
        "id": int(serial, 16),
        "issuer_ca_id": 1,
        "issuer_name": issuer,
        "common_name": names[0],
        "name_value": "\n".join(names),
        "not_before": not_before,
        "not_after": "2027-01-01T00:00:00",
        "serial_number": serial,
    }


# --- parsing ------------------------------------------------------------------------------


def test_parse_dedupes_precertificates_and_filters_lookalikes():
    rows = crtsh("glowpixel.org") + [cert_row("ff", ["glowpixel.org.evil.xyz"], "2024-01-01")]
    parsed = ct.parse_crtsh(rows, "glowpixel.org")
    assert parsed["total"] == 4  # 7 rows, three logged twice; the look-alike is dropped
    assert parsed["certs"][0]["not_before"].startswith("2025-05-16")
    assert all(c["other_domains"] == [] for c in parsed["certs"])


@pytest.mark.parametrize(
    ("dn", "org"),
    [
        ("C=US, O=Let's Encrypt, CN=YE2", "let's encrypt"),
        ('C=US, O="CLOUDFLARE, INC.", CN=Cloudflare TLS Issuing ECC CA', "cloudflare, inc."),
        ("CN=Only Common Name", "only common name"),
    ],
)
def test_issuer_org(dn, org):
    assert ct.issuer_org(dn) == org


def test_wildcard_covers_one_label_only():
    assert ct.covers("*.glowpixel.org", "www.glowpixel.org")
    assert not ct.covers("*.glowpixel.org", "glowpixel.org")
    assert not ct.covers("*.glowpixel.org", "a.b.glowpixel.org")


# --- enricher -----------------------------------------------------------------------------


def test_glowpixel_history_and_prior_life(store, tmp_path, fake):  # noqa: F811
    fake(
        {
            crt_route("glowpixel.org"): crtsh("glowpixel.org"),
            "/domain/glowpixel.org": registered("glowpixel.org", "2026-09-11T11:22:14.49Z"),
        }
    )
    view, ctx = page_view(store, tmp_path, "glowpixel.org")
    obs = by_ns(ct.CtEnricher().run(view, [], ctx))

    assert obs["ct.cert_count"][0].value == 4
    assert obs["ct.first_seen"][0].value == "2025-05-16"
    assert obs["ct.issuer"][0].value == "let's encrypt"  # newest certificate for the host
    assert obs["ct.wildcard"][0].value is True
    assert [o.value for o in obs["ct.sibling_host"]] == ["a.glowpixel.org"]

    prior = obs["ct.prior_life_days"][0]
    assert prior.signal_id == "C9"
    assert prior.value == 483
    raw = json.loads(prior.raw)
    assert raw["registered_at"].startswith("2026-09-11")
    assert raw["endpoint"].startswith("https://crt.sh/?q=glowpixel.org")
    assert prior.source.locator.startswith("crtsh:https://crt.sh/?id=")
    assert "ct.shared_cert" not in obs and "ct.san_domain" not in obs

    summary = reason_summary_for_view(view.view_id, obs["ct.prior_life_days"])
    reasons = summary.reasons_by_kind["domain"]
    assert reasons[0].signal_id == "C9"
    assert "2025-05-16" in reasons[0].summary and "2026-09-11" in reasons[0].summary


def test_certificate_minutes_before_registration_is_no_prior_life(store, tmp_path, fake):  # noqa: F811
    """Fresh domains get their first certificate before the registry stamps registration."""
    fake(
        {
            crt_route("shryxla-mynx.icu"): crtsh("shryxla-mynx.icu"),
            "/domain/shryxla-mynx.icu": rdap_doc("shryxla-mynx.icu"),
        }
    )
    view, ctx = page_view(store, tmp_path, "shryxla-mynx.icu")
    prior = by_ns(ct.CtEnricher().run(view, [], ctx))["ct.prior_life_days"][0]
    assert prior.state is State.PRESENT
    assert prior.value == 0
    assert json.loads(prior.raw)["gap_days"] == 0
    assert not reason_summary_for_view(view.view_id, [prior]).reasons_by_kind


def test_empty_answer_is_not_a_fact(store, tmp_path, fake):  # noqa: F811
    """crt.sh answered `[]` for healthlinkq.pro, which has certificates, on 2026-11-22."""
    fake({crt_route("novumfy.com"): []})
    view, ctx = page_view(store, tmp_path, "novumfy.com")
    obs = ct.CtEnricher().run(view, [], ctx)
    assert {o.namespace for o in obs} == set(ct.NAMESPACES)
    assert all(o.state is State.NOT_OBSERVABLE for o in obs)
    assert json.loads(obs[0].raw)["error"] == ct.EMPTY_ANSWER


def test_empty_answer_falls_back_to_an_earlier_list(tmp_path, monkeypatch):
    route = crt_route("glowpixel.org")
    assert ct.lookup("glowpixel.org", FakeCache(tmp_path / "c", {route: crtsh("glowpixel.org")}))
    later = _cache.now() + _cache.OK_TTL + timedelta(days=1)
    monkeypatch.setattr(_cache, "now", lambda: later)
    found = ct.lookup("glowpixel.org", FakeCache(tmp_path / "c", {route: []}))
    assert found.ok and found.stale and found.data["total"] == 4


def test_prior_life_not_observable_without_rdap(store, tmp_path, fake):  # noqa: F811
    fake({crt_route("glowpixel.org"): crtsh("glowpixel.org")})  # RDAP answers 404
    view, ctx = page_view(store, tmp_path, "glowpixel.org")
    obs = by_ns(ct.CtEnricher().run(view, [], ctx))
    assert obs["ct.first_seen"][0].state is State.PRESENT
    assert obs["ct.prior_life_days"][0].state is State.NOT_OBSERVABLE


class FlakyCache(FakeCache):
    """crt.sh failing with `error` for the first `fails` requests (as the real http_json does:
    502 not cacheable, 404 cacheable, timeouts as `network: ...`)."""

    def __init__(self, root, routes, fails: int, error: str = "HTTP 502", mode: str = "on"):
        super().__init__(root, routes, mode)
        self.fails, self.error = fails, error

    def http_json(self, url, accept="application/json", timeout=0):
        if "crt.sh" in url and self.fails:
            self.fails -= 1
            self.calls.append(url)
            raise _cache.FetchError(self.error, cacheable=self.error == "HTTP 404")
        return super().http_json(url, accept, timeout)


def crt_calls(cache) -> int:
    return sum("crt.sh" in u for u in cache.calls)


def test_one_http_error_is_retried(store, tmp_path):
    cache = FlakyCache(tmp_path / "cache", {crt_route("glowpixel.org"): crtsh("glowpixel.org")}, 1)
    _cache.reset_shared_cache(cache)
    view, ctx = page_view(store, tmp_path, "glowpixel.org")
    obs = by_ns(ct.CtEnricher().run(view, [], ctx))
    assert obs["ct.cert_count"][0].value == 4
    assert crt_calls(cache) == 2


def test_timeout_is_not_retried(store, tmp_path):
    routes = {crt_route("glowpixel.org"): crtsh("glowpixel.org")}
    cache = FlakyCache(tmp_path / "cache", routes, 1, error="network: timed out")
    _cache.reset_shared_cache(cache)
    view, ctx = page_view(store, tmp_path, "glowpixel.org")
    obs = ct.CtEnricher().run(view, [], ctx)
    assert all(o.state is State.NOT_OBSERVABLE for o in obs)
    assert crt_calls(cache) == 1


@pytest.mark.parametrize("error", ["HTTP 502", "HTTP 404"])
def test_crtsh_down_is_not_observable_and_not_cached(store, tmp_path, error):
    """A crt.sh 404 is its own hiccup (no results come as `200 []`), so it is not cached."""
    cache = FlakyCache(tmp_path / "cache", {}, fails=2, error=error)
    _cache.reset_shared_cache(cache)
    view, ctx = page_view(store, tmp_path, "glowpixel.org")
    obs = ct.CtEnricher().run(view, [], ctx)
    assert {o.namespace for o in obs} == set(ct.NAMESPACES)
    assert all(o.state is State.NOT_OBSERVABLE for o in obs)
    assert json.loads(obs[0].raw)["error"] == error
    assert not (tmp_path / "cache" / ct.CACHE_SOURCE).exists()


def test_answer_that_is_not_a_list_is_not_cached(store, tmp_path, fake):  # noqa: F811
    fake({crt_route("glowpixel.org"): {"error": "busy"}})
    view, ctx = page_view(store, tmp_path, "glowpixel.org")
    obs = ct.CtEnricher().run(view, [], ctx)
    assert all(o.state is State.NOT_OBSERVABLE for o in obs)
    assert not (tmp_path / "cache" / ct.CACHE_SOURCE).exists()


def test_breaker_stops_asking_after_repeated_failures(store, tmp_path):
    cache = FlakyCache(tmp_path / "cache", {}, fails=100, error="network: timed out")
    _cache.reset_shared_cache(cache)
    for i in range(ct.BREAKER_FAILURES + 2):
        view, ctx = page_view(store, tmp_path, f"down{i}.xyz")
        obs = ct.CtEnricher().run(view, [], ctx)
        assert all(o.state is State.NOT_OBSERVABLE for o in obs)
    assert crt_calls(cache) == ct.BREAKER_FAILURES
    assert "paused" in json.loads(obs[0].raw)["error"]


def test_transient_failure_is_retried_after_memo_ttl(tmp_path, monkeypatch):
    cache = FlakyCache(tmp_path / "cache", {crt_route("glowpixel.org"): crtsh("glowpixel.org")}, 2)
    assert not ct.lookup("glowpixel.org", cache).ok
    assert not ct.lookup("glowpixel.org", cache).ok  # remembered: no new request
    assert crt_calls(cache) == 2
    later = _cache.now() + _cache.TRANSIENT_MEMO_TTL + timedelta(seconds=1)
    monkeypatch.setattr(_cache, "now", lambda: later)
    assert ct.lookup("glowpixel.org", cache).ok


def test_stale_entry_is_used_when_crtsh_fails(tmp_path, monkeypatch):
    routes = {crt_route("glowpixel.org"): crtsh("glowpixel.org")}
    assert ct.lookup("glowpixel.org", FakeCache(tmp_path / "cache", routes)).ok
    later = _cache.now() + _cache.OK_TTL + timedelta(days=1)
    monkeypatch.setattr(_cache, "now", lambda: later)
    found = ct.lookup("glowpixel.org", FlakyCache(tmp_path / "cache", {}, fails=2))
    assert found.ok and found.stale and found.data["total"] == 4


def test_cache_mode_reads_only_the_cache(tmp_path):
    routes = {crt_route("glowpixel.org"): crtsh("glowpixel.org")}
    empty = FakeCache(tmp_path / "cache", routes, mode="cache")
    assert not ct.lookup("glowpixel.org", empty).ok and not empty.calls
    assert ct.lookup("glowpixel.org", FakeCache(tmp_path / "cache", routes)).ok
    warm = FakeCache(tmp_path / "cache", {}, mode="cache")
    assert ct.lookup("glowpixel.org", warm).data["total"] == 4 and not warm.calls


def test_enrichment_off_is_not_observable(store, tmp_path, fake):  # noqa: F811
    fake({crt_route("glowpixel.org"): crtsh("glowpixel.org")}, mode="off")
    view, ctx = page_view(store, tmp_path, "glowpixel.org")
    obs = ct.CtEnricher().run(view, [], ctx)
    assert obs and all(o.state is State.NOT_OBSERVABLE for o in obs)


def test_host_without_its_own_certificate(store, tmp_path, fake):  # noqa: F811
    fake({crt_route("plain.xyz"): [cert_row("01", ["plain.xyz"], "2026-10-01T00:00:00")]})
    view, ctx = page_view(store, tmp_path, "plain.xyz")
    host = Observation(
        view_id=view.view_id,
        signal_id="A14",
        namespace="url.host",
        value="shop.plain.xyz",
        extractor="test",
        version="1",
    )
    obs = by_ns(ct.CtEnricher().run(view, [host], ctx))
    assert obs["ct.issuer"][0].confidence == 0.5
    assert obs["ct.wildcard"][0].state is State.NOT_OBSERVABLE


def test_truncation_keeps_first_and_newest_and_is_flagged(store, tmp_path, fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ct, "MAX_CERTS", 3)
    rows = [cert_row(f"{i:02x}", ["big.xyz"], f"2026-0{i}-01T00:00:00") for i in range(1, 7)]
    parsed = ct.parse_crtsh(rows, "big.xyz")
    assert parsed["truncated"] and parsed["total"] == 6
    assert [c["not_before"][:7] for c in parsed["certs"]] == ["2026-01", "2026-05", "2026-06"]
    fake({crt_route("big.xyz"): rows})
    view, ctx = page_view(store, tmp_path, "big.xyz")
    obs = by_ns(ct.CtEnricher().run(view, [], ctx))
    assert json.loads(obs["ct.issuer"][0].raw)["truncated"] is True


def test_suspicion_domain_is_not_checked_by_certificate_rows_alone(store, tmp_path, fake):  # noqa: F811
    fake({crt_route("glowpixel.org"): crtsh("glowpixel.org")})  # RDAP fails
    view, ctx = page_view(store, tmp_path, "glowpixel.org")
    obs = ct.CtEnricher().run(view, [], ctx)
    assert "domain" in reason_summary_for_view(view.view_id, obs).unchecked_kinds


# --- names on a certificate --------------------------------------------------------------


def test_email_and_ip_names_are_dropped():
    row = cert_row(
        "01", ["solo.xyz", "hostmaster@solo.xyz", "admin@gmail.com", "203.0.113.7"], "2026-10-01"
    )
    cert = ct.parse_crtsh([row], "solo.xyz")["certs"][0]
    assert cert["names"] == ["solo.xyz"]
    assert cert["other_domains"] == []


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["a.xyz", "b.top"], True),
        (["a.xyz", "b.top", "c.icu", "d.live", "e.pro"], True),  # five, own included
        (["a.xyz", "b.top", "c.icu", "d.live", "e.pro", "f.life"], False),
        (["a.xyz", "sni42.cloudflaressl.com"], False),
        (["a.xyz", "b.top", "server12.hostco.com"], False),
        (["a.xyz", "b.top", "vps-3.hostco.com"], False),
        (["a.xyz", "www.a.xyz"], False),  # nothing else on it
    ],
)
def test_linkable(names, expected):
    cert = ct.parse_crtsh([cert_row("01", names, "2026-10-01")], "a.xyz")["certs"][0]
    assert ct.linkable(cert) is expected


# --- shared certificates as a link -------------------------------------------------------


def shared_routes(rows_one: list, rows_two: list) -> dict:
    return {
        crt_route("fake-one.xyz"): rows_one,
        crt_route("fake-two.top"): rows_two,
        "/domain/fake-one.xyz": registered("fake-one.xyz", "2026-09-20T10:00:00Z"),
        "/domain/fake-two.top": registered("fake-two.top", "2026-09-25T10:00:00Z"),
    }


def run_both(store, tmp_path) -> list[dict]:
    out = []
    for domain in ("fake-one.xyz", "fake-two.top"):
        view, ctx = page_view(store, tmp_path, domain)
        obs = ct.CtEnricher().run(view, [], ctx)
        out.append({"view": view, "obs": obs, "ns": by_ns(obs)})
    return out


def test_shared_certificate_links_two_domains_once_across_renewals(store, tmp_path, fake):  # noqa: F811
    renewals = [
        cert_row(f"0a{i}", ["fake-one.xyz", "www.fake-two.top"], f"2026-{m}-01T00:00:00")
        for i, m in enumerate(("10", "11", "12"), start=1)
    ]
    cdn = cert_row("0c0d", ["sni1.cloudflaressl.com", "fake-one.xyz", "x.com"], "2026-10-02")
    fake(shared_routes([*renewals, cdn], renewals))
    both = run_both(store, tmp_path)

    for side in both:
        rows = side["ns"]["ct.shared_cert"]
        assert [o.value for o in rows] == ["fake-one.xyz|fake-two.top"]  # one value, 3 renewals
        assert rows[0].purpose is Purpose.LINKAGE
        assert json.loads(rows[0].raw)["certificates"] == 3
        assert side["ns"]["ct.san_domain"][0].purpose is Purpose.RISK
    assert [o.value for o in both[0]["ns"]["ct.san_domain"]] == ["fake-two.top"]  # no x.com
    assert [o.value for o in both[1]["ns"]["ct.san_domain"]] == ["fake-one.xyz"]

    layer = IdsDomain()
    prints = [layer.build(side["view"], side["obs"]) for side in both]
    evidence = shared_value_evidence(*prints, lambda ns, v: 0.6, "shared_value", "2")
    assert [s["ns"] for s in evidence.shared] == ["ct.shared_cert"]
    assert evidence.score == 0.6


def test_earlier_owners_certificate_is_not_a_link(store, tmp_path, fake):  # noqa: F811
    old = cert_row("0b01", ["fake-one.xyz", "fake-two.top"], "2025-03-01T00:00:00")
    fake(shared_routes([old], [old]))
    for side in run_both(store, tmp_path):
        assert "ct.shared_cert" not in side["ns"]
        san = side["ns"]["ct.san_domain"][0]
        assert "not a link" in json.loads(san.raw)["note"]


def test_no_link_without_registration_date(store, tmp_path, fake):  # noqa: F811
    cert = cert_row("0b02", ["fake-one.xyz", "fake-two.top"], "2026-10-01T00:00:00")
    fake({crt_route("fake-one.xyz"): [cert]})  # RDAP answers 404
    view, ctx = page_view(store, tmp_path, "fake-one.xyz")
    found = by_ns(ct.CtEnricher().run(view, [], ctx))
    assert "ct.shared_cert" not in found
    assert found["ct.san_domain"][0].value == "fake-two.top"


def test_certificate_without_serial_does_not_collide(store, tmp_path, fake):  # noqa: F811
    one = {**cert_row("0e01", ["fake-one.xyz", "p.top"], "2026-10-01"), "serial_number": None}
    two = {**cert_row("0e02", ["fake-two.top", "q.icu"], "2026-10-01"), "serial_number": None}
    fake(shared_routes([one], [two]))
    a, b = run_both(store, tmp_path)
    values_a = {o.value for o in a["ns"]["ct.shared_cert"]}
    values_b = {o.value for o in b["ns"]["ct.shared_cert"]}
    assert values_a == {"fake-one.xyz|p.top"} and values_b == {"fake-two.top|q.icu"}


def test_missing_crtsh_id_does_not_break_parsing():
    first = {**cert_row("01", ["solo.xyz"], "2026-10-01"), "id": None}
    second = cert_row("01", ["solo.xyz"], "2026-10-01")
    parsed = ct.parse_crtsh([first, second], "solo.xyz")
    assert parsed["total"] == 1 and parsed["certs"][0]["id"] == 1
