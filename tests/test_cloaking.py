"""Cloaking differential (HAC-15): profiles, the difference score, J1/J5/J2 and the summary.

Unit tests build profile bundles by hand. The browser test runs a local server that cloaks
on the Referer or a click id, and is skipped where Chromium is not installed.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from ftf import pipeline
from ftf.capture.bundle import SCHEMA
from ftf.capture.profiles import AD_CLICK, BARE, CRAWLER, DESKTOP, PROFILES, SYNTHETIC_FBCLID
from ftf.contracts import State
from ftf.grouping.cloaking import (
    THRESHOLD,
    difference_score,
    is_hash_like,
    jaccard,
    text_similarity,
)
from ftf.grouping.suspicion import reason_summary_for_view
from ftf.ingest import ingest_folder

URL = "https://offer.example/lp?utm_source=fb"

OFFER = """<html><head><title>Zarabiaj z AI</title>
<script src="https://connect.facebook.net/en_US/fbevents.js"></script>
<script>fbq('init', '2012165449475163'); fbq('track', 'PageView');</script></head>
<body><header class="hero"><h1>Platforma inwestycyjna AI</h1></header>
<main class="offer"><p>Znany prezenter ujawnia sekret: zarobki od 5000 zł tygodniowo dzięki
nowej platformie inwestycyjnej. Zarejestruj się dziś, liczba miejsc jest ograniczona, a nasz
doradca oddzwoni w ciągu 15 minut i pomoże w pierwszej wpłacie na konto inwestycyjne.</p>
<form class="lead-form"><input name="name"><input name="phone"><button>Wyślij</button></form>
</main></body></html>"""

WHITE = """<html><head><title>Business Solutions</title></head>
<body><div class="a1a02dd7"><div class="f93kd02x"><div class="b7c1e9a4">
<p>We provide innovative consulting services for companies of every size. Our team
delivers quality solutions with passion, focusing on long-term partnerships and growth.</p>
</div></div><footer class="c0d8e2f1">© 2026 Company</footer></div></body></html>"""


CHROME = "131.0.6778.33"
AD_URL = AD_CLICK.url_for(URL)[0]
BARE_URL = BARE.url_for(URL)[0]


def _variant(
    root: Path,
    profile: str,
    dom: str | None,
    status: str = "ok",
    url: str | None = None,
    run: str | None = "run-1",
    tested: str = URL,
):
    """A bundle as `capture_differential` writes it for one profile."""
    prof = PROFILES[profile]
    page_url, added = prof.url_for(tested) if url is None else (url, False)
    b = root / profile
    b.mkdir(parents=True)
    manifest = {
        "schema": SCHEMA,
        "page_url": page_url,
        "final_url": page_url,
        "captured_at": "2026-11-22T10:00:00+00:00",
        "status": status,
        "profile": profile,
        "differential": tested,
        "user_agent": prof.user_agent_for(CHROME),
    }
    if run:
        manifest["differential_run"] = run
    if prof.referer:
        manifest["referer"] = prof.referer
    if added:
        manifest["click_id_added"] = True
    (b / "manifest.json").write_text(json.dumps(manifest))
    if dom is not None:
        (b / "dom.html").write_text(dom)


def _run(store, root: Path):
    _, views = ingest_folder(root, store)
    pipeline.run_extract(store)
    pipeline.run_fingerprint(store)
    pipeline.run_cloaking(store)
    return {v.meta["capture_profile"]: v for v in views}


def _obs(store, view, namespace):
    return [o for o in store.observations(view.view_id) if o.namespace == namespace]


# --- profiles ---------------------------------------------------------------------------


def test_profiles_open_the_right_form_of_the_url():
    url, added = AD_CLICK.url_for(URL)
    assert added and url == f"{URL}&fbclid={SYNTHETIC_FBCLID}"
    # the original query is kept byte for byte, never re-encoded
    raw = "https://a.example/p?q=a%20b;z&redir=https://b/?c=1"
    assert AD_CLICK.url_for(raw)[0] == f"{raw}&fbclid={SYNTHETIC_FBCLID}"
    assert AD_CLICK.url_for("https://a.example/")[0] == (
        f"https://a.example/?fbclid={SYNTHETIC_FBCLID}"
    )
    assert AD_CLICK.url_for("https://a.example/?fbclid=IwAR1") == (
        "https://a.example/?fbclid=IwAR1",
        False,
    )
    assert BARE.url_for(URL + "#x") == ("https://offer.example/lp", False)
    assert CRAWLER.url_for("https://a.example?q=1") == ("https://a.example/", False)
    assert DESKTOP.url_for(URL) == (URL, False)  # the plain capture is unchanged


def test_profiles_set_device_and_identity():
    assert AD_CLICK.is_mobile and AD_CLICK.viewport["width"] < 500
    assert AD_CLICK.referer == "https://l.facebook.com/"
    assert "Mobile" in AD_CLICK.user_agent_for("131.0.6778.33")
    assert "Headless" not in BARE.user_agent_for("131.0.6778.33") and BARE.referer is None
    assert "Googlebot" in CRAWLER.user_agent_for("131")


def test_client_hints_match_the_profile():
    ad = AD_CLICK.ua_override(CHROME)
    meta = ad["userAgentMetadata"]
    assert ad["platform"] == "Linux armv81" and meta["platform"] == "Android" and meta["mobile"]
    assert all("Headless" not in b["brand"] for b in meta["brands"])
    assert {b["brand"] for b in meta["brands"]} >= {"Google Chrome", "Chromium"}
    bare = BARE.ua_override(CHROME)
    assert bare["platform"] == "Win32" and not bare["userAgentMetadata"]["mobile"]
    crawler = CRAWLER.ua_override(CHROME)
    assert "Googlebot" in crawler["userAgent"]
    assert "Google Chrome" not in {b["brand"] for b in crawler["userAgentMetadata"]["brands"]}


# --- scoring helpers --------------------------------------------------------------------


def test_scoring_helpers():
    assert jaccard(set(), set()) is None and jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3)
    assert text_similarity("", "") is None
    assert text_similarity("a b c", "") == 0.0
    assert text_similarity("jeden dwa trzy", "jeden dwa trzy") == 1.0
    assert difference_score(None, [], hosts_differ=False) is None
    assert difference_score(1.0, [1.0], hosts_differ=True) == 0.9  # another domain
    assert difference_score(0.2, [0.4], hosts_differ=False) == 0.7


@pytest.mark.parametrize(
    "name, expected",
    [
        ("a1a02dd7", True),
        ("_3xk9f2a", True),
        ("q8f2k1z9", True),
        ("css-1ujsas3", False),  # Emotion, MUI, Chakra
        ("r-18u37iz", False),  # react-native-web
        ("x1n2onr6", False),  # Meta StyleX
        ("size-1024x768", False),
        ("banner300x250", False),
        ("Button_primary__3xK9f", False),  # CSS modules
        ("mt20px", False),
        ("col-md-6", False),
        ("btn-primary", False),
        ("header", False),
    ],
)
def test_hash_like_class_names(name, expected):
    assert is_hash_like(name) is expected


# --- differential over ingested variants ------------------------------------------------


def test_white_page_for_bare_is_cloaking_with_gating_and_white_page(tmp_path, store):
    _variant(tmp_path, "ad_click", OFFER)
    _variant(tmp_path, "bare", WHITE)
    views = _run(store, tmp_path)
    assert set(views) == {"ad_click", "bare"}  # each variant is its own view
    assert "[ad_click]" in views["ad_click"].label

    (j1,) = _obs(store, views["ad_click"], "cloak.differential")
    assert j1.state is State.PRESENT and j1.signal_id == "J1"
    assert j1.value["difference"] >= THRESHOLD
    assert j1.value["against_view"] == views["bare"].view_id
    assert j1.source.file == "differential.json"

    (j5,) = _obs(store, views["ad_click"], "cloak.gating")
    assert j5.signal_id == "J5"
    assert j5.value["factors"] == [
        "referrer https://l.facebook.com/",
        "query string and a click id (synthetic fbclid)",
        "mobile device",
    ]

    (j2,) = _obs(store, views["bare"], "cloak.white_page")
    assert j2.signal_id == "J2"
    assert any("no trackers" in t for t in j2.value["traits"])
    assert any("obfuscated" in t for t in j2.value["traits"])

    summary = reason_summary_for_view(
        views["ad_click"].view_id, store.observations(views["ad_click"].view_id)
    )
    signals = {r.signal_id for r in summary.reasons_by_kind["evasion"]}
    assert {"J1", "J5"} <= signals


def test_same_page_for_every_profile_is_checked_and_clean(tmp_path, store):
    _variant(tmp_path, "ad_click", OFFER)
    _variant(tmp_path, "bare", OFFER)
    views = _run(store, tmp_path)
    (j1,) = _obs(store, views["ad_click"], "cloak.differential")
    assert j1.state is State.ABSENT and j1.value["difference"] < 0.1
    assert not _obs(store, views["ad_click"], "cloak.gating")
    assert not _obs(store, views["bare"], "cloak.white_page")
    summary = reason_summary_for_view(
        views["ad_click"].view_id, store.observations(views["ad_click"].view_id)
    )
    assert "evasion" not in summary.reasons_by_kind
    assert "evasion" not in summary.unchecked_kinds  # the test ran: not a silent gap


def test_failed_variant_is_not_observable(tmp_path, store):
    _variant(tmp_path, "ad_click", OFFER)
    _variant(tmp_path, "bare", None, status="dns_failed")
    views = _run(store, tmp_path)
    (j1,) = _obs(store, views["ad_click"], "cloak.differential")
    assert j1.state is State.NOT_OBSERVABLE
    assert j1.value["not_loaded"] == {"bare": "dns_failed"}


def test_rerun_replaces_rows(tmp_path, store):
    _variant(tmp_path, "ad_click", OFFER)
    _variant(tmp_path, "bare", WHITE)
    views = _run(store, tmp_path)
    pipeline.run_cloaking(store)
    assert len(_obs(store, views["ad_click"], "cloak.differential")) == 1


def test_timeout_that_rendered_a_page_is_compared(tmp_path, store):
    _variant(tmp_path, "ad_click", OFFER)
    _variant(tmp_path, "bare", WHITE, status="timeout")
    views = _run(store, tmp_path)
    (j1,) = _obs(store, views["ad_click"], "cloak.differential")
    assert j1.state is State.PRESENT and j1.value["statuses"]["bare"] == "timeout"


def test_http_error_on_bare_is_not_gating(tmp_path, store):
    _variant(tmp_path, "ad_click", OFFER)
    _variant(tmp_path, "bare", "<html><body><h1>404 Not Found</h1></body></html>", "http_error")
    views = _run(store, tmp_path)
    (j1,) = _obs(store, views["ad_click"], "cloak.differential")
    assert j1.state is State.PRESENT and "caveat" in j1.value and "HTTP error" in j1.raw
    assert not _obs(store, views["ad_click"], "cloak.gating")


def test_gating_factors_come_from_what_was_captured(tmp_path, store):
    tested = "https://offer.example/lp"  # no query: only the synthetic click id differs
    _variant(tmp_path, "ad_click", OFFER, tested=tested)
    _variant(tmp_path, "bare", WHITE, tested=tested)
    views = _run(store, tmp_path)
    (j5,) = _obs(store, views["ad_click"], "cloak.gating")
    assert "click id (synthetic fbclid)" in j5.value["factors"]
    assert not any(f.startswith("query string") for f in j5.value["factors"])


def test_crawler_is_compared_but_is_not_gating(tmp_path, store):
    _variant(tmp_path, "ad_click", OFFER)
    _variant(tmp_path, "bare", OFFER)
    _variant(tmp_path, "crawler", WHITE)
    views = _run(store, tmp_path)
    rows = {o.value["against"]: o for o in _obs(store, views["ad_click"], "cloak.differential")}
    assert rows["bare"].state is State.ABSENT and rows["crawler"].state is State.PRESENT
    assert not _obs(store, views["ad_click"], "cloak.gating")
    assert _obs(store, views["crawler"], "cloak.white_page")


def test_two_runs_in_one_incident_are_never_mixed(tmp_path, store):
    _variant(tmp_path / "run1", "ad_click", OFFER, run="r1")
    _variant(tmp_path / "run1", "bare", OFFER, run="r1")
    _variant(tmp_path / "run2", "ad_click", OFFER, run="r2")
    _variant(tmp_path / "run2", "bare", WHITE, run="r2")
    _, views = ingest_folder(tmp_path, store)
    pipeline.run_extract(store)
    pipeline.run_fingerprint(store)
    pipeline.run_cloaking(store)
    by_run = {(v.meta["capture_run"], v.meta["capture_profile"]): v for v in views}
    (r1,) = _obs(store, by_run["r1", "ad_click"], "cloak.differential")
    (r2,) = _obs(store, by_run["r2", "ad_click"], "cloak.differential")
    assert r1.state is State.ABSENT and r1.value["against_view"] == by_run["r1", "bare"].view_id
    assert r2.state is State.PRESENT and r2.value["against_view"] == by_run["r2", "bare"].view_id


def test_stale_bundle_from_an_earlier_run_is_left_out(tmp_path, store):
    _variant(tmp_path, "ad_click", OFFER, run="new")
    _variant(tmp_path, "bare", OFFER, run="new")
    _variant(tmp_path, "crawler", WHITE, run="old")  # left behind by a --crawler run
    views = _run(store, tmp_path)
    (j1,) = _obs(store, views["ad_click"], "cloak.differential")
    assert j1.value["against"] == "bare"
    assert not [
        o for o in store.observations(views["crawler"].view_id) if o.namespace.startswith("cloak.")
    ]


def test_bundles_without_a_run_id_pair_by_folder(tmp_path, store):
    for run in ("a", "b"):
        _variant(tmp_path / run, "ad_click", OFFER, run=None)
        _variant(tmp_path / run, "bare", WHITE if run == "b" else OFFER, run=None)
    ingest_folder(tmp_path, store)
    pipeline.run_extract(store)
    pipeline.run_fingerprint(store)
    pipeline.run_cloaking(store)
    rows = [
        o
        for v in store.views()
        for o in store.observations(v.view_id)
        if o.namespace == "cloak.differential"
    ]
    assert sorted(o.state.value for o in rows) == ["absent", "present"]
    assert all(o.value["against"] == "bare" for o in rows)  # never ad_click vs ad_click


def test_crawler_flag_alone_runs_the_differential(tmp_path, monkeypatch):
    import ftf.capture
    from ftf.capture import DifferentialResult
    from ftf.cli import main

    called = {}

    def fake(url, incident_dir, *, profiles, **kwargs):
        called["profiles"] = [p.name for p in profiles]
        return DifferentialResult(url=url, incident=incident_dir)

    monkeypatch.setattr(ftf.capture, "capture_differential", fake)
    with pytest.raises(SystemExit):
        main(["capture", URL, "--crawler", "--out", str(tmp_path / "inc")])
    assert called["profiles"] == ["ad_click", "bare", "crawler"]


def test_plain_captures_are_untouched(tmp_path, store):
    b = tmp_path / "bundle"
    b.mkdir()
    (b / "manifest.json").write_text(
        json.dumps({"schema": SCHEMA, "page_url": URL, "captured_at": "2026-11-22T10:00:00Z"})
    )
    (b / "dom.html").write_text(OFFER)
    ingest_folder(tmp_path, store)
    pipeline.run_extract(store)
    assert pipeline.run_cloaking(store) == 0


# --- browser: a local server that cloaks ------------------------------------------------


browser = pytest.mark.usefixtures("chromium")



class _Cloaker(BaseHTTPRequestHandler):
    seen: list[dict] = []  # noqa: RUF012

    def log_message(self, *args):
        pass

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        referer = self.headers.get("Referer") or ""
        _Cloaker.seen.append(
            {
                "path": self.path,
                "referer": referer,
                "ua": self.headers.get("User-Agent"),
                "ch": self.headers.get("sec-ch-ua") or "",
                "platform": self.headers.get("sec-ch-ua-platform") or "",
            }
        )
        if urlsplit(self.path).path != "/lp":
            self.send_response(404)
            self.end_headers()
            return
        body = OFFER if "facebook.com" in referer and "fbclid" in query else WHITE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())


@pytest.fixture
def cloaker():
    _Cloaker.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Cloaker)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.browser
@browser
def test_live_differential_catches_a_referrer_gated_offer(tmp_path, store, cloaker):
    from ftf.capture import capture_differential

    result = capture_differential(f"{cloaker}/lp?utm_source=fb", tmp_path, timeout=15, settle=1)
    assert result.ok
    ad, bare = result.captures["ad_click"].manifest, result.captures["bare"].manifest
    assert ad.profile == "ad_click" and ad.click_id_added and ad.referer
    assert "fbclid=" in ad.page_url and bare.page_url == f"{cloaker}/lp"
    assert ad.viewport["width"] < 500
    first = {s["path"].split("?")[0] + str("fbclid" in s["path"]): s for s in _Cloaker.seen}
    assert first["/lpTrue"]["referer"].startswith("https://l.facebook.com")
    assert "Mobile" in first["/lpTrue"]["ua"] and not first["/lpFalse"]["referer"]
    # client hints agree with the user agent: nothing says HeadlessChrome
    assert all("Headless" not in s["ch"] for s in _Cloaker.seen)
    assert first["/lpTrue"]["platform"] == '"Android"'
    assert first["/lpFalse"]["platform"] == '"Windows"'
    assert "Platforma" in (tmp_path / "ad_click" / "dom.html").read_text()
    assert "Business Solutions" in (tmp_path / "bare" / "dom.html").read_text()
    assert json.loads((tmp_path / "differential.json").read_text())["variants"].keys() == {
        "ad_click",
        "bare",
    }

    views = _run(store, tmp_path)
    assert len(views) == 2
    (j1,) = _obs(store, views["ad_click"], "cloak.differential")
    assert j1.state is State.PRESENT and j1.value["difference"] >= THRESHOLD
