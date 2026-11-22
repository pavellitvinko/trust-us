"""Victim visitor: rules, contacts, personas, perception and the pipeline plugins.

No browser and no network here; `test_victim_browser.py` drives a real (test-only headless)
browser over local fixture pages.
"""

import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ftf import pipeline
from ftf.config import Settings
from ftf.ingest import ingest_folder
from ftf.registry import comparators, layers, load_plugins
from ftf.victim import rules
from ftf.victim.contacts import classify_field, plan_form, safe_free_text
from ftf.victim.decide import History, RuleDecider
from ftf.victim.observe import build_state
from ftf.victim.perceive import Perception, perceive_run, verify
from ftf.victim.persona import answer_for, get_persona, load_personas, suggest_persona
from ftf.victim.record import stage_key
from ftf.victim.vocab import LEVERS

PERSONA = get_persona("retiree-income")


def field(name="", type_="text", label="", required=False, **extra):
    return {
        "tag": "input",
        "type": type_,
        "name": name,
        "id": "",
        "label": label,
        "autocomplete": "",
        "required": required,
        "pattern": "",
        "visible": True,
        "xpath": f"//input[@name='{name}']",
        "options": [],
        "accept": "",
        "capture": False,
        **extra,
    }


def state(
    text="",
    candidates=(),
    forms=(),
    fields=(),
    frames=(),
    chat=(),
    url="http://shop.localhost/a",
    **flags,
):
    raw = {
        "url": url,
        "title": "t",
        "text": text,
        "candidates": list(candidates),
        "forms": list(forms),
        "chat": list(chat),
        "flags": {"password": 0, "file": 0, "fields": list(fields), "captcha": [], **flags},
        "frames": list(frames),
        "scroll": {},
    }
    return build_state(raw, [])


def link(href, text="Dalej", **extra):
    return {
        "kind": "link",
        "text": text,
        "href": href,
        "xpath": "//a",
        "in_view": True,
        "download": False,
        "marks": "",
        "button_like": False,
        **extra,
    }


def button(text, kind="button", **extra):
    return {
        "kind": kind,
        "text": text,
        "xpath": "//button",
        "in_view": True,
        "marks": "",
        "form_index": None,
        "type": "button",
        **extra,
    }


def lead_form(*fields_, submit="Wyślij"):
    return {
        "index": 0,
        "xpath": "//form",
        "action": "http://shop.localhost/submit",
        "method": "post",
        "visible": True,
        "in_view": True,
        "fields": list(fields_),
        "hidden": [],
        "submit_text": submit,
        "submit_xpath": "//form//button",
    }


def verdicts(s):
    return {c.text: rules.candidate_verdict(c, s) for c in s.candidates}


# -- personas ------------------------------------------------------------------


def test_library_has_the_four_personas_with_versions():
    personas = load_personas()
    assert sorted(personas) == ["retiree-income", "small-saver", "weight-loss", "young-gambler"]
    assert all(p.version and p.ref == f"{p.id}@{p.version}" for p in personas.values())
    assert "prostate-worry" not in personas


def test_answers_are_taken_from_the_persona():
    index, topic = answer_for(PERSONA, "Ile masz lat?", ["18–24", "25–40", "40–55", "55+"])
    assert (index, topic) == (3, "age")
    index, topic = answer_for(
        PERSONA, "Jaki jest Twój status zawodowy?", ["Zatrudniony", "Emerytowany / Emerytowana"]
    )
    assert index == 1
    assert answer_for(PERSONA, "Ulubiony kolor?", ["Niebieski", "Zielony"]) == (None, "")


def test_persona_suggestion_says_why():
    pid, why = suggest_persona("Schudnij 10 kg w miesiąc dzięki kapsułkom")
    assert pid == "weight-loss" and "schudn" in why
    pid, why = suggest_persona("")
    assert pid == "retiree-income" and "default" in why


# -- contacts (VV-30…VV-32) ----------------------------------------------------


def test_field_classification():
    assert classify_field(field("first_name", label="Imię")) == "first_name"
    assert classify_field(field("email")) == "email"
    assert classify_field(field("x", type_="tel")) == "phone"
    assert classify_field(field("cardnumber", autocomplete="cc-number")) == "forbidden"
    assert classify_field(field("iban", label="Numer konta")) == "forbidden"
    assert classify_field(field("pass", type_="password")) == "forbidden"


