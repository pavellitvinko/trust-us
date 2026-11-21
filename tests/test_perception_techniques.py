"""Technique card tests. Fake client only: nothing here touches the network."""

import json
import shutil
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from ftf import pipeline
from ftf.config import Settings
from ftf.contracts import Incident, Observation, State, View, ViewKind
from ftf.ingest import ingest_folder
from ftf.perception import techniques
from ftf.perception.image import NAME as IMAGE_READER
from ftf.perception.techniques import (
    MAX_IMAGES,
    MAX_TEXT_CHARS,
    NAME,
    NAMESPACE,
    SYSTEM_PROMPT,
    build_input,
    run_techniques,
    searchable,
)
from ftf.registry import Context

BOX = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
PAGE = """<html><head><title>Wielki sekret</title><style>.x{color:red}</style></head><body>
<h1>Tylko dziś: zostało 17 miejsc</h1><p>Cena <s>999 zł</s> teraz 99 zł</p>
<img alt="Przelew z banku" src="a.png"><form><input type="tel" name="phone">
<input type="hidden" name="csrf_token" value="HIDDENSECRET"></form>
<script>var timer = setInterval(function(){ el.textContent = left(); }, 1000);</script>
<a href="https://x.pl/?token=URLSECRET">Dołącz teraz</a></body></html>"""


def quote(text):
    return {"quote": text, "image_index": None, "region": None}


def shown(index=0, region=BOX):
    return {"quote": None, "image_index": index, "region": region}


def card(name="Stated scarcity", *evidence):
    return {
        "name": name,
        "description": "A small stock is stated to hurry the visitor.",
        "evidence": list(evidence or [quote("zostało 17 miejsc")]),
    }


def message(*cards):
    body = json.dumps({"cards": list(cards)})
    part = NS(type="output_text", text=body)
    return NS(status="completed", output=[NS(type="message", phase=None, content=[part])])


class FakeOpenAI:
    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests = []
        self.responses = NS(stream=self._stream)

    @contextmanager
    def _stream(self, **request):
        self.requests.append(request)
        response = self.queue.pop(0)
        if isinstance(response, Exception):
            raise response
        yield NS(get_final_response=lambda: response)


def image_bytes(path, colour=(200, 20, 20), size=(160, 120)):
    noise = Image.effect_noise(size, 90).convert("RGB")
    Image.blend(Image.new("RGB", size, colour), noise, 0.5).save(path, "PNG")


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "data", "", "", "openai-test", "embed-test", "video-test")


@pytest.fixture
def ctx(store, tmp_path):
    root = tmp_path / "incident"
    root.mkdir()
    store.put_incident(Incident(incident_id="i1", root=str(root)), [], [])
    return Context(store)


def add_view(ctx, view):
    incident = ctx.store.get_incident("i1")
    ctx.store.put_incident(incident, [], [*ctx.store.views(), view])
    return view


def page_view(ctx, html=PAGE, resources=False):
    root = ctx.store.get_incident("i1").root
    with open(f"{root}/page.html", "w", encoding="utf-8") as fh:
        fh.write(html)
    return add_view(
        ctx,
        View(
            view_id="v1",
            incident_id="i1",
            kind=ViewKind.PAGE,
            label="page",
            primary="page.html",
            resources_dir="page_files" if resources else None,
        ),
    )


def cards_of(ctx, view_id="v1"):
    return [o for o in ctx.store.observations(view_id) if o.extractor == NAME]


def run(ctx, settings, client):
    return run_techniques(ctx, settings=settings, client=client)


def test_card_with_found_quote_is_stored(ctx, settings):
    page_view(ctx)
    summary = run(ctx, settings, FakeOpenAI(message(card())))
    assert summary["with_cards"] == 1 and summary["cards"] == 1
    [stored] = cards_of(ctx)
    assert stored.namespace == NAMESPACE and stored.state is State.PRESENT
    assert stored.value["name"] == "Stated scarcity"
    assert stored.value["evidence"][0]["quote"] == "zostało 17 miejsc"
    assert stored.source.file == "page.html"


def test_quote_matches_despite_spacing_case_and_typography():
    assert searchable("Zostało  17\nMIEJSC – „dziś”") == searchable('zostało 17 miejsc - "dziś"')


@pytest.mark.parametrize("bad", ["this text is not on the page", "dziś", "HIDDENSECRET value"])
def test_card_with_unfound_quote_is_dropped(ctx, settings, bad):
    page_view(ctx)
    run(ctx, settings, FakeOpenAI(message(card("Invented", quote(bad)), card())))
    assert [o.value["name"] for o in cards_of(ctx)] == ["Stated scarcity"]


