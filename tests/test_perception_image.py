"""Screenshot reading tests. Fake client only: nothing here touches the network."""

import base64
import json
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from ftf.contracts import Incident, Purpose, State, View, ViewKind
from ftf.perception.image import (
    FIELDS,
    NAME,
    Region,
    normalise_domain,
    region_locator,
    run_perceive,
    sniff_media_type,
)
from ftf.registry import Context, extractors, load_plugins

from ._fakes import FakeOpenAI

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
BOX = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}


def item(value, confidence=0.9, region=BOX):
    return {"value": value, "region": region, "confidence": confidence}


def reading(**fields):
    return {field: fields.get(field, []) for field in FIELDS}


def message(body):
    part = NS(type="output_text", text=json.dumps(body))
    return NS(status="completed", output=[NS(type="message", phase=None, content=[part])])


@pytest.fixture
def ctx(store, tmp_path):
    root = tmp_path / "incident"
    root.mkdir()
    (root / "ad.png").write_bytes(PNG)
    incident = Incident(incident_id="i1", root=str(root))
    view = View(view_id="v1", incident_id="i1", kind=ViewKind.IMAGE, label="ad", primary="ad.png")
    store.put_incident(incident, [], [view])
    return Context(store)


def by_namespace(store):
    return {o.namespace: o for o in store.observations("v1")}


def test_reading_becomes_observations_with_regions(ctx, settings):
    client = FakeOpenAI(
        message(
            reading(
                visible_text=[item("Rafał Brzoska ujawnia sekret"), item("Sprawdź teraz")],
                person_names=[item("Rafał Brzoska")],
                advertiser_pages=[item("Formal Favorable")],
                visible_domains=[item("https://www.Example-News.live/oferta")],
                cta_labels=[item("Sprawdź teraz")],
                proof_objects=[item("bank-transfer screenshot")],
            )
        )
    )
    summary = run_perceive(ctx, settings=settings, client=client)
    seen = by_namespace(ctx.store)
    assert summary["read"] == 1 and summary["not_observable"] == 0
    assert seen["image.domain"].value == "example-news.live"
    assert seen["image.advertiser"].signal_id == "G4"
    assert seen["image.cta"].signal_id == "L2"
    assert seen["image.proof_object"].signal_id == "E10"
    assert seen["image.person_name"].source.locator == "image:region[0.1,0.2,0.3,0.4]"
    assert seen["image.person_name"].source.file == "ad.png"


def test_name_not_written_in_the_image_is_dropped(ctx, settings):
    client = FakeOpenAI(
        message(reading(visible_text=[item("Nowa okazja")], person_names=[item("Jan Kowalski")]))
    )
    run_perceive(ctx, settings=settings, client=client)
    assert "image.person_name" not in by_namespace(ctx.store)


def test_second_run_uses_the_cache(ctx, settings):
    client = FakeOpenAI(message(reading(visible_text=[item("tekst")])))
    run_perceive(ctx, settings=settings, client=client)
    run_perceive(ctx, settings=settings, client=client)
    assert len(client.requests) == 1
    assert len(ctx.store.observations("v1")) == 1


def test_request_sends_the_image_at_high_detail(ctx, settings):
    client = FakeOpenAI(message(reading()))
    run_perceive(ctx, settings=settings, client=client)
    image = client.requests[0]["input"][0]["content"][1]
    assert image["type"] == "input_image" and image["detail"] == "high"
    assert image["image_url"].startswith("data:image/png;base64,")


def test_refusal_is_not_observable_for_every_field(ctx, settings):
    refusal = NS(
        status="completed",
        output=[NS(type="message", phase=None, content=[NS(type="refusal", refusal="no")])],
    )
    summary = run_perceive(ctx, settings=settings, client=FakeOpenAI(refusal))
    observations = ctx.store.observations("v1")
    assert summary["not_observable"] == 1 and summary["observations"] == 0
    assert len(observations) == len(FIELDS)
    assert all(o.state is State.NOT_OBSERVABLE and o.value is None for o in observations)
    assert {o.raw for o in observations} == {"refusal"}


def test_unsupported_file_makes_no_request(ctx, settings, tmp_path):
    (tmp_path / "incident" / "ad.png").write_bytes(b"BM not an accepted format")
    client = FakeOpenAI()
    run_perceive(ctx, settings=settings, client=client)
    assert client.requests == []
    assert {o.raw for o in ctx.store.observations("v1")} == {"unsupported"}


def test_secret_read_from_an_image_is_redacted(ctx, settings):
    client = FakeOpenAI(message(reading(visible_text=[item("https://x.pl/?token=abc123secret")])))
    run_perceive(ctx, settings=settings, client=client)
    stored = ctx.store.observations("v1")[0]
    assert "abc123secret" not in stored.value and "abc123secret" not in stored.raw


def test_secret_read_from_an_image_never_reaches_the_cache(ctx, settings):
    client = FakeOpenAI(message(reading(visible_text=[item("https://x.pl/?token=abc123secret")])))
    run_perceive(ctx, settings=settings, client=client)
    cached = "".join(p.read_text() for p in settings.model_cache_dir.rglob("*.json"))
    assert cached and "abc123secret" not in cached


