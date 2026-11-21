"""The shared reader behind the identifier extractors. Synthetic material only."""

import base64
import json
from pathlib import Path

import pytest

from ftf.extract import _material as m
from ftf.ingest import ingest_folder
from ftf.registry import Context


@pytest.fixture
def ctx_for():
    m.clear_cache()
    return Context


AIO = {
    "campaign": {"uuid": "0b9611d9-0000-4000-8000-000000000001", "name": "1038 | PL - Test"},
    "visit": {"ip": "10.0.0.1", "fields": {"fb_pixel": "2012165449475163"}},
}


def _js_string(obj) -> str:
    """Encode as a single-quoted JS literal the way the kit does it."""
    return json.dumps(json.dumps(obj))[1:-1].replace("'", "\\'").replace("/", "\\/")


def make_saved_page(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "page.html").write_text(
        "<!-- saved from url=(0040)https://www.shop.example.org/L1/?pxl=1 -->\n"
        "<html><head><base href='https://cdn.example.org/landers/x/'>"
        "<script src='https://connect.facebook.net/en_US/fbevents.js'></script>"
        f"<script>window.aio = JSON.parse('{_js_string(AIO)}');</script>"
        "<script>fbq('init', '2012165449475163');</script>"
        "</head><body>"
        "<form action='https://track.example.net/lander/order.php' method='post'>"
        "<input type='hidden' name='sub2' value='Yosha'>"
        "<input type='hidden' name='fbtoken' value='{fbtoken}'>"
        "<input type='text' name='phone'>"
        "</form><a href='https://offer.example.com/go'>Buy</a>"
        "<iframe src='https://frame.example.com/'></iframe>"
        "</body></html>",
        encoding="utf-8",
    )
    files = root / "page_files"
    files.mkdir()
    (files / "main.js.pobrane").write_text("var LANDING_FORM_EVENT_BACKEND_DOMAIN='x.example';")
    (files / "1549107073347817").write_text("")
    (files / "style.css").write_text("body{}")
    (root / "shop.example.org.har").write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {"url": "https://www.shop.example.org/L1/", "method": "GET"},
                            "response": {"status": 200, "content": {"mimeType": "text/html"}},
                            "_resourceType": "document",
                        },
                        {
                            "request": {
                                "url": "https://api.example.net/v1/landing/form-event",
                                "method": "POST",
                                "postData": {"text": '{"pixel":"1"}'},
                            },
                            "response": {
                                "status": 200,
                                "content": {"mimeType": "application/json"},
                            },
                            "_resourceType": "fetch",
                        },
                    ]
                }
            }
        )
    )
    return root


def make_har_only(root: Path) -> Path:
    root.mkdir(parents=True)
    body = (
        "<html><body><script>navigator.sendBeacon('https://beacon.example.pro/c');</script>"
        "</body></html>"
    )
    js = "fetch('https://api.example.pro/x')"
    (root / "landing.har").write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {"url": "https://health.example.pro/a/", "method": "GET"},
                            "response": {
                                "status": 200,
                                "content": {
                                    "mimeType": "text/html",
                                    "encoding": "base64",
                                    "text": base64.b64encode(body.encode()).decode(),
                                },
                            },
                            "_resourceType": "document",
                        },
                        {
                            "request": {
                                "url": "https://health.example.pro/app.js",
                                "method": "GET",
                            },
                            "response": {
                                "status": 200,
                                "content": {"mimeType": "application/javascript", "text": js},
                            },
                            "_resourceType": "script",
                        },
                        {
                            "request": {"url": "https://other.example.com/lib.js", "method": "GET"},
                            "response": {
                                "status": 200,
                                "content": {"mimeType": "application/javascript", "text": "x"},
                            },
                            "_resourceType": "script",
                        },
                    ]
                }
            }
        )
    )
    return root


def _page(store, root):
    _, views = ingest_folder(root, store)
    return next(v for v in views if v.kind.value == "page")