def test_form_needing_email_is_filled_but_not_sent_without_domain():
    form = lead_form(
        field("first_name", required=True), field("email", type_="email", required=True)
    )
    plan = plan_form(form, PERSONA, None)
    assert not plan.submit and plan.stop_reason == "email_domain_unset"
    assert plan.values == {"//input[@name='first_name']": "Halina"}


def test_optional_phone_stays_empty_and_required_phone_stops_the_form():
    email = "retiree-income.x@victim.test"
    optional = lead_form(field("email", type_="email", required=True), field("phone", type_="tel"))
    plan = plan_form(optional, PERSONA, email)
    assert plan.submit and "//input[@name='phone']" not in plan.values
    required = lead_form(
        field("email", type_="email", required=True), field("phone", type_="tel", required=True)
    )
    plan = plan_form(required, PERSONA, email)
    assert not plan.submit and plan.stop_reason == "phone_required"
    assert {r["reason"] for r in plan.refusals} == {"phone_required"}


def test_free_text_never_carries_numbers_or_other_emails():
    assert safe_free_text("Tak, chętnie", None) == "Tak, chętnie"
    assert safe_free_text("mój numer 600 123 456", None) is None
    assert safe_free_text("pisz na jan@example.com", None) is None
    assert safe_free_text("a@victim.test", "a@victim.test") == "a@victim.test"


# -- action rules (VV-20…VV-24) -----------------------------------------------


def test_payment_fields_and_payment_frames_stop_the_run():
    s = state(fields=[field("cardnumber", autocomplete="cc-number", required=True)])
    assert rules.page_block(s).reason == rules.FORBIDDEN_PAYMENT
    s = state(frames=["https://js.stripe.com/v3/elements-inner-card.html"])
    assert rules.page_block(s).reason == rules.FORBIDDEN_PAYMENT
    s = state(fields=[field("blik", label="Kod BLIK")])
    assert rules.page_block(s).reason == rules.FORBIDDEN_PAYMENT


def test_uploads_stop_the_run():
    assert (
        rules.page_block(state(fields=[field("id", type_="file")])).reason == rules.FORBIDDEN_UPLOAD
    )
    assert (
        rules.page_block(state(text="Prześlij zdjęcie dowodu, aby odebrać")).reason
        == rules.FORBIDDEN_UPLOAD
    )


def test_downloads_and_installs_are_never_offered():
    s = state(
        candidates=[
            link("http://shop.localhost/app.apk", "Pobierz"),
            link("http://shop.localhost/x", "Bonus", download=True),
            link("https://play.google.com/store/apps/details?id=x", "Google Play"),
            button("Zainstaluj aplikację"),
        ]
    )
    assert all(ok is False and why == rules.FORBIDDEN_DOWNLOAD for ok, why in verdicts(s).values())
    _, refused = rules.split_candidates(s)
    assert rules.only_way_on_is_forbidden(refused).reason == rules.FORBIDDEN_DOWNLOAD


def test_real_account_login_is_never_offered_and_password_is_an_obstacle():
    s = state(
        candidates=[
            button("Kontynuuj z Facebookiem"),
            link("https://accounts.google.com/o/oauth2", "Google"),
        ],
        password=1,
    )
    assert {why for _, why in verdicts(s).values()} == {rules.FORBIDDEN_LOGIN}
    assert rules.obstacle(s) == "login"
    assert rules.obstacle(state(captcha=["g-recaptcha"])) == "captcha"


def test_payment_buttons_are_refused_but_quiz_answers_about_cards_are_not():
    s = state(
        candidates=[button("Wpłać teraz"), button("Polska karta bankowa", kind="chat_option")]
    )
    v = verdicts(s)
    assert v["Wpłać teraz"] == (False, rules.FORBIDDEN_PAYMENT)
    assert v["Polska karta bankowa"][0] is True


