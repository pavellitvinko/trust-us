"""The run's own video, its captions, and the rule that the on-page marker is never evidence."""

from __future__ import annotations

import json

from ftf.ingest import ingest_folder
from ftf.store import Store
from ftf.victim.overlay import HOST_ID, Cue, Overlay
from ftf.victim.record import Recorder

from .test_victim_browser import _headless, chromium, site, walk  # noqa: F401 - fixtures


def test_captions_are_webvtt_and_a_repeated_caption_is_one_cue():
    o = Overlay(who="persona x", draw=False)
    o.cues = [
        Cue(1, 0.5, "Opens the ad link"),
        Cue(1, 2.0, "Step 1 · Clicks “Dalej”"),
        Cue(1, 3.0, "Step 1 · Clicks “Dalej”"),
        Cue(1, 65.25, "Run ends"),
        Cue(2, 1.0, "another clip"),
    ]
    vtt = o.vtt(1)
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:00.500 --> 00:00:02.000\nOpens the ad link" in vtt
    assert "00:00:02.000 --> 00:01:05.250\nStep 1" in vtt
    assert "00:01:05.250 --> 00:01:09.250\nRun ends" in vtt
    assert vtt.count("Clicks") == 1 and "another clip" not in vtt


def test_a_run_records_its_video_with_captions_in_order(chromium, site, tmp_path):  # noqa: F811
    origin, _posts = site
    _run, result = walk(f"{origin}/index.html?fbclid=abc", tmp_path)
    rec = result.manifest["recording"]
    assert rec["size"] == {"width": 1280, "height": 860}
    assert [c["video"] for c in rec["clips"]] == ["run_files/recording/clip-01.webm"]
    video = result.root / rec["clips"][0]["video"]
    assert video.is_file() and video.stat().st_size > 1000
    vtt = (result.root / rec["clips"][0]["captions"]).read_text(encoding="utf-8")
    assert vtt.index("Opens the ad link") < vtt.index("Sends the form") < vtt.index("Run ends")
    assert "Types “Halina”" in vtt
    on_disk = json.loads((result.root / "run_files/recording/recording.json").read_text())
    assert on_disk == rec
    # The marker never reached a saved DOM.
    for dom in result.root.glob("stage-*/dom.html"):
        assert HOST_ID not in dom.read_text(encoding="utf-8")


def test_the_video_is_not_ingested_as_material(chromium, site, tmp_path):  # noqa: F811
    origin, _posts = site
    _run, result = walk(f"{origin}/index.html?fbclid=abc", tmp_path)
    store = Store(tmp_path / "t.db")
    _incident, views = ingest_folder(result.root, store)
    assert views and all(v.kind.value == "page" for v in views)
    assert not any("recording" in (v.primary or "") for v in views)


def test_screenshots_are_the_same_with_the_marker_shown(chromium, site, tmp_path):  # noqa: F811
    from playwright.sync_api import sync_playwright

    origin, _posts = site
    overlay = Overlay(who="persona x")
    rec = Recorder(tmp_path, "t")
    rec.before_capture = overlay.clear
    with sync_playwright() as pw:
        browser = _headless(pw)
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.goto(f"{origin}/thanks.html")
        clean = rec.step_screenshot(page, 1)
        overlay.show(page, "Clicks “Dalej”", "because", "Step 1", xpath="//body/*[1]", dot=True)
        assert page.evaluate(f"!!document.getElementById('{HOST_ID}')")
        assert "Clicks" not in page.evaluate("document.body.innerText")
        marked = rec.step_screenshot(page, 2)
        assert not page.evaluate(f"!!document.getElementById('{HOST_ID}')")
        browser.close()
    assert (tmp_path / "victim-t" / clean).read_bytes() == (
        tmp_path / "victim-t" / marked
    ).read_bytes()
