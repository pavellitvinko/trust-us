"""Victim runs over local fixture pages, in a real Chromium.

Real runs always open a visible window (there is no headless option in the product). These
tests pass a test-only `launcher` that starts Chromium headless, so they run anywhere a
Playwright Chromium is installed; they are skipped otherwise. Nothing leaves the machine:
the fixture server is on `funnel.localhost`, and a run that starts on a local address is
confined to loopback.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from ftf.config import Settings
from ftf.victim import rules
from ftf.victim.agent import RunOptions, VictimRun
from ftf.victim.control import write_command
from ftf.victim.persona import get_persona

FIXTURES = Path(__file__).parent / "fixtures" / "victim"
PERSONA = get_persona("retiree-income")


def _headless(pw):
    return pw.chromium.launch(headless=True)


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with sync_api.sync_playwright() as pw:
            pw.chromium.launch(headless=True).close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Playwright Chromium here: {exc}")


@pytest.fixture(scope="module")
def site():
    posts: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body=b"", headers=()):
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/get-bonus":
                self._send(
                    200,
                    b"PK\x03\x04bonus",
                    [
                        ("content-type", "application/zip"),
                        ("content-disposition", "attachment; filename=bonus.zip"),
                    ],
                )
                return
            file = FIXTURES / (path.strip("/") or "index.html")
            if not file.is_file():
                self._send(404, b"no")
                return
            self._send(200, file.read_bytes(), [("content-type", "text/html; charset=utf-8")])

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
            posts.append({"path": self.path, "form": parse_qs(body, keep_blank_values=True)})
            self._send(303, b"", [("location", "/thanks.html")])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://funnel.localhost:{server.server_port}", posts
    server.shutdown()


def settings(tmp_path, domain=""):
    return Settings(
        data_dir=tmp_path / "data",
        openai_api_key="",
        gemini_api_key="",
        openai_model="m",
        embedding_model="e",
        video_model="v",
        victim_email_domain=domain,
    )


def walk(url, tmp_path, domain="", **options):
    opts = RunOptions(pace=0, panel=False, max_steps=20, human_wait_s=0, **options)
    run = VictimRun(
        url,
        PERSONA,
        tmp_path / "runs",
        settings=settings(tmp_path, domain),
        options=opts,
        launcher=_headless,
        notify=None,
    )
    return run, run.run()


def log(result, name):
    path = result.root / "run_files" / f"{name}.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_full_funnel_to_a_callback_handoff(chromium, site, tmp_path):
    origin, posts = site
    posts.clear()
    run, result = walk(
        f"{origin}/index.html?utm_source=fb&fbclid=abc", tmp_path, domain="victim.test"
    )
    assert result.stop_reason == rules.OFFSITE_HANDOFF, result.stop_detail
    assert "konsultant zadzwoni" in result.stop_detail
    assert [s.url.split("/")[-1].split("?")[0] for s in result.stages] == [
        "index.html",
        "quiz.html",
        "thanks.html",
    ]
    assert result.manifest["funnel_shape"] == ["advertorial", "chat", "form", "thank_you"]
    # The lead went out with persona data and the run's own address only; no phone.
    assert len(posts) == 1
    form = posts[0]["form"]
    assert form["first_name"] == ["Halina"] and form["last_name"] == ["Wierzbicka"]
    assert form["email"] == [f"retiree-income.{run.run_id}@victim.test"]
    assert form["phone"] == [""]
    forms = log(result, "forms")
    assert forms[0]["sent"] and forms[0]["sent_to"][0]["method"] == "POST"
    # Funnel edges carry their proof: the click, then the form submit and its 303.
    third = result.stages[2].arrived_from
    assert third["how"] == "form_submit" and third["http_redirects"][0].endswith("/submit")
    assert result.stages[1].arrived_from["how"] == "link_click"
    chat = log(result, "chat")
    mine = [m for m in chat if m["who"] == "persona"]
    assert [m["text"] for m in mine] == ["Kobieta", "55+", "Niebieski"]
    assert [m["improvised"] for m in mine] == [False, False, True]  # no rule for colours
    steps = log(result, "steps")
    assert all(s["screenshot"] for s in steps if s["actor"] == "agent")
    refused = {r["reason"] for s in steps for r in s["refused"]}
    assert "share button (not the funnel)" in refused
    run_json = json.loads((result.root / "run_files" / "run.json").read_text())
    assert run_json["persona"] == {"id": "retiree-income", "version": "1"}
    assert run_json["referrer"] == "https://l.facebook.com/"
    assert (result.root / "stage-03" / "page.har").is_file()


def test_form_that_needs_an_email_is_not_sent_without_a_domain(chromium, site, tmp_path):
    origin, posts = site
    posts.clear()
    _, result = walk(f"{origin}/index.html", tmp_path)
    assert result.stop_reason == rules.FORM_NOT_SENT and "email_domain_unset" in result.stop_detail
    assert posts == []
    form = log(result, "forms")[0]
    assert form["sent"] is False and form["values_given"] == {
        "first_name": "Halina",
        "last_name": "Wierzbicka",
    }


def test_required_phone_stops_the_form(chromium, site, tmp_path):
    origin, posts = site
    posts.clear()
    _, result = walk(f"{origin}/phone.html", tmp_path, domain="victim.test")
    assert result.stop_reason == rules.FORM_NOT_SENT and "phone_required" in result.stop_detail
    assert posts == []


@pytest.mark.parametrize(
    ("page", "reason"),
    [
        ("payment.html", rules.FORBIDDEN_PAYMENT),
        ("upload.html", rules.FORBIDDEN_UPLOAD),
        ("permission.html", rules.FORBIDDEN_PERMISSION),
        ("download.html", rules.FORBIDDEN_DOWNLOAD),
        ("autodownload.html", rules.FORBIDDEN_DOWNLOAD),
        ("login.html", rules.BLOCKED),
        ("captcha.html", rules.BLOCKED),
    ],
)
def test_forbidden_steps_stop_the_run(chromium, site, tmp_path, page, reason):
    origin, posts = site
    posts.clear()
    _, result = walk(f"{origin}/{page}", tmp_path, domain="victim.test")
    assert result.stop_reason == reason, result.stop_detail
    assert posts == []  # nothing was ever sent
    blocks = log(result, "blocks")
    assert blocks and blocks[-1].get("reason", blocks[-1].get("kind"))
    if page == "permission.html":
        assert "notifications" in result.stop_detail
    if page == "login.html":
        assert any(b.get("kind") == "login" for b in blocks)


def test_stop_from_another_process_keeps_the_record(chromium, site, tmp_path):
    origin, _ = site
    run = VictimRun(
        f"{origin}/index.html",
        PERSONA,
        tmp_path / "runs",
        settings=settings(tmp_path),
        options=RunOptions(pace=0, panel=False),
        launcher=_headless,
        notify=None,
    )
    write_command(run.rec.files, "stop")
    result = run.run()
    assert result.stop_reason == rules.USER_STOP
    assert (result.root / "run_files" / "run.json").is_file()
    assert (result.root / "stage-01" / "dom.html").is_file()
    assert (result.root / "run_files" / "session.har").is_file()