def test_saved_page_reads_every_location(store, tmp_path, ctx_for):
    view = _page(store, make_saved_page(tmp_path / "1"))
    mat = m.load(view, ctx_for(store))
    assert not mat.errors
    assert mat.html is not None and mat.html.source.file == "page.html"
    assert len(mat.inline_scripts) == 2
    assert mat.inline_scripts[1].source.locator == "html:script[2]"
    assert [t.attrs["src"] for t in mat.script_srcs] == [
        "https://connect.facebook.net/en_US/fbevents.js"
    ]
    assert {t.attrs["name"] for t in mat.hidden_inputs} == {"sub2", "fbtoken"}
    assert mat.forms[0].attrs["action"].endswith("/lander/order.php")
    assert mat.anchors[0].attrs["href"] == "https://offer.example.com/go"
    assert mat.iframes[0].attrs["src"] == "https://frame.example.com/"
    assert mat.base_href == "https://cdn.example.org/landers/x/"
    assert [s.source.file for s in mat.first_party_scripts] == ["page_files/main.js.pobrane"]
    assert "1549107073347817" in {t.text for t in mat.resource_names}
    assert [r.method for r in mat.har_requests] == ["GET", "POST"]
    assert mat.har_requests[1].post_text == '{"pixel":"1"}'
    assert mat.har_requests[1].source.locator == "har:entries[1].request.url"
    assert m.config_get(mat.aio, "campaign.uuid") == AIO["campaign"]["uuid"]
    assert m.config_get(mat.aio, "visit.fields.fb_pixel") == "2012165449475163"
    assert mat.aio_source.locator == "html:script[1]"


def test_har_only_view_reads_base64_body_and_own_scripts(store, tmp_path, ctx_for):
    view = _page(store, make_har_only(tmp_path / "2"))
    assert view.primary is None
    mat = m.load(view, ctx_for(store))
    assert mat.html is not None and "sendBeacon" in mat.html.text
    assert mat.html.source.locator == "har:entries[0].response.content"
    assert mat.inline_scripts[0].source.locator.startswith("har:entries[0].response.content>")
    assert [s.text for s in mat.first_party_scripts] == ["fetch('https://api.example.pro/x')"]
    assert m.own_domain(view) == "example.pro"


def test_malformed_material_never_raises(store, tmp_path, ctx_for):
    root = tmp_path / "3"
    root.mkdir()
    (root / "page.html").write_bytes(
        b"<!-- saved from url=(0030)https://bad.example.org/ -->\n<html><script>window.aio = "
        b"JSON.parse('{broken');<form action=\xff\xfe<input type=hidden name="
    )
    (root / "bad.example.org.har").write_text("{not json")
    _, views = ingest_folder(root, store)
    for view in views:  # the unreadable HAR becomes its own HAR-only view
        mat = m.load(view, ctx_for(store))
        assert mat.aio is None and mat.har_requests == ()
    saved = next(v for v in views if v.primary == "page.html")
    assert m.load(saved, ctx_for(store)).html is not None


def test_images_and_videos_have_no_material(store, tmp_path, ctx_for):
    root = tmp_path / "4"
    root.mkdir()
    (root / "ad.png").write_bytes(b"\x89PNG")
    _, views = ingest_folder(root, store)
    assert m.load(views[0], ctx_for(store)) == m.Material()


def test_aio_config_forms():
    literal = 'var x=1; window.aio = {"campaign": {"uuid": "u-1", "name": "a}b"}};'
    assert m.aio_config(literal) == {"campaign": {"uuid": "u-1", "name": "a}b"}}
    assert m.aio_config(f"aio = JSON.parse('{_js_string(AIO)}')") == AIO
    assert m.aio_config("foo.aio = {}; window.aioX = {}") is None
    assert m.js_unescape(r"a\'b\u0041\x42\/c\\n") == "a'bAB/c\\n"


def test_placeholders():
    for value in ("", "  ", None, "{fbtoken}", "{{aio.visit.uuid}}", "%CLICK_ID%", "null", "0"):
        assert m.is_placeholder(value), value
    for value in ("2012165449475163", "APPA-165", "1038 | PL - Brzoska [v2]", "Yosha"):
        assert not m.is_placeholder(value), value