def test_view_with_no_verified_card_is_not_observable(ctx, settings):
    page_view(ctx)
    summary = run(ctx, settings, FakeOpenAI(message(card("Invented", quote("nie ma tego tutaj")))))
    [stored] = cards_of(ctx)
    assert stored.state is State.NOT_OBSERVABLE and stored.raw == "no_verified_cards"
    assert summary["not_observable"] == 1


def test_input_has_page_text_and_script_facts_but_no_scripts_or_secrets(ctx):
    view = page_view(ctx)
    data = build_input(view, ctx)
    assert "zostało 17 miejsc" in data.text and "Przelew z banku" in data.text
    assert "setInterval" not in data.text and "color:red" not in data.text
    assert "crossed-out text: 999 zł" in data.facts
    assert "form asks for: tel field phone" in data.facts
    assert "a script runs a countdown timer" in data.facts
    assert "HIDDENSECRET" not in json.dumps(data.facts) + data.text


def test_prompt_has_no_fixed_tag_list():
    assert "No list of categories exists" in SYSTEM_PROMPT
    for technique in ("countdown", "scarcity", "celebrity", "fake news"):
        assert technique not in SYSTEM_PROMPT.lower()


def test_text_is_capped(ctx):
    view = page_view(ctx, f"<p>{'słowo ' * 20_000}</p>")
    assert len(build_input(view, ctx).text) == MAX_TEXT_CHARS


