"""mail.tm inboxes for victim runs, against a fake mail.tm: nothing here touches the network."""

import json

import pytest

from ftf import pipeline
from ftf.config import Settings
from ftf.ingest import ingest_folder
from ftf.victim.contacts import ContactBook, contacts_for, load_contacts
from ftf.victim.mailbox import Mailbox, MailboxError, MailTm, fetch_mail, summarise
from ftf.victim.persona import get_persona

PERSONA = get_persona("retiree-income")


class FakeMailTm:
    """Answers like api.mail.tm: domains, accounts, token, messages."""

    def __init__(self, messages=(), taken=False, domains=("mailbox.example",)):
        self.calls = []
        self.accounts = {}
        self.inbox = list(messages)
        self.taken = taken
        self.domains = domains

    def __call__(self, method, url, headers, body):
        path = url.split("api.mail.tm", 1)[1]
        data = json.loads(body) if body else None
        self.calls.append((method, path, headers.get("Authorization", "")))
        if path == "/domains":
            return 200, json.dumps(
                [{"domain": d, "isActive": True, "isPrivate": False} for d in self.domains]
            ).encode()
        if path == "/accounts":
            if self.taken:
                return 422, b'{"detail":"address: This value is already used."}'
            self.accounts[data["address"]] = data["password"]
            return 201, json.dumps({"id": "acc1", "address": data["address"]}).encode()
        if path == "/token":
            if self.accounts.get(data["address"]) != data["password"]:
                return 401, b'{"message":"Invalid credentials."}'
            return 200, b'{"id":"acc1","token":"tok"}'
        assert headers.get("Authorization") == "Bearer tok"
        if path.startswith("/messages?page="):
            return 200, json.dumps([{"id": m["id"]} for m in self.inbox]).encode()
        if path.startswith("/messages/"):
            mid = path.rsplit("/", 1)[1]
            return 200, json.dumps(next(m for m in self.inbox if m["id"] == mid)).encode()
        return 404, b"{}"


MESSAGE = {
    "id": "m1",
    "createdAt": "2026-11-22T10:00:00+00:00",
    "from": {"address": "opiekun@crm.invest-leads.com", "name": "Twój opiekun"},
    "subject": "Aktywuj konto - wpłać 950 zł",
    "text": "Dzień dobry! Zadzwonimy z numeru +48 221 234 567. Zaloguj się: https://panel.trade-pro.io/login?u=1",
    "html": ['<a href="https://track.mailer-x.net/c/abc">Kliknij</a>'],
}


def test_create_and_read_an_inbox():
    fake = FakeMailTm([MESSAGE])
    client = MailTm(transport=fake)
    box = client.create("retiree-income.r1")
    assert box.address == "retiree-income.r1@mailbox.example" and box.account_id == "acc1"
    assert len(box.password) >= 20 and fake.accounts[box.address] == box.password
    messages = client.messages(box)
    assert [m["id"] for m in messages] == ["m1"]
    with pytest.raises(MailboxError, match="does not offer"):
        client.create("x", "other.example")


def test_a_taken_or_refused_address_is_an_error():
    with pytest.raises(MailboxError, match="HTTP 422"):
        MailTm(transport=FakeMailTm(taken=True)).create("retiree-income.r1")
    with pytest.raises(MailboxError, match="no active domain"):
        MailTm(transport=FakeMailTm(domains=())).create("x")


def test_mail_tm_addresses_are_short_enough_and_long_patterns_are_refused():
    from pydantic import ValidationError

    book = load_contacts()
    assert (
        book.live.email.local_part("retiree-income", "20261122-004020-93cd") == "ri26112200402093cd"
    )
    assert (
        book.live.email.local_part("young-gambler", "20261122-004020-93cd") == "yg26112200402093cd"
    )
    with pytest.raises(ValidationError, match="short address"):
        ContactBook.model_validate(
            {"version": "x", "local": {}, "live": {"email": {"provider": "mail.tm"}}}
        )


def test_summary_finds_sender_links_and_phones():
    line = summarise(MESSAGE, "retiree-income.r1@mailbox.example")
    assert line["sender_domain"] == "crm.invest-leads.com"
    assert line["link_hosts"] == ["panel.trade-pro.io", "track.mailer-x.net"]
    assert line["phones"] == ["221234567"]