def test_only_the_funnel_is_followed():
    s = state(
        candidates=[
            link("https://www.facebook.com/sharer/sharer.php?u=x", "Udostępnij"),
            link("https://pl.wikipedia.org/wiki/KNF", "KNF"),
            link("http://shop.localhost/polityka-prywatnosci", "Polityka"),
            link("http://shop.localhost/quiz?x=1", "Sprawdź"),
            link("https://glowpixel.org/?x=1", "Zarejestruj"),
        ]
    )
    v = verdicts(s)
    assert v["Sprawdź"] == (True, "funnel link")
    assert not v["Udostępnij"][0] and not v["KNF"][0] and not v["Polityka"][0]
    assert v["Zarejestruj"] == (False, "replay runs stay on the local replay")


def test_comment_boxes_are_not_lead_forms():
    comment = {**lead_form(field("comment", label="Napisz komentarz")), "index": 0}
    s = state(forms=[comment])
    assert [c.kind for c in s.candidates] == ["form"]
    assert rules.candidate_verdict(s.candidates[0], s) == (
        False,
        "not a lead form (comment or search box)",
    )


def test_handoff_detection():
    assert rules.handoff(state(text="Nasz konsultant zadzwoni do Ciebie w ciągu 24 godzin."))
    assert rules.handoff(state(text="Zarejestruj się teraz")) is None


def test_stage_key_keeps_the_query_and_drops_click_ids():
    a = "https://glowpixel.org/?utm_id=A&fbclid=1"
    b = "https://glowpixel.org/?utm_id=B"
    assert stage_key(a) != stage_key(b)
    assert stage_key(a) == stage_key("https://glowpixel.org/?fbclid=2&utm_id=A#top")


# -- decider -------------------------------------------------------------------


def test_rule_decider_answers_the_bot_and_never_changes_its_answer():
    s = state(
        candidates=[button("Mężczyzna", kind="chat_option"), button("Kobieta", kind="chat_option")],
        chat=[{"who": "bot", "text": "Jaka jest Twoja płeć?", "xpath": "//div"}],
    )
    allowed, _ = rules.split_candidates(s)
    by_n = {c.n: c for c in allowed}
    history = History()
    d = RuleDecider().choose(PERSONA, s, allowed, history)
    assert by_n[d.n].text == "Kobieta" and d.topic == "sex"
    history.asked[(s.url, d.question)] = "Kobieta"
    for _ in range(3):  # the bot is slow: the same answer again, never the other one
        again = RuleDecider().choose(PERSONA, s, allowed, history)
        assert by_n[again.n].text == "Kobieta"
        history.record(s, by_n[again.n], changed=False, note="no change")
    assert RuleDecider().choose(PERSONA, s, allowed, history) is None


def test_dead_links_are_not_offered_again():
    s = state(
        candidates=[
            link("http://shop.localhost/b", "Sprawdź"),
            link("http://shop.localhost/c", "Dalej"),
        ]
    )
    allowed, _ = rules.split_candidates(s)
    history = History()
    first = RuleDecider().choose(PERSONA, s, allowed, history)
    history.record(s, next(c for c in allowed if c.n == first.n), changed=False, note="no change")
    second = RuleDecider().choose(PERSONA, s, allowed, history)
    assert second.n != first.n


# -- perception: quotes verified against the material -----------------------------


def _section(items, reason=None):
    return {"items": items, "empty_reason": reason}


def reading(**over):
    body = {
        "asked_action": _section(
            [{"action": "leave_contact", "detail": "form", "quote": "Zostaw dane"}]
        ),
        "asked_data": _section([{"data": "email", "stage": 2, "quote": "nie ma tego w tekście"}]),
        "money_path": _section(
            [
                {
                    "kind": "entry_amount",
                    "amount": "1 150",
                    "currency": "zł",
                    "time_frame": None,
                    "quote": "Wpłać tylko 1 150 zł",
                },
                {
                    "kind": "promised_return",
                    "amount": "9 999",
                    "currency": "zł",
                    "time_frame": None,
                    "quote": "zarobisz nawet 1 800 zł",
                },
            ]
        ),
        "promise": _section(
            [{"text": "1800 zł in days", "quote": "w ciągu kilku dni zarobisz nawet 1 800 zł"}]
        ),
        "belief": _section([], "nothing about beliefs"),
        "borrowed_authority": _section([]),
        "levers": _section([{"lever": "greed_easy_money", "quote": "zarobisz nawet"}]),
        "doubt_handling": _section([]),
        "tailoring": _section([]),
        "next_step_promised": _section(
            [{"text": "an adviser will call", "quote": "doradca się odezwie"}]
        ),
        "verdict": {"answer": "convinced", "reason": "easy money", "quote": "Wpłać tylko"},
        "pitch_retelling": "Pay 1150 zł to [SITE] and get 1800 zł.",
    }
    return Perception.model_validate({**body, **over})