def test_page_sends_largest_distinct_images_only(ctx, settings):
    root = ctx.store.get_incident("i1").root
    folder = f"{root}/page_files"
    __import__("os").mkdir(folder)
    for index in range(MAX_IMAGES + 2):
        image_bytes(f"{folder}/{index}.png", size=(120 + index * 20, 100))
    shutil.copy(f"{folder}/7.png", f"{folder}/copy.png")
    with open(f"{folder}/icon.png", "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    view = page_view(ctx, resources=True)
    data = build_input(view, ctx)
    files = [image.file for image in data.images]
    assert len(files) == MAX_IMAGES and len(set(files)) == MAX_IMAGES
    assert files[0].endswith(("7.png", "copy.png")) and not any("icon" in f for f in files)


def test_image_evidence_needs_a_sent_image_and_valid_region(ctx, settings):
    root = ctx.store.get_incident("i1").root
    image_bytes(f"{root}/ad.png")
    add_view(
        ctx,
        View(view_id="v1", incident_id="i1", kind=ViewKind.IMAGE, label="ad", primary="ad.png"),
    )
    bad_box = {"x": 0.9, "y": 0.9, "width": 0.5, "height": 0.5}
    client = FakeOpenAI(
        message(
            card("Shown proof", shown(0)),
            card("Wrong image", shown(3)),
            card("Bad region", shown(0, bad_box)),
        )
    )
    run(ctx, settings, client)
    [stored] = cards_of(ctx)
    assert stored.value["name"] == "Shown proof"
    assert stored.source.file == "ad.png" and stored.source.locator.startswith("image:region[")
    parts = client.requests[0]["input"][0]["content"]
    assert any(part["type"] == "input_image" for part in parts)


def test_image_view_can_quote_text_already_read_from_it(ctx, settings):
    root = ctx.store.get_incident("i1").root
    image_bytes(f"{root}/ad.png")
    view = add_view(
        ctx,
        View(view_id="v1", incident_id="i1", kind=ViewKind.IMAGE, label="ad", primary="ad.png"),
    )
    reading = Observation(
        view_id=view.view_id,
        signal_id="E11",
        namespace="image.text",
        value="zarobiłem 5000 zł",
        raw="Zarobiłem 5000 zł",
        extractor=IMAGE_READER,
        version="2",
    )
    ctx.store.put_observations(view.view_id, IMAGE_READER, [reading])
    run(ctx, settings, FakeOpenAI(message(card("Profit claim", quote("zarobiłem 5000 zł")))))
    assert cards_of(ctx)[0].state is State.PRESENT


def test_same_name_merges_evidence(ctx, settings):
    page_view(ctx)
    client = FakeOpenAI(
        message(card("Urgency", quote("zostało 17 miejsc")), card("urgency", quote("Tylko dziś")))
    )
    run(ctx, settings, client)
    [stored] = cards_of(ctx)
    assert len(stored.value["evidence"]) == 2


def test_refusal_is_not_observable(ctx, settings):
    page_view(ctx)
    part = NS(type="refusal", refusal="no")
    refused = NS(status="completed", output=[NS(type="message", phase=None, content=[part])])
    summary = run(ctx, settings, FakeOpenAI(refused))
    assert summary["not_observable"] == 1 and cards_of(ctx)[0].raw == "refusal"


def test_passing_failure_keeps_earlier_cards(ctx, settings):
    page_view(ctx)
    run(ctx, settings, FakeOpenAI(message(card())))
    moved = Settings(settings.data_dir / "other", "", "", "openai-test", "e", "v")
    summary = run(ctx, moved, FakeOpenAI(message({"wrong": "shape"})))
    assert summary["kept"] == 1 and cards_of(ctx)[0].state is State.PRESENT


def test_repeat_run_uses_the_cache(ctx, settings):
    page_view(ctx)
    client = FakeOpenAI(message(card()))
    run(ctx, settings, client)
    run(ctx, settings, client)
    assert len(client.requests) == 1


def test_view_with_nothing_readable_makes_no_call(ctx, settings):
    page_view(ctx, "<script>var a=1</script>")
    client = FakeOpenAI()
    run(ctx, settings, client)
    assert client.requests == [] and cards_of(ctx)[0].raw == "no_content"


def test_deterministic_run_makes_no_card(ctx):
    page_view(ctx)
    pipeline.run_extract(ctx.store)
    assert cards_of(ctx) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_view_sends_frames_and_metadata(ctx, settings):
    root = ctx.store.get_incident("i1").root
    command = "ffmpeg -nostdin -v error -f lavfi -i testsrc=size=160x120:rate=5:duration=2"
    subprocess.run([*command.split(), "-y", f"{root}/clip.mp4"], check=True, timeout=30)
    with open(f"{root}/clip.json", "w", encoding="utf-8") as fh:
        json.dump({"caption": "Lekarz poleca ten produkt"}, fh)
    add_view(
        ctx,
        View(
            view_id="v1",
            incident_id="i1",
            kind=ViewKind.VIDEO,
            label="clip",
            primary="clip.mp4",
            attachments=["clip.json"],
        ),
    )
    client = FakeOpenAI(
        message(card("Doctor endorsement", quote("Lekarz poleca"), shown(MAX_IMAGES - 1)))
    )
    run(ctx, settings, client)
    [stored] = cards_of(ctx)
    locators = [item["locator"] for item in stored.value["evidence"]]
    assert locators[1].startswith(f"video:frame[{MAX_IMAGES - 1}]@")
    parts = client.requests[0]["input"][0]["content"]
    assert sum(part["type"] == "input_image" for part in parts) == MAX_IMAGES


def page_text(html):
    return " ".join(techniques._page_text(html)[0])


def test_cookie_banner_does_not_erase_the_article(ctx):
    html = "<p>Ta strona używa pliki cookie: akceptuj</p><h1>Zarobisz 5000 zł dziennie</h1>"
    assert "Zarobisz 5000 zł dziennie" in build_input(page_view(ctx, html), ctx).text


def test_broken_markup_does_not_hide_the_page():
    assert "Zarobisz" in page_text("<noscript><svg></noscript><h1>Zarobisz 5000</h1>")


def test_strike_tracking_handles_void_and_nested_tags():
    _, struck = techniques._page_text(
        '<img style="text-decoration:line-through" alt="x"><p>zwykły</p>'
        "<s>a<s>b</s>c</s><p>dalej</p>"
    )
    assert struck == ["a", "b", "c"]


@pytest.mark.parametrize(
    "hidden",
    [
        '<div style="display:none">ukryte <b>słowo</b> tutaj</div>',
        '<div style="visibility: hidden">ukryte słowo tutaj</div>',
        "<section hidden><p>ukryte słowo tutaj</p></section>",
    ],
)
def test_hidden_text_is_not_material(hidden):
    text = page_text(f"{hidden}<p>Przepis na ciasto</p>")
    assert "ukryte" not in text and "Przepis na ciasto" in text


def video_view(ctx, attachments=()):
    root = ctx.store.get_incident("i1").root
    command = "ffmpeg -nostdin -v error -f lavfi -i testsrc=size=160x120:rate=5:duration=2"
    subprocess.run([*command.split(), "-y", f"{root}/clip.mp4"], check=True, timeout=30)
    return add_view(
        ctx,
        View(
            view_id="v1",
            incident_id="i1",
            kind=ViewKind.VIDEO,
            label="clip",
            primary="clip.mp4",
            attachments=list(attachments),
        ),
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_temporary_video_failure_keeps_good_cards(ctx, settings, monkeypatch):
    video_view(ctx)
    run(ctx, settings, FakeOpenAI(message(card("Shown proof", shown(0)))))

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 60)

    monkeypatch.setattr(techniques.subprocess, "run", timed_out)
    summary = run(ctx, settings, FakeOpenAI())
    assert summary["kept"] == 1 and cards_of(ctx)[0].state is State.PRESENT


def test_damaged_gif_is_skipped(ctx, settings, monkeypatch):
    root = ctx.store.get_incident("i1").root
    __import__("os").mkdir(f"{root}/page_files")
    with open(f"{root}/page_files/banner.gif", "wb") as fh:
        fh.write(b"GIF89a" + b"0" * 2000)

    def broken(data):
        raise IndexError("index out of range")

    monkeypatch.setattr(techniques, "_is_animated_gif", broken)
    page_view(ctx, resources=True)
    assert run(ctx, settings, FakeOpenAI(message(card())))["with_cards"] == 1


def test_one_failing_view_does_not_stop_the_others(ctx, settings, monkeypatch):
    page_view(ctx)
    second = View(
        view_id="v2", incident_id="i1", kind=ViewKind.PAGE, label="b", primary="page.html"
    )
    add_view(ctx, second)
    real = techniques.observe_view

    def flaky(view, *args, **kwargs):
        if view.view_id == "v1":
            raise IndexError("boom")
        return real(view, *args, **kwargs)

    monkeypatch.setattr(techniques, "observe_view", flaky)
    summary = run(ctx, settings, FakeOpenAI(message(card())))
    assert summary["views"] == 2 and summary["with_cards"] == 1
    assert cards_of(ctx, "v1")[0].raw == "error"


def test_library_script_is_not_a_countdown_fact(ctx):
    root = ctx.store.get_incident("i1").root
    __import__("os").mkdir(f"{root}/page_files")
    with open(f"{root}/page_files/jquery.min.js", "w", encoding="utf-8") as fh:
        fh.write("jQuery.fx.timer=function(timer){setTimeout(a,1);b.text(c)}")
    view = page_view(ctx, "<p>Zwykła strona bez licznika</p>", resources=True)
    assert "a script runs a countdown timer" not in build_input(view, ctx).facts


def test_timer_word_without_a_visible_update_is_not_a_fact(ctx):
    html = "<p>Zwykła strona</p><script>var timer = setTimeout(load, 100);</script>"
    assert "a script runs a countdown timer" not in build_input(page_view(ctx, html), ctx).facts


def test_page_facts_survive_many_identifiers(ctx):
    view = page_view(ctx)
    many = [
        Observation(
            view_id=view.view_id,
            signal_id="A1",
            namespace="ad.param",
            value=f"id{index:03}",
            extractor="test",
            version="1",
        )
        for index in range(130)
    ]
    ctx.store.put_observations(view.view_id, "test", many)
    facts = build_input(view, ctx).facts
    assert "crossed-out text: 999 zł" in facts and "form asks for: tel field phone" in facts


def test_capture_screenshot_is_sent_first(ctx):
    root = ctx.store.get_incident("i1").root
    __import__("os").mkdir(f"{root}/page_files")
    image_bytes(f"{root}/page_files/large.png", size=(300, 200))
    image_bytes(f"{root}/screenshot.png", size=(120, 100))
    with open(f"{root}/manifest.json", "w", encoding="utf-8") as fh:
        fh.write("{}" + " " * 2000)
    page_view(ctx, resources=True)
    view = ctx.store.get_view("v1").model_copy(
        update={"attachments": ["manifest.json", "screenshot.png"]}
    )
    files = [image.file for image in build_input(view, ctx).images]
    assert files == ["screenshot.png", "page_files/large.png"]


def test_video_takes_only_its_own_sidecars(store, tmp_path):
    root = tmp_path / "videos"
    root.mkdir()
    for name in ("clip.mp4", "clip2.mp4", "clip.json", "clip.info.json", "clip2.json"):
        (root / name).write_bytes(b"{}")
    _, views = ingest_folder(root, store)
    attached = {v.primary: v.attachments for v in views}
    assert sorted(attached["clip.mp4"]) == ["clip.info.json", "clip.json"]
    assert attached["clip2.mp4"] == ["clip2.json"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_polish_quote_from_escaped_json_names_its_own_file(ctx, settings):
    root = ctx.store.get_incident("i1").root
    with open(f"{root}/a.json", "w", encoding="utf-8") as fh:
        json.dump({"title": "Pierwszy opis filmu"}, fh)
    with open(f"{root}/b.json", "w", encoding="utf-8") as fh:
        json.dump({"caption": "Zarobiłem 5000 zł dziś"}, fh)
    video_view(ctx, ["a.json", "b.json"])
    run(ctx, settings, FakeOpenAI(message(card("Profit claim", quote("Zarobiłem 5000 zł dziś")))))
    [stored] = cards_of(ctx)
    assert stored.state is State.PRESENT and stored.source.file == "b.json"
