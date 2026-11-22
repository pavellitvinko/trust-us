"""Probe agent: policy, harvesting, mail attribution, and a browser run on the local fake site."""

from __future__ import annotations

import json
from email.message import EmailMessage

import pytest

from ftf.contracts import Purpose, View, ViewKind
from ftf.extract.probe_findings import ProbeFindings
from ftf.probe import Level, ProbeRefused, run_probe
from ftf.probe.fake_site import FakeSite
from ftf.probe.harvest import harvest, nip_valid
from ftf.probe.jobs import create_job, importable_run, job_state, list_jobs
from ftf.probe.mailbox import parse_message, parse_received, record_messages, watch_resend
from ftf.probe.persona import Ledger
from ftf.probe.policy import (
    FORBIDDEN_FIELDS,
    check_target,
    classify_field,
    is_bait,
    is_dangerous,
    sandbox_problems,
)

BAIT = "bait@example.test"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("cardnumber", "card"),
        ("Numer karty", "card"),
        ("cvv", "card"),
        ("iban", "bank"),
        ("PESEL", "national_id"),
        ("Telefon", "phone"),
        ("data urodzenia", "birth"),
        ("Adres e-mail", "email"),
        ("Hasło", "password"),
        ("Nazwisko", "last_name"),
    ],
)
def test_classify_field(text, kind):
    assert classify_field(text) == kind


def test_payment_and_identity_fields_are_forbidden():
    assert {"card", "bank", "national_id", "phone"} <= FORBIDDEN_FIELDS


@pytest.mark.parametrize("text", ["Wyloguj", "Wpłać teraz", "Zapłać", "/wp-login.php", "Pobierz"])
def test_dangerous_controls(text):
    assert is_dangerous(text)


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.0.0.5/", "file:///etc/passwd"])
def test_private_targets_refused(url):
    with pytest.raises(ProbeRefused):
        check_target(url, Level.EXPLORE, live=True, local=False)


def test_local_mode_is_loopback_only():
    with pytest.raises(ProbeRefused):
        check_target("https://example.com/", Level.REGISTER, live=False, local=True)


def test_register_on_real_site_needs_live(monkeypatch):
    monkeypatch.setattr("ftf.probe.policy.host_is_public", lambda host: True)
    with pytest.raises(ProbeRefused, match="--live"):
        check_target("https://example.com/", Level.REGISTER, live=False, local=False)
    check_target("https://example.com/", Level.REGISTER, live=True, local=False)