MATERIAL = "=== Chat ===\nBot: Wpłać tylko 1 150 zł, a w ciągu kilku dni zarobisz nawet 1 800 zł.\nBot: Zostaw dane, a doradca się odezwie.\n"


def test_quotes_not_in_the_material_are_dropped_and_empty_fields_say_why():
    out = verify(
        reading(),
        MATERIAL,
        [
            {
                "part": "chat",
                "file": "run_files/chat.jsonl",
                "label": "Chat",
                "start": 0,
                "end": len(MATERIAL),
            }
        ],
    )
    assert [i["action"] for i in out["asked_action"]["items"]] == ["leave_contact"]
    assert out["asked_data"]["items"] == [] and "quote" in out["asked_data"]["empty_reason"]
    assert [i["value"] for i in out["money_path"]["items"]] == ["1150"]  # 9 999 is not in its quote
    assert out["belief"]["empty_reason"] == "nothing about beliefs"
    assert out["borrowed_authority"]["empty_reason"]
    assert {d["why"] for d in out["dropped"]} == {
        "quote not found in the material",
        "amount not in its quote",
    }
    for field_ in ("asked_action", "money_path", "promise", "levers", "verdict"):
        for item in out[field_]["items"]:
            assert item["evidence"]["quote_found"].casefold() in MATERIAL.casefold()
    assert (
        "[AMOUNT]" in out["pitch_retelling"]["text"]
        and "1150" not in out["pitch_retelling"]["text"]
    )


def test_retelling_needs_a_verified_belief_or_promise():
    out = verify(reading(promise=_section([{"text": "x", "quote": "nie ma"}])), MATERIAL, [])
    assert out["pitch_retelling"]["text"] == "" and out["pitch_retelling"]["empty_reason"]


# -- a recorded run through the normal pipeline ---------------------------------


def message(body):
    part = NS(type="output_text", text=json.dumps(body))
    return NS(status="completed", output=[NS(type="message", phase=None, content=[part])])


class FakeOpenAI:
    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests = []
        self.responses = NS(stream=self._stream)

    @contextmanager
    def _stream(self, **request):
        self.requests.append(request)
        yield NS(get_final_response=lambda: self.queue.pop(0))


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        openai_api_key="",
        gemini_api_key="",
        openai_model="openai-test",
        embedding_model="embed-test",
        video_model="v",
    )