def test_inline_image_data_is_not_redacted(ctx, settings, tmp_path):
    data = b"\x89PNG\r\n\x1a\n" + base64.b64decode("AAAA/token==")
    (tmp_path / "incident" / "ad.png").write_bytes(data)
    client = FakeOpenAI(message(reading()))
    run_perceive(ctx, settings=settings, client=client)
    url = client.requests[0]["input"][0]["content"][1]["image_url"]
    assert base64.b64decode(url.partition(",")[2]) == data


def test_transient_failure_keeps_the_earlier_reading(ctx, settings):
    run_perceive(
        ctx, settings=settings, client=FakeOpenAI(message(reading(visible_text=[item("tekst")])))
    )
    changed = replace(settings, openai_model="another-model")  # forces a cache miss, no key
    summary = run_perceive(ctx, settings=changed)
    assert summary["kept"] == 1
    assert [o.value for o in ctx.store.observations("v1")] == ["tekst"]


def test_stable_failure_replaces_the_earlier_reading(ctx, settings, tmp_path):
    run_perceive(
        ctx, settings=settings, client=FakeOpenAI(message(reading(visible_text=[item("tekst")])))
    )
    (tmp_path / "incident" / "ad.png").write_bytes(b"BM not an accepted format")
    run_perceive(ctx, settings=settings)
    assert {o.state for o in ctx.store.observations("v1")} == {State.NOT_OBSERVABLE}


@pytest.mark.parametrize(
    ("text", "name"),
    [("Joanna poleca", "Anna"), ("Jankowski", "Jan"), ("Rafał", "Rafał Brzoska")],
)
def test_partial_word_is_not_a_written_name(ctx, settings, text, name):
    client = FakeOpenAI(message(reading(visible_text=[item(text)], person_names=[item(name)])))
    run_perceive(ctx, settings=settings, client=client)
    assert "image.person_name" not in by_namespace(ctx.store)


def test_name_split_across_two_blocks_is_dropped(ctx, settings):
    client = FakeOpenAI(
        message(
            reading(
                visible_text=[item("Poleca Anna"), item("Nowak inwestuje")],
                person_names=[item("Anna Nowak")],
            )
        )
    )
    run_perceive(ctx, settings=settings, client=client)
    assert "image.person_name" not in by_namespace(ctx.store)


def test_domain_survives_redacted_credentials(ctx, settings):
    client = FakeOpenAI(
        message(reading(visible_domains=[item("https://user:hunter2@example.com/a")]))
    )
    run_perceive(ctx, settings=settings, client=client)
    stored = by_namespace(ctx.store)["image.domain"]
    assert stored.value == "example.com" and "hunter2" not in stored.raw


def test_text_value_is_normalised_and_raw_kept(ctx, settings):
    client = FakeOpenAI(message(reading(cta_labels=[item("SPRAWDŹ   Teraz")])))
    run_perceive(ctx, settings=settings, client=client)
    stored = by_namespace(ctx.store)["image.cta"]
    assert stored.value == "sprawdź teraz" and stored.raw == "SPRAWDŹ   Teraz"
    assert stored.purpose is Purpose.LINKAGE


def test_animated_gif_makes_no_request(ctx, settings, tmp_path):
    frames = [Image.new("L", (2, 2), color) for color in (0, 255)]
    path = tmp_path / "incident" / "ad.png"
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:])
    client = FakeOpenAI()
    run_perceive(ctx, settings=settings, client=client)
    assert client.requests == []
    assert {o.raw for o in ctx.store.observations("v1")} == {"unsupported"}


def test_still_gif_is_sent(ctx, settings, tmp_path):
    Image.new("P", (2, 2)).save(tmp_path / "incident" / "ad.png", format="GIF")
    client = FakeOpenAI(message(reading()))
    run_perceive(ctx, settings=settings, client=client)
    assert len(client.requests) == 1


def test_perception_is_not_a_pipeline_extractor():
    load_plugins()
    assert NAME not in extractors.names()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("WWW.Example.PL", "example.pl"),
        ("https://sub.example.co.uk/a?b=1", "sub.example.co.uk"),
        ("kliknij tutaj", None),
        ("localhost", None),
        ("przykład.pl", "xn--przykad-rjb.pl"),
        ("https://example.com:8443/a", "example.com"),
    ],
)
def test_normalise_domain(text, expected):
    assert normalise_domain(text) == expected


@pytest.mark.parametrize(
    ("box", "expected"),
    [
        ((0.12, 0.8, 0.4, 0.06), "image:region[0.12,0.8,0.4,0.06]"),
        ((0.9, 0.1, 0.5, 0.1), "image"),
        ((0.1, 0.1, 0, 0.1), "image"),
        ((0.1, 0.1, 0.00004, 0.1), "image"),
        ((0.12345, 0.1, 0.87655, 0.1), "image:region[0.1235,0.1,0.8765,0.1]"),
    ],
)
def test_region_locator(box, expected):
    x, y, width, height = box
    assert region_locator(Region(x=x, y=y, width=width, height=height)) == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (PNG, "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"GIF89a", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "image/webp"),
        (b"BM", None),
    ],
)
def test_sniff_media_type(data, expected):
    assert sniff_media_type(data) == expected