def test_harvest_validates_identifiers():
    html = """<p>PL61 1090 1014 0000 0712 1981 2874 PL00 1090 1014 0000 0712 1981 2874</p>
    <p>1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa</p>
    <p>NIP 123-456-32-18</p><script>k='pk_live_ABCDEFGHIJKLMNOP1234'</script>"""
    found = {(f["ns"], f["value"]) for f in harvest(html, "https://x.test/")}
    assert ("contact.iban", "PL61109010140000071219812874") in found
    assert ("contact.iban", "PL00109010140000071219812874") not in found
    assert ("contact.wallet", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") in found
    assert ("contact.wallet", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb") not in found
    assert ("company.nip", "1234563218") in found
    assert ("payment.public_key", "pk_live_ABCDEFGHIJKLMNOP1234") in found
    assert not nip_valid("1234563219")


def _mail(sender, body, auth=None):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = BAIT
    message["Subject"] = "Oferta"
    if auth:
        message["Authentication-Results"] = auth
    message.set_content(body)
    return message.as_bytes()


ENTRIES = [{"name": "Adela Brzozolski", "site": "first.test", "bundle": "b1"}]


def test_resale_needs_name_and_verified_sender():
    good = "mx; dkim=pass header.d=broker.test"
    assert parse_message(_mail("a@broker.test", "Witaj Adela Brzozolski", good), ENTRIES)["resold"]
    assert not parse_message(_mail("a@broker.test", "Witaj Adela Brzozolski"), ENTRIES)["resold"]
    unnamed = parse_message(_mail("a@broker.test", "Witaj", good), ENTRIES)
    assert unnamed["site"] is None and not unnamed["resold"]
    shared = "mx; dkim=pass header.d=gmail.com"
    assert not parse_message(_mail("a@gmail.com", "Adela Brzozolski", shared), ENTRIES)["resold"]


def test_record_messages_is_idempotent(tmp_path):
    (tmp_path / "probe.json").write_text(json.dumps({"schema": "ftf.probe/1", "findings": []}))
    entries = [{**ENTRIES[0], "bundle": str(tmp_path)}]
    item = parse_message(
        _mail("a@broker.test", "Adela Brzozolski", "mx; dkim=pass header.d=broker.test"), entries
    )
    assert record_messages([item]) == 1
    assert record_messages([item]) == 0
    data = json.loads((tmp_path / "probe.json").read_text())
    assert [f["value"] for f in data["findings"]] == ["broker.test"]


class _Ctx:
    def __init__(self, root):
        self.root = root

    def read_text(self, view, relative, limit=None):
        return (self.root / relative).read_text(encoding="utf-8")


def test_extractor_never_links_on_bait(tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_PROBE_EMAIL", BAIT)
    (tmp_path / "bundle").mkdir()
    findings = [
        {"signal": "A12", "ns": "contact.email", "value": BAIT},
        {"signal": "A12", "ns": "contact.iban", "value": "PL61109010140000071219812874"},
        {"signal": "I6", "ns": "payment.provider", "value": "stripe"},
    ]
    (tmp_path / "bundle" / "probe.json").write_text(
        json.dumps({"schema": "ftf.probe/1", "findings": findings})
    )
    view = View(
        view_id="v", incident_id="i", kind=ViewKind.PAGE, label="x", primary="bundle/dom.html"
    )
    rows = {o.namespace: o for o in ProbeFindings().run(view, _Ctx(tmp_path))}
    assert "contact.email" not in rows
    assert rows["contact.iban"].purpose is Purpose.LINKAGE
    assert rows["payment.provider"].purpose is Purpose.RISK


@pytest.fixture
def fake_site():
    site = FakeSite().start()
    yield site
    site.shutdown()
    site.server_close()


def _chromium_or_skip():
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception:  # noqa: BLE001
        pytest.skip("Chromium is not installed")


def test_level_two_submits_nothing(fake_site, tmp_path):
    _chromium_or_skip()
    result = run_probe(fake_site.url, tmp_path, level=2, local=True)
    assert fake_site.posts == []
    assert result.checkout_seen is False  # the checkout sits behind the login
    assert ("company.nip", "1234563218") in {(f["ns"], f["value"]) for f in result.findings}


def test_full_funnel_on_fake_site(fake_site, tmp_path):
    _chromium_or_skip()
    ledger = tmp_path / "ledger.json"
    result = run_probe(fake_site.url, tmp_path, level=4, email=BAIT, ledger_path=ledger, local=True)
    assert result.registered and result.logged_in and result.checkout_seen
    assert [path for path, _ in fake_site.posts] == ["/register", "/login"]
    registered = fake_site.posts[0][1]
    assert registered["email"] == BAIT
    assert "phone" not in registered or registered["phone"] == ""
    assert not any("cardnumber" in form or "cvv" in form for _, form in fake_site.posts)
    found = {(f["ns"], f["value"]) for f in result.findings}
    assert ("contact.iban", "PL61109010140000071219812874") in found
    assert ("contact.wallet", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") in found
    assert ("payment.merchant_id", "FAKE-MERCHANT-77") in found
    assert not any(value == BAIT for _, value in found)
    saved = (tmp_path / "probe.json").read_text(encoding="utf-8")
    assert registered["password"] not in saved
    assert registered["password"] not in ledger.read_text(encoding="utf-8")
    assert "/logout" not in " ".join(result.pages)


def test_each_site_gets_its_own_ascii_address(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    one = ledger.new_persona("", "one.test", tmp_path, 3, "bait.example.test")
    two = ledger.new_persona("", "two.test", tmp_path, 3, "bait.example.test")
    assert one.email != two.email
    assert one.email.isascii() and one.email.endswith("@bait.example.test")
    assert {e["email"] for e in ledger.entries()} == {one.email, two.email}
    assert one.password not in ledger.path.read_text(encoding="utf-8")


def test_bait_domain_is_never_evidence():
    assert is_bait("Anyone.1a2b@BAIT.example.test", "", "bait.example.test")
    assert not is_bait("sales@example.test", "", "bait.example.test")


def _received(to, sender="Oferta <a@broker.test>", dkim="pass"):
    return {
        "id": "m1",
        "to": [to],
        "from": sender,
        "subject": "Oferta",
        "created_at": "2026-11-22T10:00:00Z",
        "authentication": {"spf": "pass", "dkim": dkim, "dmarc": "pass"},
    }


def test_resend_attribution_uses_the_exact_address():
    entries = [{"name": "A B", "email": "a.b.1111@bait.test", "site": "first.test", "bundle": "b"}]
    assert parse_received(_received("a.b.1111@bait.test"), entries)["resold"]
    assert not parse_received(_received("a.b.1111@bait.test", dkim="gray"), entries)["resold"]
    assert parse_received(_received("other@bait.test"), entries)["site"] is None
    own = parse_received(_received("a.b.1111@bait.test", "x@first.test"), entries)
    assert own["site"] == "first.test" and not own["resold"]


def test_watch_resend_pages_and_opens_only_our_mail(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "probe.json").write_text(json.dumps({"schema": "ftf.probe/1", "findings": []}))
    address = "a.b.1111@bait.test"
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps([{"name": "A B", "email": address, "site": "first.test", "bundle": str(bundle)}])
    )
    calls = []

    def get(key, path):
        calls.append(path)
        if path == "/emails/receiving/m1":
            return _received(address)
        if "after=x9" in path:
            return {"has_more": False, "data": [{"id": "m1", "to": [address]}]}
        return {"has_more": True, "data": [{"id": "x9", "to": ["stranger@bait.test"]}]}

    report = watch_resend("key", ledger, get=get, pause=0)
    assert (report["messages"], report["attributed"], report["new"]) == (2, 1, 1)
    assert len(report["resold"]) == 1
    assert "/emails/receiving/x9" not in calls
    data = json.loads((bundle / "probe.json").read_text())
    assert [f["value"] for f in data["findings"]] == ["broker.test"]


def test_sandbox_check(tmp_path):
    good = {"FTF_PROBE_SANDBOX": "1", "FTF_PROBE_MAIL_DOMAIN": "bait.test"}
    assert sandbox_problems(tmp_path, good, tmp_path) == []
    assert sandbox_problems(tmp_path, {}, tmp_path)
    assert sandbox_problems(tmp_path, {**good, "FTF_RESEND_API_KEY": "x"}, tmp_path)
    (tmp_path / ".env").write_text("A=1")
    assert sandbox_problems(tmp_path, good, tmp_path) == [".env is visible"]


def test_real_site_refused_outside_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr("ftf.probe.policy.host_is_public", lambda host: True)
    monkeypatch.delenv("FTF_PROBE_SANDBOX", raising=False)
    with pytest.raises(ProbeRefused, match="sandbox"):
        run_probe("https://example.com/", tmp_path, level=1)


def test_ledger_survives_a_different_mount(tmp_path):
    first = tmp_path / "one" / "probe" / "ledger.json"
    bundle = tmp_path / "one" / "runs" / "a" / "bundle"
    bundle.mkdir(parents=True)
    Ledger(first).new_persona("", "site.test", bundle, 3, "bait.test")
    moved = tmp_path / "two"
    (tmp_path / "one").rename(moved)
    entry = Ledger(moved / "probe" / "ledger.json").resolved()[0]
    assert entry["bundle"] == str((moved / "runs" / "a" / "bundle").resolve())


def test_job_rules(tmp_path):
    with pytest.raises(ProbeRefused):
        create_job(tmp_path, "file:///etc/passwd", 1)
    with pytest.raises(ProbeRefused, match="confirm"):
        create_job(tmp_path, "https://example.com", 3)
    job_id = create_job(tmp_path, "example.com", 2)
    assert job_state(tmp_path, job_id)["url"] == "https://example.com"
    assert job_state(tmp_path, job_id)["state"] == "queued"
    assert job_state(tmp_path, "../../secret") == {}
    assert [j["id"] for j in list_jobs(tmp_path)] == [job_id]


def test_sandbox_cannot_rewrite_a_job_through_status(tmp_path):
    job_id = create_job(tmp_path, "https://example.com", 1)
    status = tmp_path / "status" / f"{job_id}.json"
    status.parent.mkdir()
    status.write_text(json.dumps({"state": "done", "url": "https://evil.test", "level": 4}))
    state = job_state(tmp_path, job_id)
    assert state["url"] == "https://example.com" and state["level"] == 1


def test_import_refuses_links_from_the_sandbox(tmp_path):
    job_id = "a" * 32
    run = tmp_path / "runs" / job_id / "bundle"
    run.mkdir(parents=True)
    (run.parent / "run.json").write_text("{}")
    assert importable_run(tmp_path, job_id) == run.parent
    (run / "dom.html").symlink_to("/etc/passwd")
    with pytest.raises(ProbeRefused, match="unsafe"):
        importable_run(tmp_path, job_id)


def _probe_screen():
    from ftf.config import get_settings
    from ftf.probe.ui import probe_page
    from ftf.store import Store

    settings = get_settings()
    probe_page(settings, lambda: Store(settings.db_path), lambda: None)


def test_probe_screen_queues_a_job(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    data = tmp_path / "data"
    (data / "probe-sandbox").mkdir(parents=True)
    monkeypatch.setenv("FTF_DATA_DIR", str(data))
    monkeypatch.delenv("FTF_PROBE_SANDBOX", raising=False)
    (data / "probe-sandbox" / "worker.alive").touch()
    app = AppTest.from_function(_probe_screen, default_timeout=30).run()
    assert not app.exception
    next(b for b in app.button if b.label == "Run agent").click().run()
    assert not app.exception
    jobs = list_jobs(data / "probe-sandbox")
    assert len(jobs) == 1 and jobs[0]["fake"] is True and jobs[0]["level"] == 2


def test_app_lists_the_probe_page(tmp_path, monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path))
    script = Path(__file__).resolve().parents[1] / "app/streamlit_app.py"
    app = AppTest.from_file(str(script), default_timeout=30).run()
    assert not app.exception
    assert 'title="Probe agent"' in script.read_text(encoding="utf-8")
