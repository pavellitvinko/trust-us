"""Live capture and replay (HAC-14).

Unit tests need no browser. Browser tests run a local HTTP server and are skipped where
Chromium is not installed (`uv run playwright install chromium`).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from ftf import pipeline
from ftf.capture.bundle import (
    SCHEMA,
    Hop,
    HopKind,
    Manifest,
    Server,
    Status,
    build_trace,
    classify_error,
    meta_refresh_target,
    read_manifest,
)
from ftf.extract.capture_trace import behind_cdn
from ftf.ingest import document_redirects, ingest_folder

SAMPLE = Path(__file__).resolve().parents[1] / "testing_sample"


def _entry(url, status=200, location="", body="", mime="text/html", rtype=None):
    entry = {
        "startedDateTime": "2026-11-20T10:00:00.000Z",
        "request": {"method": "GET", "url": url, "headers": []},
        "response": {
            "status": status,
            "redirectURL": location,
            "headers": [{"name": "Location", "value": location}] if location else [],
            "content": {"mimeType": mime, "text": body},
        },
    }
    if rtype:
        entry["_resourceType"] = rtype
    return entry


def _write_har(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"log": {"version": "1.2", "entries": entries}}), encoding="utf-8")


def _obs(store, view_id, namespace):
    return [o for o in store.observations(view_id) if o.namespace == namespace]


# --- unit: trace, manifest, errors ------------------------------------------------------


def test_meta_refresh_target_resolves_relative_url():
    doc = '<html><head><META HTTP-EQUIV="Refresh" CONTENT="0; URL=\'/next?a=1\'"></head></html>'
    assert meta_refresh_target(doc, "https://a.example/x/") == "https://a.example/next?a=1"
    assert meta_refresh_target("<p>none</p>", "https://a.example/") is None


def test_build_trace_tells_http_meta_and_script_apart():
    entries = [
        _entry("https://ad.example/c?id=1", 302, "https://t.example/r"),
        _entry("https://t.example/r", 301, "https://t.example/m"),
        _entry(
            "https://t.example/m",
            body='<meta http-equiv="refresh" content="0;url=https://land.example/js">',
        ),
        _entry("https://land.example/js", body="<script>location='/final'</script>"),
        _entry("https://land.example/final"),
    ]
    chains = [
        ["https://ad.example/c?id=1", "https://t.example/r", "https://t.example/m"],
        ["https://land.example/js"],
        ["https://land.example/final"],
    ]
    hops = build_trace(chains, entries)
    assert [(h.kind, h.status) for h in hops] == [
        (HopKind.HTTP, 302),
        (HopKind.HTTP, 301),
        (HopKind.META_REFRESH, None),
        (HopKind.SCRIPT, None),
    ]
    assert [h.index for h in hops] == [0, 1, 2, 3]
    assert hops[0].source == "page.har:entries[0]"
    assert hops[2].source == "page.har:entries[2]"  # the document that carried the meta tag
    assert hops[-1].to_url == "https://land.example/final"


def test_build_trace_reads_a_replayed_redirect_from_the_archive():
    # A HAR router answers a 3xx by starting a new navigation: two chains, one HTTP hop.
    entries = [
        _entry("https://click.tracker.xyz/go", 302, "https://offer.live/"),
        _entry("https://offer.live/"),
    ]
    hops = build_trace([["https://click.tracker.xyz/go"], ["https://offer.live/"]], entries)
    assert [(h.kind, h.status, h.source) for h in hops] == [
        (HopKind.HTTP, 302, "page.har:entries[0]")
    ]


def test_build_trace_records_a_history_rewrite():
    hops = build_trace([["https://a.live/?x=1"]], [], final_url="https://a.live/?x=2")
    assert [(h.kind, h.to_url) for h in hops] == [(HopKind.HISTORY, "https://a.live/?x=2")]
    assert build_trace([["https://a.live/"]], [], final_url="https://a.live/#top") == []


def test_build_trace_without_navigation_is_empty():
    assert build_trace([["https://a.example/"]], []) == []
    assert build_trace([], []) == []


@pytest.mark.parametrize(
    ("message", "status"),
    [
        ("net::ERR_NAME_NOT_RESOLVED at https://x.live/", Status.DNS_FAILED),
        ("net::ERR_CONNECTION_REFUSED at http://127.0.0.1:9/", Status.CONNECTION_FAILED),
        ("net::ERR_CERT_DATE_INVALID", Status.TLS_FAILED),
        ("Timeout 30000ms exceeded.", Status.TIMEOUT),
        ("net::ERR_ABORTED", Status.FAILED),
    ],
)
def test_classify_error(message, status):
    assert classify_error(message) is status


def test_manifest_round_trip(tmp_path):
    man = Manifest(
        page_url="https://a.example/",
        captured_at="2026-11-22T10:00:00+00:00",
        redirects=[
            Hop(index=0, from_url="https://a.example/", to_url="https://b.example/", kind="http")
        ],
        server=Server(ip="203.0.113.5", port=443, headers={"server": "nginx"}),
    )
    man.write(tmp_path)
    data = json.loads((tmp_path / "manifest.json").read_text())
    assert data["schema"] == SCHEMA and data["captured_at"].startswith("2026-11-22")
    back = read_manifest(tmp_path / "manifest.json")
    assert back is not None and back.redirects[0].kind is HopKind.HTTP
    (tmp_path / "other.json").write_text('{"page_url": "https://a.example/"}')
    assert read_manifest(tmp_path / "other.json") is None  # an organiser bundle's manifest


def test_behind_cdn():
    assert behind_cdn({"cf-ray": "1", "server": "cloudflare"})
    assert behind_cdn({"server": "cloudflare"})
    assert not behind_cdn({"server": "nginx/1.25"})


def test_har_redirects_follow_only_the_document_chain():
    entries = [
        _entry("https://px.example/b", 302, "https://ads.example/x", mime="", rtype="image"),
        _entry("https://short.example/a", 302, "https://land.example/", rtype="document"),
        _entry("https://land.example/", rtype="document"),
    ]
    hops = document_redirects(entries)
    assert hops == [(1, "https://short.example/a", "https://land.example/", 302)]


# --- unit: ingest and extraction of bundles without a browser ---------------------------


def _bundle(root: Path, manifest: dict, dom: str | None = "<html><body>Hi</body></html>"):
    b = root / "bundle"
    b.mkdir(parents=True)
    (b / "manifest.json").write_text(json.dumps({"schema": SCHEMA, **manifest}))
    if dom is not None:
        (b / "dom.html").write_text(dom)
    return b


def test_dead_domain_bundle_is_a_view_with_an_explicit_failure(tmp_path, store):
    _bundle(
        tmp_path / "dead",
        {
            "page_url": "https://gone.example/",
            "captured_at": "2026-11-22T10:00:00+00:00",
            "status": "dns_failed",
            "error": "net::ERR_NAME_NOT_RESOLVED",
        },
        dom=None,
    )
    _, views = ingest_folder(tmp_path / "dead", store)
    assert len(views) == 1
    view = views[0]
    assert view.primary is None and view.meta["capture_status"] == "dns_failed"
    assert "(dns_failed)" in view.label
    pipeline.run_extract(store)
    status = _obs(store, view.view_id, "capture.status")
    assert [o.value for o in status] == ["dns_failed"]
    assert status[0].raw == "net::ERR_NAME_NOT_RESOLVED"
    assert status[0].source.locator == "manifest:status"
    assert [o.value for o in _obs(store, view.view_id, "domain.registrable")] == ["gone.example"]


def test_organiser_manifest_fields_are_not_read_as_capture_fields(tmp_path, store):
    b = tmp_path / "org" / "bundle"
    b.mkdir(parents=True)
    (b / "dom.html").write_text("<html><body>x</body></html>")
    (b / "manifest.json").write_text(
        json.dumps(
            {
                "page_url": "https://shown.live/",
                "final_url": "https://other.live/",
                "status": "complete",
            }
        )
    )
    _, (view,) = ingest_folder(tmp_path / "org", store)
    assert view.meta == {"origin": "capture_bundle", "manifest_url": "https://shown.live/"}
    assert view.label.endswith("shown.live")


def test_proxied_capture_does_not_claim_an_origin_ip(tmp_path, store):
    _bundle(
        tmp_path / "px",
        {
            "page_url": "https://offer.live/",
            "captured_at": "2026-11-22T10:00:00+00:00",
            "egress": "http://proxy.local:3128",
            "server": {"ip": "10.0.0.1", "headers": {"server": "nginx"}},
        },
    )
    _, (view,) = ingest_folder(tmp_path / "px", store)
    pipeline.run_extract(store)
    assert not _obs(store, view.view_id, "origin.ip")


def test_bundle_redirects_and_server_become_sourced_observations(tmp_path, store):
    _bundle(
        tmp_path / "live",
        {
            "page_url": "https://click.tracker.xyz/c?id=9",
            "final_url": "https://offer.live/landing",
            "captured_at": "2026-11-22T10:00:00+00:00",
            "redirects": [
                {
                    "index": 0,
                    "from_url": "https://click.tracker.xyz/c?id=9",
                    "to_url": "https://offer.live/landing",
                    "kind": "http",
                    "status": 302,
                }
            ],
            "server": {
                "ip": "203.0.113.5",
                "headers": {"server": "nginx"},
                "tls": {
                    "issuer": "R11",
                    "valid_from": "2026-11-01T00:00:00+00:00",
                    "valid_to": "2027-01-30T00:00:00+00:00",
                },
            },
        },
    )
    _, (view,) = ingest_folder(tmp_path / "live", store)
    pipeline.run_extract(store)
    hosts = _obs(store, view.view_id, "url.host_path")
    assert [o.value for o in hosts] == ["offer.live/landing"]  # landed, not the click URL
    redirect = _obs(store, view.view_id, "redirect.domain")
    assert [o.value for o in redirect] == ["tracker.xyz"]
    assert redirect[0].source.file == "bundle/manifest.json"
    assert redirect[0].source.locator == "manifest:redirects[0]"
    assert redirect[0].signal_id == "I5"
    assert [o.value for o in _obs(store, view.view_id, "origin.ip")] == ["203.0.113.5"]
    assert [o.value for o in _obs(store, view.view_id, "tls.validity_days")] == [90]
    assert not _obs(store, view.view_id, "capture.status")  # ok is not a risk signal


# --- browser: live capture against a local server ---------------------------------------


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch(headless=True).close()
        return True
    except Exception:  # noqa: BLE001
        return False


browser = pytest.mark.skipif(not _chromium_available(), reason="Chromium not installed")

PAGES = {
    "/meta": '<html><head><meta http-equiv="refresh" content="0;url=/js"></head></html>',
    "/js": "<html><body><script>setTimeout(()=>location.href='/final',50)</script></body></html>",
    "/final": (
        "<html><head><title>Offer</title><link rel=stylesheet href=/s.css></head><body>"
        "<h1>Final offer</h1><div style='height:3000px'>long</div>"
        "<form method=post action=/lead><input name=phone></form>"
        "<script>setTimeout(()=>document.forms[0].submit(),100);"
        "fetch('/lead-xhr',{method:'POST',headers:{'content-type':"
        "'application/x-www-form-urlencoded'},body:'phone=1'}).catch(()=>{});"
        "fetch('/beacon',{method:'POST',headers:{'content-type':'application/json'},"
        "body:'{}'}).catch(()=>{})</script></body></html>"
    ),
    "/dl": "<html><body><h1>Get it</h1><script>location.href='/file.zip'</script></body></html>",
    "/onward": (
        "<html><body><h1>Landing kept</h1>"
        "<script>setTimeout(()=>location.href='ONWARD',100)</script></body></html>"
    ),
    "/s.css": "h1{color:red}",
    "/file.zip": "PK",
}


class _Handler(BaseHTTPRequestHandler):
    posts: ClassVar[list[str]] = []
    onward: ClassVar[str] = ""

    def log_message(self, *args):  # keep test output quiet
        pass

    def do_GET(self):
        if self.path == "/r":
            self.send_response(302)
            self.send_header("Location", "/meta")
            self.end_headers()
            return
        body = PAGES.get(self.path, "").replace("ONWARD", _Handler.onward) or None
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        if self.path.endswith(".css"):
            ctype = "text/css"
        elif self.path.endswith(".zip"):
            ctype = "application/zip"
            self.send_header("Content-Disposition", "attachment; filename=file.zip")
        else:
            ctype = "text/html"
        self.send_header("Content-Type", ctype)
        self.send_header("Server", "testsrv/1.0")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self):
        _Handler.posts.append(self.path)
        self.send_response(200)
        self.end_headers()


@pytest.fixture
def site():
    _Handler.posts = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@browser
def test_live_capture_traces_redirects_and_never_submits(tmp_path, store, site):
    from ftf.capture import capture_url

    result = capture_url(f"{site}/r", tmp_path / "inc" / "bundle", timeout=15, settle=2)
    man = result.manifest
    assert man.status is Status.OK, man.error
    assert man.final_url == f"{site}/final"
    assert [h.kind for h in man.redirects] == [HopKind.HTTP, HopKind.META_REFRESH, HopKind.SCRIPT]
    assert man.redirects[0].status == 302
    assert _Handler.posts == ["/beacon"]  # the form and the form-encoded fetch were stopped
    assert any(b.error == "form submit" for b in man.blocked)
    assert any(b.url.endswith("/lead-xhr") and b.method == "POST" for b in man.blocked)
    assert man.server and man.server.ip == "127.0.0.1" and man.server.headers["server"]
    bundle = result.bundle
    for name in ("dom.html", "page.har", "screenshot.png", "manifest.json"):
        assert (bundle / name).is_file(), name
    assert "Final offer" in (bundle / "dom.html").read_text()
    assert any(r.url.endswith("/s.css") for r in man.resources)
    assert all((bundle / r.path).is_file() for r in man.resources)

    _, (view,) = ingest_folder(tmp_path / "inc", store)
    assert view.har == "bundle/page.har" and view.primary == "bundle/dom.html"
    pipeline.run_extract(store)
    hops = _obs(store, view.view_id, "redirect.hop")
    assert len(hops) == 3 and all(o.source.locator.startswith("manifest:") for o in hops)
    assert _obs(store, view.view_id, "url.host_path")


@browser
def test_dead_domain_capture_records_failure(tmp_path):
    import socket

    from ftf.capture import capture_url

    with socket.socket() as sock:  # a port that was free a moment ago: nothing listens
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = capture_url(f"http://127.0.0.1:{port}/", tmp_path / "b", timeout=10, settle=1)
    assert result.manifest.status is Status.CONNECTION_FAILED
    assert (tmp_path / "b" / "manifest.json").is_file()
    assert not (tmp_path / "b" / "dom.html").exists()


@browser
def test_replay_serves_the_archive(tmp_path, store):
    from ftf.capture import capture_url

    har = tmp_path / "archive.har"
    _write_har(
        har,
        [
            _entry(
                "https://gone-offer.live/",
                body="<html><body><h1>Archived offer</h1>"
                "<script>fbq('init','2012165449475163')</script></body></html>",
                rtype="document",
            )
        ],
    )
    result = capture_url(None, tmp_path / "inc" / "bundle", replay_har=har, timeout=15, settle=1)
    man = result.manifest
    assert man.status is Status.OK, man.error
    assert man.mode == "replay" and man.page_url == "https://gone-offer.live/"
    assert man.captured_at.startswith("2026-11-20")  # the archive's time, not today
    assert man.replayed_at and man.replay_source["file"] == "archive.har"
    assert "Archived offer" in (result.bundle / "dom.html").read_text()
    assert (result.bundle / "page.har").read_bytes() == har.read_bytes()

    _, (view,) = ingest_folder(tmp_path / "inc", store)
    pipeline.run_extract(store)
    assert [o.value for o in _obs(store, view.view_id, "domain.registrable")] == ["gone-offer.live"]
    assert [o.value for o in _obs(store, view.view_id, "fb_pixel")] == ["2012165449475163"]


@browser
@pytest.mark.skipif(not SAMPLE.is_dir(), reason="testing_sample/ not present")
def test_replay_of_a_sample_har(tmp_path, store):
    from ftf.capture import capture_url

    har = SAMPLE / "2" / "healthlinkq.pro.har"
    result = capture_url(None, tmp_path / "inc" / "bundle", replay_har=har, timeout=30, settle=2)
    assert result.manifest.status is Status.OK, result.manifest.error
    _, (view,) = ingest_folder(tmp_path / "inc", store)
    pipeline.run_extract(store)
    assert [o.value for o in _obs(store, view.view_id, "domain.registrable")] == ["healthlinkq.pro"]
    assert len(store.observations(view.view_id)) > 10


def _closed_port() -> int:
    import socket

    with socket.socket() as sock:  # free a moment ago: nothing listens
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@browser
def test_download_is_refused(tmp_path, site):
    from ftf.capture import capture_url

    result = capture_url(f"{site}/dl", tmp_path / "b", timeout=15, settle=1)
    man = result.manifest
    assert result.ok, man.error
    assert man.final_url == f"{site}/dl"
    assert "Get it" in (tmp_path / "b" / "dom.html").read_text()
    assert not any(r.url.endswith(".zip") for r in man.resources)


@browser
def test_script_navigation_to_a_dead_host_keeps_the_landing(tmp_path, site):
    from ftf.capture import capture_url

    _Handler.onward = f"http://127.0.0.1:{_closed_port()}/gone"
    result = capture_url(f"{site}/onward", tmp_path / "b", timeout=15, settle=2)
    man = result.manifest
    assert man.status is Status.NAVIGATION_FAILED and result.ok
    assert man.final_url == f"{site}/onward"
    assert _Handler.onward in man.error
    assert "Landing kept" in (tmp_path / "b" / "dom.html").read_text()
    assert man.server and man.server.url == f"{site}/onward"
    assert [h.kind for h in man.redirects] == [HopKind.SCRIPT]
    assert man.redirects[0].to_url == _Handler.onward


@browser
def test_replay_walks_the_archived_redirect(tmp_path, store):
    from ftf.capture import capture_url

    har = tmp_path / "archive.har"
    _write_har(
        har,
        [
            _entry(
                "https://click.tracker.xyz/go?c=1", 302, "https://offer.live/", rtype="document"
            ),
            _entry("https://offer.live/", body="<h1>Offer</h1>", rtype="document"),
        ],
    )
    result = capture_url(None, tmp_path / "inc" / "bundle", replay_har=har, timeout=15, settle=1)
    man = result.manifest
    assert man.status is Status.OK, man.error
    assert man.page_url == "https://click.tracker.xyz/go?c=1"
    assert man.final_url == "https://offer.live/"
    assert [(h.kind, h.status) for h in man.redirects] == [(HopKind.HTTP, 302)]
    _, (view,) = ingest_folder(tmp_path / "inc", store)
    pipeline.run_extract(store)
    assert [o.value for o in _obs(store, view.view_id, "redirect.domain")] == ["tracker.xyz"]
    assert [o.value for o in _obs(store, view.view_id, "domain.registrable")] == ["offer.live"]


@browser
def test_cli_capture_ingests_and_runs(tmp_path, monkeypatch, site, capsys):
    from ftf.cli import main
    from ftf.store import Store

    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    main(["capture", f"{site}/r", "--out", str(tmp_path / "inc"), "--ingest", "--no-enrich"])
    out = capsys.readouterr().out
    assert "capture live: ok" in out and "hop 0: http 302" in out and "observations:" in out
    store = Store(tmp_path / "data" / "ftf.sqlite")
    (view,) = store.views()
    assert view.meta["capture_status"] == "ok"
    assert store.observations(view.view_id)

    with pytest.raises(SystemExit) as exc:
        main(["capture", f"http://127.0.0.1:{_closed_port()}/", "--out", str(tmp_path / "x")])
    assert exc.value.code == 3


@browser
def test_interface_replay_runs_inside_an_event_loop(tmp_path, store, monkeypatch):
    """The interface path: replay a HAR into a new incident and run every stage. Called from a
    thread with a running event loop, as Streamlit may, where Playwright's sync API refuses."""
    import asyncio

    from ftf import intake

    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    har = tmp_path / "archive.har"
    body = "<html><body><h1>Archived offer</h1><script>fbq('init','2012165449475163')</script>"
    _write_har(har, [_entry("https://gone-offer.live/", body=body + "</body></html>")])

    async def in_loop():
        return intake.capture_and_run(store, None, replay_har=har, enrich=False, timeout=15)

    result = asyncio.run(in_loop())
    assert result["capture"]["mode"] == "replay" and result["capture"]["ok"]
    assert result["views"] == 1 and result["incidents"][0].startswith("archive-")
    (view,) = [v for v in store.views() if v.incident_id == result["incidents"][0]]
    assert [o.value for o in _obs(store, view.view_id, "fb_pixel")] == ["2012165449475163"]


@browser
def test_interface_captures_a_live_url(tmp_path, monkeypatch, site):
    """Add material → URL in the Streamlit app: a live capture becomes a stored view."""
    from streamlit.testing.v1 import AppTest

    from ftf.store import Store

    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(str(root / "app/streamlit_app.py"), default_timeout=120).run()
    assert not app.exception
    next(t for t in app.text_input if t.label == "Or a URL to capture").input(f"{site}/r")
    next(c for c in app.checkbox if c.label.startswith("Look up")).uncheck()
    next(b for b in app.button if b.label == "Analyse material").click().run()
    assert not app.exception, app.exception
    assert not app.error, [e.value for e in app.error]
    assert any("Capture (live): ok" in i.value and "/final" in i.value for i in app.info)
    assert any("Added 1 views" in s.value for s in app.success)
    store = Store(tmp_path / "data" / "ftf.sqlite")
    (view,) = [v for v in store.views() if v.meta.get("capture_status") == "ok"]
    assert view.incident_id.startswith("127.0.0.1-")