def write_run(root: Path, run_id="r1"):
    """A two-stage run as the recorder writes it: article -> chat with a form."""
    files = root / "run_files"
    files.mkdir(parents=True)
    s1, s2 = root / "stage-01", root / "stage-02"
    for d in (s1, s2):
        d.mkdir()
    (s1 / "dom.html").write_text(
        "<html><body><h1>Sensacja</h1><p>Emeryci zarabiają.</p><a href='/chat'>Dalej</a></body></html>"
    )
    (s2 / "dom.html").write_text(
        "<html><body><p>Wpłać tylko 1 150 zł, a w ciągu kilku dni zarobisz nawet 1 800 zł.</p><p>Zostaw dane, a doradca się odezwie.</p></body></html>"
    )
    arrived = {
        "how": "link_click",
        "step": 3,
        "action": "[6] link: Dalej",
        "from_stage": 1,
        "from_url": "http://glowpixel.localhost/?a",
        "to_url": "http://glowpixel.localhost/?chat",
        "http_redirects": ["http://redirector.localhost/?cd=1"],
        "request_method": "GET",
        "referer_sent": "http://glowpixel.localhost/?a",
        "navigation_line": 2,
    }
    for d, index, kind, frm, url in (
        (s1, 1, "advertorial", {"how": "start"}, "http://glowpixel.localhost/?a"),
        (s2, 2, "chat", arrived, "http://glowpixel.localhost/?chat"),
    ):
        (d / "manifest.json").write_text(
            json.dumps(
                {
                    "page_url": url,
                    "victim": {
                        "run_id": run_id,
                        "stage": index,
                        "stage_kind": kind,
                        "stage_kinds_seen": [kind],
                        "arrived_from": frm,
                    },
                }
            )
        )
        (d / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (files / "steps").mkdir()
    (files / "steps" / "step-001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (files / "session.har").write_text(json.dumps({"log": {"entries": []}}))
    (files / "navigation.jsonl").write_text(
        json.dumps({"stage": 1, "how": "start"}) + "\n" + json.dumps({"stage": 2, **arrived}) + "\n"
    )
    (files / "chat.jsonl").write_text(
        json.dumps({"stage": 2, "who": "bot", "text": "Wpłać tylko 1 150 zł"}) + "\n"
    )
    form = {
        "stage": 2,
        "step": 5,
        "fields": [
            {"kind": "first_name", "name": "first_name", "label": "Imię", "required": True},
            {"kind": "email", "name": "email", "label": "E-mail", "required": True},
            {"kind": "phone", "name": "phone", "label": "Telefon", "required": True},
        ],
        "sent": True,
        "sent_to": [{"method": "POST", "url": "https://crm.leadsbox.com/v1/landing/form-event"}],
    }
    (files / "forms.jsonl").write_text(json.dumps(form) + "\n")
    run = {
        "run_id": run_id,
        "persona": {"id": "retiree-income", "version": "1"},
        "funnel_shape": ["advertorial", "chat", "form"],
        "stages": [
            {
                "index": 1,
                "dir": "stage-01",
                "url": "http://glowpixel.localhost/?a",
                "kind": "advertorial",
                "kinds": ["advertorial"],
            },
            {
                "index": 2,
                "dir": "stage-02",
                "url": "http://glowpixel.localhost/?chat",
                "kind": "chat",
                "kinds": ["chat", "form"],
            },
        ],
    }
    (files / "run.json").write_text(json.dumps(run))
    return root


def test_run_ingests_as_stage_views_with_funnel_edges_and_perception(store, tmp_path, settings):
    root = write_run(tmp_path / "victim-r1")
    client = FakeOpenAI(message(json.loads(reading().model_dump_json())))
    p = perceive_run(root, PERSONA, settings=settings, client=client, images=False)
    assert p["state"] == "present"
    material = (root / "run_files" / "material.txt").read_text()
    for section in p.values():
        if isinstance(section, dict) and "items" in section:
            for item in section["items"]:
                assert item["evidence"]["quote_found"].casefold() in material.casefold()
    assert p["pitch_embedding"]["reason"] == "no_key"  # no Gemini key in tests: not observable

    incident, views = ingest_folder(root, store)
    assert [v.primary for v in views] == ["stage-01/dom.html", "stage-02/dom.html"]
    assert {
        a.role for a in store.artifacts(incident.incident_id) if a.path.startswith("run_files/")
    } == {"resource"}
    pipeline.run_extract(store)
    pipeline.run_fingerprint(store)
    first, second = views
    obs = {
        o.namespace: o for o in store.observations(second.view_id) if o.extractor == "victim.run"
    }
    edge = obs["funnel.edge"]
    assert edge.value["from_view"] == first.view_id and edge.value["how"] == "link_click"
    assert json.loads(edge.raw)["http_redirects"] == ["http://redirector.localhost/?cd=1"]
    assert edge.source.locator == "run_files/navigation.jsonl:line[2]"
    assert obs["redirect.hop_host"].value == "redirector.localhost"
    later = {
        (o.namespace, str(o.value))
        for o in store.observations(second.view_id)
        if o.extractor == "victim.run"
    }
    assert ("form_endpoint_host", "crm.leadsbox.com") in later
    assert ("form_endpoint_path", "/v1/landing/form-event") in later
    assert ("outbound.domain", "leadsbox.com") in later

    fps = {f.layer_id: f for f in store.fingerprints(view_id=first.view_id)}
    assert {"victim.ask", "victim.money", "victim.levers", "funnel.shape"} <= set(fps)
    assert "victim.pitch" not in fps  # no embedding without a key
    ask = {i["value"] for i in fps["victim.ask"].payload}
    assert {"action:leave_contact", "data:first_name", "data:email", "data:phone"} <= ask
    assert fps["funnel.shape"].payload["values"] == ["advertorial", "chat", "form"]
    load_plugins()
    for layer_id in ("victim.ask", "victim.money", "victim.pitch", "victim.levers", "funnel.shape"):
        assert layers.get(layer_id).tier.value == "T4"
        assert layers.get(layer_id).comparator in comparators.names()


def test_perception_failure_is_recorded_not_guessed(tmp_path, settings):
    root = write_run(tmp_path / "victim-r2", "r2")
    p = perceive_run(root, PERSONA, settings=settings, images=False)  # no key, no client
    assert p == {**p, "state": "not_observable", "reason": "no_key"}
    assert "asked_action" not in p


def test_layer_comparators():
    load_plugins()
    from ftf.contracts import Fingerprint

    def fp(layer, payload, kind="set"):
        return Fingerprint(
            view_id=f"v{id(payload)}", layer_id=layer, kind=kind, payload=payload, version="1"
        )

    shape = comparators.get("victim.sequence_edit")
    a = fp(
        "funnel.shape",
        {"algo": "stage-sequence", "values": ["advertorial", "chat", "form"]},
        "hash",
    )
    b = fp("funnel.shape", {"algo": "stage-sequence", "values": ["advertorial", "form"]}, "hash")
    assert shape.compare(a, a).score == 1.0 and round(shape.compare(a, b).score, 3) == 0.667
    cos = comparators.get("victim.cosine")
    vec = [1.0 if i < 2 else 0.0 for i in range(len(LEVERS))]
    la = fp(
        "victim.levers",
        {"model": "victim.levers.v1", "labels": list(LEVERS), "values": vec},
        "vector",
    )
    assert cos.compare(la, la).score == pytest.approx(1.0)
    assert [s["value"] for s in cos.compare(la, la).shared] == list(LEVERS[:2])
    money = comparators.get("victim.money_close")
    ma = fp(
        "victim.money", {"model": "victim.money.v1", "values": [3.06, 3.26, 1.19, 0.7]}, "vector"
    )
    mb = fp("victim.money", {"model": "victim.money.v1", "values": [3.06, 0.0, 0.0, 0.0]}, "vector")
    assert money.compare(ma, mb).score == 1.0 and money.compare(ma, mb).comparable


def test_the_question_is_the_latest_bot_question():
    from ftf.victim.decide import last_question

    s = state(
        chat=[
            {"who": "bot", "text": "Cześć! Brzmi interesująco, prawda?", "xpath": ""},
            {"who": "bot", "text": "Jaka jest Twoja płeć?", "xpath": ""},
            {"who": "bot", "text": "Wybierz jedną odpowiedź.", "xpath": ""},
        ]
    )
    assert last_question(s) == "Jaka jest Twoja płeć?"


def test_model_decider_picks_from_the_allowed_list_or_falls_back(settings):
    from ftf.victim.decide import ModelDecider

    s = state(
        candidates=[
            button("Mężczyzna", kind="chat_option"),
            button("Kobieta", kind="chat_option"),
            link("http://shop.localhost/app.apk", "Pobierz"),
        ],
        chat=[{"who": "bot", "text": "Jaka jest Twoja płeć?", "xpath": ""}],
    )
    allowed, _ = rules.split_candidates(s)
    assert [c.text for c in allowed] == ["Mężczyzna", "Kobieta"]  # the download is never offered
    client = FakeOpenAI(message({"choice": 2, "text": None, "reason": "I am a woman."}))
    d = ModelDecider(settings, client).choose(PERSONA, s, allowed, History())
    assert (d.n, d.by, d.reason, d.topic) == (2, "model", "I am a woman.", "sex")
    assert "[3]" not in client.requests[0]["input"][0]["content"]  # refused actions not shown
    bad = FakeOpenAI(message({"choice": 3, "text": None, "reason": "download it"}))
    fresh = replace(settings, data_dir=settings.data_dir / "other")  # same input is cached
    d = ModelDecider(fresh, bad).choose(PERSONA, s, allowed, History())
    assert d.by.startswith("rules (model:") and d.n == 2


def test_packaged_contacts_file_gives_local_contacts_and_nothing_live():
    from ftf.victim.contacts import contacts_for, load_contacts

    book = load_contacts()
    local = contacts_for(book, PERSONA, "20261122-1000-ab12", local=True)
    assert local.email == "retiree-income.20261122-1000-ab12@victim.test"
    assert local.phone == "+48 500 000 101" and local.scope == "local"
    live = contacts_for(book, PERSONA, "20261122-1000-ab12", local=False)
    assert live.email is None and live.phone is None
    assert "not sent" in live.email_policy and "stops the form" in live.phone_policy
    assert set(book.local.phones) == set(load_personas())  # one number per persona


def test_live_numbers_need_a_confirmed_range():
    from ftf.victim.contacts import ContactBook, contacts_for

    def make(confirmed):
        return ContactBook.model_validate(
            {
                "version": "9",
                "local": {},
                "live": {
                    "email": {"domain": "team-mail.example"},
                    "phones": {"retiree-income": "+48 700 000 001"},
                    "phones_confirmed_by": confirmed,
                },
            }
        )

    unconfirmed = contacts_for(make(""), PERSONA, "r1", local=False)
    assert unconfirmed.phone is None and "nobody confirmed" in unconfirmed.phone_policy
    assert unconfirmed.email == "retiree-income.r1@team-mail.example"
    confirmed = contacts_for(
        make("UKE plan, checked by A. on 2026-11-22"), PERSONA, "r1", local=False
    )
    assert confirmed.phone == "+48 700 000 001" and "UKE plan" in confirmed.phone_policy


def test_contacts_file_is_validated():
    from pydantic import ValidationError

    from ftf.victim.contacts import ContactBook

    with pytest.raises(ValidationError, match="run_id"):
        ContactBook.model_validate(
            {"version": "1", "local": {"email": {"pattern": "fixed@{domain}"}}, "live": {}}
        )
    with pytest.raises(ValidationError, match="9-digit"):
        ContactBook.model_validate(
            {"version": "1", "local": {"phones": {"retiree-income": "12345"}}, "live": {}}
        )


def test_phone_is_typed_national_and_read_back_whatever_the_widget_does():
    from ftf.victim.contacts import allowed_values, national, value_allowed

    phone = "+48 500 000 101"
    form = lead_form(
        field("email", type_="email", required=True), field("phone", type_="tel", required=True)
    )
    plan = plan_form(form, PERSONA, "a@victim.test", phone)
    assert plan.submit and plan.values["//input[@name='phone']"] == national(phone) == "500 000 101"
    allowed = allowed_values(PERSONA, "a@victim.test", phone)
    for shown in ("500 000 101", "500-000-101", "+48 500 000 101", "+48500000101"):
        assert value_allowed(shown, allowed, phone)
    assert not value_allowed("600 000 000", allowed, phone)


def test_a_live_url_gets_the_live_scope(tmp_path, settings):
    from ftf.victim.agent import VictimRun

    run = VictimRun("https://glowpixel.org/?x=1", PERSONA, tmp_path, settings=settings)
    assert run.contacts.scope == "live" and run.email is None and run.phone is None
    local = VictimRun("http://glowpixel.localhost:1/?x=1", PERSONA, tmp_path, settings=settings)
    assert local.contacts.scope == "local" and local.phone == "+48 500 000 101"


def test_quotes_survive_dropped_guillemets_and_ranges_are_kept():
    material = "Bot: «Liczba miejsc jest ściśle limitowana». Zysk: 2 800 – 6 500 zł / miesiąc.\n"
    out = verify(
        reading(
            levers=_section(
                [{"lever": "scarcity", "quote": "Liczba miejsc jest ściśle limitowana."}]
            ),
            money_path=_section(
                [
                    {
                        "kind": "promised_return",
                        "amount": "2 800 – 6 500",
                        "currency": "zł",
                        "time_frame": "miesiąc",
                        "quote": "2 800 – 6 500 zł / miesiąc",
                    },
                    {
                        "kind": "price",
                        "amount": "9 999",
                        "currency": "zł",
                        "time_frame": None,
                        "quote": "2 800 – 6 500 zł",
                    },
                ]
            ),
        ),
        material,
        [],
    )
    lever = out["levers"]["items"][0]
    assert lever["evidence"]["quote_found"] == "Liczba miejsc jest ściśle limitowana"
    money = out["money_path"]["items"]
    assert [(m["value"], m.get("value_high")) for m in money] == [("2800", "6500")]