def test_emitter_dedups_and_keeps_first_source(store, tmp_path):
    view = _page(store, make_saved_page(tmp_path / "5"))
    emit = m.Emitter(view, "test", "1")
    first, second = m.Source(file="a", locator="x"), m.Source(file="b", locator="y")
    assert emit("A3", "fb_pixel", "123456789012345", first)
    assert not emit("A3", "fb_pixel", "123456789012345", second)
    assert not emit("A6", "aff.sub1", "{sub1}", first)
    assert [o.source for o in emit.out] == [first]


# --- review of PR #2 ----------------------------------------------------------------------


def test_malformed_post_body_does_not_cost_other_requests(store, tmp_path, ctx_for):
    root = tmp_path / "r1"
    root.mkdir()
    url = "https://shop.example.org/L1/"
    (root / "page.html").write_text(f"<!-- saved from url=(0028){url} -->\n<html></html>")
    post = lambda u, body: {"request": {"url": u, "method": "POST", "postData": body}}
    entries = [
        {
            "request": {"url": url, "method": "GET"},
            "response": {"status": 200, "content": {"mimeType": "text/html"}},
            "_resourceType": "document",
        },
        post("https://a.example.net/x", {"text": "pixel=1"}),
        post("https://b.example.net/y", "oops"),
        post("https://c.example.net/z", {"params": [{"name": "k", "value": "v"}]}),
    ]
    (root / "shop.example.org.har").write_text(json.dumps({"log": {"entries": entries}}))
    view = _page(store, root)
    mat = m.load(view, ctx_for(store))
    assert [r.index for r in mat.har_requests] == [0, 1, 2, 3]
    assert [r.post_text for r in mat.har_requests[1:]] == ["pixel=1", None, "k=v"]


def test_cache_sees_a_resource_edited_in_place(store, tmp_path, ctx_for):
    import os

    root = tmp_path / "r2"
    root.mkdir()
    (root / "page.html").write_text(
        "<!-- saved from url=(0028)https://shop.example.org/L1/ -->\n<html></html>"
    )
    files = root / "page_files"
    files.mkdir()
    script = files / "app.js"
    script.write_text("fbq('init', '1111111111111111');")
    view = _page(store, root)
    assert m.load(view, ctx_for(store)).first_party_scripts[0].text.endswith("'1111111111111111');")

    folder_mtime = files.stat().st_mtime_ns
    script.write_text("fbq('init', '2222222222222222');")  # same size, same folder
    later = script.stat().st_mtime_ns + 5_000_000_000
    os.utime(script, ns=(later, later))
    os.utime(files, ns=(folder_mtime, folder_mtime))  # the folder itself looks unchanged
    view = _page(store, root)  # re-ingest in the same process, no clear_cache()
    assert m.load(view, Context(store)).first_party_scripts[0].text.endswith("'2222222222222222');")


def test_cache_sees_a_renamed_resource(store, tmp_path, ctx_for):
    # Review of PR #2, second round: a rename keeps count, total size and mtimes.
    from ftf.extract.tracking_ids import TrackingPixels

    root = tmp_path / "r3"
    root.mkdir()
    (root / "page.html").write_text(
        "<!-- saved from url=(0028)https://shop.example.org/L1/ -->\n<html></html>"
    )
    files = root / "page_files"
    files.mkdir()
    (files / "2012165449475163").write_text("")
    view = _page(store, root)
    first = TrackingPixels().run(view, Context(store))
    assert [o.value for o in first] == ["2012165449475163"]

    (files / "2012165449475163").rename(files / "1549107073347817")
    view = _page(store, root)  # re-ingest in the same process, no clear_cache()
    second = TrackingPixels().run(view, Context(store))
    assert [(o.value, o.source.file) for o in second] == [
        ("1549107073347817", "page_files/1549107073347817")
    ]