def test_packaged_live_scope_uses_mail_tm_and_local_stays_reserved():
    book = load_contacts()
    live = contacts_for(book, PERSONA, "r1", local=False)
    assert live.provider == "mail.tm" and live.email is None
    assert "created when the run starts" in live.email_policy
    local = contacts_for(book, PERSONA, "r1", local=True)
    assert local.provider == "static" and local.email.endswith("@victim.test")


def _book_with_mail_tm(local_too=False):
    rule = {"provider": "mail.tm", "pattern": "{initials}{run_compact}@{domain}"}
    return ContactBook.model_validate(
        {"version": "t", "local": {"email": rule} if local_too else {}, "live": {"email": rule}}
    )


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "d",
        openai_api_key="",
        gemini_api_key="",
        openai_model="m",
        embedding_model="e",
        video_model="v",
    )


def test_the_run_creates_its_inbox_before_the_browser_opens(tmp_path, settings):
    from ftf.victim.agent import VictimRun

    fake = FakeMailTm()
    run = VictimRun(
        "https://glowpixel.org/?x=1",
        PERSONA,
        tmp_path,
        settings=settings,
        contacts=_book_with_mail_tm(),
        mail_client=MailTm(transport=fake),
    )
    run.provision_mailbox()
    assert run.email == f"ri{run.run_id.replace('-', '')[2:]}@mailbox.example"
    assert len(run.email.split("@")[0]) <= 21
    saved = Mailbox.load(run.rec.files)
    assert saved.address == run.email and saved.password == fake.accounts[run.email]
    assert run.contacts.email == run.email and "mail.tm inbox" in run.contacts.email_policy


def test_no_inbox_means_no_email_and_the_reason_is_kept(tmp_path, settings):
    from ftf.victim.agent import VictimRun

    run = VictimRun(
        "https://glowpixel.org/?x=1",
        PERSONA,
        tmp_path,
        settings=settings,
        contacts=_book_with_mail_tm(),
        mail_client=MailTm(transport=FakeMailTm(taken=True)),
    )
    run.provision_mailbox()
    assert run.email is None and "could not be created" in run.contacts.email_policy
    assert not (run.rec.files / "mailbox.json").exists()


def test_mail_that_arrives_becomes_observations_on_the_stage_that_gave_the_address(tmp_path, store):
    root = tmp_path / "victim-m1"
    files = root / "run_files"
    stage = root / "stage-01"
    stage.mkdir(parents=True)
    files.mkdir()
    (stage / "dom.html").write_text("<html><body><form><input name=email></form></body></html>")
    (stage / "manifest.json").write_text(
        json.dumps(
            {
                "page_url": "https://lp.example/",
                "victim": {
                    "run_id": "m1",
                    "stage": 1,
                    "stage_kind": "form",
                    "stage_kinds_seen": ["form"],
                    "arrived_from": {"how": "start"},
                },
            }
        )
    )
    fake = FakeMailTm([MESSAGE])
    client = MailTm(transport=fake)
    box = client.create("retiree-income.m1")
    box.save(files)
    (files / "forms.jsonl").write_text(
        json.dumps({"stage": 1, "values_given": {"email": box.address}, "sent": True}) + "\n"
    )
    (files / "run.json").write_text(
        json.dumps(
            {"run_id": "m1", "persona": {"id": "retiree-income", "version": "1"}, "stages": []}
        )
    )
    assert [m["id"] for m in fetch_mail(root, client)] == ["m1"]
    assert fetch_mail(root, client) == []  # already recorded
    assert (files / "mail" / "m1.json").is_file()

    _, views = ingest_folder(root, store)
    pipeline.run_extract(store)
    found = {
        (o.namespace, str(o.value))
        for o in store.observations(views[0].view_id)
        if o.extractor == "victim.run"
    }
    assert ("mail.sender_domain", "invest-leads.com") in found
    assert ("mail.link_domain", "trade-pro.io") in found and (
        "mail.link_domain",
        "mailer-x.net",
    ) in found
    assert ("mail.phone", "221234567") in found
